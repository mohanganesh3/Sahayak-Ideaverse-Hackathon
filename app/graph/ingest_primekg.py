"""Ingest the drug-relevant subset of PrimeKG into Neo4j."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, TextIO

from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5_000
DEFAULT_PROGRESS_EVERY = 100_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

READ_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

RELEVANT_RELATIONS = {
    "drug_drug",
    "contraindication",
    "indication",
    "off-label use",
    "drug_effect",
    "drug_protein",
}
PROTEIN_RELATIONSHIP_TYPES = {
    "target": "TARGETS",
    "enzyme": "METABOLIZED_BY",
    "transporter": "TRANSPORTED_BY",
    "carrier": "CARRIED_BY",
}

DRUG_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT drug_generic_name IF NOT EXISTS "
    "FOR (d:Drug) REQUIRE d.generic_name IS UNIQUE"
)
GENE_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT gene_identifier IF NOT EXISTS "
    "FOR (g:Gene) REQUIRE g.identifier IS UNIQUE"
)
DRUG_DEFAULTS_CYPHER = (
    "drug.rxcui = '', "
    "drug.drug_class = '', "
    "drug.is_nti = false, "
    "drug.is_beers = false, "
    "drug.anticholinergic_score = 0"
)
DRUG_A_DEFAULTS_CYPHER = (
    "drug_a.rxcui = '', "
    "drug_a.drug_class = '', "
    "drug_a.is_nti = false, "
    "drug_a.is_beers = false, "
    "drug_a.anticholinergic_score = 0"
)
DRUG_B_DEFAULTS_CYPHER = (
    "drug_b.rxcui = '', "
    "drug_b.drug_class = '', "
    "drug_b.is_nti = false, "
    "drug_b.is_beers = false, "
    "drug_b.anticholinergic_score = 0"
)

PRIMEKG_DDI_QUERY = f"""
UNWIND $rows AS row
MERGE (drug_a:Drug {{generic_name: row.drug_a_name}})
ON CREATE SET {DRUG_A_DEFAULTS_CYPHER}
SET drug_a.drugbank_id = coalesce(drug_a.drugbank_id, row.drug_a_id),
    drug_a.primekg_id = coalesce(drug_a.primekg_id, row.drug_a_id),
    drug_a.primekg_source = coalesce(drug_a.primekg_source, row.drug_a_source)
MERGE (drug_b:Drug {{generic_name: row.drug_b_name}})
ON CREATE SET {DRUG_B_DEFAULTS_CYPHER}
SET drug_b.drugbank_id = coalesce(drug_b.drugbank_id, row.drug_b_id),
    drug_b.primekg_id = coalesce(drug_b.primekg_id, row.drug_b_id),
    drug_b.primekg_source = coalesce(drug_b.primekg_source, row.drug_b_source)
MERGE (drug_a)-[interaction:INTERACTS_WITH {{source: 'primekg'}}]->(drug_b)
SET interaction.severity = coalesce(interaction.severity, 'unknown'),
    interaction.clinical_effect = coalesce(interaction.clinical_effect, row.display_relation),
    interaction.evidence_level = coalesce(interaction.evidence_level, 'knowledge_graph'),
    interaction.primekg_relation = row.relation,
    interaction.display_relation = row.display_relation,
    interaction.drug_a_id = coalesce(interaction.drug_a_id, row.drug_a_id),
    interaction.drug_b_id = coalesce(interaction.drug_b_id, row.drug_b_id)
"""

PRIMEKG_INDICATION_QUERY = f"""
UNWIND $rows AS row
MERGE (drug:Drug {{generic_name: row.drug_name}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER}
SET drug.drugbank_id = coalesce(drug.drugbank_id, row.drug_id),
    drug.primekg_id = coalesce(drug.primekg_id, row.drug_id),
    drug.primekg_source = coalesce(drug.primekg_source, row.drug_source)
MERGE (condition:Condition {{name: row.condition_name}})
SET condition.primekg_id = coalesce(condition.primekg_id, row.condition_id),
    condition.primekg_source = coalesce(condition.primekg_source, row.condition_source)
MERGE (drug)-[relationship:INDICATED_FOR {{source: 'primekg'}}]->(condition)
SET relationship.primekg_relation = row.relation,
    relationship.display_relation = row.display_relation,
    relationship.condition_id = coalesce(relationship.condition_id, row.condition_id),
    relationship.condition_source = coalesce(relationship.condition_source, row.condition_source)
"""

PRIMEKG_CONTRAINDICATION_QUERY = f"""
UNWIND $rows AS row
MERGE (drug:Drug {{generic_name: row.drug_name}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER}
SET drug.drugbank_id = coalesce(drug.drugbank_id, row.drug_id),
    drug.primekg_id = coalesce(drug.primekg_id, row.drug_id),
    drug.primekg_source = coalesce(drug.primekg_source, row.drug_source)
MERGE (condition:Condition {{name: row.condition_name}})
SET condition.primekg_id = coalesce(condition.primekg_id, row.condition_id),
    condition.primekg_source = coalesce(condition.primekg_source, row.condition_source)
MERGE (drug)-[relationship:CONTRAINDICATED_IN {{source: 'primekg'}}]->(condition)
SET relationship.reason = coalesce(relationship.reason, row.display_relation),
    relationship.primekg_relation = row.relation,
    relationship.display_relation = row.display_relation,
    relationship.condition_id = coalesce(relationship.condition_id, row.condition_id),
    relationship.condition_source = coalesce(relationship.condition_source, row.condition_source)
"""

PRIMEKG_OFF_LABEL_QUERY = f"""
UNWIND $rows AS row
MERGE (drug:Drug {{generic_name: row.drug_name}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER}
SET drug.drugbank_id = coalesce(drug.drugbank_id, row.drug_id),
    drug.primekg_id = coalesce(drug.primekg_id, row.drug_id),
    drug.primekg_source = coalesce(drug.primekg_source, row.drug_source)
MERGE (condition:Condition {{name: row.condition_name}})
SET condition.primekg_id = coalesce(condition.primekg_id, row.condition_id),
    condition.primekg_source = coalesce(condition.primekg_source, row.condition_source)
MERGE (drug)-[relationship:OFF_LABEL_FOR {{source: 'primekg'}}]->(condition)
SET relationship.primekg_relation = row.relation,
    relationship.display_relation = row.display_relation,
    relationship.condition_id = coalesce(relationship.condition_id, row.condition_id),
    relationship.condition_source = coalesce(relationship.condition_source, row.condition_source)
"""

PRIMEKG_SIDE_EFFECT_QUERY = f"""
UNWIND $rows AS row
MERGE (drug:Drug {{generic_name: row.drug_name}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER}
SET drug.drugbank_id = coalesce(drug.drugbank_id, row.drug_id),
    drug.primekg_id = coalesce(drug.primekg_id, row.drug_id),
    drug.primekg_source = coalesce(drug.primekg_source, row.drug_source)
MERGE (effect:SideEffect {{name: row.effect_name}})
SET effect.primekg_id = coalesce(effect.primekg_id, row.effect_id),
    effect.primekg_source = coalesce(effect.primekg_source, row.effect_source)
MERGE (drug)-[relationship:MAY_CAUSE {{source: 'primekg'}}]->(effect)
SET relationship.frequency = coalesce(relationship.frequency, 'unknown'),
    relationship.primekg_relation = row.relation,
    relationship.display_relation = row.display_relation,
    relationship.effect_id = coalesce(relationship.effect_id, row.effect_id),
    relationship.effect_source = coalesce(relationship.effect_source, row.effect_source)
"""


def _build_primekg_protein_query(relationship_type: str) -> str:
    return f"""
    UNWIND $rows AS row
    MERGE (drug:Drug {{generic_name: row.drug_name}})
    ON CREATE SET {DRUG_DEFAULTS_CYPHER}
    SET drug.drugbank_id = coalesce(drug.drugbank_id, row.drug_id),
        drug.primekg_id = coalesce(drug.primekg_id, row.drug_id),
        drug.primekg_source = coalesce(drug.primekg_source, row.drug_source)
    MERGE (gene:Gene {{identifier: row.gene_id}})
    SET gene:Protein,
        gene.name = coalesce(gene.name, row.gene_name),
        gene.source = coalesce(gene.source, row.gene_source),
        gene.primekg_id = coalesce(gene.primekg_id, row.gene_id)
    MERGE (drug)-[relationship:{relationship_type} {{source: 'primekg'}}]->(gene)
    SET relationship.primekg_relation = row.relation,
        relationship.display_relation = row.display_relation,
        relationship.drug_id = coalesce(relationship.drug_id, row.drug_id),
        relationship.gene_id = coalesce(relationship.gene_id, row.gene_id),
        relationship.gene_source = coalesce(relationship.gene_source, row.gene_source)
    """


def _default_data_dir() -> Path:
    explicit_dir = os.getenv("PRIMEKG_DATA_DIR")
    if explicit_dir:
        return Path(explicit_dir).expanduser()

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "primekg"

    home_dir = Path.home() / "sahayak-data" / "primekg"
    if home_dir.exists():
        return home_dir

    repo_dir = Path(__file__).resolve().parents[3] / "sahayak-data" / "primekg"
    if repo_dir.exists():
        return repo_dir

    return home_dir


DEFAULT_DATA_DIR = _default_data_dir()


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


def _detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _open_csv_file(
    path: Path,
    *,
    expected_delimiter: str | None = None,
) -> tuple[TextIO, csv.DictReader, str]:
    last_error: UnicodeDecodeError | None = None

    for encoding in READ_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as probe:
                sample = probe.read(8_192)
            delimiter = expected_delimiter or _detect_delimiter(sample)
            handle = path.open("r", encoding=encoding, errors="replace", newline="")
            reader = csv.DictReader(handle, delimiter=delimiter)
            LOGGER.info("Reading %s using encoding=%s delimiter=%r.", path.name, encoding, delimiter)
            return handle, reader, encoding
        except UnicodeDecodeError as exc:
            last_error = exc

    handle = path.open("r", encoding="utf-8", errors="replace", newline="")
    sample = handle.read(8_192)
    handle.seek(0)
    delimiter = expected_delimiter or _detect_delimiter(sample)
    reader = csv.DictReader(handle, delimiter=delimiter)
    LOGGER.warning(
        "Falling back to utf-8 replacement decoding for %s after decode errors: %s",
        path.name,
        last_error,
    )
    return handle, reader, "utf-8-replace"


def _require_files(data_dir: Path) -> dict[str, Path]:
    resolved = data_dir.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"PrimeKG path does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"PrimeKG path must be a directory: {resolved}")

    required = {
        "nodes": resolved / "nodes.csv",
        "edges": resolved / "edges.csv",
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"PrimeKG directory is missing required files: {', '.join(sorted(missing))}"
        )
    return required


def _ensure_schema(driver: Driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run(DRUG_CONSTRAINT_QUERY).consume()
        session.run(GENE_CONSTRAINT_QUERY).consume()


def _load_existing_drugs(driver: Driver, database: str) -> tuple[dict[str, str], dict[str, str]]:
    by_identifier: dict[str, str] = {}
    by_name: dict[str, str] = {}
    query = """
    MATCH (drug:Drug)
    RETURN properties(drug) AS props
    """
    with driver.session(database=database) as session:
        for record in session.run(query):
            props = record["props"] or {}
            generic_name = _clean_text(props.get("generic_name"))
            if not generic_name:
                continue

            normalized_name = _normalize_lookup_key(generic_name)
            if normalized_name:
                by_name[normalized_name] = generic_name

            for value in (
                props.get("drugbank_id"),
                props.get("primekg_id"),
                props.get("hetionet_id"),
                props.get("identifier"),
            ):
                cleaned = _clean_text(value)
                if cleaned:
                    by_identifier[cleaned] = generic_name
    return by_identifier, by_name


def _canonical_drug_name(
    *,
    node_id: str | None,
    node_name: str | None,
    by_identifier: dict[str, str],
    by_name: dict[str, str],
) -> str:
    cleaned_id = _clean_text(node_id)
    if cleaned_id and cleaned_id in by_identifier:
        return by_identifier[cleaned_id]

    cleaned_name = _clean_text(node_name)
    if not cleaned_name:
        raise ValueError("PrimeKG drug node is missing a name.")

    normalized_name = _normalize_lookup_key(cleaned_name)
    if normalized_name and normalized_name in by_name:
        return by_name[normalized_name]
    return cleaned_name


def _write_batch(tx: ManagedTransaction, query: str, rows: list[dict[str, Any]]) -> int:
    tx.run(query, rows=rows).consume()
    return len(rows)


def _load_node_index(nodes_path: Path) -> dict[str, dict[str, str]]:
    handle, reader, _encoding = _open_csv_file(nodes_path, expected_delimiter="\t")
    try:
        required_headers = {"node_index", "node_id", "node_type", "node_name", "node_source"}
        fieldnames = set(reader.fieldnames or [])
        missing = sorted(required_headers - fieldnames)
        if missing:
            raise ValueError(
                f"PrimeKG nodes.csv is missing required columns: {', '.join(missing)}"
            )

        node_index: dict[str, dict[str, str]] = {}
        for row in reader:
            index = _clean_text(row.get("node_index"))
            node_type = _clean_text(row.get("node_type"))
            node_name = _clean_text(row.get("node_name"))
            if not index or not node_type or not node_name:
                continue
            node_index[index] = {
                "node_id": _clean_text(row.get("node_id")) or "",
                "node_type": node_type,
                "node_name": node_name,
                "node_source": _clean_text(row.get("node_source")) or "",
            }
    finally:
        handle.close()
    return node_index


def _primekg_row_from_indices(
    raw_row: dict[str, str],
    node_index: dict[str, dict[str, str]],
) -> dict[str, str] | None:
    relation = _clean_text(raw_row.get("relation"))
    display_relation = _clean_text(raw_row.get("display_relation")) or relation or ""
    if not relation:
        return None

    if relation not in RELEVANT_RELATIONS:
        return None

    if {"x_index", "y_index"} <= set(raw_row):
        x_node = node_index.get(_clean_text(raw_row.get("x_index")) or "")
        y_node = node_index.get(_clean_text(raw_row.get("y_index")) or "")
        if not x_node or not y_node:
            return None
        return {
            "relation": relation,
            "display_relation": display_relation,
            "x_id": x_node["node_id"],
            "x_type": x_node["node_type"],
            "x_name": x_node["node_name"],
            "x_source": x_node["node_source"],
            "y_id": y_node["node_id"],
            "y_type": y_node["node_type"],
            "y_name": y_node["node_name"],
            "y_source": y_node["node_source"],
        }

    direct_fields = {"x_id", "x_type", "x_name", "x_source", "y_id", "y_type", "y_name", "y_source"}
    if direct_fields <= set(raw_row):
        normalized = {"relation": relation, "display_relation": display_relation}
        for key in direct_fields:
            normalized[key] = _clean_text(raw_row.get(key)) or ""
        return normalized

    raise ValueError(
        "PrimeKG edges.csv must contain either x_index/y_index columns or direct x_*/y_* columns."
    )


def _canonicalize_drug_pair(
    drug_a_name: str,
    drug_a_id: str,
    drug_a_source: str,
    drug_b_name: str,
    drug_b_id: str,
    drug_b_source: str,
) -> dict[str, str] | None:
    key_a = _normalize_lookup_key(drug_a_name)
    key_b = _normalize_lookup_key(drug_b_name)
    if not key_a or not key_b or key_a == key_b:
        return None

    if key_a <= key_b:
        return {
            "drug_a_name": drug_a_name,
            "drug_a_id": drug_a_id,
            "drug_a_source": drug_a_source,
            "drug_b_name": drug_b_name,
            "drug_b_id": drug_b_id,
            "drug_b_source": drug_b_source,
        }
    return {
        "drug_a_name": drug_b_name,
        "drug_a_id": drug_b_id,
        "drug_a_source": drug_b_source,
        "drug_b_name": drug_a_name,
        "drug_b_id": drug_a_id,
        "drug_b_source": drug_a_source,
    }


def ingest(
    driver: Driver,
    data_path: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> int:
    """Load the drug-relevant subset of PrimeKG into Neo4j.

    Args:
        driver: Active Neo4j driver.
        data_path: Path to the PrimeKG directory containing nodes.csv and edges.csv.
        batch_size: Number of rows to write per Neo4j batch.
        progress_every: Log progress after every N source edges scanned.
        database: Neo4j database name.

    Returns:
        Number of relevant PrimeKG relationship rows written or merged.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if progress_every <= 0:
        raise ValueError("progress_every must be a positive integer")

    files = _require_files(data_path)
    _ensure_schema(driver, database)
    existing_drug_ids, existing_drug_names = _load_existing_drugs(driver, database)
    node_index = _load_node_index(files["nodes"])
    LOGGER.info("Loaded %s PrimeKG node records into the in-memory index.", f"{len(node_index):,}")

    query_map = {
        "drug_drug": PRIMEKG_DDI_QUERY,
        "indication": PRIMEKG_INDICATION_QUERY,
        "contraindication": PRIMEKG_CONTRAINDICATION_QUERY,
        "off-label use": PRIMEKG_OFF_LABEL_QUERY,
        "drug_effect": PRIMEKG_SIDE_EFFECT_QUERY,
    }
    protein_query_cache: dict[str, str] = {}

    with driver.session(database=database) as session:
        pending_rows: dict[str, list[dict[str, str]]] = defaultdict(list)
        relation_counters = Counter()
        source_rows = 0
        relevant_rows = 0
        skipped_rows = 0

        handle, reader, _encoding = _open_csv_file(files["edges"], expected_delimiter=",")
        try:
            required_headers = {"relation", "display_relation"}
            fieldnames = set(reader.fieldnames or [])
            missing = sorted(required_headers - fieldnames)
            if missing:
                raise ValueError(
                    f"PrimeKG edges.csv is missing required columns: {', '.join(missing)}"
                )

            for raw_row in reader:
                source_rows += 1
                normalized = _primekg_row_from_indices(raw_row, node_index)
                if normalized is None:
                    continue

                relation = normalized["relation"]
                x_type = normalized["x_type"]
                y_type = normalized["y_type"]
                display_relation = normalized["display_relation"]

                batch_key: str | None = None
                row: dict[str, str] | None = None

                if relation == "drug_drug" and x_type == "drug" and y_type == "drug":
                    drug_a_name = _canonical_drug_name(
                        node_id=normalized["x_id"],
                        node_name=normalized["x_name"],
                        by_identifier=existing_drug_ids,
                        by_name=existing_drug_names,
                    )
                    drug_b_name = _canonical_drug_name(
                        node_id=normalized["y_id"],
                        node_name=normalized["y_name"],
                        by_identifier=existing_drug_ids,
                        by_name=existing_drug_names,
                    )
                    canonical = _canonicalize_drug_pair(
                        drug_a_name,
                        normalized["x_id"],
                        normalized["x_source"],
                        drug_b_name,
                        normalized["y_id"],
                        normalized["y_source"],
                    )
                    if canonical:
                        canonical["relation"] = relation
                        canonical["display_relation"] = display_relation
                        batch_key = relation
                        row = canonical

                elif relation in {"indication", "contraindication", "off-label use"}:
                    if x_type == "drug" and y_type == "disease":
                        drug_node = normalized, "x"
                        disease_node = normalized, "y"
                    elif x_type == "disease" and y_type == "drug":
                        drug_node = normalized, "y"
                        disease_node = normalized, "x"
                    else:
                        drug_node = None
                        disease_node = None

                    if drug_node and disease_node:
                        drug_source, drug_prefix = drug_node
                        disease_source, disease_prefix = disease_node
                        drug_name = _canonical_drug_name(
                            node_id=drug_source[f"{drug_prefix}_id"],
                            node_name=drug_source[f"{drug_prefix}_name"],
                            by_identifier=existing_drug_ids,
                            by_name=existing_drug_names,
                        )
                        batch_key = relation
                        row = {
                            "relation": relation,
                            "display_relation": display_relation,
                            "drug_name": drug_name,
                            "drug_id": drug_source[f"{drug_prefix}_id"],
                            "drug_source": drug_source[f"{drug_prefix}_source"],
                            "condition_name": disease_source[f"{disease_prefix}_name"],
                            "condition_id": disease_source[f"{disease_prefix}_id"],
                            "condition_source": disease_source[f"{disease_prefix}_source"],
                        }

                elif relation == "drug_effect":
                    if x_type == "drug" and y_type == "effect/phenotype":
                        drug_source, drug_prefix = normalized, "x"
                        effect_source, effect_prefix = normalized, "y"
                    elif x_type == "effect/phenotype" and y_type == "drug":
                        drug_source, drug_prefix = normalized, "y"
                        effect_source, effect_prefix = normalized, "x"
                    else:
                        drug_source = None
                        effect_source = None
                        drug_prefix = ""
                        effect_prefix = ""

                    if drug_source and effect_source:
                        drug_name = _canonical_drug_name(
                            node_id=drug_source[f"{drug_prefix}_id"],
                            node_name=drug_source[f"{drug_prefix}_name"],
                            by_identifier=existing_drug_ids,
                            by_name=existing_drug_names,
                        )
                        batch_key = relation
                        row = {
                            "relation": relation,
                            "display_relation": display_relation,
                            "drug_name": drug_name,
                            "drug_id": drug_source[f"{drug_prefix}_id"],
                            "drug_source": drug_source[f"{drug_prefix}_source"],
                            "effect_name": effect_source[f"{effect_prefix}_name"],
                            "effect_id": effect_source[f"{effect_prefix}_id"],
                            "effect_source": effect_source[f"{effect_prefix}_source"],
                        }

                elif relation == "drug_protein":
                    if x_type == "drug" and y_type == "gene/protein":
                        drug_source, drug_prefix = normalized, "x"
                        gene_source, gene_prefix = normalized, "y"
                    elif x_type == "gene/protein" and y_type == "drug":
                        drug_source, drug_prefix = normalized, "y"
                        gene_source, gene_prefix = normalized, "x"
                    else:
                        drug_source = None
                        gene_source = None
                        drug_prefix = ""
                        gene_prefix = ""

                    relationship_type = PROTEIN_RELATIONSHIP_TYPES.get(display_relation.casefold())
                    if drug_source and gene_source and relationship_type:
                        drug_name = _canonical_drug_name(
                            node_id=drug_source[f"{drug_prefix}_id"],
                            node_name=drug_source[f"{drug_prefix}_name"],
                            by_identifier=existing_drug_ids,
                            by_name=existing_drug_names,
                        )
                        batch_key = f"drug_protein:{relationship_type}"
                        row = {
                            "relation": relation,
                            "display_relation": display_relation,
                            "drug_name": drug_name,
                            "drug_id": drug_source[f"{drug_prefix}_id"],
                            "drug_source": drug_source[f"{drug_prefix}_source"],
                            "gene_name": gene_source[f"{gene_prefix}_name"],
                            "gene_id": gene_source[f"{gene_prefix}_id"],
                            "gene_source": gene_source[f"{gene_prefix}_source"],
                        }

                if batch_key is None or row is None:
                    skipped_rows += 1
                    continue

                pending_rows[batch_key].append(row)
                relevant_rows += 1
                relation_counters[batch_key] += 1

                if len(pending_rows[batch_key]) >= batch_size:
                    if batch_key in query_map:
                        query = query_map[batch_key]
                    else:
                        relationship_type = batch_key.split(":", 1)[1]
                        query = protein_query_cache.setdefault(
                            relationship_type,
                            _build_primekg_protein_query(relationship_type),
                        )
                    session.execute_write(_write_batch, query, pending_rows[batch_key])
                    pending_rows[batch_key].clear()

                if source_rows % progress_every == 0:
                    LOGGER.info(
                        "Processed %s PrimeKG edges. Relevant rows kept: %s. Skipped rows: %s.",
                        f"{source_rows:,}",
                        f"{relevant_rows:,}",
                        f"{skipped_rows:,}",
                    )
        finally:
            handle.close()

        written_rows = 0
        for batch_key, rows in pending_rows.items():
            if not rows:
                continue
            if batch_key in query_map:
                query = query_map[batch_key]
            else:
                relationship_type = batch_key.split(":", 1)[1]
                query = protein_query_cache.setdefault(
                    relationship_type,
                    _build_primekg_protein_query(relationship_type),
                )
            written_rows += session.execute_write(_write_batch, query, rows)

    LOGGER.info(
        "PrimeKG ingestion complete: %s source edges scanned, %s relevant rows written or merged, %s rows skipped.",
        f"{source_rows:,}",
        f"{relevant_rows:,}",
        f"{skipped_rows:,}",
    )
    for key, count in sorted(relation_counters.items()):
        LOGGER.info("PrimeKG kept %s rows for %s.", f"{count:,}", key)
    return relevant_rows


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest the drug-relevant PrimeKG subset into Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing PrimeKG nodes.csv and edges.csv.",
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password.")
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Number of relevant edge rows to write per batch.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="Log progress after every N source edges processed.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    """CLI entry point for PrimeKG ingestion."""
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        driver.verify_connectivity()
        ingested = ingest(
            driver,
            args.data_dir,
            batch_size=args.batch_size,
            progress_every=args.progress_every,
            database=args.database,
        )
        LOGGER.info("Created or updated %s PrimeKG relationship rows.", f"{ingested:,}")
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
