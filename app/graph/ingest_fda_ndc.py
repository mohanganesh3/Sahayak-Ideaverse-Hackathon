"""Ingest FDA National Drug Code (NDC) directory data into Neo4j."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import string
from pathlib import Path

from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 5_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_TRAILING_PUNCT_RE = re.compile(r"[,;.\-/\s]+$")
NA_VALUES = {"", "na", "n/a", "none", "null", "-", "--"}

GENERIC_NAME_OVERRIDES = {
    "paracetamol": "Acetaminophen",
    "salbutamol": "Albuterol",
    "levosalbutamol": "Levalbuterol",
    "lignocaine": "Lidocaine",
    "adrenaline": "Epinephrine",
    "noradrenaline": "Norepinephrine",
}

USBRAND_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT usbrand_ndc IF NOT EXISTS "
    "FOR (b:USBrand) REQUIRE b.ndc IS UNIQUE"
)
EXISTING_DRUGS_QUERY = "MATCH (drug:Drug) RETURN properties(drug) AS props"

ENRICH_DRUG_BATCH_QUERY = """
UNWIND $rows AS row
MATCH (drug:Drug {generic_name: row.drug_name})
SET drug.fda_product_type = coalesce(row.fda_product_type, drug.fda_product_type),
    drug.fda_route = coalesce(row.fda_route, drug.fda_route),
    drug.fda_dosage_form = coalesce(row.fda_dosage_form, drug.fda_dosage_form),
    drug.ndc_code = coalesce(row.ndc_code, drug.ndc_code)
"""

USBRAND_BATCH_QUERY = """
UNWIND $rows AS row
MERGE (brand:USBrand {ndc: row.ndc})
SET brand.brand_name = row.brand_name,
    brand.labeler = row.labeler,
    brand.dosage_form = row.dosage_form,
    brand.product_type = row.product_type,
    brand.marketing_category = row.marketing_category
WITH brand, row
UNWIND row.ingredients AS ingredient_name
MATCH (drug:Drug {generic_name: ingredient_name})
MERGE (brand)-[:CONTAINS]->(drug)
"""


def _default_data_path() -> Path:
    explicit = os.getenv("FDA_NDC_DATA_PATH")
    if explicit:
        return Path(explicit).expanduser()

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        candidate = Path(data_dir).expanduser() / "fda-ndc" / "drug-ndc-0001-of-0001.json"
        if candidate.exists():
            return candidate

    home_candidate = Path.home() / "sahayak-data" / "fda-ndc" / "drug-ndc-0001-of-0001.json"
    if home_candidate.exists():
        return home_candidate

    repo_candidate = (
        Path(__file__).resolve().parents[3]
        / "sahayak-data"
        / "fda-ndc"
        / "drug-ndc-0001-of-0001.json"
    )
    if repo_candidate.exists():
        return repo_candidate

    return home_candidate


DEFAULT_DATA_PATH = _default_data_path()


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\x00", " ").replace("\xa0", " ")).strip()
    if cleaned.casefold() in NA_VALUES:
        return None
    return cleaned or None


def _normalize_lookup_key(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _smart_title(value: str) -> str:
    tokens = value.split()
    titled = []
    for token in tokens:
        if len(token) <= 3 and any(char.isdigit() for char in token):
            titled.append(token.upper())
            continue
        titled.append(string.capwords(token, sep="-"))
    return " ".join(titled)


def _preferred_canonical_name(ingredient_name: str) -> str:
    normalized_key = _normalize_lookup_key(ingredient_name)
    if normalized_key in GENERIC_NAME_OVERRIDES:
        return GENERIC_NAME_OVERRIDES[normalized_key]
    return _smart_title(ingredient_name)


def _clean_ingredient_name(raw_name: str) -> str | None:
    """Clean an FDA active ingredient name.

    FDA uses names like ``"CAMPHOR, (-)-"`` or ``"ACETAMINOPHEN"`` (all caps).
    Strip trailing punctuation, normalize whitespace, and convert to title case.
    """
    cleaned = _clean_text(raw_name)
    if cleaned is None:
        return None
    cleaned = _TRAILING_PUNCT_RE.sub("", cleaned)
    cleaned = cleaned.strip(" ,;.-/()")
    if not cleaned:
        return None
    return cleaned


def _load_existing_drug_map(driver: Driver, database: str) -> dict[str, str]:
    lookup: dict[str, str] = {}
    with driver.session(database=database) as session:
        for record in session.run(EXISTING_DRUGS_QUERY):
            props = record["props"] or {}
            generic_name = props.get("generic_name")
            normalized_key = _normalize_lookup_key(generic_name)
            if normalized_key and normalized_key not in lookup:
                lookup[normalized_key] = generic_name
    LOGGER.info("Loaded %d existing Drug names for case-insensitive ingredient matching.", len(lookup))
    return lookup


def _canonicalize_ingredient_name(raw_name: str, existing_drugs: dict[str, str]) -> str:
    normalized_key = _normalize_lookup_key(raw_name)
    if normalized_key is None:
        raise ValueError("Cannot canonicalize an empty ingredient name.")

    if normalized_key in GENERIC_NAME_OVERRIDES:
        override_name = GENERIC_NAME_OVERRIDES[normalized_key]
        override_key = _normalize_lookup_key(override_name)
        if override_key in existing_drugs:
            return existing_drugs[override_key]
        return override_name

    if normalized_key in existing_drugs:
        return existing_drugs[normalized_key]

    preferred_name = _preferred_canonical_name(raw_name)
    preferred_key = _normalize_lookup_key(preferred_name)
    if preferred_key in existing_drugs:
        return existing_drugs[preferred_key]

    return preferred_name


def _load_ndc_json(data_path: Path) -> list[dict]:
    resolved = data_path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"FDA NDC data file does not exist: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"FDA NDC data path must be a file: {resolved}")

    LOGGER.info("Loading FDA NDC JSON from %s ...", resolved)
    with resolved.open("r", encoding="utf-8") as fh:
        data = json.load(fh)

    results = data.get("results")
    if results is None:
        raise ValueError(f"FDA NDC JSON missing 'results' key: {resolved}")

    total = data.get("meta", {}).get("results", {}).get("total", len(results))
    LOGGER.info("FDA NDC JSON contains %s product records (meta.total=%s).", f"{len(results):,}", f"{total:,}")
    return results


def _process_products(
    products: list[dict],
    existing_drugs: dict[str, str],
) -> tuple[list[dict], list[dict], int, int, int]:
    """Parse FDA NDC products and build enrichment + brand rows.

    Returns:
        Tuple of (enrich_rows, brand_rows, total_products, matched_drugs, skipped_products).
    """
    enrich_rows: list[dict] = []
    brand_rows: list[dict] = []
    matched_drug_names: set[str] = set()
    skipped = 0

    for product in products:
        brand_name = _clean_text(product.get("brand_name"))
        generic_name = _clean_text(product.get("generic_name"))
        product_ndc = _clean_text(product.get("product_ndc"))
        dosage_form = _clean_text(product.get("dosage_form"))
        product_type = _clean_text(product.get("product_type"))
        marketing_category = _clean_text(product.get("marketing_category"))
        labeler_name = _clean_text(product.get("labeler_name"))

        routes = product.get("route") or []
        route_str = ", ".join(routes) if routes else None

        active_ingredients = product.get("active_ingredients") or []
        if not active_ingredients:
            skipped += 1
            continue

        resolved_ingredients: list[str] = []
        has_match = False

        for ai in active_ingredients:
            raw_name = ai.get("name")
            if not raw_name:
                continue

            cleaned_name = _clean_ingredient_name(raw_name)
            if cleaned_name is None:
                continue

            canonical = _canonicalize_ingredient_name(cleaned_name, existing_drugs)
            canonical_key = _normalize_lookup_key(canonical)

            if canonical_key in existing_drugs:
                matched_name = existing_drugs[canonical_key]
                resolved_ingredients.append(matched_name)
                has_match = True

                if matched_name not in matched_drug_names:
                    matched_drug_names.add(matched_name)
                    enrich_rows.append({
                        "drug_name": matched_name,
                        "fda_product_type": product_type,
                        "fda_route": route_str,
                        "fda_dosage_form": dosage_form,
                        "ndc_code": product_ndc,
                    })

        if not has_match:
            skipped += 1
            continue

        if brand_name and product_ndc and resolved_ingredients:
            brand_rows.append({
                "brand_name": brand_name,
                "ndc": product_ndc,
                "labeler": labeler_name,
                "dosage_form": dosage_form,
                "product_type": product_type,
                "marketing_category": marketing_category,
                "ingredients": resolved_ingredients,
            })

    return enrich_rows, brand_rows, len(products), len(matched_drug_names), skipped


def _write_enrich_batch(tx: ManagedTransaction, rows: list[dict]) -> None:
    tx.run(ENRICH_DRUG_BATCH_QUERY, rows=rows)


def _write_brand_batch(tx: ManagedTransaction, rows: list[dict]) -> None:
    tx.run(USBRAND_BATCH_QUERY, rows=rows)


def _ensure_schema(driver: Driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run(USBRAND_CONSTRAINT_QUERY).consume()


def _write_batches(
    driver: Driver,
    database: str,
    rows: list[dict],
    batch_writer,
    batch_size: int,
    label: str,
) -> int:
    total_written = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        with driver.session(database=database) as session:
            session.execute_write(batch_writer, batch)
        total_written += len(batch)
        LOGGER.info("Wrote %s %s rows to Neo4j.", f"{total_written:,}", label)
    return total_written


def ingest(
    driver: Driver,
    data_path: Path,
    *,
    database: str = DEFAULT_NEO4J_DATABASE,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int, int]:
    """Load FDA NDC data into Neo4j.

    Returns:
        Tuple of ``(matched_drugs, total_products, skipped_products)``.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    _ensure_schema(driver, database)
    existing_drugs = _load_existing_drug_map(driver, database)

    products = _load_ndc_json(data_path)

    enrich_rows, brand_rows, total_products, matched_drugs, skipped = _process_products(
        products, existing_drugs
    )

    LOGGER.info(
        "Processed %s FDA NDC products: %s matched existing drugs, %s skipped (no match).",
        f"{total_products:,}",
        f"{matched_drugs:,}",
        f"{skipped:,}",
    )

    if enrich_rows:
        LOGGER.info("Enriching %s Drug nodes with FDA metadata ...", f"{len(enrich_rows):,}")
        _write_batches(driver, database, enrich_rows, _write_enrich_batch, batch_size, "Drug enrichment")

    if brand_rows:
        LOGGER.info("Creating %s USBrand nodes ...", f"{len(brand_rows):,}")
        _write_batches(driver, database, brand_rows, _write_brand_batch, batch_size, "USBrand")

    LOGGER.info(
        "FDA NDC ingestion complete: %s total products, %s matched drugs, %s skipped, "
        "%s Drug nodes enriched, %s USBrand nodes written.",
        f"{total_products:,}",
        f"{matched_drugs:,}",
        f"{skipped:,}",
        f"{len(enrich_rows):,}",
        f"{len(brand_rows):,}",
    )
    return matched_drugs, total_products, skipped


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest FDA National Drug Code (NDC) directory data into Neo4j as USBrand -> Drug data."
    )
    parser.add_argument(
        "--data-path",
        type=Path,
        default=DEFAULT_DATA_PATH,
        help=f"Path to FDA NDC JSON file (default: {DEFAULT_DATA_PATH})",
    )
    parser.add_argument(
        "--neo4j-uri",
        default=DEFAULT_NEO4J_URI,
        help=f"Neo4j Bolt URI (default: {DEFAULT_NEO4J_URI})",
    )
    parser.add_argument(
        "--neo4j-user",
        default=DEFAULT_NEO4J_USER,
        help=f"Neo4j username (default: {DEFAULT_NEO4J_USER})",
    )
    parser.add_argument(
        "--neo4j-password",
        default=DEFAULT_NEO4J_PASSWORD,
        help="Neo4j password.",
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_NEO4J_DATABASE,
        help=f"Neo4j database name (default: {DEFAULT_NEO4J_DATABASE})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Number of rows to write per Neo4j batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    parser = _build_argument_parser()
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    driver = GraphDatabase.driver(
        args.neo4j_uri,
        auth=(args.neo4j_user, args.neo4j_password),
    )
    try:
        driver.verify_connectivity()
        ingest(
            driver,
            args.data_path,
            database=args.database,
            batch_size=args.batch_size,
        )
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
