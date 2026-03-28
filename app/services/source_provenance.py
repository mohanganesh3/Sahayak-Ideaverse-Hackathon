"""Runtime provenance helpers for local curated datasets and DDID study metadata."""

from __future__ import annotations

import csv
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

READ_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
NA_VALUES = {"", "na", "n/a", "none", "null", "-", "--"}

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

_AYURVEDA_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "ayurvedic_herbs.json"
_CYP450_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "cyp450_data.json"
_HERB_CYP_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "herb_cyp_interactions.json"

_FDA_DDI_TABLE_URL = (
    "https://www.fda.gov/drugs/drug-interactions-labeling/"
    "drug-development-and-drug-interactions-table-substrates-inhibitors-and-inducers"
)
_FLOCKHART_MAIN_URL = "https://drug-interactions.medicine.iu.edu/Main-Table.aspx"
_FLOCKHART_REFERENCE_URLS = {
    "cyp1a2": "https://drug-interactions.medicine.iu.edu/1A2references.aspx",
    "cyp2b6": "https://drug-interactions.medicine.iu.edu/2B6References.aspx",
    "cyp2c8": "https://drug-interactions.medicine.iu.edu/2C8references.aspx",
    "cyp2c9": "https://drug-interactions.medicine.iu.edu/2C9references.aspx",
    "cyp2c19": "https://drug-interactions.medicine.iu.edu/2C19references.aspx",
    "cyp2d6": "https://drug-interactions.medicine.iu.edu/2D6references.aspx",
    "cyp2e1": "https://drug-interactions.medicine.iu.edu/2E1references.aspx",
    "cyp3a4": "https://drug-interactions.medicine.iu.edu/3A457references.aspx",
    "cyp3a5": "https://drug-interactions.medicine.iu.edu/3A457references.aspx",
    "cyp3a7": "https://drug-interactions.medicine.iu.edu/3A457references.aspx",
}


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = _WHITESPACE_RE.sub(" ", str(value).replace("\x00", " ").replace("\xa0", " ")).strip()
    if not text or text.casefold() in NA_VALUES:
        return None
    return text


def _normalize_key(value: Any) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _default_ddid_data_dir() -> Path:
    explicit_dir = os.getenv("DDID_DATA_DIR")
    if explicit_dir:
        return Path(explicit_dir).expanduser()

    data_dir = os.getenv("DATA_DIR")
    if data_dir:
        return Path(data_dir).expanduser() / "ddid"

    project_root = Path(__file__).resolve().parents[2]
    candidates = (
        project_root.parent / "sahayak-data" / "ddid",
        project_root / "sahayak-data" / "ddid",
        Path.home() / "sahayak-data" / "ddid",
        Path.home() / "IDEAVERSE" / "sahayak-data" / "ddid",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    last_error: UnicodeDecodeError | None = None
    for encoding in READ_ENCODINGS:
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError as exc:
            last_error = exc
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        if last_error:
            pass
        return list(csv.DictReader(handle))


def _append_reference_bits(reference: str | None, doi: str | None, pmid: str | None) -> str | None:
    parts: list[str] = []
    if reference:
        parts.append(reference)
    if doi and f"doi:{doi}".lower() not in " ".join(parts).lower():
        parts.append(f"doi:{doi}")
    if pmid and f"pmid:{pmid}".lower() not in " ".join(parts).lower():
        parts.append(f"PMID:{pmid}")
    return " ".join(parts) if parts else None


def _make_record_link(label: str, url: str | None) -> dict[str, str] | None:
    if not url:
        return None
    return {"label": label, "url": url}


def _ddid_record_links(row: dict[str, str]) -> list[dict[str, str]]:
    herb_or_food_id = _clean_text(row.get("Food_Herb_ID"))
    drug_id = _clean_text(row.get("Drug_ID"))
    item_type = (_clean_text(row.get("Type")) or "").casefold()

    links: list[dict[str, str]] = []
    if herb_or_food_id:
        segment = "food" if item_type == "food" or herb_or_food_id.startswith("F") else "herb"
        label = "DDID food page" if segment == "food" else "DDID herb page"
        herb_or_food_link = _make_record_link(
            label,
            f"https://bddg.hznu.edu.cn/ddid/{segment}/{herb_or_food_id}/",
        )
        if herb_or_food_link:
            links.append(herb_or_food_link)
    if drug_id:
        drug_link = _make_record_link(
            "DDID drug page",
            f"https://bddg.hznu.edu.cn/ddid/drug/{drug_id}/",
        )
        if drug_link:
            links.append(drug_link)
    return links


def _merge_record_links(*groups: list[dict[str, str]]) -> list[dict[str, str]]:
    merged: list[dict[str, str]] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            url = _clean_text(item.get("url"))
            label = _clean_text(item.get("label"))
            if not url or not label or url in seen:
                continue
            seen.add(url)
            merged.append({"label": label, "url": url})
    return merged


def _flockhart_record_links(target_name: Any) -> list[dict[str, str]]:
    normalized_target = _normalize_key(target_name)
    links = [
        {"label": "Flockhart CYP table", "url": _FLOCKHART_MAIN_URL},
        {"label": "FDA DDI table", "url": _FDA_DDI_TABLE_URL},
    ]
    if normalized_target and normalized_target in _FLOCKHART_REFERENCE_URLS:
        links.append(
            {
                "label": f"Flockhart references: {str(target_name).strip()}",
                "url": _FLOCKHART_REFERENCE_URLS[normalized_target],
            }
        )
    return links


def _ddid_evidence_rank(row: dict[str, str]) -> int:
    design = (_clean_text(row.get("Experimental_Design")) or "").casefold()
    species = (_clean_text(row.get("Experimental_Species")) or "").casefold()
    reference = (_clean_text(row.get("Reference")) or "").casefold()
    result = (_clean_text(row.get("Result")) or "").casefold()
    relationship = (_clean_text(row.get("Relationship_classification")) or "").casefold()

    if "case report" in design or "case report" in reference:
        return 5
    if species == "homo sapiens" or "healthy volunteer" in design or "randomized" in design:
        return 6
    if any(token in design for token in ("cell", "in vitro")) or "cell" in result:
        return 1
    if species and species not in {"na", ""}:
        return 2
    if "drugbank" in relationship or "package insert" in relationship:
        return 4
    return 0


def _ddid_row_score(row: dict[str, str]) -> tuple[int, int, int, int, int]:
    return (
        _ddid_evidence_rank(row),
        int(bool(_clean_text(row.get("DOI")))),
        int(bool(_clean_text(row.get("PMID")))),
        int(bool(_clean_text(row.get("Potential_Target")))),
        int(bool(_clean_text(row.get("Conclusion")))),
    )


@lru_cache(maxsize=1)
def _load_ayurveda_entries() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    herb_lookup: dict[str, dict[str, Any]] = {}
    if not _AYURVEDA_DATA_PATH.exists():
        return herb_lookup, herb_lookup

    payload = json.loads(_AYURVEDA_DATA_PATH.read_text(encoding="utf-8"))
    ddid_alias_lookup: dict[str, dict[str, Any]] = {}
    for entry in payload:
        keys = [
            entry.get("english_name"),
            entry.get("scientific_name"),
            *(entry.get("aliases") or []),
            *(entry.get("ddid_matches") or []),
        ]
        for key in keys:
            normalized = _normalize_key(key)
            if not normalized:
                continue
            herb_lookup.setdefault(normalized, entry)
        for key in entry.get("ddid_matches") or []:
            normalized = _normalize_key(key)
            if normalized:
                ddid_alias_lookup[normalized] = entry
    return herb_lookup, ddid_alias_lookup


@lru_cache(maxsize=1)
def _load_ddid_pairs() -> dict[tuple[str, str], list[dict[str, str]]]:
    data_dir = _default_ddid_data_dir()
    path = data_dir / "Interaction Information.csv"
    if not path.exists():
        return {}

    rows = _read_csv_rows(path)
    pairs: dict[tuple[str, str], list[dict[str, str]]] = {}
    for row in rows:
        herb_name = _clean_text(row.get("Food_Herb_Name"))
        drug_name = _clean_text(row.get("Drug_Name"))
        herb_key = _normalize_key(herb_name)
        drug_key = _normalize_key(drug_name)
        if not herb_key or not drug_key:
            continue
        pairs.setdefault((herb_key, drug_key), []).append(row)
    return pairs


@lru_cache(maxsize=1)
def _load_herb_cyp_pairs() -> dict[tuple[str, str, str], dict[str, Any]]:
    if not _HERB_CYP_DATA_PATH.exists():
        return {}

    payload = json.loads(_HERB_CYP_DATA_PATH.read_text(encoding="utf-8"))
    pairs: dict[tuple[str, str, str], dict[str, Any]] = {}
    for herb in payload.get("herbs", []):
        herb_keys: list[str] = []
        for value in [
            herb.get("name"),
            herb.get("scientific_name"),
            *(herb.get("aliases") or []),
        ]:
            key = _normalize_key(value)
            if key and key not in herb_keys:
                herb_keys.append(key)
        for interaction in herb.get("interactions") or []:
            relationship = _clean_text(interaction.get("relationship"))
            target_name = _clean_text(interaction.get("target_name"))
            target_type = _clean_text(interaction.get("target_type"))
            if not relationship or not target_name or target_type != "Enzyme":
                continue
            target_key = _normalize_key(target_name)
            if not target_key:
                continue
            context = {
                "reference": _clean_text(interaction.get("reference")),
                "strength": _clean_text(interaction.get("strength")),
                "confidence": interaction.get("confidence"),
                "evidence_basis": "literature",
                "scientific_name": _clean_text(herb.get("scientific_name")),
                "provenance_note": (
                    f"Matched against the curated herb-enzyme literature file for "
                    f"{_clean_text(herb.get('name')) or 'this herb'}"
                    + (
                        f" (evidence level: {_clean_text(herb.get('evidence_level'))})."
                        if _clean_text(herb.get("evidence_level"))
                        else "."
                    )
                ),
                "backing_source_key": "published_literature",
            }
            for herb_key in herb_keys:
                pairs[(herb_key, relationship, target_key)] = context
    return pairs


@lru_cache(maxsize=1)
def _load_cyp_catalog_entries() -> dict[tuple[str, str, str], dict[str, Any]]:
    if not _CYP450_DATA_PATH.exists():
        return {}

    payload = json.loads(_CYP450_DATA_PATH.read_text(encoding="utf-8"))
    catalog: dict[tuple[str, str, str], dict[str, Any]] = {}

    for enzyme in payload.get("enzymes", []):
        target_name = _clean_text(enzyme.get("name"))
        target_key = _normalize_key(target_name)
        if not target_name or not target_key:
            continue
        note = _clean_text(enzyme.get("notes"))
        for relation_name, bucket_key in (
            ("IS_SUBSTRATE_OF", "substrates"),
            ("INHIBITS", "inhibitors"),
            ("INDUCES", "inducers"),
        ):
            relation_payload = enzyme.get(bucket_key) or {}
            for bucket_name, names in relation_payload.items():
                for raw_name in names or []:
                    source_key = _normalize_key(raw_name)
                    if not source_key:
                        continue
                    catalog[(source_key, relation_name, target_key)] = {
                        "bucket_value": _clean_text(bucket_name),
                        "provenance_note": note,
                        "backing_source_key": "fda_ddi_table",
                        "record_links": _flockhart_record_links(target_name),
                    }

    for transporter in payload.get("transporters", []):
        target_name = _clean_text(transporter.get("name"))
        target_key = _normalize_key(target_name)
        if not target_name or not target_key:
            continue
        note = _clean_text(transporter.get("notes"))
        for relation_name, bucket_key in (
            ("IS_SUBSTRATE_OF", "substrates"),
            ("INHIBITS", "inhibitors"),
            ("INDUCES", "inducers"),
        ):
            for raw_name in transporter.get(bucket_key) or []:
                source_key = _normalize_key(raw_name)
                if not source_key:
                    continue
                catalog[(source_key, relation_name, target_key)] = {
                    "provenance_note": note,
                    "backing_source_key": "fda_ddi_table",
                    "record_links": [
                        {"label": "FDA DDI table", "url": _FDA_DDI_TABLE_URL},
                    ],
                }

    return catalog


def _candidate_herb_keys(herb_name: Any) -> list[str]:
    normalized = _normalize_key(herb_name)
    if not normalized:
        return []

    herb_lookup, _ddid_alias_lookup = _load_ayurveda_entries()
    entry = herb_lookup.get(normalized)
    if not entry:
        return [normalized]

    candidates: list[str] = []
    for value in [
        entry.get("english_name"),
        entry.get("scientific_name"),
        *(entry.get("aliases") or []),
        *(entry.get("ddid_matches") or []),
        herb_name,
    ]:
        key = _normalize_key(value)
        if key and key not in candidates:
            candidates.append(key)
    return candidates


def lookup_ddid_pair_details(herb_name: Any, drug_name: Any) -> dict[str, Any]:
    drug_key = _normalize_key(drug_name)
    if not drug_key:
        return {}

    pair_index = _load_ddid_pairs()
    rows: list[dict[str, str]] = []
    for herb_key in _candidate_herb_keys(herb_name):
        rows.extend(pair_index.get((herb_key, drug_key), []))
    if not rows:
        return {}

    best = max(rows, key=_ddid_row_score)
    reference = _append_reference_bits(
        _clean_text(best.get("Reference")),
        _clean_text(best.get("DOI")),
        _clean_text(best.get("PMID")),
    )
    return {
        "reference": reference,
        "doi": _clean_text(best.get("DOI")),
        "pmid": _clean_text(best.get("PMID")),
        "study_count": len(rows),
        "ddid_herb_id": _clean_text(best.get("Food_Herb_ID")),
        "ddid_drug_id": _clean_text(best.get("Drug_ID")),
        "record_locator": " | ".join(
            part
            for part in (
                f"ddid_herb_id={_clean_text(best.get('Food_Herb_ID'))}" if _clean_text(best.get("Food_Herb_ID")) else "",
                f"ddid_drug_id={_clean_text(best.get('Drug_ID'))}" if _clean_text(best.get("Drug_ID")) else "",
            )
            if part
        ),
        "record_links": _ddid_record_links(best),
        "experimental_design": _clean_text(best.get("Experimental_Design")),
        "experimental_species": _clean_text(best.get("Experimental_Species")),
        "potential_target": _clean_text(best.get("Potential_Target")),
        "component": _clean_text(best.get("Component")),
        "ddid_effect": _clean_text(best.get("Effect")),
    }


def lookup_ayurveda_context(herb_name: Any, drug_name: Any, mechanism: Any) -> dict[str, Any]:
    herb_key = _normalize_key(herb_name)
    if not herb_key:
        return {}

    herb_lookup, _ddid_alias_lookup = _load_ayurveda_entries()
    entry = herb_lookup.get(herb_key)
    if not entry:
        return {}

    drug_term_key = _normalize_key(mechanism) or _normalize_key(drug_name)
    matched_interaction: dict[str, Any] | None = None
    for interaction in entry.get("known_drug_interactions") or []:
        if _normalize_key(interaction.get("drug_or_class")) == drug_term_key:
            matched_interaction = interaction
            break

    result: dict[str, Any] = {
        "provenance_note": _clean_text(entry.get("notes")),
        "evidence_basis": _clean_text((matched_interaction or {}).get("evidence_basis")),
    }

    evidence_basis = str(result.get("evidence_basis") or "").casefold()
    note_text = str(result.get("provenance_note") or "").casefold()
    ddid_details = lookup_ddid_pair_details(herb_name, drug_name)
    if evidence_basis in {"ddid", "mixed"} or (bool(entry.get("ddid_backed")) and ddid_details):
        if ddid_details:
            result["backing_source_key"] = "ddid"
        for key, value in ddid_details.items():
            if value not in (None, "", [], {}):
                result.setdefault(key, value)
    elif "nccih" in note_text:
        result["backing_source_key"] = "nccih"

    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def lookup_cyp_mechanism_context(
    *,
    source_name: Any,
    herb_name: Any,
    relation_type: Any,
    target_name: Any,
) -> dict[str, Any]:
    normalized_relation = _clean_text(relation_type)
    target_key = _normalize_key(target_name)
    if not normalized_relation or not target_key:
        return {}

    herb_key = _normalize_key(herb_name)
    if herb_key:
        herb_context = _load_herb_cyp_pairs().get((herb_key, normalized_relation, target_key))
        if herb_context:
            return {key: value for key, value in herb_context.items() if value not in (None, "", [], {})}

    source_key = _normalize_key(source_name)
    if not source_key:
        return {}

    catalog_context = _load_cyp_catalog_entries().get((source_key, normalized_relation, target_key))
    if not catalog_context:
        return {}

    result = dict(catalog_context)
    bucket_value = _clean_text(result.pop("bucket_value", None))
    if normalized_relation == "IS_SUBSTRATE_OF" and bucket_value:
        result.setdefault("fraction", bucket_value)
    elif bucket_value:
        result.setdefault("strength", bucket_value)
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def resolve_local_provenance(citation: dict[str, Any]) -> dict[str, Any]:
    source_key = str(citation.get("source_key") or "").strip().lower()
    herb_name = citation.get("herb")
    drug_name = citation.get("drug")
    mechanism = citation.get("mechanism")
    relation_type = citation.get("relation_type")
    target_name = citation.get("enzyme") or citation.get("transporter")

    if source_key == "ddid":
        return lookup_ddid_pair_details(herb_name, drug_name)

    if source_key == "curated_ayurveda":
        return lookup_ayurveda_context(herb_name, drug_name, mechanism)

    if source_key in {"cyp450_curated", "transporter_curated"}:
        resolved = lookup_cyp_mechanism_context(
            source_name=drug_name or herb_name,
            herb_name=herb_name,
            relation_type=relation_type,
            target_name=target_name,
        )
        if resolved:
            return resolved
        return {
            "backing_source_key": "fda_ddi_table",
            "record_links": _flockhart_record_links(target_name),
            "provenance_note": "Curated from the FDA CYP table and Flockhart/CredibleMeds interaction references.",
        }

    if source_key in {"primekg_derived", "primekg_target_derived", "primekg_transporter_derived"}:
        return {
            "backing_source_key": "primekg",
            "provenance_note": "Internal mechanism edge derived from PrimeKG relationships to expand multihop coverage.",
        }

    if source_key == "crediblemeds_curated":
        return {
            "backing_source_key": "crediblemeds_pdf",
            "provenance_note": "Curated QT-risk layer grounded in CredibleMeds classifications.",
        }

    if source_key == "electrolyte_curated":
        return {
            "evidence_basis": "curated",
            "provenance_note": "Local curated potassium-effect rule layer from the structured electrolyte lists in cyp450_data.json.",
        }

    if source_key in {"electrolyte_expanded", "cns_depressant_curated"}:
        return {
            "evidence_basis": "derived_rule",
            "provenance_note": "Internal expansion rule used to add mechanism coverage where no exact external record is stored on the edge.",
        }

    if source_key == "published_literature":
        resolved = lookup_cyp_mechanism_context(
            source_name=drug_name or herb_name,
            herb_name=herb_name,
            relation_type=relation_type,
            target_name=target_name,
        )
        if resolved:
            return resolved

    return {}
