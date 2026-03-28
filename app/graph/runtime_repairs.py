"""Runtime graph repair hooks for user-facing severity consistency."""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

from app.graph.fix_primekg_unknown_severities import repair_primekg_unknown_severities
from app.graph.fix_unknown_severities import DEFAULT_GROQ_MODEL, infer_unknown_severities
from app.graph.neo4j_connection import get_driver

logger = logging.getLogger(__name__)

_REPAIRS_APPLIED = False
_REPAIRS_IN_PROGRESS = False
_REPAIRS_LOCK = threading.Lock()


def _unknown_severity_counts(database: str) -> dict[str, int]:
    driver = get_driver()
    with driver.session(database=database) as session:
        record = session.run(
            """
            CALL () {
              MATCH ()-[r1:INTERACTS_WITH {source:'ddinter'}]->()
              WHERE coalesce(r1.severity, 'unknown') = 'unknown'
              RETURN count(r1) AS ddinter_unknown
            }
            CALL () {
              MATCH ()-[r2:INTERACTS_WITH {source:'primekg'}]->()
              WHERE coalesce(r2.severity, 'unknown') = 'unknown'
              RETURN count(r2) AS primekg_unknown
            }
            RETURN ddinter_unknown, primekg_unknown
            """
        ).single()
    return {
        "ddinter_unknown": int(record["ddinter_unknown"]) if record else 0,
        "primekg_unknown": int(record["primekg_unknown"]) if record else 0,
    }


def ensure_runtime_graph_repairs() -> dict[str, Any]:
    """Repair user-facing unknown DDI severities once per process."""
    global _REPAIRS_APPLIED, _REPAIRS_IN_PROGRESS
    with _REPAIRS_LOCK:
        if _REPAIRS_APPLIED:
            return {"status": "skipped", "reason": "already_applied"}
        if _REPAIRS_IN_PROGRESS:
            return {"status": "skipped", "reason": "already_running"}
        _REPAIRS_IN_PROGRESS = True

    try:
        database = os.getenv("NEO4J_DATABASE", "neo4j")
        counts_before = _unknown_severity_counts(database)
        logger.info(
            "Runtime severity repair check: ddinter_unknown=%d primekg_unknown=%d",
            counts_before["ddinter_unknown"],
            counts_before["primekg_unknown"],
        )

        results: dict[str, Any] = {"counts_before": counts_before}
        driver = get_driver()

        if counts_before["ddinter_unknown"] > 0:
            results["ddinter_repair"] = infer_unknown_severities(
                driver,
                database=database,
                batch_size=1_000,
                llm_batch_size=10,
                groq_api_key="",
                groq_model=DEFAULT_GROQ_MODEL,
            )

        if counts_before["primekg_unknown"] > 0:
            results["primekg_repair"] = repair_primekg_unknown_severities(
                driver,
                database=database,
                batch_size=20_000,
            )

        counts_after = _unknown_severity_counts(database)
        results["counts_after"] = counts_after
        _REPAIRS_APPLIED = True
        logger.info(
            "Runtime severity repair complete: ddinter_unknown=%d primekg_unknown=%d",
            counts_after["ddinter_unknown"],
            counts_after["primekg_unknown"],
        )
        return results
    finally:
        with _REPAIRS_LOCK:
            _REPAIRS_IN_PROGRESS = False


def start_runtime_graph_repairs() -> dict[str, Any]:
    """Kick off runtime repairs in a background thread without blocking startup."""
    with _REPAIRS_LOCK:
        if _REPAIRS_APPLIED:
            return {"status": "skipped", "reason": "already_applied"}
        if _REPAIRS_IN_PROGRESS:
            return {"status": "skipped", "reason": "already_running"}

    def _runner() -> None:
        try:
            ensure_runtime_graph_repairs()
        except Exception:
            logger.exception("Background runtime severity repair failed")

    thread = threading.Thread(
        target=_runner,
        name="runtime-graph-repairs",
        daemon=True,
    )
    thread.start()
    return {"status": "started"}
