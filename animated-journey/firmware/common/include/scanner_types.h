#pragma once

#include <stdint.h>
#include <stdbool.h>

typedef enum {
    SCAN_WIFI_PROBE = 0,
    SCAN_WIFI_BEACON,
    SCAN_BLE_ADV,
    SCAN_802154,
    SCAN_LORA,
} scan_type_t;

typedef struct {
    uint8_t  mac[6];
    int8_t   rssi;
    uint8_t  channel;
    scan_type_t type;
    int64_t  timestamp_ms;
} scan_result_t;

typedef struct {
    scan_result_t base;
    char     ssid[33];
    uint8_t  ssid_len;
    uint8_t  frame_subtype;
} wifi_probe_t;

typedef struct {
    scan_result_t base;
    char     ssid[33];
    uint8_t  ssid_len;
    uint8_t  encryption;
    uint8_t  bssid[6];
} wifi_beacon_t;

typedef struct {
    scan_result_t base;
    uint8_t  addr_type;
    uint8_t  adv_type;
    char     name[32];
    uint8_t  name_len;
    uint8_t  manufacturer_data[64];
    uint8_t  manufacturer_data_len;
    int8_t   tx_power;
    uint16_t service_uuids[8];
    uint8_t  service_uuid_count;
} ble_adv_t;

typedef struct {
    char     node_id[32];
    uint32_t uptime_s;
    uint32_t free_heap;
    int8_t   wifi_rssi;
    char     fw_version[16];
    uint32_t probe_count;
    uint32_t ble_count;
    uint32_t beacon_count;
} node_status_t;
