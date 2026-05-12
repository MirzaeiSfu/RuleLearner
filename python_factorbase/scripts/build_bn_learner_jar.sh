#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

OUTPUT_JAR="${REPO_ROOT}/code/bnrunner/target/bnrunner-1.0-SNAPSHOT.jar"

cd "${REPO_ROOT}/code"
mvn -pl bnrunner -am -DskipTests package

echo "BN learner jar ready at: ${OUTPUT_JAR}"
