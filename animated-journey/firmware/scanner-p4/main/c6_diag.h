#pragma once

#include "scanner_types.h"

/* ESP-Hosted C6 co-processor diagnostics (P4 only).
 *
 * Subscribes to ESP_HOSTED_EVENT for reset-reason / link-state / reboot
 * tracking, and lazily queries the coprocessor app description + RPC version
 * once the SDIO transport is up. Call c6_diag_init() before the C6 comes up
 * (i.e. before ble_scanner_init()), then c6_diag_fill() from publish_status. */
void c6_diag_init(void);
void c6_diag_fill(node_status_t *status);
