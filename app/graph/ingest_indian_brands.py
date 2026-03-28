"""Ingest Indian medicine brand data into Neo4j."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
import string
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1_000
DEFAULT_PROGRESS_EVERY = 10_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

READ_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
NA_VALUES = {"", "na", "n/a", "none", "null", "-", "--"}
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_PARENTHESIS_RE = re.compile(r"\([^)]*\)")

DOSAGE_FORM_PATTERNS: tuple[tuple[str, str], ...] = (
    ("oral suspension", "oral suspension"),
    ("dry syrup", "dry syrup"),
    ("sugar free syrup", "syrup"),
    ("syrup", "syrup"),
    ("tablet", "tablet"),
    ("capsule", "capsule"),
    ("injection", "injection"),
    ("respules", "respules"),
    ("drops", "drops"),
    ("cream", "cream"),
    ("ointment", "ointment"),
    ("lotion", "lotion"),
    ("gel", "gel"),
    ("spray", "spray"),
    ("solution", "solution"),
    ("powder", "powder"),
    ("patch", "patch"),
    ("inhaler", "inhaler"),
    ("suspension", "suspension"),
    ("sachet", "sachet"),
)

GENERIC_NAME_OVERRIDES = {
    "paracetamol": "Acetaminophen",
    "amoxycillin": "Amoxicillin",
    "tazobactum": "Tazobactam",
    "salbutamol": "Albuterol",
    "levosalbutamol": "Levalbuterol",
    "lignocaine": "Lidocaine",
    "thyroxine": "Levothyroxine",
    "beclometasone": "Beclomethasone",
    "cefalexin": "Cephalexin",
    "chlorpheniramine maleate": "Chlorpheniramine",
    "chlorpheniramine": "Chlorpheniramine",
    "dextromethorphan hydrobromide": "Dextromethorphan",
    "escitalopram oxalate": "Escitalopram",
    "metoprolol succinate": "Metoprolol",
    "metoprolol tartrate": "Metoprolol",
    "vitamin d3": "Cholecalciferol",
    "vitamin b6": "Pyridoxine",
    "glyceryl trinitrate": "Nitroglycerin",
    "adrenaline": "Epinephrine",
    "noradrenaline": "Norepinephrine",
}

DRUG_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT drug_generic_name IF NOT EXISTS "
    "FOR (d:Drug) REQUIRE d.generic_name IS UNIQUE"
)
BRAND_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT brand_name IF NOT EXISTS "
    "FOR (b:IndianBrand) REQUIRE b.brand_name IS UNIQUE"
)
BRAND_FTS_QUERY = (
    "CREATE FULLTEXT INDEX brand_name_fulltext IF NOT EXISTS "
    "FOR (b:IndianBrand) ON EACH [b.brand_name]"
)
EXISTING_DRUGS_QUERY = "MATCH (drug:Drug) RETURN properties(drug) AS props"
BRAND_BATCH_QUERY = """
UNWIND $rows AS row
MERGE (brand:IndianBrand {brand_name: row.brand_name})
ON CREATE SET brand.manufacturer = row.manufacturer,
              brand.composition = row.composition,
              brand.dosage_form = row.dosage_form
SET brand.manufacturer = CASE
        WHEN row.manufacturer IS NOT NULL AND row.manufacturer <> '' THEN row.manufacturer
        ELSE brand.manufacturer
    END,
    brand.composition = CASE
        WHEN row.composition IS NOT NULL AND row.composition <> '' THEN row.composition
        ELSE brand.composition
    END,
    brand.dosage_form = CASE
        WHEN row.dosage_form IS NOT NULL AND row.dosage_form <> '' THEN row.dosage_form
        ELSE brand.dosage_form
    END
WITH brand, row
UNWIND row.ingredients AS ingredient_name
MERGE (drug:Drug {generic_name: ingredient_name})
ON CREATE SET drug.rxcui = '',
              drug.drug_class = '',
              drug.is_nti = false,
              drug.is_beers = false,
              drug.anticholinergic_score = 0
MERGE (brand)-[:CONTAINS]->(drug)
"""


def _default_data_dir() -> Path:
    explicit_dir = os.getenv("INDIAN_MEDS_DATA_DIR")
    if explicit_dir:
        return Path(explicit_dir).expanduser()

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        base_dir = Path(data_dir).expanduser() / "indian-meds"
        if (base_dir / "DATA").exists():
            return base_dir / "DATA"
        return base_dir

    home_dir = Path.home() / "sahayak-data" / "indian-meds"
    if (home_dir / "DATA").exists():
        return home_dir / "DATA"
    if home_dir.exists():
        return home_dir

    repo_dir = Path(__file__).resolve().parents[3] / "sahayak-data" / "indian-meds"
    if (repo_dir / "DATA").exists():
        return repo_dir / "DATA"
    if repo_dir.exists():
        return repo_dir

    return home_dir / "DATA"


DEFAULT_DATA_DIR = _default_data_dir()


@dataclass(slots=True)
class BrandAggregate:
    """Aggregated Indian brand payload ready for Neo4j."""

    brand_name: str
    manufacturer: str | None = None
    composition: str | None = None
    dosage_form: str | None = None
    ingredients: set[str] = field(default_factory=set)

    def absorb(
        self,
        *,
        manufacturer: str | None,
        composition: str | None,
        dosage_form: str | None,
        ingredients: list[str],
    ) -> None:
        """Merge duplicate rows for the same brand without losing richer metadata."""
        if manufacturer and not self.manufacturer:
            self.manufacturer = manufacturer
        if composition and (
            not self.composition or len(composition) > len(self.composition)
        ):
            self.composition = composition
        if dosage_form and not self.dosage_form:
            self.dosage_form = dosage_form
        self.ingredients.update(ingredients)

    def to_neo4j_row(self) -> dict[str, str | list[str] | None]:
        """Convert the aggregate into a Neo4j parameter dict."""
        return {
            "brand_name": self.brand_name,
            "manufacturer": self.manufacturer,
            "composition": self.composition,
            "dosage_form": self.dosage_form,
            "ingredients": sorted(self.ingredients),
        }


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


def _detect_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _open_csv_file(path: Path) -> tuple[TextIO, csv.DictReader, str]:
    last_error: UnicodeDecodeError | None = None

    for encoding in READ_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as probe:
                sample = probe.read(8_192)
            handle = path.open("r", encoding=encoding, errors="replace", newline="")
            reader = csv.DictReader(handle, delimiter=_detect_delimiter(sample))
            LOGGER.info("Reading %s using encoding=%s.", path.name, encoding)
            return handle, reader, encoding
        except UnicodeDecodeError as exc:
            last_error = exc

    handle = path.open("r", encoding="utf-8", errors="replace", newline="")
    sample = handle.read(8_192)
    handle.seek(0)
    reader = csv.DictReader(handle, delimiter=_detect_delimiter(sample))
    LOGGER.warning(
        "Falling back to utf-8 replacement decoding for %s after decode errors: %s",
        path.name,
        last_error,
    )
    return handle, reader, "utf-8-replace"


def _discover_csv_files(data_path: Path) -> list[Path]:
    resolved_path = data_path.expanduser().resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"Indian medicine path does not exist: {resolved_path}")

    if resolved_path.is_file():
        if resolved_path.suffix.casefold() != ".csv":
            raise ValueError(f"Indian medicine file must be a CSV: {resolved_path}")
        return [resolved_path]

    files = sorted(
        path
        for path in resolved_path.rglob("*.csv")
        if path.is_file() and ".git" not in path.parts
    )
    if not files:
        raise FileNotFoundError(f"No CSV files found in Indian medicine directory: {resolved_path}")
    return files


def _iter_composition_candidates(row: dict[str, str]) -> list[str]:
    candidates: list[str] = []

    salt_composition = _clean_text(row.get("salt_composition"))
    if salt_composition:
        candidates.extend(
            component
            for component in re.split(r"\s*\+\s*", salt_composition)
            if _clean_text(component)
        )

    for field_name in ("short_composition1", "short_composition2"):
        component = _clean_text(row.get(field_name))
        if component:
            candidates.append(component)

    return candidates


def _extract_ingredient_name(component: str) -> str | None:
    cleaned = _clean_text(component)
    if cleaned is None:
        return None

    without_parentheses = _PARENTHESIS_RE.sub(" ", cleaned)
    without_parentheses = _WHITESPACE_RE.sub(" ", without_parentheses).strip(" +,/;-")
    return without_parentheses or None


def _build_composition(row: dict[str, str]) -> str | None:
    preferred = _clean_text(row.get("salt_composition"))
    if preferred:
        return preferred

    components = []
    for field_name in ("short_composition1", "short_composition2"):
        component = _clean_text(row.get(field_name))
        if component:
            components.append(component)

    if not components:
        return None
    return " + ".join(components)


def _infer_dosage_form(name: str | None, pack_size_label: str | None) -> str | None:
    haystacks = []
    cleaned_name = _clean_text(name)
    cleaned_pack = _clean_text(pack_size_label)
    if cleaned_name:
        haystacks.append(cleaned_name.casefold())
    if cleaned_pack:
        haystacks.append(cleaned_pack.casefold())

    for haystack in haystacks:
        for token, label in DOSAGE_FORM_PATTERNS:
            if token in haystack:
                return label

    return None


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


def _row_to_brand_payload(
    row: dict[str, str],
    *,
    existing_drugs: dict[str, str],
) -> tuple[str, str | None, str | None, str | None, list[str]] | None:
    brand_name = _clean_text(row.get("name"))
    if brand_name is None:
        return None

    manufacturer = _clean_text(row.get("manufacturer_name"))
    composition = _build_composition(row)
    dosage_form = _infer_dosage_form(brand_name, row.get("pack_size_label"))

    ingredient_names: list[str] = []
    for component in _iter_composition_candidates(row):
        ingredient = _extract_ingredient_name(component)
        if ingredient is None:
            continue
        canonical_name = _canonicalize_ingredient_name(ingredient, existing_drugs)
        if canonical_name not in ingredient_names:
            ingredient_names.append(canonical_name)

    return brand_name, manufacturer, composition, dosage_form, ingredient_names


def _aggregate_brand_rows(
    csv_files: list[Path],
    *,
    existing_drugs: dict[str, str],
    progress_every: int,
) -> tuple[dict[str, BrandAggregate], int, int]:
    aggregates: dict[str, BrandAggregate] = {}
    processed_rows = 0
    skipped_rows = 0

    for csv_path in csv_files:
        handle, reader, _encoding = _open_csv_file(csv_path)
        with handle:
            for row in reader:
                processed_rows += 1
                payload = _row_to_brand_payload(row, existing_drugs=existing_drugs)
                if payload is None:
                    skipped_rows += 1
                    continue

                brand_name, manufacturer, composition, dosage_form, ingredients = payload
                lookup_key = _normalize_lookup_key(brand_name)
                if lookup_key is None:
                    skipped_rows += 1
                    continue

                aggregate = aggregates.get(lookup_key)
                if aggregate is None:
                    aggregate = BrandAggregate(brand_name=brand_name)
                    aggregates[lookup_key] = aggregate

                aggregate.absorb(
                    manufacturer=manufacturer,
                    composition=composition,
                    dosage_form=dosage_form,
                    ingredients=ingredients,
                )

                if processed_rows % progress_every == 0:
                    LOGGER.info(
                        "Scanned %s rows across %s files; %s unique brands aggregated so far.",
                        f"{processed_rows:,}",
                        len(csv_files),
                        f"{len(aggregates):,}",
                    )

    return aggregates, processed_rows, skipped_rows


def _write_batch(tx: ManagedTransaction, rows: list[dict[str, str | list[str] | None]]) -> None:
    tx.run(BRAND_BATCH_QUERY, rows=rows)


def _ensure_schema(driver: Driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run(DRUG_CONSTRAINT_QUERY).consume()
        session.run(BRAND_CONSTRAINT_QUERY).consume()
        session.run(BRAND_FTS_QUERY).consume()


def ingest(
    driver: Driver,
    data_path: Path,
    *,
    database: str = DEFAULT_NEO4J_DATABASE,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
) -> tuple[int, int, int]:
    """Load Indian brand data into Neo4j.

    Returns:
        Tuple of ``(brands_written, source_rows_scanned, skipped_rows)``.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if progress_every <= 0:
        raise ValueError("progress_every must be positive")

    csv_files = _discover_csv_files(data_path)
    LOGGER.info("Discovered %d Indian medicine CSV files under %s.", len(csv_files), data_path)

    _ensure_schema(driver, database)
    existing_drugs = _load_existing_drug_map(driver, database)
    aggregates, processed_rows, skipped_rows = _aggregate_brand_rows(
        csv_files,
        existing_drugs=existing_drugs,
        progress_every=progress_every,
    )

    rows = [aggregate.to_neo4j_row() for aggregate in aggregates.values()]
    total_written = 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        with driver.session(database=database) as session:
            session.execute_write(_write_batch, batch)
        total_written += len(batch)
        if total_written % progress_every == 0 or total_written == len(rows):
            LOGGER.info(
                "Wrote %s IndianBrand nodes batches to Neo4j (%s total brands).",
                f"{total_written:,}",
                f"{len(rows):,}",
            )

    LOGGER.info(
        "Indian brand ingestion complete: %s source rows scanned, %s unique brands written, %s rows skipped.",
        f"{processed_rows:,}",
        f"{total_written:,}",
        f"{skipped_rows:,}",
    )
    return total_written, processed_rows, skipped_rows


def _build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest the Indian Medicine Dataset into Neo4j as IndianBrand -> Drug data."
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Directory containing Indian medicine CSV files (default: {DEFAULT_DATA_DIR})",
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
        "--neo4j-database",
        default=DEFAULT_NEO4J_DATABASE,
        help=f"Neo4j database name (default: {DEFAULT_NEO4J_DATABASE})",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Number of IndianBrand rows to write per batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help=f"Progress logging frequency in rows (default: {DEFAULT_PROGRESS_EVERY})",
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
            args.data_dir,
            database=args.neo4j_database,
            batch_size=args.batch_size,
            progress_every=args.progress_every,
        )
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
