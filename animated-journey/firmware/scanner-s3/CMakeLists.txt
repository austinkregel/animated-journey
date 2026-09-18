cmake_minimum_required(VERSION 3.16)

set(EXTRA_COMPONENT_DIRS "../common")

include($ENV{IDF_PATH}/tools/cmake/project.cmake)

# Prefer the git version passed in by build.sh; otherwise ESP-IDF falls back to
# its own `git describe` (or "1" when .git isn't present, e.g. the Docker build).
if(DEFINED ENV{AJ_FW_VERSION} AND NOT "$ENV{AJ_FW_VERSION}" STREQUAL "")
    set(PROJECT_VER "$ENV{AJ_FW_VERSION}")
endif()

project(scanner-s3)

# Workaround: IDF v5.4.x NimBLE ble_gap.c has missing forward declarations
idf_component_get_property(bt_lib bt COMPONENT_LIB)
target_compile_options(${bt_lib} PRIVATE
    -Wno-error=implicit-function-declaration
    -Wno-error=return-type
    -Wno-error=unused-function)
