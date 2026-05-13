#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 4 ]]; then
  echo "Usage: $0 <source-config> <scenario-name> <java-dbname> <python-dbname>" >&2
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SOURCE_CONFIG="$1"
SCENARIO_NAME="$2"
JAVA_DBNAME="$3"
PYTHON_DBNAME="$4"

CONFIG_DIR="${ROOT_DIR}/compare_runs/${SCENARIO_NAME}/generated_configs"
RESULT_DIR="${ROOT_DIR}/compare_runs/${SCENARIO_NAME}"
JAVA_CONFIG="${CONFIG_DIR}/${SCENARIO_NAME}_java.cfg"
PYTHON_CONFIG="${CONFIG_DIR}/${SCENARIO_NAME}_python.cfg"
STATUS_FILE="${RESULT_DIR}/status.txt"
CURRENT_STEP="initialization"

mkdir -p "${CONFIG_DIR}" "${RESULT_DIR}"

on_exit() {
  local exit_code=$?
  if [[ ${exit_code} -eq 0 ]]; then
    printf 'SUCCESS\nlast_step=%s\n' "${CURRENT_STEP}" > "${STATUS_FILE}"
  else
    printf 'FAILED\nlast_step=%s\nexit_code=%s\n' "${CURRENT_STEP}" "${exit_code}" > "${STATUS_FILE}"
  fi
}

trap on_exit EXIT

JAVA_SOURCE_DB="${JAVA_DBNAME}"
PYTHON_SOURCE_DB="${PYTHON_DBNAME}"
JAVA_SETUP_DB="${JAVA_DBNAME}_setup"
PYTHON_SETUP_DB="${PYTHON_DBNAME}_setup"
JAVA_BN_DB="${JAVA_DBNAME}_BN"
PYTHON_BN_DB="${PYTHON_DBNAME}_BN"
JAVA_CT_DB="${JAVA_DBNAME}_CT"
PYTHON_CT_DB="${PYTHON_DBNAME}_CT"
JAVA_CT_CACHE_DB="${JAVA_DBNAME}_CT_cache"
PYTHON_CT_CACHE_DB="${PYTHON_DBNAME}_CT_cache"
JAVA_GLOBAL_COUNTS_DB="${JAVA_DBNAME}_global_counts"
PYTHON_GLOBAL_COUNTS_DB="${PYTHON_DBNAME}_global_counts"

sed "s/^dbname = .*/dbname = ${JAVA_DBNAME}/" "${ROOT_DIR}/${SOURCE_CONFIG}" > "${JAVA_CONFIG}"
sed "s/^dbname = .*/dbname = ${PYTHON_DBNAME}/" "${ROOT_DIR}/${SOURCE_CONFIG}" > "${PYTHON_CONFIG}"

echo "== Resetting generated artifacts for ${SCENARIO_NAME} =="
rm -rf "${ROOT_DIR}/${JAVA_DBNAME}" "${ROOT_DIR}/${PYTHON_DBNAME}"
rm -f "${ROOT_DIR}/Bif_${JAVA_DBNAME}.xml" "${ROOT_DIR}/Bif_${PYTHON_DBNAME}.xml"

echo "== Resetting MySQL databases for ${SCENARIO_NAME} =="
mysql -h 127.0.0.1 -u fbuser -e "
DROP DATABASE IF EXISTS \`${JAVA_SOURCE_DB}\`;
DROP DATABASE IF EXISTS \`${PYTHON_SOURCE_DB}\`;
DROP DATABASE IF EXISTS \`${JAVA_SETUP_DB}\`;
DROP DATABASE IF EXISTS \`${PYTHON_SETUP_DB}\`;
DROP DATABASE IF EXISTS \`${JAVA_BN_DB}\`;
DROP DATABASE IF EXISTS \`${PYTHON_BN_DB}\`;
DROP DATABASE IF EXISTS \`${JAVA_CT_DB}\`;
DROP DATABASE IF EXISTS \`${PYTHON_CT_DB}\`;
DROP DATABASE IF EXISTS \`${JAVA_CT_CACHE_DB}\`;
DROP DATABASE IF EXISTS \`${PYTHON_CT_CACHE_DB}\`;
DROP DATABASE IF EXISTS \`${JAVA_GLOBAL_COUNTS_DB}\`;
DROP DATABASE IF EXISTS \`${PYTHON_GLOBAL_COUNTS_DB}\`;
CREATE DATABASE \`${JAVA_SOURCE_DB}\` CHARACTER SET latin1 COLLATE latin1_swedish_ci;
CREATE DATABASE \`${PYTHON_SOURCE_DB}\` CHARACTER SET latin1 COLLATE latin1_swedish_ci;
"

echo "== Cloning unielwin into ${JAVA_DBNAME} and ${PYTHON_DBNAME} =="
mysqldump -h 127.0.0.1 -u fbuser unielwin | mysql -h 127.0.0.1 -u fbuser "${JAVA_SOURCE_DB}"
mysqldump -h 127.0.0.1 -u fbuser unielwin | mysql -h 127.0.0.1 -u fbuser "${PYTHON_SOURCE_DB}"

echo "== Running Java FactorBase for ${SCENARIO_NAME} =="
CURRENT_STEP="java_factorbase"
java -Dconfig="${JAVA_CONFIG}" -jar "${ROOT_DIR}/code/factorbase/target/factorbase-1.0-SNAPSHOT.jar" \
  2>&1 | tee "${RESULT_DIR}/java.log"

echo "== Running Python FactorBase for ${SCENARIO_NAME} =="
CURRENT_STEP="python_factorbase"
python "${ROOT_DIR}/python_factorbase/pyfactorbase.py" \
  --config "${PYTHON_CONFIG}" \
  --jarlearner "${ROOT_DIR}/code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar" \
  2>&1 | tee "${RESULT_DIR}/python.log"

echo "== Comparing Java and Python outputs for ${SCENARIO_NAME} =="
CURRENT_STEP="compare_outputs"
python "${ROOT_DIR}/python_factorbase/scripts/compare_original_vs_python.py" \
  --baseline-dbname "${JAVA_DBNAME}" \
  --python-dbname "${PYTHON_DBNAME}" \
  --outdir "compare_runs/${SCENARIO_NAME}" \
  2>&1 | tee "${RESULT_DIR}/compare.log"

CURRENT_STEP="completed"
echo "Stored results in ${RESULT_DIR}"
