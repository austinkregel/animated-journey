#include <string.h>
#include <stdio.h>
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_rom_md5.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "config.h"
#include "nvs_config.h"

static const char *TAG = "nvs_config";
static const char *NVS_NAMESPACE = "aj-node-cfg";

static void generate_node_id_from_mac(char *out, size_t out_len)
{
    uint8_t mac[6];
    esp_read_mac(mac, ESP_MAC_BASE);

    md5_context_t ctx;
    esp_rom_md5_init(&ctx);
    esp_rom_md5_update(&ctx, mac, sizeof(mac));
    uint8_t digest[16];
    esp_rom_md5_final(digest, &ctx);

    char hex[9];
    snprintf(hex, sizeof(hex), "%02x%02x%02x%02x",
             digest[0], digest[1], digest[2], digest[3]);
    hex[7] = '\0';

    snprintf(out, out_len, "node-%s", hex);
}

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
        ESP_LOGW(TAG, "NVS namespace not found, generating defaults from MAC");
        nvs_config_set_defaults(config);
        nvs_config_save(config);
        return ESP_OK;
    }

    size_t len;
    bool need_save = false;

    len = sizeof(config->node_id);
    if (nvs_get_str(handle, "node_id", config->node_id, &len) != ESP_OK
        || strcmp(config->node_id, "unconfigured") == 0
        || config->node_id[0] == '\0') {
        generate_node_id_from_mac(config->node_id, sizeof(config->node_id));
        ESP_LOGI(TAG, "Auto-generated node_id from MAC: %s", config->node_id);
        need_save = true;
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

    if (need_save) {
        ESP_LOGI(TAG, "Persisting auto-generated node_id to NVS");
        nvs_config_save(config);
    }

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
    generate_node_id_from_mac(config->node_id, sizeof(config->node_id));
    config->mqtt_port = DEFAULT_MQTT_PORT;
    ESP_LOGI(TAG, "Defaults set with auto-generated node_id: %s", config->node_id);
}
