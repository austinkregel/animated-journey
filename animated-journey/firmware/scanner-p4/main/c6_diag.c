#include <string.h>
#include <stdio.h>
#include "esp_log.h"
#include "esp_event.h"
#include "esp_hosted.h"
#include "esp_hosted_misc.h"
#include "esp_hosted_event.h"
#include "c6_diag.h"

static const char *TAG = "c6_diag";

/* Cached coprocessor diagnostics, updated from ESP-Hosted events and a one-time
 * RPC query. Written from the event loop / publish task, read from publish_status
 * -- all in task context (no ISR), single-writer-ish, so plain globals are fine. */
static uint8_t  s_reset_reason = 0;   /* esp_reset_reason_t from CP_INIT */
static uint8_t  s_link_up = 0;
static uint32_t s_reboots = 0;
static bool     s_info_queried = false;
static char     s_fw[32] = {0};
static char     s_chip[16] = {0};
static char     s_rpc[16] = {0};

static void c6_query_info(void)
{
    /* App description: network_adapter version, project name, IDF version. */
    esp_hosted_app_desc_t desc = {0};
    if (esp_hosted_get_coprocessor_app_desc(&desc) == ESP_OK) {
        strncpy(s_fw, desc.version, sizeof(s_fw) - 1);
    }

    /* Coprocessor chip identity. */
    uint32_t chip_id = 0;
    char target[16] = {0};
    if (esp_hosted_get_cp_info(&chip_id, target, sizeof(target)) == ESP_OK) {
        strncpy(s_chip, target, sizeof(s_chip) - 1);
    }

    /* ESP-Hosted RPC protocol version (detects host<->slave skew). */
    esp_hosted_coprocessor_fwver_t ver = {0};
    if (esp_hosted_get_coprocessor_fwversion(&ver) == ESP_OK) {
        snprintf(s_rpc, sizeof(s_rpc), "%u.%u.%u",
                 (unsigned)ver.major1, (unsigned)ver.minor1, (unsigned)ver.patch1);
    }

    if (s_fw[0] || s_chip[0]) {
        s_info_queried = true;
        ESP_LOGI(TAG, "C6 info: fw=%s chip=%s rpc=%s", s_fw, s_chip, s_rpc);
    }
}

static void c6_event_handler(void *arg, esp_event_base_t base,
                             int32_t id, void *data)
{
    switch (id) {
    case ESP_HOSTED_EVENT_CP_INIT: {
        const esp_hosted_event_init_t *e = (const esp_hosted_event_init_t *)data;
        if (e) {
            s_reset_reason = (uint8_t)e->reason;
        }
        s_reboots++;
        ESP_LOGW(TAG, "C6 (re)init #%u, reset_reason=%u",
                 (unsigned)s_reboots, (unsigned)s_reset_reason);
        break;
    }
    case ESP_HOSTED_EVENT_TRANSPORT_UP:
        s_link_up = 1;
        ESP_LOGI(TAG, "C6 SDIO transport up");
        break;
    case ESP_HOSTED_EVENT_TRANSPORT_DOWN:
    case ESP_HOSTED_EVENT_TRANSPORT_FAILURE:
        s_link_up = 0;
        ESP_LOGW(TAG, "C6 SDIO transport down/failure (event %d)", (int)id);
        break;
    case ESP_HOSTED_EVENT_CP_HEARTBEAT:
        s_link_up = 1;  /* heartbeat is proof of life */
        break;
    default:
        break;
    }
}

void c6_diag_init(void)
{
    /* Ensure a default event loop exists (usually already created by netif). */
    esp_err_t r = esp_event_loop_create_default();
    if (r != ESP_OK && r != ESP_ERR_INVALID_STATE) {
        ESP_LOGW(TAG, "esp_event_loop_create_default: %s", esp_err_to_name(r));
    }

    esp_event_handler_register(ESP_HOSTED_EVENT, ESP_EVENT_ANY_ID,
                               c6_event_handler, NULL);

    /* Periodic heartbeat so we can tell a hung C6 from a healthy one. */
    esp_hosted_configure_heartbeat(true, 30);
}

void c6_diag_fill(node_status_t *status)
{
    /* Query the static coprocessor info once the link is up (RPC is ready by
     * the time we're publishing status, so this is the safe place to ask). */
    if (!s_info_queried && s_link_up) {
        c6_query_info();
    }

    strncpy(status->c6_fw, s_fw, sizeof(status->c6_fw) - 1);
    strncpy(status->c6_chip, s_chip, sizeof(status->c6_chip) - 1);
    strncpy(status->c6_rpc, s_rpc, sizeof(status->c6_rpc) - 1);
    status->c6_reset = s_reset_reason;
    status->c6_link = s_link_up;
    status->c6_reboots = s_reboots;
}
