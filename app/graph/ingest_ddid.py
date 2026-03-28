"""Ingest DDID herb-food/drug interaction data into Neo4j."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
from dataclasses import asdict, dataclass, field
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
SEVERITY_RANK = {"minor": 1, "moderate": 2, "major": 3}
EVIDENCE_RANK = {
    "in_vitro": 1,
    "animal": 2,
    "knowledgebase": 3,
    "package_insert": 4,
    "case_report": 5,
    "human_clinical": 6,
    "unspecified": 0,
}
MAJOR_RISK_KEYWORDS = (
    "fatal",
    "life-threatening",
    "life threatening",
    "unsafe",
    "death",
    "shock",
    "severe",
    "serious",
    "nephrotoxicity",
    "toxicity",
    "major bleeding",
    "hemorrh",
    "haemorrh",
)

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

FOOD_INFO_FILE = "Food Information.csv"
HERB_INFO_FILE = "Herb Information.csv"
INTERACTION_INFO_FILE = "Interaction Information.csv"
CURATED_HERBS_PATH = Path(__file__).resolve().parents[1] / "data" / "ayurvedic_herbs.json"

DRUG_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT drug_generic_name IF NOT EXISTS "
    "FOR (d:Drug) REQUIRE d.generic_name IS UNIQUE"
)
HERB_CONSTRAINT_QUERY = (
    "CREATE CONSTRAINT herb_name IF NOT EXISTS "
    "FOR (h:Herb) REQUIRE h.name IS UNIQUE"
)
HERB_NODE_BATCH_QUERY = """
UNWIND $rows AS row
MERGE (herb:Herb {name: row.name})
ON CREATE SET herb.category = row.category,
              herb.hindi_name = row.hindi_name,
              herb.tamil_name = row.tamil_name,
              herb.telugu_name = row.telugu_name,
              herb.kannada_name = row.kannada_name,
              herb.scientific_name = row.scientific_name,
              herb.ddid_id = row.ddid_id
SET herb.category = coalesce(herb.category, row.category),
    herb.hindi_name = coalesce(herb.hindi_name, row.hindi_name),
    herb.tamil_name = coalesce(herb.tamil_name, row.tamil_name),
    herb.telugu_name = coalesce(herb.telugu_name, row.telugu_name),
    herb.kannada_name = coalesce(herb.kannada_name, row.kannada_name),
    herb.scientific_name = coalesce(herb.scientific_name, row.scientific_name),
    herb.ddid_id = coalesce(herb.ddid_id, row.ddid_id)
"""
INTERACTION_BATCH_QUERY = """
UNWIND $rows AS row
MERGE (herb:Herb {name: row.herb_name})
ON CREATE SET herb.category = row.herb_category,
              herb.hindi_name = row.hindi_name,
              herb.tamil_name = row.tamil_name,
              herb.telugu_name = row.telugu_name,
              herb.kannada_name = row.kannada_name
SET herb.category = coalesce(herb.category, row.herb_category),
    herb.hindi_name = coalesce(herb.hindi_name, row.hindi_name),
    herb.tamil_name = coalesce(herb.tamil_name, row.tamil_name),
    herb.telugu_name = coalesce(herb.telugu_name, row.telugu_name),
    herb.kannada_name = coalesce(herb.kannada_name, row.kannada_name)
MERGE (drug:Drug {generic_name: row.drug_name})
ON CREATE SET drug.rxcui = '',
              drug.drug_class = '',
              drug.is_nti = false,
              drug.is_beers = false,
              drug.anticholinergic_score = 0
MERGE (herb)-[interaction:INTERACTS_WITH_DRUG {source: 'ddid'}]->(drug)
SET interaction.severity = row.severity,
    interaction.mechanism = row.mechanism,
    interaction.clinical_effect = row.clinical_effect,
    interaction.management = row.management,
    interaction.evidence_level = row.evidence_level
"""


def _default_data_dir() -> Path:
    explicit_dir = os.getenv("DDID_DATA_DIR")
    if explicit_dir:
        return Path(explicit_dir).expanduser()

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "ddid"

    home_dir = Path.home() / "sahayak-data" / "ddid"
    if home_dir.exists():
        return home_dir

    repo_dir = Path(__file__).resolve().parents[3] / "sahayak-data" / "ddid"
    if repo_dir.exists():
        return repo_dir

    return home_dir


DEFAULT_DATA_DIR = _default_data_dir()


@dataclass(frozen=True, slots=True)
class CuratedHerbMetadata:
    """Regional-name enrichment loaded from the curated herb JSON file."""

    english_name: str
    scientific_name: str | None = None
    hindi_name: str | None = None
    tamil_name: str | None = None
    telugu_name: str | None = None
    kannada_name: str | None = None
    aliases: tuple[str, ...] = ()
    ddid_matches: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HerbNodeRecord:
    """Normalized Herb node payload."""

    name: str
    category: str
    hindi_name: str | None = None
    tamil_name: str | None = None
    telugu_name: str | None = None
    kannada_name: str | None = None
    scientific_name: str | None = None
    ddid_id: str | None = None

    def merge(self, candidate: HerbNodeRecord) -> HerbNodeRecord:
        """Merge duplicate node candidates without discarding existing metadata."""
        return HerbNodeRecord(
            name=self.name,
            category=_merge_category(self.category, candidate.category),
            hindi_name=self.hindi_name or candidate.hindi_name,
            tamil_name=self.tamil_name or candidate.tamil_name,
            telugu_name=self.telugu_name or candidate.telugu_name,
            kannada_name=self.kannada_name or candidate.kannada_name,
            scientific_name=self.scientific_name or candidate.scientific_name,
            ddid_id=self.ddid_id or candidate.ddid_id,
        )

    def to_neo4j_row(self) -> dict[str, str | None]:
        """Convert the node to a Neo4j-friendly parameter dict."""
        return asdict(self)


@dataclass(slots=True)
class InteractionAggregate:
    """Aggregated DDID evidence for one Herb -> Drug pair."""

    herb_name: str
    herb_category: str
    hindi_name: str | None
    tamil_name: str | None
    telugu_name: str | None
    kannada_name: str | None
    drug_name: str
    severity: str
    mechanism: str | None
    clinical_effect: str | None
    management: str
    evidence_level: str
    best_score: tuple[int, int, int]
    mechanisms: list[str] = field(default_factory=list)

    def absorb(
        self,
        *,
        severity: str,
        mechanism: str | None,
        clinical_effect: str | None,
        evidence_level: str,
        score: tuple[int, int, int],
    ) -> None:
        """Absorb another DDID study for the same Herb -> Drug pair."""
        if mechanism:
            _append_unique(self.mechanisms, mechanism)
        if SEVERITY_RANK[severity] > SEVERITY_RANK[self.severity]:
            self.severity = severity
        if EVIDENCE_RANK[evidence_level] > EVIDENCE_RANK[self.evidence_level]:
            self.evidence_level = evidence_level
        if score > self.best_score:
            self.best_score = score
            self.clinical_effect = clinical_effect or self.clinical_effect
            self.mechanism = mechanism or self.mechanism
        if not self.mechanism and self.mechanisms:
            self.mechanism = self.mechanisms[0]
        self.management = _management_for_severity(self.severity)

    def to_neo4j_row(self) -> dict[str, str | None]:
        """Convert the aggregate to a Neo4j relationship payload."""
        mechanism = self.mechanism
        if self.mechanisms:
            mechanism = " | ".join(self.mechanisms[:4])

        return {
            "herb_name": self.herb_name,
            "herb_category": self.herb_category,
            "hindi_name": self.hindi_name,
            "tamil_name": self.tamil_name,
            "telugu_name": self.telugu_name,
            "kannada_name": self.kannada_name,
            "drug_name": self.drug_name,
            "severity": self.severity,
            "mechanism": mechanism,
            "clinical_effect": self.clinical_effect,
            "management": self.management,
            "evidence_level": self.evidence_level,
        }


def _normalize_lookup_key(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\x00", " ").replace("\xa0", " ")).strip()
    if cleaned.casefold() in NA_VALUES:
        return None
    return cleaned or None


def _truncate_text(value: str | None, *, limit: int = 1_000) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None or len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[: limit - 3].rstrip()}..."


def _append_unique(target: list[str], value: str, *, limit: int = 4) -> None:
    if value in target or len(target) >= limit:
        return
    target.append(value)


def _merge_category(existing: str | None, candidate: str | None) -> str:
    if not existing:
        return candidate or "herb"
    if not candidate or existing == candidate:
        return existing
    categories = {existing, candidate}
    if categories == {"food", "herb"}:
        return "food/herb"
    return existing


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


def _require_files(data_dir: Path) -> dict[str, Path]:
    resolved = data_dir.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"DDID path does not exist: {resolved}")
    if not resolved.is_dir():
        raise ValueError(f"DDID path must be a directory: {resolved}")

    required = {
        FOOD_INFO_FILE: resolved / FOOD_INFO_FILE,
        HERB_INFO_FILE: resolved / HERB_INFO_FILE,
        INTERACTION_INFO_FILE: resolved / INTERACTION_INFO_FILE,
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise FileNotFoundError(
            f"DDID directory is missing required files: {', '.join(sorted(missing))}"
        )
    return required


def _load_curated_herbs(metadata_path: Path) -> dict[str, CuratedHerbMetadata]:
    if not metadata_path.exists():
        LOGGER.info("Curated herb file not found at %s; continuing without name enrichment.", metadata_path)
        return {}

    with metadata_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    lookup: dict[str, CuratedHerbMetadata] = {}
    for item in payload:
        english_name = _clean_text(item.get("english_name") or item.get("name"))
        if not english_name:
            continue
        aliases = item.get("aliases")
        if not aliases and item.get("common_names"):
            aliases = item["common_names"]
        metadata = CuratedHerbMetadata(
            english_name=english_name,
            scientific_name=_clean_text(item.get("scientific_name")),
            hindi_name=_clean_text(item.get("hindi_name")),
            tamil_name=_clean_text(item.get("tamil_name")),
            telugu_name=_clean_text(item.get("telugu_name")),
            kannada_name=_clean_text(item.get("kannada_name")),
            aliases=tuple(_clean_text(alias) for alias in aliases or [] if _clean_text(alias)),
            ddid_matches=tuple(
                _clean_text(match) for match in item.get("ddid_matches", []) if _clean_text(match)
            ),
        )
        keys = {
            metadata.english_name,
            metadata.scientific_name,
            *metadata.aliases,
            *metadata.ddid_matches,
        }
        for key in keys:
            normalized = _normalize_lookup_key(key)
            if normalized:
                lookup[normalized] = metadata

    LOGGER.info("Loaded %d curated herb aliases from %s.", len(lookup), metadata_path.name)
    return lookup


def _match_curated_metadata(
    *,
    display_name: str | None,
    scientific_name: str | None,
    ddid_name: str | None,
    lookup: dict[str, CuratedHerbMetadata],
) -> CuratedHerbMetadata | None:
    for value in (scientific_name, display_name, ddid_name):
        normalized = _normalize_lookup_key(value)
        if normalized and normalized in lookup:
            return lookup[normalized]
    return None


def _canonicalize_herb(
    *,
    display_name: str | None,
    scientific_name: str | None,
    ddid_name: str | None,
    category: str,
    ddid_id: str | None,
    lookup: dict[str, CuratedHerbMetadata],
) -> HerbNodeRecord:
    match = _match_curated_metadata(
        display_name=display_name,
        scientific_name=scientific_name,
        ddid_name=ddid_name,
        lookup=lookup,
    )
    name = _clean_text(display_name) or _clean_text(ddid_name) or _clean_text(scientific_name)
    if match is not None:
        name = match.english_name
    if not name:
        raise ValueError("Unable to determine a herb/food display name.")

    return HerbNodeRecord(
        name=name,
        category=category,
        hindi_name=match.hindi_name if match else None,
        tamil_name=match.tamil_name if match else None,
        telugu_name=match.telugu_name if match else None,
        kannada_name=match.kannada_name if match else None,
        scientific_name=_clean_text(scientific_name) or (match.scientific_name if match else None),
        ddid_id=_clean_text(ddid_id),
    )


def _parse_food_catalog(
    food_path: Path,
    lookup: dict[str, CuratedHerbMetadata],
) -> dict[str, HerbNodeRecord]:
    catalog: dict[str, HerbNodeRecord] = {}
    handle, reader, _encoding = _open_csv_file(food_path)
    try:
        for row in reader:
            try:
                node = _canonicalize_herb(
                    display_name=row.get("Food_Name"),
                    scientific_name=row.get("Scientific_Name"),
                    ddid_name=row.get("Food_Name"),
                    category="food",
                    ddid_id=row.get("FHDI_Food_ID"),
                    lookup=lookup,
                )
            except ValueError:
                LOGGER.warning(
                    "Skipping unnamed DDID food row at %s:%s.",
                    food_path.name,
                    reader.line_num,
                )
                continue
            catalog[row["FHDI_Food_ID"].strip()] = node
    finally:
        handle.close()
    return catalog


def _parse_herb_catalog(
    herb_path: Path,
    lookup: dict[str, CuratedHerbMetadata],
) -> dict[str, HerbNodeRecord]:
    catalog: dict[str, HerbNodeRecord] = {}
    handle, reader, _encoding = _open_csv_file(herb_path)
    try:
        for row in reader:
            try:
                node = _canonicalize_herb(
                    display_name=row.get("Herb_English_Name"),
                    scientific_name=row.get("Herb_Latin_Name"),
                    ddid_name=row.get("Herb_English_Name") or row.get("Herb_Latin_Name"),
                    category="herb",
                    ddid_id=row.get("FHDI_Herb_ID"),
                    lookup=lookup,
                )
            except ValueError:
                LOGGER.warning(
                    "Skipping unnamed DDID herb row at %s:%s.",
                    herb_path.name,
                    reader.line_num,
                )
                continue
            catalog[row["FHDI_Herb_ID"].strip()] = node
    finally:
        handle.close()
    return catalog


def _merge_node_catalogs(*catalogs: dict[str, HerbNodeRecord]) -> dict[str, HerbNodeRecord]:
    merged: dict[str, HerbNodeRecord] = {}
    for catalog in catalogs:
        for node in catalog.values():
            existing = merged.get(node.name)
            merged[node.name] = node if existing is None else existing.merge(node)
    return merged


def _classify_evidence(row: dict[str, str]) -> str:
    relationship_class = _clean_text(row.get("Relationship_classification"))
    if relationship_class:
        relationship_key = relationship_class.casefold()
        if "package insert" in relationship_key:
            return "package_insert"
        if relationship_key == "drugbank":
            return "knowledgebase"

    design = (_clean_text(row.get("Experimental_Design")) or "").casefold()
    species = (_clean_text(row.get("Experimental_Species")) or "").casefold()
    reference = (_clean_text(row.get("Reference")) or "").casefold()
    result = (_clean_text(row.get("Result")) or "").casefold()

    if "case report" in design or "case report" in reference:
        return "case_report"
    if species == "homo sapiens" or "healthy volunteer" in design or "randomized" in design:
        return "human_clinical"
    if any(token in design for token in ("cell", "in vitro")) or "cell" in result:
        return "in_vitro"
    if species and species not in {"na", ""}:
        return "animal"
    return "unspecified"


def _derive_severity(row: dict[str, str], evidence_level: str) -> str:
    effect = (_clean_text(row.get("Effect")) or "").casefold()
    potential_target = (_clean_text(row.get("Potential_Target")) or "").casefold()
    text = " ".join(
        part.casefold()
        for part in (
            _clean_text(row.get("Result")),
            _clean_text(row.get("Conclusion")),
            _clean_text(row.get("Note")),
        )
        if part
    )

    if effect == "harmful":
        severity = "major"
    elif effect in {"positive", "negative"}:
        severity = "moderate"
    elif effect == "possible" and EVIDENCE_RANK[evidence_level] >= EVIDENCE_RANK["human_clinical"]:
        severity = "moderate"
    else:
        severity = "minor"

    if any(keyword in text for keyword in MAJOR_RISK_KEYWORDS):
        return "major"
    if effect == "possible" and (
        EVIDENCE_RANK[evidence_level] >= EVIDENCE_RANK["package_insert"]
        or bool(potential_target)
        or any(
            keyword in text
            for keyword in (
                "increase plasma concentration",
                "decrease plasma concentration",
                "bioavailability",
                "close monitoring",
                "caution",
                "avoid",
                "cyp",
                "p-gp",
                "pgp",
                "bcrp",
            )
        )
    ):
        return "moderate"
    return severity


def _derive_mechanism(row: dict[str, str]) -> str | None:
    component = _clean_text(row.get("Component"))
    target = _clean_text(row.get("Potential_Target"))
    conclusion = _clean_text(row.get("Conclusion"))

    parts: list[str] = []
    if component:
        parts.append(f"Component: {component}")
    if target:
        parts.append(f"Target: {target}")
    if conclusion and any(
        token in conclusion.casefold()
        for token in ("cyp", "p-gp", "pgp", "bcrp", "oatp", "inhibit", "induc")
    ):
        parts.append(_truncate_text(conclusion, limit=220))

    if not parts:
        return None
    return " ; ".join(parts[:3])


def _derive_clinical_effect(row: dict[str, str]) -> str | None:
    return _truncate_text(row.get("Conclusion")) or _truncate_text(row.get("Result"))


def _management_for_severity(severity: str) -> str:
    if severity == "major":
        return "Avoid or use only with clear clinical justification; monitor closely for toxicity or loss of effect."
    if severity == "moderate":
        return "Use cautiously and monitor for altered efficacy, toxicity, bleeding, or exposure changes."
    return "Monitor if used regularly, at high doses, or with narrow therapeutic index drugs."


def _ensure_schema(driver: Driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run(DRUG_CONSTRAINT_QUERY).consume()
        session.run(HERB_CONSTRAINT_QUERY).consume()


def _write_herb_batch(tx: ManagedTransaction, rows: list[dict[str, str | None]]) -> int:
    tx.run(HERB_NODE_BATCH_QUERY, rows=rows).consume()
    return len(rows)


def _write_interaction_batch(tx: ManagedTransaction, rows: list[dict[str, str | None]]) -> int:
    tx.run(INTERACTION_BATCH_QUERY, rows=rows).consume()
    return len(rows)


def ingest(
    driver: Driver,
    data_path: Path,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_every: int = DEFAULT_PROGRESS_EVERY,
    database: str = DEFAULT_NEO4J_DATABASE,
    curated_herbs_path: Path = CURATED_HERBS_PATH,
) -> int:
    """Load DDID herb-food/drug interaction data into Neo4j.

    Args:
        driver: Active Neo4j driver.
        data_path: Path to the DDID directory.
        batch_size: Number of rows to write per Neo4j batch.
        progress_every: Log progress after every N source interaction rows.
        database: Neo4j database name.
        curated_herbs_path: Optional regional-name enrichment JSON path.

    Returns:
        Number of unique herb-drug relationships created or updated.
    """
    if batch_size <= 0:
        raise ValueError("batch_size must be a positive integer")
    if progress_every <= 0:
        raise ValueError("progress_every must be a positive integer")

    required_files = _require_files(data_path)
    curated_lookup = _load_curated_herbs(curated_herbs_path)

    food_catalog = _parse_food_catalog(required_files[FOOD_INFO_FILE], curated_lookup)
    herb_catalog = _parse_herb_catalog(required_files[HERB_INFO_FILE], curated_lookup)
    node_catalog = _merge_node_catalogs(food_catalog, herb_catalog)

    LOGGER.info(
        "Prepared %d unique Herb nodes from %d food records and %d herb records.",
        len(node_catalog),
        len(food_catalog),
        len(herb_catalog),
    )

    _ensure_schema(driver, database)

    with driver.session(database=database) as session:
        pending_nodes: list[dict[str, str | None]] = []
        written_nodes = 0
        for node in node_catalog.values():
            pending_nodes.append(node.to_neo4j_row())
            if len(pending_nodes) >= batch_size:
                written_nodes += session.execute_write(_write_herb_batch, pending_nodes)
                pending_nodes.clear()
        if pending_nodes:
            written_nodes += session.execute_write(_write_herb_batch, pending_nodes)
        LOGGER.info("Created or updated %d Herb nodes from DDID metadata.", written_nodes)

        handle, reader, _encoding = _open_csv_file(required_files[INTERACTION_INFO_FILE])
        try:
            source_rows = 0
            skipped_rows = 0
            skipped_no_effect = 0
            interactions: dict[tuple[str, str], InteractionAggregate] = {}

            for row in reader:
                source_rows += 1

                drug_name = _clean_text(row.get("Drug_Name"))
                herb_id = _clean_text(row.get("Food_Herb_ID"))
                raw_herb_name = _clean_text(row.get("Food_Herb_Name"))
                effect = (_clean_text(row.get("Effect")) or "").casefold()
                if effect == "no effect":
                    skipped_no_effect += 1
                    continue
                if not drug_name or not raw_herb_name:
                    skipped_rows += 1
                    continue

                catalog_node = (
                    food_catalog.get(herb_id or "")
                    or herb_catalog.get(herb_id or "")
                    or _canonicalize_herb(
                        display_name=raw_herb_name,
                        scientific_name=None,
                        ddid_name=raw_herb_name,
                        category=(_clean_text(row.get("Type")) or "herb").casefold(),
                        ddid_id=herb_id,
                        lookup=curated_lookup,
                    )
                )
                herb_category = _merge_category(
                    catalog_node.category,
                    (_clean_text(row.get("Type")) or "").casefold(),
                )

                evidence_level = _classify_evidence(row)
                severity = _derive_severity(row, evidence_level)
                mechanism = _derive_mechanism(row)
                clinical_effect = _derive_clinical_effect(row)
                score = (
                    SEVERITY_RANK[severity],
                    EVIDENCE_RANK[evidence_level],
                    int(bool(mechanism)) + int(bool(clinical_effect)),
                )

                key = (catalog_node.name.casefold(), drug_name.casefold())
                aggregate = interactions.get(key)
                if aggregate is None:
                    mechanisms: list[str] = []
                    if mechanism:
                        mechanisms.append(mechanism)
                    interactions[key] = InteractionAggregate(
                        herb_name=catalog_node.name,
                        herb_category=herb_category,
                        hindi_name=catalog_node.hindi_name,
                        tamil_name=catalog_node.tamil_name,
                        telugu_name=catalog_node.telugu_name,
                        kannada_name=catalog_node.kannada_name,
                        drug_name=drug_name,
                        severity=severity,
                        mechanism=mechanism,
                        clinical_effect=clinical_effect,
                        management=_management_for_severity(severity),
                        evidence_level=evidence_level,
                        best_score=score,
                        mechanisms=mechanisms,
                    )
                else:
                    aggregate.absorb(
                        severity=severity,
                        mechanism=mechanism,
                        clinical_effect=clinical_effect,
                        evidence_level=evidence_level,
                        score=score,
                    )

                if source_rows % progress_every == 0:
                    LOGGER.info(
                        "Processed %s DDID interaction rows: %s aggregated relationships, %s neutral rows skipped.",
                        f"{source_rows:,}",
                        f"{len(interactions):,}",
                        f"{skipped_no_effect:,}",
                    )
        finally:
            handle.close()

        pending_relationships: list[dict[str, str | None]] = []
        written_relationships = 0
        for aggregate in interactions.values():
            pending_relationships.append(aggregate.to_neo4j_row())
            if len(pending_relationships) >= batch_size:
                written_relationships += session.execute_write(
                    _write_interaction_batch,
                    pending_relationships,
                )
                pending_relationships.clear()
        if pending_relationships:
            written_relationships += session.execute_write(
                _write_interaction_batch,
                pending_relationships,
            )

    LOGGER.info(
        "DDID ingestion complete: %s interaction rows processed, %s unique Herb->Drug relationships written, %s neutral rows skipped, %s malformed rows skipped.",
        f"{source_rows:,}",
        f"{written_relationships:,}",
        f"{skipped_no_effect:,}",
        f"{skipped_rows:,}",
    )
    return written_relationships


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ingest DDID herb-food/drug interaction data into Neo4j.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help="Directory containing DDID CSV files.",
    )
    parser.add_argument(
        "--curated-herbs",
        type=Path,
        default=CURATED_HERBS_PATH,
        help="Path to the curated ayurvedic herb JSON file used for regional-name enrichment.",
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
        help="Number of rows to write per batch.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help="Log progress after every N interaction rows.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    """CLI entry point for DDID ingestion."""
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
        created = ingest(
            driver,
            args.data_dir,
            batch_size=args.batch_size,
            progress_every=args.progress_every,
            database=args.database,
            curated_herbs_path=args.curated_herbs,
        )
        LOGGER.info("Created or updated %s DDID herb-drug relationships.", f"{created:,}")
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
