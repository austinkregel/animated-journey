#pragma once

#include <stdbool.h>
#include "esp_err.h"

/**
 * Initialize the onboard audio path: I2C -> ES8311 codec -> I2S TX -> NS4150B PA.
 * Returns ESP_OK on success. If the codec is not found on I2C, returns an error
 * and subsequent play calls become no-ops.
 */
esp_err_t audio_beep_init(void);

/**
 * Play a tone at the given frequency for the given duration, repeated `repeats` times
 * with a gap between each repeat. Non-blocking: spawns a one-shot task.
 * No-op if init failed or a beep is already playing.
 */
void audio_beep_play(int freq_hz, int duration_ms, int gap_ms, int repeats);

/**
 * Play a distinctive 3-beep identify pattern (~3 seconds total).
 * Non-blocking. No-op if init failed.
 */
void audio_beep_identify(void);
