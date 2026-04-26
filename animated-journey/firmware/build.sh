#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${SCRIPT_DIR}/out"
IDF_DIR="${SCRIPT_DIR}/idf-source"
IMAGE_NAME="animated-journey-idf"
ALL_TARGETS=("scanner-s3:esp32s3" "scanner-c6:esp32c6" "scanner-p4:esp32p4")

usage() {
    echo "Usage: $0 [target|clean]"
    echo ""
    echo "  (no args)    Build all targets (s3, c6, p4)"
    echo "  scanner-s3   Build only scanner-s3"
    echo "  scanner-c6   Build only scanner-c6"
    echo "  scanner-p4   Build only scanner-p4"
    echo "  clean        Remove idf-source caches"
    exit 1
}

clean_cache() {
    echo "=== Removing idf-source caches ==="
    rm -rf "${IDF_DIR}/builds" "${IDF_DIR}/components" "${IDF_DIR}/cache"
    # Also purge in-tree build/managed_components dirs that VSCode/idf.py
    # extensions may have created on the host. If they are left behind they
    # get rsynced into the docker build dir and reintroduce stale targets.
    for pair in "${ALL_TARGETS[@]}"; do
        local target="${pair%%:*}"
        rm -rf "${SCRIPT_DIR}/${target}/build" \
               "${SCRIPT_DIR}/${target}/managed_components"
        rm -f  "${SCRIPT_DIR}/${target}/sdkconfig" \
               "${SCRIPT_DIR}/${target}/sdkconfig.old" \
               "${SCRIPT_DIR}/${target}/dependencies.lock"
    done
    echo "Done."
    exit 0
}

prepare_dirs() {
    local target="$1"
    mkdir -p "$OUT_DIR"
    mkdir -p "${IDF_DIR}/builds/${target}"
    mkdir -p "${IDF_DIR}/components/${target}"
    mkdir -p "${IDF_DIR}/cache"
}

build_target() {
    local target="$1"
    local idf_target="$2"

    echo ""
    echo "=== Building ${target} (${idf_target}) ==="

    prepare_dirs "$target"

    # Self-heal stale CMakeCache.txt when an external tool (e.g. the VSCode
    # ESP-IDF extension) reconfigured this build dir against a different
    # target. Without this, idf.py refuses to build with:
    #   "Target settings are not consistent: '<x>' in env, '<y>' in CMakeCache"
    local cache_file="${IDF_DIR}/builds/${target}/CMakeCache.txt"
    if [[ -f "$cache_file" ]]; then
        local cached_target
        cached_target="$(grep -E '^IDF_TARGET:' "$cache_file" | head -n1 | cut -d= -f2 || true)"
        if [[ -n "$cached_target" && "$cached_target" != "$idf_target" ]]; then
            echo "  -> stale CMakeCache target '${cached_target}' != '${idf_target}', purging build dir"
            rm -rf "${IDF_DIR}/builds/${target:?}"/*
        fi
    fi

    # Bind-mount layout:
    #   /src                (ro)  <- firmware source tree
    #   /project/<t>/build        <- idf-source/builds/<t>   (CMake cache + objects)
    #   /project/<t>/main/managed_components
    #                             <- idf-source/components/<t> (esp_hosted, etc.)
    #   /root/.cache              <- idf-source/cache (global component registry)
    docker run --rm \
      --network=host \
      -e "IDF_TARGET=${idf_target}" \
      -v "${SCRIPT_DIR}:/src:ro" \
      -v "${IDF_DIR}/builds/${target}:/project/${target}/build" \
      -v "${IDF_DIR}/components/${target}:/project/${target}/main/managed_components" \
      -v "${IDF_DIR}/cache:/root/.cache" \
      "$IMAGE_NAME" \
      ". /opt/esp/idf/export.sh && \
       mkdir -p /project/common /project/${target} && \
       cp -a /src/common/. /project/common/ && \
       for entry in /src/${target}/*; do \
           name=\$(basename \"\$entry\"); \
           case \"\$name\" in \
               build|managed_components|sdkconfig|sdkconfig.old|dependencies.lock) continue ;; \
           esac; \
           cp -a \"\$entry\" /project/${target}/; \
       done && \
       cd /project/${target} && \
       idf.py build"

    local build="${IDF_DIR}/builds/${target}"
    local dest="${OUT_DIR}/${target}"
    mkdir -p "$dest"

    if [[ -f "${build}/${target}.bin" ]]; then
        cp "${build}/${target}.bin"                          "${dest}/${target}.bin"
        cp "${build}/bootloader/bootloader.bin"              "${dest}/bootloader.bin"    2>/dev/null || true
        cp "${build}/partition_table/partition-table.bin"     "${dest}/partition-table.bin" 2>/dev/null || true
        cp "${build}/ota_data_initial.bin"                   "${dest}/ota_data_initial.bin" 2>/dev/null || true
        cp "${build}/flasher_args.json"                      "${dest}/flasher_args.json" 2>/dev/null || true
        echo "  -> ${target} artifacts copied to out/${target}/"
    else
        echo "  Warning: ${target}.bin not found (build may have failed)"
    fi
}

# Handle arguments
if [[ "${1:-}" == "clean" ]]; then
    clean_cache
fi
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    usage
fi

mkdir -p "$OUT_DIR"

echo "=== Preparing builder image ==="
docker build -f "${SCRIPT_DIR}/Dockerfile.build" -t "$IMAGE_NAME" "${SCRIPT_DIR}"

# Build requested target(s)
if [[ -n "${1:-}" ]]; then
    case "$1" in
        scanner-s3) build_target scanner-s3 esp32s3 ;;
        scanner-c6) build_target scanner-c6 esp32c6 ;;
        scanner-p4) build_target scanner-p4 esp32p4 ;;
        *) echo "Unknown target: $1"; usage ;;
    esac
else
    for pair in "${ALL_TARGETS[@]}"; do
        build_target "${pair%%:*}" "${pair##*:}"
    done
fi

echo ""
echo "=== Build complete ==="
ls -la "$OUT_DIR"
