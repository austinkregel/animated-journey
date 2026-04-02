#include <string.h>
#include <stdbool.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "esp_https_ota.h"
#include "mqtt_client.h"
#include "cJSON.h"
#include "config.h"
#include "ota_client.h"

static const char *TAG = "ota_client";

static esp_mqtt_client_handle_t s_mqtt_client = NULL;
static char s_node_id[32] = {0};
static char s_ota_url[256] = {0};
static bool s_ota_pending = false;

static void publish_ota_status(const char *status_msg)
{
    if (s_mqtt_client == NULL) return;

    char topic[128];
    char payload[256];
    snprintf(topic, sizeof(topic), "%s/nodes/%s/status",
             DEFAULT_MQTT_TOPIC_PREFIX, s_node_id);
    snprintf(payload, sizeof(payload), "{\"ota_status\":\"%s\"}", status_msg);
    esp_mqtt_client_publish(s_mqtt_client, topic, payload, 0, 1, 0);
}

static void ota_task(void *arg)
{
    ESP_LOGI(TAG, "Starting OTA from %s", s_ota_url);
    publish_ota_status("downloading");

    esp_http_client_config_t http_cfg = {
        .url = s_ota_url,
        .timeout_ms = 30000,
    };

    esp_https_ota_config_t ota_cfg = {
        .http_config = &http_cfg,
    };

    esp_err_t ret = esp_https_ota(&ota_cfg);
    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "OTA succeeded, rebooting in 2 seconds");
        publish_ota_status("success");
        vTaskDelay(pdMS_TO_TICKS(2000));
        esp_restart();
    } else {
        ESP_LOGE(TAG, "OTA failed: %s", esp_err_to_name(ret));
        publish_ota_status("error");
    }

    s_ota_pending = false;
    vTaskDelete(NULL);
}

static void mqtt_ota_data_handler(const char *data, int data_len)
{
    if (s_ota_pending) {
        ESP_LOGW(TAG, "OTA already in progress, ignoring");
        return;
    }

    char buf[512];
    int copy_len = data_len < (int)(sizeof(buf) - 1) ? data_len : (int)(sizeof(buf) - 1);
    memcpy(buf, data, copy_len);
    buf[copy_len] = '\0';

    const char *url = buf;

    cJSON *root = cJSON_Parse(buf);
    if (root) {
        cJSON *url_item = cJSON_GetObjectItem(root, "url");
        if (cJSON_IsString(url_item) && url_item->valuestring) {
            url = url_item->valuestring;
        }
    }

    int url_len = strlen(url);
    if (url_len == 0 || url_len >= (int)sizeof(s_ota_url)) {
        ESP_LOGW(TAG, "Empty or too-long OTA URL");
        cJSON_Delete(root);
        return;
    }

    strncpy(s_ota_url, url, sizeof(s_ota_url) - 1);
    s_ota_url[sizeof(s_ota_url) - 1] = '\0';
    cJSON_Delete(root);

    ESP_LOGI(TAG, "OTA URL received: %s", s_ota_url);
    s_ota_pending = true;
}

static void mqtt_event_handler(void *arg, esp_event_base_t base,
                                int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;

    switch (event->event_id) {
    case MQTT_EVENT_CONNECTED: {
        char topic[128];
        snprintf(topic, sizeof(topic), "%s/nodes/%s/%s",
                 DEFAULT_MQTT_TOPIC_PREFIX, s_node_id, OTA_TOPIC_SUFFIX);
        esp_mqtt_client_subscribe(s_mqtt_client, topic, 1);
        ESP_LOGI(TAG, "Subscribed to OTA topic: %s", topic);
        break;
    }
    case MQTT_EVENT_DATA: {
        char ota_topic[128];
        snprintf(ota_topic, sizeof(ota_topic), "%s/nodes/%s/%s",
                 DEFAULT_MQTT_TOPIC_PREFIX, s_node_id, OTA_TOPIC_SUFFIX);
        if (event->topic_len == (int)strlen(ota_topic) &&
            strncmp(event->topic, ota_topic, event->topic_len) == 0) {
            mqtt_ota_data_handler(event->data, event->data_len);
        }
        break;
    }
    default:
        break;
    }
}

void ota_client_init(esp_mqtt_client_handle_t mqtt_client, const char *node_id)
{
    s_mqtt_client = mqtt_client;
    strncpy(s_node_id, node_id, sizeof(s_node_id) - 1);

    esp_mqtt_client_register_event(mqtt_client, ESP_EVENT_ANY_ID,
                                    mqtt_event_handler, NULL);
    ESP_LOGI(TAG, "OTA client initialized for node %s", s_node_id);
}

void ota_client_check(void)
{
    if (s_ota_pending) {
        s_ota_pending = false;
        xTaskCreate(ota_task, "ota_task", 8192, NULL, 5, NULL);
    }
}
