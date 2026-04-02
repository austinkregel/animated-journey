#pragma once

#include <stdbool.h>
#include "mqtt_client.h"
#include "scanner_types.h"
#include "nvs_config.h"

typedef struct {
    const char *host;
    uint16_t    port;
    const char *username;
    const char *password;
    const char *node_id;
} mqtt_reporter_config_t;

typedef void (*mqtt_command_cb_t)(const char *command, const char *payload, int payload_len);

void mqtt_reporter_init(const mqtt_reporter_config_t *config);
void mqtt_reporter_set_command_callback(mqtt_command_cb_t cb);
void mqtt_reporter_publish_scan(const scan_result_t *result);
void mqtt_reporter_publish_status(const node_status_t *status);
void mqtt_reporter_publish_discovery(const char *node_id, const char *model);
bool mqtt_reporter_is_connected(void);
esp_mqtt_client_handle_t mqtt_reporter_get_client(void);
void mqtt_reporter_flush(void);
