"""Repair Beers Criteria gaps by cross-referencing AGS 2023 criteria against the live graph.

This script patches three classes of omissions:
1. Table 4 "use with caution" drugs that the original ingest skipped.
2. Class/regimen criteria that are clinically meaningful search targets
   (for example, "sliding-scale insulin" and "systemic estrogens").
3. 2023 Table 8 legacy PIMs that were moved off the main tables because of
   low U.S. use or market status but are still considered potentially
   inappropriate per the AGS 2019/2023 guidance.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase

LOGGER = logging.getLogger(__name__)

DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_DATA_PATH = Path(__file__).resolve().parents[1] / "data" / "beers_criteria.json"

READ_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")

FIND_MATCHING_DRUGS_QUERY = """
MATCH (d:Drug)
WHERE toLower(coalesce(d.generic_name, '')) = toLower($name)
   OR toLower(coalesce(d.canonical_name, '')) = toLower($name)
   OR ANY(s IN coalesce(d.synonyms, []) WHERE toLower(s) = toLower($name))
RETURN elementId(d) AS node_id,
       d.generic_name AS generic_name,
       COUNT { (d)--() } AS degree
ORDER BY degree DESC, generic_name ASC
"""

UPDATE_EXISTING_DRUGS_QUERY = """
UNWIND $ids AS node_id
MATCH (d:Drug) WHERE elementId(d) = node_id
SET d.is_beers = true,
    d.beers_category = CASE
        WHEN coalesce(d.beers_category, '') = '' THEN $category
        WHEN d.beers_category CONTAINS $category THEN d.beers_category
        ELSE d.beers_category + ' | ' + $category
    END,
    d.beers_rationale = CASE
        WHEN coalesce(d.beers_rationale, '') = '' THEN $rationale
        WHEN d.beers_rationale CONTAINS $rationale THEN d.beers_rationale
        ELSE d.beers_rationale + ' | ' + $rationale
    END,
    d.beers_recommendation = CASE
        WHEN coalesce(d.beers_recommendation, '') = '' THEN $recommendation
        WHEN d.beers_recommendation CONTAINS $recommendation THEN d.beers_recommendation
        ELSE d.beers_recommendation + ' | ' + $recommendation
    END,
    d.beers_quality_of_evidence = CASE
        WHEN coalesce(d.beers_quality_of_evidence, '') = '' THEN $quality_of_evidence
        ELSE d.beers_quality_of_evidence
    END,
    d.beers_strength = CASE
        WHEN coalesce(d.beers_strength, '') = '' THEN $strength
        ELSE d.beers_strength
    END,
    d.beers_legacy = coalesce(d.beers_legacy, false) OR $is_legacy,
    d.beers_note = CASE
        WHEN coalesce($note, '') = '' THEN d.beers_note
        WHEN coalesce(d.beers_note, '') = '' THEN $note
        WHEN d.beers_note CONTAINS $note THEN d.beers_note
        ELSE d.beers_note + ' | ' + $note
    END
WITH d
MERGE (criteria:BeersCriteria {edition: 'AGS 2023'})
MERGE (d)-[flag:FLAGGED_BY {source: 'beers_2023', table: $table_name, criterion: $criterion_key}]->(criteria)
SET flag.category = $category,
    flag.rationale = $rationale,
    flag.recommendation = $recommendation,
    flag.quality_of_evidence = $quality_of_evidence,
    flag.strength = $strength,
    flag.note = $note,
    flag.is_legacy = $is_legacy
RETURN count(d) AS updated
"""

CREATE_DRUG_AND_FLAG_QUERY = """
MERGE (d:Drug {generic_name: $create_name})
ON CREATE SET d.rxcui = '',
              d.drug_class = '',
              d.atc_code = '',
              d.is_nti = false,
              d.is_beers = false,
              d.anticholinergic_score = 0
SET d.is_beers = true,
    d.beers_category = $category,
    d.beers_rationale = $rationale,
    d.beers_recommendation = $recommendation,
    d.beers_quality_of_evidence = $quality_of_evidence,
    d.beers_strength = $strength,
    d.beers_legacy = $is_legacy,
    d.beers_note = $note
WITH d
MERGE (criteria:BeersCriteria {edition: 'AGS 2023'})
MERGE (d)-[flag:FLAGGED_BY {source: 'beers_2023', table: $table_name, criterion: $criterion_key}]->(criteria)
SET flag.category = $category,
    flag.rationale = $rationale,
    flag.recommendation = $recommendation,
    flag.quality_of_evidence = $quality_of_evidence,
    flag.strength = $strength,
    flag.note = $note,
    flag.is_legacy = $is_legacy
RETURN elementId(d) AS node_id, d.generic_name AS generic_name
"""

VERIFY_TARGET_QUERY = """
UNWIND $names AS name
OPTIONAL MATCH (d:Drug)
WHERE toLower(coalesce(d.generic_name, '')) = toLower(name)
RETURN name,
       collect({
         generic_name: d.generic_name,
         is_beers: d.is_beers,
         beers_category: d.beers_category,
         beers_legacy: d.beers_legacy,
         beers_note: d.beers_note
       }) AS matches
ORDER BY name
"""

WEB_SOURCE_2023 = (
    "2023 AGS Beers Criteria Table 8: drugs removed from main tables remain potentially "
    "inappropriate per 2019 guidance when relevant outside the U.S. market context."
)
WEB_SOURCE_2019 = (
    "2019 AGS Beers Criteria Table 8 confirms Pentazocine oral was removed since 2015 "
    "because it is no longer on the U.S. market."
)


@dataclass(frozen=True, slots=True)
class BeersFlagRow:
    match_name: str
    create_name: str
    category: str
    rationale: str
    recommendation: str
    quality_of_evidence: str
    strength: str
    table_name: str
    criterion_key: str
    note: str = ""
    is_legacy: bool = False


ACTIVE_CLASS_EXPANSIONS: dict[str, tuple[str, ...]] = {
    "Estrogens with or without progestins (systemic oral or transdermal)": (
        "systemic estrogens",
        "oral estrogens",
        "transdermal estrogens",
        "estradiol",
        "conjugated estrogens",
        "esterified estrogens",
    ),
    "Sliding-scale insulin regimens without basal insulin": (
        "sliding-scale insulin",
        "short-acting insulin without basal insulin",
        "rapid-acting insulin without basal insulin",
    ),
    "Barbiturates": ("barbiturates",),
    "Growth hormone": ("growth hormone",),
    "Selected antidepressants, selected antiepileptics, antipsychotics, diuretics, and tramadol": (
        "SNRIs",
        "SSRIs",
        "tricyclic antidepressants",
        "antipsychotics",
        "diuretics",
    ),
}

# Table 8 entries from the official 2023 AGS paper that remain PIMs despite
# removal from the main 2023 tables for low U.S. use or market status.
LEGACY_TABLE8_ROWS: tuple[BeersFlagRow, ...] = (
    BeersFlagRow("carbinoxamine", "Carbinoxamine", "avoid_legacy_2019",
                 "Legacy Table 8 PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_carbinoxamine",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("clemastine", "Clemastine", "avoid_legacy_2019",
                 "Legacy Table 8 PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_clemastine",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("guanabenz", "Guanabenz", "avoid_legacy_2019",
                 "Legacy Table 8 PIM carried forward from 2019; not on the U.S. market in 2023.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_guanabenz",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("methyldopa", "Methyldopa", "avoid_legacy_2019",
                 "Legacy Table 8 PIM carried forward from 2019; removed from the main 2023 tables because it is not on the U.S. market, not because it became appropriate.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_methyldopa",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("reserpine", "Reserpine", "avoid_legacy_2019",
                 "Legacy Table 8 PIM carried forward from 2019; higher-dose reserpine was removed from the main 2023 tables because of U.S. market context.",
                 "Avoid doses >0.1 mg/day.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_reserpine",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("disopyramide", "Disopyramide", "avoid_legacy_2019",
                 "Legacy Table 8 PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_disopyramide",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("protriptyline", "Protriptyline", "avoid_legacy_2019",
                 "Legacy Table 8 PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_protriptyline",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("trimipramine", "Trimipramine", "avoid_legacy_2019",
                 "Legacy Table 8 PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_trimipramine",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("amobarbital", "Amobarbital", "avoid_legacy_2019",
                 "Legacy Table 8 barbiturate PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_amobarbital",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("butobarbital", "Butobarbital", "avoid_legacy_2019",
                 "Legacy Table 8 barbiturate PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_butobarbital",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("mephobarbital", "Mephobarbital", "avoid_legacy_2019",
                 "Legacy Table 8 barbiturate PIM carried forward from 2019; not on the U.S. market in 2023.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_mephobarbital",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("pentobarbital", "Pentobarbital", "avoid_legacy_2019",
                 "Legacy Table 8 barbiturate PIM carried forward from 2019; not on the U.S. market in 2023.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_pentobarbital",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("secobarbital", "Secobarbital", "avoid_legacy_2019",
                 "Legacy Table 8 barbiturate PIM carried forward from 2019; not on the U.S. market in 2023.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_secobarbital",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("flurazepam", "Flurazepam", "avoid_legacy_2019",
                 "Legacy Table 8 benzodiazepine PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_flurazepam",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("quazepam", "Quazepam", "avoid_legacy_2019",
                 "Legacy Table 8 benzodiazepine PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_quazepam",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("isoxsuprine", "Isoxsuprine", "avoid_legacy_2019",
                 "Legacy Table 8 vasodilator PIM carried forward from 2019; not on the U.S. market in 2023.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_isoxsuprine",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("chlorpropamide", "Chlorpropamide", "avoid_legacy_2019",
                 "Legacy Table 8 long-acting sulfonylurea PIM carried forward from 2019; removed from the main 2023 tables because it is not on the U.S. market, not because it became appropriate.",
                 "Avoid.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_chlorpropamide",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("fenoprofen", "Fenoprofen", "avoid_legacy_2019",
                 "Legacy Table 8 NSAID PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid chronic use.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_fenoprofen",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("ketoprofen", "Ketoprofen", "avoid_legacy_2019",
                 "Legacy Table 8 NSAID PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid chronic use.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_ketoprofen",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("meclofenamate", "Meclofenamate", "avoid_legacy_2019",
                 "Legacy Table 8 NSAID PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid chronic use.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_meclofenamate",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("mefenamic acid", "Mefenamic acid", "avoid_legacy_2019",
                 "Legacy Table 8 NSAID PIM carried forward from 2019; low U.S. use in 2023 does not imply appropriateness.",
                 "Avoid chronic use.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_mefenamic_acid",
                 WEB_SOURCE_2023, True),
    BeersFlagRow("tolmetin", "Tolmetin", "avoid_legacy_2019",
                 "Legacy Table 8 NSAID PIM carried forward from 2019; not on the U.S. market in 2023.",
                 "Avoid chronic use.", "Legacy per 2019/2023 AGS", "Strong", "table8_removed_since_2019", "legacy_tolmetin",
                 WEB_SOURCE_2023, True),
)

EXPLICITLY_NOT_ACTIVE_2023: dict[str, str] = {
    "methyldopa": "2023 Table 8 legacy PIM, not an active main-table criterion.",
    "pentazocine": "Removed from the AGS Beers Criteria in the 2019 update because oral pentazocine was no longer on the U.S. market; not an active 2023 criterion.",
    "trimethobenzamide": "Not supported as an active AGS 2023 Beers entry in the verified sources used for this repair.",
}


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\x00", " ").replace("\xa0", " ")).strip()
    return cleaned or None


def _normalize_key(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if not cleaned:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _resolve_data_path(path: Path) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"Beers JSON path does not exist: {resolved}")
    return resolved


def _load_json(path: Path) -> dict[str, Any]:
    last_error: UnicodeDecodeError | None = None
    for encoding in READ_ENCODINGS:
        try:
            return json.loads(path.read_text(encoding=encoding))
        except UnicodeDecodeError as exc:
            last_error = exc
    LOGGER.warning("Fallback utf-8 replacement decode for %s after %s", path.name, last_error)
    return json.loads(path.read_text(encoding="utf-8", errors="replace"))


def _smart_name(raw_name: str) -> str:
    cleaned = _clean_text(raw_name) or raw_name
    if cleaned.isupper() or any(char.isupper() for char in cleaned[1:]):
        return cleaned
    return " ".join(token.capitalize() for token in cleaned.split())


def _build_active_rows(payload: dict[str, Any]) -> list[BeersFlagRow]:
    rows: list[BeersFlagRow] = []

    def append_rows(
        table_name: str,
        category: str,
        entries: list[dict[str, Any]],
    ) -> None:
        for entry in entries:
            rationale = _clean_text(entry.get("rationale")) or ""
            recommendation = _clean_text(entry.get("recommendation")) or ""
            quality = _clean_text(entry.get("quality_of_evidence")) or ""
            strength = _clean_text(entry.get("strength")) or ""
            drug_or_class = _clean_text(entry.get("drug_or_class")) or ""
            base_key = _normalize_key(drug_or_class) or f"{table_name}_{len(rows)}"

            for drug_name in entry.get("drug_names", []):
                cleaned = _clean_text(drug_name)
                if not cleaned:
                    continue
                rows.append(
                    BeersFlagRow(
                        match_name=cleaned,
                        create_name=_smart_name(cleaned),
                        category=category,
                        rationale=rationale,
                        recommendation=recommendation,
                        quality_of_evidence=quality,
                        strength=strength,
                        table_name=table_name,
                        criterion_key=f"{base_key}:{_normalize_key(cleaned) or cleaned.casefold()}",
                    )
                )

            for label in entry.get("class_labels", []):
                cleaned = _clean_text(label)
                if not cleaned:
                    continue
                rows.append(
                    BeersFlagRow(
                        match_name=cleaned,
                        create_name=cleaned,
                        category=category,
                        rationale=rationale,
                        recommendation=recommendation,
                        quality_of_evidence=quality,
                        strength=strength,
                        table_name=table_name,
                        criterion_key=f"{base_key}:class:{_normalize_key(cleaned) or cleaned.casefold()}",
                        note="Class/regimen concept node added so text-based lookups can surface the Beers criterion explicitly.",
                    )
                )

            for expanded_name in ACTIVE_CLASS_EXPANSIONS.get(drug_or_class, ()):
                rows.append(
                    BeersFlagRow(
                        match_name=expanded_name,
                        create_name=_smart_name(expanded_name),
                        category=category,
                        rationale=rationale,
                        recommendation=recommendation,
                        quality_of_evidence=quality,
                        strength=strength,
                        table_name=table_name,
                        criterion_key=f"{base_key}:expanded:{_normalize_key(expanded_name) or expanded_name.casefold()}",
                        note="Expanded from AGS 2023 class/regimen criterion for matching and safety lookup.",
                    )
                )

    append_rows("table2", "avoid", payload.get("table2_avoid_in_older_adults", []))
    append_rows("table4", "use_with_caution", payload.get("table4_use_with_caution", []))
    append_rows("table6", "renal_adjust", payload.get("table6_renal_dose_adjustments", []))

    for entry in payload.get("table7_anticholinergic_drugs", []):
        drug_name = _clean_text(entry.get("drug_name"))
        if not drug_name:
            continue
        rows.append(
            BeersFlagRow(
                match_name=drug_name,
                create_name=_smart_name(drug_name),
                category="strong_anticholinergic",
                rationale="Strong anticholinergic properties in AGS Beers Table 7.",
                recommendation="Avoid when possible or minimize cumulative anticholinergic burden.",
                quality_of_evidence=_clean_text(entry.get("citation")) or "AGS 2023 Table 7",
                strength="Strong",
                table_name="table7",
                criterion_key=f"table7:{_normalize_key(drug_name) or drug_name.casefold()}",
            )
        )

    deduped: dict[tuple[str, str], BeersFlagRow] = {}
    for row in rows + list(LEGACY_TABLE8_ROWS):
        key = (row.table_name, _normalize_key(row.match_name) or row.match_name.casefold())
        deduped[key] = row
    return list(deduped.values())


def _find_matching_drugs(session, name: str) -> list[str]:
    return [record["node_id"] for record in session.run(FIND_MATCHING_DRUGS_QUERY, name=name)]


def _apply_flag(driver: Driver, database: str, row: BeersFlagRow) -> dict[str, Any]:
    with driver.session(database=database) as session:
        matching_ids = _find_matching_drugs(session, row.match_name)
        params = {
            "category": row.category,
            "rationale": row.rationale,
            "recommendation": row.recommendation,
            "quality_of_evidence": row.quality_of_evidence,
            "strength": row.strength,
            "table_name": row.table_name,
            "criterion_key": row.criterion_key,
            "note": row.note,
            "is_legacy": row.is_legacy,
        }
        if matching_ids:
            updated = session.run(UPDATE_EXISTING_DRUGS_QUERY, ids=matching_ids, **params).single()["updated"]
            return {"matched": updated, "created": 0}
        session.run(CREATE_DRUG_AND_FLAG_QUERY, create_name=row.create_name, **params).consume()
        return {"matched": 0, "created": 1}


def _verification_report(driver: Driver, database: str) -> list[dict[str, Any]]:
    names = [
        "methyldopa",
        "meperidine",
        "pentazocine",
        "trimethobenzamide",
        "mineral oil",
        "sliding-scale insulin",
        "desmopressin",
        "growth hormone",
        "oral estrogens",
        "testosterone",
        "megestrol",
        "chlorpropamide",
        "glyburide",
        "metoclopramide",
        "barbiturates",
        "somatropin",
        "estradiol",
        "conjugated estrogens",
        "phenobarbital",
        "primidone",
        "butalbital",
    ]
    with driver.session(database=database) as session:
        return [record.data() for record in session.run(VERIFY_TARGET_QUERY, names=names)]


def ingest(
    driver: Driver,
    data_path: Path,
    *,
    database: str = DEFAULT_NEO4J_DATABASE,
) -> dict[str, Any]:
    payload = _load_json(_resolve_data_path(data_path))
    rows = _build_active_rows(payload)

    summary = {
        "rows_considered": len(rows),
        "matched_updates": 0,
        "created_nodes": 0,
        "table4_rows": 0,
        "table8_legacy_rows": len(LEGACY_TABLE8_ROWS),
    }

    for row in rows:
        result = _apply_flag(driver, database, row)
        summary["matched_updates"] += result["matched"]
        summary["created_nodes"] += result["created"]
        if row.table_name == "table4":
            summary["table4_rows"] += 1

    with driver.session(database=database) as session:
        summary["beers_drugs_total"] = session.run(
            "MATCH (d:Drug) WHERE d.is_beers = true RETURN count(d) AS count"
        ).single()["count"]
        summary["legacy_beers_drugs_total"] = session.run(
            "MATCH (d:Drug) WHERE d.is_beers = true AND coalesce(d.beers_legacy, false) = true RETURN count(d) AS count"
        ).single()["count"]
        summary["table4_flagged_edges"] = session.run(
            "MATCH ()-[r:FLAGGED_BY {source:'beers_2023', table:'table4'}]->() RETURN count(r) AS count"
        ).single()["count"]
        summary["table8_flagged_edges"] = session.run(
            "MATCH ()-[r:FLAGGED_BY {source:'beers_2023', table:'table8_removed_since_2019'}]->() RETURN count(r) AS count"
        ).single()["count"]

    summary["verification"] = _verification_report(driver, database)
    summary["not_active_2023_notes"] = EXPLICITLY_NOT_ACTIVE_2023
    return summary


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repair AGS Beers 2023 gaps in the Neo4j graph.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data-path", type=Path, default=DEFAULT_DATA_PATH)
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI)
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER)
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD)
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE)
    parser.add_argument("--log-level", default="INFO")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    logging.basicConfig(level=getattr(logging, str(args.log_level).upper(), logging.INFO))

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        driver.verify_connectivity()
        summary = ingest(driver, args.data_path, database=args.database)
    finally:
        driver.close()

    LOGGER.info("Beers gap repair summary:\n%s", json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
