"""Ingest curated Ayurvedic herb metadata and interactions into Neo4j."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import string
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase

LOGGER = logging.getLogger(__name__)

DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "ayurvedic_herbs.json"

READ_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

DRUG_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT drug_generic_name IF NOT EXISTS "
    "FOR (d:Drug) REQUIRE d.generic_name IS UNIQUE"
)
HERB_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT herb_name IF NOT EXISTS "
    "FOR (h:Herb) REQUIRE h.name IS UNIQUE"
)

DRUG_DEFAULTS_CYPHER = (
    "drug.rxcui = '', "
    "drug.drug_class = '', "
    "drug.is_nti = false, "
    "drug.is_beers = false, "
    "drug.anticholinergic_score = 0"
)

GENERIC_NAME_OVERRIDES = {
    "paracetamol": "Acetaminophen",
    "amoxycillin": "Amoxicillin",
    "thyroxine": "Levothyroxine",
    "salbutamol": "Albuterol",
    "levosalbutamol": "Levalbuterol",
    "lignocaine": "Lidocaine",
    "glyceryl trinitrate": "Nitroglycerin",
    "adrenaline": "Epinephrine",
    "noradrenaline": "Norepinephrine",
}

HERB_TERM_CLASS_MAP: dict[str, tuple[str, ...]] = {
    "anticoagulants and antiplatelets": (
        "anticoagulant",
        "doac",
        "vitamin k antagonist",
        "antiplatelet",
        "dual antiplatelet",
    ),
    "antidiabetics": (
        "biguanide",
        "antidiabetic",
        "antidiabetic combination",
        "dpp-4 inhibitor",
        "dpp-4 inhibitor + metformin",
        "sglt2 inhibitor",
        "sulfonylurea",
    ),
    "antidiabetics and antihypertensives": (
        "biguanide",
        "antidiabetic",
        "antidiabetic combination",
        "dpp-4 inhibitor",
        "dpp-4 inhibitor + metformin",
        "sglt2 inhibitor",
        "sulfonylurea",
        "ace inhibitor",
        "arb",
        "beta blocker",
        "ccb",
        "diuretic",
        "loop diuretic",
    ),
    "antihypertensives": (
        "ace inhibitor",
        "arb",
        "beta blocker",
        "ccb",
        "diuretic",
        "loop diuretic",
    ),
    "antiplatelets": ("antiplatelet", "dual antiplatelet"),
    "corticosteroids": ("corticosteroid", "inhaled corticosteroid", "ics/laba"),
    "diuretics": ("diuretic", "loop diuretic", "arb + diuretic"),
    "diuretics or antihypertensives": (
        "diuretic",
        "loop diuretic",
        "arb + diuretic",
        "ace inhibitor",
        "arb",
        "beta blocker",
        "ccb",
    ),
    "hydrochlorothiazide and other diuretics": ("diuretic", "loop diuretic", "arb + diuretic"),
    "immunosuppressants": ("immunosuppressant",),
    "other anticoagulants or antiplatelets": (
        "anticoagulant",
        "doac",
        "vitamin k antagonist",
        "antiplatelet",
        "dual antiplatelet",
    ),
    "sedatives and other cns depressants": ("anxiolytic", "benzodiazepine", "opioid"),
    "thyroid hormone": ("thyroid",),
    "vitamin k antagonists and doacs": ("anticoagulant", "doac", "vitamin k antagonist"),
    "warfarin and antiplatelets": ("vitamin k antagonist", "antiplatelet", "dual antiplatelet"),
}

EXPLICIT_TERM_DRUGS: dict[str, tuple[str, ...]] = {
    "digoxin and other cardiac glycosides": ("Digoxin",),
    "digoxin, lithium, and other oral drugs": ("Digoxin", "Lithium"),
    "fexofenadine and domperidone": ("Fexofenadine", "Domperidone"),
    "other narrow therapeutic index cyp/p-gp substrates": (
        "Digoxin",
        "Tacrolimus",
        "Theophylline",
        "Lithium",
    ),
    "thyroid, sedative, or immunosuppressive medicines": ("Levothyroxine", "Tacrolimus"),
}


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


def _smart_title(value: str) -> str:
    parts = []
    for token in value.split():
        if token.isupper():
            parts.append(token)
            continue
        parts.append(string.capwords(token, sep="-"))
    return " ".join(parts)


def _preferred_drug_name(value: str) -> str:
    normalized = _normalize_lookup_key(value)
    if normalized in GENERIC_NAME_OVERRIDES:
        return GENERIC_NAME_OVERRIDES[normalized]
    cleaned = _clean_text(value)
    if cleaned is None:
        raise ValueError("drug value is required")
    return _smart_title(cleaned)


def _normalize_severity(value: str | None) -> str:
    severity = (_clean_text(value) or "moderate").casefold()
    if severity in {"high", "severe", "major"}:
        return "major"
    if severity in {"minor", "low"}:
        return "minor"
    return "moderate"


def _management_for_severity(severity: str) -> str:
    if severity == "major":
        return "Avoid concomitant use unless clinically necessary; monitor closely for toxicity or therapeutic failure."
    if severity == "moderate":
        return "Use cautiously and monitor for altered efficacy, sedation, bleeding, blood pressure, or metabolic effects."
    return "Monitor for tolerability and dose-timing issues when used regularly."


def _resolve_data_path(data_path: Path) -> Path:
    resolved = data_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Herb JSON path does not exist: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"Herb path must be a file: {resolved}")
    return resolved


def _load_json(path: Path) -> list[dict[str, Any]]:
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
        session.run(HERB_CONSTRAINT_QUERY).consume()


def _load_drug_catalog(driver: Driver, database: str) -> tuple[dict[str, str], list[tuple[str, str]]]:
    lookup: dict[str, str] = {}
    catalog: list[tuple[str, str]] = []
    query = """
    MATCH (drug:Drug)
    RETURN drug.generic_name AS generic_name, coalesce(drug.drug_class, '') AS drug_class
    """
    with driver.session(database=database) as session:
        for record in session.run(query):
            generic_name = _clean_text(record["generic_name"])
            if not generic_name:
                continue
            normalized = _normalize_lookup_key(generic_name)
            if normalized:
                lookup[normalized] = generic_name
            catalog.append((generic_name, _normalize_lookup_key(record["drug_class"]) or ""))
    return lookup, catalog


def _load_existing_herb_lookup(driver: Driver, database: str) -> dict[str, set[str]]:
    lookup: dict[str, set[str]] = {}
    query = """
    MATCH (herb:Herb)
    RETURN herb.name AS name, herb.scientific_name AS scientific_name
    """
    with driver.session(database=database) as session:
        for record in session.run(query):
            for candidate in (record["name"], record["scientific_name"]):
                normalized = _normalize_lookup_key(candidate)
                if not normalized:
                    continue
                lookup.setdefault(normalized, set()).add(record["name"])
    return lookup


def _match_explicit_drug_names(term: str, drug_lookup: dict[str, str]) -> set[str]:
    term_normalized = _normalize_lookup_key(term) or ""
    padded = f" {term_normalized} "
    matches: set[str] = set()
    for normalized_name, canonical in drug_lookup.items():
        if len(normalized_name) < 5:
            continue
        if f" {normalized_name} " in padded:
            matches.add(canonical)
    return matches


def _match_class_drugs(term: str, drug_catalog: list[tuple[str, str]]) -> set[str]:
    term_normalized = _normalize_lookup_key(term) or ""
    matches: set[str] = set()
    class_fragments = HERB_TERM_CLASS_MAP.get(term_normalized, ())
    for generic_name, class_name in drug_catalog:
        lower_name = _normalize_lookup_key(generic_name) or ""
        if any(fragment in class_name for fragment in class_fragments):
            matches.add(generic_name)
            continue
        if "statin" in term_normalized and lower_name.endswith("statin"):
            matches.add(generic_name)
        elif "cns depressant" in term_normalized and lower_name in {
            "alprazolam",
            "clonazepam",
            "diazepam",
            "lorazepam",
            "zolpidem",
            "tramadol",
            "codeine",
            "pregabalin",
            "gabapentin",
        }:
            matches.add(generic_name)
        elif "immunosuppress" in term_normalized and lower_name in {
            "tacrolimus",
            "cyclophosphamide",
            "everolimus",
            "cyclosporine",
            "mycophenolate mofetil",
        }:
            matches.add(generic_name)
        elif "antiepileptic" in term_normalized and lower_name in {
            "phenytoin",
            "carbamazepine",
            "valproic acid",
            "levetiracetam",
        }:
            matches.add(generic_name)
        elif "hepatotoxic" in term_normalized and lower_name in {
            "methotrexate",
            "isoniazid",
            "pyrazinamide",
            "nevirapine",
            "saquinavir",
        }:
            matches.add(generic_name)
    for canonical in EXPLICIT_TERM_DRUGS.get(term_normalized, ()):
        matches.add(canonical)
    return matches


def _candidate_herb_names(entry: dict[str, Any]) -> set[str]:
    candidates = {
        value
        for value in (
            entry.get("english_name"),
            entry.get("scientific_name"),
        )
        if _clean_text(value)
    }
    for key in ("aliases", "ddid_matches"):
        for value in entry.get(key, []):
            cleaned = _clean_text(value)
            if cleaned:
                candidates.add(cleaned)
    return candidates


def _merge_or_update_herb(
    driver: Driver,
    database: str,
    *,
    herb_name: str,
    properties: dict[str, Any],
    alias_names: set[str],
) -> None:
    merge_query = """
    MERGE (herb:Herb {name: $name})
    SET herb.category = coalesce($category, herb.category),
        herb.hindi_name = coalesce($hindi_name, herb.hindi_name),
        herb.tamil_name = coalesce($tamil_name, herb.tamil_name),
        herb.telugu_name = coalesce($telugu_name, herb.telugu_name),
        herb.kannada_name = coalesce($kannada_name, herb.kannada_name),
        herb.scientific_name = coalesce($scientific_name, herb.scientific_name),
        herb.risk_level_for_elderly = coalesce($risk_level_for_elderly, herb.risk_level_for_elderly),
        herb.common_uses = coalesce($common_uses, herb.common_uses),
        herb.notes = coalesce($notes, herb.notes),
        herb.aliases = coalesce($aliases, herb.aliases)
    """
    alias_query = """
    MATCH (herb:Herb)
    WHERE toLower(herb.name) IN $alias_names
    SET herb.category = coalesce($category, herb.category),
        herb.hindi_name = coalesce($hindi_name, herb.hindi_name),
        herb.tamil_name = coalesce($tamil_name, herb.tamil_name),
        herb.telugu_name = coalesce($telugu_name, herb.telugu_name),
        herb.kannada_name = coalesce($kannada_name, herb.kannada_name),
        herb.scientific_name = coalesce($scientific_name, herb.scientific_name),
        herb.risk_level_for_elderly = coalesce($risk_level_for_elderly, herb.risk_level_for_elderly),
        herb.common_uses = coalesce($common_uses, herb.common_uses),
        herb.notes = coalesce($notes, herb.notes)
    """
    with driver.session(database=database) as session:
        session.run(merge_query, name=herb_name, **properties).consume()
        if alias_names:
            session.run(alias_query, alias_names=sorted(alias_names), **properties).consume()


def _interaction_exists(
    driver: Driver,
    database: str,
    *,
    herb_names: set[str],
    drug_name: str,
) -> bool:
    query = """
    MATCH (herb:Herb)-[interaction:INTERACTS_WITH_DRUG]->(drug:Drug {generic_name: $drug_name})
    WHERE toLower(herb.name) IN $herb_names
    RETURN count(interaction) > 0 AS exists
    """
    with driver.session(database=database) as session:
        return bool(
            session.run(
                query,
                herb_names=sorted(herb_names),
                drug_name=drug_name,
            ).single()["exists"]
        )


def _create_interaction(
    driver: Driver,
    database: str,
    *,
    herb_name: str,
    drug_name: str,
    severity: str,
    mechanism: str,
    clinical_effect: str,
    evidence_level: str,
) -> None:
    query = f"""
    MATCH (herb:Herb {{name: $herb_name}})
    MERGE (drug:Drug {{generic_name: $drug_name}})
    ON CREATE SET {DRUG_DEFAULTS_CYPHER}
    MERGE (herb)-[interaction:INTERACTS_WITH_DRUG {{source: 'curated_ayurveda'}}]->(drug)
    SET interaction.severity = $severity,
        interaction.mechanism = $mechanism,
        interaction.clinical_effect = $clinical_effect,
        interaction.management = $management,
        interaction.evidence_level = $evidence_level
    """
    with driver.session(database=database) as session:
        session.run(
            query,
            herb_name=herb_name,
            drug_name=drug_name,
            severity=severity,
            mechanism=mechanism,
            clinical_effect=clinical_effect,
            management=_management_for_severity(severity),
            evidence_level=evidence_level,
        ).consume()


def ingest_ayurvedic_herbs(
    driver: Driver,
    data_path: Path,
    *,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> dict[str, int]:
    """Load curated Ayurvedic herb metadata and non-duplicative drug interactions."""
    resolved_path = _resolve_data_path(data_path)
    payload = _load_json(resolved_path)
    _ensure_schema(driver, database)

    drug_lookup, drug_catalog = _load_drug_catalog(driver, database)
    herb_lookup = _load_existing_herb_lookup(driver, database)

    herbs_processed = 0
    interactions_created = 0
    interactions_skipped = 0

    for entry in payload:
        english_name = _clean_text(entry.get("english_name"))
        if not english_name:
            continue

        candidate_names = _candidate_herb_names(entry)
        matched_alias_names: set[str] = set()
        for candidate in candidate_names:
            normalized = _normalize_lookup_key(candidate)
            if normalized:
                matched_alias_names.update(herb_lookup.get(normalized, set()))

        properties = {
            "category": _clean_text(entry.get("category")),
            "hindi_name": _clean_text(entry.get("hindi_name")),
            "tamil_name": _clean_text(entry.get("tamil_name")),
            "telugu_name": _clean_text(entry.get("telugu_name")),
            "kannada_name": _clean_text(entry.get("kannada_name")),
            "scientific_name": _clean_text(entry.get("scientific_name")),
            "risk_level_for_elderly": _clean_text(entry.get("elderly_risk_level")),
            "common_uses": [
                use
                for use in (_clean_text(value) for value in entry.get("common_uses", []))
                if use
            ],
            "notes": _clean_text(entry.get("notes")),
            "aliases": [
                alias
                for alias in (_clean_text(value) for value in entry.get("aliases", []))
                if alias
            ],
        }
        _merge_or_update_herb(
            driver,
            database,
            herb_name=english_name,
            properties=properties,
            alias_names={_normalize_lookup_key(name) for name in matched_alias_names if _normalize_lookup_key(name)},
        )
        herb_lookup.setdefault(_normalize_lookup_key(english_name) or english_name.casefold(), set()).add(english_name)
        herbs_processed += 1

        known_herb_names = {english_name}
        known_herb_names.update(matched_alias_names)
        normalized_known_herb_names = {
            _normalize_lookup_key(name)
            for name in known_herb_names
            if _normalize_lookup_key(name)
        }

        for interaction in entry.get("known_drug_interactions", []):
            drug_term = _clean_text(interaction.get("drug_or_class"))
            clinical_effect = _clean_text(interaction.get("interaction"))
            if not drug_term or not clinical_effect:
                continue

            matched_drugs = _match_explicit_drug_names(drug_term, drug_lookup)
            matched_drugs.update(_match_class_drugs(drug_term, drug_catalog))

            if not matched_drugs:
                preferred = _normalize_lookup_key(_preferred_drug_name(drug_term))
                if preferred and preferred in drug_lookup:
                    matched_drugs.add(drug_lookup[preferred])

            severity = _normalize_severity(interaction.get("severity"))
            evidence_level = _clean_text(interaction.get("evidence_basis")) or "curated"

            for drug_name in sorted(matched_drugs):
                if _interaction_exists(
                    driver,
                    database,
                    herb_names=normalized_known_herb_names,
                    drug_name=drug_name,
                ):
                    interactions_skipped += 1
                    continue
                _create_interaction(
                    driver,
                    database,
                    herb_name=english_name,
                    drug_name=drug_name,
                    severity=severity,
                    mechanism=drug_term,
                    clinical_effect=clinical_effect,
                    evidence_level=evidence_level,
                )
                interactions_created += 1

    with driver.session(database=database) as session:
        herbs_with_hindi = session.run(
            "MATCH (h:Herb) WHERE h.hindi_name IS NOT NULL AND h.hindi_name <> '' RETURN count(h) AS count"
        ).single()["count"]

    LOGGER.info(
        "Ayurvedic herb ingestion complete: %s herbs processed, %s curated Herb->Drug edges created, %s interactions skipped because a relationship already existed, %s herbs now have Hindi names.",
        f"{herbs_processed:,}",
        f"{interactions_created:,}",
        f"{interactions_skipped:,}",
        f"{herbs_with_hindi:,}",
    )
    return {
        "herbs_processed": herbs_processed,
        "interactions_created": interactions_created,
        "interactions_skipped": interactions_skipped,
        "herbs_with_hindi": herbs_with_hindi,
    }


def ingest_herb_database(
    driver: Driver,
    data_path: Path,
    *,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> dict[str, int]:
    """Compatibility wrapper for curated Ayurvedic herb ingest."""
    return ingest_ayurvedic_herbs(driver, data_path, database=database)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest curated Ayurvedic herbs into Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help="Path to ayurvedic_herbs.json.",
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password.")
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    """CLI entry point for curated herb ingestion."""
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        driver.verify_connectivity()
        ingest_ayurvedic_herbs(driver, args.data_path, database=args.database)
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
