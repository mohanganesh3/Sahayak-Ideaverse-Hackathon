"""Backfill Drug.drug_class and Drug.rxcui for high-priority medications."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import string
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 500
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_BRAND_MAP_PATH = Path(__file__).resolve().parents[1] / "data" / "indian_brand_map.json"

RXNORM_RXCUI_URL = "https://rxnav.nlm.nih.gov/REST/rxcui.json?search=2&name={name}"
DEFAULT_MAX_RXCUI_LOOKUPS = 60
DEFAULT_RXCUI_WORKERS = 8

READ_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

GENERIC_NAME_OVERRIDES = {
    "paracetamol": "Acetaminophen",
    "amoxycillin": "Amoxicillin",
    "thyroxine": "Levothyroxine",
    "salbutamol": "Albuterol",
    "levosalbutamol": "Levalbuterol",
    "lignocaine": "Lidocaine",
    "beclometasone": "Beclomethasone",
    "cefalexin": "Cephalexin",
    "glyceryl trinitrate": "Nitroglycerin",
    "adrenaline": "Epinephrine",
    "noradrenaline": "Norepinephrine",
}

FAMILY_CLASS_RULES: tuple[tuple[str, str], ...] = (
    ("statin", r".*statin$"),
    ("ppi", r".*prazole$"),
    ("ace inhibitor", r".*pril$"),
    ("arb", r".*sartan$"),
    ("beta blocker", r".*olol$"),
    ("ccb", r".*dipine$"),
    ("dpp-4 inhibitor", r".*gliptin$"),
    ("sglt2 inhibitor", r".*gliflozin$"),
)

EXACT_FAMILY_CLASS_RULES: tuple[tuple[str, set[str]], ...] = (
    ("biguanide", {"metformin"}),
    ("sulfonylurea", {"glimepiride", "gliclazide", "glipizide", "glyburide", "glibenclamide"}),
    ("antiplatelet", {"aspirin", "clopidogrel", "prasugrel", "ticagrelor", "cilostazol", "dipyridamole"}),
    ("anticoagulant", {"warfarin", "dabigatran", "rivaroxaban", "apixaban", "edoxaban", "betrixaban"}),
    ("loop diuretic", {"furosemide", "torsemide", "bumetanide"}),
    ("diuretic", {"hydrochlorothiazide", "chlorthalidone", "indapamide", "amiloride", "triamterene", "spironolactone"}),
    ("ccb", {"diltiazem", "verapamil"}),
    ("h2 blocker", {"cimetidine", "famotidine", "nizatidine", "ranitidine"}),
    ("thyroid", {"levothyroxine", "liothyronine"}),
    ("bronchodilator", {"theophylline", "tiotropium", "ipratropium", "albuterol", "levalbuterol"}),
    ("cox-2 inhibitor", {"celecoxib", "etoricoxib"}),
    ("bph", {"tamsulosin", "silodosin", "alfuzosin"}),
    ("5-alpha reductase inhibitor", {"finasteride", "dutasteride"}),
    ("laxative", {"lactulose", "bisacodyl", "psyllium", "polyethylene glycol", "senna"}),
)

CLASS_UPDATE_QUERY = """
UNWIND $rows AS row
MATCH (drug:Drug {generic_name: row.drug_name})
SET drug.drug_class = row.drug_class
"""

RXCUI_UPDATE_QUERY = """
UNWIND $rows AS row
MATCH (drug:Drug {generic_name: row.drug_name})
SET drug.rxcui = row.rxcui
"""


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
    tokens = []
    for token in value.split():
        if token.isupper():
            tokens.append(token)
            continue
        tokens.append(string.capwords(token, sep="-"))
    return " ".join(tokens)


def _preferred_canonical_name(value: str) -> str:
    normalized = _normalize_lookup_key(value)
    if normalized in GENERIC_NAME_OVERRIDES:
        return GENERIC_NAME_OVERRIDES[normalized]
    cleaned = _clean_text(value)
    if cleaned is None:
        raise ValueError("generic name is required")
    return _smart_title(cleaned)


def _resolve_brand_map_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Indian brand map path does not exist: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"Indian brand map must be a file: {resolved}")
    return resolved


def _load_brand_map(path: Path) -> dict[str, dict[str, Any]]:
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


def _load_drug_inventory(driver: Driver, database: str) -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    inventory: dict[str, dict[str, str]] = {}
    brand_counts: dict[str, int] = {}

    drug_query = """
    MATCH (drug:Drug)
    RETURN drug.generic_name AS generic_name,
           coalesce(drug.drug_class, '') AS drug_class,
           coalesce(drug.rxcui, '') AS rxcui,
           coalesce(drug.drugbank_id, '') AS drugbank_id
    """
    brand_query = """
    MATCH (:IndianBrand)-[:CONTAINS]->(drug:Drug)
    RETURN drug.generic_name AS generic_name, count(*) AS brand_count
    """

    with driver.session(database=database) as session:
        for record in session.run(drug_query):
            generic_name = _clean_text(record["generic_name"])
            if not generic_name:
                continue
            normalized = _normalize_lookup_key(generic_name)
            if not normalized:
                continue
            inventory[normalized] = {
                "generic_name": generic_name,
                "drug_class": _clean_text(record["drug_class"]) or "",
                "rxcui": _clean_text(record["rxcui"]) or "",
                "drugbank_id": _clean_text(record["drugbank_id"]) or "",
            }

        for record in session.run(brand_query):
            generic_name = _clean_text(record["generic_name"])
            if not generic_name:
                continue
            normalized = _normalize_lookup_key(generic_name)
            if normalized:
                brand_counts[normalized] = int(record["brand_count"])

    return inventory, brand_counts


def _derive_classes_from_brand_map(
    inventory: dict[str, dict[str, str]],
    brand_map: dict[str, dict[str, Any]],
) -> dict[str, str]:
    class_updates: dict[str, str] = {}
    for entry in brand_map.values():
        generic = _clean_text(entry.get("generic"))
        drug_class = _clean_text(entry.get("class"))
        if not generic or not drug_class:
            continue
        if "+" in generic:
            normalized_combo = _normalize_lookup_key(_preferred_canonical_name(generic))
            if normalized_combo and normalized_combo in inventory:
                class_updates[inventory[normalized_combo]["generic_name"]] = drug_class.casefold()
            continue

        canonical_name = _preferred_canonical_name(generic)
        normalized = _normalize_lookup_key(canonical_name)
        if normalized and normalized in inventory:
            class_updates[inventory[normalized]["generic_name"]] = drug_class.casefold()

    return class_updates


def _class_from_family(name: str) -> str | None:
    normalized = _normalize_lookup_key(name) or ""
    for drug_class, members in EXACT_FAMILY_CLASS_RULES:
        if normalized in members:
            return drug_class
    for drug_class, pattern in FAMILY_CLASS_RULES:
        if re.fullmatch(pattern, normalized):
            return drug_class
    return None


def _derive_classes_from_families(
    inventory: dict[str, dict[str, str]],
    brand_counts: dict[str, int],
    seeded_classes: dict[str, str],
) -> dict[str, str]:
    class_updates: dict[str, str] = {}
    for normalized, drug in inventory.items():
        if drug["generic_name"] in seeded_classes:
            continue
        if drug["drug_class"]:
            continue
        priority_score = brand_counts.get(normalized, 0)
        derived = _class_from_family(drug["generic_name"])
        if not derived:
            continue
        if priority_score > 0 or normalized in {
            "aspirin",
            "clopidogrel",
            "atorvastatin",
            "rosuvastatin",
            "metformin",
            "pantoprazole",
            "levothyroxine",
            "telmisartan",
            "amlodipine",
            "ramipril",
        }:
            class_updates[drug["generic_name"]] = derived
    return class_updates


def _fetch_rxcui(name: str) -> str | None:
    url = RXNORM_RXCUI_URL.format(name=urllib.parse.quote(name))
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        LOGGER.warning("RxNorm lookup failed for %s: %s", name, exc)
        return None

    candidates = payload.get("idGroup", {}).get("rxnormId") or []
    if len(candidates) == 1:
        return str(candidates[0])
    return None


def _derive_rxcui_updates(
    inventory: dict[str, dict[str, str]],
    brand_counts: dict[str, int],
    *,
    max_lookups: int = DEFAULT_MAX_RXCUI_LOOKUPS,
    workers: int = DEFAULT_RXCUI_WORKERS,
) -> dict[str, str]:
    prioritized: list[tuple[int, str, str]] = []
    for normalized, drug in inventory.items():
        if drug["rxcui"] or not drug["drugbank_id"]:
            continue
        if "+" in drug["generic_name"]:
            continue
        priority = brand_counts.get(normalized, 0)
        if priority == 0 and normalized not in {
            "aspirin",
            "clopidogrel",
            "atorvastatin",
            "rosuvastatin",
            "metformin",
            "pantoprazole",
            "levothyroxine",
            "telmisartan",
            "amlodipine",
            "ramipril",
            "omeprazole",
            "warfarin",
            "rivaroxaban",
            "apixaban",
            "gabapentin",
            "pregabalin",
            "tamsulosin",
            "dutasteride",
        }:
            continue
        prioritized.append((priority, normalized, drug["generic_name"]))

    prioritized.sort(reverse=True)
    updates: dict[str, str] = {}
    candidates = prioritized[:max_lookups]
    if not candidates:
        return updates

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        future_map = {
            executor.submit(_fetch_rxcui, generic_name): generic_name
            for _, _normalized, generic_name in candidates
        }
        for future in concurrent.futures.as_completed(future_map):
            generic_name = future_map[future]
            try:
                rxcui = future.result()
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("RxNorm lookup crashed for %s: %s", generic_name, exc)
                continue
            if rxcui:
                updates[generic_name] = rxcui
    return updates


def _write_batch(tx: ManagedTransaction, query: str, rows: list[dict[str, str]]) -> int:
    tx.run(query, rows=rows).consume()
    return len(rows)


def _apply_updates(
    driver: Driver,
    database: str,
    query: str,
    updates: dict[str, str],
    *,
    value_key: str,
    batch_size: int,
) -> int:
    if not updates:
        return 0
    written = 0
    rows = [{"drug_name": drug_name, value_key: value} for drug_name, value in sorted(updates.items())]
    with driver.session(database=database) as session:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            written += session.execute_write(_write_batch, query, batch)
    return written


def backfill(
    driver: Driver,
    *,
    brand_map_path: Path = DEFAULT_BRAND_MAP_PATH,
    batch_size: int = DEFAULT_BATCH_SIZE,
    database: str = DEFAULT_NEO4J_DATABASE,
    max_rxcui_lookups: int = DEFAULT_MAX_RXCUI_LOOKUPS,
    rxcui_workers: int = DEFAULT_RXCUI_WORKERS,
) -> dict[str, int]:
    """Backfill Drug.drug_class and Drug.rxcui for high-priority medications."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    resolved_brand_map_path = _resolve_brand_map_path(brand_map_path)
    brand_map = _load_brand_map(resolved_brand_map_path)
    inventory, brand_counts = _load_drug_inventory(driver, database)

    seeded_classes = _derive_classes_from_brand_map(inventory, brand_map)
    family_classes = _derive_classes_from_families(inventory, brand_counts, seeded_classes)
    all_class_updates = dict(seeded_classes)
    all_class_updates.update({key: value for key, value in family_classes.items() if key not in all_class_updates})

    rxcui_updates = _derive_rxcui_updates(
        inventory,
        brand_counts,
        max_lookups=max_rxcui_lookups,
        workers=rxcui_workers,
    )

    seeded_written = _apply_updates(
        driver,
        database,
        CLASS_UPDATE_QUERY,
        seeded_classes,
        value_key="drug_class",
        batch_size=batch_size,
    )
    family_written = _apply_updates(
        driver,
        database,
        CLASS_UPDATE_QUERY,
        {key: value for key, value in family_classes.items() if key not in seeded_classes},
        value_key="drug_class",
        batch_size=batch_size,
    )
    rxcui_written = _apply_updates(
        driver,
        database,
        RXCUI_UPDATE_QUERY,
        rxcui_updates,
        value_key="rxcui",
        batch_size=batch_size,
    )

    with driver.session(database=database) as session:
        drugs_with_class = session.run(
            "MATCH (d:Drug) WHERE d.drug_class IS NOT NULL AND d.drug_class <> '' RETURN count(d) AS count"
        ).single()["count"]
        drugs_with_rxcui = session.run(
            "MATCH (d:Drug) WHERE d.rxcui IS NOT NULL AND d.rxcui <> '' RETURN count(d) AS count"
        ).single()["count"]

    LOGGER.info(
        "Drug metadata backfill complete: %s class seeds applied from Indian brand map, %s family-propagated class updates, %s RxCUI values populated.",
        f"{seeded_written:,}",
        f"{family_written:,}",
        f"{rxcui_written:,}",
    )
    LOGGER.info(
        "Current coverage: %s Drug nodes with drug_class, %s Drug nodes with rxcui.",
        f"{drugs_with_class:,}",
        f"{drugs_with_rxcui:,}",
    )
    return {
        "seeded_classes": seeded_written,
        "family_classes": family_written,
        "rxcui_updates": rxcui_written,
        "drugs_with_class": drugs_with_class,
        "drugs_with_rxcui": drugs_with_rxcui,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill Drug.drug_class and Drug.rxcui in Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--brand-map-path",
        type=Path,
        default=DEFAULT_BRAND_MAP_PATH,
        help="Path to indian_brand_map.json.",
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password.")
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Batch size.")
    parser.add_argument(
        "--max-rxcui-lookups",
        type=int,
        default=DEFAULT_MAX_RXCUI_LOOKUPS,
        help="Maximum number of high-priority RxCUI lookups to perform.",
    )
    parser.add_argument(
        "--rxcui-workers",
        type=int,
        default=DEFAULT_RXCUI_WORKERS,
        help="Parallel workers for targeted RxCUI lookups.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    """CLI entry point for metadata backfill."""
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        driver.verify_connectivity()
        backfill(
            driver,
            brand_map_path=args.brand_map_path,
            batch_size=args.batch_size,
            database=args.database,
            max_rxcui_lookups=args.max_rxcui_lookups,
            rxcui_workers=args.rxcui_workers,
        )
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
