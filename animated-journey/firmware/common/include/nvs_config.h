#pragma once

#include <stdint.h>
#include "esp_err.h"

typedef struct {
    char     node_id[32];
    char     wifi_ssid[32];
    char     wifi_password[64];
    char     mqtt_host[64];
    uint16_t mqtt_port;
    char     mqtt_username[32];
    char     mqtt_password[64];
} nvs_config_t;

esp_err_t nvs_config_init(void);
esp_err_t nvs_config_load(nvs_config_t *config);
esp_err_t nvs_config_save(const nvs_config_t *config);
void      nvs_config_set_defaults(nvs_config_t *config);
