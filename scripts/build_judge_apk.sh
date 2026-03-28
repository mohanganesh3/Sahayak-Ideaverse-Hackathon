#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
MOBILE_DIR="${ROOT_DIR}/mobile"
API_BASE_URL="${1:-${PUBLIC_API_BASE_URL:-}}"

if [[ -z "${API_BASE_URL}" ]]; then
  echo "Usage: PUBLIC_API_BASE_URL=https://your-public-url ${BASH_SOURCE[0]}" >&2
  echo "   or: ${BASH_SOURCE[0]} https://your-public-url" >&2
  exit 1
fi

API_BASE_URL="${API_BASE_URL%/}"

if ! command -v curl >/dev/null 2>&1; then
  echo "Missing required command: curl" >&2
  exit 1
fi

if ! command -v npx >/dev/null 2>&1; then
  echo "Missing required command: npx" >&2
  exit 1
fi

echo "Verifying public backend: ${API_BASE_URL}"
curl -fsS "${API_BASE_URL}/healthz" >/dev/null
bash "${SCRIPT_DIR}/hackathon_smoke_test.sh" "${API_BASE_URL}" >/dev/null

echo "Submitting Android preview APK build with EXPO_PUBLIC_API_BASE_URL=${API_BASE_URL}"
cd "${MOBILE_DIR}"
EXPO_PUBLIC_API_BASE_URL="${API_BASE_URL}" npx eas build -p android --profile preview --non-interactive
