#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${1:-${API_BASE_URL:-http://localhost:8000}}"
BASE_URL="${BASE_URL%/}"

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd jq

pass() {
  echo "[PASS] $1"
}

fail() {
  echo "[FAIL] $1" >&2
  exit 1
}

json_post() {
  local path="$1"
  local payload="$2"
  curl -fsS -X POST "${BASE_URL}${path}" \
    -H "Content-Type: application/json" \
    -d "${payload}"
}

echo "Running hackathon smoke test against ${BASE_URL}"

health_json="$(curl -fsS "${BASE_URL}/healthz")" || fail "healthz is unreachable"
graph_nodes="$(jq -r '.graph_nodes // 0' <<<"${health_json}")"
[[ "${graph_nodes}" =~ ^[0-9]+$ ]] || fail "healthz did not return graph_nodes"
(( graph_nodes > 0 )) || fail "graph_nodes must be > 0"
pass "healthz returned graph_nodes=${graph_nodes}"

drug_json="$(json_post "/resolve-drug" '{"name":"Dolo 650","source_lang":"en-IN"}')" || fail "resolve-drug request failed"
drug_name="$(jq -r '.generic_name // empty' <<<"${drug_json}")"
[[ "${drug_name}" == "Acetaminophen" ]] || fail "resolve-drug expected Acetaminophen, got '${drug_name}'"
pass "resolve-drug maps Dolo 650 -> ${drug_name}"

herb_json="$(json_post "/resolve-herb" '{"name":"ashwagandha","source_lang":"en-IN"}')" || fail "resolve-herb request failed"
herb_name="$(jq -r '.name // empty' <<<"${herb_json}")"
[[ "${herb_name}" == "Ashwagandha" ]] || fail "resolve-herb expected Ashwagandha, got '${herb_name}'"
pass "resolve-herb maps ashwagandha -> ${herb_name}"

restored_ddi_json="$(json_post "/safety-check" '{"drugs":["warfarin","aspirin"],"herbs":[],"age":72}')" || fail "safety-check failed for warfarin + aspirin"
restored_ddi_findings="$(jq -r '.findings | length' <<<"${restored_ddi_json}")"
restored_ddi_curated="$(jq -r '[.findings[].citations[]? | select(.source_key == "sentinel_curated")] | length' <<<"${restored_ddi_json}")"
(( restored_ddi_findings >= 1 )) || fail "expected restored finding for warfarin + aspirin"
(( restored_ddi_curated >= 1 )) || fail "expected sentinel-curated repair citation for warfarin + aspirin"
pass "warfarin + aspirin returns restored sentinel coverage"

ddi_json="$(json_post "/safety-check" '{"drugs":["warfarin","ibuprofen"],"herbs":[],"age":72}')" || fail "safety-check failed for warfarin + ibuprofen"
ddi_findings="$(jq -r '.findings | length' <<<"${ddi_json}")"
ddi_dataset_record="$(jq -r '[.findings[].citations[]? | select(.source_key == "ddinter" and .evidence_scope == "dataset_record")] | length' <<<"${ddi_json}")"
(( ddi_findings >= 1 )) || fail "expected >=1 finding for warfarin + ibuprofen"
(( ddi_dataset_record >= 1 )) || fail "expected DDInter dataset-record citation for warfarin + ibuprofen"
pass "warfarin + ibuprofen returns ${ddi_findings} finding(s) with DDInter citation"

multihop_json="$(json_post "/safety-check" '{"drugs":["simvastatin","clarithromycin"],"herbs":[],"age":72}')" || fail "safety-check failed for simvastatin + clarithromycin"
multihop_findings="$(jq -r '.findings | length' <<<"${multihop_json}")"
multihop_mechanistic="$(jq -r '[.findings[] | select(.source_layer == "L2_multihop" and .enzyme == "CYP3A4")] | length' <<<"${multihop_json}")"
multihop_backing="$(jq -r '[.findings[].citations[]? | select(.backing_source_key == "fda_ddi_table")] | length' <<<"${multihop_json}")"
(( multihop_findings >= 2 )) || fail "expected >=2 findings for simvastatin + clarithromycin"
(( multihop_mechanistic >= 1 )) || fail "expected CYP3A4 multihop finding for simvastatin + clarithromycin"
(( multihop_backing >= 1 )) || fail "expected FDA-backed citation for simvastatin + clarithromycin"
pass "simvastatin + clarithromycin returns direct and multihop CYP3A4 evidence"

herb_ddi_json="$(json_post "/safety-check" '{"drugs":["aspirin"],"herbs":["ginger"],"age":72}')" || fail "safety-check failed for ginger + aspirin"
herb_direct="$(jq -r '.herb_drug_interactions | length' <<<"${herb_ddi_json}")"
herb_reference="$(jq -r '[.herb_drug_interactions[].citations[]? | select(.source_key == "ddid" and .evidence_scope == "exact_reference" and (.reference_url // "") != "")] | length' <<<"${herb_ddi_json}")"
(( herb_direct >= 1 )) || fail "expected direct herb-drug interaction for ginger + aspirin"
(( herb_reference >= 1 )) || fail "expected exact DDID reference for ginger + aspirin"
pass "ginger + aspirin returns direct DDID evidence with exact reference"

echo "Smoke test completed successfully."
