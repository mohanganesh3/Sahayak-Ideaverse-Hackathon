"""Neo4j graph schema creation – constraints, indexes, and full node/relationship definitions.

Run directly to create the schema::

    python -m app.graph.schema

Schema overview
===============

Nodes
-----
(:Drug {rxcui, generic_name, canonical_name, synonyms, drug_class, atc_code, is_nti, is_beers,
        beers_category, beers_rationale, anticholinergic_score,
        max_daily_dose, geriatric_dose_adjust})
(:IndianBrand {brand_name, manufacturer, composition, dosage_form})
(:Herb {name, hindi_name, tamil_name, telugu_name, kannada_name, category})
(:Condition {name, icd10})
(:SideEffect {name, meddra_code, frequency})
(:Enzyme {name, notes})
(:Transporter {name, notes})
(:AdverseEffect {name})
(:ElectrolyteEffect {name})
(:PharmacokineticEffect {name})
(:Patient {session_id, name, age, weight_kg, gender, language})

Relationships
-------------
(:Drug)-[:INTERACTS_WITH {severity, mechanism, clinical_effect, management, evidence_level, source}]->(:Drug)
(:Herb)-[:INTERACTS_WITH_DRUG {severity, mechanism, clinical_effect, management, source}]->(:Drug)
(:IndianBrand)-[:CONTAINS]->(:Drug)
(:Drug)-[:INDICATED_FOR]->(:Condition)
(:Drug)-[:CONTRAINDICATED_IN {reason}]->(:Condition)
(:Drug)-[:MAY_CAUSE {frequency, source}]->(:SideEffect)
(:Drug)-[:COPRESCRIPTION_EFFECT {adverse_events, max_prr, source}]->(:Drug)
(:Drug)-[:IS_SUBSTRATE_OF {fraction, source}]->(:Enzyme)
(:Drug)-[:INHIBITS {strength, source}]->(:Enzyme)
(:Drug)-[:INDUCES {strength, source}]->(:Enzyme)
(:Drug)-[:IS_SUBSTRATE_OF {fraction, source}]->(:Transporter)
(:Drug)-[:INHIBITS {strength, source}]->(:Transporter)
(:Drug)-[:INDUCES {strength, source}]->(:Transporter)
(:Herb)-[:IS_SUBSTRATE_OF {fraction, source}]->(:Enzyme)
(:Herb)-[:INHIBITS {strength, source}]->(:Enzyme)
(:Herb)-[:INDUCES {strength, source}]->(:Enzyme)
(:Drug)-[:PROLONGS_QT {risk_category, source}]->(:AdverseEffect)
(:Drug)-[:DEPLETES {electrolyte, source}]->(:ElectrolyteEffect)
(:Drug)-[:ELEVATES {electrolyte, source}]->(:ElectrolyteEffect)
(:Drug)-[:SPARES {electrolyte, source}]->(:ElectrolyteEffect)
(:Drug)-[:SENSITIVE_TO {electrolyte, source}]->(:ElectrolyteEffect)
(:Drug)-[:CAUSES_CNS_DEPRESSION {source}]->(:AdverseEffect)
(:Herb)-[:AFFECTS_ABSORPTION {source, confidence, reference, note}]->(:PharmacokineticEffect)
(:Enzyme)-[:MAPS_TO {source, matched_alias}]->(:Gene)
(:Enzyme)-[:MAPS_TO {source, matched_alias}]->(:Protein)
(:Transporter)-[:MAPS_TO {source, matched_alias}]->(:Gene)
(:Transporter)-[:MAPS_TO {source, matched_alias}]->(:Protein)
(:USBrand {brand_name, ndc, labeler, dosage_form, product_type})-[:CONTAINS]->(:Drug)
(:Patient)-[:TAKES {dose, frequency, prescribed_by, source_type}]->(:Drug)
(:Patient)-[:TAKES_HERB {prescribed_by, source_type}]->(:Herb)
(:Patient)-[:HAS_CONDITION]->(:Condition)
"""

from __future__ import annotations

from neo4j import Driver

# ── Uniqueness constraints ───────────────────────────────────────────────────
_CONSTRAINTS: list[str] = [
    "CREATE CONSTRAINT drug_generic_name IF NOT EXISTS "
    "FOR (d:Drug) REQUIRE d.generic_name IS UNIQUE",

    "CREATE CONSTRAINT brand_name IF NOT EXISTS "
    "FOR (b:IndianBrand) REQUIRE b.brand_name IS UNIQUE",

    "CREATE CONSTRAINT herb_name IF NOT EXISTS "
    "FOR (h:Herb) REQUIRE h.name IS UNIQUE",

    "CREATE CONSTRAINT condition_name IF NOT EXISTS "
    "FOR (c:Condition) REQUIRE c.name IS UNIQUE",

    "CREATE CONSTRAINT enzyme_name IF NOT EXISTS "
    "FOR (e:Enzyme) REQUIRE e.name IS UNIQUE",

    "CREATE CONSTRAINT transporter_name IF NOT EXISTS "
    "FOR (t:Transporter) REQUIRE t.name IS UNIQUE",

    "CREATE CONSTRAINT adverse_effect_name IF NOT EXISTS "
    "FOR (ae:AdverseEffect) REQUIRE ae.name IS UNIQUE",

    "CREATE CONSTRAINT electrolyte_effect_name IF NOT EXISTS "
    "FOR (ee:ElectrolyteEffect) REQUIRE ee.name IS UNIQUE",

    "CREATE CONSTRAINT pharmacokinetic_effect_name IF NOT EXISTS "
    "FOR (pe:PharmacokineticEffect) REQUIRE pe.name IS UNIQUE",

    "CREATE CONSTRAINT usbrand_ndc IF NOT EXISTS "
    "FOR (b:USBrand) REQUIRE b.ndc IS UNIQUE",

    "CREATE CONSTRAINT patient_session IF NOT EXISTS "
    "FOR (p:Patient) REQUIRE p.session_id IS UNIQUE",
]

# ── Lookup indexes (non-unique) ──────────────────────────────────────────────
_INDEXES: list[str] = [
    # Drug
    "CREATE INDEX drug_rxcui_idx IF NOT EXISTS FOR (d:Drug) ON (d.rxcui)",
    "CREATE INDEX drug_class_idx IF NOT EXISTS FOR (d:Drug) ON (d.drug_class)",
    "CREATE INDEX drug_atc_idx IF NOT EXISTS FOR (d:Drug) ON (d.atc_code)",
    "CREATE INDEX drug_is_beers_idx IF NOT EXISTS FOR (d:Drug) ON (d.is_beers)",
    "CREATE INDEX drug_is_nti_idx IF NOT EXISTS FOR (d:Drug) ON (d.is_nti)",

    # IndianBrand
    "CREATE INDEX brand_manufacturer_idx IF NOT EXISTS FOR (b:IndianBrand) ON (b.manufacturer)",

    # Herb
    "CREATE INDEX herb_hindi_idx IF NOT EXISTS FOR (h:Herb) ON (h.hindi_name)",
    "CREATE INDEX herb_tamil_idx IF NOT EXISTS FOR (h:Herb) ON (h.tamil_name)",
    "CREATE INDEX herb_telugu_idx IF NOT EXISTS FOR (h:Herb) ON (h.telugu_name)",
    "CREATE INDEX herb_kannada_idx IF NOT EXISTS FOR (h:Herb) ON (h.kannada_name)",
    "CREATE INDEX herb_category_idx IF NOT EXISTS FOR (h:Herb) ON (h.category)",

    # Condition
    "CREATE INDEX condition_icd10_idx IF NOT EXISTS FOR (c:Condition) ON (c.icd10)",

    # SideEffect
    "CREATE INDEX side_effect_name_idx IF NOT EXISTS FOR (se:SideEffect) ON (se.name)",
    "CREATE INDEX side_effect_meddra_idx IF NOT EXISTS FOR (se:SideEffect) ON (se.meddra_code)",

    # Enzyme / Transporter / AdverseEffect / ElectrolyteEffect
    "CREATE INDEX enzyme_name_idx IF NOT EXISTS FOR (e:Enzyme) ON (e.name)",
    "CREATE INDEX transporter_name_idx IF NOT EXISTS FOR (t:Transporter) ON (t.name)",
    "CREATE INDEX adverse_effect_name_idx IF NOT EXISTS FOR (ae:AdverseEffect) ON (ae.name)",
    "CREATE INDEX electrolyte_effect_name_idx IF NOT EXISTS FOR (ee:ElectrolyteEffect) ON (ee.name)",
    "CREATE INDEX pharmacokinetic_effect_name_idx IF NOT EXISTS FOR (pe:PharmacokineticEffect) ON (pe.name)",

    # Patient
    "CREATE INDEX patient_age_idx IF NOT EXISTS FOR (p:Patient) ON (p.age)",
    "CREATE INDEX patient_language_idx IF NOT EXISTS FOR (p:Patient) ON (p.language)",
]

# ── Full-text indexes for fuzzy search ───────────────────────────────────────
_FULLTEXT_INDEXES: list[str] = [
    "CREATE FULLTEXT INDEX drug_name_fulltext IF NOT EXISTS "
    "FOR (d:Drug) ON EACH [d.generic_name]",

    "CREATE FULLTEXT INDEX drug_synonym_fulltext IF NOT EXISTS "
    "FOR (d:Drug) ON EACH [d.generic_name, d.synonyms]",

    "CREATE FULLTEXT INDEX brand_name_fulltext IF NOT EXISTS "
    "FOR (b:IndianBrand) ON EACH [b.brand_name]",

    "CREATE FULLTEXT INDEX usbrand_name_fulltext IF NOT EXISTS "
    "FOR (b:USBrand) ON EACH [b.brand_name]",
]

# ── Relationship property indexes ────────────────────────────────────────────
_REL_INDEXES: list[str] = [
    "CREATE INDEX interacts_severity_idx IF NOT EXISTS "
    "FOR ()-[r:INTERACTS_WITH]-() ON (r.severity)",

    "CREATE INDEX herb_interacts_severity_idx IF NOT EXISTS "
    "FOR ()-[r:INTERACTS_WITH_DRUG]-() ON (r.severity)",
]

# ── Combined list ────────────────────────────────────────────────────────────
_ALL_STATEMENTS: list[str] = _CONSTRAINTS + _INDEXES + _FULLTEXT_INDEXES + _REL_INDEXES


def create_schema(driver: Driver) -> None:
    """Execute all schema statements (constraints + indexes).

    Args:
        driver: Active Neo4j driver.
    """
    with driver.session() as session:
        for stmt in _ALL_STATEMENTS:
            session.run(stmt)


def drop_all_data(driver: Driver) -> None:
    """Delete every node and relationship in the database.

    Args:
        driver: Active Neo4j driver.
    """
    with driver.session() as session:
        session.run("MATCH (n) DETACH DELETE n")


def drop_constraints_and_indexes(driver: Driver) -> None:
    """Drop all user-created constraints and indexes.

    Args:
        driver: Active Neo4j driver.
    """
    with driver.session() as session:
        result = session.run("SHOW CONSTRAINTS YIELD name RETURN name")
        for record in result:
            session.run(f"DROP CONSTRAINT {record['name']} IF EXISTS")

        result = session.run(
            "SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP' RETURN name"
        )
        for record in result:
            session.run(f"DROP INDEX {record['name']} IF EXISTS")


if __name__ == "__main__":
    import sys
    from pathlib import Path

    # Ensure project root is on sys.path so `app.*` imports resolve.
    _project_root = str(Path(__file__).resolve().parent.parent.parent)
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)

    from app.graph.neo4j_connection import get_driver, close_driver

    print("Connecting to Neo4j...")
    driver = get_driver()
    driver.verify_connectivity()
    print("Connected.")

    print("Creating schema (constraints + indexes)...")
    create_schema(driver)

    print("✅ Schema created successfully")

    close_driver()
