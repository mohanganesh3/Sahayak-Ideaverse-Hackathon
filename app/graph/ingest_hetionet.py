"""Ingest the Hetionet v1.0 biomedical knowledge graph into Neo4j."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5_000
DEFAULT_PROGRESS_EVERY = 100_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

NODE_KIND_ALIASES = {
    "Anatomy": "Anatomy",
    "Biological Process": "BiologicalProcess",
    "Cellular Component": "CellularComponent",
    "Compound": "Compound",
    "Disease": "Disease",
    "Gene": "Gene",
    "Molecular Function": "MolecularFunction",
    "Pathway": "Pathway",
    "Pharmacologic Class": "PharmacologicClass",
    "Side Effect": "SideEffect",
    "Symptom": "Symptom",
}

DRUG_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT drug_generic_name IF NOT EXISTS "
    "FOR (d:Drug) REQUIRE d.generic_name IS UNIQUE"
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


def _default_data_path() -> Path:
    explicit_path = os.getenv("HETIONET_DATA_PATH")
    if explicit_path:
        return Path(explicit_path).expanduser()

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "hetionet" / "hetionet-v1.0.json"

    home_path = Path.home() / "sahayak-data" / "hetionet" / "hetionet-v1.0.json"
    if home_path.exists():
        return home_path

    repo_path = (
        Path(__file__).resolve().parents[3] / "sahayak-data" / "hetionet" / "hetionet-v1.0.json"
    )
    if repo_path.exists():
        return repo_path

    return home_path


DEFAULT_DATA_PATH = _default_data_path()


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


def _normalize_property_name(name: str) -> str:
    normalized = _NON_ALNUM_RE.sub("_", name.casefold()).strip("_")
    return normalized or "value"


def _sanitize_relationship_type(name: str) -> str:
    sanitized = _NON_ALNUM_RE.sub("_", name.casefold()).strip("_").upper()
    return sanitized or "RELATED_TO"


def _cypher_identifier(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def _labels_for_kind(kind: str) -> tuple[str, tuple[str, ...]]:
    primary = NODE_KIND_ALIASES.get(kind)
    if primary is None:
        parts = [part for part in _NON_ALNUM_RE.split(kind) if part]
        primary = "".join(part.capitalize() for part in parts) or "Entity"
    labels = [primary]
    if kind != primary:
        labels.append(kind)
    return primary, tuple(labels)


def _coerce_property_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return _clean_text(value)
    if isinstance(value, list):
        coerced_items: list[Any] = []
        for item in value:
            coerced = _coerce_property_value(item)
            if coerced is not None:
                coerced_items.append(coerced)
        return coerced_items or None
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def _prefixed_properties(
    data: dict[str, Any] | None,
    *,
    prefix: str,
    skip_keys: set[str] | None = None,
) -> dict[str, Any]:
    if not data:
        return {}

    skip_keys = skip_keys or set()
    properties: dict[str, Any] = {}
    for key, value in data.items():
        normalized = _normalize_property_name(key)
        if normalized in skip_keys:
            continue
        coerced = _coerce_property_value(value)
        if coerced is not None:
            properties[f"{prefix}{normalized}"] = coerced
    return properties


def _resolve_data_path(data_path: Path) -> Path:
    resolved = data_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Hetionet JSON path does not exist: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"Hetionet path must be a JSON file: {resolved}")
    if resolved.suffix.casefold() != ".json":
        raise ValueError(f"Hetionet file must end with .json: {resolved}")
    return resolved


def _load_hetionet_payload(path: Path) -> dict[str, Any]:
    last_error: UnicodeDecodeError | None = None
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            with path.open("r", encoding=encoding, errors="strict") as handle:
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


def _node_constraint_query(label: str) -> str:
    constraint_name = f"hetionet_{_normalize_property_name(label)}_identifier"
    return (
        f"CREATE CONSTRAINT {_cypher_identifier(constraint_name)} IF NOT EXISTS "
        f"FOR (n:{_cypher_identifier(label)}) REQUIRE n.identifier IS UNIQUE"
    )


def _ensure_schema(driver: Driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run(DRUG_CONSTRAINT_QUERY).consume()
        for label in sorted(set(NODE_KIND_ALIASES.values())):
            session.run(_node_constraint_query(label)).consume()


def _load_existing_drug_maps(driver: Driver, database: str) -> tuple[dict[str, str], dict[str, str]]:
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

            for key in (
                props.get("drugbank_id"),
                props.get("primekg_id"),
                props.get("hetionet_id"),
                props.get("identifier"),
            ):
                cleaned = _clean_text(key)
                if cleaned:
                    by_identifier[cleaned] = generic_name

    return by_identifier, by_name


def _match_existing_drug(
    *,
    identifier: str,
    name: str,
    by_identifier: dict[str, str],
    by_name: dict[str, str],
) -> str | None:
    if identifier in by_identifier:
        return by_identifier[identifier]

    normalized_name = _normalize_lookup_key(name)
    if normalized_name:
        return by_name.get(normalized_name)
    return None


def _build_general_node_query(labels: tuple[str, ...]) -> str:
    label_clause = "".join(f":{_cypher_identifier(label)}" for label in labels)
    return f"""
    UNWIND $rows AS row
    MERGE (node{label_clause} {{identifier: row.identifier}})
    SET node += row.properties
    """


COMPOUND_DRUG_BATCH_QUERY = f"""
UNWIND $rows AS row
MERGE (drug:{_cypher_identifier('Drug')} {{generic_name: row.generic_name}})
ON CREATE SET {DRUG_DEFAULTS_CYPHER}
SET drug:{_cypher_identifier('Compound')}
SET drug += row.properties
"""


def _build_edge_query(source_label: str, target_label: str, relationship_type: str) -> str:
    return f"""
    UNWIND $rows AS row
    MATCH (source:{_cypher_identifier(source_label)} {{identifier: row.source_identifier}})
    MATCH (target:{_cypher_identifier(target_label)} {{identifier: row.target_identifier}})
    MERGE (source)-[relationship:{_cypher_identifier(relationship_type)} {{source: 'hetionet'}}]->(target)
    SET relationship += row.properties
    """


def _write_batch(tx: ManagedTransaction, query: str, rows: list[dict[str, Any]]) -> int:
    tx.run(query, rows=rows).consume()
    return len(rows)


def _node_properties(node: dict[str, Any]) -> dict[str, Any]:
    identifier = _clean_text(node.get("identifier"))
    name = _clean_text(node.get("name"))
    kind = _clean_text(node.get("kind"))
    data = node.get("data") or {}

    if not identifier or not name or not kind:
        raise ValueError("Hetionet node is missing identifier, name, or kind.")

    properties = {
        "identifier": identifier,
        "name": name,
        "dataset": "hetionet",
        "hetionet_kind": kind,
    }

    for field in ("source", "url", "license", "inchi", "inchikey"):
        cleaned = _clean_text(data.get(field))
        if cleaned:
            properties[field] = cleaned

    properties.update(
        _prefixed_properties(
            data,
            prefix="meta_",
            skip_keys={"source", "url", "license", "inchi", "inchikey"},
        )
    )
    return properties


def _compound_drug_row(node: dict[str, Any], generic_name: str) -> dict[str, Any]:
    properties = _node_properties(node)
    name = properties.pop("name", None)
    properties["generic_name"] = generic_name
    properties["hetionet_id"] = properties["identifier"]
    properties["drugbank_id"] = properties["identifier"]
    if name:
        properties["hetionet_name"] = name
    return {"generic_name": generic_name, "properties": properties}


def _general_node_row(node: dict[str, Any]) -> tuple[tuple[str, ...], dict[str, Any]]:
    kind = _clean_text(node.get("kind"))
    if not kind:
        raise ValueError("Hetionet node is missing kind.")
    _primary, labels = _labels_for_kind(kind)
    properties = _node_properties(node)
    return labels, {"identifier": properties["identifier"], "properties": properties}


def _edge_row(edge: dict[str, Any]) -> tuple[tuple[str, str, str], dict[str, Any]]:
    source_id = edge.get("source_id")
    target_id = edge.get("target_id")
    kind = _clean_text(edge.get("kind"))
    direction = _clean_text(edge.get("direction")) or "forward"
    data = edge.get("data") or {}

    if not isinstance(source_id, list) or len(source_id) != 2:
        raise ValueError("Hetionet edge has an invalid source_id payload.")
    if not isinstance(target_id, list) or len(target_id) != 2:
        raise ValueError("Hetionet edge has an invalid target_id payload.")
    if not kind:
        raise ValueError("Hetionet edge is missing a kind.")

    source_kind = _clean_text(source_id[0])
    target_kind = _clean_text(target_id[0])
    source_identifier = _clean_text(source_id[1])
    target_identifier = _clean_text(target_id[1])
    if not source_kind or not target_kind or not source_identifier or not target_identifier:
        raise ValueError("Hetionet edge is missing source/target kind or identifier.")

    source_label, _ = _labels_for_kind(source_kind)
    target_label, _ = _labels_for_kind(target_kind)
    relationship_type = _sanitize_relationship_type(kind)

    properties: dict[str, Any] = {
        "source": "hetionet",
        "hetionet_kind": kind,
        "direction": direction,
        "source_kind": source_kind,
        "target_kind": target_kind,
    }
    properties.update(_prefixed_properties(data, prefix="meta_"))

    return (
        (source_label, target_label, relationship_type),
        {
            "source_identifier": source_identifier,
            "target_identifier": target_identifier,
            "properties": properties,
        },
    )


def ingest(
    driver: Driver,
    data_path: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> int:
    """Load Hetionet v1.0 into Neo4j.

    Args:
        driver: Active Neo4j driver.
        data_path: Path to ``hetionet-v1.0.json``.
        batch_size: Number of rows to write per Neo4j batch.
        progress_every: Log progress after every N nodes/edges scanned.
        database: Neo4j database name.

    Returns:
        Number of Hetionet relationships written or merged.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if progress_every <= 0:
        raise ValueError("progress_every must be a positive integer")

    resolved_path = _resolve_data_path(data_path)
    payload = _load_hetionet_payload(resolved_path)

    nodes = payload.get("nodes")
    edges = payload.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list):
        raise ValueError("Hetionet JSON must contain top-level 'nodes' and 'edges' lists.")

    _ensure_schema(driver, database)
    existing_drug_ids, existing_drug_names = _load_existing_drug_maps(driver, database)

    node_query_cache: dict[tuple[str, ...], str] = {}
    edge_query_cache: dict[tuple[str, str, str], str] = {}

    with driver.session(database=database) as session:
        compound_drug_rows: list[dict[str, Any]] = []
        general_node_rows: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        node_count = 0
        matched_compounds = 0

        for node in nodes:
            node_count += 1
            identifier = _clean_text(node.get("identifier"))
            name = _clean_text(node.get("name"))
            kind = _clean_text(node.get("kind"))
            if not identifier or not name or not kind:
                raise ValueError(f"Malformed Hetionet node at position {node_count}: {node!r}")

            if kind == "Compound":
                matched_name = _match_existing_drug(
                    identifier=identifier,
                    name=name,
                    by_identifier=existing_drug_ids,
                    by_name=existing_drug_names,
                )
                if matched_name:
                    compound_drug_rows.append(_compound_drug_row(node, matched_name))
                    matched_compounds += 1
                    if len(compound_drug_rows) >= batch_size:
                        session.execute_write(_write_batch, COMPOUND_DRUG_BATCH_QUERY, compound_drug_rows)
                        compound_drug_rows.clear()
                else:
                    labels, row = _general_node_row(node)
                    general_node_rows[labels].append(row)
                    if len(general_node_rows[labels]) >= batch_size:
                        query = node_query_cache.setdefault(labels, _build_general_node_query(labels))
                        session.execute_write(_write_batch, query, general_node_rows[labels])
                        general_node_rows[labels].clear()
            else:
                labels, row = _general_node_row(node)
                general_node_rows[labels].append(row)
                if len(general_node_rows[labels]) >= batch_size:
                    query = node_query_cache.setdefault(labels, _build_general_node_query(labels))
                    session.execute_write(_write_batch, query, general_node_rows[labels])
                    general_node_rows[labels].clear()

            if node_count % progress_every == 0:
                LOGGER.info(
                    "Processed %s Hetionet nodes. Compound->Drug matches so far: %s.",
                    f"{node_count:,}",
                    f"{matched_compounds:,}",
                )

        if compound_drug_rows:
            session.execute_write(_write_batch, COMPOUND_DRUG_BATCH_QUERY, compound_drug_rows)
        for labels, rows in general_node_rows.items():
            if rows:
                query = node_query_cache.setdefault(labels, _build_general_node_query(labels))
                session.execute_write(_write_batch, query, rows)

        LOGGER.info(
            "Hetionet node load complete: %s nodes processed, %s Compound nodes attached to existing Drug nodes.",
            f"{node_count:,}",
            f"{matched_compounds:,}",
        )

        pending_edges: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        edge_count = 0
        written_edges = 0
        for edge in edges:
            edge_count += 1
            query_key, row = _edge_row(edge)
            pending_edges[query_key].append(row)
            if len(pending_edges[query_key]) >= batch_size:
                query = edge_query_cache.setdefault(query_key, _build_edge_query(*query_key))
                written_edges += session.execute_write(_write_batch, query, pending_edges[query_key])
                pending_edges[query_key].clear()

            if edge_count % progress_every == 0:
                LOGGER.info("Processed %s Hetionet edges.", f"{edge_count:,}")

        for query_key, rows in pending_edges.items():
            if rows:
                query = edge_query_cache.setdefault(query_key, _build_edge_query(*query_key))
                written_edges += session.execute_write(_write_batch, query, rows)

    LOGGER.info(
        "Hetionet ingestion complete: %s nodes scanned, %s edges written or merged.",
        f"{node_count:,}",
        f"{written_edges:,}",
    )
    return written_edges


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest Hetionet v1.0 JSON into Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help="Path to hetionet-v1.0.json.",
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password.")
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Number of nodes/edges to write per batch.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="Log progress after every N nodes or edges processed.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    """CLI entry point for Hetionet ingestion."""
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
            args.data_path,
            batch_size=args.batch_size,
            progress_every=args.progress_every,
            database=args.database,
        )
        LOGGER.info("Created or updated %s Hetionet relationships.", f"{ingested:,}")
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
