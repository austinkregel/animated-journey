#pragma once

#include "mqtt_client.h"

void ota_client_init(esp_mqtt_client_handle_t mqtt_client, const char *node_id);
void ota_client_check(void);
