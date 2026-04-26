#!/usr/bin/env bash
# Build ESP-Hosted C6 coprocessor firmware from the same managed_components
# revision as this host project (scanner-p4). Output:
#   build/slave-c6/network_adapter.bin
set -euo pipefail

if [[ "${SCANNER_P4_SKIP_C6_SLAVE_BUILD:-}" == "1" ]]; then
  echo "scanner-p4: skipping C6 slave build (SCANNER_P4_SKIP_C6_SLAVE_BUILD=1)"
  exit 0
fi

root="$(cd "$(dirname "$0")/.." && pwd)"
slave="${root}/managed_components/espressif__esp_hosted/slave"
build="${root}/build/slave-c6"
overlay="${root}/c6_slave"

if [[ ! -d "$slave" ]]; then
  echo "scanner-p4: esp_hosted slave not found at:" >&2
  echo "  $slave" >&2
  echo "Run idf.py build or reconfigure once so managed_components is populated." >&2
  exit 1
fi

if [[ -z "${IDF_PATH:-}" ]]; then
  echo "scanner-p4: IDF_PATH is not set. Source \$IDF_PATH/export.sh, then rebuild." >&2
  exit 1
fi

export SDKCONFIG_DEFAULTS="${slave}/sdkconfig.defaults;${overlay}/sdkconfig.defaults"

# Host build exports IDF_TARGET=esp32p4; nested idf.py for the slave must not
# inherit it or set-target esp32c6 fails with "not consistent with target in the environment".
run_slave_idf() {
  (
    unset IDF_TARGET
    export IDF_PATH
    export SDKCONFIG_DEFAULTS
    idf.py -C "$slave" -B "$build" "$@"
  )
}

if [[ ! -f "${build}/sdkconfig" ]]; then
  run_slave_idf set-target esp32c6
fi

run_slave_idf build

echo "scanner-p4: C6 slave firmware ready: ${build}/network_adapter.bin"
