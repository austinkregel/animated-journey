#pragma once

#include <stdbool.h>

/**
 * Initialize the status LED on the configured GPIO.
 * Call once from app_main before starting the scanner loop.
 */
void led_ctrl_init(int gpio_num);

void led_ctrl_set(bool on);

/**
 * Trigger a rapid identify blink pattern (non-blocking).
 * The pattern runs for ~10 seconds then returns to normal heartbeat.
 */
void led_ctrl_identify(void);

/**
 * Call periodically from the main loop to drive heartbeat / identify patterns.
 */
void led_ctrl_tick(void);

bool led_ctrl_is_identifying(void);
