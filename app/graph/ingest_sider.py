"""Ingest SIDER drug–side-effect data into Neo4j.

SIDER (Side Effect Resource) links drugs to their known side effects using
STITCH compound IDs and MedDRA terminology.  Only drugs that already exist in
the graph are linked — no new Drug nodes are created.

Data file: meddra_all_se.tsv (headerless, tab-separated, 6 columns)
  Col 0: STITCH flat compound ID   (e.g. CID100000085)
  Col 1: STITCH stereo compound ID (e.g. CID000010917)
  Col 2: UMLS concept ID for the side-effect label
  Col 3: MedDRA concept type (LLT or PT)
  Col 4: UMLS concept ID for the MedDRA term
  Col 5: Side-effect name          (e.g. "Abdominal cramps")
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction
import requests

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5_000
DEFAULT_PROGRESS_EVERY = 50_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

TSV_FILE_NAME = "meddra_all_se.tsv"
FREQUENCY_FILE_CANDIDATES = ("meddra_freq.tsv", "meddra_freq.tsv.gz")
PUBCHEM_CACHE_NAME = ".pubchem_cid_to_drug.json"
PUBCHEM_BATCH_SIZE = 50
PUBCHEM_TIMEOUT_SECONDS = 60

INGEST_BATCH_QUERY = """
UNWIND $rows AS row
MATCH (drug:Drug {generic_name: row.drug_name})
MERGE (se:SideEffect {name: row.side_effect})
MERGE (drug)-[r:MAY_CAUSE {source: 'sider'}]->(se)
SET r.umls_id = row.umls_id,
    r.meddra_type = row.meddra_type,
    r.frequency = coalesce(row.frequency, r.frequency),
    r.frequency_category = coalesce(row.frequency_category, r.frequency_category)
"""


def _default_data_dir() -> Path:
    explicit_dir = os.getenv("SIDER_DATA_DIR")
    if explicit_dir:
        return Path(explicit_dir).expanduser()

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "sider"

    home_dir = Path.home() / "IDEAVERSE" / "sahayak-data" / "sider"
    if home_dir.exists():
        return home_dir

    repo_dir = Path(__file__).resolve().parents[3] / "sahayak-data" / "sider"
    if repo_dir.exists():
        return repo_dir

    return home_dir


DEFAULT_DATA_DIR = _default_data_dir()


def _clean_text(value: str | None) -> str | None:
    """Collapse whitespace and strip surrounding blanks."""
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\x00", " ")).strip()
    return cleaned or None


def _normalize_lookup_key(value: str | None) -> str | None:
    """Lower-case, strip non-alphanumeric chars — used for fuzzy Drug matching."""
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
            "calcium",
            "magnesium",
            "succinate",
            "tartrate",
            "phosphate",
            "acetate",
            "maleate",
            "sulfate",
            "citrate",
            "acid",
        }
    ]
    return " ".join(tokens).strip() or normalized


def _parse_stitch_to_pubchem(stitch_id: str) -> int | None:
    """Extract PubChem CID from a STITCH compound ID.

    STITCH flat  IDs look like CID1XXXXXXXX  (PubChem CID = int(XXXXXXXX))
    STITCH stereo IDs look like CID0XXXXXXXX (PubChem CID = int(XXXXXXXX))
    """
    if not stitch_id or len(stitch_id) < 5 or not stitch_id.startswith("CID"):
        return None
    try:
        return int(stitch_id[4:])
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Drug lookup helpers
# ---------------------------------------------------------------------------

def _load_drug_lookup(
    driver: Driver,
    database: str,
) -> tuple[dict[str, str], dict[str, str]]:
    """Return exact and qualifier-stripped Drug name maps from Neo4j.

    Returns:
        by_name: {normalized_generic_name -> generic_name}
        by_stripped_name: {qualifier_stripped_name -> generic_name}
    """
    by_name: dict[str, str] = {}
    by_stripped_name: dict[str, str] = {}

    query = "MATCH (d:Drug) RETURN d.generic_name AS generic_name"
    with driver.session(database=database) as session:
        for record in session.run(query):
            generic_name = _clean_text(record["generic_name"])
            if not generic_name:
                continue

            norm = _normalize_lookup_key(generic_name)
            if norm:
                by_name[norm] = generic_name
            stripped = _strip_qualifiers(generic_name)
            if stripped:
                by_stripped_name.setdefault(stripped, generic_name)

    LOGGER.info(
        "Loaded %d Drug names and %d stripped Drug aliases for SIDER matching.",
        len(by_name),
        len(by_stripped_name),
    )
    return by_name, by_stripped_name


def _load_pubchem_cache(cache_path: Path) -> dict[str, str | None]:
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict):
            return {str(key): (value if value is None else str(value)) for key, value in payload.items()}
    except (OSError, json.JSONDecodeError) as exc:
        LOGGER.warning("Ignoring unreadable SIDER PubChem cache %s: %s", cache_path, exc)
    return {}


def _save_pubchem_cache(cache_path: Path, cache: dict[str, str | None]) -> None:
    try:
        with cache_path.open("w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=True, indent=2, sort_keys=True)
    except OSError as exc:
        LOGGER.warning("Could not write SIDER PubChem cache %s: %s", cache_path, exc)


def _fetch_pubchem_synonyms_batch(cids: list[str]) -> dict[str, list[str]]:
    if not cids:
        return {}

    url = (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/cid/"
        f"{','.join(cids)}/synonyms/JSON"
    )
    response = requests.get(url, timeout=PUBCHEM_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    info_list = payload.get("InformationList", {}).get("Information", [])
    synonyms_by_cid: dict[str, list[str]] = {cid: [] for cid in cids}
    for item in info_list:
        cid = str(item.get("CID", ""))
        if cid:
            synonyms_by_cid[cid] = item.get("Synonym", []) or []
    return synonyms_by_cid


def _fetch_pubchem_synonyms(cids: list[str]) -> dict[str, list[str]]:
    try:
        return _fetch_pubchem_synonyms_batch(cids)
    except requests.RequestException as exc:
        if len(cids) == 1:
            LOGGER.warning("PubChem synonym lookup failed for CID %s: %s", cids[0], exc)
            return {cids[0]: []}
        midpoint = len(cids) // 2
        left = _fetch_pubchem_synonyms(cids[:midpoint])
        right = _fetch_pubchem_synonyms(cids[midpoint:])
        return left | right


def _iter_unique_pubchem_cids(tsv_path: Path) -> set[str]:
    cids: set[str] = set()
    with tsv_path.open("r", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for cols in reader:
            if len(cols) < 2:
                continue
            for stitch_id in (cols[0].strip(), cols[1].strip()):
                pubchem_cid = _parse_stitch_to_pubchem(stitch_id)
                if pubchem_cid is not None:
                    cids.add(str(pubchem_cid))
    return cids


def _resolve_synonyms_to_drug(
    synonyms: list[str],
    by_name: dict[str, str],
    by_stripped_name: dict[str, str],
) -> str | None:
    for synonym in synonyms:
        norm = _normalize_lookup_key(synonym)
        if norm and norm in by_name:
            return by_name[norm]
        stripped = _strip_qualifiers(synonym)
        if stripped and stripped in by_stripped_name:
            return by_stripped_name[stripped]
    return None


def _build_cid_to_drug_map(
    tsv_path: Path,
    data_dir: Path,
    by_name: dict[str, str],
    by_stripped_name: dict[str, str],
) -> dict[str, str | None]:
    cache_path = data_dir / PUBCHEM_CACHE_NAME
    cache = _load_pubchem_cache(cache_path)

    all_cids = sorted(_iter_unique_pubchem_cids(tsv_path))
    missing_cids = [cid for cid in all_cids if cid not in cache]
    LOGGER.info(
        "SIDER PubChem resolution: %s cached CID mappings, %s CIDs to fetch.",
        f"{len(cache):,}",
        f"{len(missing_cids):,}",
    )

    for start in range(0, len(missing_cids), PUBCHEM_BATCH_SIZE):
        batch = missing_cids[start : start + PUBCHEM_BATCH_SIZE]
        synonyms_by_cid = _fetch_pubchem_synonyms(batch)
        for cid in batch:
            cache[cid] = _resolve_synonyms_to_drug(
                synonyms_by_cid.get(cid, []),
                by_name,
                by_stripped_name,
            )
        if (start // PUBCHEM_BATCH_SIZE + 1) % 10 == 0 or start + PUBCHEM_BATCH_SIZE >= len(missing_cids):
            LOGGER.info(
                "Resolved %s/%s missing PubChem CID batches for SIDER.",
                f"{min(start + PUBCHEM_BATCH_SIZE, len(missing_cids)):,}",
                f"{len(missing_cids):,}",
            )

    if missing_cids:
        _save_pubchem_cache(cache_path, cache)

    return cache


def _resolve_drug(
    stitch_flat: str,
    stitch_stereo: str,
    cid_to_drug: dict[str, str | None],
) -> str | None:
    """Resolve a SIDER STITCH flat/stereo pair to an existing Drug name."""
    for sid in (stitch_stereo, stitch_flat):
        pubchem_cid = _parse_stitch_to_pubchem(sid)
        if pubchem_cid is None:
            continue
        resolved = cid_to_drug.get(str(pubchem_cid))
        if resolved:
            return resolved
    return None


def _classify_frequency(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric >= 0.1:
        return "common"
    if numeric >= 0.01:
        return "uncommon"
    return "rare"


def _open_frequency_file(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("r", encoding="utf-8", errors="replace")


def _load_frequency_map(data_dir: Path) -> dict[tuple[str, str], dict[str, str | None]]:
    frequency_path = next((data_dir / name for name in FREQUENCY_FILE_CANDIDATES if (data_dir / name).exists()), None)
    if frequency_path is None:
        LOGGER.warning(
            "SIDER frequency file not found under %s; MAY_CAUSE edges will not receive SIDER frequency values.",
            data_dir,
        )
        return {}

    frequency_map: dict[tuple[str, str], dict[str, str | None]] = {}
    with _open_frequency_file(frequency_path) as fh:
        reader = csv.reader(fh, delimiter="\t")
        for cols in reader:
            if len(cols) < 10:
                continue
            stitch_flat = cols[0].strip()
            frequency_value = _clean_text(cols[4])
            side_effect = _clean_text(cols[9])
            if not stitch_flat or not side_effect:
                continue
            frequency_map[(stitch_flat, side_effect)] = {
                "frequency": frequency_value,
                "frequency_category": _classify_frequency(frequency_value),
            }

    LOGGER.info(
        "Loaded %s SIDER frequency rows from %s.",
        f"{len(frequency_map):,}",
        frequency_path.name,
    )
    return frequency_map


# ---------------------------------------------------------------------------
# Neo4j write helpers
# ---------------------------------------------------------------------------

def _ensure_schema(driver: Driver, database: str) -> None:
    del driver, database


def _write_batch(tx: ManagedTransaction, rows: list[dict[str, Any]]) -> int:
    tx.run(INGEST_BATCH_QUERY, rows=rows).consume()
    return len(rows)


# ---------------------------------------------------------------------------
# Main ingestion
# ---------------------------------------------------------------------------

def ingest(
    driver: Driver,
    data_dir: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> dict[str, int]:
    """Load SIDER meddra_all_se.tsv into Neo4j as MAY_CAUSE edges.

    Only PT (Preferred Term) rows are used; LLT rows are skipped to avoid
    duplicate side-effect entries.  Only drugs already present in the graph
    are linked.

    Returns:
        Dict with source_rows, pt_rows, matched_drugs, skipped_rows,
        unique_edges, edges_written.
    """
    resolved_dir = data_dir.expanduser().resolve()
    tsv_path = resolved_dir / TSV_FILE_NAME if resolved_dir.is_dir() else resolved_dir
    if not tsv_path.exists():
        raise FileNotFoundError(f"SIDER data file not found: {tsv_path}")

    LOGGER.info("Loading existing Drug nodes from Neo4j for matching...")
    by_name, by_stripped_name = _load_drug_lookup(driver, database)
    cid_to_drug = _build_cid_to_drug_map(tsv_path, resolved_dir, by_name, by_stripped_name)
    frequency_map = _load_frequency_map(resolved_dir)

    _ensure_schema(driver, database)

    # Phase 1 — stream TSV, filter to PT rows, resolve drugs, deduplicate
    LOGGER.info("Phase 1: Scanning %s ...", tsv_path)
    unique_edges: dict[tuple[str, str], dict[str, str]] = {}
    source_rows = 0
    pt_rows = 0
    skipped_llt = 0
    skipped_no_match = 0
    skipped_malformed = 0
    matched_stitch_ids: set[str] = set()
    unmatched_stitch_ids: set[str] = set()

    with open(tsv_path, "r", encoding="utf-8", errors="replace") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for cols in reader:
            source_rows += 1

            if len(cols) < 6:
                skipped_malformed += 1
                if skipped_malformed <= 5:
                    LOGGER.warning(
                        "Skipping malformed row %d (only %d columns).",
                        source_rows,
                        len(cols),
                    )
                continue

            stitch_flat = cols[0].strip()
            stitch_stereo = cols[1].strip()
            meddra_type = cols[3].strip().upper()
            umls_id = cols[4].strip()
            side_effect = _clean_text(cols[5])

            # Only keep PT (Preferred Term) rows
            if meddra_type != "PT":
                skipped_llt += 1
                continue

            pt_rows += 1

            if not side_effect:
                skipped_malformed += 1
                continue

            drug_name = _resolve_drug(
                stitch_flat,
                stitch_stereo,
                cid_to_drug,
            )

            if drug_name is None:
                skipped_no_match += 1
                unmatched_stitch_ids.add(stitch_flat)
                continue

            matched_stitch_ids.add(stitch_flat)

            edge_key = (drug_name, side_effect)
            if edge_key not in unique_edges:
                frequency_data = (
                    frequency_map.get((stitch_flat, side_effect))
                    or frequency_map.get((stitch_stereo, side_effect))
                    or {"frequency": None, "frequency_category": None}
                )
                unique_edges[edge_key] = {
                    "drug_name": drug_name,
                    "side_effect": side_effect,
                    "umls_id": umls_id,
                    "meddra_type": meddra_type,
                    "frequency": frequency_data["frequency"],
                    "frequency_category": frequency_data["frequency_category"],
                }

            if source_rows % progress_every == 0:
                LOGGER.info(
                    "Scanned %s rows (%s PT): %s unique edges, %s matched drugs, %s skipped.",
                    f"{source_rows:,}",
                    f"{pt_rows:,}",
                    f"{len(unique_edges):,}",
                    f"{len(matched_stitch_ids):,}",
                    f"{skipped_no_match:,}",
                )

    LOGGER.info(
        "Phase 1 complete: %s total rows, %s PT rows, %s LLT skipped, "
        "%s malformed, %s unique edges, %s matched STITCH IDs, %s unmatched.",
        f"{source_rows:,}",
        f"{pt_rows:,}",
        f"{skipped_llt:,}",
        f"{skipped_malformed:,}",
        f"{len(unique_edges):,}",
        f"{len(matched_stitch_ids):,}",
        f"{len(unmatched_stitch_ids):,}",
    )

    # Phase 2 — batch-write unique edges to Neo4j
    LOGGER.info("Phase 2: Writing %s MAY_CAUSE edges to Neo4j...", f"{len(unique_edges):,}")
    pending: list[dict[str, Any]] = []
    edges_written = 0

    for edge_row in unique_edges.values():
        pending.append(edge_row)

        if len(pending) >= batch_size:
            with driver.session(database=database) as session:
                edges_written += session.execute_write(_write_batch, pending)
            pending.clear()

    if pending:
        with driver.session(database=database) as session:
            edges_written += session.execute_write(_write_batch, pending)

    LOGGER.info(
        "SIDER ingestion complete: %s source rows, %s PT rows, %s unique edges written, "
        "%s matched STITCH IDs, %s unmatched STITCH IDs, %s LLT skipped, %s malformed.",
        f"{source_rows:,}",
        f"{pt_rows:,}",
        f"{edges_written:,}",
        f"{len(matched_stitch_ids):,}",
        f"{len(unmatched_stitch_ids):,}",
        f"{skipped_llt:,}",
        f"{skipped_malformed:,}",
    )

    return {
        "source_rows": source_rows,
        "pt_rows": pt_rows,
        "matched_drugs": len(matched_stitch_ids),
        "skipped_rows": skipped_no_match + skipped_llt + skipped_malformed,
        "unique_edges": len(unique_edges),
        "edges_written": edges_written,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest SIDER drug–side-effect data into Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing meddra_all_se.tsv (or path to the file itself).",
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password.")
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Number of edges to write per Neo4j transaction.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="Log progress after every N source rows.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    """CLI entry point for SIDER ingestion."""
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    driver = GraphDatabase.driver(
        args.neo4j_uri,
        auth=(args.neo4j_user, args.neo4j_password),
    )
    try:
        driver.verify_connectivity()
        stats = ingest(
            driver,
            args.data_dir,
            batch_size=args.batch_size,
            progress_every=args.progress_every,
            database=args.database,
        )
        LOGGER.info(
            "Final stats: %s unique MAY_CAUSE edges from %s matched drugs.",
            f"{stats['unique_edges']:,}",
            f"{stats['matched_drugs']:,}",
        )
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
