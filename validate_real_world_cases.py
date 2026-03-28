"""Run real-world validation scenarios against the live SAHAYAK Neo4j graph."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from neo4j import GraphDatabase

PROJECT_ROOT = Path(__file__).resolve().parent
SIDER_DATA_DIR = PROJECT_ROOT.parent / "sahayak-data" / "sider"
SIDER_FREQUENCY_FILES = ("meddra_freq.tsv", "meddra_freq.tsv.gz")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.graph.query_engine import search_indian_brand
from app.services.drug_normalizer import brand_to_generic

URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")


@dataclass(slots=True)
class TestResult:
    category: str
    name: str
    severity: str
    passed: bool
    details: str


def run_query(driver, query: str, **params):
    with driver.session(database=DATABASE) as session:
        return [record.data() for record in session.run(query, **params)]


def add_result(results: list[TestResult], category: str, name: str, severity: str, passed: bool, details: str) -> None:
    results.append(
        TestResult(
            category=category,
            name=name,
            severity=severity,
            passed=passed,
            details=details,
        )
    )


def main() -> int:
    driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
    results: list[TestResult] = []

    try:
        driver.verify_connectivity()

        orphan_rows = run_query(driver, "MATCH (d:Drug) WHERE NOT (d)--() RETURN count(d) AS count")
        orphan_count = orphan_rows[0]["count"]
        add_result(
            results,
            "graph",
            "No orphan Drug nodes",
            "high",
            orphan_count == 0,
            f"orphan_drugs={orphan_count}",
        )

        index_rows = run_query(
            driver,
            "SHOW INDEXES YIELD name WHERE name IN ['brand_name_fulltext', 'drug_synonym_fulltext'] RETURN collect(name) AS names",
        )
        index_names = set(index_rows[0]["names"])
        add_result(
            results,
            "graph",
            "Required full-text indexes exist",
            "high",
            index_names == {"brand_name_fulltext", "drug_synonym_fulltext"},
            f"indexes={sorted(index_names)}",
        )

        canonical_rows = run_query(
            driver,
            """
            MATCH (d:Drug) WHERE d.rxcui = '1191'
            RETURN count(d) AS count,
                   collect(d.generic_name) AS names,
                   head(collect(d.synonyms)) AS synonyms
            """,
        )
        canonical = canonical_rows[0]
        canonical_ok = canonical["count"] == 1 and canonical["names"] == ["Aspirin"] and "Acetylsalicylic acid" in (canonical["synonyms"] or [])
        add_result(
            results,
            "canonicalization",
            "Aspirin and Acetylsalicylic acid merged",
            "critical",
            canonical_ok,
            f"count={canonical['count']} names={canonical['names']} synonyms_sample={(canonical['synonyms'] or [])[:8]}",
        )

        brand_expectations = {
            "Ecosprin 75": "aspirin",
            "Thyronorm 50": "levothyroxine",
            "Dolo 650": "paracetamol",
            "Crocin": "paracetamol",
            "PAN40": "pantoprazole",
            "Combiflam": "ibuprofen + paracetamol",
            "Augmentin 625 Duo": "amoxycillin + clavulanic acid",
        }
        for brand_name, expected_generic in brand_expectations.items():
            resolved = brand_to_generic(brand_name)
            add_result(
                results,
                "brand-normalization",
                f"brand_to_generic({brand_name})",
                "high",
                resolved == expected_generic,
                f"resolved={resolved!r} expected={expected_generic!r}",
            )

        fuzzy_expectations = {
            "650 Dolo": "Acetaminophen",
            "dolo650": "Acetaminophen",
            "PAN40": "Pantoprazole",
            "Thyronorm50mcg": "Levothyroxine",
            "UrimaxD": "Tamsulosin",
            "Aciloc D 10 150": "Ranitidine",
        }
        for query_text, expected_drug in fuzzy_expectations.items():
            matches = search_indian_brand(query_text, limit=1)
            top_match = matches[0] if matches else None
            contained = set(top_match["contained_drugs"]) if top_match else set()
            add_result(
                results,
                "brand-search",
                f"search_indian_brand({query_text}) top-1 contains {expected_drug}",
                "high",
                expected_drug in contained,
                f"top_match={top_match}",
            )

        warfarin_rows = run_query(
            driver,
            """
            MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
            WHERE toLower(a.generic_name) = 'warfarin'
              AND ANY(s IN coalesce(b.synonyms, []) WHERE toLower(s) = 'aspirin')
            RETURN a.generic_name AS drug_a, b.generic_name AS drug_b, r.severity AS severity, r.source AS source
            LIMIT 5
            """,
        )
        add_result(
            results,
            "ddi",
            "Warfarin-Aspirin interaction exists after synonym merge",
            "critical",
            bool(warfarin_rows),
            f"rows={warfarin_rows}",
        )
        warfarin_quality_ok = any(row["severity"] in {"major", "moderate"} for row in warfarin_rows)
        add_result(
            results,
            "ddi",
            "Warfarin-Aspirin interaction has clinically useful severity",
            "critical",
            warfarin_quality_ok,
            f"rows={warfarin_rows}",
        )
        warfarin_ddinter_rows = run_query(
            driver,
            """
            MATCH (a:Drug)-[r:INTERACTS_WITH {source:'ddinter'}]-(b:Drug)
            WHERE toLower(a.generic_name) = 'warfarin'
              AND ANY(s IN coalesce(b.synonyms, []) WHERE toLower(s) = 'aspirin')
            RETURN a.generic_name AS drug_a, b.generic_name AS drug_b, r.severity AS severity
            LIMIT 5
            """,
        )
        add_result(
            results,
            "ddi",
            "Warfarin-Aspirin DDInter edge restored after canonicalization",
            "critical",
            any(row["severity"] == "major" for row in warfarin_ddinter_rows),
            f"rows={warfarin_ddinter_rows}",
        )

        levothyroxine_rows = run_query(
            driver,
            """
            MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
            WHERE toLower(a.generic_name) = 'levothyroxine'
              AND toLower(b.generic_name) CONTAINS 'ferrous fumarate'
            RETURN a.generic_name AS drug_a, b.generic_name AS drug_b, r.severity AS severity, r.source AS source
            LIMIT 10
            """,
        )
        add_result(
            results,
            "ddi",
            "Levothyroxine-Ferrous Fumarate interaction exists",
            "high",
            any(row["source"] == "ddinter" and row["severity"] == "moderate" for row in levothyroxine_rows),
            f"rows={levothyroxine_rows}",
        )

        tramadol_rows = run_query(
            driver,
            """
            MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
            WHERE toLower(a.generic_name) = 'tramadol'
              AND toLower(b.generic_name) = 'paroxetine'
            RETURN a.generic_name AS drug_a, b.generic_name AS drug_b, r.severity AS severity, r.source AS source
            LIMIT 10
            """,
        )
        add_result(
            results,
            "ddi",
            "Tramadol-Paroxetine interaction exists",
            "high",
            bool(tramadol_rows),
            f"rows={tramadol_rows}",
        )

        ramipril_rows = run_query(
            driver,
            """
            MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
            WHERE toLower(a.generic_name) = 'ramipril'
              AND toLower(b.generic_name) = 'spironolactone'
            RETURN a.generic_name AS drug_a, b.generic_name AS drug_b, r.severity AS severity, r.source AS source
            LIMIT 10
            """,
        )
        add_result(
            results,
            "ddi",
            "Ramipril-Spironolactone interaction exists",
            "high",
            bool(ramipril_rows),
            f"rows={ramipril_rows}",
        )

        herb_queries = {
            "Arjuna with anticoagulants": """
                MATCH (h:Herb)-[r:INTERACTS_WITH_DRUG]->(d:Drug)
                WHERE toLower(h.name) = 'arjuna'
                  AND toLower(d.generic_name) IN ['aspirin','warfarin','apixaban']
                RETURN h.name AS herb, d.generic_name AS drug, r.severity AS severity
                ORDER BY drug
            """,
            "Garlic with Warfarin": """
                MATCH (h:Herb)-[r:INTERACTS_WITH_DRUG]->(d:Drug)
                WHERE toLower(h.name) = 'garlic'
                  AND toLower(d.generic_name) = 'warfarin'
                RETURN h.name AS herb, d.generic_name AS drug, r.severity AS severity
            """,
            "Fenugreek with Metformin": """
                MATCH (h:Herb)-[r:INTERACTS_WITH_DRUG]->(d:Drug)
                WHERE toLower(h.name) = 'fenugreek'
                  AND toLower(d.generic_name) = 'metformin'
                RETURN h.name AS herb, d.generic_name AS drug, r.severity AS severity
            """,
            "Ashwagandha with Levothyroxine": """
                MATCH (h:Herb)-[r:INTERACTS_WITH_DRUG]->(d:Drug)
                WHERE toLower(h.name) = 'ashwagandha'
                  AND toLower(d.generic_name) = 'levothyroxine'
                RETURN h.name AS herb, d.generic_name AS drug, r.severity AS severity
            """,
        }
        for name, query in herb_queries.items():
            rows = run_query(driver, query)
            add_result(
                results,
                "herb-drug",
                name,
                "high",
                bool(rows),
                f"rows={rows}",
            )

        beers_rows = run_query(
            driver,
            """
            UNWIND ['diphenhydramine','hydroxyzine','amitriptyline','diazepam','glimepiride'] AS name
            MATCH (d:Drug) WHERE toLower(d.generic_name) = name
            RETURN name, d.is_beers AS is_beers, d.beers_category AS category
            """,
        )
        beers_ok = all(row["is_beers"] is True for row in beers_rows) and len(beers_rows) == 5
        add_result(
            results,
            "beers",
            "Core Beers sentinel drugs flagged",
            "critical",
            beers_ok,
            f"rows={beers_rows}",
        )

        methyldopa_rows = run_query(
            driver,
            "MATCH (d:Drug) WHERE toLower(d.generic_name) = 'methyldopa' RETURN d.generic_name AS drug, d.is_beers AS is_beers, d.beers_category AS category",
        )
        methyldopa_legacy_handled = bool(methyldopa_rows) and methyldopa_rows[0]["is_beers"] is True and "legacy" in str(methyldopa_rows[0]["category"] or "").lower()
        add_result(
            results,
            "beers",
            "Methyldopa handled explicitly as a legacy Beers risk",
            "medium",
            methyldopa_legacy_handled,
            f"rows={methyldopa_rows}",
        )

        metformin_connectivity = run_query(
            driver,
            """
            MATCH (d:Drug) WHERE toLower(d.generic_name) = 'metformin'
            CALL { WITH d OPTIONAL MATCH (d)-[ddi:INTERACTS_WITH]-(:Drug) RETURN count(DISTINCT ddi) AS drug_interactions }
            CALL { WITH d OPTIONAL MATCH (d)-[mc:MAY_CAUSE]->(:SideEffect) RETURN count(DISTINCT mc) AS side_effects }
            CALL { WITH d OPTIONAL MATCH (d)<-[:CONTAINS]-(ib:IndianBrand) RETURN count(DISTINCT ib) AS indian_brands }
            CALL { WITH d OPTIONAL MATCH (d)-[ci:CONTRAINDICATED_IN]->(:Condition) RETURN count(DISTINCT ci) AS contraindications }
            CALL { WITH d OPTIONAL MATCH (:Herb)-[hdi:INTERACTS_WITH_DRUG]->(d) RETURN count(DISTINCT hdi) AS herb_interactions }
            RETURN drug_interactions, side_effects, indian_brands, contraindications, herb_interactions
            """,
        )[0]
        connectivity_ok = (
            metformin_connectivity["drug_interactions"] > 1000
            and metformin_connectivity["side_effects"] > 100
            and metformin_connectivity["indian_brands"] > 1000
            and metformin_connectivity["contraindications"] > 10
            and metformin_connectivity["herb_interactions"] > 10
        )
        add_result(
            results,
            "integration",
            "Metformin cross-source connectivity is strong",
            "high",
            connectivity_ok,
            f"counts={metformin_connectivity}",
        )

        twosides_rows = run_query(
            driver,
            """
            MATCH (a:Drug)-[r:COPRESCRIPTION_EFFECT]->(b:Drug)
            WHERE toLower(a.generic_name) = 'aspirin'
            RETURN b.generic_name AS drug, r.adverse_events[0..3] AS adverse_events, r.num_events AS num_events
            LIMIT 5
            """,
        )
        twosides_ok = all(row["adverse_events"] and row["num_events"] for row in twosides_rows) and bool(twosides_rows)
        add_result(
            results,
            "side-effects",
            "TwoSIDES coprescription effects carry event payloads",
            "high",
            twosides_ok,
            f"rows={twosides_rows}",
        )

        frequency_rows = run_query(
            driver,
            """
            MATCH (d:Drug)-[r:MAY_CAUSE]->(:SideEffect)
            RETURN r.source AS source,
                   count(CASE
                     WHEN r.frequency IS NOT NULL
                      AND trim(toString(r.frequency)) <> ''
                      AND toLower(toString(r.frequency)) <> 'unknown'
                     THEN 1
                   END) AS informative_frequency
            ORDER BY source
            """,
        )
        informative_by_source = {row["source"]: row["informative_frequency"] for row in frequency_rows}
        onsides_confidence_rows = run_query(
            driver,
            """
            MATCH (:Drug)-[r:MAY_CAUSE {source:'onsides'}]->(:SideEffect)
            WHERE r.confidence IS NOT NULL AND trim(toString(r.confidence)) <> ''
            RETURN count(r) AS count
            """,
        )
        onsides_confidence_count = onsides_confidence_rows[0]["count"]
        sider_frequency_available = any((SIDER_DATA_DIR / file_name).exists() for file_name in SIDER_FREQUENCY_FILES)
        if sider_frequency_available:
            frequency_ok = informative_by_source.get("sider", 0) > 0
            frequency_details = (
                f"sider_frequency_available={sider_frequency_available} "
                f"informative_frequency={informative_by_source} onsides_confidence={onsides_confidence_count}"
            )
            frequency_name = "SIDER contributes informative frequency values when frequency file is present"
        else:
            frequency_ok = onsides_confidence_count > 0
            frequency_details = (
                f"sider_frequency_available={sider_frequency_available} "
                f"informative_frequency={informative_by_source} onsides_confidence={onsides_confidence_count}"
            )
            frequency_name = "OnSIDES contributes confidence values and the graph does not invent missing SIDER frequencies"
        add_result(
            results,
            "side-effects",
            frequency_name,
            "high",
            frequency_ok,
            frequency_details,
        )

        ndc_rows = run_query(
            driver,
            "MATCH (d:Drug) WHERE d.ndc_code IS NOT NULL AND trim(d.ndc_code) <> '' RETURN count(d) AS count",
        )
        ndc_count = ndc_rows[0]["count"]
        add_result(
            results,
            "brands",
            "FDA NDC enrichment present",
            "medium",
            ndc_count > 0,
            f"fda_enriched={ndc_count}",
        )

    finally:
        driver.close()

    passed = [result for result in results if result.passed]
    failed = [result for result in results if not result.passed]

    print("\n=== REAL-WORLD VALIDATION RESULTS ===")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"[{status}] [{result.severity.upper()}] {result.category} :: {result.name}")
        print(f"    {result.details}")

    print("\n=== SUMMARY ===")
    print(f"total={len(results)} passed={len(passed)} failed={len(failed)}")
    if failed:
        print("failed_checks=")
        for result in failed:
            print(f"  - [{result.severity.upper()}] {result.category} :: {result.name}")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
