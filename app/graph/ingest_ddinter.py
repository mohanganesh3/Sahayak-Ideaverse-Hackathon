"""Ingest DDInter (Drug-Drug Interaction) CSV exports into Neo4j."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import TextIO

from neo4j import Driver, GraphDatabase, ManagedTransaction

def _default_data_dir() -> Path:
    explicit_dir = os.getenv("DDINTER_DATA_DIR")
    if explicit_dir:
        return Path(explicit_dir).expanduser()

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "ddinter"

    home_dir = Path.home() / "sahayak-data" / "ddinter"
    if home_dir.exists():
        return home_dir

    repo_dir = Path(__file__).resolve().parents[3] / "sahayak-data" / "ddinter"
    if repo_dir.exists():
        return repo_dir

    return home_dir

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1_000
DEFAULT_PROGRESS_EVERY = 10_000
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_DATA_DIR = _default_data_dir()

READ_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
EXPECTED_SEVERITIES = {"major", "moderate", "minor"}
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_WARNED_SEVERITIES: set[str] = set()

HEADER_ALIASES = {
    "ddinterid_a": "ddinter_id_a",
    "drug_a": "drug_a",
    "ddinterid_b": "ddinter_id_b",
    "drug_b": "drug_b",
    "level": "severity",
    "severity": "severity",
    "mechanism": "mechanism",
    "clinical_effect": "clinical_effect",
    "clinical_effects": "clinical_effect",
    "clinicaleffects": "clinical_effect",
    "management": "management",
    "alternative": "alternative",
    "alternatives": "alternative",
    "evidence": "evidence_level",
    "evidence_level": "evidence_level",
    "evidencelevel": "evidence_level",
}
REQUIRED_FIELDS = {"drug_a", "drug_b", "severity"}

DRUG_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT drug_generic_name IF NOT EXISTS "
    "FOR (d:Drug) REQUIRE d.generic_name IS UNIQUE"
)
INGEST_BATCH_QUERY = """
UNWIND $rows AS row
MERGE (drug_a:Drug {generic_name: row.drug_a})
  ON CREATE SET drug_a.rxcui = ''
MERGE (drug_b:Drug {generic_name: row.drug_b})
  ON CREATE SET drug_b.rxcui = ''
MERGE (drug_a)-[interaction:INTERACTS_WITH {source: 'ddinter'}]->(drug_b)
SET interaction.severity = coalesce(row.severity, interaction.severity),
    interaction.mechanism = coalesce(row.mechanism, interaction.mechanism),
    interaction.clinical_effect = coalesce(row.clinical_effect, interaction.clinical_effect),
    interaction.management = coalesce(row.management, interaction.management),
    interaction.evidence_level = coalesce(row.evidence_level, interaction.evidence_level),
    interaction.alternative = coalesce(row.alternative, interaction.alternative),
    interaction.ddinter_id_a = coalesce(row.ddinter_id_a, interaction.ddinter_id_a),
    interaction.ddinter_id_b = coalesce(row.ddinter_id_b, interaction.ddinter_id_b)
"""


@dataclass(frozen=True, slots=True)
class InteractionRecord:
    """Normalized DDInter record ready for Neo4j ingestion."""

    drug_a: str
    drug_b: str
    severity: str
    mechanism: str | None = None
    clinical_effect: str | None = None
    management: str | None = None
    evidence_level: str | None = None
    alternative: str | None = None
    ddinter_id_a: str | None = None
    ddinter_id_b: str | None = None

    def key(self) -> tuple[str, str]:
        """Return the canonical pair key used for de-duplication."""
        return (self.drug_a, self.drug_b)

    def to_neo4j_row(self) -> dict[str, str | None]:
        """Convert the record into a Neo4j-friendly parameter payload."""
        return asdict(self)


def _normalize_header(header: str) -> str:
    normalized = _NON_ALNUM_RE.sub("_", header.casefold()).strip("_")
    return normalized


def _normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\x00", " ")).strip()
    return cleaned or None


def _strip_qualifiers(value: str | None) -> str | None:
    normalized = _normalize_text(value)
    if normalized is None:
        return None
    tokens = [
        token
        for token in _NON_ALNUM_RE.sub(" ", normalized.casefold()).split()
        if token
        not in {
            "hydrochloride",
            "hcl",
            "sodium",
            "potassium",
            "succinate",
            "tartrate",
            "phosphate",
            "acetate",
            "maleate",
            "sulfate",
            "calcium",
            "magnesium",
        }
    ]
    return " ".join(tokens).strip() or _NON_ALNUM_RE.sub(" ", normalized.casefold()).strip()


def _smart_title(value: str) -> str:
    return " ".join(part.capitalize() if part.islower() else part for part in value.split())


def _normalize_severity(value: str | None) -> str | None:
    cleaned = _normalize_text(value)
    if cleaned is None:
        return None

    severity = cleaned.casefold()
    if severity not in EXPECTED_SEVERITIES and severity not in _WARNED_SEVERITIES:
        LOGGER.warning(
            "Encountered unexpected DDInter severity %r; storing the normalized value as-is.",
            cleaned,
        )
        _WARNED_SEVERITIES.add(severity)
    return severity


def _canonicalize_record(record: InteractionRecord) -> InteractionRecord:
    """Store interactions in a deterministic direction to prevent reverse duplicates."""
    if record.drug_a.casefold() <= record.drug_b.casefold():
        return record

    return InteractionRecord(
        drug_a=record.drug_b,
        drug_b=record.drug_a,
        severity=record.severity,
        mechanism=record.mechanism,
        clinical_effect=record.clinical_effect,
        management=record.management,
        evidence_level=record.evidence_level,
        alternative=record.alternative,
        ddinter_id_a=record.ddinter_id_b,
        ddinter_id_b=record.ddinter_id_a,
    )


def _load_existing_drug_lookup(driver: Driver, database: str) -> tuple[dict[str, str], dict[str, str]]:
    exact_lookup: dict[str, str] = {}
    stripped_lookup: dict[str, str] = {}
    query = """
    MATCH (drug:Drug)
    RETURN drug.generic_name AS generic_name,
           coalesce(drug.canonical_name, '') AS canonical_name,
           coalesce(drug.synonyms, []) AS synonyms
    """
    with driver.session(database=database) as session:
        for record in session.run(query):
            names = [
                _normalize_text(record["generic_name"]),
                _normalize_text(record["canonical_name"]),
                *(_normalize_text(name) for name in (record["synonyms"] or [])),
            ]
            canonical_name = next((name for name in names if name), None)
            if canonical_name is None:
                continue
            for name in names:
                if not name:
                    continue
                normalized = _NON_ALNUM_RE.sub(" ", name.casefold()).strip()
                stripped = _strip_qualifiers(name)
                if normalized:
                    exact_lookup[normalized] = canonical_name
                if stripped:
                    stripped_lookup.setdefault(stripped, canonical_name)
    return exact_lookup, stripped_lookup


def _canonical_drug_name(
    raw_name: str,
    *,
    exact_lookup: dict[str, str],
    stripped_lookup: dict[str, str],
) -> str:
    cleaned = _normalize_text(raw_name)
    if cleaned is None:
        raise ValueError("drug name is required")

    normalized = _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()
    stripped = _strip_qualifiers(cleaned)
    if normalized and normalized in exact_lookup:
        return exact_lookup[normalized]
    if stripped and stripped in stripped_lookup:
        return stripped_lookup[stripped]
    return _smart_title(cleaned)


def _merge_records(
    existing: InteractionRecord,
    candidate: InteractionRecord,
    *,
    file_name: str,
    line_number: int,
) -> tuple[InteractionRecord, bool]:
    """Merge duplicate rows while preserving the most complete metadata."""
    updates: dict[str, str | None] = {}
    conflict_fields: list[str] = []

    for field_name in (
        "severity",
        "mechanism",
        "clinical_effect",
        "management",
        "evidence_level",
        "alternative",
        "ddinter_id_a",
        "ddinter_id_b",
    ):
        old_value = getattr(existing, field_name)
        new_value = getattr(candidate, field_name)

        if old_value == new_value or not new_value:
            continue
        if not old_value:
            updates[field_name] = new_value
            continue
        conflict_fields.append(field_name)

    if conflict_fields:
        LOGGER.warning(
            "Conflicting metadata for %s/%s at %s:%s; keeping the first non-empty values for %s.",
            existing.drug_a,
            existing.drug_b,
            file_name,
            line_number,
            ", ".join(conflict_fields),
        )

    if not updates:
        return existing, False

    return replace(existing, **updates), True


def _discover_csv_files(data_path: Path) -> list[Path]:
    resolved_path = data_path.expanduser().resolve()
    if not resolved_path.exists():
        raise FileNotFoundError(f"DDInter path does not exist: {resolved_path}")

    if resolved_path.is_file():
        if resolved_path.suffix.casefold() != ".csv":
            raise ValueError(f"DDInter file must be a CSV: {resolved_path}")
        return [resolved_path]

    files = sorted(
        path
        for path in resolved_path.iterdir()
        if path.is_file() and path.suffix.casefold() == ".csv"
    )
    if not files:
        raise FileNotFoundError(f"No CSV files found in DDInter directory: {resolved_path}")

    return files


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


def _build_header_map(fieldnames: list[str] | None, path: Path) -> dict[str, str]:
    if not fieldnames:
        raise ValueError(f"CSV file {path} does not contain a header row.")

    header_map: dict[str, str] = {}
    for header in fieldnames:
        alias = HEADER_ALIASES.get(_normalize_header(header))
        if alias and alias not in header_map:
            header_map[alias] = header

    missing = sorted(REQUIRED_FIELDS - set(header_map))
    if missing:
        raise ValueError(
            f"CSV file {path} is missing required DDInter columns: {', '.join(missing)}"
        )

    return header_map


def _parse_record(
    raw_row: dict[str | None, str | None],
    header_map: dict[str, str],
) -> InteractionRecord:
    drug_a = _normalize_text(raw_row.get(header_map["drug_a"]))
    drug_b = _normalize_text(raw_row.get(header_map["drug_b"]))
    severity = _normalize_severity(raw_row.get(header_map["severity"]))

    if not drug_a or not drug_b:
        raise ValueError("missing Drug_A or Drug_B")
    if not severity:
        raise ValueError("missing interaction severity/Level")

    if drug_a.casefold() == drug_b.casefold():
        raise ValueError("self-interaction rows are not valid DDInter pairs")

    record = InteractionRecord(
        drug_a=drug_a,
        drug_b=drug_b,
        severity=severity,
        mechanism=_normalize_text(raw_row.get(header_map.get("mechanism"))),
        clinical_effect=_normalize_text(raw_row.get(header_map.get("clinical_effect"))),
        management=_normalize_text(raw_row.get(header_map.get("management"))),
        evidence_level=_normalize_text(raw_row.get(header_map.get("evidence_level"))),
        alternative=_normalize_text(raw_row.get(header_map.get("alternative"))),
        ddinter_id_a=_normalize_text(raw_row.get(header_map.get("ddinter_id_a"))),
        ddinter_id_b=_normalize_text(raw_row.get(header_map.get("ddinter_id_b"))),
    )
    return _canonicalize_record(record)


def _ensure_schema(driver: Driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run(DRUG_CONSTRAINT_QUERY).consume()


def _write_batch(tx: ManagedTransaction, rows: list[dict[str, str | None]]) -> int:
    tx.run(INGEST_BATCH_QUERY, rows=rows).consume()
    return len(rows)


def ingest(
    driver: Driver,
    data_path: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> int:
    """Load DDInter CSV data into Neo4j as Drug nodes and INTERACTS_WITH edges.

    Args:
        driver: Active Neo4j driver.
        data_path: Directory of DDInter CSV files or a single DDInter CSV file.
        batch_size: Number of unique interaction rows to write per Neo4j batch.
        progress_every: Log progress after every N source rows processed.
        database: Neo4j database name.

    Returns:
        Number of unique DDInter relationships ingested.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if progress_every <= 0:
        raise ValueError("progress_every must be a positive integer")

    csv_files = _discover_csv_files(data_path)
    LOGGER.info("Discovered %d DDInter CSV files under %s.", len(csv_files), data_path)

    _ensure_schema(driver, database)
    exact_lookup, stripped_lookup = _load_existing_drug_lookup(driver, database)

    source_rows = 0
    skipped_rows = 0
    duplicate_rows = 0
    written_rows = 0
    unique_interactions = 0
    pending_rows: list[dict[str, str | None]] = []
    seen_records: dict[tuple[str, str], InteractionRecord] = {}

    with driver.session(database=database) as session:
        for csv_file in csv_files:
            handle, reader, _encoding = _open_csv_file(csv_file)
            try:
                header_map = _build_header_map(reader.fieldnames, csv_file)

                for raw_row in reader:
                    source_rows += 1

                    try:
                        record = _parse_record(raw_row, header_map)
                    except ValueError as exc:
                        skipped_rows += 1
                        LOGGER.warning(
                            "Skipping malformed DDInter row in %s:%s: %s",
                            csv_file.name,
                            reader.line_num,
                            exc,
                        )
                        continue

                    canonical_a = _canonical_drug_name(
                        record.drug_a,
                        exact_lookup=exact_lookup,
                        stripped_lookup=stripped_lookup,
                    )
                    canonical_b = _canonical_drug_name(
                        record.drug_b,
                        exact_lookup=exact_lookup,
                        stripped_lookup=stripped_lookup,
                    )
                    for raw_name, canonical_name in (
                        (record.drug_a, canonical_a),
                        (record.drug_b, canonical_b),
                    ):
                        normalized = _NON_ALNUM_RE.sub(" ", raw_name.casefold()).strip()
                        stripped = _strip_qualifiers(raw_name)
                        canonical_normalized = _NON_ALNUM_RE.sub(" ", canonical_name.casefold()).strip()
                        canonical_stripped = _strip_qualifiers(canonical_name)
                        if normalized:
                            exact_lookup[normalized] = canonical_name
                        if stripped:
                            stripped_lookup.setdefault(stripped, canonical_name)
                        if canonical_normalized:
                            exact_lookup[canonical_normalized] = canonical_name
                        if canonical_stripped:
                            stripped_lookup.setdefault(canonical_stripped, canonical_name)

                    record = _canonicalize_record(
                        replace(record, drug_a=canonical_a, drug_b=canonical_b)
                    )

                    existing = seen_records.get(record.key())
                    if existing is None:
                        seen_records[record.key()] = record
                        pending_rows.append(record.to_neo4j_row())
                        unique_interactions += 1
                    elif existing == record:
                        duplicate_rows += 1
                    else:
                        merged_record, changed = _merge_records(
                            existing,
                            record,
                            file_name=csv_file.name,
                            line_number=reader.line_num,
                        )
                        if changed:
                            seen_records[record.key()] = merged_record
                            pending_rows.append(merged_record.to_neo4j_row())
                        else:
                            duplicate_rows += 1

                    if len(pending_rows) >= batch_size:
                        written_rows += session.execute_write(_write_batch, pending_rows)
                        pending_rows.clear()

                    if source_rows % progress_every == 0:
                        LOGGER.info(
                            "Processed %s DDInter rows: %s unique interactions, %s duplicates, %s skipped.",
                            f"{source_rows:,}",
                            f"{unique_interactions:,}",
                            f"{duplicate_rows:,}",
                            f"{skipped_rows:,}",
                        )
            finally:
                handle.close()

        if pending_rows:
            written_rows += session.execute_write(_write_batch, pending_rows)

    LOGGER.info(
        "DDInter ingestion complete: %s valid source rows, %s unique interactions, %s Neo4j writes, %s duplicates, %s skipped.",
        f"{source_rows - skipped_rows:,}",
        f"{unique_interactions:,}",
        f"{written_rows:,}",
        f"{duplicate_rows:,}",
        f"{skipped_rows:,}",
    )
    return unique_interactions


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest DDInter CSV exports into Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing DDInter CSV files, or a single DDInter CSV file.",
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument(
        "--neo4j-password",
        default=DEFAULT_NEO4J_PASSWORD,
        help="Neo4j password.",
    )
    parser.add_argument(
        "--database",
        default=DEFAULT_NEO4J_DATABASE,
        help="Neo4j database name.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help="Number of unique interaction rows to write per batch.",
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
    """CLI entry point for DDInter ingestion."""
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
        ingested = ingest(
            driver,
            args.data_dir,
            batch_size=args.batch_size,
            progress_every=args.progress_every,
            database=args.database,
        )
        LOGGER.info("Created or updated %s DDInter interactions.", f"{ingested:,}")
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
