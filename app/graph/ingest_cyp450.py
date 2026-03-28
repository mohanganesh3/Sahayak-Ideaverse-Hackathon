"""Ingest curated CYP450, transporter, QT, and electrolyte effect data into Neo4j."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path

from neo4j import Driver, GraphDatabase

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "cyp450_data.json"

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

DRUG_NAME_OVERRIDES = {
    "paracetamol": "Acetaminophen",
    "adrenaline": "Epinephrine",
    "noradrenaline": "Norepinephrine",
    "salbutamol": "Albuterol",
    "levosalbutamol": "Levalbuterol",
}

HERB_METADATA = {
    "ashwagandha": {
        "name": "Ashwagandha",
        "category": "ayurvedic",
        "scientific_name": "Withania somnifera",
    },
    "black pepper": {
        "name": "Black Pepper",
        "category": "food",
        "scientific_name": "Piper nigrum",
    },
    "piperine": {
        "name": "Black Pepper",
        "category": "food",
        "scientific_name": "Piper nigrum",
    },
    "broccoli": {
        "name": "Broccoli",
        "category": "food",
        "scientific_name": "Brassica oleracea var. italica",
    },
    "brussels sprouts": {
        "name": "Brussels Sprouts",
        "category": "food",
        "scientific_name": "Brassica oleracea var. gemmifera",
    },
    "char grilled meat": {
        "name": "Char-grilled Meat",
        "category": "food",
        "scientific_name": None,
    },
    "char grilled meats": {
        "name": "Char-grilled Meat",
        "category": "food",
        "scientific_name": None,
    },
    "garlic": {
        "name": "Garlic",
        "category": "food",
        "scientific_name": "Allium sativum",
    },
    "grapefruit": {
        "name": "Grapefruit",
        "category": "food",
        "scientific_name": "Citrus paradisi",
    },
    "grapefruit juice": {
        "name": "Grapefruit",
        "category": "food",
        "scientific_name": "Citrus paradisi",
    },
    "st john s wort": {
        "name": "St. John's Wort",
        "category": "herbal",
        "scientific_name": "Hypericum perforatum",
    },
    "turmeric": {
        "name": "Turmeric",
        "category": "ayurvedic",
        "scientific_name": "Curcuma longa",
    },
    "curcumin": {
        "name": "Turmeric",
        "category": "ayurvedic",
        "scientific_name": "Curcuma longa",
    },
}

MECHANISM_NODE_ALIASES = {
    "CYP3A4": ["CYP3A4"],
    "CYP2D6": ["CYP2D6"],
    "CYP2C9": ["CYP2C9"],
    "CYP2C19": ["CYP2C19"],
    "CYP1A2": ["CYP1A2"],
    "CYP2B6": ["CYP2B6"],
    "CYP2E1": ["CYP2E1"],
    "P-glycoprotein": ["ABCB1", "P-glycoprotein", "MDR1"],
    "OATP1B1": ["SLCO1B1", "OATP1B1"],
    "BCRP": ["ABCG2", "BCRP"],
}

SCHEMA_STATEMENTS = (
    "CREATE CONSTRAINT enzyme_name IF NOT EXISTS FOR (e:Enzyme) REQUIRE e.name IS UNIQUE",
    "CREATE CONSTRAINT transporter_name IF NOT EXISTS FOR (t:Transporter) REQUIRE t.name IS UNIQUE",
    "CREATE CONSTRAINT adverse_effect_name IF NOT EXISTS FOR (ae:AdverseEffect) REQUIRE ae.name IS UNIQUE",
    "CREATE CONSTRAINT electrolyte_effect_name IF NOT EXISTS FOR (ee:ElectrolyteEffect) REQUIRE ee.name IS UNIQUE",
    "CREATE INDEX enzyme_name_idx IF NOT EXISTS FOR (e:Enzyme) ON (e.name)",
    "CREATE INDEX transporter_name_idx IF NOT EXISTS FOR (t:Transporter) ON (t.name)",
    "CREATE INDEX adverse_effect_name_idx IF NOT EXISTS FOR (ae:AdverseEffect) ON (ae.name)",
    "CREATE INDEX electrolyte_effect_name_idx IF NOT EXISTS FOR (ee:ElectrolyteEffect) ON (ee.name)",
)

DRUG_LOOKUP_QUERY = """
MATCH (d:Drug)
RETURN d.generic_name AS generic_name,
       d.canonical_name AS canonical_name,
       coalesce(d.synonyms, []) AS synonyms
"""

HERB_LOOKUP_QUERY = """
MATCH (h:Herb)
RETURN h.name AS name,
       h.scientific_name AS scientific_name,
       h.hindi_name AS hindi_name,
       h.tamil_name AS tamil_name,
       h.telugu_name AS telugu_name,
       h.kannada_name AS kannada_name
"""

LEGACY_MECHANISM_LOOKUP_QUERY = """
MATCH (n)
WHERE n:Gene OR n:Protein
RETURN elementId(n) AS node_element_id,
       coalesce(n.name, '') AS name,
       coalesce(n.identifier, '') AS identifier
"""

MERGE_HERBS_QUERY = """
UNWIND $rows AS row
MERGE (h:Herb {name: row.name})
SET h.category = coalesce(h.category, row.category),
    h.scientific_name = coalesce(h.scientific_name, row.scientific_name)
"""

MERGE_ENZYMES_QUERY = """
UNWIND $rows AS row
MERGE (e:Enzyme {name: row.name})
SET e.notes = row.notes
"""

MERGE_TRANSPORTERS_QUERY = """
UNWIND $rows AS row
MERGE (t:Transporter {name: row.name})
SET t.notes = row.notes
"""

MERGE_EFFECTS_QUERY = """
MERGE (qt:AdverseEffect {name: 'QT prolongation'})
SET qt:SideEffect
MERGE (hypo:ElectrolyteEffect {name: 'hypokalemia'})
MERGE (hyper:ElectrolyteEffect {name: 'hyperkalemia'})
"""

BRIDGE_MECHANISM_QUERY = """
UNWIND $rows AS row
MATCH (curated {name: row.curated_name})
WHERE row.curated_label IN labels(curated)
MATCH (legacy)
WHERE elementId(legacy) = row.legacy_element_id
MERGE (curated)-[r:MAPS_TO]->(legacy)
SET r.source = row.source,
    r.matched_alias = row.matched_alias
"""

DRUG_TARGET_REL_QUERY_TEMPLATE = """
UNWIND $rows AS row
MATCH (d:Drug {{generic_name: row.source_name}})
MATCH (t:{target_label} {{name: row.target_name}})
MERGE (d)-[r:{relationship_type}]->(t)
SET r.{property_name} = row.value,
    r.source = row.source
"""

HERB_TARGET_REL_QUERY_TEMPLATE = """
UNWIND $rows AS row
MATCH (h:Herb {{name: row.source_name}})
MATCH (t:{target_label} {{name: row.target_name}})
MERGE (h)-[r:{relationship_type}]->(t)
SET r.{property_name} = row.value,
    r.source = row.source
"""

QT_REL_QUERY = """
UNWIND $rows AS row
MATCH (d:Drug {generic_name: row.drug_name})
MATCH (qt:AdverseEffect {name: 'QT prolongation'})
MERGE (d)-[r:PROLONGS_QT]->(qt)
SET r.risk_category = row.risk_category,
    r.source = row.source
"""

ELECTROLYTE_REL_QUERY_TEMPLATE = """
UNWIND $rows AS row
MATCH (d:Drug {{generic_name: row.drug_name}})
MATCH (e:ElectrolyteEffect {{name: row.effect_name}})
MERGE (d)-[r:{relationship_type}]->(e)
SET r.electrolyte = row.electrolyte,
    r.source = row.source
"""


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\xa0", " ").strip())
    return cleaned or None


def _normalize_key(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _chunked(rows: list[dict], size: int) -> Iterable[list[dict]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def _canonical_herb_entry(raw_name: str) -> dict | None:
    return HERB_METADATA.get(_normalize_key(raw_name))


def _load_json(data_path: Path) -> dict:
    resolved = data_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"CYP450 data file not found: {resolved}")
    with resolved.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _create_driver(uri: str, user: str, password: str) -> Driver:
    return GraphDatabase.driver(uri, auth=(user, password))


def _ensure_schema(driver: Driver, database: str) -> None:
    with driver.session(database=database) as session:
        for stmt in SCHEMA_STATEMENTS:
            session.run(stmt)


def _load_drug_lookup(driver: Driver, database: str) -> dict[str, str]:
    lookup: dict[str, str] = {}
    with driver.session(database=database) as session:
        for record in session.run(DRUG_LOOKUP_QUERY):
            canonical = record["generic_name"]
            for value in [record["generic_name"], record["canonical_name"], *record["synonyms"]]:
                key = _normalize_key(value)
                if key:
                    lookup.setdefault(key, canonical)
    for alias, canonical in DRUG_NAME_OVERRIDES.items():
        alias_key = _normalize_key(alias)
        canonical_key = _normalize_key(canonical)
        if alias_key and canonical_key and canonical_key in lookup:
            lookup[alias_key] = lookup[canonical_key]
    LOGGER.info("Loaded %d normalized drug aliases.", len(lookup))
    return lookup


def _load_herb_lookup(driver: Driver, database: str) -> dict[str, str]:
    lookup: dict[str, str] = {}
    with driver.session(database=database) as session:
        for record in session.run(HERB_LOOKUP_QUERY):
            canonical = record["name"]
            for field in (
                record["name"],
                record["scientific_name"],
                record["hindi_name"],
                record["tamil_name"],
                record["telugu_name"],
                record["kannada_name"],
            ):
                key = _normalize_key(field)
                if key:
                    lookup.setdefault(key, canonical)
    LOGGER.info("Loaded %d normalized herb aliases.", len(lookup))
    return lookup


def _load_legacy_mechanism_lookup(driver: Driver, database: str) -> dict[str, list[dict]]:
    lookup: dict[str, list[dict]] = defaultdict(list)
    with driver.session(database=database) as session:
        for record in session.run(LEGACY_MECHANISM_LOOKUP_QUERY):
            for value in (record["name"], record["identifier"]):
                key = _normalize_key(value)
                if key:
                    lookup[key].append(
                        {
                            "node_element_id": record["node_element_id"],
                            "matched_value": value,
                        }
                    )
    return lookup


def _resolve_drug(raw_name: str, drug_lookup: dict[str, str]) -> str | None:
    key = _normalize_key(raw_name)
    if key is None:
        return None
    return drug_lookup.get(key)


def _resolve_herb(raw_name: str, herb_lookup: dict[str, str]) -> str | None:
    key = _normalize_key(raw_name)
    if key is None:
        return None
    entry = HERB_METADATA.get(key)
    if entry:
        canonical_key = _normalize_key(entry["name"])
        if canonical_key in herb_lookup:
            return herb_lookup[canonical_key]
    return herb_lookup.get(key)


def _prepare_herb_rows(data: dict, herb_lookup: dict[str, str]) -> list[dict]:
    names: set[str] = set()
    for enzyme in data.get("enzymes", []):
        for bucket in ("major", "minor"):
            names.update(enzyme.get("substrates", {}).get(bucket, []))
        for bucket in ("strong", "moderate", "weak"):
            names.update(enzyme.get("inhibitors", {}).get(bucket, []))
            names.update(enzyme.get("inducers", {}).get(bucket, []))
    for transporter in data.get("transporters", []):
        names.update(transporter.get("substrates", []))
        names.update(transporter.get("inhibitors", []))
        names.update(transporter.get("inducers", []))

    rows: list[dict] = []
    seen: set[str] = set()
    for raw_name in names:
        entry = _canonical_herb_entry(raw_name)
        if entry is None:
            continue
        canonical_key = _normalize_key(entry["name"])
        resolved_name = herb_lookup.get(canonical_key, entry["name"])
        if resolved_name in seen:
            continue
        rows.append(
            {
                "name": resolved_name,
                "category": entry["category"],
                "scientific_name": entry["scientific_name"],
            }
        )
        seen.add(resolved_name)
    return sorted(rows, key=lambda row: row["name"])


def _merge_rows(session, query: str, rows: list[dict], batch_size: int) -> None:
    if not rows:
        return
    for batch in _chunked(rows, batch_size):
        session.run(query, rows=batch)


def _merge_static_rows(driver: Driver, database: str, query: str, rows: list[dict], batch_size: int) -> None:
    with driver.session(database=database) as session:
        _merge_rows(session, query, rows, batch_size)


def _run_write_query(driver: Driver, database: str, query: str) -> None:
    with driver.session(database=database) as session:
        session.run(query)


def _relationship_query(source_label: str, relationship_type: str, target_label: str, property_name: str) -> str:
    if source_label not in {"Drug", "Herb"}:
        raise ValueError(f"Unsupported source label: {source_label}")
    if target_label not in {"Enzyme", "Transporter"}:
        raise ValueError(f"Unsupported target label: {target_label}")
    if relationship_type not in {"IS_SUBSTRATE_OF", "INHIBITS", "INDUCES"}:
        raise ValueError(f"Unsupported relationship type: {relationship_type}")
    if property_name not in {"fraction", "strength"}:
        raise ValueError(f"Unsupported property name: {property_name}")
    template = DRUG_TARGET_REL_QUERY_TEMPLATE if source_label == "Drug" else HERB_TARGET_REL_QUERY_TEMPLATE
    return template.format(
        target_label=target_label,
        relationship_type=relationship_type,
        property_name=property_name,
    )


def _electrolyte_query(relationship_type: str) -> str:
    if relationship_type not in {"DEPLETES", "SPARES", "SENSITIVE_TO"}:
        raise ValueError(f"Unsupported electrolyte relationship type: {relationship_type}")
    return ELECTROLYTE_REL_QUERY_TEMPLATE.format(relationship_type=relationship_type)


def _write_batches(driver: Driver, database: str, query: str, rows: list[dict], batch_size: int) -> None:
    if not rows:
        return
    with driver.session(database=database) as session:
        _merge_rows(session, query, rows, batch_size)


def _accumulate_target_rows(
    targets: list[dict],
    *,
    target_label: str,
    drug_lookup: dict[str, str],
    herb_lookup: dict[str, str],
    unresolved: Counter,
) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = defaultdict(list)
    source_name = "cyp450_curated" if target_label == "Enzyme" else "transporter_curated"
    for target in targets:
        target_name = target["name"]
        for fraction, names in target.get("substrates", {}).items():
            for raw_name in names:
                matched = False
                herb_name = _resolve_herb(raw_name, herb_lookup)
                if herb_name:
                    rows[f"Herb|IS_SUBSTRATE_OF|{target_label}|fraction"].append(
                        {
                            "source_name": herb_name,
                            "target_name": target_name,
                            "value": fraction,
                            "source": source_name,
                        }
                    )
                    matched = True
                drug_name = _resolve_drug(raw_name, drug_lookup)
                if drug_name:
                    rows[f"Drug|IS_SUBSTRATE_OF|{target_label}|fraction"].append(
                        {
                            "source_name": drug_name,
                            "target_name": target_name,
                            "value": fraction,
                            "source": source_name,
                        }
                    )
                    matched = True
                if not matched:
                    unresolved[f"{target_name} substrate::{raw_name}"] += 1
        for strength, names in target.get("inhibitors", {}).items():
            for raw_name in names:
                matched = False
                herb_name = _resolve_herb(raw_name, herb_lookup)
                if herb_name:
                    rows[f"Herb|INHIBITS|{target_label}|strength"].append(
                        {
                            "source_name": herb_name,
                            "target_name": target_name,
                            "value": strength,
                            "source": source_name,
                        }
                    )
                    matched = True
                drug_name = _resolve_drug(raw_name, drug_lookup)
                if drug_name:
                    rows[f"Drug|INHIBITS|{target_label}|strength"].append(
                        {
                            "source_name": drug_name,
                            "target_name": target_name,
                            "value": strength,
                            "source": source_name,
                        }
                    )
                    matched = True
                if not matched:
                    unresolved[f"{target_name} inhibitor::{raw_name}"] += 1
        for strength, names in target.get("inducers", {}).items():
            for raw_name in names:
                matched = False
                herb_name = _resolve_herb(raw_name, herb_lookup)
                if herb_name:
                    rows[f"Herb|INDUCES|{target_label}|strength"].append(
                        {
                            "source_name": herb_name,
                            "target_name": target_name,
                            "value": strength,
                            "source": source_name,
                        }
                    )
                    matched = True
                drug_name = _resolve_drug(raw_name, drug_lookup)
                if drug_name:
                    rows[f"Drug|INDUCES|{target_label}|strength"].append(
                        {
                            "source_name": drug_name,
                            "target_name": target_name,
                            "value": strength,
                            "source": source_name,
                        }
                    )
                    matched = True
                if not matched:
                    unresolved[f"{target_name} inducer::{raw_name}"] += 1
    return rows


def _accumulate_transporter_rows(
    targets: list[dict],
    *,
    drug_lookup: dict[str, str],
    herb_lookup: dict[str, str],
    unresolved: Counter,
) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = defaultdict(list)
    for transporter in targets:
        target_name = transporter["name"]
        for raw_name in transporter.get("substrates", []):
            matched = False
            herb_name = _resolve_herb(raw_name, herb_lookup)
            if herb_name:
                rows["Herb|IS_SUBSTRATE_OF|Transporter|fraction"].append(
                    {
                        "source_name": herb_name,
                        "target_name": target_name,
                        "value": "major",
                        "source": "transporter_curated",
                    }
                )
                matched = True
            drug_name = _resolve_drug(raw_name, drug_lookup)
            if drug_name:
                rows["Drug|IS_SUBSTRATE_OF|Transporter|fraction"].append(
                    {
                        "source_name": drug_name,
                        "target_name": target_name,
                        "value": "major",
                        "source": "transporter_curated",
                    }
                )
                matched = True
            if not matched:
                unresolved[f"{target_name} substrate::{raw_name}"] += 1
        for raw_name in transporter.get("inhibitors", []):
            matched = False
            herb_name = _resolve_herb(raw_name, herb_lookup)
            if herb_name:
                rows["Herb|INHIBITS|Transporter|strength"].append(
                    {
                        "source_name": herb_name,
                        "target_name": target_name,
                        "value": "moderate",
                        "source": "transporter_curated",
                    }
                )
                matched = True
            drug_name = _resolve_drug(raw_name, drug_lookup)
            if drug_name:
                rows["Drug|INHIBITS|Transporter|strength"].append(
                    {
                        "source_name": drug_name,
                        "target_name": target_name,
                        "value": "moderate",
                        "source": "transporter_curated",
                    }
                )
                matched = True
            if not matched:
                unresolved[f"{target_name} inhibitor::{raw_name}"] += 1
        for raw_name in transporter.get("inducers", []):
            matched = False
            herb_name = _resolve_herb(raw_name, herb_lookup)
            if herb_name:
                rows["Herb|INDUCES|Transporter|strength"].append(
                    {
                        "source_name": herb_name,
                        "target_name": target_name,
                        "value": "moderate",
                        "source": "transporter_curated",
                    }
                )
                matched = True
            drug_name = _resolve_drug(raw_name, drug_lookup)
            if drug_name:
                rows["Drug|INDUCES|Transporter|strength"].append(
                    {
                        "source_name": drug_name,
                        "target_name": target_name,
                        "value": "moderate",
                        "source": "transporter_curated",
                    }
                )
                matched = True
            if not matched:
                unresolved[f"{target_name} inducer::{raw_name}"] += 1
    return rows


def _accumulate_qt_rows(
    data: dict,
    *,
    drug_lookup: dict[str, str],
    unresolved: Counter,
) -> list[dict]:
    rows: list[dict] = []
    qt_data = data.get("qt_prolonging_drugs", {})
    for risk_category, names in qt_data.items():
        normalized_risk = risk_category.replace("_risk", "")
        for raw_name in names:
            drug_name = _resolve_drug(raw_name, drug_lookup)
            if drug_name:
                rows.append(
                    {
                        "drug_name": drug_name,
                        "risk_category": normalized_risk,
                        "source": "crediblemeds_curated",
                    }
                )
            else:
                unresolved[f"qt::{raw_name}"] += 1
    return rows


def _accumulate_electrolyte_rows(
    data: dict,
    *,
    drug_lookup: dict[str, str],
    unresolved: Counter,
) -> dict[str, list[dict]]:
    rows: dict[str, list[dict]] = defaultdict(list)
    effects = data.get("electrolyte_effects", {})
    mappings = (
        ("potassium_depleting", "DEPLETES", "hypokalemia"),
        ("potassium_sparing", "SPARES", "hyperkalemia"),
        ("potassium_sensitive", "SENSITIVE_TO", "hypokalemia"),
    )
    for key, relationship_type, effect_name in mappings:
        for raw_name in effects.get(key, []):
            drug_name = _resolve_drug(raw_name, drug_lookup)
            if drug_name:
                rows[relationship_type].append(
                    {
                        "drug_name": drug_name,
                        "effect_name": effect_name,
                        "electrolyte": "potassium",
                        "source": "electrolyte_curated",
                    }
                )
            else:
                unresolved[f"{key}::{raw_name}"] += 1
    return rows


def _dedupe_rows(rows: list[dict], keys: tuple[str, ...]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple] = set()
    for row in rows:
        key = tuple(row[field] for field in keys)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _execute_relationship_groups(driver: Driver, database: str, grouped_rows: dict[str, list[dict]], batch_size: int) -> None:
    for group_key, rows in grouped_rows.items():
        if not rows:
            continue
        source_label, relationship_type, target_label, property_name = group_key.split("|")
        query = _relationship_query(source_label, relationship_type, target_label, property_name)
        deduped = _dedupe_rows(rows, ("source_name", "target_name", "value"))
        _write_batches(driver, database, query, deduped, batch_size)
        LOGGER.info("Wrote %s %s rows to %s.", f"{len(deduped):,}", relationship_type, target_label)


def _top_unresolved(unresolved: Counter, limit: int = 20) -> list[tuple[str, int]]:
    return unresolved.most_common(limit)


def _build_mechanism_bridge_rows(data: dict, mechanism_lookup: dict[str, list[dict]]) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple[str, int]] = set()
    for enzyme in data.get("enzymes", []):
        curated_name = enzyme["name"]
        for alias in MECHANISM_NODE_ALIASES.get(curated_name, [curated_name]):
            key = _normalize_key(alias)
            for legacy in mechanism_lookup.get(key, []):
                dedupe_key = ("Enzyme", legacy["node_element_id"])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(
                    {
                        "curated_label": "Enzyme",
                        "curated_name": curated_name,
                        "legacy_element_id": legacy["node_element_id"],
                        "matched_alias": alias,
                        "source": "mechanism_bridge",
                    }
                )
    for transporter in data.get("transporters", []):
        curated_name = transporter["name"]
        for alias in MECHANISM_NODE_ALIASES.get(curated_name, [curated_name]):
            key = _normalize_key(alias)
            for legacy in mechanism_lookup.get(key, []):
                dedupe_key = ("Transporter", legacy["node_element_id"])
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(
                    {
                        "curated_label": "Transporter",
                        "curated_name": curated_name,
                        "legacy_element_id": legacy["node_element_id"],
                        "matched_alias": alias,
                        "source": "mechanism_bridge",
                    }
                )
    return rows


def ingest(
    *,
    data_path: Path,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    database: str,
    batch_size: int,
) -> None:
    data = _load_json(data_path)
    driver = _create_driver(neo4j_uri, neo4j_user, neo4j_password)
    unresolved = Counter()
    try:
        driver.verify_connectivity()
        _ensure_schema(driver, database)

        drug_lookup = _load_drug_lookup(driver, database)
        herb_lookup = _load_herb_lookup(driver, database)

        herb_rows = _prepare_herb_rows(data, herb_lookup)
        if herb_rows:
            LOGGER.info("Ensuring %d Herb nodes for CYP/food modifiers.", len(herb_rows))
            _merge_static_rows(driver, database, MERGE_HERBS_QUERY, herb_rows, batch_size)
            herb_lookup = _load_herb_lookup(driver, database)

        enzyme_rows = [{"name": item["name"], "notes": item.get("notes", "")} for item in data.get("enzymes", [])]
        transporter_rows = [{"name": item["name"], "notes": item.get("notes", "")} for item in data.get("transporters", [])]
        _merge_static_rows(driver, database, MERGE_ENZYMES_QUERY, enzyme_rows, batch_size)
        _merge_static_rows(driver, database, MERGE_TRANSPORTERS_QUERY, transporter_rows, batch_size)
        _run_write_query(driver, database, MERGE_EFFECTS_QUERY)
        mechanism_lookup = _load_legacy_mechanism_lookup(driver, database)
        bridge_rows = _build_mechanism_bridge_rows(data, mechanism_lookup)
        _write_batches(driver, database, BRIDGE_MECHANISM_QUERY, bridge_rows, batch_size)
        LOGGER.info("Wrote %s MAPS_TO mechanism bridge rows.", f"{len(bridge_rows):,}")

        enzyme_rels = _accumulate_target_rows(
            data.get("enzymes", []),
            target_label="Enzyme",
            drug_lookup=drug_lookup,
            herb_lookup=herb_lookup,
            unresolved=unresolved,
        )
        transporter_rels = _accumulate_transporter_rows(
            data.get("transporters", []),
            drug_lookup=drug_lookup,
            herb_lookup=herb_lookup,
            unresolved=unresolved,
        )
        _execute_relationship_groups(driver, database, enzyme_rels, batch_size)
        _execute_relationship_groups(driver, database, transporter_rels, batch_size)

        qt_rows = _dedupe_rows(
            _accumulate_qt_rows(data, drug_lookup=drug_lookup, unresolved=unresolved),
            ("drug_name", "risk_category"),
        )
        _write_batches(driver, database, QT_REL_QUERY, qt_rows, batch_size)
        LOGGER.info("Wrote %s PROLONGS_QT rows.", f"{len(qt_rows):,}")

        electrolyte_rows = _accumulate_electrolyte_rows(data, drug_lookup=drug_lookup, unresolved=unresolved)
        for relationship_type, rows in electrolyte_rows.items():
            deduped = _dedupe_rows(rows, ("drug_name", "effect_name", "electrolyte"))
            _write_batches(driver, database, _electrolyte_query(relationship_type), deduped, batch_size)
            LOGGER.info("Wrote %s %s rows.", f"{len(deduped):,}", relationship_type)

        LOGGER.info(
            "Finished CYP450 ingest: %s enzymes, %s transporters, %s QT rows, %s unresolved names.",
            f"{len(enzyme_rows):,}",
            f"{len(transporter_rows):,}",
            f"{len(qt_rows):,}",
            f"{sum(unresolved.values()):,}",
        )
        for item, count in _top_unresolved(unresolved):
            LOGGER.warning("Unresolved input %s (%d)", item, count)
    finally:
        driver.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-file", type=Path, default=DEFAULT_DATA_PATH, help="Path to cyp450_data.json")
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD)
    parser.add_argument("--neo4j-database", default=DEFAULT_NEO4J_DATABASE)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )
    ingest(
        data_path=args.data_file,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        database=args.neo4j_database,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
