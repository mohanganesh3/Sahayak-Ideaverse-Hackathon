"""Validate curated sentinel interactions against the live Neo4j graph."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase

LOGGER = logging.getLogger(__name__)

DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_SENTINEL_PATH = Path(__file__).resolve().parents[1] / "data" / "sentinel_interactions.json"
DEFAULT_REPORT_PATH = Path(__file__).resolve().parents[2] / "sentinel_validation_report_2026-03-26.json"

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

READ_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
SEVERITY_RANK = {"major": 3, "moderate": 2, "minor": 1, "unknown": 0, "": -1, None: -1}
SOURCE_PRIORITY = {
    "sentinel_curated": 100,
    "ddinter": 90,
    "ddid": 80,
    "curated_ayurveda": 75,
    "beers_2023": 70,
    "sider": 60,
    "onsides": 55,
    "primekg": 50,
    "twosides": 40,
}

RESOLVE_DRUG_EXACT_QUERY = """
MATCH (d:Drug)
WHERE toLower(coalesce(d.generic_name, '')) = toLower($name)
   OR toLower(coalesce(d.canonical_name, '')) = toLower($name)
   OR ANY(s IN coalesce(d.synonyms, []) WHERE toLower(s) = toLower($name))
RETURN elementId(d) AS element_id,
       d.generic_name AS generic_name,
       d.canonical_name AS canonical_name,
       coalesce(d.synonyms, []) AS synonyms
ORDER BY COUNT { (d)--() } DESC, generic_name
"""

RESOLVE_DRUG_FULLTEXT_QUERY = """
CALL db.index.fulltext.queryNodes('drug_synonym_fulltext', $query_text) YIELD node, score
RETURN elementId(node) AS element_id,
       node.generic_name AS generic_name,
       node.canonical_name AS canonical_name,
       coalesce(node.synonyms, []) AS synonyms,
       score
ORDER BY score DESC, generic_name
LIMIT 5
"""

RESOLVE_HERB_QUERY = """
MATCH (h:Herb)
WHERE toLower(coalesce(h.name, '')) = toLower($name)
   OR toLower(coalesce(h.hindi_name, '')) = toLower($name)
   OR toLower(coalesce(h.tamil_name, '')) = toLower($name)
   OR toLower(coalesce(h.telugu_name, '')) = toLower($name)
   OR toLower(coalesce(h.kannada_name, '')) = toLower($name)
   OR toLower(coalesce(h.scientific_name, '')) = toLower($name)
RETURN elementId(h) AS element_id, h.name AS name
ORDER BY COUNT { (h)--() } DESC, name
"""

FETCH_DDI_QUERY = """
MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
WHERE elementId(a) IN $a_ids AND elementId(b) IN $b_ids
RETURN a.generic_name AS drug_a,
       b.generic_name AS drug_b,
       r.severity AS severity,
       r.source AS source,
       r.mechanism AS mechanism,
       r.clinical_effect AS clinical_effect,
       r.management AS management
"""

FETCH_HDI_QUERY = """
MATCH (h:Herb)-[r:INTERACTS_WITH_DRUG]->(d:Drug)
WHERE elementId(h) IN $herb_ids AND elementId(d) IN $drug_ids
RETURN h.name AS herb,
       d.generic_name AS drug,
       r.severity AS severity,
       r.source AS source,
       r.mechanism AS mechanism,
       r.clinical_effect AS clinical_effect,
       r.management AS management
"""

FIND_MULTIHOP_DDI_QUERY = """
MATCH (a:Drug) WHERE elementId(a) IN $a_ids
MATCH (b:Drug) WHERE elementId(b) IN $b_ids
MATCH p = shortestPath((a)-[*..3]-(b))
RETURN [n IN nodes(p) | coalesce(n.generic_name, n.name, n.brand_name, n.identifier)] AS node_labels,
       [rel IN relationships(p) | type(rel)] AS rel_types
LIMIT 1
"""

FIND_MULTIHOP_HDI_QUERY = """
MATCH (h:Herb) WHERE elementId(h) IN $herb_ids
MATCH (d:Drug) WHERE elementId(d) IN $drug_ids
MATCH p = shortestPath((h)-[*..3]-(d))
RETURN [n IN nodes(p) | coalesce(n.name, n.generic_name, n.brand_name, n.identifier)] AS node_labels,
       [rel IN relationships(p) | type(rel)] AS rel_types
LIMIT 1
"""

CREATE_DRUG_QUERY = """
MERGE (d:Drug {generic_name: $generic_name})
ON CREATE SET d.rxcui = '',
              d.drug_class = '',
              d.atc_code = '',
              d.is_nti = false,
              d.is_beers = false,
              d.anticholinergic_score = 0
RETURN elementId(d) AS element_id, d.generic_name AS generic_name
"""

CREATE_HERB_QUERY = """
MERGE (h:Herb {name: $name})
ON CREATE SET h.category = 'curated_sentinel'
RETURN elementId(h) AS element_id, h.name AS name
"""

UPSERT_SENTINEL_DDI_QUERY = """
MATCH (a:Drug) WHERE elementId(a) = $a_id
MATCH (b:Drug) WHERE elementId(b) = $b_id
MERGE (a)-[r:INTERACTS_WITH {source: 'sentinel_curated'}]->(b)
SET r.severity = $severity,
    r.mechanism = $mechanism,
    r.clinical_effect = $mechanism,
    r.management = 'Curated sentinel validation edge; verify with clinician context.',
    r.evidence_level = 'sentinel_curated',
    r.severity_source = 'sentinel_curated',
    r.severity_confidence = 'high'
RETURN type(r) AS rel_type
"""

UPSERT_SENTINEL_HDI_QUERY = """
MATCH (h:Herb) WHERE elementId(h) = $herb_id
MATCH (d:Drug) WHERE elementId(d) = $drug_id
MERGE (h)-[r:INTERACTS_WITH_DRUG {source: 'sentinel_curated'}]->(d)
SET r.severity = $severity,
    r.mechanism = $mechanism,
    r.clinical_effect = $mechanism,
    r.management = 'Curated sentinel validation edge; verify with clinician context.'
RETURN type(r) AS rel_type
"""


@dataclass(slots=True)
class SentinelResult:
    kind: str
    label: str
    expected_severity: str
    found: bool
    severity_match: bool
    actual_severity: str | None
    actual_source: str | None
    matched_nodes: list[str]
    path_hint: dict[str, Any] | None
    repaired: bool = False
    repair_reason: str | None = None


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\x00", " ").replace("\xa0", " ")).strip()
    return cleaned or None


def _normalize_key(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _smart_name(raw_name: str) -> str:
    cleaned = _clean_text(raw_name) or raw_name
    if cleaned.isupper() or any(ch.isupper() for ch in cleaned[1:]):
        return cleaned
    return " ".join(token.capitalize() for token in cleaned.split())


def _load_json(path: Path) -> list[dict[str, Any]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in READ_ENCODINGS:
        try:
            return json.loads(path.read_text(encoding=encoding))
        except UnicodeDecodeError as exc:
            last_error = exc
    LOGGER.warning("Fallback utf-8 replacement decode for %s after %s", path.name, last_error)
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _resolve_drug(driver: Driver, database: str, name: str) -> list[dict[str, Any]]:
    with driver.session(database=database) as session:
        exact = [record.data() for record in session.run(RESOLVE_DRUG_EXACT_QUERY, name=name)]
        if exact:
            return exact
        query_text = '"' + (_clean_text(name) or name) + '"'
        return [record.data() for record in session.run(RESOLVE_DRUG_FULLTEXT_QUERY, query_text=query_text)]


def _resolve_herb(driver: Driver, database: str, name: str) -> list[dict[str, Any]]:
    with driver.session(database=database) as session:
        return [record.data() for record in session.run(RESOLVE_HERB_QUERY, name=name)]


def _pick_preferred_edge(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(
        rows,
        key=lambda row: (
            SOURCE_PRIORITY.get(row.get("source"), 0),
            SEVERITY_RANK.get(row.get("severity"), -1),
        ),
        reverse=True,
    )[0]


def _find_direct_ddi(driver: Driver, database: str, a_nodes: list[dict[str, Any]], b_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    a_ids = [row["element_id"] for row in a_nodes]
    b_ids = [row["element_id"] for row in b_nodes]
    if not a_ids or not b_ids:
        return []
    with driver.session(database=database) as session:
        return [record.data() for record in session.run(FETCH_DDI_QUERY, a_ids=a_ids, b_ids=b_ids)]


def _find_direct_hdi(driver: Driver, database: str, herb_nodes: list[dict[str, Any]], drug_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    herb_ids = [row["element_id"] for row in herb_nodes]
    drug_ids = [row["element_id"] for row in drug_nodes]
    if not herb_ids or not drug_ids:
        return []
    with driver.session(database=database) as session:
        return [record.data() for record in session.run(FETCH_HDI_QUERY, herb_ids=herb_ids, drug_ids=drug_ids)]


def _find_path_hint(
    driver: Driver,
    database: str,
    *,
    kind: str,
    left_ids: list[str],
    right_ids: list[str],
) -> dict[str, Any] | None:
    if not left_ids or not right_ids:
        return None
    query = FIND_MULTIHOP_DDI_QUERY if kind == "drug-drug" else FIND_MULTIHOP_HDI_QUERY
    param_names = {"a_ids": left_ids, "b_ids": right_ids} if kind == "drug-drug" else {"herb_ids": left_ids, "drug_ids": right_ids}
    with driver.session(database=database) as session:
        record = session.run(query, **param_names).single()
        return record.data() if record else None


def _ensure_drug(driver: Driver, database: str, name: str) -> dict[str, Any]:
    resolved = _resolve_drug(driver, database, name)
    if resolved:
        return resolved[0]
    create_name = _smart_name(name)
    with driver.session(database=database) as session:
        return session.run(CREATE_DRUG_QUERY, generic_name=create_name).single().data()


def _ensure_herb(driver: Driver, database: str, name: str) -> dict[str, Any]:
    resolved = _resolve_herb(driver, database, name)
    if resolved:
        return resolved[0]
    create_name = _smart_name(name)
    with driver.session(database=database) as session:
        return session.run(CREATE_HERB_QUERY, name=create_name).single().data()


def _upsert_sentinel_interaction(driver: Driver, database: str, entry: dict[str, Any], reason: str) -> None:
    mechanism = _clean_text(entry.get("mechanism")) or "Curated sentinel interaction"
    severity = _clean_text(entry.get("expected_severity")) or "moderate"
    if "herb" in entry:
        herb_node = _ensure_herb(driver, database, entry["herb"])
        drug_node = _ensure_drug(driver, database, entry["drug"])
        with driver.session(database=database) as session:
            session.run(
                UPSERT_SENTINEL_HDI_QUERY,
                herb_id=herb_node["element_id"],
                drug_id=drug_node["element_id"],
                severity=severity,
                mechanism=mechanism,
            ).consume()
        LOGGER.info("Sentinel repair (%s): %s -> %s", reason, herb_node["name"], drug_node["generic_name"])
        return

    drug_a = _ensure_drug(driver, database, entry["drug_a"])
    drug_b = _ensure_drug(driver, database, entry["drug_b"])
    with driver.session(database=database) as session:
        session.run(
            UPSERT_SENTINEL_DDI_QUERY,
            a_id=drug_a["element_id"],
            b_id=drug_b["element_id"],
            severity=severity,
            mechanism=mechanism,
        ).consume()
    LOGGER.info("Sentinel repair (%s): %s -> %s", reason, drug_a["generic_name"], drug_b["generic_name"])


def _evaluate_entry(driver: Driver, database: str, entry: dict[str, Any]) -> SentinelResult:
    expected = _clean_text(entry.get("expected_severity")) or "moderate"
    if "herb" in entry:
        herb = _clean_text(entry["herb"]) or entry["herb"]
        drug = _clean_text(entry["drug"]) or entry["drug"]
        herb_nodes = _resolve_herb(driver, database, herb)
        drug_nodes = _resolve_drug(driver, database, drug)
        rows = _find_direct_hdi(driver, database, herb_nodes, drug_nodes)
        preferred = _pick_preferred_edge(rows)
        path_hint = None
        if not preferred:
            path_hint = _find_path_hint(
                driver,
                database,
                kind="herb-drug",
                left_ids=[row["element_id"] for row in herb_nodes],
                right_ids=[row["element_id"] for row in drug_nodes],
            )
        return SentinelResult(
            kind="herb-drug",
            label=f"{herb} -> {drug}",
            expected_severity=expected,
            found=preferred is not None,
            severity_match=preferred is not None and preferred.get("severity") == expected,
            actual_severity=preferred.get("severity") if preferred else None,
            actual_source=preferred.get("source") if preferred else None,
            matched_nodes=sorted({*(row["name"] for row in herb_nodes), *(row["generic_name"] for row in drug_nodes)}),
            path_hint=path_hint,
        )

    drug_a = _clean_text(entry["drug_a"]) or entry["drug_a"]
    drug_b = _clean_text(entry["drug_b"]) or entry["drug_b"]
    nodes_a = _resolve_drug(driver, database, drug_a)
    nodes_b = _resolve_drug(driver, database, drug_b)
    rows = _find_direct_ddi(driver, database, nodes_a, nodes_b)
    preferred = _pick_preferred_edge(rows)
    path_hint = None
    if not preferred:
        path_hint = _find_path_hint(
            driver,
            database,
            kind="drug-drug",
            left_ids=[row["element_id"] for row in nodes_a],
            right_ids=[row["element_id"] for row in nodes_b],
        )
    return SentinelResult(
        kind="drug-drug",
        label=f"{drug_a} <-> {drug_b}",
        expected_severity=expected,
        found=preferred is not None,
        severity_match=preferred is not None and preferred.get("severity") == expected,
        actual_severity=preferred.get("severity") if preferred else None,
        actual_source=preferred.get("source") if preferred else None,
        matched_nodes=sorted({*(row["generic_name"] for row in nodes_a), *(row["generic_name"] for row in nodes_b)}),
        path_hint=path_hint,
    )


def validate(
    driver: Driver,
    sentinel_entries: list[dict[str, Any]],
    *,
    database: str,
    repair: bool,
) -> dict[str, Any]:
    initial_results = [_evaluate_entry(driver, database, entry) for entry in sentinel_entries]
    repaired_labels: list[str] = []

    if repair:
        for entry, result in zip(sentinel_entries, initial_results, strict=True):
            if not result.found:
                _upsert_sentinel_interaction(driver, database, entry, "not_found")
                repaired_labels.append(result.label)
            elif not result.severity_match:
                _upsert_sentinel_interaction(driver, database, entry, "severity_mismatch")
                repaired_labels.append(result.label)

    final_results = []
    for entry in sentinel_entries:
        result = _evaluate_entry(driver, database, entry)
        if result.label in repaired_labels:
            result.repaired = True
            result.repair_reason = "not_found_or_severity_mismatch"
        final_results.append(result)

    return {
        "before": {
            "found": sum(1 for result in initial_results if result.found),
            "severity_matched": sum(1 for result in initial_results if result.severity_match),
            "total": len(initial_results),
            "results": [asdict(result) for result in initial_results],
        },
        "after": {
            "found": sum(1 for result in final_results if result.found),
            "severity_matched": sum(1 for result in final_results if result.severity_match),
            "total": len(final_results),
            "repaired": repaired_labels,
            "results": [asdict(result) for result in final_results],
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and repair critical sentinel interactions in Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--sentinel-path", type=Path, default=DEFAULT_SENTINEL_PATH)
    parser.add_argument("--report-path", type=Path, default=DEFAULT_REPORT_PATH)
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD)
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE)
    parser.add_argument("--repair", action="store_true", help="Patch not-found or severity-mismatched sentinels.")
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO))

    sentinel_entries = _load_json(args.sentinel_path)
    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        driver.verify_connectivity()
        report = validate(driver, sentinel_entries, database=args.database, repair=args.repair)
    finally:
        driver.close()

    args.report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    LOGGER.info("Sentinel validation report written to %s", args.report_path)
    LOGGER.info(
        "Sentinel validation summary: before=%s/%s found, %s/%s severity matched; after=%s/%s found, %s/%s severity matched.",
        report["before"]["found"],
        report["before"]["total"],
        report["before"]["severity_matched"],
        report["before"]["total"],
        report["after"]["found"],
        report["after"]["total"],
        report["after"]["severity_matched"],
        report["after"]["total"],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
