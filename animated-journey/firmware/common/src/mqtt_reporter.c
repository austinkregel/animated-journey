#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <inttypes.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "esp_heap_caps.h"
#include "mqtt_client.h"
#include "config.h"
#include "mqtt_reporter.h"
#include "scanner_types.h"

static const char *TAG = "mqtt_reporter";

#if defined(CONFIG_IDF_TARGET_ESP32P4)
#define MSG_QUEUE_SIZE 256
#else
#define MSG_QUEUE_SIZE 128
#endif
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

static mqtt_msg_t *s_msg_queue = NULL;
static int s_queue_head = 0;
static int s_queue_tail = 0;
static int s_queue_count = 0;
static SemaphoreHandle_t s_queue_mutex = NULL;

static bool s_discovery_published = false;
static bool s_initialized = false;
static mqtt_command_cb_t s_command_cb = NULL;
static char s_cmd_topic_prefix[TOPIC_MAX_LEN] = {0};

static uint32_t s_drop_count = 0;

static void enqueue_msg(const char *topic, const char *payload, int qos)
{
    xSemaphoreTake(s_queue_mutex, portMAX_DELAY);
    if (s_queue_count >= MSG_QUEUE_SIZE) {
        s_queue_tail = (s_queue_tail + 1) % MSG_QUEUE_SIZE;
        s_queue_count--;
        s_drop_count++;
        if (s_drop_count == 1 || (s_drop_count % 256) == 0) {
            ESP_LOGW(TAG, "Message queue full, %"PRIu32" messages dropped total", s_drop_count);
        }
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
        s_drop_count = 0;
        if (!s_discovery_published) {
            mqtt_reporter_publish_discovery(s_node_id, "scanner");
            s_discovery_published = true;
        }
        if (s_cmd_topic_prefix[0]) {
            char sub_topic[TOPIC_MAX_LEN + 4];
            snprintf(sub_topic, sizeof(sub_topic), "%s/#", s_cmd_topic_prefix);
            esp_mqtt_client_subscribe(s_client, sub_topic, 1);
            ESP_LOGI(TAG, "Subscribed to %s", sub_topic);
        }
        drain_queue();
        break;
    case MQTT_EVENT_DISCONNECTED:
        ESP_LOGW(TAG, "MQTT disconnected");
        s_connected = false;
        break;
    case MQTT_EVENT_DATA:
        if (s_command_cb && event->topic_len > 0 && event->topic) {
            size_t prefix_len = strlen(s_cmd_topic_prefix);
            if ((size_t)event->topic_len > prefix_len + 1 &&
                memcmp(event->topic, s_cmd_topic_prefix, prefix_len) == 0 &&
                event->topic[prefix_len] == '/') {
                const char *cmd = event->topic + prefix_len + 1;
                int cmd_len = event->topic_len - prefix_len - 1;
                char cmd_buf[64];
                int n = cmd_len < (int)sizeof(cmd_buf) - 1 ? cmd_len : (int)sizeof(cmd_buf) - 1;
                memcpy(cmd_buf, cmd, n);
                cmd_buf[n] = '\0';
                s_command_cb(cmd_buf, event->data, event->data_len);
            }
        }
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

    s_msg_queue = heap_caps_malloc(MSG_QUEUE_SIZE * sizeof(mqtt_msg_t),
                                   MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_msg_queue) {
        s_msg_queue = malloc(MSG_QUEUE_SIZE * sizeof(mqtt_msg_t));
    }
    if (!s_msg_queue) {
        ESP_LOGE(TAG, "Failed to allocate msg queue (%u x %u = %u bytes) -- MQTT disabled",
                 (unsigned)MSG_QUEUE_SIZE, (unsigned)sizeof(mqtt_msg_t),
                 (unsigned)(MSG_QUEUE_SIZE * sizeof(mqtt_msg_t)));
        return;
    }
    memset(s_msg_queue, 0, MSG_QUEUE_SIZE * sizeof(mqtt_msg_t));

    strncpy(s_node_id, config->node_id, sizeof(s_node_id) - 1);

    char uri[128];
    snprintf(uri, sizeof(uri), "mqtt://%s:%u", config->host, config->port);

    esp_mqtt_client_config_t mqtt_cfg = {
        .broker.address.uri = uri,
        .credentials.username = config->username,
        .credentials.authentication.password = config->password,
    };

    snprintf(s_cmd_topic_prefix, sizeof(s_cmd_topic_prefix),
             "%s/nodes/%s/cmd", DEFAULT_MQTT_TOPIC_PREFIX, config->node_id);

    s_client = esp_mqtt_client_init(&mqtt_cfg);
    esp_mqtt_client_register_event(s_client, ESP_EVENT_ANY_ID,
                                    mqtt_event_handler, NULL);
    esp_mqtt_client_start(s_client);
    s_initialized = true;
    ESP_LOGI(TAG, "MQTT client started, broker=%s:%u", config->host, config->port);
}

void mqtt_reporter_set_command_callback(mqtt_command_cb_t cb)
{
    s_command_cb = cb;
}

void mqtt_reporter_publish_scan(const scan_result_t *result)
{
    if (!s_initialized) {
        static bool warned = false;
        if (!warned) {
            ESP_LOGW(TAG, "publish_scan called but MQTT not initialized (no network?), scan results will be dropped");
            warned = true;
        }
        return;
    }
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

    double timestamp_s = result->timestamp_ms / 1000.0;

    if (result->type == SCAN_WIFI_PROBE) {
        const wifi_probe_t *probe = (const wifi_probe_t *)result;
        snprintf(payload, sizeof(payload),
                 "{\"mac\":\"%s\",\"rssi\":%d,\"channel\":%u,"
                 "\"ssid\":\"%.*s\",\"node_id\":\"%s\",\"type\":\"%s\","
                 "\"timestamp\":%.3f}",
                 mac_str, result->rssi, result->channel,
                 probe->ssid_len, probe->ssid,
                 s_node_id, type_str, timestamp_s);
    } else if (result->type == SCAN_WIFI_BEACON) {
        const wifi_beacon_t *beacon = (const wifi_beacon_t *)result;
        char bssid_str[18];
        format_mac(beacon->bssid, bssid_str, sizeof(bssid_str));
        snprintf(payload, sizeof(payload),
                 "{\"mac\":\"%s\",\"rssi\":%d,\"channel\":%u,"
                 "\"ssid\":\"%.*s\",\"bssid\":\"%s\",\"enc\":%u,"
                 "\"node_id\":\"%s\",\"type\":\"%s\",\"timestamp\":%.3f}",
                 mac_str, result->rssi, result->channel,
                 beacon->ssid_len, beacon->ssid, bssid_str,
                 beacon->encryption,
                 s_node_id, type_str, timestamp_s);
    } else if (result->type == SCAN_BLE_ADV) {
        const ble_adv_t *adv = (const ble_adv_t *)result;
        const char *event_str = (adv->event == BLE_EVENT_GONE) ? "gone" : "seen";
        snprintf(payload, sizeof(payload),
                 "{\"mac\":\"%s\",\"rssi\":%d,\"name\":\"%.*s\","
                 "\"addr_type\":%u,\"adv_type\":%u,\"tx_power\":%d,"
                 "\"seen_count\":%"PRIu32",\"first_seen\":%.3f,"
                 "\"event\":\"%s\","
                 "\"node_id\":\"%s\",\"type\":\"%s\",\"timestamp\":%.3f}",
                 mac_str, result->rssi,
                 adv->name_len, adv->name,
                 adv->addr_type, adv->adv_type, adv->tx_power,
                 adv->seen_count, adv->first_seen_s,
                 event_str,
                 s_node_id, type_str, timestamp_s);
    } else {
        snprintf(payload, sizeof(payload),
                 "{\"mac\":\"%s\",\"rssi\":%d,\"channel\":%u,"
                 "\"node_id\":\"%s\",\"type\":\"%s\",\"timestamp\":%.3f}",
                 mac_str, result->rssi, result->channel,
                 s_node_id, type_str, timestamp_s);
    }

    if (s_connected) {
        esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 0);
    } else {
        enqueue_msg(topic, payload, 0);
    }
}

void mqtt_reporter_publish_status(const node_status_t *status)
{
    if (!s_initialized) {
        static bool warned = false;
        if (!warned) {
            ESP_LOGW(TAG, "publish_status called but MQTT not initialized (no network?), status reports will be dropped");
            warned = true;
        }
        return;
    }
    char topic[TOPIC_MAX_LEN];
    char payload[MSG_MAX_LEN];

    snprintf(topic, sizeof(topic), "%s/nodes/%s/status",
             DEFAULT_MQTT_TOPIC_PREFIX, status->node_id);

    snprintf(payload, sizeof(payload),
             "{\"node_id\":\"%s\",\"uptime\":%"PRIu32",\"free_heap\":%"PRIu32","
             "\"wifi_rssi\":%d,\"fw_version\":\"%s\","
             "\"probes\":%"PRIu32",\"ble\":%"PRIu32",\"beacons\":%"PRIu32","
             "\"ble_active\":%"PRIu32"}",
             status->node_id, status->uptime_s, status->free_heap,
             status->wifi_rssi, status->fw_version,
             status->probe_count, status->ble_count, status->beacon_count,
             status->ble_active);

    if (s_connected) {
        esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 1);
    } else {
        enqueue_msg(topic, payload, 0);
    }
}

static void publish_discovery_sensor(const char *node_id, const char *model,
                                     const char *suffix, const char *name_suffix,
                                     const char *value_tpl,
                                     const char *unit, const char *dev_class,
                                     const char *entity_cat)
{
    char topic[TOPIC_MAX_LEN];
    char payload[MSG_MAX_LEN];

    snprintf(topic, sizeof(topic),
             "homeassistant/sensor/animated-journey_%s_%s/config", node_id, suffix);

    int n = snprintf(payload, sizeof(payload),
             "{\"name\":\"%s\","
             "\"state_topic\":\"%s/nodes/%s/status\","
             "\"value_template\":\"%s\","
             "\"unique_id\":\"animated-journey_%s_%s\","
             "\"object_id\":\"aj_%s_%s\"",
             name_suffix,
             DEFAULT_MQTT_TOPIC_PREFIX, node_id, value_tpl,
             node_id, suffix, node_id, suffix);

    if (unit) {
        n += snprintf(payload + n, sizeof(payload) - n,
                      ",\"unit_of_measurement\":\"%s\"", unit);
    }
    if (dev_class) {
        n += snprintf(payload + n, sizeof(payload) - n,
                      ",\"device_class\":\"%s\"", dev_class);
    }
    if (entity_cat) {
        n += snprintf(payload + n, sizeof(payload) - n,
                      ",\"entity_category\":\"%s\"", entity_cat);
    }
    snprintf(payload + n, sizeof(payload) - n,
             ",\"device\":{\"identifiers\":[\"animated-journey_%s\"],"
             "\"name\":\"animated-journey %s\",\"model\":\"%s\","
             "\"manufacturer\":\"animated-journey\"}}",
             node_id, node_id, model);

    if (s_connected) {
        esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 1);
    } else {
        enqueue_msg(topic, payload, 0);
    }
}

void mqtt_reporter_publish_discovery(const char *node_id, const char *model)
{
    if (!s_initialized) {
        ESP_LOGW(TAG, "publish_discovery called but MQTT not initialized (no network?), discovery will not be sent");
        return;
    }

    publish_discovery_sensor(node_id, model, "ble", "BLE Unique Total",
        "{{ value_json.ble }}", NULL, NULL, NULL);

    publish_discovery_sensor(node_id, model, "ble_active", "BLE Active Devices",
        "{{ value_json.ble_active }}", NULL, NULL, NULL);

    publish_discovery_sensor(node_id, model, "probes", "WiFi Probes",
        "{{ value_json.probes }}", NULL, NULL, NULL);

    publish_discovery_sensor(node_id, model, "beacons", "WiFi Beacons",
        "{{ value_json.beacons }}", NULL, NULL, NULL);

    publish_discovery_sensor(node_id, model, "uptime", "Uptime",
        "{{ value_json.uptime }}", "s", "duration", NULL);

    publish_discovery_sensor(node_id, model, "heap", "Free Heap",
        "{{ value_json.free_heap }}", "B", NULL, "diagnostic");

    publish_discovery_sensor(node_id, model, "fw", "Firmware",
        "{{ value_json.fw_version }}", NULL, NULL, "diagnostic");

    publish_discovery_sensor(node_id, model, "wifi_rssi", "WiFi RSSI",
        "{{ value_json.wifi_rssi }}", "dBm", "signal_strength", "diagnostic");

    /* Button: Identify -- blinks the status LED for 10s */
    {
        char topic[TOPIC_MAX_LEN];
        char payload[MSG_MAX_LEN];

        snprintf(topic, sizeof(topic),
                 "homeassistant/button/animated-journey_%s_identify/config", node_id);
        snprintf(payload, sizeof(payload),
                 "{\"name\":\"Identify\","
                 "\"command_topic\":\"%s/nodes/%s/cmd/identify\","
                 "\"unique_id\":\"animated-journey_%s_identify\","
                 "\"object_id\":\"aj_%s_identify\","
                 "\"device_class\":\"identify\","
                 "\"entity_category\":\"config\","
                 "\"device\":{\"identifiers\":[\"animated-journey_%s\"],"
                 "\"name\":\"animated-journey %s\",\"model\":\"%s\","
                 "\"manufacturer\":\"animated-journey\"}}",
                 DEFAULT_MQTT_TOPIC_PREFIX, node_id,
                 node_id, node_id,
                 node_id, node_id, model);

        if (s_connected) {
            esp_mqtt_client_publish(s_client, topic, payload, 0, 0, 1);
        } else {
            enqueue_msg(topic, payload, 0);
        }
    }

    ESP_LOGI(TAG, "Published HA MQTT auto-discovery for node %s (8 sensors + 1 button)", node_id);
}

bool mqtt_reporter_is_connected(void)
{
    return s_connected;
}

esp_mqtt_client_handle_t mqtt_reporter_get_client(void)
{
    return s_client;
}

void mqtt_reporter_flush(void)
{
    if (!s_initialized) {
        return;
    }
    if (s_connected) {
        drain_queue();
    }
}
