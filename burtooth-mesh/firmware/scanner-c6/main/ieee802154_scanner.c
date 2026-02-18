#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "esp_log.h"
#include "scanner_types.h"
#include "config.h"

static const char *TAG = "ieee802154_scanner";

#define IEEE802154_RING_BUF_SIZE MAX_SCAN_BATCH_SIZE

static scan_result_t s_ring_buf[IEEE802154_RING_BUF_SIZE];
static int s_ring_head = 0;
static int s_ring_count = 0;
static SemaphoreHandle_t s_ring_mutex = NULL;

void ieee802154_scanner_init(void)
{
    s_ring_mutex = xSemaphoreCreateMutex();
    configASSERT(s_ring_mutex);
    ESP_LOGW(TAG, "802.15.4 scanner not yet implemented");
}

void ieee802154_scanner_start(void)
{
    ESP_LOGW(TAG, "802.15.4 scanner not yet implemented - start ignored");
}

void ieee802154_scanner_stop(void)
{
    ESP_LOGW(TAG, "802.15.4 scanner not yet implemented - stop ignored");
}

int ieee802154_scanner_get_results(scan_result_t *results, int max, int *count)
{
    xSemaphoreTake(s_ring_mutex, portMAX_DELAY);
    int n = s_ring_count < max ? s_ring_count : max;
    int tail = (s_ring_head - s_ring_count + IEEE802154_RING_BUF_SIZE) % IEEE802154_RING_BUF_SIZE;
    for (int i = 0; i < n; i++) {
        int idx = (tail + i) % IEEE802154_RING_BUF_SIZE;
        memcpy(&results[i], &s_ring_buf[idx], sizeof(scan_result_t));
    }
    s_ring_count -= n;
    *count = n;
    xSemaphoreGive(s_ring_mutex);
    return n;
}
