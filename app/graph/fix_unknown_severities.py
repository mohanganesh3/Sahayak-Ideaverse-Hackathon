"""Infer clinically useful severity labels for DDInter edges currently marked unknown."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 1_000
DEFAULT_LLM_BATCH_SIZE = 10
DEFAULT_GROQ_MODEL = "llama-3.3-70b-versatile"
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
DEFAULT_TIMEOUT_SECONDS = 60

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_LLM_LINE_RE = re.compile(r"^(?P<rel_id>[A-Za-z0-9:.-]+)\s*:\s*(?P<severity>major|moderate|minor)\s*$", re.IGNORECASE)

FETCH_UNKNOWN_QUERY = """
MATCH (a:Drug)-[r:INTERACTS_WITH {source: 'ddinter', severity: 'unknown'}]->(b:Drug)
OPTIONAL MATCH (a)-[effect:COPRESCRIPTION_EFFECT]-(b)
WITH a, b, r, head(collect(effect)) AS effect
RETURN elementId(r) AS rel_id,
       a.generic_name AS drug_a,
       b.generic_name AS drug_b,
       coalesce(a.drug_class, '') AS class_a,
       coalesce(b.drug_class, '') AS class_b,
       coalesce(a.is_nti, false) AS is_nti_a,
       coalesce(b.is_nti, false) AS is_nti_b,
       coalesce(r.mechanism, '') AS mechanism,
       effect IS NOT NULL AS has_twosides,
       coalesce(effect.adverse_events, []) AS adverse_events
ORDER BY a.generic_name, b.generic_name
"""

UPDATE_SEVERITY_BATCH_QUERY = """
UNWIND $rows AS row
MATCH ()-[r:INTERACTS_WITH]->()
WHERE elementId(r) = row.rel_id
  AND r.source = 'ddinter'
  AND r.severity = 'unknown'
SET r.original_severity = coalesce(r.original_severity, 'unknown'),
    r.severity = row.severity,
    r.severity_source = 'inferred',
    r.severity_inference_method = row.method,
    r.severity_inference_evidence = row.evidence
RETURN count(r) AS updated
"""

VERIFY_DDINTER_QUERY = """
MATCH ()-[r:INTERACTS_WITH {source:'ddinter'}]->()
RETURN r.severity AS severity, count(r) AS count
ORDER BY count DESC, severity
"""

VERIFY_ALL_QUERY = """
MATCH ()-[r:INTERACTS_WITH]->()
RETURN r.severity AS severity, count(r) AS count
ORDER BY count DESC, severity
"""

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

MAJOR_EVENT_KEYWORDS = (
    "death",
    "cardiac arrest",
    "respiratory failure",
    "anaphylactic",
    "anaphylaxis",
    "hepatic failure",
    "liver failure",
    "renal failure",
    "kidney failure",
    "seizure",
    "hemorrhage",
    "haemorrhage",
    "stroke",
)

MODERATE_EVENT_KEYWORDS = (
    "hospitalization",
    "hospitalisation",
    "arrhythmia",
    "hypotension",
    "hyperkalemia",
    "hyperkalaemia",
    "hypoglycemia",
    "hypoglycaemia",
    "bleeding",
)

NTI_NAMES = {
    "warfarin",
    "digoxin",
    "lithium",
    "phenytoin",
    "theophylline",
    "carbamazepine",
    "cyclosporine",
    "tacrolimus",
}

CNS_DEPRESSANT_CLASS_KEYWORDS = (
    "opioid",
    "benzodiazepine",
    "sedative",
    "hypnotic",
    "barbiturate",
    "antipsychotic",
    "anxiolytic",
)

CNS_DEPRESSANT_NAMES = {
    "alprazolam",
    "clonazepam",
    "diazepam",
    "lorazepam",
    "midazolam",
    "zolpidem",
    "zopiclone",
    "eszopiclone",
    "quetiapine",
    "olanzapine",
    "risperidone",
    "haloperidol",
    "chlorpromazine",
    "morphine",
    "codeine",
    "tramadol",
    "fentanyl",
    "oxycodone",
    "hydromorphone",
    "methadone",
    "buprenorphine",
}

ANTICOAGULANT_CLASS_KEYWORDS = (
    "anticoagulant",
    "antiplatelet",
)

ANTICOAGULANT_NAMES = {
    "warfarin",
    "heparin",
    "enoxaparin",
    "apixaban",
    "rivaroxaban",
    "dabigatran",
    "edoxaban",
    "fondaparinux",
    "aspirin",
    "clopidogrel",
    "prasugrel",
    "ticagrelor",
    "dipyridamole",
    "cilostazol",
}

ANTIHYPERTENSIVE_CLASS_KEYWORDS = (
    "antihypertensive",
    "ace inhibitor",
    "angiotensin converting enzyme inhibitor",
    "angiotensin receptor blocker",
    "arb",
    "beta blocker",
    "calcium channel blocker",
    "ccb",
    "thiazide",
    "diuretic",
    "vasodilator",
    "alpha blocker",
)

ANTIHYPERTENSIVE_NAMES = {
    "amlodipine",
    "telmisartan",
    "losartan",
    "valsartan",
    "ramipril",
    "lisinopril",
    "enalapril",
    "metoprolol",
    "atenolol",
    "bisoprolol",
    "nifedipine",
    "verapamil",
    "diltiazem",
    "hydrochlorothiazide",
    "chlorthalidone",
    "spironolactone",
    "furosemide",
    "torasemide",
    "clonidine",
    "doxazosin",
}

ANTIDIABETIC_CLASS_KEYWORDS = (
    "antidiabetic",
    "biguanide",
    "sulfonylurea",
    "insulin",
    "dpp 4",
    "dpp-4",
    "glp 1",
    "glp-1",
    "sglt2",
    "thiazolidinedione",
    "meglitinide",
    "alpha glucosidase",
)

ANTIDIABETIC_NAMES = {
    "metformin",
    "glimepiride",
    "glipizide",
    "gliclazide",
    "pioglitazone",
    "rosiglitazone",
    "acarbose",
    "voglibose",
    "repaglinide",
    "nateglinide",
    "sitagliptin",
    "vildagliptin",
    "linagliptin",
    "dapagliflozin",
    "empagliflozin",
    "canagliflozin",
    "insulin",
}

PPI_CLASS_KEYWORDS = (
    "ppi",
    "proton pump inhibitor",
)

PPI_NAMES = {
    "pantoprazole",
    "omeprazole",
    "esomeprazole",
    "rabeprazole",
    "lansoprazole",
    "dexlansoprazole",
}

GASTRIC_PH_SENSITIVE_NAMES = {
    "atazanavir",
    "rilpivirine",
    "erlotinib",
    "gefitinib",
    "dasatinib",
    "ketoconazole",
    "itraconazole",
    "posaconazole",
    "cefuroxime",
}


@dataclass(frozen=True, slots=True)
class UnknownInteraction:
    rel_id: str
    drug_a: str
    drug_b: str
    class_a: str
    class_b: str
    is_nti_a: bool
    is_nti_b: bool
    mechanism: str
    has_twosides: bool
    adverse_events: list[str]


@dataclass(frozen=True, slots=True)
class SeverityUpdate:
    rel_id: str
    severity: str
    method: str
    evidence: list[str]


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    return _WHITESPACE_RE.sub(" ", value.replace("\x00", " ").replace("\xa0", " ")).strip()


def _normalize(value: str | None) -> str:
    cleaned = _clean_text(value)
    if not cleaned:
        return ""
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _contains_any(texts: list[str], keywords: tuple[str, ...]) -> tuple[bool, list[str]]:
    normalized_texts = [_normalize(text) for text in texts if _clean_text(text)]
    matched: list[str] = []
    for keyword in keywords:
        normalized_keyword = _normalize(keyword)
        if any(normalized_keyword in text for text in normalized_texts):
            matched.append(keyword)
    return bool(matched), matched


def _classify_from_twosides(interaction: UnknownInteraction) -> SeverityUpdate | None:
    if not interaction.has_twosides:
        return None
    major_hit, major_keywords = _contains_any(interaction.adverse_events, MAJOR_EVENT_KEYWORDS)
    if major_hit:
        return SeverityUpdate(
            rel_id=interaction.rel_id,
            severity="major",
            method="twosides",
            evidence=major_keywords,
        )
    moderate_hit, moderate_keywords = _contains_any(interaction.adverse_events, MODERATE_EVENT_KEYWORDS)
    if moderate_hit:
        return SeverityUpdate(
            rel_id=interaction.rel_id,
            severity="moderate",
            method="twosides",
            evidence=moderate_keywords,
        )
    evidence = [_clean_text(event) for event in interaction.adverse_events[:5] if _clean_text(event)]
    return SeverityUpdate(
        rel_id=interaction.rel_id,
        severity="minor",
        method="twosides",
        evidence=evidence or ["twosides_pair_without_high_risk_keywords"],
    )


def _class_matches(drug_class: str, keywords: tuple[str, ...]) -> bool:
    normalized = _normalize(drug_class)
    return bool(normalized) and any(_normalize(keyword) in normalized for keyword in keywords)


def _name_matches(drug_name: str, candidates: set[str]) -> bool:
    normalized = _normalize(drug_name)
    return normalized in candidates


def _is_nti(interaction: UnknownInteraction) -> bool:
    return (
        interaction.is_nti_a
        or interaction.is_nti_b
        or _name_matches(interaction.drug_a, NTI_NAMES)
        or _name_matches(interaction.drug_b, NTI_NAMES)
    )


def _is_cns_depressant(drug_name: str, drug_class: str) -> bool:
    return _name_matches(drug_name, CNS_DEPRESSANT_NAMES) or _class_matches(drug_class, CNS_DEPRESSANT_CLASS_KEYWORDS)


def _is_anticoagulant_or_antiplatelet(drug_name: str, drug_class: str) -> bool:
    return _name_matches(drug_name, ANTICOAGULANT_NAMES) or _class_matches(drug_class, ANTICOAGULANT_CLASS_KEYWORDS)


def _is_antihypertensive(drug_name: str, drug_class: str) -> bool:
    return _name_matches(drug_name, ANTIHYPERTENSIVE_NAMES) or _class_matches(drug_class, ANTIHYPERTENSIVE_CLASS_KEYWORDS)


def _is_antidiabetic(drug_name: str, drug_class: str) -> bool:
    return _name_matches(drug_name, ANTIDIABETIC_NAMES) or _class_matches(drug_class, ANTIDIABETIC_CLASS_KEYWORDS)


def _is_ppi(drug_name: str, drug_class: str) -> bool:
    return _name_matches(drug_name, PPI_NAMES) or _class_matches(drug_class, PPI_CLASS_KEYWORDS)


def _is_gastric_ph_sensitive(drug_name: str) -> bool:
    return _name_matches(drug_name, GASTRIC_PH_SENSITIVE_NAMES)


def _classify_from_rules(interaction: UnknownInteraction) -> SeverityUpdate | None:
    if _is_nti(interaction):
        return SeverityUpdate(
            rel_id=interaction.rel_id,
            severity="major",
            method="pharmacology_rule",
            evidence=["narrow_therapeutic_index"],
        )

    drug_a_cns = _is_cns_depressant(interaction.drug_a, interaction.class_a)
    drug_b_cns = _is_cns_depressant(interaction.drug_b, interaction.class_b)
    if drug_a_cns and drug_b_cns:
        return SeverityUpdate(
            rel_id=interaction.rel_id,
            severity="major",
            method="pharmacology_rule",
            evidence=["dual_cns_depressants"],
        )

    drug_a_antithrombotic = _is_anticoagulant_or_antiplatelet(interaction.drug_a, interaction.class_a)
    drug_b_antithrombotic = _is_anticoagulant_or_antiplatelet(interaction.drug_b, interaction.class_b)
    if drug_a_antithrombotic and drug_b_antithrombotic:
        return SeverityUpdate(
            rel_id=interaction.rel_id,
            severity="major",
            method="pharmacology_rule",
            evidence=["dual_anticoagulant_or_antiplatelet"],
        )

    drug_a_antihypertensive = _is_antihypertensive(interaction.drug_a, interaction.class_a)
    drug_b_antihypertensive = _is_antihypertensive(interaction.drug_b, interaction.class_b)
    if drug_a_antihypertensive and drug_b_antihypertensive:
        return SeverityUpdate(
            rel_id=interaction.rel_id,
            severity="moderate",
            method="pharmacology_rule",
            evidence=["dual_antihypertensives"],
        )

    drug_a_antidiabetic = _is_antidiabetic(interaction.drug_a, interaction.class_a)
    drug_b_antidiabetic = _is_antidiabetic(interaction.drug_b, interaction.class_b)
    if drug_a_antidiabetic and drug_b_antidiabetic:
        return SeverityUpdate(
            rel_id=interaction.rel_id,
            severity="moderate",
            method="pharmacology_rule",
            evidence=["dual_antidiabetics"],
        )

    drug_a_ppi = _is_ppi(interaction.drug_a, interaction.class_a)
    drug_b_ppi = _is_ppi(interaction.drug_b, interaction.class_b)
    if (drug_a_ppi and _is_gastric_ph_sensitive(interaction.drug_b)) or (drug_b_ppi and _is_gastric_ph_sensitive(interaction.drug_a)):
        return SeverityUpdate(
            rel_id=interaction.rel_id,
            severity="minor",
            method="pharmacology_rule",
            evidence=["ppi_with_gastric_ph_sensitive_drug"],
        )

    return None


def _call_groq_for_batch(
    interactions: list[UnknownInteraction],
    *,
    groq_api_key: str,
    groq_model: str,
) -> dict[str, str]:
    if not groq_api_key:
        return {}

    user_lines = [
        f"{interaction.rel_id}: {interaction.mechanism}"
        for interaction in interactions
        if _clean_text(interaction.mechanism)
    ]
    if not user_lines:
        return {}

    payload = {
        "model": groq_model,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You classify drug interaction severity. "
                    "For each input line in the form '<id>: <mechanism>', reply with one line "
                    "in the exact form '<id>: major|moderate|minor'. Do not add explanations."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Given these drug interaction mechanisms, classify the clinical severity.\n"
                    "Use exactly one of: major, moderate, minor.\n"
                    "major = life-threatening or permanent damage possible,\n"
                    "moderate = may require medical intervention or monitoring,\n"
                    "minor = minimal clinical significance.\n\n"
                    + "\n".join(user_lines)
                ),
            },
        ],
    }
    response = requests.post(
        GROQ_URL,
        headers={
            "Authorization": f"Bearer {groq_api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=DEFAULT_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]

    classifications: dict[str, str] = {}
    for line in content.splitlines():
        match = _LLM_LINE_RE.match(_clean_text(line))
        if match:
            classifications[match.group("rel_id")] = match.group("severity").casefold()
    return classifications


def _classify_with_llm(
    interactions: list[UnknownInteraction],
    *,
    groq_api_key: str,
    groq_model: str,
    llm_batch_size: int,
) -> list[SeverityUpdate]:
    candidates = [interaction for interaction in interactions if _clean_text(interaction.mechanism)]
    if not candidates:
        return []
    if not groq_api_key:
        LOGGER.warning("GROQ_API_KEY is not configured; skipping LLM severity classification.")
        return []

    updates: list[SeverityUpdate] = []
    for start in range(0, len(candidates), llm_batch_size):
        batch = candidates[start : start + llm_batch_size]
        try:
            classifications = _call_groq_for_batch(
                batch,
                groq_api_key=groq_api_key,
                groq_model=groq_model,
            )
        except requests.RequestException as exc:
            LOGGER.warning("Groq severity classification batch failed: %s", exc)
            continue

        for interaction in batch:
            severity = classifications.get(interaction.rel_id)
            if severity in {"major", "moderate", "minor"}:
                updates.append(
                    SeverityUpdate(
                        rel_id=interaction.rel_id,
                        severity=severity,
                        method="llm_groq",
                        evidence=[_clean_text(interaction.mechanism)],
                    )
                )
    return updates


def _default_updates(interactions: list[UnknownInteraction]) -> list[SeverityUpdate]:
    return [
        SeverityUpdate(
            rel_id=interaction.rel_id,
            severity="moderate",
            method="default_conservative",
            evidence=["default_moderate_for_remaining_unknown"],
        )
        for interaction in interactions
    ]


def _fetch_unknown_interactions(driver: Driver, database: str) -> list[UnknownInteraction]:
    with driver.session(database=database) as session:
        return [
            UnknownInteraction(
                rel_id=record["rel_id"],
                drug_a=record["drug_a"],
                drug_b=record["drug_b"],
                class_a=_clean_text(record["class_a"]),
                class_b=_clean_text(record["class_b"]),
                is_nti_a=bool(record["is_nti_a"]),
                is_nti_b=bool(record["is_nti_b"]),
                mechanism=_clean_text(record["mechanism"]),
                has_twosides=bool(record["has_twosides"]),
                adverse_events=[_clean_text(event) for event in (record["adverse_events"] or []) if _clean_text(event)],
            )
            for record in session.run(FETCH_UNKNOWN_QUERY)
        ]


def _write_batch(tx: ManagedTransaction, rows: list[dict[str, Any]]) -> int:
    record = tx.run(UPDATE_SEVERITY_BATCH_QUERY, rows=rows).single()
    return int(record["updated"])


def _apply_updates(
    driver: Driver,
    database: str,
    updates: list[SeverityUpdate],
    *,
    batch_size: int,
) -> int:
    if not updates:
        return 0
    rows = [
        {
            "rel_id": update.rel_id,
            "severity": update.severity,
            "method": update.method,
            "evidence": update.evidence,
        }
        for update in updates
    ]
    written = 0
    with driver.session(database=database) as session:
        for start in range(0, len(rows), batch_size):
            batch = rows[start : start + batch_size]
            written += session.execute_write(_write_batch, batch)
    return written


def _verify(driver: Driver, database: str) -> dict[str, list[dict[str, Any]]]:
    with driver.session(database=database) as session:
        ddinter = [record.data() for record in session.run(VERIFY_DDINTER_QUERY)]
        all_sources = [record.data() for record in session.run(VERIFY_ALL_QUERY)]
    return {"ddinter": ddinter, "all_sources": all_sources}


def infer_unknown_severities(
    driver: Driver,
    *,
    database: str,
    batch_size: int,
    llm_batch_size: int,
    groq_api_key: str,
    groq_model: str,
) -> dict[str, Any]:
    unknown_interactions = _fetch_unknown_interactions(driver, database)
    LOGGER.info("Fetched %s DDInter edges with severity='unknown'.", f"{len(unknown_interactions):,}")

    remaining: dict[str, UnknownInteraction] = {interaction.rel_id: interaction for interaction in unknown_interactions}
    selected_updates: list[SeverityUpdate] = []
    method_counts = {"twosides": 0, "pharmacology_rule": 0, "llm_groq": 0, "default_conservative": 0}

    twosides_updates: list[SeverityUpdate] = []
    for interaction in list(remaining.values()):
        update = _classify_from_twosides(interaction)
        if update is None:
            continue
        twosides_updates.append(update)
        remaining.pop(interaction.rel_id, None)
    selected_updates.extend(twosides_updates)
    method_counts["twosides"] = len(twosides_updates)
    LOGGER.info("TwoSIDES strategy classified %s edges.", f"{len(twosides_updates):,}")

    rule_updates: list[SeverityUpdate] = []
    for interaction in list(remaining.values()):
        update = _classify_from_rules(interaction)
        if update is None:
            continue
        rule_updates.append(update)
        remaining.pop(interaction.rel_id, None)
    selected_updates.extend(rule_updates)
    method_counts["pharmacology_rule"] = len(rule_updates)
    LOGGER.info("Pharmacological rules classified %s edges.", f"{len(rule_updates):,}")

    llm_updates = _classify_with_llm(
        list(remaining.values()),
        groq_api_key=groq_api_key,
        groq_model=groq_model,
        llm_batch_size=llm_batch_size,
    )
    for update in llm_updates:
        remaining.pop(update.rel_id, None)
    selected_updates.extend(llm_updates)
    method_counts["llm_groq"] = len(llm_updates)
    LOGGER.info("LLM strategy classified %s edges.", f"{len(llm_updates):,}")

    default_updates = _default_updates(list(remaining.values()))
    for update in default_updates:
        remaining.pop(update.rel_id, None)
    selected_updates.extend(default_updates)
    method_counts["default_conservative"] = len(default_updates)
    LOGGER.info("Defaulted %s edges to moderate.", f"{len(default_updates):,}")

    written = _apply_updates(
        driver,
        database,
        selected_updates,
        batch_size=batch_size,
    )
    verification = _verify(driver, database)
    return {
        "unknown_edges_initial": len(unknown_interactions),
        "updates_selected": len(selected_updates),
        "updates_written": written,
        "method_counts": method_counts,
        "verification": verification,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Infer severity for DDInter edges currently marked unknown.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password.")
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Neo4j write batch size.")
    parser.add_argument("--llm-batch-size", type=int, default=DEFAULT_LLM_BATCH_SIZE, help="Mechanism classification batch size.")
    parser.add_argument("--groq-api-key", default=DEFAULT_GROQ_API_KEY, help="Groq API key for optional LLM classification.")
    parser.add_argument("--groq-model", default=DEFAULT_GROQ_MODEL, help="Groq model to use for LLM classification.")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        driver.verify_connectivity()
        result = infer_unknown_severities(
            driver,
            database=args.database,
            batch_size=args.batch_size,
            llm_batch_size=args.llm_batch_size,
            groq_api_key=args.groq_api_key,
            groq_model=args.groq_model,
        )
        LOGGER.info("Unknown severity repair complete: %s", json.dumps(result, ensure_ascii=False))
    finally:
        driver.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
