#include <string.h>
#include <stdio.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "mqtt_client.h"
#include "config.h"
#include "mqtt_reporter.h"
#include "scanner_types.h"

static const char *TAG = "mqtt_reporter";

#define MSG_QUEUE_SIZE 128
#define MSG_MAX_LEN    512
#define TOPIC_MAX_LEN  128

typedef struct {
    char topic[TOPIC_MAX_LEN];
    char payload[MSG_MAX_LEN];
    int  qos;
} mqtt_msg_t;

static esp_mqtt_client_handle_t s_client = NULL;
static bool s_connected = false;
static char s_node_id[32] = {0};

static mqtt_msg_t s_msg_queue[MSG_QUEUE_SIZE];
static int s_queue_head = 0;
static int s_queue_tail = 0;
static int s_queue_count = 0;
static SemaphoreHandle_t s_queue_mutex = NULL;

static bool s_discovery_published = false;

static void enqueue_msg(const char *topic, const char *payload, int qos)
{
    xSemaphoreTake(s_queue_mutex, portMAX_DELAY);
    if (s_queue_count >= MSG_QUEUE_SIZE) {
        s_queue_tail = (s_queue_tail + 1) % MSG_QUEUE_SIZE;
        s_queue_count--;
        ESP_LOGW(TAG, "Message queue full, dropping oldest");
    }
    mqtt_msg_t *msg = &s_msg_queue[s_queue_head];
    strncpy(msg->topic, topic, TOPIC_MAX_LEN - 1);
    msg->topic[TOPIC_MAX_LEN - 1] = '\0';
    strncpy(msg->payload, payload, MSG_MAX_LEN - 1);
    msg->payload[MSG_MAX_LEN - 1] = '\0';
    msg->qos = qos;
    s_queue_head = (s_queue_head + 1) % MSG_QUEUE_SIZE;
    s_queue_count++;
    xSemaphoreGive(s_queue_mutex);
}

static void drain_queue(void)
{
    xSemaphoreTake(s_queue_mutex, portMAX_DELAY);
    while (s_queue_count > 0 && s_connected) {
        mqtt_msg_t *msg = &s_msg_queue[s_queue_tail];
        int msg_id = esp_mqtt_client_publish(s_client, msg->topic, msg->payload,
                                              0, msg->qos, 0);
        if (msg_id < 0) {
            ESP_LOGW(TAG, "Publish failed, will retry");
            break;
        }
        s_queue_tail = (s_queue_tail + 1) % MSG_QUEUE_SIZE;
        s_queue_count--;
    }
    xSemaphoreGive(s_queue_mutex);
}

static void mqtt_event_handler(void *arg, esp_event_base_t base,
                                int32_t event_id, void *event_data)
{
    esp_mqtt_event_handle_t event = event_data;
    switch (event->event_id) {
    case MQTT_EVENT_CONNECTED:
        ESP_LOGI(TAG, "MQTT connected");
        s_connected = true;
        if (!s_discovery_published) {
            mqtt_reporter_publish_discovery(s_node_id, "scanner");
            s_discovery_published = true;
        }
        drain_queue();
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT disconnected");
        s_connected = false;
        break;
    case MQTT_EVENT_ERROR:
        ESP_LOGE(TAG, "MQTT error type: %d", event->error_handle->error_type);
        break;
    default:
        break;
    }
}

static void format_mac(const uint8_t *mac, char *buf, size_t len)
{
    snprintf(buf, len, "%02X:%02X:%02X:%02X:%02X:%02X",
             mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

void mqtt_reporter_init(const mqtt_reporter_config_t *config)
{
    s_queue_mutex = xSemaphoreCreateMutex();
    configASSERT(s_queue_mutex);

    strncpy(s_node_id, config->node_id, sizeof(s_node_id) - 1);

    char uri[128];
    snprintf(uri, sizeof(uri), "mqtt://%s:%u", config->host, config->port);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = uri,
        .credentials.username = config->username,
        .credentials.authentication.password = config->password,
    };

    s_client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID,
                                    mqtt_event_handler, NULL);
    esp_mqtt_client_start(s_client);
    ESP_LOGI(TAG, "MQTT client started, broker=%s:%u", config->host, config->port);
}

void mqtt_reporter_publish_scan(const scan_result_t *result)
{
    char topic[TOPIC_MAX_LEN];
    char payload[MSG_MAX_LEN];
    char mac_str[18];
    format_mac(result->mac, mac_str, sizeof(mac_str));

    const char *type_str;
    switch (result->type) {
    case SCAN_WIFI_PROBE:  type_str = "wifi_probe"; break;
    case SCAN_WIFI_BEACON: type_str = "wifi_beacon"; break;
    case SCAN_BLE_ADV:     type_str = "ble_adv"; break;
    case SCAN_802154:      type_str = "ieee802154"; break;
    case SCAN_LORA:        type_str = "lora"; break;
    default:               type_str = "unknown"; break;
    }

    snprintf(topic, sizeof(topic), "%s/scan/%s/%s",
             DEFAULT_MQTT_TOPIC_PREFIX, type_str, s_node_id);

    if (result->type == SCAN_WIFI_PROBE) {
        const wifi_probe_t *probe = (const wifi_probe_t *)result;
        snprintf(payload, sizeof(payload),
                 "{\"mac\":\"%s\",\"rssi\":%d,\"channel\":%u,"
                 "\"ssid\":\"%.*s\",\"ts\":%" PRId64 "}",
                 mac_str, result->rssi, result->channel,
                 probe->ssid_len, probe->ssid, result->timestamp_ms);
    } else if (result->type == SCAN_WIFI_BEACON) {
        const wifi_beacon_t *beacon = (const wifi_beacon_t *)result;
        char bssid_str[18];
        format_mac(beacon->bssid, bssid_str, sizeof(bssid_str));
        snprintf(payload, sizeof(payload),
                 "{\"mac\":\"%s\",\"rssi\":%d,\"channel\":%u,"
                 "\"ssid\":\"%.*s\",\"bssid\":\"%s\",\"enc\":%u,\"ts\":%" PRId64 "}",
                 mac_str, result->rssi, result->channel,
                 beacon->ssid_len, beacon->ssid, bssid_str,
                 beacon->encryption, result->timestamp_ms);
    } else if (result->type == SCAN_BLE_ADV) {
        const ble_adv_t *adv = (const ble_adv_t *)result;
        snprintf(payload, sizeof(payload),
                 "{\"mac\":\"%s\",\"rssi\":%d,\"name\":\"%.*s\","
                 "\"addr_type\":%u,\"adv_type\":%u,\"tx_power\":%d,\"ts\":%" PRId64 "}",
                 mac_str, result->rssi,
                 adv->name_len, adv->name,
                 adv->addr_type, adv->adv_type,
                 adv->tx_power, result->timestamp_ms);
    } else {
        snprintf(payload, sizeof(payload),
                 "{\"mac\":\"%s\",\"rssi\":%d,\"channel\":%u,\"ts\":%" PRId64 "}",
                 mac_str, result->rssi, result->channel, result->timestamp_ms);
    }

    if (s_connected) {
        esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 0);
    } else {
        enqueue_msg(topic, payload, 0);
    }
}

void mqtt_reporter_publish_status(const node_status_t *status)
{
    char topic[TOPIC_MAX_LEN];
    char payload[MSG_MAX_LEN];

    snprintf(topic, sizeof(topic), "%s/nodes/%s/status",
             DEFAULT_MQTT_TOPIC_PREFIX, status->node_id);

    snprintf(payload, sizeof(payload),
             "{\"node_id\":\"%s\",\"uptime\":%"PRIu32",\"free_heap\":%"PRIu32","
             "\"wifi_rssi\":%d,\"fw_version\":\"%s\","
             "\"probes\":%"PRIu32",\"ble\":%"PRIu32",\"beacons\":%"PRIu32"}",
             status->node_id, status->uptime_s, status->free_heap,
             status->wifi_rssi, status->fw_version,
             status->probe_count, status->ble_count, status->beacon_count);

    if (s_connected) {
        esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 1);
    } else {
        enqueue_msg(topic, payload, 0);
    }
}

void mqtt_reporter_publish_discovery(const char *node_id, const char *model)
{
    char topic[TOPIC_MAX_LEN];
    char payload[MSG_MAX_LEN];

    /* Sensor: WiFi probe count */
    snprintf(topic, sizeof(topic),
             "homeassistant/sensor/animated-journey_%s_probes/config", node_id);
    snprintf(payload, sizeof(payload),
             "{\"name\":\"animated-journey %s Probes\","
             "\"state_topic\":\"%s/nodes/%s/status\","
             "\"value_template\":\"{{ value_json.probes }}\","
             "\"unique_id\":\"animated-journey_%s_probes\","
             "\"device\":{\"identifiers\":[\"animated-journey_%s\"],"
             "\"name\":\"animated-journey %s\",\"model\":\"%s\","
             "\"manufacturer\":\"animated-journey\"}}",
             node_id, DEFAULT_MQTT_TOPIC_PREFIX, node_id,
             node_id, node_id, node_id, model);

    if (s_connected) {
        esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 1);
    } else {
        enqueue_msg(topic, payload, 0);
    }

    /* Sensor: BLE adv count */
    snprintf(topic, sizeof(topic),
             "homeassistant/sensor/animated-journey_%s_ble/config", node_id);
    snprintf(payload, sizeof(payload),
             "{\"name\":\"animated-journey %s BLE\","
             "\"state_topic\":\"%s/nodes/%s/status\","
             "\"value_template\":\"{{ value_json.ble }}\","
             "\"unique_id\":\"animated-journey_%s_ble\","
             "\"device\":{\"identifiers\":[\"animated-journey_%s\"],"
             "\"name\":\"animated-journey %s\",\"model\":\"%s\","
             "\"manufacturer\":\"animated-journey\"}}",
             node_id, DEFAULT_MQTT_TOPIC_PREFIX, node_id,
             node_id, node_id, node_id, model);

    if (s_connected) {
        esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 1);
    } else {
        enqueue_msg(topic, payload, 0);
    }

    /* Sensor: uptime */
    snprintf(topic, sizeof(topic),
             "homeassistant/sensor/animated-journey_%s_uptime/config", node_id);
    snprintf(payload, sizeof(payload),
             "{\"name\":\"animated-journey %s Uptime\","
             "\"state_topic\":\"%s/nodes/%s/status\","
             "\"value_template\":\"{{ value_json.uptime }}\","
             "\"unit_of_measurement\":\"s\","
             "\"unique_id\":\"animated-journey_%s_uptime\","
             "\"device\":{\"identifiers\":[\"animated-journey_%s\"],"
             "\"name\":\"animated-journey %s\",\"model\":\"%s\","
             "\"manufacturer\":\"animated-journey\"}}",
             node_id, DEFAULT_MQTT_TOPIC_PREFIX, node_id,
             node_id, node_id, node_id, model);

    if (s_connected) {
        esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 1);
    } else {
        enqueue_msg(topic, payload, 0);
    }

    /* Sensor: free heap */
    snprintf(topic, sizeof(topic),
             "homeassistant/sensor/animated-journey_%s_heap/config", node_id);
    snprintf(payload, sizeof(payload),
             "{\"name\":\"animated-journey %s Free Heap\","
             "\"state_topic\":\"%s/nodes/%s/status\","
             "\"value_template\":\"{{ value_json.free_heap }}\","
             "\"unit_of_measurement\":\"B\","
             "\"unique_id\":\"animated-journey_%s_heap\","
             "\"device\":{\"identifiers\":[\"animated-journey_%s\"],"
             "\"name\":\"animated-journey %s\",\"model\":\"%s\","
             "\"manufacturer\":\"animated-journey\"}}",
             node_id, DEFAULT_MQTT_TOPIC_PREFIX, node_id,
             node_id, node_id, node_id, model);

    if (s_connected) {
        esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 1);
    } else {
        enqueue_msg(topic, payload, 0);
    }

    ESP_LOGI(TAG, "Published HA MQTT auto-discovery for node %s", node_id);
}

bool mqtt_reporter_is_connected(void)
{
    return s_connected;
}

void mqtt_reporter_flush(void)
{
    if (s_connected) {
        drain_queue();
    }
}
