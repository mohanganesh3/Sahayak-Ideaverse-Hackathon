"""Ingest OnSIDES drug-adverse event data into Neo4j.

OnSIDES contains drug side effects extracted from FDA Structured Product Labels.
Uses the pre-computed high_confidence.csv as the primary source (664 curated
ingredient-effect pairs) and also streams the large product_adverse_effect.csv
to derive additional ingredient-level pairs via the vocabulary mapping tables.

Only links drugs already present in the graph — does NOT create new Drug nodes.
Creates SideEffect nodes and MAY_CAUSE relationships.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 10_000
DEFAULT_PROGRESS_EVERY = 1_000_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

MAY_CAUSE_BATCH_QUERY = """
UNWIND $rows AS row
MATCH (d:Drug {generic_name: row.drug_name})
MERGE (se:SideEffect {name: row.side_effect_name})
SET se.meddra_code = coalesce(se.meddra_code, row.meddra_code)
MERGE (d)-[r:MAY_CAUSE {source: 'onsides'}]->(se)
SET r.side_effect = row.side_effect_name,
    r.side_effect_name = row.side_effect_name,
    r.confidence = row.confidence
"""


def _default_data_dir() -> Path:
    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "onsides"
    return Path.home() / "IDEAVERSE" / "sahayak-data" / "onsides"


DEFAULT_DATA_DIR = _default_data_dir()


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
    LOGGER.info("Loaded %d existing Drug names for OnSIDES matching.", len(lookup))
    return lookup


def _load_rxnorm_ingredients(csv_dir: Path) -> dict[str, str]:
    """Load vocab_rxnorm_ingredient.csv: rxnorm_id -> rxnorm_name."""
    path = csv_dir / "vocab_rxnorm_ingredient.csv"
    lookup: dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rxnorm_id = row.get("rxnorm_id", "").strip()
            rxnorm_name = _clean_text(row.get("rxnorm_name"))
            if rxnorm_id and rxnorm_name:
                lookup[rxnorm_id] = rxnorm_name
    LOGGER.info("Loaded %d RxNorm ingredient names.", len(lookup))
    return lookup


def _load_meddra_effects(csv_dir: Path) -> dict[str, str]:
    """Load vocab_meddra_adverse_effect.csv: meddra_id -> meddra_name."""
    path = csv_dir / "vocab_meddra_adverse_effect.csv"
    lookup: dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            meddra_id = row.get("meddra_id", "").strip()
            meddra_name = _clean_text(row.get("meddra_name"))
            if meddra_id and meddra_name:
                lookup[meddra_id] = meddra_name
    LOGGER.info("Loaded %d MedDRA adverse effect names.", len(lookup))
    return lookup


def _load_product_to_ingredients(csv_dir: Path) -> dict[str, set[str]]:
    """Load vocab_rxnorm_ingredient_to_product.csv: product_id -> set of ingredient_ids."""
    path = csv_dir / "vocab_rxnorm_ingredient_to_product.csv"
    lookup: dict[str, set[str]] = defaultdict(set)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            product_id = row.get("product_id", "").strip()
            ingredient_id = row.get("ingredient_id", "").strip()
            if product_id and ingredient_id:
                lookup[product_id].add(ingredient_id)
    LOGGER.info("Loaded product-to-ingredient mappings for %d products.", len(lookup))
    return lookup


def _load_label_to_product(csv_dir: Path) -> dict[str, str]:
    """Load product_to_rxnorm.csv: label_id -> rxnorm_product_id."""
    path = csv_dir / "product_to_rxnorm.csv"
    lookup: dict[str, str] = {}
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        for row in reader:
            label_id = row.get("label_id", "").strip()
            product_id = row.get("rxnorm_product_id", "").strip()
            if label_id and product_id:
                lookup[label_id] = product_id
    LOGGER.info("Loaded %d label-to-product mappings.", len(lookup))
    return lookup


def _write_batch(tx: ManagedTransaction, rows: list[dict[str, Any]]) -> int:
    tx.run(MAY_CAUSE_BATCH_QUERY, rows=rows).consume()
    return len(rows)


def ingest(
    driver: Driver,
    data_dir: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> dict[str, int]:
    """Load OnSIDES data into Neo4j as MAY_CAUSE edges from Drug to SideEffect.

    Only links drugs already in the graph.
    Aggregates at ingredient level across products.

    Returns:
        Dict with high_confidence_pairs, label_derived_pairs, matched_pairs,
        skipped_unknown_drugs, edges_written.
    """
    resolved = data_dir.expanduser().resolve()
    csv_dir = resolved / "csv"
    if not csv_dir.exists():
        raise FileNotFoundError(f"OnSIDES csv directory not found: {csv_dir}")

    # Load vocabulary tables
    rxnorm_ingredients = _load_rxnorm_ingredients(csv_dir)
    meddra_effects = _load_meddra_effects(csv_dir)
    product_to_ingredients = _load_product_to_ingredients(csv_dir)
    label_to_product = _load_label_to_product(csv_dir)

    # Load existing Drug nodes for matching
    drug_lookup = _load_existing_drugs(driver, database)

    # Collect unique (ingredient_id, effect_meddra_id) -> confidence
    # "high_confidence" takes priority over "label_derived"
    ingredient_effect_pairs: dict[tuple[str, str], str] = {}

    # Phase 1: high_confidence.csv (curated pairs)
    high_conf_path = csv_dir / "high_confidence.csv"
    high_confidence_count = 0
    if high_conf_path.exists():
        LOGGER.info("Phase 1: Loading high_confidence.csv...")
        with open(high_conf_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ingredient_id = row.get("ingredient_id", "").strip()
                effect_id = row.get("effect_meddra_id", "").strip()
                if ingredient_id and effect_id:
                    ingredient_effect_pairs[(ingredient_id, effect_id)] = "high_confidence"
                    high_confidence_count += 1
        LOGGER.info("Phase 1 complete: %d high-confidence pairs loaded.", high_confidence_count)
    else:
        LOGGER.warning("high_confidence.csv not found at %s, skipping.", high_conf_path)

    # Phase 2: stream product_adverse_effect.csv and aggregate at ingredient level
    pae_path = csv_dir / "product_adverse_effect.csv"
    source_rows = 0
    skipped_no_mapping = 0
    label_derived_new = 0

    if pae_path.exists():
        LOGGER.info("Phase 2: Streaming product_adverse_effect.csv and aggregating at ingredient level...")
        with open(pae_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f)
            for row in reader:
                source_rows += 1

                label_id = row.get("product_label_id", "").strip()
                effect_id = row.get("effect_meddra_id", "").strip()

                if not label_id or not effect_id:
                    skipped_no_mapping += 1
                    if source_rows % progress_every == 0:
                        LOGGER.info(
                            "Streamed %s rows: %s unique pairs so far.",
                            f"{source_rows:,}",
                            f"{len(ingredient_effect_pairs):,}",
                        )
                    continue

                # Resolve label_id -> product_id -> ingredient_ids
                product_id = label_to_product.get(label_id)
                if not product_id:
                    skipped_no_mapping += 1
                    if source_rows % progress_every == 0:
                        LOGGER.info(
                            "Streamed %s rows: %s unique pairs so far.",
                            f"{source_rows:,}",
                            f"{len(ingredient_effect_pairs):,}",
                        )
                    continue

                ingredient_ids = product_to_ingredients.get(product_id, set())
                if not ingredient_ids:
                    skipped_no_mapping += 1
                    if source_rows % progress_every == 0:
                        LOGGER.info(
                            "Streamed %s rows: %s unique pairs so far.",
                            f"{source_rows:,}",
                            f"{len(ingredient_effect_pairs):,}",
                        )
                    continue

                for ing_id in ingredient_ids:
                    pair_key = (ing_id, effect_id)
                    if pair_key not in ingredient_effect_pairs:
                        ingredient_effect_pairs[pair_key] = "label_derived"
                        label_derived_new += 1

                if source_rows % progress_every == 0:
                    LOGGER.info(
                        "Streamed %s rows: %s unique pairs so far.",
                        f"{source_rows:,}",
                        f"{len(ingredient_effect_pairs):,}",
                    )

        LOGGER.info(
            "Phase 2 complete: %s source rows streamed, %s new label-derived pairs, %s skipped (no mapping).",
            f"{source_rows:,}",
            f"{label_derived_new:,}",
            f"{skipped_no_mapping:,}",
        )
    else:
        LOGGER.warning("product_adverse_effect.csv not found at %s, skipping.", pae_path)

    # Phase 3: resolve pairs to drug names and side effect names, then write to Neo4j
    LOGGER.info(
        "Phase 3: Resolving %s ingredient-effect pairs and writing to Neo4j...",
        f"{len(ingredient_effect_pairs):,}",
    )
    rows_to_write: list[dict[str, Any]] = []
    edges_written = 0
    matched_pairs = 0
    skipped_unknown_drugs = 0
    skipped_unknown_effects = 0
    unknown_drug_names: set[str] = set()

    for (ingredient_id, effect_id), confidence in ingredient_effect_pairs.items():
        # Resolve ingredient_id -> drug name
        ingredient_name = rxnorm_ingredients.get(ingredient_id)
        if not ingredient_name:
            skipped_unknown_drugs += 1
            continue

        # Match to existing Drug node
        norm_key = _normalize_lookup_key(ingredient_name)
        canonical_drug = drug_lookup.get(norm_key) if norm_key else None
        if not canonical_drug:
            skipped_unknown_drugs += 1
            if ingredient_name not in unknown_drug_names:
                unknown_drug_names.add(ingredient_name)
                LOGGER.debug("No matching Drug node for ingredient: %s", ingredient_name)
            continue

        # Resolve effect_id -> side effect name
        effect_name = meddra_effects.get(effect_id)
        if not effect_name:
            skipped_unknown_effects += 1
            continue

        matched_pairs += 1
        rows_to_write.append({
            "drug_name": canonical_drug,
            "side_effect_name": effect_name,
            "meddra_code": effect_id,
            "confidence": confidence,
        })

        if len(rows_to_write) >= batch_size:
            with driver.session(database=database) as session:
                edges_written += session.execute_write(_write_batch, rows_to_write)
            rows_to_write.clear()

    # Flush remaining rows
    if rows_to_write:
        with driver.session(database=database) as session:
            edges_written += session.execute_write(_write_batch, rows_to_write)

    if unknown_drug_names:
        LOGGER.info(
            "Skipped %d unique drug names not found in graph (showing first 20): %s",
            len(unknown_drug_names),
            sorted(unknown_drug_names)[:20],
        )

    LOGGER.info(
        "OnSIDES ingestion complete: %s high-confidence pairs, %s label-derived pairs, "
        "%s total unique pairs, %s matched to graph, %s edges written, "
        "%s skipped (unknown drug), %s skipped (unknown effect).",
        f"{high_confidence_count:,}",
        f"{label_derived_new:,}",
        f"{len(ingredient_effect_pairs):,}",
        f"{matched_pairs:,}",
        f"{edges_written:,}",
        f"{skipped_unknown_drugs:,}",
        f"{skipped_unknown_effects:,}",
    )

    return {
        "high_confidence_pairs": high_confidence_count,
        "label_derived_pairs": label_derived_new,
        "total_unique_pairs": len(ingredient_effect_pairs),
        "matched_pairs": matched_pairs,
        "edges_written": edges_written,
        "skipped_unknown_drugs": skipped_unknown_drugs,
        "skipped_unknown_effects": skipped_unknown_effects,
        "source_rows_streamed": source_rows,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest OnSIDES drug-adverse event data into Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR, help="Path to onsides data directory.")
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
            args.data_dir,
            batch_size=args.batch_size,
            progress_every=args.progress_every,
            database=args.database,
        )
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
