"""Repair PrimeKG DDI severities by borrowing stronger evidence from the graph first."""

from __future__ import annotations

import argparse
import json
import logging
import os
from typing import Any

from neo4j import Driver, GraphDatabase

from app.graph.fix_unknown_severities import (
    ANTICOAGULANT_CLASS_KEYWORDS,
    ANTICOAGULANT_NAMES,
    ANTIDIABETIC_CLASS_KEYWORDS,
    ANTIDIABETIC_NAMES,
    ANTIHYPERTENSIVE_CLASS_KEYWORDS,
    ANTIHYPERTENSIVE_NAMES,
    CNS_DEPRESSANT_CLASS_KEYWORDS,
    CNS_DEPRESSANT_NAMES,
    GASTRIC_PH_SENSITIVE_NAMES,
    MAJOR_EVENT_KEYWORDS,
    MODERATE_EVENT_KEYWORDS,
    NTI_NAMES,
    PPI_CLASS_KEYWORDS,
    PPI_NAMES,
)

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 20_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

FETCH_UNKNOWN_COUNT_QUERY = """
MATCH ()-[r:INTERACTS_WITH {source:'primekg'}]->()
WHERE r.severity = 'unknown'
RETURN count(r) AS count
"""

VERIFY_QUERY = """
MATCH ()-[r:INTERACTS_WITH {source:'primekg'}]->()
RETURN r.severity AS severity, count(r) AS count
ORDER BY count DESC, severity
"""

VERIFY_ALL_QUERY = """
MATCH ()-[r:INTERACTS_WITH]->()
RETURN r.severity AS severity, count(r) AS count
ORDER BY count DESC, severity
"""

INFERRED_METHODS_QUERY = """
MATCH ()-[r:INTERACTS_WITH {source:'primekg'}]->()
WHERE r.severity_source IS NOT NULL
RETURN r.severity_source AS source, count(r) AS count
ORDER BY count DESC, source
"""

MATCH_DDINTER_QUERY = """
MATCH (a:Drug)-[p:INTERACTS_WITH {source:'primekg', severity:'unknown'}]->(b:Drug)
MATCH (a)-[d:INTERACTS_WITH {source:'ddinter'}]-(b)
WITH p, d
LIMIT $limit
SET p.original_severity = coalesce(p.original_severity, 'unknown'),
    p.severity = d.severity,
    p.severity_source = 'matched_ddinter',
    p.severity_inference_method = 'matched_ddinter',
    p.severity_inference_evidence = [
      'ddinter:' + coalesce(d.severity, 'unknown'),
      'ddinter_source:' + coalesce(d.severity_source, 'original')
    ],
    p.severity_confidence = 'high',
    p.severity_review_needed = false
RETURN count(p) AS updated
"""

MATCH_BEERS_QUERY = """
MATCH (a:Drug)-[p:INTERACTS_WITH {source:'primekg', severity:'unknown'}]->(b:Drug)
WHERE coalesce(p.beers_flagged, false) = true
   OR EXISTS { MATCH (a)-[:INTERACTS_WITH {source:'beers_2023'}]-(b) }
WITH p
LIMIT $limit
SET p.original_severity = coalesce(p.original_severity, 'unknown'),
    p.severity = 'major',
    p.severity_source = 'matched_beers_2023',
    p.severity_inference_method = 'matched_beers_2023',
    p.severity_inference_evidence = [coalesce(p.beers_risk, 'beers_flagged_pair')],
    p.severity_confidence = 'high',
    p.severity_review_needed = false
RETURN count(p) AS updated
"""

MATCH_TWOSIDES_QUERY = """
MATCH (a:Drug)-[p:INTERACTS_WITH {source:'primekg', severity:'unknown'}]->(b:Drug)
MATCH (a)-[c:COPRESCRIPTION_EFFECT]-(b)
WITH p, coalesce(c.adverse_events, []) AS adverse_events
LIMIT $limit
WITH p,
     adverse_events,
     [event IN adverse_events WHERE ANY(keyword IN $major_keywords WHERE toLower(event) CONTAINS keyword)][0..5] AS major_hits,
     [event IN adverse_events WHERE ANY(keyword IN $moderate_keywords WHERE toLower(event) CONTAINS keyword)][0..5] AS moderate_hits
SET p.original_severity = coalesce(p.original_severity, 'unknown'),
    p.severity = CASE
      WHEN size(major_hits) > 0 THEN 'major'
      WHEN size(moderate_hits) > 0 THEN 'moderate'
      ELSE 'minor'
    END,
    p.severity_source = 'twosides_inferred',
    p.severity_inference_method = 'twosides',
    p.severity_inference_evidence = CASE
      WHEN size(major_hits) > 0 THEN major_hits
      WHEN size(moderate_hits) > 0 THEN moderate_hits
      ELSE adverse_events[0..5]
    END,
    p.severity_confidence = CASE
      WHEN size(major_hits) > 0 OR size(moderate_hits) > 0 THEN 'medium'
      ELSE 'low'
    END,
    p.severity_review_needed = CASE
      WHEN size(major_hits) > 0 OR size(moderate_hits) > 0 THEN false
      ELSE true
    END
RETURN count(p) AS updated
"""

MATCH_RULES_QUERY = """
MATCH (a:Drug)-[p:INTERACTS_WITH {source:'primekg', severity:'unknown'}]->(b:Drug)
WITH p, a, b,
     toLower(coalesce(a.generic_name, '')) AS a_name,
     toLower(coalesce(b.generic_name, '')) AS b_name,
     toLower(coalesce(a.drug_class, '')) AS a_class,
     toLower(coalesce(b.drug_class, '')) AS b_class,
     coalesce(a.is_nti, false) AS a_nti,
     coalesce(b.is_nti, false) AS b_nti
WITH p,
     (a_nti OR b_nti OR a_name IN $nti_names OR b_name IN $nti_names) AS nti_pair,
     (a_name IN $cns_names OR ANY(keyword IN $cns_keywords WHERE a_class CONTAINS keyword)) AS a_cns,
     (b_name IN $cns_names OR ANY(keyword IN $cns_keywords WHERE b_class CONTAINS keyword)) AS b_cns,
     (a_name IN $antithrombotic_names OR ANY(keyword IN $antithrombotic_keywords WHERE a_class CONTAINS keyword)) AS a_antithrombotic,
     (b_name IN $antithrombotic_names OR ANY(keyword IN $antithrombotic_keywords WHERE b_class CONTAINS keyword)) AS b_antithrombotic,
     (a_name IN $antihypertensive_names OR ANY(keyword IN $antihypertensive_keywords WHERE a_class CONTAINS keyword)) AS a_antihypertensive,
     (b_name IN $antihypertensive_names OR ANY(keyword IN $antihypertensive_keywords WHERE b_class CONTAINS keyword)) AS b_antihypertensive,
     (a_name IN $antidiabetic_names OR ANY(keyword IN $antidiabetic_keywords WHERE a_class CONTAINS keyword)) AS a_antidiabetic,
     (b_name IN $antidiabetic_names OR ANY(keyword IN $antidiabetic_keywords WHERE b_class CONTAINS keyword)) AS b_antidiabetic,
     (a_name IN $ppi_names OR ANY(keyword IN $ppi_keywords WHERE a_class CONTAINS keyword)) AS a_ppi,
     (b_name IN $ppi_names OR ANY(keyword IN $ppi_keywords WHERE b_class CONTAINS keyword)) AS b_ppi,
     a_name IN $gastric_sensitive_names AS a_gastric_sensitive,
     b_name IN $gastric_sensitive_names AS b_gastric_sensitive
WITH p,
     CASE
       WHEN nti_pair THEN 'major'
       WHEN a_cns AND b_cns THEN 'major'
       WHEN a_antithrombotic AND b_antithrombotic THEN 'major'
       WHEN a_antihypertensive AND b_antihypertensive THEN 'moderate'
       WHEN a_antidiabetic AND b_antidiabetic THEN 'moderate'
       WHEN (a_ppi AND b_gastric_sensitive) OR (b_ppi AND a_gastric_sensitive) THEN 'minor'
       ELSE NULL
     END AS inferred_severity,
     CASE
       WHEN nti_pair THEN ['narrow_therapeutic_index']
       WHEN a_cns AND b_cns THEN ['dual_cns_depressants']
       WHEN a_antithrombotic AND b_antithrombotic THEN ['dual_anticoagulant_or_antiplatelet']
       WHEN a_antihypertensive AND b_antihypertensive THEN ['dual_antihypertensives']
       WHEN a_antidiabetic AND b_antidiabetic THEN ['dual_antidiabetics']
       WHEN (a_ppi AND b_gastric_sensitive) OR (b_ppi AND a_gastric_sensitive) THEN ['ppi_with_gastric_ph_sensitive_drug']
       ELSE []
     END AS evidence
WHERE inferred_severity IS NOT NULL
WITH p, inferred_severity, evidence
LIMIT $limit
SET p.original_severity = coalesce(p.original_severity, 'unknown'),
    p.severity = inferred_severity,
    p.severity_source = 'pharmacology_rule',
    p.severity_inference_method = 'pharmacology_rule',
    p.severity_inference_evidence = evidence,
    p.severity_confidence = 'low',
    p.severity_review_needed = true
RETURN count(p) AS updated
"""

DEFAULT_QUERY = """
MATCH ()-[p:INTERACTS_WITH {source:'primekg', severity:'unknown'}]->()
WITH p
LIMIT $limit
SET p.original_severity = coalesce(p.original_severity, 'unknown'),
    p.severity = 'minor',
    p.severity_source = 'primekg_low_confidence_default',
    p.severity_inference_method = 'default_low_confidence',
    p.severity_inference_evidence = ['primekg_synergistic_interaction_without_stronger_support'],
    p.severity_confidence = 'low',
    p.severity_review_needed = true
RETURN count(p) AS updated
"""


def _run_count_query(driver: Driver, database: str, query: str) -> int:
    with driver.session(database=database) as session:
        record = session.run(query).single()
    return int(record["count"])


def _run_update_batch(driver: Driver, database: str, query: str, **params: Any) -> int:
    with driver.session(database=database) as session:
        record = session.execute_write(lambda tx: tx.run(query, **params).single())
    return int(record["updated"])


def _run_strategy(
    driver: Driver,
    database: str,
    *,
    name: str,
    query: str,
    batch_size: int,
    params: dict[str, Any] | None = None,
) -> int:
    total = 0
    payload = dict(params or {})
    payload["limit"] = batch_size
    while True:
        updated = _run_update_batch(driver, database, query, **payload)
        if updated == 0:
            break
        total += updated
        LOGGER.info("%s updated %s PrimeKG edges so far.", name, f"{total:,}")
    return total


def _verify(driver: Driver, database: str) -> dict[str, Any]:
    with driver.session(database=database) as session:
        primekg_distribution = [record.data() for record in session.run(VERIFY_QUERY)]
        all_distribution = [record.data() for record in session.run(VERIFY_ALL_QUERY)]
        inferred_sources = [record.data() for record in session.run(INFERRED_METHODS_QUERY)]
    return {
        "primekg_distribution": primekg_distribution,
        "all_distribution": all_distribution,
        "inferred_sources": inferred_sources,
    }


def repair_primekg_unknown_severities(
    driver: Driver,
    *,
    database: str,
    batch_size: int,
) -> dict[str, Any]:
    before = _run_count_query(driver, database, FETCH_UNKNOWN_COUNT_QUERY)
    LOGGER.info("PrimeKG unknown severities before repair: %s", f"{before:,}")

    strategy_counts = {
        "matched_ddinter": _run_strategy(
            driver,
            database,
            name="matched_ddinter",
            query=MATCH_DDINTER_QUERY,
            batch_size=batch_size,
        ),
        "matched_beers_2023": _run_strategy(
            driver,
            database,
            name="matched_beers_2023",
            query=MATCH_BEERS_QUERY,
            batch_size=batch_size,
        ),
        "twosides_inferred": _run_strategy(
            driver,
            database,
            name="twosides_inferred",
            query=MATCH_TWOSIDES_QUERY,
            batch_size=batch_size,
            params={
                "major_keywords": [keyword.lower() for keyword in MAJOR_EVENT_KEYWORDS],
                "moderate_keywords": [keyword.lower() for keyword in MODERATE_EVENT_KEYWORDS],
            },
        ),
        "pharmacology_rule": _run_strategy(
            driver,
            database,
            name="pharmacology_rule",
            query=MATCH_RULES_QUERY,
            batch_size=batch_size,
            params={
                "nti_names": sorted(name.lower() for name in NTI_NAMES),
                "cns_names": sorted(name.lower() for name in CNS_DEPRESSANT_NAMES),
                "cns_keywords": [keyword.lower() for keyword in CNS_DEPRESSANT_CLASS_KEYWORDS],
                "antithrombotic_names": sorted(name.lower() for name in ANTICOAGULANT_NAMES),
                "antithrombotic_keywords": [keyword.lower() for keyword in ANTICOAGULANT_CLASS_KEYWORDS],
                "antihypertensive_names": sorted(name.lower() for name in ANTIHYPERTENSIVE_NAMES),
                "antihypertensive_keywords": [keyword.lower() for keyword in ANTIHYPERTENSIVE_CLASS_KEYWORDS],
                "antidiabetic_names": sorted(name.lower() for name in ANTIDIABETIC_NAMES),
                "antidiabetic_keywords": [keyword.lower() for keyword in ANTIDIABETIC_CLASS_KEYWORDS],
                "ppi_names": sorted(name.lower() for name in PPI_NAMES),
                "ppi_keywords": [keyword.lower() for keyword in PPI_CLASS_KEYWORDS],
                "gastric_sensitive_names": sorted(name.lower() for name in GASTRIC_PH_SENSITIVE_NAMES),
            },
        ),
        "default_low_confidence": _run_strategy(
            driver,
            database,
            name="default_low_confidence",
            query=DEFAULT_QUERY,
            batch_size=batch_size,
        ),
    }

    after = _run_count_query(driver, database, FETCH_UNKNOWN_COUNT_QUERY)
    verification = _verify(driver, database)
    return {
        "primekg_unknown_before": before,
        "primekg_unknown_after": after,
        "strategy_counts": strategy_counts,
        "verification": verification,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair PrimeKG drug-drug interaction severities with source-aware inference.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password.")
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Per-update batch size.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
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
        result = repair_primekg_unknown_severities(
            driver,
            database=args.database,
            batch_size=args.batch_size,
        )
        LOGGER.info("PrimeKG severity repair complete: %s", json.dumps(result, ensure_ascii=False))
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
