#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

: "${NEO4J_URI:=bolt://localhost:7687}"
: "${NEO4J_USER:=neo4j}"
: "${NEO4J_PASSWORD:=changeme}"
: "${NEO4J_DATABASE:=neo4j}"

if ! command -v python >/dev/null 2>&1; then
  echo "Missing required command: python" >&2
  exit 1
fi

echo "Repairing sentinel interaction coverage on ${NEO4J_URI}"
cd "${ROOT_DIR}"
NEO4J_URI="${NEO4J_URI}" \
NEO4J_USER="${NEO4J_USER}" \
NEO4J_PASSWORD="${NEO4J_PASSWORD}" \
NEO4J_DATABASE="${NEO4J_DATABASE}" \
python app/graph/validate_sentinel_interactions.py \
  --repair \
  --report-path "${ROOT_DIR}/sentinel_validation_report_latest.json"
