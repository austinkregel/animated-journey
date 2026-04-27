#include <string.h>
#include <stdlib.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_netif_sntp.h"
#include "esp_system.h"
#include "nvs_flash.h"
#include "config.h"
#include "nvs_config.h"
#include "mqtt_reporter.h"
#include "ota_client.h"
#include "scanner_types.h"
#include "ethernet.h"
#include "led_ctrl.h"
#include "audio_beep.h"

#define STATUS_LED_GPIO  2

static const char *TAG = "main";

extern bool     ble_scanner_init(void);
extern bool     ble_scanner_start(void);
extern void     ble_scanner_stop(void);
extern int      ble_scanner_drain_seen(ble_adv_t *out, int max, int *count);
extern int      ble_scanner_drain_gone(ble_adv_t *out, int max, int *count);
extern int      ble_scanner_active_count(void);
extern uint32_t ble_scanner_unique_total(void);

static nvs_config_t s_config;
static volatile bool s_network_up = false;

static void mqtt_command_handler(const char *command, const char *payload, int payload_len)
{
    if (strcmp(command, "identify") == 0) {
        ESP_LOGI(TAG, "Identify command received");
        led_ctrl_identify();
        audio_beep_identify();
    } else {
        ESP_LOGD(TAG, "Unknown command: %s", command);
    }
}

static void sntp_init_time(void)
{
    ESP_LOGI(TAG, "Initializing SNTP");
    esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_netif_sntp_init(&config);
}

static void network_services_task(void *arg)
{
    ESP_LOGI(TAG, "IP obtained -- starting SNTP, MQTT, OTA");

    sntp_init_time();

    mqtt_reporter_config_t mqtt_cfg = {
        .host = s_config.mqtt_host,
        .port = s_config.mqtt_port,
        .username = s_config.mqtt_username,
        .password = s_config.mqtt_password,
        .node_id = s_config.node_id,
    };
    mqtt_reporter_set_command_callback(mqtt_command_handler);
    mqtt_reporter_init(&mqtt_cfg);

    int retries = 0;
    while (!mqtt_reporter_is_connected() && retries < 30) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        retries++;
    }

    if (mqtt_reporter_is_connected()) {
        ESP_LOGI(TAG, "MQTT connected after %d s", retries);
    } else {
        ESP_LOGW(TAG, "MQTT did not connect within %d s -- will keep trying in background", retries);
    }

    ota_client_init(mqtt_reporter_get_client(), s_config.node_id);
    mqtt_reporter_publish_discovery(s_config.node_id, "ESP32-P4");

    s_network_up = true;
    vTaskDelete(NULL);
}

static void on_ip_obtained(void)
{
    if (s_network_up) {
        return;
    }
    xTaskCreate(network_services_task, "net_svc_init", 6144, NULL, 5, NULL);
}

static void publish_status(void)
{
    node_status_t status = {0};
    strncpy(status.node_id, s_config.node_id, sizeof(status.node_id) - 1);
    status.uptime_s = (uint32_t)(esp_timer_get_time() / 1000000ULL);
    status.free_heap = (uint32_t)esp_get_free_heap_size();
    strncpy(status.fw_version, FW_VERSION, sizeof(status.fw_version) - 1);
    status.ble_count = ble_scanner_unique_total();
    status.ble_active = (uint32_t)ble_scanner_active_count();
    status.wifi_rssi = 0;

    mqtt_reporter_publish_status(&status);
}

static void scanner_main_loop(void *arg)
{
    ble_adv_t *ble_buf = heap_caps_malloc(MAX_BLE_TRACKED * sizeof(ble_adv_t),
                                          MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!ble_buf) {
        ble_buf = malloc(MAX_BLE_TRACKED * sizeof(ble_adv_t));
    }
    configASSERT(ble_buf);

    int count;
    int64_t last_status_time = 0;
    bool scan_running = false;

    scan_running = ble_scanner_start();
    if (!scan_running) {
        ESP_LOGW(TAG, "BLE scan not started yet, will retry in loop");
    }

    while (!s_network_up) {
        ESP_LOGI(TAG, "Waiting for network before publishing...");
        vTaskDelay(pdMS_TO_TICKS(1000));
    }
    ESP_LOGI(TAG, "Network up, starting scan+publish loop");

    while (1) {
        if (!scan_running) {
            scan_running = ble_scanner_start();
            if (!scan_running) {
                ESP_LOGW(TAG, "BLE scan not running, will retry next cycle");
            }
        }

        vTaskDelay(pdMS_TO_TICKS(SCAN_BATCH_INTERVAL_MS));

        /* Publish all currently-tracked devices */
        ble_scanner_drain_seen(ble_buf, MAX_BLE_TRACKED, &count);
        for (int i = 0; i < count; i++) {
            mqtt_reporter_publish_scan((scan_result_t *)&ble_buf[i]);
        }
        if (count > 0) {
            mqtt_reporter_flush();
            ESP_LOGI(TAG, "BLE seen: %d unique devices (total unique: %"PRIu32")",
                     count, ble_scanner_unique_total());

            int shown = count < 5 ? count : 5;
            for (int i = 0; i < shown; i++) {
                const ble_adv_t *a = &ble_buf[i];
                char mac[18];
                snprintf(mac, sizeof(mac), "%02X:%02X:%02X:%02X:%02X:%02X",
                         a->base.mac[0], a->base.mac[1], a->base.mac[2],
                         a->base.mac[3], a->base.mac[4], a->base.mac[5]);
                if (a->name_len > 0) {
                    ESP_LOGI(TAG, "  [%d] %s rssi=%d x%"PRIu32" name=\"%.*s\"",
                             i, mac, a->base.rssi, a->seen_count,
                             a->name_len, a->name);
                } else {
                    ESP_LOGI(TAG, "  [%d] %s rssi=%d x%"PRIu32,
                             i, mac, a->base.rssi, a->seen_count);
                }
            }
            if (count > 5) {
                ESP_LOGI(TAG, "  ... and %d more", count - 5);
            }
        }

        /* Publish "gone" for devices that timed out */
        int gone_count;
        ble_scanner_drain_gone(ble_buf, MAX_BLE_TRACKED, &gone_count);
        for (int i = 0; i < gone_count; i++) {
            mqtt_reporter_publish_scan((scan_result_t *)&ble_buf[i]);
        }
        if (gone_count > 0) {
            mqtt_reporter_flush();
            ESP_LOGI(TAG, "BLE gone: %d devices evicted", gone_count);
        }

        int64_t now = esp_timer_get_time() / 1000;
        if (now - last_status_time >= STATUS_REPORT_INTERVAL_MS) {
            publish_status();
            last_status_time = now;
        }

        led_ctrl_tick();
        ota_client_check();
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "animated-journey Mesh Scanner (P4) v%s", FW_VERSION);

    led_ctrl_init(STATUS_LED_GPIO);

    esp_err_t audio_ret = audio_beep_init();
    if (audio_ret != ESP_OK) {
        ESP_LOGW(TAG, "Audio beep init failed (%s) -- identify will be LED-only",
                 esp_err_to_name(audio_ret));
    }

    ESP_ERROR_CHECK(nvs_config_init());
    nvs_config_load(&s_config);

    ESP_LOGI(TAG, "Node ID: %s", s_config.node_id);

    /* Start Ethernet (non-blocking, fires callback when IP arrives) */
    esp_err_t eth_ret = ethernet_init();
    if (eth_ret == ESP_OK) {
        ethernet_set_ip_callback(on_ip_obtained);

        /* Give DHCP a quick 10s chance before moving on */
        if (ethernet_wait_for_ip(10000)) {
            ESP_LOGI(TAG, "Network ready within initial wait");
        } else {
            ESP_LOGI(TAG, "No IP yet -- MQTT/SNTP will start when DHCP completes");
        }
    } else {
        ESP_LOGW(TAG, "Ethernet driver failed (%s) -- running in BLE scan-only mode",
                 esp_err_to_name(eth_ret));
    }

    /* Initialize BLE scanner (via esp_hosted C6 slave HCI transport) */
    if (!ble_scanner_init()) {
        ESP_LOGE(TAG, "BLE scanner init failed -- scanning will not be available");
    }

    /* P4 is dual-core; run scanner on core 0, NimBLE host on core 1 */
    xTaskCreatePinnedToCore(scanner_main_loop, "scanner_loop", 8192,
                            NULL, 5, NULL, 0);

    ESP_LOGI(TAG, "Scanner started (network=%s)", s_network_up ? "yes" : "pending");
}
