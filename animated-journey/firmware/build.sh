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
       cp -a /src/${target}/. /project/${target}/ && \
       cd /project/${target} && \
       idf.py build"

    local build="${IDF_DIR}/builds/${target}"
    local dest="${OUT_DIR}/${target}"
    mkdir -p "$dest"

    if [[ -f "${build}/${target}.bin" ]]; then
        cp "${build}/${target}.bin"                          "${dest}/${target}.bin"
        cp "${build}/bootloader/bootloader.bin"              "${dest}/bootloader.bin"    2>/dev/null || true
        cp "${build}/partition_table/partition-table.bin"     "${dest}/partition-table.bin" 2>/dev/null || true
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
