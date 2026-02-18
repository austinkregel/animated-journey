#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "scanner_types.h"
#include "config.h"

#ifdef CONFIG_LORA_ENABLED

static const char *TAG = "lora_scanner";

#define LORA_RING_BUF_SIZE MAX_SCAN_BATCH_SIZE

static scan_result_t s_ring_buf[LORA_RING_BUF_SIZE];
static int s_ring_head = 0;
static int s_ring_count = 0;
static SemaphoreHandle_t s_ring_mutex = NULL;

void lora_scanner_init(void)
{
    s_ring_mutex = xSemaphoreCreateMutex();
    configASSERT(s_ring_mutex);
    ESP_LOGW(TAG, "LoRa scanner not yet implemented");
}

void lora_scanner_start(void)
{
    ESP_LOGW(TAG, "LoRa scanner not yet implemented - start ignored");
}

void lora_scanner_stop(void)
{
    ESP_LOGW(TAG, "LoRa scanner not yet implemented - stop ignored");
}

int lora_scanner_get_results(scan_result_t *results, int max, int *count)
{
    xSemaphoreTake(s_ring_mutex, portMAX_DELAY);
    int n = s_ring_count < max ? s_ring_count : max;
    int tail = (s_ring_head - s_ring_count + LORA_RING_BUF_SIZE) % LORA_RING_BUF_SIZE;
    for (int i = 0; i < n; i++) {
        int idx = (tail + i) % LORA_RING_BUF_SIZE;
        memcpy(&results[i], &s_ring_buf[idx], sizeof(scan_result_t));
    }
    s_ring_count -= n;
    *count = n;
    xSemaphoreGive(s_ring_mutex);
    return n;
}

#endif /* CONFIG_LORA_ENABLED */
