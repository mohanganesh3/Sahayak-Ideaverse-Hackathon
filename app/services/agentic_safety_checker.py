"""SAHAYAK Agentic Safety Checker — LangGraph-powered intelligence layer.

Orchestrates query_engine.py (deterministic graph lookups) with:
  - CRAG (Corrective RAG): LLM evaluates completeness of graph results
  - Deep analysis: broader Neo4j search + pharmacological LLM reasoning
  - Anti-hallucination guard: every LLM finding verified against graph
  - 30-second hard timeout with graceful fallback to graph-only results

Pipeline:
  intake_and_resolve
    → graph_safety_checks
      → evaluate_completeness
        ├─ (score < 0.7) → deep_analysis → verify_and_compile
        └─ (score ≥ 0.7) → verify_and_compile
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import re
import time
from operator import add
from typing import Annotated, Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from app.config import GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY
from app.graph.neo4j_connection import get_driver
from app.graph.query_engine import (
    calculate_anticholinergic_burden,
    check_beers_criteria,
    check_direct_interactions,
    check_herb_drug_interactions,
    check_indirect_interactions,
    check_therapeutic_duplication,
    get_drug_side_effects,
    resolve_drug_name,
    resolve_herb_name,
)
from app.services.citation_utils import (
    dedupe_citations,
    make_citation,
    source_summary_from_citations,
)

logger = logging.getLogger(__name__)

# ── Pipeline constants ───────────────────────────────────────────────────────

_PIPELINE_TIMEOUT_S = 30.0
_LLM_CALL_TIMEOUT_S = 20.0
_COMPLETENESS_THRESHOLD = 0.7

_SEVERITY_SORT_KEY: dict[str, int] = {
    "critical": 5, "major": 4, "moderate": 3,
    "minor": 2, "unknown": 1, "": 1,
}
_DISPLAY_SEVERITY_ORDER = ("critical", "major", "moderate", "minor", "doctor_review")


# ── State definition ─────────────────────────────────────────────────────────

class SafetyState(TypedDict, total=False):
    # ── Input ────────────────────────────────────────────────────────────
    patient_data: dict

    # ── After NODE 1: intake_and_resolve ─────────────────────────────────
    resolved_drugs: list[dict]       # serialized ResolvedDrug dicts
    resolved_herbs: list[dict]       # serialized ResolvedHerb dicts
    unresolved_drugs: list[str]
    unresolved_herbs: list[str]
    effective_drug_names: list[str]  # generic names incl. combo ingredients
    complexity: str                  # "simple"|"moderate"|"complex"

    # ── After NODE 2: graph_safety_checks ─────────────────────────────────
    l1_findings: list[dict]
    l2_findings: list[dict]
    herb_findings: list[dict]
    beers_flags: list[dict]
    acb_result: dict
    duplications: list[dict]
    side_effects: dict

    # ── After NODE 3: evaluate_completeness ───────────────────────────────
    completeness_score: float
    missing_interactions: list[dict]

    # ── After NODE 4: deep_analysis ──────────────────────────────────────
    deep_findings: list[dict]        # L3 candidates (not yet verified)

    # ── After NODE 5: verify_and_compile ──────────────────────────────────
    verified_findings: list[dict]
    removed_findings: list[dict]
    final_report: dict


# ── LLM factory ──────────────────────────────────────────────────────────────

def _get_llm() -> ChatOpenAI | None:
    """Return LLM instance: OpenAI (primary) → Gemini (fallback) → Groq (fallback)."""
    if OPENAI_API_KEY:
        return ChatOpenAI(
            model="gpt-4o-mini",
            api_key=OPENAI_API_KEY,
            temperature=0,
            timeout=22,
            max_retries=1,
        )
    if GEMINI_API_KEY:
        return ChatOpenAI(
            model="gemini-2.0-flash",
            api_key=GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            temperature=0,
            timeout=22,
            max_retries=1,
        )
    if not GROQ_API_KEY:
        logger.warning("GROQ_API_KEY not set — LLM features will be skipped")
        return None
    return ChatOpenAI(
        model="llama-3.3-70b-versatile",
        api_key=GROQ_API_KEY,
        base_url="https://api.groq.com/openai/v1",
        temperature=0,
        timeout=22,
        max_retries=1,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize(obj: Any) -> Any:
    """Recursively convert dataclasses and lists to JSON-serializable dicts."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, list):
        return [_serialize(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _extract_json(text: str) -> dict:
    """Extract JSON from LLM response, handling markdown code fences."""
    # Strip markdown fences
    match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
    if match:
        text = match.group(1)
    # Try parsing (may still be dirty)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Find outermost {...}
        brace_match = re.search(r"\{[\s\S]+\}", text)
        if brace_match:
            return json.loads(brace_match.group(0))
        raise


def _format_findings_for_llm(
    l1: list[dict], l2: list[dict], herbs: list[dict]
) -> str:
    lines: list[str] = []
    for f in l1:
        sev = (f.get("severity") or "?").upper()
        lines.append(
            f"  [{sev}] {f.get('drug_a')} + {f.get('drug_b')}: "
            f"{(f.get('clinical_effect') or '')[:80]}"
        )
    for f in l2:
        itype = f.get("interaction_type", "indirect")
        lines.append(
            f"  [INDIRECT/{itype}] {f.get('drug_a')} + {f.get('drug_b')}: "
            f"{(f.get('pathway') or '')[:80]}"
        )
    for f in herbs:
        lines.append(
            f"  [HERB] {f.get('herb')} + {f.get('drug')}: "
            f"{(f.get('clinical_effect') or '')[:60]}"
        )
    return "\n".join(lines) if lines else "  (none)"


def _format_optional_clinical_context(patient_data: dict) -> str:
    """Format optional vitals and recent values for LLM review."""
    fields = [
        ("Weight (kg)", patient_data.get("weight_kg")),
        ("Blood pressure", (
            f"{patient_data.get('systolic_bp')}/{patient_data.get('diastolic_bp')} mmHg"
            if patient_data.get("systolic_bp") and patient_data.get("diastolic_bp")
            else ""
        )),
        ("Fasting glucose", f"{patient_data.get('fasting_blood_sugar')} mg/dL" if patient_data.get("fasting_blood_sugar") else ""),
        ("Post-meal glucose", f"{patient_data.get('postprandial_blood_sugar')} mg/dL" if patient_data.get("postprandial_blood_sugar") else ""),
        ("SpO2", f"{patient_data.get('spo2')}%" if patient_data.get("spo2") else ""),
        ("Heart rate", f"{patient_data.get('heart_rate')} bpm" if patient_data.get("heart_rate") else ""),
        ("Serum creatinine", f"{patient_data.get('serum_creatinine')} mg/dL" if patient_data.get("serum_creatinine") else ""),
    ]
    lines = [f"  - {label}: {value}" for label, value in fields if value not in ("", None, 0, 0.0)]
    return "\n".join(lines) if lines else "  (no optional clinical values provided)"


def _finding_priority(f: dict) -> int:
    """Sort key: higher = shown first."""
    sev = (f.get("severity") or f.get("expected_severity") or "unknown").lower()
    score = _SEVERITY_SORT_KEY.get(sev, 1)
    cyp_bonus = 2 if "cyp" in (f.get("interaction_type") or "") else 0
    return score * 10 + cyp_bonus


def _display_severity_for_finding(finding: dict[str, Any]) -> str:
    severity = str(finding.get("severity") or "").strip().lower()
    if severity in {"critical", "major", "moderate", "minor"}:
        return severity
    try:
        score = float(finding.get("severity_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score >= 8.0:
        return "critical"
    if score >= 5.0:
        return "major"
    if score > 0:
        return "moderate"
    return "doctor_review"


# ── Neo4j helpers for deep analysis + verification ────────────────────────────

def _neo4j_find_cyp_link(drug_a_lower: str, drug_b_lower: str) -> list[dict]:
    """Check if drug_a inhibits an enzyme that drug_b uses as substrate."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(
                """
                MATCH (a:Drug)-[inh:INHIBITS]->(e:Enzyme)<-[sub:IS_SUBSTRATE_OF]-(b:Drug)
                WHERE toLower(a.generic_name) = $da AND toLower(b.generic_name) = $db
                RETURN a.generic_name                           AS drug_a,
                       e.name                                   AS enzyme,
                       b.generic_name                           AS drug_b,
                       coalesce(inh.strength, 'unknown')        AS inh_strength,
                       coalesce(sub.fraction, 'unknown')        AS sub_fraction,
                       coalesce(inh.source, 'unknown')          AS inh_source,
                       coalesce(sub.source, 'unknown')          AS sub_source,
                       coalesce(toFloat(inh.confidence), 0.8)  AS inh_conf,
                       coalesce(toFloat(sub.confidence), 0.8)  AS sub_conf
                """,
                da=drug_a_lower,
                db=drug_b_lower,
            )
            return [dict(r) for r in result]
    except Exception as exc:
        logger.error("CYP link query failed for %s/%s: %s", drug_a_lower, drug_b_lower, exc)
        return []


def _neo4j_verify_inhibits(drug_lower: str, enzyme: str) -> bool:
    """Return True if drug has an INHIBITS edge to the given enzyme."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)-[:INHIBITS]->(e:Enzyme)
                WHERE toLower(d.generic_name) = $drug AND e.name = $enzyme
                RETURN count(*) AS cnt
                """,
                drug=drug_lower,
                enzyme=enzyme,
            )
            records = list(result)
            return (records[0]["cnt"] > 0) if records else False
    except Exception as exc:
        logger.error("_neo4j_verify_inhibits failed: %s", exc)
        return False


def _neo4j_verify_substrate(drug_lower: str, enzyme: str) -> bool:
    """Return True if drug has an IS_SUBSTRATE_OF edge to the given enzyme."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(
                """
                MATCH (d:Drug)-[:IS_SUBSTRATE_OF]->(e:Enzyme)
                WHERE toLower(d.generic_name) = $drug AND e.name = $enzyme
                RETURN count(*) AS cnt
                """,
                drug=drug_lower,
                enzyme=enzyme,
            )
            records = list(result)
            return (records[0]["cnt"] > 0) if records else False
    except Exception as exc:
        logger.error("_neo4j_verify_substrate failed: %s", exc)
        return False


def _neo4j_get_drug_class(drug_lower: str) -> str:
    """Return the drug_class for a given lowercase generic name."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(
                "MATCH (d:Drug) WHERE toLower(d.generic_name) = $name "
                "RETURN coalesce(d.drug_class, '') AS drug_class LIMIT 1",
                name=drug_lower,
            )
            records = list(result)
            return str(records[0]["drug_class"]) if records else ""
    except Exception as exc:
        logger.error("_neo4j_get_drug_class failed: %s", exc)
        return ""


def _neo4j_decompose_brand(brand_name: str) -> list[str]:
    """Return generic ingredient names for a brand via CONTAINS edges."""
    try:
        driver = get_driver()
        with driver.session() as session:
            result = session.run(
                """
                MATCH (b:IndianBrand)-[:CONTAINS]->(d:Drug)
                WHERE toLower(b.brand_name) CONTAINS toLower($name)
                RETURN d.generic_name AS ingredient
                LIMIT 10
                """,
                name=brand_name.lower()[:20],  # guard against too-long names
            )
            return [str(r["ingredient"]) for r in result if r["ingredient"]]
    except Exception as exc:
        logger.error("Brand decomposition query failed for %r: %s", brand_name, exc)
        return []


# ── NODE 1: intake_and_resolve ────────────────────────────────────────────────

def intake_and_resolve_node(state: SafetyState) -> dict:
    """Resolve all drug and herb names, decompose combo brands, classify complexity."""
    patient_data: dict = state.get("patient_data") or {}
    raw_drugs: list[str] = [str(d) for d in (patient_data.get("drugs") or []) if d]
    raw_herbs: list[str] = [str(h) for h in (patient_data.get("herbs") or []) if h]

    logger.info(
        "NODE intake_and_resolve: %d drugs, %d herbs",
        len(raw_drugs), len(raw_herbs),
    )

    resolved_drugs: list[dict] = []
    unresolved_drugs: list[str] = []

    for raw in raw_drugs:
        rd = resolve_drug_name(raw)
        if rd.found:
            resolved_drugs.append(_serialize(rd))
        else:
            unresolved_drugs.append(raw)
            logger.warning("Drug not resolved at intake: %r", raw)

    # Decompose combo brands → individual ingredients
    effective_set: dict[str, dict] = {}  # lower_generic → resolved dict
    for rd_dict in resolved_drugs:
        ingredients: list[str] = rd_dict.get("ingredients") or []
        if ingredients:
            for ing in ingredients:
                if ing.lower() not in effective_set:
                    ing_rd = resolve_drug_name(ing)
                    if ing_rd.found:
                        effective_set[ing_rd.generic_name.lower()] = _serialize(ing_rd)
                    else:
                        # Keep raw ingredient name for best-effort checking
                        effective_set[ing.lower()] = {
                            "found": True,
                            "generic_name": ing,
                            "drug_class": "",
                            "match_type": "brand_ingredient",
                            "confidence": 0.80,
                        }
        else:
            lower = rd_dict.get("generic_name", "").lower()
            if lower and lower not in effective_set:
                effective_set[lower] = rd_dict

    effective_drug_names = list(effective_set.keys())

    # Resolve herbs
    resolved_herbs: list[dict] = []
    unresolved_herbs: list[str] = []
    for raw in raw_herbs:
        rh = resolve_herb_name(raw)
        resolved_herbs.append(_serialize(rh))
        if not rh.found:
            unresolved_herbs.append(raw)

    # Complexity classification
    n_drugs = len(effective_drug_names)
    n_herbs = len(resolved_herbs)
    n_unresolved = len(unresolved_drugs)

    if n_drugs >= 8 or n_unresolved > 0:
        complexity = "complex"
    elif n_drugs >= 4 or n_herbs > 0:
        complexity = "moderate"
    else:
        complexity = "simple"

    logger.info(
        "Resolved %d effective drugs, %d herbs, complexity=%s",
        n_drugs, n_herbs, complexity,
    )

    return {
        "resolved_drugs": resolved_drugs,
        "resolved_herbs": resolved_herbs,
        "unresolved_drugs": unresolved_drugs,
        "unresolved_herbs": unresolved_herbs,
        "effective_drug_names": effective_drug_names,
        "complexity": complexity,
    }


# ── NODE 2: graph_safety_checks ───────────────────────────────────────────────

def graph_safety_checks_node(state: SafetyState) -> dict:
    """Run all deterministic query_engine checks. Tags findings with source layer."""
    effective_names: list[str] = state.get("effective_drug_names") or []
    resolved_herbs = state.get("resolved_herbs") or []
    patient_data: dict = state.get("patient_data") or {}
    patient_age: int = int(patient_data.get("age") or 65)
    conditions: list[str] = [str(c) for c in (patient_data.get("conditions") or []) if c]
    herb_names = [rh["name"] for rh in resolved_herbs if rh.get("found") and rh.get("name")]

    logger.info(
        "NODE graph_safety_checks: %d drugs, %d herbs",
        len(effective_names), len(herb_names),
    )

    direct = check_direct_interactions(effective_names)
    indirect = check_indirect_interactions(effective_names, patient_age=patient_age)
    herb_drug = check_herb_drug_interactions(herb_names, effective_names)
    beers = check_beers_criteria(effective_names, patient_age, conditions)
    acb = calculate_anticholinergic_burden(effective_names)
    dups = check_therapeutic_duplication(effective_names)
    side_fx = get_drug_side_effects(effective_names)

    return {
        "l1_findings": _serialize(direct),
        "l2_findings": _serialize(indirect),
        "herb_findings": _serialize(herb_drug),
        "beers_flags": _serialize(beers),
        "acb_result": _serialize(acb),
        "duplications": _serialize(dups),
        "side_effects": side_fx,
    }


# ── NODE 3: evaluate_completeness (CRAG) ─────────────────────────────────────

async def evaluate_completeness_node(state: SafetyState) -> dict:
    """CRAG: LLM evaluates whether graph findings are complete.

    Sends patient context + graph results to Groq/llama-3.3.
    Returns completeness_score and list of suspected missing interactions.
    On timeout or LLM unavailability, returns score=1.0 to skip deep analysis.
    """
    llm = _get_llm()
    if llm is None:
        logger.info("LLM unavailable — skipping CRAG evaluation (score=1.0)")
        return {"completeness_score": 1.0, "missing_interactions": []}

    patient_data: dict = state.get("patient_data") or {}
    age = patient_data.get("age", "unknown")
    gender = patient_data.get("gender", "unknown")
    conditions = patient_data.get("conditions") or []
    resolved_drugs = state.get("resolved_drugs") or []
    resolved_herbs = state.get("resolved_herbs") or []
    unresolved = state.get("unresolved_drugs") or []

    l1 = state.get("l1_findings") or []
    l2 = state.get("l2_findings") or []
    herbs = state.get("herb_findings") or []

    # Build drug list with classes
    drug_lines = []
    for rd in resolved_drugs:
        name = rd.get("generic_name", "?")
        cls = rd.get("drug_class", "")
        drug_lines.append(f"  - {name}" + (f" ({cls})" if cls else ""))
    drug_summary = "\n".join(drug_lines) or "  (none)"

    herb_lines = [
        f"  - {rh.get('name', '?')}" + (f" ({rh.get('category','')})" if rh.get("category") else "")
        for rh in resolved_herbs if rh.get("found")
    ]
    herb_summary = "\n".join(herb_lines) or "  (none)"

    findings_summary = _format_findings_for_llm(l1, l2, herbs)
    clinical_context = _format_optional_clinical_context(patient_data)

    system_msg = (
        "You are a senior clinical pharmacist with 20 years experience in "
        "geriatric polypharmacy. You review automated drug interaction screening "
        "results and catch dangerous interactions that automated systems miss. "
        "You respond ONLY with valid JSON, never with prose."
    )

    user_msg = f"""Patient: {age}yo {gender}, conditions: {', '.join(conditions) or 'none stated'}

Medications:
{drug_summary}

Herbs/supplements:
{herb_summary}

Optional clinical values:
{clinical_context}

Automated system found these interactions:
{findings_summary}

Unresolved drug names (could not be identified): {unresolved or 'none'}

EVALUATE COMPLETENESS. Think step by step:
1. Are there well-known dangerous interactions between these specific medications MISSING from results?
2. Are there herb-drug interactions that should have been caught?
3. Are there condition-drug contraindications not flagged (especially for elderly patients)?
4. Do the optional clinical values make any interaction more urgent, especially kidney disease, hypotension, hypoglycemia, hypoxia, or renal dose concerns?
5. For any unresolved drugs, what are they likely to be?

Respond ONLY with valid JSON, no other text:
{{
  "completeness_score": 0.0,
  "missing_interactions": [
    {{"drug_a": "", "drug_b": "", "expected_severity": "major|moderate|minor", "mechanism": "", "reasoning": ""}}
  ],
  "missing_herb_checks": [
    {{"herb": "", "drug": "", "concern": ""}}
  ],
  "unresolved_guesses": [
    {{"original": "", "likely_identity": ""}}
  ],
  "needs_deeper_check": true
}}"""

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(content=system_msg),
                HumanMessage(content=user_msg),
            ]),
            timeout=_LLM_CALL_TIMEOUT_S,
        )
        data = _extract_json(response.content)
        score = float(data.get("completeness_score", 1.0))
        score = max(0.0, min(1.0, score))

        # Merge missing_interactions and missing_herb_checks
        missing: list[dict] = []
        for item in (data.get("missing_interactions") or []):
            if item.get("drug_a") and item.get("drug_b"):
                item["source"] = "crag_evaluation"
                missing.append(item)
        for item in (data.get("missing_herb_checks") or []):
            if item.get("herb") and item.get("drug"):
                missing.append({
                    "drug_a": item["herb"],
                    "drug_b": item["drug"],
                    "expected_severity": "moderate",
                    "mechanism": item.get("concern", ""),
                    "reasoning": item.get("concern", ""),
                    "source": "crag_herb_check",
                })

        logger.info(
            "CRAG evaluation: score=%.2f, missing=%d, herbs=%d",
            score, len(missing), len(data.get("missing_herb_checks") or []),
        )
        return {"completeness_score": score, "missing_interactions": missing}

    except asyncio.TimeoutError:
        logger.warning("CRAG LLM timed out — treating as complete (score=1.0)")
        return {"completeness_score": 1.0, "missing_interactions": []}
    except Exception as exc:
        logger.error("CRAG evaluation failed: %s", exc)
        return {"completeness_score": 1.0, "missing_interactions": []}


# ── Routing function ──────────────────────────────────────────────────────────

def _route_after_evaluation(state: SafetyState) -> str:
    score = float(state.get("completeness_score") or 1.0)
    missing = state.get("missing_interactions") or []
    if score < _COMPLETENESS_THRESHOLD and missing:
        logger.info("CRAG score=%.2f → routing to deep_analysis", score)
        return "deep_analysis"
    logger.info("CRAG score=%.2f → routing to verify_and_compile", score)
    return "verify_and_compile"


# ── NODE 4: deep_analysis ────────────────────────────────────────────────────

async def deep_analysis_node(state: SafetyState) -> dict:
    """For each CRAG-identified gap: try broader Neo4j search, then LLM reasoning.

    All findings produced here are tagged L3_llm_assisted and must survive
    verification in Node 5 before reaching the final report.
    """
    missing: list[dict] = state.get("missing_interactions") or []
    logger.info("NODE deep_analysis: investigating %d suspected gaps", len(missing))

    llm = _get_llm()
    deep_findings: list[dict] = []

    for item in missing:
        drug_a = str(item.get("drug_a") or "").strip()
        drug_b = str(item.get("drug_b") or "").strip()
        if not drug_a or not drug_b:
            continue

        # ── Step 1: Broader Neo4j CYP search ─────────────────────────────
        cyp_links = _neo4j_find_cyp_link(drug_a.lower(), drug_b.lower())
        if cyp_links:
            for link in cyp_links:
                inh_conf = float(link.get("inh_conf") or 0.8)
                sub_conf = float(link.get("sub_conf") or 0.8)
                confidence = round(min(inh_conf, sub_conf) * 0.9, 3)
                enzyme = str(link.get("enzyme") or "")
                inh_str = str(link.get("inh_strength") or "unknown")
                sub_frac = str(link.get("sub_fraction") or "unknown")
                citations = dedupe_citations(
                    [
                        make_citation(
                            source_key=link.get("inh_source"),
                            relation_type="INHIBITS",
                            source_layer="L2_multihop",
                            evidence=f"{drug_a} inhibits {enzyme} ({inh_str}).",
                            evidence_type="mechanism_path",
                            confidence=inh_conf,
                            extras={"drug": drug_a, "enzyme": enzyme, "strength": inh_str},
                        ),
                        make_citation(
                            source_key=link.get("sub_source"),
                            relation_type="IS_SUBSTRATE_OF",
                            source_layer="L2_multihop",
                            evidence=f"{drug_b} is a {sub_frac} substrate of {enzyme}.",
                            evidence_type="mechanism_path",
                            confidence=sub_conf,
                            extras={"drug": drug_b, "enzyme": enzyme, "fraction": sub_frac},
                        ),
                    ]
                )
                deep_findings.append({
                    "drug_a": str(link.get("drug_a") or drug_a),
                    "drug_b": str(link.get("drug_b") or drug_b),
                    "interaction_type": "cyp_inhibition",
                    "pathway": (
                        f"{drug_a} --[inhibits {inh_str}]--> {enzyme} "
                        f"<--[substrate {sub_frac}]-- {drug_b}"
                    ),
                    "severity": item.get("expected_severity", "moderate"),
                    "severity_score": 6.0,
                    "mechanism": item.get("mechanism") or f"CYP {enzyme} mediated",
                    "clinical_implication": (
                        f"{drug_a} inhibits {enzyme} metabolism of {drug_b}. "
                        "Increased plasma levels and toxicity risk."
                    ),
                    "confidence": confidence,
                    "source": source_summary_from_citations(citations),
                    "source_layer": "L2_multihop",  # confirmed in graph = L2
                    "citations": citations,
                    "is_ai_assessed": False,
                    "_enzyme": enzyme,
                    "_verified_inhibitor": True,
                    "_verified_substrate": True,
                })
            logger.info("CYP link confirmed in graph: %s → %s", drug_a, drug_b)
            continue

        # ── Step 2: LLM pharmacological reasoning ────────────────────────
        if llm is None:
            continue

        class_a = _neo4j_get_drug_class(drug_a.lower())
        class_b = _neo4j_get_drug_class(drug_b.lower())

        prompt = (
            f"Based on pharmacological knowledge:\n"
            f"Drug A: {drug_a} (class: {class_a or 'unknown'})\n"
            f"Drug B: {drug_b} (class: {class_b or 'unknown'})\n\n"
            f"Context: {item.get('reasoning', '')}\n\n"
            f"Is there a clinically significant interaction? "
            f"Respond ONLY with valid JSON:\n"
            f'{{"interaction": true, "mechanism": "...", '
            f'"severity": "major|moderate|minor", '
            f'"confidence": 0.65, '
            f'"enzyme_involved": "CYP3A4 or null", '
            f'"reasoning": "brief explanation"}}'
        )

        try:
            response = await asyncio.wait_for(
                llm.ainvoke([HumanMessage(content=prompt)]),
                timeout=10.0,
            )
            assessment = _extract_json(response.content)
            if not assessment.get("interaction"):
                continue

            raw_conf = float(assessment.get("confidence") or 0.6)
            confidence = min(raw_conf, 0.75)  # cap at 0.75 for L3
            enzyme_involved = str(assessment.get("enzyme_involved") or "")

            deep_findings.append({
                "drug_a": drug_a,
                "drug_b": drug_b,
                "interaction_type": (
                    "cyp_inhibition" if enzyme_involved and enzyme_involved != "null"
                    else "pharmacodynamic"
                ),
                "pathway": f"{drug_a} + {drug_b}: {assessment.get('mechanism', '')}",
                "severity": assessment.get("severity", "moderate"),
                "severity_score": _SEVERITY_SORT_KEY.get(
                    assessment.get("severity", "moderate"), 3
                ) * 1.5,
                "mechanism": str(assessment.get("mechanism") or ""),
                "clinical_implication": str(assessment.get("reasoning") or ""),
                "confidence": confidence,
                "source": "AI-assisted Pharmacology Review",
                "source_layer": "L3_llm_assisted",
                "citations": [
                    make_citation(
                        source_key="ai_assisted",
                        relation_type="LLM_ASSESSMENT",
                        source_layer="L3_llm_assisted",
                        evidence=str(assessment.get("reasoning") or assessment.get("mechanism") or ""),
                        evidence_type="ai_assessment",
                        confidence=confidence,
                        extras={"enzyme": enzyme_involved if enzyme_involved != "null" else ""},
                    )
                ],
                "is_ai_assessed": True,
                "_enzyme": enzyme_involved if enzyme_involved != "null" else "",
                "_verified_inhibitor": False,
                "_verified_substrate": False,
                "_llm_reasoning": str(assessment.get("reasoning") or ""),
            })
            logger.info(
                "LLM identified interaction %s+%s (conf=%.2f, layer=L3)",
                drug_a, drug_b, confidence,
            )

        except asyncio.TimeoutError:
            logger.warning("LLM assessment timed out for %s+%s", drug_a, drug_b)
        except Exception as exc:
            logger.error("LLM assessment failed for %s+%s: %s", drug_a, drug_b, exc)

    logger.info("deep_analysis produced %d candidate findings", len(deep_findings))
    return {"deep_findings": deep_findings}


# ── NODE 5: verify_and_compile ────────────────────────────────────────────────

def verify_and_compile_node(state: SafetyState) -> dict:
    """Verify LLM findings against Neo4j, compile and sort the final report."""
    l1 = state.get("l1_findings") or []
    l2 = state.get("l2_findings") or []
    herb = state.get("herb_findings") or []
    deep = state.get("deep_findings") or []
    beers = state.get("beers_flags") or []
    acb = state.get("acb_result") or {}
    dups = state.get("duplications") or []
    side_fx = state.get("side_effects") or {}
    patient_data = state.get("patient_data") or {}
    resolved_herbs = state.get("resolved_herbs") or []
    unresolved_drugs = state.get("unresolved_drugs") or []
    completeness_score = float(state.get("completeness_score") or 1.0)

    logger.info(
        "NODE verify_and_compile: l1=%d l2=%d herb=%d deep=%d",
        len(l1), len(l2), len(herb), len(deep),
    )

    verified: list[dict] = []
    removed: list[dict] = []

    # ── Verified L1 and L2 findings (graph edges — already authoritative) ─
    for f in l1:
        f["is_ai_assessed"] = False
        if not f.get("source") and f.get("citations"):
            f["source"] = source_summary_from_citations(f["citations"])
        verified.append(f)
    for f in l2:
        f["is_ai_assessed"] = False
        if not f.get("source") and f.get("citations"):
            f["source"] = source_summary_from_citations(f["citations"])
        verified.append(f)
    for f in herb:
        f["is_ai_assessed"] = False
        if not f.get("source") and f.get("citations"):
            f["source"] = source_summary_from_citations(f["citations"])
        verified.append(f)

    # ── Verify each deep (L3 or newly found L2) finding ───────────────────
    for f in deep:
        layer = f.get("source_layer", "L3_llm_assisted")

        if layer == "L2_multihop" and f.get("_verified_inhibitor") and f.get("_verified_substrate"):
            # Already confirmed via graph in deep_analysis
            verified.append(f)
            continue

        if layer != "L3_llm_assisted":
            verified.append(f)
            continue

        # L3: verify mechanism claims against Neo4j
        drug_a = (f.get("drug_a") or "").lower()
        drug_b = (f.get("drug_b") or "").lower()
        enzyme = (f.get("_enzyme") or "").strip()
        passed = False

        if enzyme and enzyme != "null":
            # Verify: drug_a inhibits enzyme AND drug_b is substrate
            inhibits_ok = _neo4j_verify_inhibits(drug_a, enzyme)
            substrate_ok = _neo4j_verify_substrate(drug_b, enzyme)

            if inhibits_ok and substrate_ok:
                f["confidence"] = round(f["confidence"] * 1.1, 3)  # boost if confirmed
                f["source_layer"] = "L2_multihop"   # upgrade to L2 — confirmed
                f["is_ai_assessed"] = False
                verification_links = _neo4j_find_cyp_link(drug_a, drug_b)
                verification_citations: list[dict] = []
                for link in verification_links:
                    verification_citations.extend(
                        [
                            make_citation(
                                source_key=link.get("inh_source"),
                                relation_type="INHIBITS",
                                source_layer="L2_multihop",
                                evidence=f"{f.get('drug_a')} inhibits {enzyme}.",
                                evidence_type="mechanism_path",
                                confidence=link.get("inh_conf"),
                                extras={
                                    "drug": f.get("drug_a"),
                                    "enzyme": enzyme,
                                    "strength": link.get("inh_strength"),
                                },
                            ),
                            make_citation(
                                source_key=link.get("sub_source"),
                                relation_type="IS_SUBSTRATE_OF",
                                source_layer="L2_multihop",
                                evidence=f"{f.get('drug_b')} is a substrate of {enzyme}.",
                                evidence_type="mechanism_path",
                                confidence=link.get("sub_conf"),
                                extras={
                                    "drug": f.get("drug_b"),
                                    "enzyme": enzyme,
                                    "fraction": link.get("sub_fraction"),
                                },
                            ),
                        ]
                    )
                f["citations"] = dedupe_citations((f.get("citations") or []) + verification_citations)
                if f.get("citations"):
                    f["source"] = source_summary_from_citations(f["citations"])
                passed = True
                logger.info(
                    "L3 finding UPGRADED to L2: %s+%s via %s",
                    f.get("drug_a"), f.get("drug_b"), enzyme,
                )
            elif inhibits_ok or substrate_ok:
                # Partial confirmation — keep as L3 with reduced confidence
                f["confidence"] = round(f["confidence"] * 0.8, 3)
                f["_partial_verification"] = True
                passed = True
                logger.info(
                    "L3 finding PARTIALLY verified: %s+%s via %s",
                    f.get("drug_a"), f.get("drug_b"), enzyme,
                )
            else:
                # Neither drug confirmed — hallucination risk, remove
                f["_removal_reason"] = (
                    f"Neo4j does not confirm {f.get('drug_a')} inhibits {enzyme} "
                    f"and {f.get('drug_b')} is substrate of {enzyme}"
                )
                removed.append(f)
                logger.warning(
                    "L3 finding REMOVED (unverified): %s+%s via %s",
                    f.get("drug_a"), f.get("drug_b"), enzyme,
                )
                continue
        else:
            # No enzyme — verify at least that both drug classes exist in graph
            class_a = _neo4j_get_drug_class(drug_a)
            class_b = _neo4j_get_drug_class(drug_b)
            if class_a or class_b:
                passed = True
                logger.info(
                    "L3 class-verified: %s(%s) + %s(%s)",
                    f.get("drug_a"), class_a, f.get("drug_b"), class_b,
                )
            else:
                f["_removal_reason"] = "Neither drug found in graph — possible hallucination"
                removed.append(f)
                logger.warning("L3 finding REMOVED (no graph evidence): %s+%s",
                               f.get("drug_a"), f.get("drug_b"))
                continue

        if passed:
            f["is_ai_assessed"] = True
            if not f.get("source") and f.get("citations"):
                f["source"] = source_summary_from_citations(f["citations"])
            verified.append(f)

    # Deduplicate: if deep_analysis confirmed something already in l1/l2, skip
    seen_pairs: set[tuple[str, str]] = set()
    for f in l1 + l2:
        da = (f.get("drug_a") or "").lower()
        db = (f.get("drug_b") or "").lower()
        if da and db:
            seen_pairs.add((min(da, db), max(da, db)))

    final_verified: list[dict] = []
    for f in verified:
        da = (f.get("drug_a") or "").lower()
        db = (f.get("drug_b") or "").lower()
        pair = (min(da, db), max(da, db)) if da and db else None
        if pair and pair in seen_pairs and f.get("is_ai_assessed"):
            # This was already found by graph — don't double-count
            continue
        if pair:
            seen_pairs.add(pair)
        f["display_severity"] = _display_severity_for_finding(f)
        final_verified.append(f)

    # Sort findings: critical/major first
    final_verified.sort(key=_finding_priority, reverse=True)

    # ── Three-tier herb classification ────────────────────────────────────
    herb_safety: list[dict] = []
    for rh in resolved_herbs:
        raw_name = rh.get("raw_input") or rh.get("name") or ""
        in_db = bool(rh.get("herb_in_database"))
        has_ixn = bool(rh.get("has_interaction_data"))

        if not in_db:
            classification = "not_in_database"
            note = (
                "This herb is NOT in the SAHAYAK database. "
                "Safety cannot be assessed. CONSULT YOUR DOCTOR before use."
            )
        elif has_ixn:
            classification = "studied_interactions_present"
            note = (
                "Herb is in the database with known drug interaction data. "
                "Review herb-drug interaction findings above."
            )
        else:
            classification = "insufficient_data"
            note = (
                "Herb is in the database but no interaction edges available. "
                "Absence of data does NOT mean safe. CONSULT YOUR DOCTOR."
            )

        herb_safety.append({
            "name": raw_name,
            "resolved_name": rh.get("name") if rh.get("found") else None,
            "in_database": in_db,
            "classification": classification,
            "classification_note": note,
            "confidence": rh.get("confidence", 0.0),
        })

    # ── Summary counts ────────────────────────────────────────────────────
    severity_counts = {severity: 0 for severity in _DISPLAY_SEVERITY_ORDER}
    for finding in final_verified:
        display_severity = str(finding.get("display_severity") or "doctor_review")
        if display_severity in severity_counts:
            severity_counts[display_severity] += 1
    l3_count = sum(1 for f in final_verified if f.get("source_layer") == "L3_llm_assisted")

    # ── Build final report ────────────────────────────────────────────────
    import datetime
    final_report = {
        "summary": {
            "total_findings": len(final_verified),
            "critical_count": severity_counts["critical"],
            "major_count": severity_counts["major"],
            "moderate_count": severity_counts["moderate"],
            "minor_count": severity_counts["minor"],
            "doctor_review_count": severity_counts["doctor_review"],
            "beers_flags": len(beers),
            "acb_score": acb.get("total_score", 0),
            "acb_risk_level": acb.get("risk_level", "unknown"),
            "duplications": len(dups),
            "herb_drug_interactions": len(herb),
            "ai_assessed_findings": l3_count,
            "removed_findings": len(removed),
            "completeness_score": round(completeness_score, 2),
            "unresolved_drugs": unresolved_drugs,
            "patient_age": patient_data.get("age"),
            "conditions": patient_data.get("conditions"),
        },
        "findings": final_verified,
        "herb_drug_interactions": herb,
        "herb_safety": herb_safety,
        "beers_flags": beers,
        "acb": acb,
        "duplications": dups,
        "side_effects": side_fx,
        "removed_ai_findings": removed,
        "metadata": {
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "pipeline_nodes_executed": [
                "intake_and_resolve",
                "graph_safety_checks",
                "evaluate_completeness",
                *( ["deep_analysis"] if state.get("deep_findings") is not None else []),
                "verify_and_compile",
            ],
            "llm_used": bool(_get_llm()),
            "completeness_score": round(completeness_score, 2),
            "graph_findings_l1": len(l1),
            "graph_findings_l2": len(l2),
            "l3_candidates": len(deep),
            "l3_verified": l3_count,
            "l3_removed": len(removed),
            "ai_assessment_note": (
                "Findings marked is_ai_assessed=True were generated by LLM reasoning "
                "and verified against the knowledge graph. They are labeled "
                "'AI-assessed, not from curated database' for clinical review."
            ),
        },
    }

    logger.info(
        "verify_and_compile done: %d verified, %d removed, %d AI-assessed",
        len(final_verified), len(removed), l3_count,
    )

    return {
        "verified_findings": final_verified,
        "removed_findings": removed,
        "final_report": final_report,
    }


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_safety_graph() -> Any:
    """Compile and return the LangGraph StateGraph."""
    builder = StateGraph(SafetyState)

    builder.add_node("intake_and_resolve", intake_and_resolve_node)
    builder.add_node("graph_safety_checks", graph_safety_checks_node)
    builder.add_node("evaluate_completeness", evaluate_completeness_node)
    builder.add_node("deep_analysis", deep_analysis_node)
    builder.add_node("verify_and_compile", verify_and_compile_node)

    builder.add_edge(START, "intake_and_resolve")
    builder.add_edge("intake_and_resolve", "graph_safety_checks")
    builder.add_edge("graph_safety_checks", "evaluate_completeness")
    builder.add_conditional_edges(
        "evaluate_completeness",
        _route_after_evaluation,
        {
            "deep_analysis": "deep_analysis",
            "verify_and_compile": "verify_and_compile",
        },
    )
    builder.add_edge("deep_analysis", "verify_and_compile")
    builder.add_edge("verify_and_compile", END)

    return builder.compile()


# ── Main entry point ──────────────────────────────────────────────────────────

async def run_safety_check(patient_data: dict) -> dict:
    """Main entry point. Returns the complete safety report dict.

    Enforces a 30-second hard timeout over the entire pipeline.
    Falls back to a pure graph-only report if the pipeline times out.

    Args:
        patient_data: Dict with keys: drugs, herbs, age, gender,
                      weight_kg, conditions, prescriber_info.

    Returns:
        Complete safety report dict.
    """
    t0 = time.monotonic()
    logger.info("run_safety_check started")

    graph = build_safety_graph()

    async def _run() -> dict:
        result = await graph.ainvoke({"patient_data": patient_data})
        return result.get("final_report") or {}

    try:
        report = await asyncio.wait_for(_run(), timeout=_PIPELINE_TIMEOUT_S)
        elapsed = time.monotonic() - t0
        logger.info("Pipeline completed in %.1fs", elapsed)
        return report

    except asyncio.TimeoutError:
        elapsed = time.monotonic() - t0
        logger.warning(
            "Pipeline timed out after %.1fs — falling back to graph-only report",
            elapsed,
        )
        # Synchronous fallback: run query_engine directly
        from app.graph.query_engine import get_comprehensive_safety_report
        import dataclasses as dc
        fallback = get_comprehensive_safety_report(patient_data)
        report_dict = dc.asdict(fallback)
        report_dict["metadata"]["pipeline_note"] = (
            "TIMEOUT: Agentic pipeline exceeded 30s. "
            "Returning graph-only results without LLM evaluation."
        )
        return report_dict

    except Exception as exc:
        logger.error("Pipeline failed with unexpected error: %s", exc, exc_info=True)
        # Minimal safe fallback
        from app.graph.query_engine import get_comprehensive_safety_report
        import dataclasses as dc
        fallback = get_comprehensive_safety_report(patient_data)
        report_dict = dc.asdict(fallback)
        report_dict["metadata"]["pipeline_error"] = str(exc)
        return report_dict
