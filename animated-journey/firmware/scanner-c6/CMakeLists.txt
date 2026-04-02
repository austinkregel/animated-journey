cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "../common")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(scanner-c6)

# Workaround: IDF v5.4.x NimBLE ble_gap.c has missing forward declarations
idf_component_get_property(bt_lib bt COMPONENT_LIB)
target_compile_options(${bt_lib} PRIVATE
    -Wno-error=implicit-function-declaration
    -Wno-error=return-type
    -Wno-error=unused-function)
