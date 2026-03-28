#!/bin/bash
set -euo pipefail

NEO4J_DB_NAME="${NEO4J_DB_NAME:-neo4j}"
BOOTSTRAP_DUMP="/opt/bootstrap/${NEO4J_DB_NAME}.dump"

if [ -f "$BOOTSTRAP_DUMP" ] && [ ! -d "/data/databases/${NEO4J_DB_NAME}" ]; then
  echo "=== Restoring ${NEO4J_DB_NAME} from bundled dump ==="
  mkdir -p /data/databases /data/transactions
  neo4j-admin database load "${NEO4J_DB_NAME}" \
    --from-path=/opt/bootstrap \
    --overwrite-destination=true
  chown -R neo4j:neo4j /data
fi

exec /startup/docker-entrypoint.sh "$@"
