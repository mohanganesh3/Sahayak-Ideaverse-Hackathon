#!/usr/bin/env python3
"""SAHAYAK bootstrap – run at container startup before uvicorn.

1. Wait for Neo4j to become reachable (up to 60 s).
2. Verify connectivity.
3. Apply schema (constraints + indexes) idempotently.
4. Print graph status and guidance on data ingestion.

Usage (inside container):
    python -m scripts.bootstrap          # normal boot
    python -m scripts.bootstrap --schema # apply schema only, then exit
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from dotenv import load_dotenv

load_dotenv(Path(_project_root) / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger("sahayak.bootstrap")

MAX_WAIT_S = 60
POLL_INTERVAL_S = 3


def wait_for_neo4j() -> bool:
    """Block until Neo4j is reachable or timeout expires."""
    from app.graph.neo4j_connection import verify_connectivity

    deadline = time.monotonic() + MAX_WAIT_S
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        if verify_connectivity():
            logger.info("Neo4j reachable after %d attempt(s)", attempt)
            return True
        logger.info("Waiting for Neo4j… (attempt %d)", attempt)
        time.sleep(POLL_INTERVAL_S)

    logger.error("Neo4j not reachable after %ds", MAX_WAIT_S)
    return False


def apply_schema() -> None:
    """Idempotently create constraints and indexes."""
    from app.graph.neo4j_connection import get_driver
    from app.graph.schema import create_schema

    driver = get_driver()
    logger.info("Applying schema (constraints + indexes)…")
    create_schema(driver)
    logger.info("Schema applied successfully")


def print_graph_status() -> None:
    """Print node counts to help the user know if data needs ingestion."""
    from app.graph.neo4j_connection import get_driver

    driver = get_driver()
    try:
        with driver.session() as session:
            result = session.run(
                "MATCH (n) RETURN labels(n)[0] AS label, count(*) AS cnt "
                "ORDER BY cnt DESC LIMIT 15"
            )
            records = list(result)

        if not records:
            logger.warning(
                "Graph is EMPTY. Run the following to ingest data:\n"
                "  docker compose exec sahayak-app python run_all_ingestions.py\n"
                "Make sure sahayak-data is mounted and NEO4J_PASSWORD is set in .env."
            )
        else:
            total = sum(r["cnt"] for r in records)
            logger.info("Graph contains %d nodes:", total)
            for r in records:
                logger.info("  %-25s %d", r["label"], r["cnt"])
    except Exception as exc:
        logger.warning("Could not query graph status: %s", exc)


def apply_runtime_repairs() -> None:
    """Log runtime repair intent without blocking container startup."""
    from app.graph.runtime_repairs import _unknown_severity_counts

    logger.info("Checking runtime graph repairs…")
    counts = _unknown_severity_counts(os.getenv("NEO4J_DATABASE", "neo4j"))
    logger.info(
        "Runtime graph repairs deferred to application startup thread: %s",
        counts,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="SAHAYAK bootstrap")
    parser.add_argument("--schema", action="store_true", help="Apply schema then exit")
    args = parser.parse_args()

    if not wait_for_neo4j():
        logger.error("Aborting — Neo4j is not available")
        sys.exit(1)

    apply_schema()
    apply_runtime_repairs()
    print_graph_status()

    if args.schema:
        logger.info("--schema flag: exiting after schema setup")
        sys.exit(0)

    logger.info("Bootstrap complete — starting application")


if __name__ == "__main__":
    main()
