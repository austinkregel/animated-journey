#include <string.h>
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "config.h"
#include "nvs_config.h"

static const char *TAG = "nvs_config";
static const char *NVS_NAMESPACE = "burtooth";

esp_err_t nvs_config_init(void)
{
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_LOGW(TAG, "NVS partition truncated, erasing...");
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    return ret;
}

esp_err_t nvs_config_load(nvs_config_t *config)
{
    nvs_handle_t handle;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle);
    if (ret != ESP_OK) {
        ESP_LOGW(TAG, "NVS namespace not found, using defaults");
        nvs_config_set_defaults(config);
        return ESP_ERR_NVS_NOT_FOUND;
    }

    size_t len;

    len = sizeof(config->node_id);
    if (nvs_get_str(handle, "node_id", config->node_id, &len) != ESP_OK) {
        strncpy(config->node_id, "unconfigured", sizeof(config->node_id));
    }

    len = sizeof(config->wifi_ssid);
    if (nvs_get_str(handle, "wifi_ssid", config->wifi_ssid, &len) != ESP_OK) {
        config->wifi_ssid[0] = '\0';
    }

    len = sizeof(config->wifi_password);
    if (nvs_get_str(handle, "wifi_pass", config->wifi_password, &len) != ESP_OK) {
        config->wifi_password[0] = '\0';
    }

    len = sizeof(config->mqtt_host);
    if (nvs_get_str(handle, "mqtt_host", config->mqtt_host, &len) != ESP_OK) {
        config->mqtt_host[0] = '\0';
    }

    if (nvs_get_u16(handle, "mqtt_port", &config->mqtt_port) != ESP_OK) {
        config->mqtt_port = DEFAULT_MQTT_PORT;
    }

    len = sizeof(config->mqtt_username);
    if (nvs_get_str(handle, "mqtt_user", config->mqtt_username, &len) != ESP_OK) {
        config->mqtt_username[0] = '\0';
    }

    len = sizeof(config->mqtt_password);
    if (nvs_get_str(handle, "mqtt_pass", config->mqtt_password, &len) != ESP_OK) {
        config->mqtt_password[0] = '\0';
    }

    nvs_close(handle);
    ESP_LOGI(TAG, "Config loaded: node_id=%s mqtt=%s:%u",
             config->node_id, config->mqtt_host, config->mqtt_port);
    return ESP_OK;
}

esp_err_t nvs_config_save(const nvs_config_t *config)
{
    nvs_handle_t handle;
    esp_err_t ret = nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to open NVS for writing: %s", esp_err_to_name(ret));
        return ret;
    }

    nvs_set_str(handle, "node_id", config->node_id);
    nvs_set_str(handle, "wifi_ssid", config->wifi_ssid);
    nvs_set_str(handle, "wifi_pass", config->wifi_password);
    nvs_set_str(handle, "mqtt_host", config->mqtt_host);
    nvs_set_u16(handle, "mqtt_port", config->mqtt_port);
    nvs_set_str(handle, "mqtt_user", config->mqtt_username);
    nvs_set_str(handle, "mqtt_pass", config->mqtt_password);

    ret = nvs_commit(handle);
    nvs_close(handle);

    if (ret == ESP_OK) {
        ESP_LOGI(TAG, "Config saved successfully");
    } else {
        ESP_LOGE(TAG, "Failed to commit NVS: %s", esp_err_to_name(ret));
    }
    return ret;
}

void nvs_config_set_defaults(nvs_config_t *config)
{
    memset(config, 0, sizeof(nvs_config_t));
    strncpy(config->node_id, "unconfigured", sizeof(config->node_id) - 1);
    config->mqtt_port = DEFAULT_MQTT_PORT;
}
