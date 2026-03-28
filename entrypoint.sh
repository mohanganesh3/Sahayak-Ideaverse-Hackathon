#!/bin/bash
set -e

cd /app

echo "=== SAHAYAK container starting ==="

# Run bootstrap unless explicitly skipped for serverless/load-balanced workers.
if [ "${SKIP_BOOTSTRAP:-0}" != "1" ]; then
  python -m scripts.bootstrap
else
  echo "=== SKIP_BOOTSTRAP=1, skipping blocking bootstrap ==="
fi

# Start the FastAPI server
exec uvicorn app.api.server:app \
    --host 0.0.0.0 \
    --port "${PORT:-${APP_PORT:-8000}}" \
    --log-level "${LOG_LEVEL:-info}"
