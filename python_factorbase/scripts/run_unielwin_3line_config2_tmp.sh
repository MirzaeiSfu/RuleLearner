#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

"${ROOT_DIR}/python_factorbase/scripts/run_unielwin_compare_common.sh" \
  "config2.tmp" \
  "unielwin_config2_tmp" \
  "unielwin_config2_tmp_java" \
  "unielwin_config2_tmp_python"
