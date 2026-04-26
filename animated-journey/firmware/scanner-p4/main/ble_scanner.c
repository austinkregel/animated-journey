#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "nimble/nimble_port.h"
#include "nimble/nimble_port_freertos.h"
#include "host/ble_hs.h"
#include "host/ble_gap.h"
#include "esp_hosted_misc.h"
#include "config.h"
#include "scanner_types.h"

static const char *TAG = "ble_scanner";

#define BLE_RING_BUF_SIZE MAX_SCAN_BATCH_SIZE

static ble_adv_t s_ring_buf[BLE_RING_BUF_SIZE];
static int s_ring_head = 0;
static int s_ring_count = 0;
static SemaphoreHandle_t s_ring_mutex = NULL;
static bool s_scanning = false;
static bool s_initialized = false;

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

static void ring_buf_push(const ble_adv_t *adv)
{
    xSemaphoreTake(s_ring_mutex, portMAX_DELAY);
    memcpy(&s_ring_buf[s_ring_head], adv, sizeof(ble_adv_t));
    s_ring_head = (s_ring_head + 1) % BLE_RING_BUF_SIZE;
    if (s_ring_count < BLE_RING_BUF_SIZE) {
        s_ring_count++;
    }
    xSemaphoreGive(s_ring_mutex);
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

static int gap_event_cb(struct ble_gap_event *event, void *arg)
{
    if (event->type != BLE_GAP_EVENT_DISC) return 0;

    const struct ble_gap_disc_desc *desc = &event->disc;

    ble_adv_t adv = {0};
    memcpy(adv.base.mac, desc->addr.val, 6);
    adv.base.rssi = desc->rssi;
    adv.base.channel = 0;
    adv.base.type = SCAN_BLE_ADV;
    adv.base.timestamp_ms = esp_timer_get_time() / 1000;
    adv.addr_type = desc->addr.type;
    adv.adv_type = desc->event_type;
    adv.tx_power = -127;

    if (desc->length_data > 0 && desc->data != NULL) {
        parse_adv_fields(desc->data, desc->length_data, &adv);
    }

    ring_buf_push(&adv);
    return 0;
}

static void nimble_host_task(void *param)
{
    nimble_port_run();
    nimble_port_freertos_deinit();
}

bool ble_scanner_init(void)
{
    ESP_LOGI(TAG, "Initializing BLE scanner (NimBLE over ESP-Hosted SDIO)...");

    s_ring_mutex = xSemaphoreCreateMutex();
    if (!s_ring_mutex) {
        ESP_LOGE(TAG, "Failed to create ring buffer mutex");
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
    ESP_LOGI(TAG, "BLE scanner initialized, waiting for host-controller sync before scanning");
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

int ble_scanner_get_results(ble_adv_t *results, int max, int *count)
{
    xSemaphoreTake(s_ring_mutex, portMAX_DELAY);
    int n = s_ring_count < max ? s_ring_count : max;
    int tail = (s_ring_head - s_ring_count + BLE_RING_BUF_SIZE) % BLE_RING_BUF_SIZE;
    for (int i = 0; i < n; i++) {
        int idx = (tail + i) % BLE_RING_BUF_SIZE;
        memcpy(&results[i], &s_ring_buf[idx], sizeof(ble_adv_t));
    }
    s_ring_count -= n;
    *count = n;
    xSemaphoreGive(s_ring_mutex);
    return n;
}
