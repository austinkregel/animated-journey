#include <string.h>
#include <stdlib.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_gap.h"
#include "esp_hosted_misc.h"
#include "config.h"
#include "scanner_types.h"

static const char *TAG = "ble_scanner";

/* ---------- hash table --------------------------------------------------- */

#define TABLE_CAP MAX_BLE_TRACKED

typedef struct {
    bool     occupied;
    ble_adv_t snapshot;
    int64_t  first_seen_us;
    int64_t  last_seen_us;
    uint32_t seen_count;
    uint32_t unique_id;
} ble_slot_t;

static ble_slot_t *s_table = NULL;
static int s_table_count = 0;
static uint32_t s_unique_total = 0;
static SemaphoreHandle_t s_table_mutex = NULL;
static bool s_scanning = false;
static bool s_initialized = false;

static uint32_t fnv1a_mac(const uint8_t *mac)
{
    uint32_t h = 2166136261u;
    for (int i = 0; i < 6; i++) {
        h ^= mac[i];
        h *= 16777619u;
    }
    return h;
}

static int table_find_or_insert(const uint8_t *mac, bool *is_new)
{
    uint32_t h = fnv1a_mac(mac);
    uint32_t idx = h % TABLE_CAP;

    for (uint32_t i = 0; i < TABLE_CAP; i++) {
        uint32_t probe = (idx + i) % TABLE_CAP;
        if (!s_table[probe].occupied) {
            *is_new = true;
            return (int)probe;
        }
        if (memcmp(s_table[probe].snapshot.base.mac, mac, 6) == 0) {
            *is_new = false;
            return (int)probe;
        }
    }

    /* Table full: evict the LRU entry */
    int lru_idx = 0;
    int64_t lru_time = INT64_MAX;
    for (int i = 0; i < TABLE_CAP; i++) {
        if (s_table[i].occupied && s_table[i].last_seen_us < lru_time) {
            lru_time = s_table[i].last_seen_us;
            lru_idx = i;
        }
    }
    s_table[lru_idx].occupied = false;
    s_table_count--;
    *is_new = true;
    return lru_idx;
}

/* ---------- advertisement parsing ---------------------------------------- */

static const char *nimble_rc_str(int rc)
{
    switch (rc) {
    case 0:                     return "OK";
    case BLE_HS_EAGAIN:         return "EAGAIN (temporarily unable)";
    case BLE_HS_EALREADY:       return "EALREADY (already in progress)";
    case BLE_HS_EINVAL:         return "EINVAL (invalid arguments)";
    case BLE_HS_EMSGSIZE:       return "EMSGSIZE (message too large)";
    case BLE_HS_ENOENT:         return "ENOENT (no entry)";
    case BLE_HS_ENOMEM:         return "ENOMEM (out of memory)";
    case BLE_HS_ENOTCONN:       return "ENOTCONN (not connected)";
    case BLE_HS_ENOTSUP:        return "ENOTSUP (not supported)";
    case BLE_HS_ETIMEOUT:       return "ETIMEOUT (timed out)";
    case BLE_HS_EDONE:          return "EDONE (done)";
    case BLE_HS_EBUSY:          return "EBUSY (busy)";
    case BLE_HS_ENOTSYNCED:     return "ENOTSYNCED (host not synced with controller)";
    case BLE_HS_ETIMEOUT_HCI:   return "ETIMEOUT_HCI (HCI request timed out, controller unresponsive)";
    default:                    return "unknown";
    }
}

static void parse_adv_fields(const uint8_t *data, uint8_t data_len, ble_adv_t *adv)
{
    int pos = 0;
    while (pos < data_len) {
        uint8_t field_len = data[pos];
        if (field_len == 0 || pos + 1 + field_len > data_len) break;

        uint8_t field_type = data[pos + 1];
        const uint8_t *field_data = &data[pos + 2];
        uint8_t field_data_len = field_len - 1;

        switch (field_type) {
        case 0x08: /* Shortened Local Name */
        case 0x09: /* Complete Local Name */
            if (field_data_len > sizeof(adv->name) - 1) {
                field_data_len = sizeof(adv->name) - 1;
            }
            memcpy(adv->name, field_data, field_data_len);
            adv->name[field_data_len] = '\0';
            adv->name_len = field_data_len;
            break;

        case 0xFF: /* Manufacturer Specific Data */
            if (field_data_len > sizeof(adv->manufacturer_data)) {
                field_data_len = sizeof(adv->manufacturer_data);
            }
            memcpy(adv->manufacturer_data, field_data, field_data_len);
            adv->manufacturer_data_len = field_data_len;
            break;

        case 0x02: /* Incomplete List of 16-bit Service UUIDs */
        case 0x03: /* Complete List of 16-bit Service UUIDs */
            for (int i = 0; i + 1 < field_data_len && adv->service_uuid_count < 8; i += 2) {
                adv->service_uuids[adv->service_uuid_count++] =
                    field_data[i] | (field_data[i + 1] << 8);
            }
            break;

        case 0x0A: /* TX Power Level */
            if (field_data_len >= 1) {
                adv->tx_power = (int8_t)field_data[0];
            }
            break;

        default:
            break;
        }

        pos += 1 + field_len;
    }
}

/* ---------- GAP callback ------------------------------------------------- */

static int gap_event_cb(struct ble_gap_event *event, void *arg)
{
    if (event->type != BLE_GAP_EVENT_DISC) return 0;

    const struct ble_gap_disc_desc *desc = &event->disc;
    int64_t now_us = esp_timer_get_time();

    ble_adv_t incoming = {0};
    memcpy(incoming.base.mac, desc->addr.val, 6);
    incoming.base.rssi = desc->rssi;
    incoming.base.channel = 0;
    incoming.base.type = SCAN_BLE_ADV;
    incoming.base.timestamp_ms = now_us / 1000;
    incoming.addr_type = desc->addr.type;
    incoming.adv_type = desc->event_type;
    incoming.tx_power = -127;

    if (desc->length_data > 0 && desc->data != NULL) {
        parse_adv_fields(desc->data, desc->length_data, &incoming);
    }

    xSemaphoreTake(s_table_mutex, portMAX_DELAY);

    bool is_new = false;
    int idx = table_find_or_insert(desc->addr.val, &is_new);

    ble_slot_t *slot = &s_table[idx];

    if (is_new) {
        memset(slot, 0, sizeof(*slot));
        slot->occupied = true;
        slot->first_seen_us = now_us;
        slot->seen_count = 0;
        s_table_count++;
        s_unique_total++;
        memcpy(&slot->snapshot, &incoming, sizeof(ble_adv_t));
    } else {
        slot->snapshot.base.rssi = incoming.base.rssi;
        slot->snapshot.base.timestamp_ms = incoming.base.timestamp_ms;
        slot->snapshot.adv_type = incoming.adv_type;
        if (incoming.name_len > 0) {
            memcpy(slot->snapshot.name, incoming.name, incoming.name_len + 1);
            slot->snapshot.name_len = incoming.name_len;
        }
        if (incoming.manufacturer_data_len > 0) {
            memcpy(slot->snapshot.manufacturer_data, incoming.manufacturer_data,
                   incoming.manufacturer_data_len);
            slot->snapshot.manufacturer_data_len = incoming.manufacturer_data_len;
        }
        if (incoming.tx_power != -127) {
            slot->snapshot.tx_power = incoming.tx_power;
        }
        if (incoming.service_uuid_count > 0) {
            memcpy(slot->snapshot.service_uuids, incoming.service_uuids,
                   incoming.service_uuid_count * sizeof(uint16_t));
            slot->snapshot.service_uuid_count = incoming.service_uuid_count;
        }
    }

    slot->last_seen_us = now_us;
    slot->seen_count++;

    xSemaphoreGive(s_table_mutex);
    return 0;
}

/* ---------- NimBLE host task --------------------------------------------- */

static void nimble_host_task(void *param)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

/* ---------- public API --------------------------------------------------- */

bool ble_scanner_init(void)
{
    ESP_LOGI(TAG, "Initializing BLE scanner (NimBLE over ESP-Hosted SDIO)...");

    s_table_mutex = xSemaphoreCreateMutex();
    if (!s_table_mutex) {
        ESP_LOGE(TAG, "Failed to create table mutex");
        return false;
    }

    s_table = heap_caps_calloc(TABLE_CAP, sizeof(ble_slot_t),
                               MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_table) {
        s_table = calloc(TABLE_CAP, sizeof(ble_slot_t));
    }
    if (!s_table) {
        ESP_LOGE(TAG, "Failed to allocate BLE table (%u bytes)",
                 (unsigned)(TABLE_CAP * sizeof(ble_slot_t)));
        return false;
    }

    ESP_LOGI(TAG, "Calling nimble_port_init() (triggers SDIO transport to C6)...");
    esp_err_t ret = nimble_port_init();
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "nimble_port_init() failed: %s (0x%x) -- is the C6 co-processor responding?",
                 esp_err_to_name(ret), ret);
        return false;
    }
    ESP_LOGI(TAG, "nimble_port_init() succeeded");

    ESP_LOGI(TAG, "Waiting for ESP-Hosted transport, then init+enable C6 BT controller...");
    esp_err_t bt_ret = ESP_FAIL;
    for (int i = 1; i <= 20; i++) {
        bt_ret = esp_hosted_bt_controller_init();
        if (bt_ret == ESP_OK) break;
        ESP_LOGW(TAG, "Attempt %d/20: esp_hosted_bt_controller_init() returned %s -- retrying in 1s",
                 i, esp_err_to_name(bt_ret));
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    if (bt_ret != ESP_OK) {
        ESP_LOGE(TAG, "esp_hosted_bt_controller_init() failed after retries: %s (0x%x)",
                 esp_err_to_name(bt_ret), bt_ret);
        return false;
    }
    ESP_LOGI(TAG, "C6 BT controller initialized");

    bt_ret = esp_hosted_bt_controller_enable();
    if (bt_ret != ESP_OK) {
        ESP_LOGE(TAG, "esp_hosted_bt_controller_enable() failed: %s (0x%x)",
                 esp_err_to_name(bt_ret), bt_ret);
        return false;
    }
    ESP_LOGI(TAG, "C6 BT controller enabled");

    ESP_LOGI(TAG, "Starting NimBLE host task...");
    nimble_port_freertos_init(nimble_host_task);

    s_initialized = true;
    ESP_LOGI(TAG, "BLE scanner initialized (table cap=%d), waiting for host-controller sync",
             TABLE_CAP);
    return true;
}

bool ble_scanner_start(void)
{
    if (s_scanning) {
        ESP_LOGD(TAG, "ble_scanner_start called but already scanning");
        return true;
    }

    if (!s_initialized) {
        ESP_LOGE(TAG, "ble_scanner_start called but scanner was never initialized");
        return false;
    }

    struct ble_gap_disc_params disc_params = {
        .passive = 1,
        .itvl = BLE_SCAN_INTERVAL_MS * 1000 / 625,
        .window = BLE_SCAN_WINDOW_MS * 1000 / 625,
        .filter_duplicates = 0,
        .limited = 0,
    };

    ESP_LOGI(TAG, "Starting BLE scan (passive, interval=%dms, window=%dms)...",
             BLE_SCAN_INTERVAL_MS, BLE_SCAN_WINDOW_MS);

    for (int attempt = 1; attempt <= 10; attempt++) {
        bool enabled = ble_hs_is_enabled();
        bool synced = ble_hs_synced();

        if (!enabled || !synced) {
            ESP_LOGW(TAG, "Attempt %d/10: NimBLE host enabled=%s synced=%s -- "
                     "C6 co-processor not ready, retrying in 2s",
                     attempt, enabled ? "yes" : "NO", synced ? "yes" : "NO");
            vTaskDelay(pdMS_TO_TICKS(2000));
            continue;
        }

        int rc = ble_gap_disc(BLE_OWN_ADDR_PUBLIC, BLE_HS_FOREVER,
                               &disc_params, gap_event_cb, NULL);
        if (rc == 0) {
            s_scanning = true;
            ESP_LOGI(TAG, "BLE passive scanning started successfully on attempt %d", attempt);
            return true;
        }

        ESP_LOGW(TAG, "Attempt %d/10: ble_gap_disc() returned rc=%d (%s), retrying in 2s",
                 attempt, rc, nimble_rc_str(rc));
        vTaskDelay(pdMS_TO_TICKS(2000));
    }

    ESP_LOGE(TAG, "Failed to start BLE scan after 10 attempts -- "
             "check that the ESP-Hosted C6 co-processor is flashed and connected via SDIO");
    return false;
}

void ble_scanner_stop(void)
{
    if (!s_scanning) return;

    ble_gap_disc_cancel();
    s_scanning = false;
    ESP_LOGI(TAG, "BLE scanning stopped");
}

int ble_scanner_drain_seen(ble_adv_t *out, int max, int *count)
{
    xSemaphoreTake(s_table_mutex, portMAX_DELAY);
    int n = 0;
    for (int i = 0; i < TABLE_CAP && n < max; i++) {
        if (!s_table[i].occupied) continue;

        ble_adv_t *dst = &out[n];
        memcpy(dst, &s_table[i].snapshot, sizeof(ble_adv_t));
        dst->seen_count = s_table[i].seen_count;
        dst->first_seen_s = (double)s_table[i].first_seen_us / 1000000.0;
        dst->event = BLE_EVENT_SEEN;
        n++;
    }
    *count = n;
    xSemaphoreGive(s_table_mutex);
    return n;
}

int ble_scanner_drain_gone(ble_adv_t *out, int max, int *count)
{
    int64_t now_us = esp_timer_get_time();
    int64_t cutoff_us = (int64_t)BLE_GONE_TIMEOUT_MS * 1000LL;

    xSemaphoreTake(s_table_mutex, portMAX_DELAY);
    int n = 0;
    for (int i = 0; i < TABLE_CAP && n < max; i++) {
        if (!s_table[i].occupied) continue;
        if ((now_us - s_table[i].last_seen_us) < cutoff_us) continue;

        ble_adv_t *dst = &out[n];
        memcpy(dst, &s_table[i].snapshot, sizeof(ble_adv_t));
        dst->seen_count = s_table[i].seen_count;
        dst->first_seen_s = (double)s_table[i].first_seen_us / 1000000.0;
        dst->event = BLE_EVENT_GONE;
        n++;

        s_table[i].occupied = false;
        s_table_count--;
    }
    *count = n;
    xSemaphoreGive(s_table_mutex);
    return n;
}

int ble_scanner_active_count(void)
{
    xSemaphoreTake(s_table_mutex, portMAX_DELAY);
    int n = s_table_count;
    xSemaphoreGive(s_table_mutex);
    return n;
}

uint32_t ble_scanner_unique_total(void)
{
    xSemaphoreTake(s_table_mutex, portMAX_DELAY);
    uint32_t n = s_unique_total;
    xSemaphoreGive(s_table_mutex);
    return n;
}
