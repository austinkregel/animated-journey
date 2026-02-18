#pragma once

#include <stdbool.h>
#include "scanner_types.h"
#include "nvs_config.h"

typedef struct {
    const char *host;
    uint16_t    port;
    const char *username;
    const char *password;
    const char *node_id;
} mqtt_reporter_config_t;

void mqtt_reporter_init(const mqtt_reporter_config_t *config);
void mqtt_reporter_publish_scan(const scan_result_t *result);
void mqtt_reporter_publish_status(const node_status_t *status);
void mqtt_reporter_publish_discovery(const char *node_id, const char *model);
bool mqtt_reporter_is_connected(void);
void mqtt_reporter_flush(void);
