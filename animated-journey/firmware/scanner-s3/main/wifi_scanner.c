#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/semphr.h"
#include "freertos/timers.h"
#include "esp_wifi.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "config.h"
#include "scanner_types.h"

static const char *TAG = "wifi_scanner";

typedef struct {
    uint8_t frame_ctrl[2];
    uint8_t duration[2];
    uint8_t addr1[6];   /* destination */
    uint8_t addr2[6];   /* source (transmitter) */
    uint8_t addr3[6];   /* BSSID */
    uint8_t seq_ctrl[2];
} __attribute__((packed)) ieee80211_hdr_t;

/* Tagged parameter: ID + length + data */
typedef struct {
    uint8_t id;
    uint8_t len;
    uint8_t data[];
} __attribute__((packed)) tagged_param_t;

#define RING_BUF_SIZE MAX_SCAN_BATCH_SIZE

static scan_result_t s_ring_buf[RING_BUF_SIZE];
static int s_ring_head = 0;
static int s_ring_count = 0;
static SemaphoreHandle_t s_ring_mutex = NULL;

static TimerHandle_t s_channel_hop_timer = NULL;
static uint8_t s_current_channel = 1;
static bool s_scanning = false;

static void ring_buf_push(const scan_result_t *result)
{
    xSemaphoreTake(s_ring_mutex, portMAX_DELAY);
    memcpy(&s_ring_buf[s_ring_head], result, sizeof(scan_result_t));
    s_ring_head = (s_ring_head + 1) % RING_BUF_SIZE;
    if (s_ring_count < RING_BUF_SIZE) {
        s_ring_count++;
    }
    xSemaphoreGive(s_ring_mutex);
}

static void channel_hop_callback(TimerHandle_t timer)
{
    s_current_channel++;
    if (s_current_channel > 13) {
        s_current_channel = 1;
    }
    esp_wifi_set_channel(s_current_channel, WIFI_SECOND_CHAN_NONE);
}

static void promiscuous_rx_cb(void *buf, wifi_promiscuous_pkt_type_t type)
{
    if (type != WIFI_PKT_MGMT) return;

    const wifi_promiscuous_pkt_t *pkt = (wifi_promiscuous_pkt_t *)buf;
    const uint8_t *payload = pkt->payload;
    int len = pkt->rx_ctrl.sig_len;

    if (len < (int)sizeof(ieee80211_hdr_t)) return;

    const ieee80211_hdr_t *hdr = (const ieee80211_hdr_t *)payload;
    uint8_t frame_type = (hdr->frame_ctrl[0] >> 2) & 0x03;
    uint8_t frame_subtype = (hdr->frame_ctrl[0] >> 4) & 0x0F;

    /* Only management frames (type 0) */
    if (frame_type != 0) return;

    int64_t now = esp_timer_get_time() / 1000;

    if (frame_subtype == 0x04) {
        /* Probe request */
        wifi_probe_t probe = {0};
        memcpy(probe.base.mac, hdr->addr2, 6);
        probe.base.rssi = pkt->rx_ctrl.rssi;
        probe.base.channel = pkt->rx_ctrl.channel;
        probe.base.type = SCAN_WIFI_PROBE;
        probe.base.timestamp_ms = now;
        probe.frame_subtype = frame_subtype;

        /* Parse SSID from tagged parameters */
        int offset = sizeof(ieee80211_hdr_t);
        if (offset < len) {
            const tagged_param_t *tag = (const tagged_param_t *)(payload + offset);
            if (tag->id == 0 && tag->len <= 32 && (offset + 2 + tag->len) <= len) {
                memcpy(probe.ssid, tag->data, tag->len);
                probe.ssid[tag->len] = '\0';
                probe.ssid_len = tag->len;
            }
        }

        ring_buf_push((scan_result_t *)&probe);

    } else if (frame_subtype == 0x08) {
        /* Beacon */
        wifi_beacon_t beacon = {0};
        memcpy(beacon.base.mac, hdr->addr2, 6);
        beacon.base.rssi = pkt->rx_ctrl.rssi;
        beacon.base.channel = pkt->rx_ctrl.channel;
        beacon.base.type = SCAN_WIFI_BEACON;
        beacon.base.timestamp_ms = now;
        memcpy(beacon.bssid, hdr->addr3, 6);

        /* Beacon frame body: timestamp(8) + interval(2) + capability(2) + tagged params */
        int offset = sizeof(ieee80211_hdr_t) + 12;
        if (offset < len) {
            const tagged_param_t *tag = (const tagged_param_t *)(payload + offset);
            if (tag->id == 0 && tag->len <= 32 && (offset + 2 + tag->len) <= len) {
                memcpy(beacon.ssid, tag->data, tag->len);
                beacon.ssid[tag->len] = '\0';
                beacon.ssid_len = tag->len;
            }
        }

        ring_buf_push((scan_result_t *)&beacon);
    }
}

void wifi_scanner_init(void)
{
    s_ring_mutex = xSemaphoreCreateMutex();
    configASSERT(s_ring_mutex);

    s_channel_hop_timer = xTimerCreate("ch_hop",
                                        pdMS_TO_TICKS(WIFI_CHANNEL_HOP_INTERVAL_MS),
                                        pdTRUE, NULL, channel_hop_callback);
    ESP_LOGI(TAG, "WiFi scanner initialized");
}

void wifi_scanner_start(void)
{
    if (s_scanning) return;

    esp_wifi_set_promiscuous_rx_cb(promiscuous_rx_cb);

    wifi_promiscuous_filter_t filter = {
        .filter_mask = WIFI_PROMIS_FILTER_MASK_MGMT,
    };
    esp_wifi_set_promiscuous_filter(&filter);
    esp_wifi_set_promiscuous(true);

    s_current_channel = 1;
    esp_wifi_set_channel(s_current_channel, WIFI_SECOND_CHAN_NONE);
    xTimerStart(s_channel_hop_timer, 0);

    s_scanning = true;
    ESP_LOGI(TAG, "WiFi promiscuous scanning started");
}

void wifi_scanner_stop(void)
{
    if (!s_scanning) return;

    xTimerStop(s_channel_hop_timer, 0);
    esp_wifi_set_promiscuous(false);
    s_scanning = false;
    ESP_LOGI(TAG, "WiFi promiscuous scanning stopped");
}

int wifi_scanner_get_results(scan_result_t *results, int max, int *count)
{
    xSemaphoreTake(s_ring_mutex, portMAX_DELAY);
    int n = s_ring_count < max ? s_ring_count : max;
    int tail = (s_ring_head - s_ring_count + RING_BUF_SIZE) % RING_BUF_SIZE;
    for (int i = 0; i < n; i++) {
        int idx = (tail + i) % RING_BUF_SIZE;
        memcpy(&results[i], &s_ring_buf[idx], sizeof(scan_result_t));
    }
    s_ring_count -= n;
    *count = n;
    xSemaphoreGive(s_ring_mutex);
    return n;
}
