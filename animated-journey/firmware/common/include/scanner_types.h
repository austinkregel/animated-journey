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

typedef enum {
    BLE_EVENT_SEEN = 0,
    BLE_EVENT_GONE = 1,
} ble_event_t;

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
    uint32_t seen_count;
    double   first_seen_s;
    uint8_t  event;
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
    uint32_t ble_active;
    /* Extended diagnostics */
    float    chip_temp_c;       /* Internal die temperature (C) */
    uint32_t min_free_heap;     /* Minimum free heap since boot */
    uint32_t psram_free;        /* Free PSRAM (0 if none) */
    uint32_t psram_total;       /* Total PSRAM (0 if none) */
    uint8_t  reset_reason;      /* esp_reset_reason_t value */
    uint8_t  cpu_count;         /* Number of CPU cores */
    uint16_t cpu_freq_mhz;     /* CPU clock frequency */
    char     idf_version[16];   /* ESP-IDF version string */
    char     chip_model[16];    /* Chip model name */
    /* ESP-Hosted coprocessor (C6) diagnostics -- P4 only; empty/zero elsewhere */
    char     c6_fw[32];         /* Coprocessor network_adapter app version */
    char     c6_chip[16];       /* Coprocessor target, e.g. "esp32c6" */
    char     c6_rpc[16];        /* ESP-Hosted RPC version "maj.min.patch" */
    uint8_t  c6_reset;          /* Last C6 reset reason (esp_reset_reason_t) */
    uint8_t  c6_link;           /* 1 = SDIO transport to C6 is up */
    uint32_t c6_reboots;        /* C6 (re)init events seen since P4 boot */
} node_status_t;
