#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

"${ROOT_DIR}/python_factorbase/scripts/run_unielwin_compare_common.sh" \
  "config.tmp" \
  "unielwin_config_tmp" \
  "unielwin_config_tmp_java" \
  "unielwin_config_tmp_python"
