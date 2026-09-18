#include <string.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_heap_caps.h"
#include "esp_netif_sntp.h"
#include "esp_system.h"
#include "esp_chip_info.h"
#include "esp_idf_version.h"
#include "esp_clk_tree.h"
#include "driver/temperature_sensor.h"
#include "nvs_flash.h"
#include "config.h"
#include "nvs_config.h"
#include "mqtt_reporter.h"
#include "ota_client.h"
#include "scanner_types.h"

static const char *TAG = "main";
static temperature_sensor_handle_t s_temp_sensor = NULL;

/* Declared in wifi_scanner.c */
extern void wifi_scanner_init(void);
extern void wifi_scanner_start(void);
extern void wifi_scanner_stop(void);
extern int  wifi_scanner_get_results(scan_result_t *results, int max, int *count);

/* Declared in ble_scanner.c */
extern void ble_scanner_init(void);
extern void ble_scanner_start(void);
extern void ble_scanner_stop(void);
extern int  ble_scanner_get_results(ble_adv_t *results, int max, int *count);

static nvs_config_t s_config;
static uint32_t s_probe_count = 0;
static uint32_t s_ble_count = 0;
static uint32_t s_beacon_count = 0;

static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0

static void wifi_event_handler(void *arg, esp_event_base_t base,
                                int32_t event_id, void *event_data)
{
    if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WiFi disconnected, reconnecting...");
        xEventGroupClearBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
        vTaskDelay(pdMS_TO_TICKS(1000));
        esp_wifi_connect();
    } else if (base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)event_data;
        ESP_LOGI(TAG, "Got IP: " IPSTR, IP2STR(&event->ip_info.ip));
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

static void wifi_init_sta(void)
{
    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, NULL));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, NULL));

    wifi_config_t wifi_cfg = {0};
    strncpy((char *)wifi_cfg.sta.ssid, s_config.wifi_ssid,
            sizeof(wifi_cfg.sta.ssid) - 1);
    strncpy((char *)wifi_cfg.sta.password, s_config.wifi_password,
            sizeof(wifi_cfg.sta.password) - 1);

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "WiFi STA initialized, connecting to %s", s_config.wifi_ssid);

    xEventGroupWaitBits(s_wifi_event_group, WIFI_CONNECTED_BIT,
                        pdFALSE, pdFALSE, pdMS_TO_TICKS(15000));
}

static void sntp_init_time(void)
{
    ESP_LOGI(TAG, "Initializing SNTP");
    esp_sntp_config_t config = ESP_NETIF_SNTP_DEFAULT_CONFIG("pool.ntp.org");
    esp_netif_sntp_init(&config);
}

static const char *chip_model_name(esp_chip_model_t model)
{
    switch (model) {
    case CHIP_ESP32:   return "ESP32";
    case CHIP_ESP32S2: return "ESP32-S2";
    case CHIP_ESP32S3: return "ESP32-S3";
    case CHIP_ESP32C3: return "ESP32-C3";
    case CHIP_ESP32C2: return "ESP32-C2";
    case CHIP_ESP32C6: return "ESP32-C6";
    case CHIP_ESP32H2: return "ESP32-H2";
    case CHIP_ESP32P4: return "ESP32-P4";
    default:           return "Unknown";
    }
}

static void publish_status(void)
{
    node_status_t status = {0};
    strncpy(status.node_id, s_config.node_id, sizeof(status.node_id) - 1);
    status.uptime_s = (uint32_t)(esp_timer_get_time() / 1000000ULL);
    status.free_heap = (uint32_t)esp_get_free_heap_size();
    strncpy(status.fw_version, FW_VERSION, sizeof(status.fw_version) - 1);
    status.probe_count = s_probe_count;
    status.ble_count = s_ble_count;
    status.beacon_count = s_beacon_count;

    wifi_ap_record_t ap_info;
    if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) {
        status.wifi_rssi = ap_info.rssi;
    }

    if (s_temp_sensor) {
        temperature_sensor_get_celsius(s_temp_sensor, &status.chip_temp_c);
    }

    status.min_free_heap = (uint32_t)esp_get_minimum_free_heap_size();
    status.psram_free = (uint32_t)heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
    status.psram_total = (uint32_t)heap_caps_get_total_size(MALLOC_CAP_SPIRAM);
    status.reset_reason = (uint8_t)esp_reset_reason();

    esp_chip_info_t ci;
    esp_chip_info(&ci);
    status.cpu_count = ci.cores;
    strncpy(status.chip_model, chip_model_name(ci.model), sizeof(status.chip_model) - 1);

    uint32_t cpu_hz = 0;
    esp_clk_tree_src_get_freq_hz(SOC_MOD_CLK_CPU, ESP_CLK_TREE_SRC_FREQ_PRECISION_CACHED, &cpu_hz);
    status.cpu_freq_mhz = (uint16_t)(cpu_hz / 1000000);

    strncpy(status.idf_version, esp_get_idf_version(), sizeof(status.idf_version) - 1);

    mqtt_reporter_publish_status(&status);
}

static void scanner_main_loop(void *arg)
{
    static scan_result_t wifi_results[MAX_SCAN_BATCH_SIZE];
    static ble_adv_t ble_results[MAX_SCAN_BATCH_SIZE];
    int count;
    int64_t last_status_time = 0;

    while (1) {
        int64_t now = esp_timer_get_time() / 1000;

        /* --- WiFi scan phase --- */
        wifi_scanner_start();
        vTaskDelay(pdMS_TO_TICKS(WIFI_SCAN_DURATION_MS));
        wifi_scanner_stop();

        /* Collect and publish WiFi results */
        wifi_scanner_get_results(wifi_results, MAX_SCAN_BATCH_SIZE, &count);
        for (int i = 0; i < count; i++) {
            mqtt_reporter_publish_scan(&wifi_results[i]);
            if (wifi_results[i].type == SCAN_WIFI_PROBE) {
                s_probe_count++;
            } else if (wifi_results[i].type == SCAN_WIFI_BEACON) {
                s_beacon_count++;
            }
        }
        mqtt_reporter_flush();
        vTaskDelay(pdMS_TO_TICKS(MQTT_FLUSH_INTERVAL_MS));

        /* --- BLE scan phase --- */
        ble_scanner_start();
        vTaskDelay(pdMS_TO_TICKS(SCAN_BATCH_INTERVAL_MS));
        ble_scanner_stop();

        ble_scanner_get_results(ble_results, MAX_SCAN_BATCH_SIZE, &count);
        for (int i = 0; i < count; i++) {
            mqtt_reporter_publish_scan((scan_result_t *)&ble_results[i]);
            s_ble_count++;
        }
        mqtt_reporter_flush();

        /* --- Status report --- */
        now = esp_timer_get_time() / 1000;
        if (now - last_status_time >= STATUS_REPORT_INTERVAL_MS) {
            publish_status();
            last_status_time = now;
        }

        /* --- OTA check --- */
        ota_client_check();

        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

void app_main(void)
{
    ESP_LOGI(TAG, "animated-journey Mesh Scanner (S3) v%s", FW_VERSION);

    /* Internal die temperature sensor */
    temperature_sensor_config_t temp_cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(20, 100);
    if (temperature_sensor_install(&temp_cfg, &s_temp_sensor) == ESP_OK) {
        temperature_sensor_enable(s_temp_sensor);
        float t;
        temperature_sensor_get_celsius(s_temp_sensor, &t);
        ESP_LOGI(TAG, "Chip temperature at boot: %.1f C", t);
    } else {
        ESP_LOGW(TAG, "Temperature sensor init failed");
    }

    /* Initialize NVS and load config */
    ESP_ERROR_CHECK(nvs_config_init());
    nvs_config_load(&s_config);

    ESP_LOGI(TAG, "Node ID: %s", s_config.node_id);

    /* Connect to WiFi */
    wifi_init_sta();

    /* Initialize SNTP */
    sntp_init_time();

    /* Initialize MQTT */
    mqtt_reporter_config_t mqtt_cfg = {
        .host = s_config.mqtt_host,
        .port = s_config.mqtt_port,
        .username = s_config.mqtt_username,
        .password = s_config.mqtt_password,
        .node_id = s_config.node_id,
    };
    mqtt_reporter_init(&mqtt_cfg);

    /* Wait for MQTT connection */
    int retries = 0;
    while (!mqtt_reporter_is_connected() && retries < 30) {
        vTaskDelay(pdMS_TO_TICKS(1000));
        retries++;
    }

    /* Initialize OTA client */
    ota_client_init(mqtt_reporter_get_client(), s_config.node_id);

    /* Publish HA discovery */
    mqtt_reporter_publish_discovery(s_config.node_id, "ESP32-S3");

    /* Initialize scanners */
    wifi_scanner_init();
    ble_scanner_init();

    /* Start main scanner loop on core 0; BLE host runs on core 1 via NimBLE */
    xTaskCreatePinnedToCore(scanner_main_loop, "scanner_loop", 4096,
                            NULL, 5, NULL, 0);

    ESP_LOGI(TAG, "Scanner started");
}
