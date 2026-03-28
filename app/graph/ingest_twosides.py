"""Ingest TWOSIDES drug-pair adverse event data into Neo4j.

TWOSIDES contains statistically significant drug-pair adverse events from FAERS.
Only links drugs already present in the graph — does NOT create new Drug nodes.
Aggregates all adverse events for each drug pair into a single COPRESCRIPTION_EFFECT edge.

CSV columns: drug_1_rxnorn_id, drug_1_concept_name, drug_2_rxnorm_id,
drug_2_concept_name, condition_meddra_id, condition_concept_name,
A, B, C, D, PRR, PRR_error, mean_reporting_frequency
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, TextIO

from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5_000
DEFAULT_PROGRESS_EVERY = 1_000_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

COPRESCRIPTION_BATCH_QUERY = """
UNWIND $rows AS row
MATCH (drug_a:Drug {generic_name: row.drug_a})
MATCH (drug_b:Drug {generic_name: row.drug_b})
MERGE (drug_a)-[r:COPRESCRIPTION_EFFECT {source: 'twosides'}]->(drug_b)
SET r.adverse_events = row.adverse_events,
    r.prr = row.prr,
    r.max_prr = row.prr,
    r.num_events = row.num_events,
    r.confidence = row.confidence,
    r.events_truncated = row.events_truncated
"""


def _default_data_path() -> Path:
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "twosides" / "TWOSIDES.csv"
    return Path.home() / "IDEAVERSE" / "sahayak-data" / "twosides" / "TWOSIDES.csv"


DEFAULT_DATA_PATH = _default_data_path()


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\x00", " ")).strip()
    return cleaned or None


def _normalize_lookup_key(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _load_existing_drugs(driver: Driver, database: str) -> dict[str, str]:
    """Load all existing Drug generic_name values, keyed by normalized name."""
    lookup: dict[str, str] = {}
    with driver.session(database=database) as session:
        for record in session.run("MATCH (d:Drug) RETURN d.generic_name AS name"):
            name = _clean_text(record["name"])
            if name:
                key = _normalize_lookup_key(name)
                if key:
                    lookup[key] = name
    LOGGER.info("Loaded %d existing Drug names for TWOSIDES matching.", len(lookup))
    return lookup


def _write_batch(tx: ManagedTransaction, rows: list[dict[str, Any]]) -> int:
    tx.run(COPRESCRIPTION_BATCH_QUERY, rows=rows).consume()
    return len(rows)


def ingest(
    driver: Driver,
    data_path: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> dict[str, int]:
    """Load TWOSIDES CSV into Neo4j as COPRESCRIPTION_EFFECT edges.

    Only links drugs already in the graph.
    Aggregates adverse events per drug pair.

    Returns:
        Dict with source_rows, matched_pairs, skipped_rows, edges_written.
    """
    resolved = data_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"TWOSIDES file not found: {resolved}")

    drug_lookup = _load_existing_drugs(driver, database)

    # Phase 1: stream CSV and aggregate adverse events per drug pair
    LOGGER.info("Phase 1: Scanning TWOSIDES CSV and aggregating adverse events per drug pair...")
    pair_data: dict[tuple[str, str], dict[str, Any]] = {}
    source_rows = 0
    skipped_no_match = 0

    with open(resolved, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            source_rows += 1

            drug_1_name = _clean_text(row.get("drug_1_concept_name"))
            drug_2_name = _clean_text(row.get("drug_2_concept_name"))
            event_name = _clean_text(row.get("condition_concept_name"))

            if not drug_1_name or not drug_2_name or not event_name:
                skipped_no_match += 1
                continue

            key_1 = _normalize_lookup_key(drug_1_name)
            key_2 = _normalize_lookup_key(drug_2_name)

            canonical_1 = drug_lookup.get(key_1) if key_1 else None
            canonical_2 = drug_lookup.get(key_2) if key_2 else None

            if not canonical_1 or not canonical_2:
                skipped_no_match += 1
                continue

            if canonical_1 == canonical_2:
                skipped_no_match += 1
                continue

            # Canonical pair key (alphabetical order)
            pair_key = (canonical_1, canonical_2) if canonical_1 < canonical_2 else (canonical_2, canonical_1)

            try:
                prr = float(row.get("PRR", 0) or 0)
            except (ValueError, TypeError):
                prr = 0.0

            if pair_key not in pair_data:
                pair_data[pair_key] = {"events": {}, "max_prr": prr}
            existing_prr = pair_data[pair_key]["events"].get(event_name)
            if existing_prr is None or prr > existing_prr:
                pair_data[pair_key]["events"][event_name] = prr
            if prr > pair_data[pair_key]["max_prr"]:
                pair_data[pair_key]["max_prr"] = prr

            if source_rows % progress_every == 0:
                LOGGER.info(
                    "Scanned %s rows: %s unique drug pairs so far, %s skipped.",
                    f"{source_rows:,}",
                    f"{len(pair_data):,}",
                    f"{skipped_no_match:,}",
                )

    LOGGER.info(
        "Phase 1 complete: %s source rows, %s unique drug pairs, %s skipped.",
        f"{source_rows:,}",
        f"{len(pair_data):,}",
        f"{skipped_no_match:,}",
    )

    # Phase 2: write aggregated pairs to Neo4j
    LOGGER.info("Phase 2: Writing %s aggregated COPRESCRIPTION_EFFECT edges...", f"{len(pair_data):,}")
    rows_to_write: list[dict[str, Any]] = []
    edges_written = 0

    for (drug_a, drug_b), data in pair_data.items():
        ranked_events = sorted(
            data["events"].items(),
            key=lambda item: (-item[1], item[0]),
        )
        event_names = [event_name for event_name, _ in ranked_events]
        stored_events = event_names[:50]
        rows_to_write.append({
            "drug_a": drug_a,
            "drug_b": drug_b,
            "adverse_events": stored_events,
            "prr": round(data["max_prr"], 4),
            "num_events": len(event_names),
            "confidence": "statistically_significant_faers_signal",
            "events_truncated": len(event_names) > len(stored_events),
        })

        if len(rows_to_write) >= batch_size:
            with driver.session(database=database) as session:
                edges_written += session.execute_write(_write_batch, rows_to_write)
            rows_to_write.clear()

    if rows_to_write:
        with driver.session(database=database) as session:
            edges_written += session.execute_write(_write_batch, rows_to_write)

    LOGGER.info(
        "TWOSIDES ingestion complete: %s source rows, %s drug pairs matched, %s edges written, %s rows skipped.",
        f"{source_rows:,}",
        f"{len(pair_data):,}",
        f"{edges_written:,}",
        f"{skipped_no_match:,}",
    )
    return {
        "source_rows": source_rows,
        "matched_pairs": len(pair_data),
        "edges_written": edges_written,
        "skipped_rows": skipped_no_match,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest TWOSIDES drug-pair adverse events into Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data-path", type=Path, default=DEFAULT_DATA_PATH, help="Path to TWOSIDES.csv.")
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD)
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--progress-every", type=int, default=DEFAULT_PROGRESS_EVERY)
    parser.add_argument("--log-level", default="INFO", choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        driver.verify_connectivity()
        ingest(
            driver,
            args.data_path,
            batch_size=args.batch_size,
            progress_every=args.progress_every,
            database=args.database,
        )
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
