"""Ingest AGS Beers Criteria 2023 JSON into Neo4j."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import string
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "beers_criteria.json"

READ_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

DRUG_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT drug_generic_name IF NOT EXISTS "
    "FOR (d:Drug) REQUIRE d.generic_name IS UNIQUE"
)
CONDITION_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT condition_name IF NOT EXISTS "
    "FOR (c:Condition) REQUIRE c.name IS UNIQUE"
)
BEERS_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT beers_criteria_edition IF NOT EXISTS "
    "FOR (b:BeersCriteria) REQUIRE b.edition IS UNIQUE"
)

DRUG_DEFAULTS_CYPHER = (
    "drug.rxcui = '', "
    "drug.drug_class = '', "
    "drug.is_nti = false, "
    "drug.is_beers = false, "
    "drug.anticholinergic_score = 0"
)

BEERS_NODE_QUERY = """
MERGE (criteria:BeersCriteria {edition: 'AGS 2023'})
SET criteria.title = $title,
    criteria.source = 'beers_2023',
    criteria.document_path = $document_path
"""

TABLE2_BATCH_QUERY = f"""
UNWIND $rows AS row
MERGE (drug:Drug {{generic_name: row.drug_name}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER}
SET drug.is_beers = true,
    drug.beers_category = CASE
        WHEN coalesce(drug.beers_category, '') = '' THEN 'avoid'
        WHEN toLower(drug.beers_category) CONTAINS 'avoid' THEN drug.beers_category
        ELSE drug.beers_category + ' | avoid'
    END,
    drug.beers_rationale = CASE
        WHEN coalesce(drug.beers_rationale, '') = '' THEN row.rationale
        WHEN drug.beers_rationale CONTAINS row.rationale THEN drug.beers_rationale
        ELSE drug.beers_rationale + ' | ' + row.rationale
    END,
    drug.beers_recommendation = coalesce(drug.beers_recommendation, row.recommendation)
WITH drug, row
MERGE (criteria:BeersCriteria {{edition: 'AGS 2023'}})
MERGE (drug)-[flag:FLAGGED_BY {{source: 'beers_2023', table: 'table2'}}]->(criteria)
SET flag.recommendation = row.recommendation,
    flag.rationale = row.rationale,
    flag.quality_of_evidence = row.quality_of_evidence,
    flag.strength = row.strength
"""

TABLE3_BATCH_QUERY = f"""
UNWIND $rows AS row
MERGE (drug:Drug {{generic_name: row.drug_name}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER}
SET drug.is_beers = true
MERGE (condition:Condition {{name: row.condition_name}})
MERGE (drug)-[relationship:CONTRAINDICATED_IN {{source: 'beers_2023'}}]->(condition)
SET relationship.reason = row.reason,
    relationship.recommendation = row.recommendation,
    relationship.quality_of_evidence = row.quality_of_evidence,
    relationship.strength = row.strength,
    relationship.beers_table = 'table3'
WITH drug, row
MERGE (criteria:BeersCriteria {{edition: 'AGS 2023'}})
MERGE (drug)-[flag:FLAGGED_BY {{source: 'beers_2023', table: 'table3'}}]->(criteria)
SET flag.condition_name = row.condition_name,
    flag.rationale = row.reason,
    flag.recommendation = row.recommendation,
    flag.quality_of_evidence = row.quality_of_evidence,
    flag.strength = row.strength
"""

TABLE6_BATCH_QUERY = f"""
UNWIND $rows AS row
MERGE (drug:Drug {{generic_name: row.drug_name}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER}
SET drug.is_beers = true,
    drug.renal_dose_adjust = row.renal_dose_adjust
WITH drug, row
MERGE (criteria:BeersCriteria {{edition: 'AGS 2023'}})
MERGE (drug)-[flag:FLAGGED_BY {{source: 'beers_2023', table: 'table6'}}]->(criteria)
SET flag.rationale = row.rationale,
    flag.recommendation = row.recommendation,
    flag.quality_of_evidence = row.quality_of_evidence,
    flag.strength = row.strength
"""

TABLE7_BATCH_QUERY = f"""
UNWIND $rows AS row
MERGE (drug:Drug {{generic_name: row.drug_name}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER}
SET drug.is_beers = true,
    drug.anticholinergic_score = row.anticholinergic_score,
    drug.anticholinergic_burden = row.anticholinergic_burden,
    drug.anticholinergic_score_basis = row.score_basis,
    drug.anticholinergic_citation = row.citation
WITH drug, row
MERGE (criteria:BeersCriteria {{edition: 'AGS 2023'}})
MERGE (drug)-[flag:FLAGGED_BY {{source: 'beers_2023', table: 'table7'}}]->(criteria)
SET flag.score_basis = row.score_basis,
    flag.anticholinergic_score = row.anticholinergic_score,
    flag.citation = row.citation
"""

TABLE5_APPLY_QUERY = f"""
MERGE (drug_a:Drug {{generic_name: $drug_a}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER.replace('drug.', 'drug_a.')}
MERGE (drug_b:Drug {{generic_name: $drug_b}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER.replace('drug.', 'drug_b.')}
WITH drug_a, drug_b
OPTIONAL MATCH (drug_a)-[existing:INTERACTS_WITH]-(drug_b)
WITH drug_a, drug_b, collect(existing) AS existing_relationships
FOREACH (relationship IN existing_relationships |
    SET relationship.beers_flagged = true,
        relationship.beers_risk = $risk,
        relationship.beers_recommendation = $recommendation,
        relationship.beers_quality_of_evidence = $quality_of_evidence,
        relationship.beers_strength = $strength
)
FOREACH (_ IN CASE WHEN size(existing_relationships) = 0 THEN [1] ELSE [] END |
    MERGE (drug_a)-[relationship:INTERACTS_WITH {{source: 'beers_2023'}}]->(drug_b)
    SET relationship.severity = 'major',
        relationship.clinical_effect = $risk,
        relationship.management = $recommendation,
        relationship.evidence_level = $quality_of_evidence,
        relationship.beers_flagged = true,
        relationship.beers_risk = $risk,
        relationship.beers_recommendation = $recommendation,
        relationship.beers_quality_of_evidence = $quality_of_evidence,
        relationship.beers_strength = $strength
)
RETURN size(existing_relationships) AS existing_count
"""


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\x00", " ").replace("\xa0", " ")).strip()
    return cleaned or None


def _normalize_lookup_key(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _strip_qualifiers(value: str | None) -> str | None:
    normalized = _normalize_lookup_key(value)
    if normalized is None:
        return None
    tokens = [
        token
        for token in normalized.split()
        if token
        not in {
            "hydrochloride",
            "hcl",
            "sodium",
            "potassium",
            "succinate",
            "tartrate",
            "phosphate",
            "medoxomil",
            "dipropionate",
            "acetate",
            "maleate",
            "sulfate",
        }
    ]
    return " ".join(tokens).strip() or normalized


def _smart_title(value: str) -> str:
    parts = []
    for token in value.split():
        if token.isupper():
            parts.append(token)
            continue
        parts.append(string.capwords(token, sep="-"))
    return " ".join(parts)


def _resolve_data_path(data_path: Path) -> Path:
    resolved = data_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Beers JSON path does not exist: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"Beers path must be a file: {resolved}")
    return resolved


def _load_json(path: Path) -> dict[str, Any]:
    last_error: UnicodeDecodeError | None = None
    for encoding in READ_ENCODINGS:
        try:
            with path.open("r", encoding=encoding) as handle:
                payload = json.load(handle)
            LOGGER.info("Loaded %s using encoding=%s.", path.name, encoding)
            return payload
        except UnicodeDecodeError as exc:
            last_error = exc

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        payload = json.load(handle)
    LOGGER.warning(
        "Falling back to utf-8 replacement decoding for %s after decode errors: %s",
        path.name,
        last_error,
    )
    return payload


def _ensure_schema(driver: Driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run(DRUG_CONSTRAINT_QUERY).consume()
        session.run(CONDITION_CONSTRAINT_QUERY).consume()
        session.run(BEERS_CONSTRAINT_QUERY).consume()


def _load_existing_drug_lookup(driver: Driver, database: str) -> tuple[dict[str, str], dict[str, str]]:
    exact_lookup: dict[str, str] = {}
    stripped_lookup: dict[str, str] = {}
    query = "MATCH (drug:Drug) RETURN drug.generic_name AS generic_name"

    with driver.session(database=database) as session:
        for record in session.run(query):
            generic_name = _clean_text(record["generic_name"])
            if not generic_name:
                continue
            normalized = _normalize_lookup_key(generic_name)
            stripped = _strip_qualifiers(generic_name)
            if normalized:
                exact_lookup[normalized] = generic_name
            if stripped:
                stripped_lookup.setdefault(stripped, generic_name)

    return exact_lookup, stripped_lookup


def _canonical_drug_name(
    raw_name: str,
    *,
    exact_lookup: dict[str, str],
    stripped_lookup: dict[str, str],
) -> str:
    cleaned = _clean_text(raw_name)
    if cleaned is None:
        raise ValueError("drug name is required")

    normalized = _normalize_lookup_key(cleaned)
    stripped = _strip_qualifiers(cleaned)
    if normalized and normalized in exact_lookup:
        return exact_lookup[normalized]
    if stripped and stripped in stripped_lookup:
        return stripped_lookup[stripped]
    return _smart_title(cleaned)


def _ensure_beers_node(driver: Driver, database: str, payload: dict[str, Any], data_path: Path) -> None:
    metadata = payload.get("metadata") or {}
    with driver.session(database=database) as session:
        session.run(
            BEERS_NODE_QUERY,
            title=_clean_text(metadata.get("source_title")) or "AGS Beers Criteria 2023",
            document_path=str(data_path),
        ).consume()


def _write_batch(tx: ManagedTransaction, query: str, rows: list[dict[str, Any]]) -> int:
    tx.run(query, rows=rows).consume()
    return len(rows)


def _batch_write(
    driver: Driver,
    database: str,
    query: str,
    rows: list[dict[str, Any]],
    *,
    batch_size: int,
) -> int:
    if not rows:
        return 0

    written = 0
    with driver.session(database=database) as session:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            written += session.execute_write(_write_batch, query, batch)
    return written


def _append_unique_pair(
    pairs: dict[tuple[str, str], dict[str, str]],
    *,
    drug_a: str,
    drug_b: str,
    risk: str,
    recommendation: str,
    quality_of_evidence: str,
    strength: str,
) -> None:
    if drug_a.casefold() == drug_b.casefold():
        return
    ordered = tuple(sorted((drug_a, drug_b), key=str.casefold))
    pairs.setdefault(
        ordered,
        {
            "drug_a": ordered[0],
            "drug_b": ordered[1],
            "risk": risk,
            "recommendation": recommendation,
            "quality_of_evidence": quality_of_evidence,
            "strength": strength,
        },
    )


def ingest(
    driver: Driver,
    data_path: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> dict[str, int]:
    """Load AGS Beers Criteria JSON into Neo4j."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    resolved_path = _resolve_data_path(data_path)
    payload = _load_json(resolved_path)
    _ensure_schema(driver, database)
    _ensure_beers_node(driver, database, payload, resolved_path)
    exact_lookup, stripped_lookup = _load_existing_drug_lookup(driver, database)

    table2_rows: list[dict[str, str]] = []
    for entry in payload.get("table2_avoid_in_older_adults", []):
        rationale = _clean_text(entry.get("rationale"))
        recommendation = _clean_text(entry.get("recommendation"))
        quality_of_evidence = _clean_text(entry.get("quality_of_evidence"))
        strength = _clean_text(entry.get("strength"))
        for drug_name in entry.get("drug_names", []):
            canonical = _canonical_drug_name(
                drug_name,
                exact_lookup=exact_lookup,
                stripped_lookup=stripped_lookup,
            )
            normalized = _normalize_lookup_key(canonical)
            stripped = _strip_qualifiers(canonical)
            if normalized:
                exact_lookup[normalized] = canonical
            if stripped:
                stripped_lookup.setdefault(stripped, canonical)
            table2_rows.append(
                {
                    "drug_name": canonical,
                    "rationale": rationale or "",
                    "recommendation": recommendation or "",
                    "quality_of_evidence": quality_of_evidence or "",
                    "strength": strength or "",
                }
            )

    table3_rows: list[dict[str, str]] = []
    for entry in payload.get("table3_drug_disease_interactions", []):
        condition_name = _clean_text(entry.get("disease_or_syndrome"))
        if not condition_name:
            continue
        rationale = _clean_text(entry.get("rationale"))
        recommendation = _clean_text(entry.get("recommendation"))
        quality_of_evidence = _clean_text(entry.get("quality_of_evidence"))
        strength = _clean_text(entry.get("strength"))
        for drug_name in entry.get("drug_names", []):
            canonical = _canonical_drug_name(
                drug_name,
                exact_lookup=exact_lookup,
                stripped_lookup=stripped_lookup,
            )
            normalized = _normalize_lookup_key(canonical)
            stripped = _strip_qualifiers(canonical)
            if normalized:
                exact_lookup[normalized] = canonical
            if stripped:
                stripped_lookup.setdefault(stripped, canonical)
            table3_rows.append(
                {
                    "drug_name": canonical,
                    "condition_name": condition_name,
                    "reason": rationale or "",
                    "recommendation": recommendation or "",
                    "quality_of_evidence": quality_of_evidence or "",
                    "strength": strength or "",
                }
            )

    table5_pairs: dict[tuple[str, str], dict[str, str]] = {}
    for entry in payload.get("table5_drug_drug_interactions", []):
        risk = _clean_text(entry.get("risk")) or "Major drug-drug interaction risk"
        recommendation = _clean_text(entry.get("recommendation")) or "Avoid if possible."
        quality_of_evidence = _clean_text(entry.get("quality_of_evidence")) or "Moderate"
        strength = _clean_text(entry.get("strength")) or "Strong"
        drugs_1 = [
            _canonical_drug_name(
                drug_name,
                exact_lookup=exact_lookup,
                stripped_lookup=stripped_lookup,
            )
            for drug_name in entry.get("drug_names_1", [])
            if _clean_text(drug_name)
        ]
        drugs_2 = [
            _canonical_drug_name(
                drug_name,
                exact_lookup=exact_lookup,
                stripped_lookup=stripped_lookup,
            )
            for drug_name in entry.get("drug_names_2", [])
            if _clean_text(drug_name)
        ]
        for drug_a in drugs_1:
            for drug_b in drugs_2:
                _append_unique_pair(
                    table5_pairs,
                    drug_a=drug_a,
                    drug_b=drug_b,
                    risk=risk,
                    recommendation=recommendation,
                    quality_of_evidence=quality_of_evidence,
                    strength=strength,
                )

    table6_rows: list[dict[str, str]] = []
    for entry in payload.get("table6_renal_dose_adjustments", []):
        threshold = _clean_text(entry.get("threshold")) or ""
        recommendation = _clean_text(entry.get("recommendation")) or ""
        renal_function_metric = _clean_text(entry.get("renal_function_metric")) or "CrCl"
        rationale = _clean_text(entry.get("rationale"))
        quality_of_evidence = _clean_text(entry.get("quality_of_evidence"))
        strength = _clean_text(entry.get("strength"))
        renal_dose_adjust = f"{renal_function_metric} {threshold}: {recommendation}".strip(": ")
        for drug_name in entry.get("drug_names", []):
            canonical = _canonical_drug_name(
                drug_name,
                exact_lookup=exact_lookup,
                stripped_lookup=stripped_lookup,
            )
            normalized = _normalize_lookup_key(canonical)
            stripped = _strip_qualifiers(canonical)
            if normalized:
                exact_lookup[normalized] = canonical
            if stripped:
                stripped_lookup.setdefault(stripped, canonical)
            table6_rows.append(
                {
                    "drug_name": canonical,
                    "renal_dose_adjust": renal_dose_adjust,
                    "rationale": rationale or "",
                    "recommendation": recommendation,
                    "quality_of_evidence": quality_of_evidence or "",
                    "strength": strength or "",
                }
            )

    table7_rows: list[dict[str, Any]] = []
    for entry in payload.get("table7_anticholinergic_drugs", []):
        drug_name = _clean_text(entry.get("drug_name"))
        if not drug_name:
            continue
        canonical = _canonical_drug_name(
            drug_name,
            exact_lookup=exact_lookup,
            stripped_lookup=stripped_lookup,
        )
        normalized = _normalize_lookup_key(canonical)
        stripped = _strip_qualifiers(canonical)
        if normalized:
            exact_lookup[normalized] = canonical
        if stripped:
            stripped_lookup.setdefault(stripped, canonical)
        table7_rows.append(
            {
                "drug_name": canonical,
                "anticholinergic_score": int(entry.get("anticholinergic_score") or 0),
                "anticholinergic_burden": _clean_text(entry.get("anticholinergic_burden")) or "",
                "score_basis": _clean_text(entry.get("score_basis")) or "",
                "citation": _clean_text(entry.get("citation")) or "",
            }
        )

    summary: dict[str, int] = {}
    summary["table2_drugs"] = _batch_write(
        driver,
        database,
        TABLE2_BATCH_QUERY,
        table2_rows,
        batch_size=batch_size,
    )
    LOGGER.info("Beers Table 2 complete: %s drug rows processed.", f"{summary['table2_drugs']:,}")

    summary["table3_pairs"] = _batch_write(
        driver,
        database,
        TABLE3_BATCH_QUERY,
        table3_rows,
        batch_size=batch_size,
    )
    LOGGER.info(
        "Beers Table 3 complete: %s drug-disease contraindication rows processed.",
        f"{summary['table3_pairs']:,}",
    )

    flagged_existing = 0
    created_new = 0
    with driver.session(database=database) as session:
        for pair in table5_pairs.values():
            existing_count = session.execute_write(
                lambda tx: tx.run(TABLE5_APPLY_QUERY, **pair).single()["existing_count"]
            )
            if existing_count:
                flagged_existing += 1
            else:
                created_new += 1
    summary["table5_existing_flagged"] = flagged_existing
    summary["table5_created_new"] = created_new
    LOGGER.info(
        "Beers Table 5 complete: %s existing interaction pairs flagged, %s Beers-only interaction pairs created.",
        f"{flagged_existing:,}",
        f"{created_new:,}",
    )

    summary["table6_drugs"] = _batch_write(
        driver,
        database,
        TABLE6_BATCH_QUERY,
        table6_rows,
        batch_size=batch_size,
    )
    LOGGER.info(
        "Beers Table 6 complete: %s renal dose-adjustment rows processed.",
        f"{summary['table6_drugs']:,}",
    )

    summary["table7_drugs"] = _batch_write(
        driver,
        database,
        TABLE7_BATCH_QUERY,
        table7_rows,
        batch_size=batch_size,
    )
    LOGGER.info(
        "Beers Table 7 complete: %s anticholinergic rows processed.",
        f"{summary['table7_drugs']:,}",
    )

    with driver.session(database=database) as session:
        summary["beers_drugs"] = session.run(
            "MATCH (d:Drug) WHERE d.is_beers = true RETURN count(d) AS count"
        ).single()["count"]
        summary["beers_contraindications"] = session.run(
            "MATCH ()-[r:CONTRAINDICATED_IN {source: 'beers_2023'}]->() RETURN count(r) AS count"
        ).single()["count"]
        summary["beers_nodes"] = session.run(
            "MATCH (b:BeersCriteria) RETURN count(b) AS count"
        ).single()["count"]
        summary["flagged_by_edges"] = session.run(
            "MATCH ()-[r:FLAGGED_BY {source: 'beers_2023'}]->() RETURN count(r) AS count"
        ).single()["count"]

    LOGGER.info(
        "Beers ingestion complete: %s Drug nodes flagged, %s Beers contraindication edges, %s FLAGGED_BY edges.",
        f"{summary['beers_drugs']:,}",
        f"{summary['beers_contraindications']:,}",
        f"{summary['flagged_by_edges']:,}",
    )
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest AGS Beers Criteria 2023 JSON into Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help="Path to beers_criteria.json.",
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password.")
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Batch size.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    """CLI entry point for Beers ingestion."""
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
            database=args.database,
        )
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
