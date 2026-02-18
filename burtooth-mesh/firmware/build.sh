#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="${SCRIPT_DIR}/out"
mkdir -p "$OUT_DIR"

echo "=== Building scanner-s3 ==="
docker build -f "${SCRIPT_DIR}/Dockerfile.build" \
  --build-arg TARGET=scanner-s3 \
  --build-arg IDF_TARGET=esp32s3 \
  -t burtooth-s3-build "${SCRIPT_DIR}"

CONTAINER_ID=$(docker create burtooth-s3-build)
docker cp "${CONTAINER_ID}:/project/scanner-s3/build/scanner-s3.bin" "${OUT_DIR}/scanner-s3.bin" 2>/dev/null || \
  echo "Warning: scanner-s3.bin not found (build may have failed)"
docker rm "$CONTAINER_ID" > /dev/null

echo "=== Building scanner-c6 ==="
docker build -f "${SCRIPT_DIR}/Dockerfile.build" \
  --build-arg TARGET=scanner-c6 \
  --build-arg IDF_TARGET=esp32c6 \
  -t burtooth-c6-build "${SCRIPT_DIR}"

CONTAINER_ID=$(docker create burtooth-c6-build)
docker cp "${CONTAINER_ID}:/project/scanner-c6/build/scanner-c6.bin" "${OUT_DIR}/scanner-c6.bin" 2>/dev/null || \
  echo "Warning: scanner-c6.bin not found (build may have failed)"
docker rm "$CONTAINER_ID" > /dev/null

echo "=== Build complete ==="
ls -la "$OUT_DIR"
