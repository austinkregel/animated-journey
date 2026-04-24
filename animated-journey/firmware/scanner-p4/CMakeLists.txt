cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "../common")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(scanner-p4)

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
