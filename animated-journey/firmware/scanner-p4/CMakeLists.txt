cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "../common")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(scanner-p4)

# Always build ESP-Hosted C6 slave from managed_components/espressif__esp_hosted/slave
# so host and coprocessor come from the same component version (avoids RPC skew).
# Binary: ${CMAKE_BINARY_DIR}/slave-c6/network_adapter.bin
# To skip: SCANNER_P4_SKIP_C6_SLAVE_BUILD=1 idf.py build
add_custom_target(esp_hosted_c6_slave ALL
    COMMAND /usr/bin/env bash "${CMAKE_SOURCE_DIR}/scripts/build_esp_hosted_c6_slave.sh"
    WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
    VERBATIM)

# Workaround: IDF v5.4.x NimBLE ble_gap.c has missing forward declarations
idf_component_get_property(bt_lib bt COMPONENT_LIB)
target_compile_options(${bt_lib} PRIVATE
    -Wno-error=implicit-function-declaration
    -Wno-error=return-type
    -Wno-error=unused-function)

# Workaround: espressif/esp_codec_dev v1.4.x logs uint32_t fields with %d in
# audio_codec_data_i2s.c, which trips -Werror=format on this toolchain.
idf_component_get_property(codec_dev_lib espressif__esp_codec_dev COMPONENT_LIB)
target_compile_options(${codec_dev_lib} PRIVATE
    -Wno-error=format)
