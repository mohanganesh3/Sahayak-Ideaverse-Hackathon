"""SAHAYAK Query Engine – Core brain of the drug safety check system.

Every function is defensively coded: never crashes on missing/null graph data,
always returns confidence scores, and logs every query for auditability.

Source layers:
  L1_direct       – Edge present in graph (DDInter, PrimeKG, Beers)
  L2_multihop     – Inferred via CYP enzymes / QT / electrolytes (indirect)
  L3_llm_assisted – LLM-enriched (filled by agents above this layer)
  L5_unknown      – Fallback when source cannot be determined
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.graph.neo4j_connection import get_driver
from app.services.citation_utils import (
    build_evidence_text,
    dedupe_citations,
    make_citation,
    source_summary_from_citations,
)

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────

_SEVERITY_RANK: dict[str | None, int] = {
    "critical": 5,
    "major": 4,
    "moderate": 3,
    "minor": 2,
    "unknown": 1,
    None: 1,
    "": 1,
}

_DIRECT_SOURCE_PRIORITY: dict[str, int] = {
    "ddinter": 60,
    "beers_2023": 55,
    "sentinel_curated": 50,
    "primekg": 40,
    "primekg_derived": 35,
    "knowledge_graph": 30,
    "unknown": 10,
}

_DIRECT_SEVERITY_SOURCE_PRIORITY: dict[str, int] = {
    "original": 60,
    "matched_ddinter": 55,
    "matched_beers_2023": 54,
    "twosides_inferred": 45,
    "inferred": 40,
    "pharmacology_rule": 35,
    "default_conservative": 25,
    "default_low_confidence": 20,
    "primekg_low_confidence_default": 15,
    "": 10,
    None: 10,
}

_CYP_INHIBITOR_STRENGTH: dict[str | None, int] = {
    "strong": 3,
    "moderate": 2,
    "weak": 1,
    None: 1,
    "": 1,
}

_CYP_SUBSTRATE_FRACTION: dict[str | None, int] = {
    "major": 3,
    "moderate": 2,
    "minor": 1,
    None: 1,
    "": 1,
}

_ACB_RISK_THRESHOLDS = [(0, 1, "low"), (1, 3, "moderate"), (3, 999, "high")]

_RXNORM_TIMEOUT_S = 5  # conservative timeout for clinical context

# ── Existing OCR / brand-normalization helpers (preserved public API) ────────

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_ALNUM_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")
_DOSAGE_FORM_RE = re.compile(
    r"\b(tablet|tablets|capsule|capsules|syrup|suspension|drops|drop|injection|cream|"
    r"ointment|gel|inhaler|respules|spray|patch|oral|liquid|mr|sr|xr|er|dt|md)\b"
)
_STRENGTH_UNIT_RE = re.compile(r"(?<=\d)\s*(mg|mcg|g|gm|ml)\b")

INDIAN_BRAND_SEARCH_QUERY = """
UNWIND $queries AS search_query
CALL {
    WITH search_query
    CALL db.index.fulltext.queryNodes('brand_name_fulltext', search_query) YIELD node, score
    WHERE node:IndianBrand
    RETURN node, score
    ORDER BY score DESC
    LIMIT $candidate_limit
}
WITH node, max(score) AS score
OPTIONAL MATCH (node)-[:CONTAINS]->(drug:Drug)
WITH node, score, collect(DISTINCT drug.generic_name) AS contained_drugs
RETURN node.brand_name AS brand_name,
       node.manufacturer AS manufacturer,
       node.composition AS composition,
       node.dosage_form AS dosage_form,
       contained_drugs,
       score
ORDER BY score DESC, size(contained_drugs) DESC, brand_name ASC
LIMIT $result_limit
"""


def _ordered_unique(values: Iterable[str]) -> list[str]:
    """Return values in first-seen order without duplicates."""
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _normalize_brand_text(value: str) -> str:
    """Normalize OCR-noisy brand text for fuzzy search."""
    normalized = _CAMEL_BOUNDARY_RE.sub(" ", value).casefold().strip()
    normalized = _ALNUM_BOUNDARY_RE.sub(" ", normalized)
    normalized = _NON_ALNUM_RE.sub(" ", normalized)
    normalized = _WHITESPACE_RE.sub(" ", normalized).strip()
    return normalized


def _brand_search_variants(query: str) -> list[str]:
    """Generate OCR-tolerant brand search variants for full-text lookup."""
    cleaned = _normalize_brand_text(query)
    if not cleaned:
        return []

    variants = [cleaned]
    without_forms = _WHITESPACE_RE.sub(" ", _DOSAGE_FORM_RE.sub(" ", cleaned)).strip()
    variants.append(without_forms)

    compact = without_forms.replace(" mg", "mg").replace(" mcg", "mcg")
    variants.append(compact)

    without_units = _WHITESPACE_RE.sub(" ", _STRENGTH_UNIT_RE.sub("", compact)).strip()
    variants.append(without_units)

    source_tokens = (without_units or compact or without_forms or cleaned).split()
    word_tokens = [t for t in source_tokens if not any(c.isdigit() for c in t)]
    strength_tokens = [t for t in source_tokens if any(c.isdigit() for c in t)]

    if word_tokens:
        variants.append(" ".join(word_tokens))
    if word_tokens and strength_tokens:
        variants.append(" ".join(word_tokens + strength_tokens))
        variants.append(" ".join(strength_tokens + word_tokens))

    return _ordered_unique(v for v in variants if v)


def _fulltext_queries(query: str) -> list[str]:
    """Expand search variants into Lucene-compatible full-text queries."""
    queries: list[str] = []
    for variant in _brand_search_variants(query):
        tokens = [t for t in variant.split() if t]
        if " " in variant:
            queries.append(f'"{variant}"')
        if tokens:
            queries.append(" ".join(f"+{t}" for t in tokens))
        queries.append(variant)
    return _ordered_unique(queries)


# ── Dataclasses – the public API contract ───────────────────────────────────

@dataclass
class ResolvedDrug:
    found: bool
    generic_name: str = ""
    rxcui: str = ""
    drug_class: str = ""
    is_beers: bool = False
    is_nti: bool = False
    anticholinergic_score: int = 0
    renal_dose_adjust: str = ""
    match_type: str = ""  # "exact"|"synonym"|"brand"|"fuzzy"|"rxnorm_api"|"not_found"
    confidence: float = 0.0
    raw_input: str = ""
    ingredients: list[str] = field(default_factory=list)  # populated for combo brands

    @property
    def resolved(self) -> bool:
        """Alias for ``found`` — compatibility property."""
        return self.found

    @property
    def resolution_method(self) -> str:
        """Alias for ``match_type`` — compatibility property."""
        return self.match_type


@dataclass
class ResolvedHerb:
    found: bool
    name: str = ""
    hindi_name: str = ""
    category: str = ""
    match_type: str = ""  # "exact"|"hindi"|"tamil"|"telugu"|"kannada"|"scientific"|"fuzzy"|"not_found"
    confidence: float = 0.0
    raw_input: str = ""
    herb_in_database: bool = False
    has_interaction_data: bool = False  # has at least one INTERACTS_WITH_DRUG edge

    @property
    def resolved(self) -> bool:
        """Alias for ``found`` — compatibility property."""
        return self.found

    @property
    def canonical_name(self) -> str:
        """Alias for ``name`` — compatibility property."""
        return self.name


@dataclass
class DirectInteraction:
    drug_a: str
    drug_b: str
    severity: str
    mechanism: str
    clinical_effect: str
    management: str
    source: str
    beers_flagged: bool
    coprescription_events: list[str]
    confidence: float
    source_layer: str = "L1_direct"
    citations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class IndirectInteraction:
    drug_a: str
    drug_b: str
    interaction_type: str  # "cyp_inhibition"|"cyp_induction"|"qt_combined"|"electrolyte_cascade"|"cns_combined"
    pathway: str
    severity_score: float
    clinical_implication: str
    confidence: float
    source: str = ""
    source_layer: str = "L2_multihop"
    citations: list[dict[str, Any]] = field(default_factory=list)
    # CYP-specific fields (populated for cyp_inhibition/cyp_induction)
    enzyme: str = ""
    inhibitor_strength: str = ""
    substrate_fraction: str = ""
    victim_is_nti: bool = False

    @property
    def perpetrator(self) -> str:
        """Alias for ``drug_a`` — compatibility property for CYP inhibition results."""
        return self.drug_a

    @property
    def victim(self) -> str:
        """Alias for ``drug_b`` — compatibility property for CYP inhibition results."""
        return self.drug_b


@dataclass
class HerbDrugInteraction:
    herb: str
    drug: str
    severity: str
    mechanism: str
    clinical_effect: str
    management: str
    source: str
    interaction_pathway: str  # "direct"|"cyp_mediated"
    enzyme: str = ""
    confidence: float = 0.0
    citations: list[dict[str, Any]] = field(default_factory=list)
    source_layer: str = "L1_direct"

    @property
    def herb_name(self) -> str:
        """Alias for ``herb`` — compatibility property."""
        return self.herb

    @property
    def drug_name(self) -> str:
        """Alias for ``drug`` — compatibility property."""
        return self.drug

    @property
    def interaction_type(self) -> str:
        """Alias for ``interaction_pathway`` — compatibility property."""
        return self.interaction_pathway


@dataclass
class BeersFlag:
    drug: str
    flag_type: str  # "inappropriate_elderly"|"disease_drug"|"ddi_beers"|"renal_adjust"
    category: str
    rationale: str
    recommendation: str
    source: str = "beers_2023"
    condition_involved: str = ""
    confidence: float = 0.97
    source_layer: str = "L1_direct"
    citations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def drug_name(self) -> str:
        """Alias for ``drug`` — compatibility property."""
        return self.drug

    @property
    def condition(self) -> str:
        """Alias for ``condition_involved`` — compatibility property."""
        return self.condition_involved


@dataclass
class ACBResult:
    total_score: int
    risk_level: str  # "low"|"moderate"|"high"
    contributing_drugs: list[dict]
    clinical_warning: str
    confidence: float = 0.95
    source_layer: str = "L1_direct"
    citations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Duplication:
    drug_class: str
    drugs: list[str]
    duplication_type: str  # "same_class"|"same_ingredient"
    recommendation: str
    confidence: float = 0.99
    source_layer: str = "L1_direct"
    citations: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SafetyReport:
    summary: dict
    direct_interactions: list[DirectInteraction]
    indirect_interactions: list[IndirectInteraction]
    herb_drug_interactions: list[HerbDrugInteraction]
    beers_flags: list[BeersFlag]
    acb_result: ACBResult
    duplications: list[Duplication]
    side_effects: dict
    unresolved_drugs: list[str]
    unresolved_herbs: list[dict]  # [{name, herb_in_database, classification, classification_note}]
    metadata: dict

    @property
    def total_findings(self) -> int:
        """Total number of findings across all categories."""
        return self.summary.get("total_findings", 0)

    @property
    def critical_count(self) -> int:
        """Number of critical findings."""
        return self.summary.get("critical_count", 0)


# ── Private helpers ──────────────────────────────────────────────────────────

def _safe_str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    return str(val)


def _safe_bool(val: Any) -> bool:
    if val is None:
        return False
    return bool(val)


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _safe_list(val: Any) -> list:
    if val is None:
        return []
    if isinstance(val, list):
        return val
    return [val]


def _record_locator(*parts: Any) -> str:
    values = [str(part).strip() for part in parts if str(part or "").strip()]
    return " | ".join(values)


def _normalized_severity(value: Any) -> str:
    severity = _safe_str(value, "unknown").strip().lower()
    return severity if severity in _SEVERITY_RANK else "unknown"


def _direct_row_priority(row: dict[str, Any]) -> tuple[int, int, int, int]:
    severity = _normalized_severity(row.get("severity"))
    source = _safe_str(row.get("source"), "unknown").strip().lower()
    severity_source = _safe_str(row.get("severity_source")).strip().lower()
    evidence_level = _safe_str(row.get("evidence_level")).strip().lower()
    evidence_rank = {"established": 4, "strong": 3, "moderate": 2, "weak": 1}.get(evidence_level, 0)
    return (
        _SEVERITY_RANK.get(severity, 1),
        _DIRECT_SOURCE_PRIORITY.get(source, 20),
        _DIRECT_SEVERITY_SOURCE_PRIORITY.get(severity_source, 10),
        evidence_rank,
    )


def _prefer_direct_row(candidate: dict[str, Any], current: dict[str, Any] | None) -> bool:
    if current is None:
        return True
    return _direct_row_priority(candidate) > _direct_row_priority(current)


def _fetch_drug_props(session: Any, lower_name: str) -> Any:
    """Run the standard drug-property projection for one lowercase name."""
    result = session.run(
        """
        MATCH (d:Drug)
        WHERE toLower(d.generic_name) = $lower_name
        RETURN d.generic_name                              AS generic_name,
               coalesce(toString(d.rxcui), '')             AS rxcui,
               coalesce(d.drug_class, '')                  AS drug_class,
               coalesce(d.is_beers, false)                 AS is_beers,
               coalesce(d.is_nti, false)                   AS is_nti,
               coalesce(d.anticholinergic_score, 0)        AS anticholinergic_score,
               coalesce(d.renal_dose_adjust, '')           AS renal_dose_adjust
        LIMIT 1
        """,
        lower_name=lower_name,
    )
    records = list(result)
    return records[0] if records else None


def _rxnorm_approximate_name(name: str) -> str:
    """Call the RxNorm approximate-term API and return the best candidate name."""
    try:
        encoded = urllib.parse.quote(name)
        url = (
            f"https://rxnav.nlm.nih.gov/REST/approximateTerm.json"
            f"?term={encoded}&maxEntries=1"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "SAHAYAK/1.0"})
        with urllib.request.urlopen(req, timeout=_RXNORM_TIMEOUT_S) as resp:
            data = json.loads(resp.read().decode())
        candidates = data.get("approximateGroup", {}).get("candidate", [])
        if not candidates:
            return ""
        rxcui = candidates[0].get("rxcui", "")
        if not rxcui:
            return ""
        name_url = f"https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json"
        req = urllib.request.Request(name_url, headers={"User-Agent": "SAHAYAK/1.0"})
        with urllib.request.urlopen(req, timeout=_RXNORM_TIMEOUT_S) as resp:
            name_data = json.loads(resp.read().decode())
        return name_data.get("properties", {}).get("name", "")
    except Exception as exc:
        logger.warning("RxNorm API error for %r: %s", name, exc)
        return ""


def _cyp_severity_score(
    inh_strength: str | None,
    sub_fraction: str | None,
    is_nti: bool,
    patient_age: int,
) -> float:
    """Calculate CYP-mediated severity on a 0–10+ scale."""
    s = _CYP_INHIBITOR_STRENGTH.get(inh_strength, 1)
    f = _CYP_SUBSTRATE_FRACTION.get(sub_fraction, 1)
    score: float = float(s * f)  # max 9 (strong × major)
    if is_nti:
        score *= 1.5
    if patient_age >= 70:
        score *= 1.2
    return round(min(score, 15.0), 2)


def _acb_risk_level(total: int) -> str:
    for lo, hi, label in _ACB_RISK_THRESHOLDS:
        if lo <= total < hi:
            return label
    return "high"


def _herb_has_interactions(herb_name: str) -> bool:
    """Return True if the herb has at least one INTERACTS_WITH_DRUG edge."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(
                """
                MATCH (h:Herb)-[:INTERACTS_WITH_DRUG]->()
                WHERE h.name = $herb_name
                RETURN count(*) AS cnt
                LIMIT 1
                """,
                herb_name=herb_name,
            )
            records = list(result)
            return _safe_int(records[0]["cnt"]) > 0 if records else False
    except Exception as exc:
        logger.error("_herb_has_interactions failed for %r: %s", herb_name, exc)
        return False


# ── Function 1: resolve_drug_name ────────────────────────────────────────────

def resolve_drug_name(name: str) -> ResolvedDrug:
    """Resolve any drug input to a canonical Drug node in Neo4j.

    Search cascade (stops at first hit):
      a) Exact generic name match
      b) Synonym array search
      c) Indian brand fulltext index
      d) Drug name fulltext fuzzy (Lucene ~)
      e) RxNorm API fallback

    Never guesses — returns ``found=False`` only when all five steps fail.

    Args:
        name: Raw drug name (brand, generic, OCR output, regional spelling).

    Returns:
        :class:`ResolvedDrug`.
    """
    if not name or not name.strip():
        logger.warning("resolve_drug_name received empty input")
        return ResolvedDrug(found=False, match_type="not_found", raw_input=name or "")

    name = name.strip()
    lower_name = name.lower()
    logger.info("resolve_drug_name: %r", name)

    driver = get_driver()

    # ── a) Exact generic name ─────────────────────────────────────────────
    try:
        with driver.session() as session:
            rec = _fetch_drug_props(session, lower_name)
            if rec:
                logger.debug("Resolved %r via exact generic", name)
                return ResolvedDrug(
                    found=True,
                    generic_name=_safe_str(rec["generic_name"]),
                    rxcui=_safe_str(rec["rxcui"]),
                    drug_class=_safe_str(rec["drug_class"]),
                    is_beers=_safe_bool(rec["is_beers"]),
                    is_nti=_safe_bool(rec["is_nti"]),
                    anticholinergic_score=_safe_int(rec["anticholinergic_score"]),
                    renal_dose_adjust=_safe_str(rec["renal_dose_adjust"]),
                    match_type="exact",
                    confidence=1.0,
                    raw_input=name,
                )
    except Exception as exc:
        logger.error("Exact match failed for %r: %s", name, exc)

    # ── b) Synonym array ──────────────────────────────────────────────────
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)
                WHERE ANY(s IN coalesce(d.synonyms, []) WHERE toLower(s) = $lower_name)
                RETURN d.generic_name                              AS generic_name,
                       coalesce(toString(d.rxcui), '')             AS rxcui,
                       coalesce(d.drug_class, '')                  AS drug_class,
                       coalesce(d.is_beers, false)                 AS is_beers,
                       coalesce(d.is_nti, false)                   AS is_nti,
                       coalesce(d.anticholinergic_score, 0)        AS anticholinergic_score,
                       coalesce(d.renal_dose_adjust, '')           AS renal_dose_adjust
                LIMIT 1
                """,
                lower_name=lower_name,
            )
            records = list(result)
            if records:
                rec = records[0]
                logger.debug("Resolved %r via synonym", name)
                return ResolvedDrug(
                    found=True,
                    generic_name=_safe_str(rec["generic_name"]),
                    rxcui=_safe_str(rec["rxcui"]),
                    drug_class=_safe_str(rec["drug_class"]),
                    is_beers=_safe_bool(rec["is_beers"]),
                    is_nti=_safe_bool(rec["is_nti"]),
                    anticholinergic_score=_safe_int(rec["anticholinergic_score"]),
                    renal_dose_adjust=_safe_str(rec["renal_dose_adjust"]),
                    match_type="synonym",
                    confidence=0.98,
                    raw_input=name,
                )
    except Exception as exc:
        logger.error("Synonym match failed for %r: %s", name, exc)

    # ── c) Indian brand fulltext (uses existing OCR-tolerant helper) ──────
    try:
        brand_results = search_indian_brand(name, limit=1)
        if brand_results and brand_results[0]["score"] > 1.0:
            ingredients: list[str] = [
                g for g in (brand_results[0].get("contained_drugs") or []) if g
            ]
            if ingredients:
                with driver.session() as session:
                    rec = _fetch_drug_props(session, ingredients[0].lower())
                    if rec:
                        confidence = min(0.93, 0.55 + brand_results[0]["score"] / 20.0)
                        logger.debug(
                            "Resolved %r via brand → %r",
                            name,
                            rec["generic_name"],
                        )
                        return ResolvedDrug(
                            found=True,
                            generic_name=_safe_str(rec["generic_name"]),
                            rxcui=_safe_str(rec["rxcui"]),
                            drug_class=_safe_str(rec["drug_class"]),
                            is_beers=_safe_bool(rec["is_beers"]),
                            is_nti=_safe_bool(rec["is_nti"]),
                            anticholinergic_score=_safe_int(rec["anticholinergic_score"]),
                            renal_dose_adjust=_safe_str(rec["renal_dose_adjust"]),
                            match_type="brand",
                            confidence=confidence,
                            raw_input=name,
                            ingredients=ingredients,
                        )
    except Exception as exc:
        logger.error("Brand match failed for %r: %s", name, exc)

    # ── d) Drug name fulltext fuzzy ───────────────────────────────────────
    try:
        with driver.session() as session:
            result = session.run(
                """
                CALL db.index.fulltext.queryNodes("drug_name_fulltext", $lucene_query)
                YIELD node, score
                WHERE score > 0.5
                RETURN node.generic_name                              AS generic_name,
                       coalesce(toString(node.rxcui), '')             AS rxcui,
                       coalesce(node.drug_class, '')                  AS drug_class,
                       coalesce(node.is_beers, false)                 AS is_beers,
                       coalesce(node.is_nti, false)                   AS is_nti,
                       coalesce(node.anticholinergic_score, 0)        AS anticholinergic_score,
                       coalesce(node.renal_dose_adjust, '')           AS renal_dose_adjust,
                       score
                ORDER BY score DESC
                LIMIT 1
                """,
                lucene_query=name + "~",
            )
            records = list(result)
            if records:
                rec = records[0]
                ft_score = _safe_float(rec["score"])
                confidence = min(0.82, 0.35 + ft_score / 10.0)
                logger.debug(
                    "Resolved %r via fuzzy → %r (score %.2f)",
                    name,
                    rec["generic_name"],
                    ft_score,
                )
                return ResolvedDrug(
                    found=True,
                    generic_name=_safe_str(rec["generic_name"]),
                    rxcui=_safe_str(rec["rxcui"]),
                    drug_class=_safe_str(rec["drug_class"]),
                    is_beers=_safe_bool(rec["is_beers"]),
                    is_nti=_safe_bool(rec["is_nti"]),
                    anticholinergic_score=_safe_int(rec["anticholinergic_score"]),
                    renal_dose_adjust=_safe_str(rec["renal_dose_adjust"]),
                    match_type="fuzzy",
                    confidence=confidence,
                    raw_input=name,
                )
    except Exception as exc:
        logger.error("Fuzzy match failed for %r: %s", name, exc)

    # ── e) RxNorm API fallback ────────────────────────────────────────────
    try:
        rxnorm_name = _rxnorm_approximate_name(name)
        if rxnorm_name and rxnorm_name.lower() != lower_name:
            logger.debug("RxNorm suggests %r for %r", rxnorm_name, name)
            with driver.session() as session:
                rec = _fetch_drug_props(session, rxnorm_name.lower())
                if rec:
                    logger.info("Resolved %r via RxNorm → %r", name, rec["generic_name"])
                    return ResolvedDrug(
                        found=True,
                        generic_name=_safe_str(rec["generic_name"]),
                        rxcui=_safe_str(rec["rxcui"]),
                        drug_class=_safe_str(rec["drug_class"]),
                        is_beers=_safe_bool(rec["is_beers"]),
                        is_nti=_safe_bool(rec["is_nti"]),
                        anticholinergic_score=_safe_int(rec["anticholinergic_score"]),
                        renal_dose_adjust=_safe_str(rec["renal_dose_adjust"]),
                        match_type="rxnorm_api",
                        confidence=0.80,
                        raw_input=name,
                    )
    except Exception as exc:
        logger.error("RxNorm API fallback failed for %r: %s", name, exc)

    logger.warning("Drug not resolved: %r", name)
    return ResolvedDrug(found=False, match_type="not_found", confidence=0.0, raw_input=name)


# ── Function 2: resolve_herb_name ────────────────────────────────────────────

def resolve_herb_name(name: str) -> ResolvedHerb:
    """Resolve herb names including regional Indian language variants.

    Search cascade: exact name → Hindi → Tamil → Telugu → Kannada
    → scientific name CONTAINS → fuzzy (CONTAINS / starts-with).

    Args:
        name: Raw herb name in any language or transliteration.

    Returns:
        :class:`ResolvedHerb` with ``herb_in_database=False`` when not found.
        NEVER marks an herb as safe — see :func:`get_comprehensive_safety_report`
        for the three-tier classification.
    """
    if not name or not name.strip():
        logger.warning("resolve_herb_name received empty input")
        return ResolvedHerb(
            found=False, match_type="not_found", raw_input=name or "",
            herb_in_database=False,
        )

    name = name.strip()
    lower_name = name.lower()
    logger.info("resolve_herb_name: %r", name)

    driver = get_driver()

    lang_steps = [
        ("exact",      "toLower(h.name) = $val"),
        ("hindi",      "toLower(h.hindi_name) = $val"),
        ("tamil",      "toLower(h.tamil_name) = $val"),
        ("telugu",     "toLower(h.telugu_name) = $val"),
        ("kannada",    "toLower(h.kannada_name) = $val"),
        ("scientific", "toLower(h.scientific_name) CONTAINS $val"),
    ]
    step_confidences = {
        "exact": 1.0, "hindi": 0.97, "tamil": 0.97, "telugu": 0.97,
        "kannada": 0.97, "scientific": 0.90,
    }

    for match_type, where_expr in lang_steps:
        try:
            with driver.session() as session:
                result = session.run(
                    f"""
                    MATCH (h:Herb)
                    WHERE {where_expr}
                    RETURN h.name                          AS name,
                           coalesce(h.hindi_name, '')      AS hindi_name,
                           coalesce(h.category, '')        AS category
                    LIMIT 1
                    """,
                    val=lower_name,
                )
                records = list(result)
                if records:
                    herb_name = _safe_str(records[0]["name"])
                    has_ixn = _herb_has_interactions(herb_name)
                    logger.debug("Resolved herb %r via %s → %r", name, match_type, herb_name)
                    return ResolvedHerb(
                        found=True,
                        name=herb_name,
                        hindi_name=_safe_str(records[0]["hindi_name"]),
                        category=_safe_str(records[0]["category"]),
                        match_type=match_type,
                        confidence=step_confidences[match_type],
                        raw_input=name,
                        herb_in_database=True,
                        has_interaction_data=has_ixn,
                    )
        except Exception as exc:
            logger.error("Herb %s match failed for %r: %s", match_type, name, exc)

    # Fuzzy: CONTAINS or starts-with
    try:
        with driver.session() as session:
            prefix = lower_name[:4] if len(lower_name) >= 4 else lower_name
            result = session.run(
                """
                MATCH (h:Herb)
                WHERE toLower(h.name) CONTAINS $val
                   OR toLower(h.name) STARTS WITH $prefix
                RETURN h.name                          AS name,
                       coalesce(h.hindi_name, '')      AS hindi_name,
                       coalesce(h.category, '')        AS category
                LIMIT 1
                """,
                val=lower_name,
                prefix=prefix,
            )
            records = list(result)
            if records:
                herb_name = _safe_str(records[0]["name"])
                has_ixn = _herb_has_interactions(herb_name)
                logger.debug("Resolved herb %r via fuzzy → %r", name, herb_name)
                return ResolvedHerb(
                    found=True,
                    name=herb_name,
                    hindi_name=_safe_str(records[0]["hindi_name"]),
                    category=_safe_str(records[0]["category"]),
                    match_type="fuzzy",
                    confidence=0.72,
                    raw_input=name,
                    herb_in_database=True,
                    has_interaction_data=has_ixn,
                )
    except Exception as exc:
        logger.error("Fuzzy herb match failed for %r: %s", name, exc)

    logger.warning("Herb not resolved: %r", name)
    return ResolvedHerb(
        found=False, match_type="not_found", confidence=0.0,
        raw_input=name, herb_in_database=False,
    )


# ── Function 3: check_direct_interactions ───────────────────────────────────

def check_direct_interactions(drug_names: list[str]) -> list[DirectInteraction]:
    """Check all pairwise INTERACTS_WITH edges between a patient's drugs.

    Uses a single UNWIND query for efficiency (not one round-trip per pair).
    Also enriches results with TwoSIDES co-prescription adverse events.

    Args:
        drug_names: Raw drug names (resolved internally).

    Returns:
        List of :class:`DirectInteraction` sorted major → moderate → minor.
    """
    logger.info("check_direct_interactions: %d input drugs", len(drug_names))

    resolved_map: dict[str, str] = {}  # lower_generic → canonical generic
    for raw in drug_names:
        rd = resolve_drug_name(raw)
        if rd.found:
            resolved_map[rd.generic_name.lower()] = rd.generic_name
        else:
            logger.warning("Skipping unresolved drug %r in direct interaction check", raw)

    unique_lowers = sorted(resolved_map.keys())
    if len(unique_lowers) < 2:
        logger.info("Fewer than 2 resolved drugs — no pairs to check")
        return []

    pairs = [[a, b] for a, b in itertools.combinations(unique_lowers, 2)]
    logger.info("Checking %d drug pairs for direct interactions", len(pairs))

    driver = get_driver()

    # ── INTERACTS_WITH (DDInter + PrimeKG + Beers) ────────────────────────
    ddi_rows: list[dict] = []
    try:
        with driver.session() as session:
            result = session.run(
                """
                UNWIND $pairs AS pair
                MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
                WHERE toLower(a.generic_name) = pair[0]
                  AND toLower(b.generic_name) = pair[1]
                RETURN pair[0]                                  AS drug_a,
                       pair[1]                                  AS drug_b,
                       coalesce(r.severity, 'unknown')          AS severity,
                       coalesce(r.severity_source, '')          AS severity_source,
                       coalesce(r.severity_confidence, '')      AS severity_confidence,
                       coalesce(r.mechanism, '')                AS mechanism,
                       coalesce(r.clinical_effect, '')          AS clinical_effect,
                       coalesce(r.management, '')               AS management,
                       coalesce(r.source, 'unknown')            AS source,
                       coalesce(r.evidence_level, '')           AS evidence_level,
                       ''                                        AS alternative,
                       coalesce(r.ddinter_id_a, '')             AS ddinter_id_a,
                       coalesce(r.ddinter_id_b, '')             AS ddinter_id_b,
                       coalesce(r.beers_flagged, false)         AS beers_flagged,
                       coalesce(r.beers_quality_of_evidence, '') AS beers_quality_of_evidence,
                       coalesce(r.beers_strength, '')           AS beers_strength,
                       coalesce(r.beers_risk, '')               AS beers_risk,
                       coalesce(r.beers_recommendation, '')     AS beers_recommendation
                """,
                pairs=pairs,
            )
            ddi_rows = [dict(r) for r in result]
    except Exception as exc:
        logger.error("INTERACTS_WITH query failed: %s", exc)

    # ── COPRESCRIPTION_EFFECT (TwoSIDES) ──────────────────────────────────
    coprescription_map: dict[tuple[str, str], list[str]] = defaultdict(list)
    try:
        with driver.session() as session:
            result = session.run(
                """
                UNWIND $pairs AS pair
                MATCH (a:Drug)-[r:COPRESCRIPTION_EFFECT]-(b:Drug)
                WHERE toLower(a.generic_name) = pair[0]
                  AND toLower(b.generic_name) = pair[1]
                RETURN pair[0]          AS drug_a,
                       pair[1]          AS drug_b,
                       r.adverse_events AS events
                """,
                pairs=pairs,
            )
            for row in result:
                key = (_safe_str(row["drug_a"]), _safe_str(row["drug_b"]))
                coprescription_map[key].extend(_safe_list(row["events"])[:5])
    except Exception as exc:
        logger.error("COPRESCRIPTION_EFFECT query failed: %s", exc)

    # ── Merge and deduplicate (keep highest severity per pair) ─────────────
    grouped: dict[tuple[str, str], dict] = {}

    for row in ddi_rows:
        key = (_safe_str(row["drug_a"]), _safe_str(row["drug_b"]))
        sev = _normalized_severity(row["severity"])
        src = _safe_str(row["source"], "unknown")

        if key not in grouped:
            entry = dict(row)
            entry["severity"] = sev
            entry["all_sources"] = [src]
            entry["support_rows"] = [dict(row)]
            entry["best_support_row"] = dict(row)
            grouped[key] = entry
        else:
            existing = grouped[key]
            best_support = existing.get("best_support_row")
            if _prefer_direct_row(row, best_support):
                existing["best_support_row"] = dict(row)
                existing["severity"] = sev
                existing["severity_source"] = _safe_str(row.get("severity_source"))
                existing["severity_confidence"] = _safe_str(row.get("severity_confidence"))
                if row["mechanism"]:
                    existing["mechanism"] = row["mechanism"]
                if row["clinical_effect"]:
                    existing["clinical_effect"] = row["clinical_effect"]
                if row["management"]:
                    existing["management"] = row["management"]
            existing["beers_flagged"] = existing["beers_flagged"] or _safe_bool(row["beers_flagged"])
            existing["all_sources"].append(src)
            existing["support_rows"].append(dict(row))

    interactions: list[DirectInteraction] = []
    for (da, db), data in grouped.items():
        best_support = data.get("best_support_row") or {}
        sources = _ordered_unique(data.get("all_sources", []))
        is_beers = _safe_bool(data.get("beers_flagged"))
        confidence = 1.0 if len(sources) > 1 else (0.98 if is_beers else 0.95)

        co_events = _ordered_unique(
            coprescription_map.get((da, db), [])
            + coprescription_map.get((db, da), [])
        )[:5]

        citations: list[dict[str, Any]] = []
        for row in data.get("support_rows", []):
            evidence = build_evidence_text(
                row.get("clinical_effect"),
                row.get("mechanism"),
                row.get("management"),
                row.get("beers_risk"),
            )
            citations.append(
                make_citation(
                    source_key=row.get("source"),
                    relation_type="INTERACTS_WITH",
                    source_layer="L1_direct",
                    evidence=evidence or "Direct drug-drug interaction edge in the SAHAYAK knowledge graph.",
                    evidence_type="direct_interaction",
                    confidence=confidence,
                    extras={
                        "drug_a": resolved_map.get(da, da),
                        "drug_b": resolved_map.get(db, db),
                        "severity": _normalized_severity(row.get("severity")),
                        "severity_source": _safe_str(row.get("severity_source")),
                        "severity_confidence": _safe_str(row.get("severity_confidence")),
                        "evidence_level": _safe_str(row.get("evidence_level")),
                        "mechanism": _safe_str(row.get("mechanism")),
                        "clinical_effect": _safe_str(row.get("clinical_effect")),
                        "management": _safe_str(row.get("management")),
                        "alternative": _safe_str(row.get("alternative")),
                        "ddinter_id_a": _safe_str(row.get("ddinter_id_a")),
                        "ddinter_id_b": _safe_str(row.get("ddinter_id_b")),
                        "record_locator": _record_locator(
                            f"ddinter_id_a={_safe_str(row.get('ddinter_id_a'))}" if _safe_str(row.get("ddinter_id_a")) else "",
                            f"ddinter_id_b={_safe_str(row.get('ddinter_id_b'))}" if _safe_str(row.get("ddinter_id_b")) else "",
                        ),
                        "quality_of_evidence": _safe_str(row.get("beers_quality_of_evidence")),
                        "strength": _safe_str(row.get("beers_strength")),
                        "guideline_risk": _safe_str(row.get("beers_risk")),
                        "guideline_recommendation": _safe_str(row.get("beers_recommendation")),
                    },
                )
            )
        if co_events:
            citations.append(
                make_citation(
                    source_key="twosides",
                    relation_type="COPRESCRIPTION_EFFECT",
                    source_layer="L1_direct",
                    evidence=f"Shared coprescription adverse-event signals: {', '.join(co_events)}",
                    evidence_type="coprescription_signal",
                    confidence=0.8,
                    extras={"adverse_events": co_events},
                )
            )
        citations = dedupe_citations(citations)

        interactions.append(
            DirectInteraction(
                drug_a=resolved_map.get(da, da),
                drug_b=resolved_map.get(db, db),
                severity=_normalized_severity(best_support.get("severity") or data.get("severity")),
                mechanism=_safe_str(best_support.get("mechanism") or data.get("mechanism")),
                clinical_effect=_safe_str(best_support.get("clinical_effect") or data.get("clinical_effect")),
                management=_safe_str(best_support.get("management") or data.get("management")),
                source=", ".join(sources),
                beers_flagged=is_beers,
                coprescription_events=co_events,
                confidence=confidence,
                source_layer="L1_direct",
                citations=citations,
            )
        )

    interactions.sort(key=lambda x: _SEVERITY_RANK.get(x.severity, 1), reverse=True)
    logger.info("Found %d direct drug interactions", len(interactions))
    return interactions


# ── Function 4: check_indirect_interactions ──────────────────────────────────

def check_indirect_interactions(
    drug_names: list[str],
    patient_age: int = 65,
) -> list[IndirectInteraction]:
    """Discover interactions via CYP enzymes, QT prolongation, and electrolyte cascades.

    Finds 52K+ interaction pairs that direct-edge checkers miss.

    Args:
        drug_names: Raw drug names (resolved internally).
        patient_age: Patient age — elderly modifier (×1.2) applied if ≥70.

    Returns:
        List of :class:`IndirectInteraction` sorted by severity_score descending.
    """
    logger.info(
        "check_indirect_interactions: %d drugs, age=%d", len(drug_names), patient_age
    )

    # Resolve and build lower→is_nti map
    resolved_map: dict[str, bool] = {}  # lower_generic → is_nti
    for raw in drug_names:
        rd = resolve_drug_name(raw)
        if rd.found:
            resolved_map[rd.generic_name.lower()] = rd.is_nti
        else:
            logger.warning("Skipping unresolved drug %r from indirect check", raw)

    names_lower = list(resolved_map.keys())
    if len(names_lower) < 2:
        return []

    driver = get_driver()
    results: list[IndirectInteraction] = []

    # ── 4a: CYP inhibition ────────────────────────────────────────────────
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (perp:Drug)-[inh:INHIBITS]->(enz:Enzyme)<-[sub:IS_SUBSTRATE_OF]-(victim:Drug)
                WHERE toLower(perp.generic_name) IN $names
                  AND toLower(victim.generic_name) IN $names
                  AND perp <> victim
                RETURN perp.generic_name                          AS perpetrator,
                       enz.name                                   AS enzyme,
                       victim.generic_name                        AS victim,
                       coalesce(inh.strength, '')                 AS inh_strength,
                       coalesce(sub.fraction, '')                 AS sub_fraction,
                       coalesce(inh.source, 'unknown')            AS inh_source,
                       coalesce(sub.source, 'unknown')            AS sub_source,
                       coalesce(toFloat(inh.confidence), 0.8)    AS inh_conf,
                       coalesce(toFloat(sub.confidence), 0.8)    AS sub_conf,
                       coalesce(victim.is_nti, false)             AS victim_is_nti
                """,
                names=names_lower,
            )
            for row in result:
                perp = _safe_str(row["perpetrator"])
                victim = _safe_str(row["victim"])
                enzyme = _safe_str(row["enzyme"])
                inh_strength = _safe_str(row["inh_strength"]) or None
                sub_fraction = _safe_str(row["sub_fraction"]) or None
                is_nti = _safe_bool(row["victim_is_nti"]) or resolved_map.get(
                    victim.lower(), False
                )
                inh_conf = _safe_float(row["inh_conf"], 0.8)
                sub_conf = _safe_float(row["sub_conf"], 0.8)

                score = _cyp_severity_score(inh_strength, sub_fraction, is_nti, patient_age)
                confidence = round(min(inh_conf, sub_conf) * 0.9, 3)
                nti_tag = " [NTI — narrow therapeutic index]" if is_nti else ""
                pathway = (
                    f"{perp} --[inhibits {inh_strength or 'unknown'}]--> {enzyme} "
                    f"<--[substrate {sub_fraction or 'unknown'}]-- {victim}{nti_tag}"
                )
                implication = (
                    f"{perp} inhibits {enzyme} metabolism of {victim}. "
                    f"Expect increased {victim} plasma levels and toxicity risk"
                    + (" — NTI drug, requires therapeutic drug monitoring" if is_nti else "")
                    + ". Consider dose reduction or alternative."
                )
                citations = dedupe_citations(
                    [
                        make_citation(
                            source_key=row.get("inh_source"),
                            relation_type="INHIBITS",
                            source_layer="L2_multihop",
                            evidence=f"{perp} inhibits {enzyme} ({inh_strength or 'unknown'} strength).",
                            evidence_type="mechanism_path",
                            confidence=inh_conf,
                            extras={
                                "drug": perp,
                                "enzyme": enzyme,
                                "strength": inh_strength or "unknown",
                            },
                        ),
                        make_citation(
                            source_key=row.get("sub_source"),
                            relation_type="IS_SUBSTRATE_OF",
                            source_layer="L2_multihop",
                            evidence=f"{victim} is a {sub_fraction or 'unknown'} substrate of {enzyme}.",
                            evidence_type="mechanism_path",
                            confidence=sub_conf,
                            extras={
                                "drug": victim,
                                "enzyme": enzyme,
                                "fraction": sub_fraction or "unknown",
                                "victim_is_nti": bool(is_nti),
                            },
                        ),
                    ]
                )
                results.append(
                    IndirectInteraction(
                        drug_a=perp,
                        drug_b=victim,
                        interaction_type="cyp_inhibition",
                        pathway=pathway,
                        severity_score=score,
                        clinical_implication=implication,
                        confidence=confidence,
                        source=source_summary_from_citations(citations),
                        source_layer="L2_multihop",
                        citations=citations,
                        enzyme=enzyme,
                        inhibitor_strength=inh_strength or "",
                        substrate_fraction=sub_fraction or "",
                        victim_is_nti=bool(is_nti),
                    )
                )
    except Exception as exc:
        logger.error("CYP inhibition query failed: %s", exc)

    # ── 4b: CYP induction ─────────────────────────────────────────────────
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (ind:Drug)-[r:INDUCES]->(enz:Enzyme)<-[sub:IS_SUBSTRATE_OF]-(victim:Drug)
                WHERE toLower(ind.generic_name) IN $names
                  AND toLower(victim.generic_name) IN $names
                  AND ind <> victim
                RETURN ind.generic_name                           AS inducer,
                       enz.name                                   AS enzyme,
                       victim.generic_name                        AS victim,
                       coalesce(sub.fraction, '')                 AS sub_fraction,
                       coalesce(r.source, 'unknown')              AS ind_source,
                       coalesce(sub.source, 'unknown')            AS sub_source,
                       coalesce(toFloat(r.confidence), 0.75)     AS ind_conf,
                       coalesce(toFloat(sub.confidence), 0.8)    AS sub_conf,
                       coalesce(victim.is_nti, false)             AS victim_is_nti
                """,
                names=names_lower,
            )
            for row in result:
                inducer = _safe_str(row["inducer"])
                victim = _safe_str(row["victim"])
                enzyme = _safe_str(row["enzyme"])
                sub_fraction = _safe_str(row["sub_fraction"]) or None
                is_nti = _safe_bool(row["victim_is_nti"])
                ind_conf = _safe_float(row["ind_conf"], 0.75)
                sub_conf = _safe_float(row["sub_conf"], 0.8)

                score = 4.0 * (1.5 if is_nti else 1.0) * (1.2 if patient_age >= 70 else 1.0)
                confidence = round(min(ind_conf, sub_conf) * 0.9, 3)
                pathway = (
                    f"{inducer} --[induces]--> {enzyme} "
                    f"<--[substrate {sub_fraction or 'unknown'}]-- {victim}"
                )
                implication = (
                    f"{inducer} induces {enzyme}, reducing {victim} plasma levels. "
                    f"Risk of {victim} treatment failure"
                    + (" — NTI drug, monitor closely" if is_nti else "")
                    + "."
                )
                citations = dedupe_citations(
                    [
                        make_citation(
                            source_key=row.get("ind_source"),
                            relation_type="INDUCES",
                            source_layer="L2_multihop",
                            evidence=f"{inducer} induces {enzyme}.",
                            evidence_type="mechanism_path",
                            confidence=ind_conf,
                            extras={
                                "drug": inducer,
                                "enzyme": enzyme,
                            },
                        ),
                        make_citation(
                            source_key=row.get("sub_source"),
                            relation_type="IS_SUBSTRATE_OF",
                            source_layer="L2_multihop",
                            evidence=f"{victim} is a {sub_fraction or 'unknown'} substrate of {enzyme}.",
                            evidence_type="mechanism_path",
                            confidence=sub_conf,
                            extras={
                                "drug": victim,
                                "enzyme": enzyme,
                                "fraction": sub_fraction or "unknown",
                                "victim_is_nti": bool(is_nti),
                            },
                        ),
                    ]
                )
                results.append(
                    IndirectInteraction(
                        drug_a=inducer,
                        drug_b=victim,
                        interaction_type="cyp_induction",
                        pathway=pathway,
                        severity_score=round(min(score, 10.0), 2),
                        clinical_implication=implication,
                        confidence=confidence,
                        source=source_summary_from_citations(citations),
                        source_layer="L2_multihop",
                        citations=citations,
                        enzyme=enzyme,
                        inhibitor_strength="",
                        substrate_fraction=sub_fraction or "",
                        victim_is_nti=bool(is_nti),
                    )
                )
    except Exception as exc:
        logger.error("CYP induction query failed: %s", exc)

    # ── 4c: Combined QT prolongation ──────────────────────────────────────
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d1:Drug)-[r1:PROLONGS_QT]->(qt:AdverseEffect)<-[r2:PROLONGS_QT]-(d2:Drug)
                WHERE toLower(d1.generic_name) IN $names
                  AND toLower(d2.generic_name) IN $names
                  AND d1.generic_name < d2.generic_name
                RETURN d1.generic_name AS drug1,
                       d2.generic_name AS drug2,
                       qt.name         AS effect,
                       coalesce(r1.source, 'unknown') AS source_1,
                       coalesce(r2.source, 'unknown') AS source_2,
                       coalesce(r1.risk_category, '') AS risk_1,
                       coalesce(r2.risk_category, '') AS risk_2
                """,
                names=names_lower,
            )
            for row in result:
                d1 = _safe_str(row["drug1"])
                d2 = _safe_str(row["drug2"])
                score = min(8.0 * (1.2 if patient_age >= 70 else 1.0), 10.0)
                citations = dedupe_citations(
                    [
                        make_citation(
                            source_key=row.get("source_1"),
                            relation_type="PROLONGS_QT",
                            source_layer="L2_multihop",
                            evidence=f"{d1} is listed as a QT-prolonging medicine.",
                            evidence_type="mechanism_path",
                            confidence=0.88,
                            extras={
                                "drug": d1,
                                "risk_category": _safe_str(row.get("risk_1")),
                            },
                        ),
                        make_citation(
                            source_key=row.get("source_2"),
                            relation_type="PROLONGS_QT",
                            source_layer="L2_multihop",
                            evidence=f"{d2} is listed as a QT-prolonging medicine.",
                            evidence_type="mechanism_path",
                            confidence=0.88,
                            extras={
                                "drug": d2,
                                "risk_category": _safe_str(row.get("risk_2")),
                            },
                        ),
                    ]
                )
                results.append(
                    IndirectInteraction(
                        drug_a=d1,
                        drug_b=d2,
                        interaction_type="qt_combined",
                        pathway=(
                            f"{d1} + {d2} both prolong QT interval — "
                            "combined cardiac arrhythmia risk"
                        ),
                        severity_score=round(score, 2),
                        clinical_implication=(
                            "Combined QT prolongation may cause torsades de pointes "
                            "and fatal arrhythmia. ECG monitoring required. "
                            "Avoid concurrent use if clinically possible."
                        ),
                        confidence=0.88,
                        source=source_summary_from_citations(citations),
                        source_layer="L2_multihop",
                        citations=citations,
                    )
                )
    except Exception as exc:
        logger.error("QT prolongation query failed: %s", exc)

    # ── 4d: Electrolyte cascade ───────────────────────────────────────────
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (dep:Drug)-[r1:DEPLETES]->(e:ElectrolyteEffect)<-[r2:SENSITIVE_TO]-(sens:Drug)
                WHERE toLower(dep.generic_name) IN $names
                  AND toLower(sens.generic_name) IN $names
                RETURN dep.generic_name   AS depleter,
                       e.name             AS electrolyte,
                       sens.generic_name  AS sensitive_drug,
                       coalesce(r1.source, 'unknown') AS source_1,
                       coalesce(r2.source, 'unknown') AS source_2
                """,
                names=names_lower,
            )
            for row in result:
                dep = _safe_str(row["depleter"])
                elec = _safe_str(row["electrolyte"])
                sens = _safe_str(row["sensitive_drug"])
                score = min(7.0 * (1.2 if patient_age >= 70 else 1.0), 10.0)
                citations = dedupe_citations(
                    [
                        make_citation(
                            source_key=row.get("source_1"),
                            relation_type="DEPLETES",
                            source_layer="L2_multihop",
                            evidence=f"{dep} can deplete {elec}.",
                            evidence_type="mechanism_path",
                            confidence=0.82,
                            extras={
                                "drug": dep,
                                "electrolyte": elec,
                            },
                        ),
                        make_citation(
                            source_key=row.get("source_2"),
                            relation_type="SENSITIVE_TO",
                            source_layer="L2_multihop",
                            evidence=f"{sens} is sensitive to {elec} changes.",
                            evidence_type="mechanism_path",
                            confidence=0.82,
                            extras={
                                "drug": sens,
                                "electrolyte": elec,
                            },
                        ),
                    ]
                )
                results.append(
                    IndirectInteraction(
                        drug_a=dep,
                        drug_b=sens,
                        interaction_type="electrolyte_cascade",
                        pathway=f"{dep} depletes {elec} — increases toxicity of {sens}",
                        severity_score=round(score, 2),
                        clinical_implication=(
                            f"{dep} depletes {elec}, potentiating {sens} toxicity. "
                            "Monitor serum electrolytes regularly."
                        ),
                        confidence=0.82,
                        source=source_summary_from_citations(citations),
                        source_layer="L2_multihop",
                        citations=citations,
                    )
                )
    except Exception as exc:
        logger.error("Electrolyte cascade query failed: %s", exc)

    # ── 4e: Combined CNS depression ───────────────────────────────────────
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d1:Drug)-[r1:CAUSES_CNS_DEPRESSION]->(cns)<-[r2:CAUSES_CNS_DEPRESSION]-(d2:Drug)
                WHERE toLower(d1.generic_name) IN $names
                  AND toLower(d2.generic_name) IN $names
                  AND d1.generic_name < d2.generic_name
                RETURN d1.generic_name AS drug1,
                       d2.generic_name AS drug2,
                       coalesce(r1.source, 'unknown') AS source_1,
                       coalesce(r2.source, 'unknown') AS source_2
                """,
                names=names_lower,
            )
            for row in result:
                d1 = _safe_str(row["drug1"])
                d2 = _safe_str(row["drug2"])
                score = min(6.0 * (1.2 if patient_age >= 70 else 1.0), 10.0)
                citations = dedupe_citations(
                    [
                        make_citation(
                            source_key=row.get("source_1"),
                            relation_type="CAUSES_CNS_DEPRESSION",
                            source_layer="L2_multihop",
                            evidence=f"{d1} can contribute to CNS depression.",
                            evidence_type="mechanism_path",
                            confidence=0.8,
                            extras={"drug": d1},
                        ),
                        make_citation(
                            source_key=row.get("source_2"),
                            relation_type="CAUSES_CNS_DEPRESSION",
                            source_layer="L2_multihop",
                            evidence=f"{d2} can contribute to CNS depression.",
                            evidence_type="mechanism_path",
                            confidence=0.8,
                            extras={"drug": d2},
                        ),
                    ]
                )
                results.append(
                    IndirectInteraction(
                        drug_a=d1,
                        drug_b=d2,
                        interaction_type="cns_combined",
                        pathway=f"Combined CNS depression: {d1} + {d2}",
                        severity_score=round(score, 2),
                        clinical_implication=(
                            "Combined CNS depression — increased risk of sedation, "
                            "respiratory depression, and falls. "
                            "High-risk in elderly (age ≥65). Use lowest effective doses."
                        ),
                        confidence=0.80,
                        source=source_summary_from_citations(citations),
                        source_layer="L2_multihop",
                        citations=citations,
                    )
                )
    except Exception as exc:
        logger.error("CNS combined depression query failed: %s", exc)

    results.sort(key=lambda x: x.severity_score, reverse=True)
    logger.info("Found %d indirect interactions", len(results))
    return results


# ── Function 5: check_herb_drug_interactions ─────────────────────────────────

def check_herb_drug_interactions(
    herb_names: list[str],
    drug_names: list[str],
) -> list[HerbDrugInteraction]:
    """Check herb–drug interactions including CYP-mediated pathways.

    Args:
        herb_names: Raw herb names (resolved internally).
        drug_names: Raw drug names (resolved internally).

    Returns:
        List of :class:`HerbDrugInteraction`.
    """
    logger.info(
        "check_herb_drug_interactions: %d herbs × %d drugs",
        len(herb_names),
        len(drug_names),
    )

    resolved_herbs: list[str] = []
    for h in herb_names:
        rh = resolve_herb_name(h)
        if rh.found:
            resolved_herbs.append(rh.name)
        else:
            logger.warning("Herb %r not resolved — skipping from interaction check", h)

    resolved_drugs_lower: list[str] = []
    resolved_drugs_map: dict[str, str] = {}
    for d in drug_names:
        rd = resolve_drug_name(d)
        if rd.found:
            resolved_drugs_lower.append(rd.generic_name.lower())
            resolved_drugs_map[rd.generic_name.lower()] = rd.generic_name
        else:
            logger.warning("Drug %r not resolved — skipping from herb-drug check", d)

    if not resolved_herbs or not resolved_drugs_lower:
        return []

    driver = get_driver()
    results: list[HerbDrugInteraction] = []

    # ── 5a: Direct INTERACTS_WITH_DRUG edges ─────────────────────────────
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (h:Herb)-[r:INTERACTS_WITH_DRUG]->(d:Drug)
                WHERE h.name IN $herbs
                  AND toLower(d.generic_name) IN $drugs
                RETURN h.name                              AS herb,
                       d.generic_name                      AS drug,
                       coalesce(r.severity, 'unknown')     AS severity,
                       coalesce(r.mechanism, '')           AS mechanism,
                       coalesce(r.clinical_effect, '')     AS clinical_effect,
                       coalesce(r.management, '')          AS management,
                       coalesce(r.source, 'unknown')       AS source,
                       coalesce(r.evidence_level, '')      AS evidence_level,
                       coalesce(r.reference, '')           AS reference,
                       coalesce(r.note, '')                AS note
                """,
                herbs=resolved_herbs,
                drugs=resolved_drugs_lower,
            )
            for row in result:
                drug_lower = _safe_str(row["drug"]).lower()
                citations = dedupe_citations(
                    [
                        make_citation(
                            source_key=row.get("source"),
                            relation_type="INTERACTS_WITH_DRUG",
                            source_layer="L1_direct",
                            evidence=build_evidence_text(
                                row.get("clinical_effect"),
                                row.get("mechanism"),
                                row.get("management"),
                            ) or "Direct herb-drug interaction edge in the SAHAYAK knowledge graph.",
                            evidence_type="herb_drug_interaction",
                            confidence=0.92,
                            extras={
                                "herb": _safe_str(row["herb"]),
                                "drug": resolved_drugs_map.get(drug_lower, _safe_str(row["drug"])),
                                "severity": _safe_str(row.get("severity"), "unknown"),
                                "evidence_level": _safe_str(row.get("evidence_level")),
                                "mechanism": _safe_str(row.get("mechanism")),
                                "clinical_effect": _safe_str(row.get("clinical_effect")),
                                "management": _safe_str(row.get("management")),
                                "reference": _safe_str(row.get("reference")),
                                "note": _safe_str(row.get("note")),
                            },
                        )
                    ]
                )
                results.append(
                    HerbDrugInteraction(
                        herb=_safe_str(row["herb"]),
                        drug=resolved_drugs_map.get(drug_lower, _safe_str(row["drug"])),
                        severity=_safe_str(row["severity"], "unknown"),
                        mechanism=_safe_str(row["mechanism"]),
                        clinical_effect=_safe_str(row["clinical_effect"]),
                        management=_safe_str(row["management"]),
                        source=_safe_str(row["source"]),
                        interaction_pathway="direct",
                        confidence=0.92,
                        citations=citations,
                        source_layer="L1_direct",
                    )
                )
    except Exception as exc:
        logger.error("Herb-drug direct query failed: %s", exc)

    # ── 5b: CYP-mediated herb–drug (e.g., black pepper → CYP3A4 → statin) ─
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (h:Herb)-[inh:INHIBITS]->(enz:Enzyme)<-[sub:IS_SUBSTRATE_OF]-(d:Drug)
                WHERE h.name IN $herbs
                  AND toLower(d.generic_name) IN $drugs
                RETURN h.name                                     AS herb,
                       d.generic_name                             AS drug,
                       enz.name                                   AS enzyme,
                       coalesce(inh.strength, 'unknown')          AS inh_strength,
                       coalesce(sub.fraction, 'unknown')          AS sub_fraction,
                       coalesce(inh.source, 'unknown')            AS inh_source,
                       coalesce(sub.source, 'unknown')            AS sub_source,
                       coalesce(inh.reference, '')                AS inh_reference,
                       coalesce(inh.note, '')                     AS inh_note,
                       coalesce(toFloat(inh.confidence), 0.7)    AS inh_conf,
                       coalesce(toFloat(sub.confidence), 0.8)    AS sub_conf
                """,
                herbs=resolved_herbs,
                drugs=resolved_drugs_lower,
            )
            for row in result:
                drug_lower = _safe_str(row["drug"]).lower()
                herb = _safe_str(row["herb"])
                drug = resolved_drugs_map.get(drug_lower, _safe_str(row["drug"]))
                enzyme = _safe_str(row["enzyme"])
                inh_strength = _safe_str(row["inh_strength"])
                sub_fraction = _safe_str(row["sub_fraction"])
                confidence = round(
                    min(
                        _safe_float(row["inh_conf"], 0.7),
                        _safe_float(row["sub_conf"], 0.8),
                    ) * 0.9,
                    3,
                )
                citations = dedupe_citations(
                    [
                        make_citation(
                            source_key=row.get("inh_source"),
                            relation_type="INHIBITS",
                            source_layer="L2_multihop",
                            evidence=f"{herb} inhibits {enzyme} ({inh_strength}).",
                            evidence_type="mechanism_path",
                            confidence=_safe_float(row["inh_conf"], 0.7),
                            extras={
                                "herb": herb,
                                "drug": drug,
                                "enzyme": enzyme,
                                "strength": inh_strength,
                                "reference": _safe_str(row.get("inh_reference")),
                                "note": _safe_str(row.get("inh_note")),
                            },
                        ),
                        make_citation(
                            source_key=row.get("sub_source"),
                            relation_type="IS_SUBSTRATE_OF",
                            source_layer="L2_multihop",
                            evidence=f"{drug} is a {sub_fraction} substrate of {enzyme}.",
                            evidence_type="mechanism_path",
                            confidence=_safe_float(row["sub_conf"], 0.8),
                            extras={
                                "herb": herb,
                                "drug": drug,
                                "enzyme": enzyme,
                                "fraction": sub_fraction,
                            },
                        ),
                    ]
                )
                results.append(
                    HerbDrugInteraction(
                        herb=herb,
                        drug=drug,
                        severity="moderate",
                        mechanism=(
                            f"{herb} inhibits {enzyme} ({inh_strength}) — "
                            f"{drug} is a {sub_fraction} substrate"
                        ),
                        clinical_effect=(
                            f"Possible increased {drug} plasma levels and toxicity risk "
                            f"via {enzyme} inhibition"
                        ),
                        management=(
                            f"Monitor for {drug} adverse effects when taken with {herb}. "
                            "Consider dose adjustment if co-administered."
                        ),
                        source=source_summary_from_citations(citations),
                        interaction_pathway="cyp_mediated",
                        enzyme=enzyme,
                        confidence=confidence,
                        citations=citations,
                        source_layer="L2_multihop",
                    )
                )
    except Exception as exc:
        logger.error("CYP-mediated herb-drug query failed: %s", exc)

    logger.info("Found %d herb-drug interactions", len(results))
    return results


# ── Function 6: check_beers_criteria ─────────────────────────────────────────

def check_beers_criteria(
    drug_names: list[str],
    patient_age: int = 65,
    patient_conditions: list[str] | None = None,
    *,
    age: int | None = None,
    diagnoses: list[str] | None = None,
) -> list[BeersFlag]:
    """Screen drugs against AGS 2023 Beers Criteria.

    Only applies when patient_age ≥ 65.

    Args:
        drug_names: Raw drug names (resolved internally).
        patient_age: Patient age in years.
        patient_conditions: Free-text conditions (e.g. ["diabetes", "kidney disease"]).

    Returns:
        List of :class:`BeersFlag`.
    """
    # Support keyword aliases age= and diagnoses= for compatibility
    if age is not None:
        patient_age = age
    if patient_conditions is None:
        patient_conditions = diagnoses or []

    logger.info(
        "check_beers_criteria: age=%d, %d drugs, %d conditions",
        patient_age, len(drug_names), len(patient_conditions),
    )

    if patient_age < 65:
        logger.info("Patient age %d < 65 — Beers check skipped", patient_age)
        return []

    resolved_map: dict[str, ResolvedDrug] = {}
    for raw in drug_names:
        rd = resolve_drug_name(raw)
        if rd.found:
            resolved_map[rd.generic_name.lower()] = rd
        else:
            logger.warning("Skipping unresolved drug %r from Beers check", raw)

    if not resolved_map:
        return []

    names_lower = list(resolved_map.keys())
    conditions_lower = [c.lower() for c in patient_conditions if c]
    driver = get_driver()
    flags: list[BeersFlag] = []

    # ── a) Drugs flagged is_beers=true ────────────────────────────────────
    for _, rd in resolved_map.items():
        if rd.is_beers:
            citations = [
                make_citation(
                    source_key="beers_2023",
                    relation_type="FLAGGED_BY",
                    source_layer="L1_direct",
                    evidence=(
                        f"{rd.generic_name} is flagged as potentially inappropriate for older adults "
                        "in the AGS 2023 Beers Criteria."
                    ),
                    evidence_type="geriatric_guideline",
                    confidence=0.97,
                )
            ]
            flags.append(
                BeersFlag(
                    drug=rd.generic_name,
                    flag_type="inappropriate_elderly",
                    category=rd.drug_class or "Potentially Inappropriate Medication",
                    rationale=(
                        "Flagged as potentially inappropriate for older adults "
                        "(AGS 2023 Beers Criteria)."
                    ),
                    recommendation=(
                        "Review necessity. Safer alternatives may be available. "
                        "Consult geriatrician or clinical pharmacist."
                    ),
                    source="beers_2023",
                    confidence=0.97,
                    source_layer="L1_direct",
                    citations=citations,
                )
            )

    # ── b) Drug–disease contraindications ────────────────────────────────
    if conditions_lower:
        try:
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (d:Drug)-[r:CONTRAINDICATED_IN]->(c:Condition)
                    WHERE toLower(d.generic_name) IN $names
                      AND ANY(cond IN $conditions WHERE toLower(c.name) CONTAINS cond)
                    RETURN d.generic_name               AS drug,
                           c.name                       AS condition,
                           coalesce(r.reason, '')        AS reason,
                           coalesce(r.recommendation, '') AS recommendation,
                           coalesce(r.source, 'beers_2023') AS source,
                           coalesce(r.beers_table, '')    AS beers_table,
                           coalesce(r.quality_of_evidence, '') AS quality_of_evidence,
                           coalesce(r.strength, '')       AS strength
                    """,
                    names=names_lower,
                    conditions=conditions_lower,
                )
                for row in result:
                    drug_lower = _safe_str(row["drug"]).lower()
                    canonical = resolved_map.get(drug_lower, ResolvedDrug(False)).generic_name
                    condition = _safe_str(row["condition"])
                    reason = _safe_str(row["reason"])
                    recommendation = _safe_str(row["recommendation"])
                    citations = [
                        make_citation(
                            source_key=row.get("source"),
                            relation_type="CONTRAINDICATED_IN",
                            source_layer="L1_direct",
                            evidence=reason or f"{canonical or row['drug']} may worsen {condition} in older adults.",
                            evidence_type="geriatric_guideline",
                            confidence=0.93,
                            extras={
                                "condition": condition,
                                "table": _safe_str(row.get("beers_table")),
                                "quality_of_evidence": _safe_str(row.get("quality_of_evidence")),
                                "strength": _safe_str(row.get("strength")),
                                "recommendation": recommendation,
                            },
                        )
                    ]
                    flags.append(
                        BeersFlag(
                            drug=canonical or _safe_str(row["drug"]),
                            flag_type="disease_drug",
                            category="Drug–Disease Interaction",
                            rationale=(
                                reason
                                or f"{canonical} may worsen {condition} in elderly patients."
                            ),
                            recommendation=(
                                recommendation
                                or f"Avoid or use with extreme caution given patient's "
                                f"{condition}. Discuss with prescriber."
                            ),
                            source=_safe_str(row.get("source"), "beers_2023"),
                            condition_involved=condition,
                            confidence=0.93,
                            source_layer="L1_direct",
                            citations=citations,
                        )
                    )
        except Exception as exc:
            logger.error("Drug-disease Beers query failed: %s", exc)

    # ── c) Beers Table 5 DDIs (beers_flagged=true on INTERACTS_WITH) ──────
    if len(names_lower) >= 2:
        pairs = [[a, b] for a, b in itertools.combinations(sorted(names_lower), 2)]
        try:
            with driver.session() as session:
                result = session.run(
                    """
                    UNWIND $pairs AS pair
                    MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
                    WHERE toLower(a.generic_name) = pair[0]
                      AND toLower(b.generic_name) = pair[1]
                      AND coalesce(r.beers_flagged, false) = true
                    RETURN a.generic_name                     AS drug_a,
                           b.generic_name                     AS drug_b,
                           coalesce(r.clinical_effect, '')    AS clinical_effect,
                           coalesce(r.management, '')         AS management,
                           coalesce(r.source, 'beers_2023')   AS source,
                           coalesce(r.beers_quality_of_evidence, '') AS quality_of_evidence,
                           coalesce(r.beers_strength, '')     AS strength
                    """,
                    pairs=pairs,
                )
                seen: set[tuple[str, str]] = set()
                for row in result:
                    da = _safe_str(row["drug_a"])
                    db = _safe_str(row["drug_b"])
                    pair_key = tuple(sorted([da.lower(), db.lower()]))
                    if pair_key in seen:
                        continue
                    seen.add(pair_key)
                    citations = [
                        make_citation(
                            source_key=row.get("source"),
                            relation_type="INTERACTS_WITH",
                            source_layer="L1_direct",
                            evidence=build_evidence_text(
                                row.get("clinical_effect"),
                                row.get("management"),
                            ) or "Combination flagged in the AGS 2023 Beers Criteria Table 5.",
                            evidence_type="geriatric_guideline",
                            confidence=0.97,
                            extras={
                                "table": "table5",
                                "quality_of_evidence": _safe_str(row.get("quality_of_evidence")),
                                "strength": _safe_str(row.get("strength")),
                            },
                        )
                    ]
                    flags.append(
                        BeersFlag(
                            drug=f"{da} + {db}",
                            flag_type="ddi_beers",
                            category="Beers Drug–Drug Interaction",
                            rationale=(
                                _safe_str(row["clinical_effect"])
                                or f"Combination flagged in AGS 2023 Beers Criteria Table 5."
                            ),
                            recommendation=(
                                _safe_str(row["management"])
                                or "Avoid combination if possible. Review prescriptions."
                            ),
                            source=_safe_str(row.get("source"), "beers_2023"),
                            confidence=0.97,
                            source_layer="L1_direct",
                            citations=citations,
                        )
                    )
        except Exception as exc:
            logger.error("Beers DDI query failed: %s", exc)

    # ── d) Renal dose adjustments ─────────────────────────────────────────
    has_kidney_condition = any(
        kw in c
        for kw in ("kidney", "renal", "ckd", "creatinine", "egfr", "nephro")
        for c in conditions_lower
    )
    if has_kidney_condition:
        for _, rd in resolved_map.items():
            if rd.renal_dose_adjust:
                flags.append(
                    BeersFlag(
                        drug=rd.generic_name,
                        flag_type="renal_adjust",
                        category="Renal Dose Adjustment Required",
                        rationale=(
                            f"{rd.generic_name} requires renal dose adjustment: "
                            f"{rd.renal_dose_adjust}"
                        ),
                        recommendation=(
                            "Adjust dose based on CrCl (creatinine clearance). "
                            "Consult nephrologist or clinical pharmacist before use."
                        ),
                        source="beers_2023",
                        confidence=0.95,
                        source_layer="L1_direct",
                        citations=[
                            make_citation(
                                source_key="beers_2023",
                                relation_type="FLAGGED_BY",
                                source_layer="L1_direct",
                                evidence=f"{rd.generic_name} requires renal dose adjustment: {rd.renal_dose_adjust}",
                                evidence_type="geriatric_guideline",
                                confidence=0.95,
                                extras={"table": "table6"},
                            )
                        ],
                    )
                )

    logger.info("Found %d Beers flags", len(flags))
    return flags


# ── Function 7: calculate_anticholinergic_burden ─────────────────────────────

def calculate_anticholinergic_burden(drug_names: list[str]) -> ACBResult:
    """Calculate the total Anticholinergic Cognitive Burden (ACB) score.

    Args:
        drug_names: Raw drug names (resolved internally).

    Returns:
        :class:`ACBResult` with total score, risk level, and contributing drugs list.
    """
    logger.info("calculate_anticholinergic_burden: %d drugs", len(drug_names))

    names_lower: list[str] = []
    names_map: dict[str, str] = {}
    for raw in drug_names:
        rd = resolve_drug_name(raw)
        if rd.found:
            names_lower.append(rd.generic_name.lower())
            names_map[rd.generic_name.lower()] = rd.generic_name
        else:
            logger.warning("Skipping unresolved drug %r from ACB check", raw)

    if not names_lower:
        return ACBResult(
            total_score=0,
            risk_level="low",
            contributing_drugs=[],
            clinical_warning="No resolved drugs to evaluate.",
            confidence=0.0,
            source_layer="L1_direct",
        )

    driver = get_driver()
    contributing: list[dict] = []

    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)
                WHERE toLower(d.generic_name) IN $names
                  AND d.anticholinergic_score IS NOT NULL
                  AND d.anticholinergic_score > 0
                RETURN d.generic_name          AS name,
                       d.anticholinergic_score  AS score,
                       coalesce(d.anticholinergic_citation, '') AS citation
                ORDER BY d.anticholinergic_score DESC
                """,
                names=names_lower,
            )
            for row in result:
                name_lower = _safe_str(row["name"]).lower()
                contributing.append(
                    {
                        "drug": names_map.get(name_lower, _safe_str(row["name"])),
                        "score": _safe_int(row["score"]),
                        "citation": _safe_str(row.get("citation")),
                    }
                )
    except Exception as exc:
        logger.error("ACB query failed: %s", exc)

    total = sum(c["score"] for c in contributing)
    risk = _acb_risk_level(total)

    if risk == "high":
        warning = (
            f"HIGH anticholinergic burden (ACB score = {total}). "
            "Strong association with cognitive impairment, delirium, falls, "
            "urinary retention, and constipation in elderly patients. "
            "Urgent medication review recommended."
        )
    elif risk == "moderate":
        warning = (
            f"MODERATE anticholinergic burden (ACB score = {total}). "
            "Monitor for cognitive effects and falls. "
            "Reduce anticholinergic load where clinically possible."
        )
    else:
        warning = f"Low anticholinergic burden (ACB score = {total}). Continue routine monitoring."

    logger.info("ACB: total=%d, risk=%s, contributors=%d", total, risk, len(contributing))
    citations = [
        make_citation(
            source_key="acb_scale",
            relation_type="FLAGGED_BY",
            source_layer="L1_direct",
            evidence=f"{item['drug']} contributes anticholinergic burden score {item['score']}.",
            evidence_type="anticholinergic_burden",
            confidence=0.95,
            extras={
                "table": "table7",
                "score": item["score"],
                "reference": item.get("citation"),
            },
        )
        for item in contributing
    ]
    return ACBResult(
        total_score=total,
        risk_level=risk,
        contributing_drugs=contributing,
        clinical_warning=warning,
        confidence=0.95,
        source_layer="L1_direct",
        citations=dedupe_citations(citations),
    )


# ── Function 8: check_therapeutic_duplication ────────────────────────────────

def check_therapeutic_duplication(drug_names: list[str]) -> list[Duplication]:
    """Detect same-class or same-ingredient therapeutic duplication.

    Args:
        drug_names: Raw drug names (resolved internally).

    Returns:
        List of :class:`Duplication`.
    """
    logger.info("check_therapeutic_duplication: %d drugs", len(drug_names))

    resolved_list: list[ResolvedDrug] = []
    for raw in drug_names:
        rd = resolve_drug_name(raw)
        resolved_list.append(rd)

    duplications: list[Duplication] = []

    # ── a) Same ingredient: two different raw inputs → same generic ───────
    generic_to_inputs: dict[str, list[str]] = defaultdict(list)
    for raw, rd in zip(drug_names, resolved_list):
        if rd.found and rd.generic_name:
            generic_to_inputs[rd.generic_name.lower()].append(raw)

    for generic_lower, inputs in generic_to_inputs.items():
        if len(inputs) > 1:
            canonical = next(
                (rd.generic_name for rd in resolved_list
                 if rd.found and rd.generic_name.lower() == generic_lower),
                generic_lower,
            )
            drug_class = next(
                (rd.drug_class for rd in resolved_list
                 if rd.found and rd.generic_name.lower() == generic_lower),
                "",
            )
            duplications.append(
                Duplication(
                    drug_class=drug_class or canonical,
                    drugs=inputs,
                    duplication_type="same_ingredient",
                    recommendation=(
                        f"Multiple products containing {canonical}: "
                        f"{', '.join(inputs)}. Risk of accidental dose doubling. "
                        "Use only ONE product — consult prescriber immediately."
                    ),
                    confidence=0.99,
                    source_layer="L1_direct",
                    citations=[
                        make_citation(
                            source_key="knowledge_graph",
                            relation_type="MAPS_TO",
                            source_layer="L1_direct",
                            evidence=f"Multiple inputs map to the same ingredient {canonical}: {', '.join(inputs)}.",
                            evidence_type="duplication_rule",
                            confidence=0.99,
                        )
                    ],
                )
            )

    # ── b) Same drug class: group resolved drugs by drug_class ────────────
    found_lowers = [rd.generic_name.lower() for rd in resolved_list if rd.found]
    if found_lowers:
        try:
            driver = get_driver()
            with driver.session() as session:
                result = session.run(
                    """
                    MATCH (d:Drug)
                    WHERE toLower(d.generic_name) IN $names
                      AND d.drug_class IS NOT NULL
                      AND d.drug_class <> ''
                    RETURN d.generic_name  AS name,
                           d.drug_class    AS drug_class
                    """,
                    names=found_lowers,
                )
                class_to_drugs: dict[str, list[str]] = defaultdict(list)
                for row in result:
                    class_to_drugs[_safe_str(row["drug_class"])].append(
                        _safe_str(row["name"])
                    )

                # Deduplicate against same_ingredient flags already found
                already_flagged_generics: set[str] = set()
                for dup in duplications:
                    if dup.duplication_type == "same_ingredient":
                        already_flagged_generics.update(d.lower() for d in dup.drugs)

                for drug_class, members in class_to_drugs.items():
                    if len(members) < 2:
                        continue
                    # Skip if all members already captured by same_ingredient
                    if all(m.lower() in already_flagged_generics for m in members):
                        continue
                    duplications.append(
                        Duplication(
                            drug_class=drug_class,
                            drugs=members,
                            duplication_type="same_class",
                            recommendation=(
                                f"Multiple {drug_class} drugs prescribed: "
                                f"{', '.join(members)}. "
                                "Therapeutic duplication may increase adverse effects "
                                "without added benefit. Review with prescriber."
                            ),
                            confidence=0.92,
                            source_layer="L1_direct",
                            citations=[
                                make_citation(
                                    source_key="knowledge_graph",
                                    relation_type="DRUG_CLASS",
                                    source_layer="L1_direct",
                                    evidence=f"All listed medicines share the drug class {drug_class}: {', '.join(members)}.",
                                    evidence_type="duplication_rule",
                                    confidence=0.92,
                                )
                            ],
                        )
                    )
        except Exception as exc:
            logger.error("Therapeutic duplication class query failed: %s", exc)

    logger.info("Found %d therapeutic duplications", len(duplications))
    return duplications


# ── Function 9: get_drug_side_effects ────────────────────────────────────────

def get_drug_side_effects(drug_names: list[str]) -> dict[str, list[dict]]:
    """Retrieve the top 5 side effects per drug from MAY_CAUSE edges.

    Args:
        drug_names: Raw drug names (resolved internally).

    Returns:
        Dict of canonical_name → list of {side_effect, frequency, source}.
    """
    logger.info("get_drug_side_effects: %d drugs", len(drug_names))

    names_lower: list[str] = []
    names_map: dict[str, str] = {}
    for raw in drug_names:
        rd = resolve_drug_name(raw)
        if rd.found:
            names_lower.append(rd.generic_name.lower())
            names_map[rd.generic_name.lower()] = rd.generic_name

    if not names_lower:
        return {}

    driver = get_driver()
    side_effects: dict[str, list[dict]] = {names_map[n]: [] for n in names_lower}
    per_drug_counts: dict[str, int] = defaultdict(int)

    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)-[r:MAY_CAUSE]->(se:SideEffect)
                WHERE toLower(d.generic_name) IN $names
                RETURN d.generic_name           AS drug,
                       se.name                  AS side_effect,
                       coalesce(r.frequency, '') AS frequency,
                       coalesce(r.source, '')    AS source
                ORDER BY d.generic_name,
                         CASE WHEN r.frequency IS NULL THEN 1 ELSE 0 END ASC,
                         r.frequency DESC
                """,
                names=names_lower,
            )
            for row in result:
                drug_lower = _safe_str(row["drug"]).lower()
                if per_drug_counts[drug_lower] >= 5:
                    continue
                canonical = names_map.get(drug_lower, _safe_str(row["drug"]))
                side_effects.setdefault(canonical, []).append(
                    {
                        "side_effect": _safe_str(row["side_effect"]),
                        "frequency": _safe_str(row["frequency"]),
                        "source": _safe_str(row["source"]),
                    }
                )
                per_drug_counts[drug_lower] += 1
    except Exception as exc:
        logger.error("Side effects query failed: %s", exc)

    return side_effects


# ── Function 10: get_comprehensive_safety_report ─────────────────────────────

def get_comprehensive_safety_report(
    patient_data: dict | None = None,
    *,
    drugs: list[str] | None = None,
    herbs: list[str] | None = None,
    age: int | None = None,
    diagnoses: list[str] | None = None,
) -> "SafetyReport":
    """Orchestrate all safety checks and compile a full SafetyReport.

    Input format::

        patient_data = {
            "drugs":          ["Ecosprin 75mg", "Metformin 500mg", ...],
            "herbs":          ["Ashwagandha", "Triphala"],
            "age":            72,
            "gender":         "female",
            "weight_kg":      65.0,
            "conditions":     ["diabetes", "high blood pressure", "kidney disease"],
            "prescriber_info": {"Ecosprin 75mg": "doctor", "Ashwagandha": "self"},
        }

    Three-tier herb safety classification (CRITICAL):
      - Herb in DB + has interaction edges  → "studied_interactions_present"
      - Herb in DB + no interaction edges   → "insufficient_data" (NOT safe)
      - Herb NOT in DB at all               → "not_in_database" (NOT safe)
    Never outputs "safe" or "no interactions" for unstudied herbs.

    Args:
        patient_data: Patient context dict (see above).

    Returns:
        :class:`SafetyReport`.
    """
    # Support keyword arguments as an alternative to patient_data dict
    if patient_data is None:
        patient_data = {}
    if drugs is not None:
        patient_data = dict(patient_data, drugs=drugs)
    if herbs is not None:
        patient_data = dict(patient_data, herbs=herbs)
    if age is not None:
        patient_data = dict(patient_data, age=age)
    if diagnoses is not None:
        # diagnoses maps to conditions in the patient_data dict
        patient_data = dict(patient_data, conditions=diagnoses)

    timestamp = datetime.now(tz=timezone.utc).isoformat()
    logger.info("get_comprehensive_safety_report started at %s", timestamp)

    # ── Extract fields ────────────────────────────────────────────────────
    raw_drugs: list[str] = [str(d) for d in (patient_data.get("drugs") or []) if d]
    raw_herbs: list[str] = [str(h) for h in (patient_data.get("herbs") or []) if h]
    patient_age: int = _safe_int(patient_data.get("age"), 65)
    conditions: list[str] = [str(c) for c in (patient_data.get("conditions") or []) if c]

    # ── Step 1: Resolve all drug names ────────────────────────────────────
    logger.info("Step 1: Resolving %d drug names", len(raw_drugs))
    resolved_drugs: list[ResolvedDrug] = []
    unresolved_drugs: list[str] = []

    for raw in raw_drugs:
        rd = resolve_drug_name(raw)
        if rd.found:
            resolved_drugs.append(rd)
        else:
            unresolved_drugs.append(raw)
            logger.warning("Drug not resolved: %r", raw)

    # ── Step 2: Resolve all herb names + three-tier classification ────────
    logger.info("Step 2: Resolving %d herb names", len(raw_herbs))
    resolved_herbs: list[ResolvedHerb] = []
    unresolved_herbs: list[dict] = []

    for raw in raw_herbs:
        rh = resolve_herb_name(raw)
        resolved_herbs.append(rh)

        # Three-tier herb safety classification — NEVER say "safe"
        if not rh.herb_in_database:
            classification = "not_in_database"
            classification_note = (
                "This herb was not found in the SAHAYAK database. "
                "Its safety profile cannot be assessed. "
                "PLEASE CONSULT YOUR DOCTOR before continuing use."
            )
        elif rh.has_interaction_data:
            classification = "studied_interactions_present"
            classification_note = (
                "Herb is in the database with known drug interaction data. "
                "See herb-drug interaction results for details."
            )
        else:
            # Herb in DB but no edges — absence of data ≠ safety
            classification = "insufficient_data"
            classification_note = (
                "Herb found in database, but no drug interaction edges are available. "
                "Absence of data does NOT mean safe. "
                "Please consult your doctor before use."
            )

        unresolved_herbs.append(
            {
                "name": raw,
                "resolved_name": rh.name if rh.found else None,
                "herb_in_database": rh.herb_in_database,
                "classification": classification,
                "classification_note": classification_note,
                "match_type": rh.match_type,
                "confidence": rh.confidence,
            }
        )

    # ── Step 3: Decompose combo/multi-ingredient brand drugs ──────────────
    logger.info("Step 3: Decomposing combo drugs")
    effective_resolved: dict[str, ResolvedDrug] = {}  # lower_generic → ResolvedDrug

    for rd in resolved_drugs:
        if rd.ingredients:
            # Multi-ingredient brand — resolve and add each ingredient
            for ingredient in rd.ingredients:
                lower_ing = ingredient.lower()
                if lower_ing not in effective_resolved:
                    ing_rd = resolve_drug_name(ingredient)
                    if ing_rd.found:
                        effective_resolved[ing_rd.generic_name.lower()] = ing_rd
                    else:
                        # Use as-is if resolution fails (shouldn't happen for CONTAINS targets)
                        effective_resolved[lower_ing] = ResolvedDrug(
                            found=True,
                            generic_name=ingredient,
                            match_type="brand_ingredient",
                            confidence=0.85,
                            raw_input=ingredient,
                        )
                        logger.warning("Combo ingredient %r could not be re-resolved", ingredient)
        else:
            lower = rd.generic_name.lower()
            if lower not in effective_resolved:
                effective_resolved[lower] = rd

    effective_drug_names = list(effective_resolved.keys())
    logger.info(
        "Effective drug list (%d): %s",
        len(effective_drug_names),
        effective_drug_names,
    )

    # ── Step 4: Direct DDI ────────────────────────────────────────────────
    logger.info("Step 4: Direct interactions")
    direct_interactions = check_direct_interactions(effective_drug_names)

    # ── Step 5: Indirect (CYP / QT / electrolyte / CNS) ──────────────────
    logger.info("Step 5: Indirect interactions")
    indirect_interactions = check_indirect_interactions(
        effective_drug_names, patient_age=patient_age
    )

    # ── Step 6: Herb–drug ─────────────────────────────────────────────────
    logger.info("Step 6: Herb-drug interactions")
    resolved_herb_names = [rh.name for rh in resolved_herbs if rh.found]
    herb_drug_interactions = check_herb_drug_interactions(
        resolved_herb_names, effective_drug_names
    )

    # ── Step 7: Beers Criteria ────────────────────────────────────────────
    logger.info("Step 7: Beers Criteria (age=%d)", patient_age)
    beers_flags = check_beers_criteria(effective_drug_names, patient_age, conditions)

    # ── Step 8: Anticholinergic burden ────────────────────────────────────
    logger.info("Step 8: Anticholinergic burden")
    acb_result = calculate_anticholinergic_burden(effective_drug_names)

    # ── Step 9: Therapeutic duplication ──────────────────────────────────
    logger.info("Step 9: Therapeutic duplication")
    duplications = check_therapeutic_duplication(effective_drug_names)

    # ── Step 10: Side effects ─────────────────────────────────────────────
    logger.info("Step 10: Side effects")
    side_effects = get_drug_side_effects(effective_drug_names)

    # ── Compile summary ───────────────────────────────────────────────────
    critical_count = 0
    major_count = sum(1 for i in direct_interactions if i.severity == "major")
    moderate_count = sum(1 for i in direct_interactions if i.severity == "moderate")
    minor_count = sum(1 for i in direct_interactions if i.severity == "minor")
    doctor_review_count = sum(1 for i in direct_interactions if i.severity == "unknown")

    for interaction in indirect_interactions:
        if interaction.severity_score >= 8.0:
            critical_count += 1
        elif interaction.severity_score >= 5.0:
            major_count += 1
        elif interaction.severity_score > 0:
            moderate_count += 1
        else:
            doctor_review_count += 1

    summary = {
        "total_direct_interactions": len(direct_interactions),
        "total_indirect_interactions": len(indirect_interactions),
        "total_herb_drug_interactions": len(herb_drug_interactions),
        "critical_count": critical_count,
        "major_count": major_count,
        "moderate_count": moderate_count,
        "minor_count": minor_count,
        "doctor_review_count": doctor_review_count,
        "beers_flags": len(beers_flags),
        "acb_score": acb_result.total_score,
        "acb_risk_level": acb_result.risk_level,
        "duplications": len(duplications),
        "effective_drugs_checked": effective_drug_names,
        "patient_age": patient_age,
        "conditions": conditions,
        "unresolved_drug_count": len(unresolved_drugs),
    }

    metadata = {
        "timestamp": timestamp,
        "checks_performed": [
            "direct_ddi",
            "cyp_inhibition",
            "cyp_induction",
            "qt_prolongation",
            "electrolyte_cascade",
            "cns_depression",
            "herb_drug_direct",
            "herb_drug_cyp",
            "beers_criteria_2023",
            "anticholinergic_burden",
            "therapeutic_duplication",
            "side_effects",
        ],
        "graph_version": "SAHAYAK-KG-v1",
        "confidence_methodology": (
            "L1_direct=0.95-1.0 (graph edges present); "
            "L2_multihop=0.65-0.90 (inferred via CYP/QT/electrolyte pathways)"
        ),
    }

    logger.info(
        "Safety report complete: %d direct, %d indirect, %d herb-drug, "
        "%d beers, ACB=%d (%s), %d duplications",
        len(direct_interactions),
        len(indirect_interactions),
        len(herb_drug_interactions),
        len(beers_flags),
        acb_result.total_score,
        acb_result.risk_level,
        len(duplications),
    )

    return SafetyReport(
        summary=summary,
        direct_interactions=direct_interactions,
        indirect_interactions=indirect_interactions,
        herb_drug_interactions=herb_drug_interactions,
        beers_flags=beers_flags,
        acb_result=acb_result,
        duplications=duplications,
        side_effects=side_effects,
        unresolved_drugs=unresolved_drugs,
        unresolved_herbs=unresolved_herbs,
        metadata=metadata,
    )


# ── Preserved public API (legacy / compatibility) ───────────────────────────

def search_indian_brand(query: str, limit: int = 5) -> list[dict]:
    """Search Indian brands via the Neo4j full-text index.

    Handles common OCR artifacts by searching multiple normalized variants,
    including missing spaces, extra spaces, missing dosage, and swapped
    brand/strength order.

    Args:
        query: Raw brand query text.
        limit: Maximum number of brand matches to return.

    Returns:
        List of matched brands with contained drugs and full-text score.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")

    queries = _fulltext_queries(query)
    if not queries:
        return []

    candidate_limit = max(limit * 4, 10)
    result_limit = max(limit * 4, 10)
    query_variants = set(_brand_search_variants(query))
    driver = get_driver()
    with driver.session() as session:
        records = session.run(
            INDIAN_BRAND_SEARCH_QUERY,
            queries=queries,
            candidate_limit=candidate_limit,
            result_limit=result_limit,
        )
        results = []
        for record in records:
            contained_drugs = sorted(
                dn for dn in (record["contained_drugs"] or []) if dn
            )
            results.append(
                {
                    "brand_name": record["brand_name"],
                    "manufacturer": record["manufacturer"],
                    "composition": record["composition"],
                    "dosage_form": record["dosage_form"],
                    "contained_drugs": contained_drugs,
                    "score": float(record["score"]),
                }
            )
    results.sort(
        key=lambda r: (
            not bool(query_variants & set(_brand_search_variants(r["brand_name"]))),
            -r["score"],
            -len(r["contained_drugs"]),
            r["brand_name"],
        )
    )
    return results[:limit]


def find_interactions(drug_names: list[str]) -> list[dict]:
    """Find pairwise direct drug interactions (legacy wrapper).

    Delegates to :func:`check_direct_interactions`.
    """
    interactions = check_direct_interactions(drug_names)
    return [
        {
            "drug_a": i.drug_a,
            "drug_b": i.drug_b,
            "severity": i.severity,
            "mechanism": i.mechanism,
            "clinical_effect": i.clinical_effect,
            "management": i.management,
            "source": i.source,
            "beers_flagged": i.beers_flagged,
            "confidence": i.confidence,
        }
        for i in interactions
    ]


def find_herb_drug_interactions(herb: str) -> list[dict]:
    """Find all drug interactions for a specific herb (legacy wrapper).

    Args:
        herb: Herb name.

    Returns:
        List of interaction dicts.
    """
    driver = get_driver()
    try:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (h:Herb)-[r:INTERACTS_WITH_DRUG]->(d:Drug)
                WHERE toLower(h.name) = toLower($herb)
                RETURN d.generic_name                      AS drug,
                       coalesce(r.severity, 'unknown')     AS severity,
                       coalesce(r.mechanism, '')           AS mechanism,
                       coalesce(r.clinical_effect, '')     AS clinical_effect,
                       coalesce(r.management, '')          AS management,
                       coalesce(r.source, 'unknown')       AS source
                """,
                herb=herb,
            )
            return [dict(row) for row in result]
    except Exception as exc:
        logger.error("find_herb_drug_interactions failed for %r: %s", herb, exc)
        return []


def find_beers_flags(drug_names: list[str]) -> list[dict]:
    """Find Beers Criteria flags (legacy wrapper, assumes age 70).

    Args:
        drug_names: List of drug names.

    Returns:
        List of flag dicts.
    """
    flags = check_beers_criteria(drug_names, patient_age=70, patient_conditions=[])
    return [
        {
            "drug": f.drug,
            "flag_type": f.flag_type,
            "category": f.category,
            "rationale": f.rationale,
            "recommendation": f.recommendation,
            "confidence": f.confidence,
        }
        for f in flags
    ]


def get_drug_details(drug_name: str) -> dict | None:
    """Retrieve full node properties for a drug.

    Args:
        drug_name: Drug name (brand or generic).

    Returns:
        Drug properties dict or ``None`` if not found.
    """
    rd = resolve_drug_name(drug_name)
    if not rd.found:
        return None
    return {
        "generic_name": rd.generic_name,
        "rxcui": rd.rxcui,
        "drug_class": rd.drug_class,
        "is_beers": rd.is_beers,
        "is_nti": rd.is_nti,
        "anticholinergic_score": rd.anticholinergic_score,
        "renal_dose_adjust": rd.renal_dose_adjust,
        "match_type": rd.match_type,
        "confidence": rd.confidence,
    }
