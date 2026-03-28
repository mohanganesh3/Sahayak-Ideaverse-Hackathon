"""Expand CYP/transporter/QT/electrolyte coverage using live graph and public sources.

This pass is additive:
- derives missing mechanism edges from PrimeKG/Hetionet already in Neo4j
- adds higher-confidence FDA DDI table evidence
- ingests curated herb-CYP literature coverage
- expands QT, potassium, and CNS-depressant mechanism nodes

It is intentionally conservative:
- existing curated edges are never overwritten
- low-confidence PrimeKG-derived edges are only upgraded when the FDA table confirms them
- ambiguous BINDS edges are ignored to avoid false positives
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pandas as pd
import requests
from neo4j import Driver, GraphDatabase

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_HERB_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "herb_cyp_interactions.json"

FDA_DDI_TABLE_URL = (
    "https://www.fda.gov/drugs/drug-interactions-labeling/"
    "drug-development-and-drug-interactions-table-substrates-inhibitors-and-inducers"
)
QT_DRUGS_PDF_URL = "https://www.impaactnetwork.org/sites/default/files/2023-12/QTDrugsToAvoidList_12DEC2023.pdf"

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_FOOTNOTE_RE = re.compile(r"\([a-z, ]+\)")
_GENERIC_QT_RE = re.compile(r"([A-Z][A-Za-z0-9’'()\-\.,/ +]+?)\s*\((KR|PR|CR|SR)\)")

DERIVED_EDGE_SOURCES = {
    "primekg_derived",
    "primekg_target_derived",
    "primekg_transporter_derived",
}

DRUG_ALIAS_OVERRIDES = {
    "paracetamol": "Acetaminophen",
    "adrenaline": "Epinephrine",
    "noradrenaline": "Norepinephrine",
    "salbutamol": "Albuterol",
    "levosalbutamol": "Levalbuterol",
    "rifampin": "Rifampicin",
    "s warfarin": "Warfarin",
    "r warfarin": "Warfarin",
    "fluorouracil": "5-Fluorouracil",
    "5 fu": "5-Fluorouracil",
    "5-fluorouracil": "5-Fluorouracil",
    "artemether lumefantrine": "Lumefantrine",
    "dextromethorphan quinidine": "Quinidine",
}

MECHANISM_NODE_SPECS = {
    "Enzyme": {
        "CYP3A4": ["CYP3A4", "1576"],
        "CYP2D6": ["CYP2D6", "1565"],
        "CYP2C9": ["CYP2C9", "1559"],
        "CYP2C19": ["CYP2C19", "1557"],
        "CYP1A2": ["CYP1A2", "1544"],
        "CYP2B6": ["CYP2B6", "1555"],
        "CYP2E1": ["CYP2E1", "1571"],
        "CYP3A5": ["CYP3A5", "1577"],
        "CYP2C8": ["CYP2C8"],
        "CYP2A6": ["CYP2A6"],
    },
    "Transporter": {
        "P-glycoprotein": ["ABCB1", "P-glycoprotein", "MDR1"],
        "OATP1B1": ["SLCO1B1", "OATP1B1"],
        "OATP1B3": ["SLCO1B3", "OATP1B3"],
        "BCRP": ["ABCG2", "BCRP"],
        "OAT1": ["SLC22A6", "OAT1"],
        "OAT3": ["SLC22A8", "OAT3"],
        "OCT1": ["SLC22A1", "OCT1"],
        "OCT2": ["SLC22A2", "OCT2"],
        "MATE1": ["SLC47A1", "MATE1"],
        "MATE-2K": ["SLC47A2", "MATE-2K", "MATE2K"],
    },
}

NODE_NOTES = {
    "CYP3A5": "PrimeKG/FDA-expanded cytochrome P450 isoform.",
    "CYP2C8": "PrimeKG/FDA-expanded cytochrome P450 isoform.",
    "CYP2A6": "PrimeKG/herb-expanded cytochrome P450 isoform.",
    "OATP1B3": "Organic anion-transporting polypeptide 1B3 transporter.",
    "OAT1": "Organic anion transporter 1.",
    "OAT3": "Organic anion transporter 3.",
    "OCT1": "Organic cation transporter 1.",
    "OCT2": "Organic cation transporter 2.",
    "MATE1": "Multidrug and toxin extrusion protein 1.",
    "MATE-2K": "Multidrug and toxin extrusion protein 2-K.",
}

POTASSIUM_DEPLETING_DRUGS = [
    "furosemide",
    "bumetanide",
    "torsemide",
    "ethacrynic acid",
    "hydrochlorothiazide",
    "chlorthalidone",
    "indapamide",
    "metolazone",
    "chlorothiazide",
    "bendroflumethiazide",
    "methyclothiazide",
    "trichlormethiazide",
    "acetazolamide",
    "dichlorphenamide",
    "fludrocortisone",
    "prednisone",
    "prednisolone",
    "dexamethasone",
    "hydrocortisone",
    "amphotericin b",
    "albuterol",
    "salbutamol",
    "terbutaline",
    "ritodrine",
    "dobutamine",
    "fenoterol",
    "arformoterol",
    "indacaterol",
    "bisacodyl",
    "senna",
    "phenolphthalein",
]

POTASSIUM_ELEVATING_DRUGS = [
    "spironolactone",
    "eplerenone",
    "amiloride",
    "triamterene",
    "lisinopril",
    "enalapril",
    "ramipril",
    "captopril",
    "perindopril",
    "telmisartan",
    "losartan",
    "valsartan",
    "olmesartan",
    "candesartan",
    "irbesartan",
    "trimethoprim",
    "cyclosporine",
    "tacrolimus",
    "heparin",
    "drospirenone",
]

POTASSIUM_SENSITIVE_DRUGS = [
    "digoxin",
    "dofetilide",
]

CNS_DEPRESSANT_DRUGS = [
    "alprazolam",
    "clonazepam",
    "diazepam",
    "lorazepam",
    "midazolam",
    "triazolam",
    "zolpidem",
    "zopiclone",
    "eszopiclone",
    "phenobarbital",
    "primidone",
    "pentobarbital",
    "quetiapine",
    "olanzapine",
    "risperidone",
    "haloperidol",
    "chlorpromazine",
    "clozapine",
    "pimozide",
    "promethazine",
    "hydroxyzine",
    "diphenhydramine",
    "gabapentin",
    "pregabalin",
    "baclofen",
    "tizanidine",
    "cyclobenzaprine",
    "carisoprodol",
    "methocarbamol",
    "morphine",
    "codeine",
    "tramadol",
    "fentanyl",
    "oxycodone",
    "hydromorphone",
    "methadone",
    "buprenorphine",
]

LEGACY_LOOKUP_QUERY = """
MATCH (n)
WHERE n:Gene OR n:Protein
RETURN elementId(n) AS element_id,
       coalesce(n.name, '') AS name,
       coalesce(n.identifier, '') AS identifier
"""

MERGE_NODE_QUERY_TEMPLATE = """
UNWIND $rows AS row
MERGE (n:{label} {{name: row.name}})
SET n.notes = coalesce(n.notes, row.notes)
"""

BRIDGE_QUERY = """
UNWIND $rows AS row
MATCH (curated {name: row.curated_name})
WHERE row.curated_label IN labels(curated)
MATCH (legacy)
WHERE elementId(legacy) = row.legacy_element_id
MERGE (curated)-[r:MAPS_TO]->(legacy)
SET r.source = row.source,
    r.matched_alias = row.matched_alias
"""

MERGE_EFFECT_NODES_QUERY = """
MERGE (qt:AdverseEffect {name: 'QT prolongation'})
SET qt:SideEffect
MERGE (cns:AdverseEffect {name: 'CNS depression'})
SET cns:SideEffect
MERGE (hypo:ElectrolyteEffect {name: 'hypokalemia'})
MERGE (hyper:ElectrolyteEffect {name: 'hyperkalemia'})
MERGE (:PharmacokineticEffect {name: 'Reduced oral drug absorption'})
"""

PRIMEKG_DERIVE_QUERY_TEMPLATE = """
MATCH (target:{target_label})-[:MAPS_TO]->(legacy)
MATCH (d:Drug)-[:{source_rel}]->(legacy)
WITH DISTINCT d, target
WHERE NOT EXISTS {{ MATCH (d)-[:{target_rel}]->(target) }}
CREATE (d)-[r:{target_rel}]->(target)
SET r += $props
RETURN count(r) AS created
"""

COUNT_QUERIES = {
    "substrates_by_enzyme": "MATCH (d:Drug)-[:IS_SUBSTRATE_OF]->(e:Enzyme) RETURN e.name AS name, count(d) AS count ORDER BY count DESC, name",
    "inhibitors_by_enzyme": "MATCH (d:Drug)-[:INHIBITS]->(e:Enzyme) RETURN e.name AS name, count(d) AS count ORDER BY count DESC, name",
    "herb_enzyme_total": "MATCH (h:Herb)-[r]->(e:Enzyme) RETURN h.name AS herb, type(r) AS relationship, e.name AS enzyme ORDER BY herb, relationship, enzyme",
    "indirect_pairs": "MATCH (a:Drug)-[:INHIBITS]->(e:Enzyme)<-[:IS_SUBSTRATE_OF]-(b:Drug) WHERE a <> b RETURN count(DISTINCT [a.generic_name, b.generic_name]) AS count",
    "qt_drugs": "MATCH (d:Drug)-[:PROLONGS_QT]->() RETURN count(d) AS count",
    "electrolytes": "MATCH ()-[r:DEPLETES|SPARES|SENSITIVE_TO|ELEVATES]->() RETURN type(r) AS relationship, count(*) AS count ORDER BY count DESC, relationship",
    "clarithromycin_simvastatin": (
        "MATCH (a:Drug)-[:INHIBITS]->(e:Enzyme)<-[:IS_SUBSTRATE_OF]-(b:Drug) "
        "WHERE toLower(a.generic_name) CONTAINS 'clarithromycin' "
        "AND toLower(b.generic_name) CONTAINS 'simvastatin' "
        "RETURN a.generic_name AS inhibitor, e.name AS enzyme, b.generic_name AS substrate"
    ),
    "pepper_test": (
        "MATCH (h:Herb)-[:INHIBITS]->(e:Enzyme)<-[:IS_SUBSTRATE_OF]-(d:Drug) "
        "WHERE toLower(h.name) CONTAINS 'pepper' "
        "RETURN h.name AS herb, e.name AS enzyme, d.generic_name AS drug "
        "ORDER BY drug LIMIT 10"
    ),
}


def _chunked(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for start in range(0, len(rows), size):
        yield rows[start:start + size]


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", str(value).replace("\xa0", " ").strip())
    return cleaned or None


def _normalize_key(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _load_json(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    return json.loads(resolved.read_text(encoding="utf-8"))


def _create_driver(uri: str, user: str, password: str) -> Driver:
    return GraphDatabase.driver(uri, auth=(user, password))


def _load_drug_lookup(driver: Driver, database: str) -> dict[str, str]:
    lookup: dict[str, str] = {}
    query = """
    MATCH (d:Drug)
    RETURN d.generic_name AS generic_name,
           d.canonical_name AS canonical_name,
           coalesce(d.synonyms, []) AS synonyms
    """
    with driver.session(database=database) as session:
        for record in session.run(query):
            canonical = record["generic_name"]
            for value in [record["generic_name"], record["canonical_name"], *record["synonyms"]]:
                key = _normalize_key(value)
                if key:
                    lookup.setdefault(key, canonical)
    for alias, canonical in DRUG_ALIAS_OVERRIDES.items():
        alias_key = _normalize_key(alias)
        canonical_key = _normalize_key(canonical)
        if alias_key and canonical_key and canonical_key in lookup:
            lookup[alias_key] = lookup[canonical_key]
    return lookup


def _load_herb_lookup(driver: Driver, database: str) -> dict[str, str]:
    lookup: dict[str, str] = {}
    query = """
    MATCH (h:Herb)
    RETURN h.name AS name,
           h.scientific_name AS scientific_name,
           h.hindi_name AS hindi_name,
           h.tamil_name AS tamil_name,
           h.telugu_name AS telugu_name,
           h.kannada_name AS kannada_name
    """
    with driver.session(database=database) as session:
        for record in session.run(query):
            canonical = record["name"]
            for value in (
                record["name"],
                record["scientific_name"],
                record["hindi_name"],
                record["tamil_name"],
                record["telugu_name"],
                record["kannada_name"],
            ):
                key = _normalize_key(value)
                if key:
                    lookup.setdefault(key, canonical)
    return lookup


def _load_legacy_lookup(driver: Driver, database: str) -> dict[str, list[dict[str, str]]]:
    lookup: dict[str, list[dict[str, str]]] = defaultdict(list)
    with driver.session(database=database) as session:
        for record in session.run(LEGACY_LOOKUP_QUERY):
            for value in (record["name"], record["identifier"]):
                key = _normalize_key(value)
                if key:
                    lookup[key].append(
                        {
                            "element_id": record["element_id"],
                            "matched_value": value,
                        }
                    )
    return lookup


def _normalize_enzyme_name(raw_name: str) -> list[str]:
    name = _clean_text(raw_name) or ""
    name = name.replace("(a)", "").replace("(b)", "").strip()
    if name == "CYP3A":
        return ["CYP3A4"]
    return [name]


def _normalize_transporter_names(raw_name: str) -> list[str]:
    name = (_clean_text(raw_name) or "").replace("  ", " ")
    if not name or name == "-":
        return []
    parts = [part.strip() for part in name.split(",")]
    return [part for part in parts if part]


def _candidate_names(raw_name: str) -> list[str]:
    cleaned = _clean_text(raw_name)
    if cleaned is None or cleaned == "-":
        return []
    stripped = _FOOTNOTE_RE.sub("", cleaned).strip(" ,;")
    candidates: list[str] = []
    seen: set[str] = set()

    def add(candidate: str | None) -> None:
        candidate = _clean_text(candidate)
        if candidate and candidate not in seen and candidate != "-":
            seen.add(candidate)
            candidates.append(candidate)

    add(stripped)
    add(re.sub(r"\([^)]*\)", "", stripped))
    for inner in re.findall(r"\(([^)]{2,})\)", stripped):
        add(inner)
    if "/" in stripped:
        for part in stripped.split("/"):
            add(part)
    if stripped.lower().startswith(("s-", "r-")) and len(stripped) > 2:
        add(stripped[2:])
    return candidates


def _resolve_drug_name(raw_name: str, drug_lookup: dict[str, str]) -> str | None:
    for candidate in _candidate_names(raw_name):
        key = _normalize_key(candidate)
        if key and key in drug_lookup:
            return drug_lookup[key]
    return None


def _resolve_herb_name(raw_names: list[str], herb_lookup: dict[str, str]) -> str | None:
    for raw_name in raw_names:
        key = _normalize_key(raw_name)
        if key and key in herb_lookup:
            return herb_lookup[key]
    return None


def _ensure_mechanism_nodes_and_bridges(driver: Driver, database: str, batch_size: int) -> None:
    enzyme_rows = [
        {"name": name, "notes": NODE_NOTES.get(name, "")}
        for name in sorted(MECHANISM_NODE_SPECS["Enzyme"])
    ]
    transporter_rows = [
        {"name": name, "notes": NODE_NOTES.get(name, "")}
        for name in sorted(MECHANISM_NODE_SPECS["Transporter"])
    ]
    with driver.session(database=database) as session:
        for batch in _chunked(enzyme_rows, batch_size):
            session.run(MERGE_NODE_QUERY_TEMPLATE.format(label="Enzyme"), rows=batch)
        for batch in _chunked(transporter_rows, batch_size):
            session.run(MERGE_NODE_QUERY_TEMPLATE.format(label="Transporter"), rows=batch)
        session.run(MERGE_EFFECT_NODES_QUERY)

    legacy_lookup = _load_legacy_lookup(driver, database)
    bridge_rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for label, mapping in MECHANISM_NODE_SPECS.items():
        for curated_name, aliases in mapping.items():
            for alias in aliases:
                key = _normalize_key(alias)
                for legacy in legacy_lookup.get(key, []):
                    dedupe_key = (label, curated_name, legacy["element_id"])
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    bridge_rows.append(
                        {
                            "curated_label": label,
                            "curated_name": curated_name,
                            "legacy_element_id": legacy["element_id"],
                            "matched_alias": alias,
                            "source": "mechanism_bridge",
                        }
                    )
    if bridge_rows:
        with driver.session(database=database) as session:
            for batch in _chunked(bridge_rows, batch_size):
                session.run(BRIDGE_QUERY, rows=batch)
    LOGGER.info("Ensured mechanism nodes and %s MAPS_TO bridge rows.", f"{len(bridge_rows):,}")


def _derive_primekg_edges(driver: Driver, database: str) -> dict[str, int]:
    results: dict[str, int] = {}
    with driver.session(database=database) as session:
        substrate_count = session.run(
            PRIMEKG_DERIVE_QUERY_TEMPLATE.format(
                target_label="Enzyme",
                source_rel="METABOLIZED_BY",
                target_rel="IS_SUBSTRATE_OF",
            ),
            props={"fraction": "unknown", "source": "primekg_derived", "confidence": 0.75},
        ).single()["created"]
        inhibitor_count = session.run(
            PRIMEKG_DERIVE_QUERY_TEMPLATE.format(
                target_label="Enzyme",
                source_rel="TARGETS",
                target_rel="INHIBITS",
            ),
            props={"strength": "unknown", "source": "primekg_target_derived", "confidence": 0.60},
        ).single()["created"]
        transporter_count = session.run(
            PRIMEKG_DERIVE_QUERY_TEMPLATE.format(
                target_label="Transporter",
                source_rel="TRANSPORTED_BY",
                target_rel="IS_SUBSTRATE_OF",
            ),
            props={"fraction": "unknown", "source": "primekg_transporter_derived", "confidence": 0.75},
        ).single()["created"]
    results["primekg_enzyme_substrates"] = substrate_count
    results["primekg_enzyme_inhibitors"] = inhibitor_count
    results["primekg_transporter_substrates"] = transporter_count
    return results


def _fetch_fda_tables() -> list[dict[str, Any]]:
    tables = pd.read_html(FDA_DDI_TABLE_URL)
    rows: list[dict[str, Any]] = []

    substrate_table = tables[3]
    for _, rec in substrate_table.iterrows():
        for enzyme_name in _normalize_enzyme_name(rec["Enzyme"]):
            for item in _candidate_names(rec["Sensitive index substrates unless otherwise noted"]):
                rows.append(
                    {
                        "relationship": "IS_SUBSTRATE_OF",
                        "target_label": "Enzyme",
                        "target_name": enzyme_name,
                        "raw_drug": item,
                        "props": {
                            "fraction": "major",
                            "source": "fda_ddi_table",
                            "confidence": 0.95,
                        },
                    }
                )

    inhibitor_table = tables[4]
    for _, rec in inhibitor_table.iterrows():
        for enzyme_name in _normalize_enzyme_name(rec["Enzyme"]):
            for column_name, strength in (
                ("Strong index inhibitors", "strong"),
                ("Moderate index inhibitors", "moderate"),
            ):
                for item in _candidate_names(rec[column_name]):
                    rows.append(
                        {
                            "relationship": "INHIBITS",
                            "target_label": "Enzyme",
                            "target_name": enzyme_name,
                            "raw_drug": item,
                            "props": {
                                "strength": strength,
                                "source": "fda_ddi_table",
                                "confidence": 0.95,
                            },
                        }
                    )

    inducer_table = tables[5]
    for _, rec in inducer_table.iterrows():
        for enzyme_name in _normalize_enzyme_name(rec["Unnamed: 0"]):
            for column_name, strength in (
                ("Strong inducers", "strong"),
                ("Moderate inducers", "moderate"),
            ):
                for item in _candidate_names(rec[column_name]):
                    rows.append(
                        {
                            "relationship": "INDUCES",
                            "target_label": "Enzyme",
                            "target_name": enzyme_name,
                            "raw_drug": item,
                            "props": {
                                "strength": strength,
                                "source": "fda_ddi_table",
                                "confidence": 0.95,
                            },
                        }
                    )

    transporter_substrate_table = tables[6]
    for _, rec in transporter_substrate_table.iterrows():
        for transporter_name in _normalize_transporter_names(rec["Transporter"]):
            for item in _candidate_names(rec["Substrate"]):
                rows.append(
                    {
                        "relationship": "IS_SUBSTRATE_OF",
                        "target_label": "Transporter",
                        "target_name": transporter_name,
                        "raw_drug": item,
                        "props": {
                            "fraction": "major",
                            "source": "fda_ddi_table",
                            "confidence": 0.95,
                        },
                    }
                )

    transporter_inhibitor_table = tables[7]
    for _, rec in transporter_inhibitor_table.iterrows():
        for transporter_name in _normalize_transporter_names(rec["Transporter"]):
            for item in _candidate_names(rec["Inhibitor"]):
                rows.append(
                    {
                        "relationship": "INHIBITS",
                        "target_label": "Transporter",
                        "target_name": transporter_name,
                        "raw_drug": item,
                        "props": {
                            "strength": "moderate",
                            "source": "fda_ddi_table",
                            "confidence": 0.95,
                        },
                    }
                )

    return rows


def _rel_type_query(
    source_label: str,
    relationship_type: str,
    target_label: str,
    action: str,
) -> str:
    source_key = "generic_name" if source_label == "Drug" else "name"
    if action == "fetch":
        return f"""
        MATCH (s:{source_label} {{{source_key}: $source_name}})-[r:{relationship_type}]->(t:{target_label} {{name: $target_name}})
        RETURN elementId(r) AS element_id, r.source AS source
        """
    if action == "create":
        return f"""
        MATCH (s:{source_label} {{{source_key}: $source_name}})
        MATCH (t:{target_label} {{name: $target_name}})
        CREATE (s)-[r:{relationship_type}]->(t)
        SET r += $props
        """
    if action == "update":
        return """
        MATCH ()-[r]->()
        WHERE elementId(r) = $element_id
        SET r += $props
        """
    if action == "delete":
        return """
        MATCH ()-[r]->()
        WHERE elementId(r) = $element_id
        DELETE r
        """
    raise ValueError(f"Unsupported action: {action}")


def _upsert_authoritative_drug_edge(
    session,
    *,
    drug_name: str,
    relationship_type: str,
    target_label: str,
    target_name: str,
    props: dict[str, Any],
) -> str:
    existing = list(
        session.run(
            _rel_type_query("Drug", relationship_type, target_label, "fetch"),
            source_name=drug_name,
            target_name=target_name,
        )
    )
    if not existing:
        session.run(
            _rel_type_query("Drug", relationship_type, target_label, "create"),
            source_name=drug_name,
            target_name=target_name,
            props=props,
        )
        return "created"

    non_derived = [row for row in existing if row["source"] not in DERIVED_EDGE_SOURCES]
    if non_derived:
        return "skipped_existing"

    primary = existing[0]
    session.run(
        _rel_type_query("Drug", relationship_type, target_label, "update"),
        element_id=primary["element_id"],
        props=props,
    )
    for duplicate in existing[1:]:
        session.run(
            _rel_type_query("Drug", relationship_type, target_label, "delete"),
            element_id=duplicate["element_id"],
        )
    return "upgraded"


def _apply_fda_rows(driver: Driver, database: str, drug_lookup: dict[str, str]) -> Counter:
    counters = Counter()
    rows = _fetch_fda_tables()
    with driver.session(database=database) as session:
        for row in rows:
            drug_name = _resolve_drug_name(row["raw_drug"], drug_lookup)
            if not drug_name:
                counters["unresolved_drugs"] += 1
                continue
            status = _upsert_authoritative_drug_edge(
                session,
                drug_name=drug_name,
                relationship_type=row["relationship"],
                target_label=row["target_label"],
                target_name=row["target_name"],
                props=row["props"],
            )
            counters[status] += 1
            counters[f"{row['relationship']}_{row['target_label']}"] += 1
    return counters


def _merge_herb_node(session, herb: dict[str, Any]) -> None:
    session.run(
        """
        MERGE (h:Herb {name: $name})
        SET h.category = coalesce(h.category, $category),
            h.scientific_name = coalesce(h.scientific_name, $scientific_name)
        """,
        name=herb["name"],
        category=herb.get("category"),
        scientific_name=herb.get("scientific_name"),
    )


def _apply_herb_literature(driver: Driver, database: str, herb_data_path: Path) -> Counter:
    herb_data = _load_json(herb_data_path)
    counters = Counter()
    with driver.session(database=database) as session:
        for herb in herb_data.get("herbs", []):
            _merge_herb_node(session, herb)
    herb_lookup = _load_herb_lookup(driver, database)
    with driver.session(database=database) as session:
        for herb in herb_data.get("herbs", []):
            canonical = _resolve_herb_name([herb["name"], *herb.get("aliases", [])], herb_lookup) or herb["name"]
            if canonical != herb["name"]:
                session.run(
                    """
                    MATCH (h:Herb {name: $canonical})
                    SET h.scientific_name = coalesce(h.scientific_name, $scientific_name),
                        h.category = coalesce(h.category, $category)
                    """,
                    canonical=canonical,
                    scientific_name=herb.get("scientific_name"),
                        category=herb.get("category"),
                )
            if herb.get("evidence_level") == "special_case":
                existing_absorption = session.run(
                    """
                    MATCH (h:Herb {name: $herb_name})-[r:AFFECTS_ABSORPTION]->(:PharmacokineticEffect {name: 'Reduced oral drug absorption'})
                    RETURN count(r) AS count
                    """,
                    herb_name=canonical,
                ).single()["count"]
                if not existing_absorption:
                    session.run(
                        """
                        MATCH (h:Herb {name: $herb_name})
                        MATCH (pe:PharmacokineticEffect {name: 'Reduced oral drug absorption'})
                        CREATE (h)-[r:AFFECTS_ABSORPTION]->(pe)
                        SET r.source = 'published_literature',
                            r.confidence = 0.70,
                            r.reference = $reference,
                            r.note = $note
                        """,
                        herb_name=canonical,
                        reference="Psyllium absorption-timing literature",
                        note=herb.get("notes", "Separate psyllium from oral medicines by at least 2 hours."),
                    )
                    counters["created_absorption_edges"] += 1
            for interaction in herb.get("interactions", []):
                existing = list(
                    session.run(
                        _rel_type_query("Herb", interaction["relationship"], interaction["target_type"], "fetch"),
                        source_name=canonical,
                        target_name=interaction["target_name"],
                    )
                )
                if existing:
                    counters["existing_edges_skipped"] += 1
                    continue
                props = {
                    "source": "published_literature",
                    "confidence": interaction["confidence"],
                    "reference": interaction["reference"],
                }
                if interaction["relationship"] == "IS_SUBSTRATE_OF":
                    props["fraction"] = "minor" if interaction.get("strength") == "weak" else "major"
                else:
                    props["strength"] = interaction["strength"]
                session.run(
                    _rel_type_query("Herb", interaction["relationship"], interaction["target_type"], "create"),
                    source_name=canonical,
                    target_name=interaction["target_name"],
                    props=props,
                )
                counters["created_edges"] += 1
                counters[f"{interaction['relationship']}_{interaction['target_type']}"] += 1
    return counters


def _fetch_qt_pdf_rows() -> dict[str, list[str]]:
    response = requests.get(QT_DRUGS_PDF_URL, timeout=60)
    response.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(response.content)
        temp_path = Path(handle.name)
    try:
        text = subprocess.run(
            ["pdftotext", str(temp_path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    finally:
        temp_path.unlink(missing_ok=True)
    start = text.find("Abarelix")
    if start != -1:
        text = text[start:]
    items = _GENERIC_QT_RE.findall(text.replace("\x0c", "\n"))
    qt_rows: dict[str, list[str]] = defaultdict(list)
    category_map = {"KR": "known", "PR": "possible", "CR": "conditional", "SR": "special"}
    for raw_name, category in items:
        qt_rows[category_map[category]].append(raw_name)
    return qt_rows


def _apply_qt_rows(driver: Driver, database: str, drug_lookup: dict[str, str]) -> Counter:
    counters = Counter()
    qt_rows = _fetch_qt_pdf_rows()
    with driver.session(database=database) as session:
        for risk_category, raw_names in qt_rows.items():
            for raw_name in raw_names:
                drug_name = _resolve_drug_name(raw_name, drug_lookup)
                if not drug_name:
                    counters["unresolved_drugs"] += 1
                    continue
                existing = session.run(
                    """
                    MATCH (d:Drug {generic_name: $drug_name})-[r:PROLONGS_QT]->(:AdverseEffect {name: 'QT prolongation'})
                    RETURN count(r) AS count
                    """,
                    drug_name=drug_name,
                ).single()["count"]
                if existing:
                    counters["existing_edges_skipped"] += 1
                    continue
                session.run(
                    """
                    MATCH (d:Drug {generic_name: $drug_name})
                    MATCH (qt:AdverseEffect {name: 'QT prolongation'})
                    CREATE (d)-[r:PROLONGS_QT]->(qt)
                    SET r.risk_category = $risk_category,
                        r.source = 'crediblemeds_pdf',
                        r.confidence = 0.90
                    """,
                    drug_name=drug_name,
                    risk_category=risk_category,
                )
                counters["created_edges"] += 1
    return counters


def _apply_electrolyte_rows(driver: Driver, database: str, drug_lookup: dict[str, str]) -> Counter:
    counters = Counter()
    configs = (
        ("DEPLETES", "hypokalemia", POTASSIUM_DEPLETING_DRUGS),
        ("SPARES", "hyperkalemia", ["spironolactone", "amiloride", "triamterene", "eplerenone"]),
        ("ELEVATES", "hyperkalemia", POTASSIUM_ELEVATING_DRUGS),
        ("SENSITIVE_TO", "hypokalemia", POTASSIUM_SENSITIVE_DRUGS),
    )
    with driver.session(database=database) as session:
        for relationship_type, effect_name, raw_names in configs:
            for raw_name in raw_names:
                drug_name = _resolve_drug_name(raw_name, drug_lookup)
                if not drug_name:
                    counters["unresolved_drugs"] += 1
                    continue
                existing = session.run(
                    f"""
                    MATCH (d:Drug {{generic_name: $drug_name}})-[r:{relationship_type}]->(:ElectrolyteEffect {{name: $effect_name}})
                    RETURN count(r) AS count
                    """,
                    drug_name=drug_name,
                    effect_name=effect_name,
                ).single()["count"]
                if existing:
                    counters["existing_edges_skipped"] += 1
                    continue
                session.run(
                    f"""
                    MATCH (d:Drug {{generic_name: $drug_name}})
                    MATCH (e:ElectrolyteEffect {{name: $effect_name}})
                    CREATE (d)-[r:{relationship_type}]->(e)
                    SET r.electrolyte = 'potassium',
                        r.source = 'electrolyte_expanded',
                        r.confidence = 0.80
                    """,
                    drug_name=drug_name,
                    effect_name=effect_name,
                )
                counters[f"created_{relationship_type.lower()}"] += 1
    return counters


def _apply_cns_rows(driver: Driver, database: str, drug_lookup: dict[str, str]) -> Counter:
    counters = Counter()
    with driver.session(database=database) as session:
        for raw_name in CNS_DEPRESSANT_DRUGS:
            drug_name = _resolve_drug_name(raw_name, drug_lookup)
            if not drug_name:
                counters["unresolved_drugs"] += 1
                continue
            existing = session.run(
                """
                MATCH (d:Drug {generic_name: $drug_name})-[r:CAUSES_CNS_DEPRESSION]->(:AdverseEffect {name: 'CNS depression'})
                RETURN count(r) AS count
                """,
                drug_name=drug_name,
            ).single()["count"]
            if existing:
                counters["existing_edges_skipped"] += 1
                continue
            session.run(
                """
                MATCH (d:Drug {generic_name: $drug_name})
                MATCH (cns:AdverseEffect {name: 'CNS depression'})
                CREATE (d)-[r:CAUSES_CNS_DEPRESSION]->(cns)
                SET r.source = 'cns_depressant_curated',
                    r.confidence = 0.80
                """,
                drug_name=drug_name,
            )
            counters["created_edges"] += 1
    return counters


def _run_counts(driver: Driver, database: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    with driver.session(database=database) as session:
        for key, query in COUNT_QUERIES.items():
            rows = [dict(record) for record in session.run(query)]
            result[key] = rows
    return result


def _log_summary(before: dict[str, Any], after: dict[str, Any]) -> None:
    LOGGER.info("Before/after enzyme substrate counts:")
    before_map = {row["name"]: row["count"] for row in before["substrates_by_enzyme"]}
    after_map = {row["name"]: row["count"] for row in after["substrates_by_enzyme"]}
    for enzyme_name in sorted(set(before_map) | set(after_map)):
        LOGGER.info(
            "  %s substrates: %s -> %s",
            enzyme_name,
            before_map.get(enzyme_name, 0),
            after_map.get(enzyme_name, 0),
        )
    before_indirect = before["indirect_pairs"][0]["count"] if before["indirect_pairs"] else 0
    after_indirect = after["indirect_pairs"][0]["count"] if after["indirect_pairs"] else 0
    before_qt = before["qt_drugs"][0]["count"] if before["qt_drugs"] else 0
    after_qt = after["qt_drugs"][0]["count"] if after["qt_drugs"] else 0
    LOGGER.info("Indirect inhibitor->substrate pairs: %s -> %s", before_indirect, after_indirect)
    LOGGER.info("QT drugs: %s -> %s", before_qt, after_qt)


def ingest(
    *,
    herb_data_path: Path,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_password: str,
    database: str,
    batch_size: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    driver = _create_driver(neo4j_uri, neo4j_user, neo4j_password)
    try:
        driver.verify_connectivity()
        before = _run_counts(driver, database)
        _ensure_mechanism_nodes_and_bridges(driver, database, batch_size)
        drug_lookup = _load_drug_lookup(driver, database)
        primekg_counts = _derive_primekg_edges(driver, database)
        LOGGER.info("PrimeKG-derived mechanism rows: %s", dict(primekg_counts))
        drug_lookup = _load_drug_lookup(driver, database)
        fda_counts = _apply_fda_rows(driver, database, drug_lookup)
        LOGGER.info("FDA DDI table rows applied: %s", dict(fda_counts))
        herb_counts = _apply_herb_literature(driver, database, herb_data_path)
        LOGGER.info("Herb literature rows applied: %s", dict(herb_counts))
        qt_counts = _apply_qt_rows(driver, database, drug_lookup)
        LOGGER.info("QT rows applied: %s", dict(qt_counts))
        electrolyte_counts = _apply_electrolyte_rows(driver, database, drug_lookup)
        LOGGER.info("Electrolyte rows applied: %s", dict(electrolyte_counts))
        cns_counts = _apply_cns_rows(driver, database, drug_lookup)
        LOGGER.info("CNS depressant rows applied: %s", dict(cns_counts))
        after = _run_counts(driver, database)
        _log_summary(before, after)
        return before, after
    finally:
        driver.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--herb-data-file", type=Path, default=DEFAULT_HERB_DATA_PATH)
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
        herb_data_path=args.herb_data_file,
        neo4j_uri=args.neo4j_uri,
        neo4j_user=args.neo4j_user,
        neo4j_password=args.neo4j_password,
        database=args.neo4j_database,
        batch_size=args.batch_size,
    )


if __name__ == "__main__":
    main()
