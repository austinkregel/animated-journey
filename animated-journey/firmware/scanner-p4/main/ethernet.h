#pragma once

#include <stdbool.h>
#include "esp_err.h"

typedef void (*ethernet_ip_callback_t)(void);

/**
 * Start Ethernet. Returns ESP_OK if the PHY was found and driver started.
 * Does NOT block waiting for DHCP -- call ethernet_wait_for_ip() or
 * register a callback with ethernet_set_ip_callback() instead.
 */
esp_err_t ethernet_init(void);

/**
 * Register a callback that fires when an IP address is obtained.
 * If an IP was already obtained before this call, fires immediately.
 * Must be called before or after ethernet_init().
 */
void ethernet_set_ip_callback(ethernet_ip_callback_t cb);

/**
 * Block up to timeout_ms waiting for an IP. Returns true if IP obtained.
 */
bool ethernet_wait_for_ip(int timeout_ms);

bool ethernet_has_ip(void);
