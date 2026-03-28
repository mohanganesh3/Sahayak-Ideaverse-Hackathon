"""Patient-friendly multilingual safety report synthesis."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from openai import OpenAI

from app.config import GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY
from app.services.citation_utils import (
    build_evidence_text,
    dedupe_citations,
    make_citation,
    source_summary_from_citations,
    split_source_keys,
    summarize_evidence_profile,
)
from app.services.translation_service import translate_report

logger = logging.getLogger(__name__)

_OPENAI_MODEL = "gpt-4o-mini"
_GEMINI_MODEL = "gemini-2.0-flash"
_GROQ_MODEL = "llama-3.3-70b-versatile"
_DISPLAY_SEVERITY_ORDER = {"critical": 0, "major": 1, "moderate": 2, "minor": 3, "doctor_review": 4}


def _extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("LLM response did not contain a JSON object")
    return json.loads(text[start : end + 1])


def _openai_client() -> OpenAI | None:
    if not OPENAI_API_KEY:
        return None
    return OpenAI(api_key=OPENAI_API_KEY)


def _gemini_client() -> OpenAI | None:
    if not GEMINI_API_KEY:
        return None
    return OpenAI(
        api_key=GEMINI_API_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )


def _groq_client() -> OpenAI:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")
    return OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")


def _self_prescribed_warning(patient_info: dict[str, Any], findings: list[dict[str, Any]]) -> str | None:
    prescriber_info = patient_info.get("prescriber_info") or {}
    if not isinstance(prescriber_info, dict):
        return None

    risky_names: set[str] = set()
    for finding in findings:
        for med in finding.get("medicines", []) or []:
            risky_names.add(str(med).strip().lower())

    self_named = [
        name for name, source in prescriber_info.items()
        if str(source).strip().lower() in {"self", "medical_shop", "medical shop"}
    ]
    if not self_named:
        return None

    flagged = [name for name in self_named if name.strip().lower() in risky_names]
    if flagged:
        return (
            "Some medicines with safety concerns appear to be self-started or bought without a clear prescription: "
            + ", ".join(flagged)
            + ". Please discuss them with your doctor or pharmacist before continuing."
        )
    return (
        "Some medicines were marked as self-started or bought from a medical shop. "
        "Please review them with your doctor or pharmacist."
    )


def _severity_bucket(item: dict[str, Any]) -> str:
    severity = str(item.get("severity", "unknown")).lower()
    if severity in {"critical", "major", "moderate", "minor", "unknown"}:
        return severity
    if severity in {"high", "severe"}:
        return "critical"
    if severity in {"significant", "considerable"}:
        return "major"
    try:
        score = float(item.get("severity_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score >= 8:
        return "critical"
    if score >= 5:
        return "major"
    if score > 0:
        return "moderate"
    return "moderate"


def _display_severity_bucket(item: dict[str, Any], raw_severity: str) -> str:
    if raw_severity in {"critical", "major", "moderate", "minor"}:
        return raw_severity
    try:
        score = float(item.get("severity_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    if score >= 8:
        return "critical"
    if score >= 5:
        return "major"
    if score > 0:
        return "moderate"
    return "doctor_review"


def _confidence_bucket(value: Any) -> str:
    if isinstance(value, str) and value.lower() in {"high", "medium", "low"}:
        return value.lower()
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = 0.8
    if numeric >= 0.9:
        return "high"
    if numeric >= 0.7:
        return "medium"
    return "low"


def _coerce_citations(
    item: dict[str, Any],
    *,
    relation_type: str,
    evidence_type: str,
    default_source: str,
    default_evidence: str,
    source_layer: str | None = None,
    extras: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    existing = item.get("citations")
    if isinstance(existing, list) and existing:
        return dedupe_citations([dict(c) for c in existing if isinstance(c, dict)])

    layer = source_layer or str(item.get("source_layer") or "L1_direct")
    evidence = (
        build_evidence_text(
            item.get("clinical_effect"),
            item.get("clinical_implication"),
            item.get("pathway"),
            item.get("mechanism"),
            item.get("rationale"),
            item.get("recommendation"),
            default_evidence,
        )
        or default_evidence
    )
    raw_sources = split_source_keys(item.get("source") or default_source)
    if not raw_sources:
        raw_sources = [default_source]
    citations = [
        make_citation(
            source_key=source_key,
            relation_type=relation_type,
            source_layer=layer,
            evidence=evidence,
            evidence_type=evidence_type,
            confidence=item.get("confidence"),
            extras=extras,
        )
        for source_key in raw_sources
    ]
    return dedupe_citations(citations)


def _build_acb_section(safety_report: dict[str, Any]) -> dict[str, Any]:
    acb_source = safety_report.get("acb_section") or safety_report.get("acb_result") or safety_report.get("acb") or {}
    return {
        "score": int(acb_source.get("total_score") or acb_source.get("score") or 0),
        "risk": acb_source.get("clinical_warning") or acb_source.get("risk_level") or "No anticholinergic concern identified.",
        "drugs": [item.get("drug") for item in acb_source.get("contributing_drugs", []) if item.get("drug")],
        "citations": acb_source.get("citations") or [],
    }


def _build_seed_findings(safety_report: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []

    for item in (safety_report.get("findings") or safety_report.get("direct_interactions") or []):
        severity = _severity_bucket(item)
        display_severity = _display_severity_bucket(item, severity)
        medicines = [med for med in [item.get("drug_a"), item.get("drug_b"), item.get("herb")] if med]
        default_evidence = str(item.get("clinical_effect") or item.get("clinical_implication") or item.get("pathway") or "Possible medicine interaction detected.")
        citations = _coerce_citations(
            item,
            relation_type=str(item.get("interaction_type") or "INTERACTS_WITH").upper(),
            evidence_type="interaction_finding",
            default_source=str(item.get("source") or "knowledge_graph"),
            default_evidence=default_evidence,
        )
        evidence_profile, evidence_profile_note = summarize_evidence_profile(citations)
        findings.append(
            {
                "severity": severity,
                "display_severity": display_severity,
                "title": f"{item.get('drug_a', '')} + {item.get('drug_b', '')}".strip(" +"),
                "patient_explanation": default_evidence,
                "doctor_explanation": str(item.get("mechanism") or item.get("pathway") or item.get("clinical_implication") or "Mechanism not clearly documented."),
                "action": str(item.get("management") or "Please discuss this combination with your doctor."),
                "medicines": medicines,
                "confidence": _confidence_bucket(item.get("confidence")),
                "source": source_summary_from_citations(citations) or item.get("source") or item.get("source_layer") or "Knowledge Graph",
                "citations": citations,
                "evidence_profile": evidence_profile,
                "evidence_profile_note": evidence_profile_note,
            }
        )

    for item in (safety_report.get("beers_flags") or []):
        drug = str(item.get("drug") or "Unknown")
        concern = str(item.get("rationale") or item.get("concern") or "Potentially inappropriate for elderly patients.")
        citations = _coerce_citations(
            item,
            relation_type="BEERS_FLAG",
            evidence_type="geriatric_guideline",
            default_source=str(item.get("source") or "beers_2023"),
            default_evidence=concern,
        )
        evidence_profile, evidence_profile_note = summarize_evidence_profile(citations)
        findings.append(
            {
                "severity": "major",
                "display_severity": "major",
                "title": f"{drug} — BEERS Criteria Flag",
                "patient_explanation": f"{drug} may not be safe for you. {concern}",
                "doctor_explanation": f"BEERS criteria flag: {concern}",
                "action": str(item.get("recommendation") or "Discuss with your doctor whether this medicine is appropriate for your age and conditions."),
                "medicines": [drug],
                "confidence": _confidence_bucket(item.get("confidence") or "high"),
                "source": source_summary_from_citations(citations) or item.get("source") or "AGS 2023 Beers Criteria",
                "citations": citations,
                "evidence_profile": evidence_profile,
                "evidence_profile_note": evidence_profile_note,
            }
        )

    for item in (safety_report.get("herb_drug_interactions") or []):
        herb = str(item.get("herb") or "")
        drug = str(item.get("drug") or "")
        effect = str(item.get("clinical_effect") or "Possible herb-drug interaction.")
        severity = _severity_bucket(item)
        citations = _coerce_citations(
            item,
            relation_type="INTERACTS_WITH_DRUG",
            evidence_type="herb_drug_interaction",
            default_source=str(item.get("source") or "curated_ayurveda"),
            default_evidence=effect,
        )
        evidence_profile, evidence_profile_note = summarize_evidence_profile(citations)
        findings.append(
            {
                "severity": severity,
                "display_severity": _display_severity_bucket(item, severity),
                "title": f"{herb} + {drug}".strip(" +"),
                "patient_explanation": effect,
                "doctor_explanation": str(item.get("mechanism") or f"Herb-drug interaction between {herb} and {drug}: {effect}"),
                "action": str(item.get("management") or "Tell your doctor about any herbal medicines or supplements you take."),
                "medicines": [m for m in [herb, drug] if m],
                "confidence": _confidence_bucket(item.get("confidence")),
                "source": source_summary_from_citations(citations) or item.get("source") or "Curated Ayurveda",
                "citations": citations,
                "evidence_profile": evidence_profile,
                "evidence_profile_note": evidence_profile_note,
            }
        )

    for item in (safety_report.get("duplications") or []):
        medicines = [str(med) for med in (item.get("drugs") or []) if med]
        duplication_type = str(item.get("duplication_type") or "")
        citations = _coerce_citations(
            item,
            relation_type="THERAPEUTIC_DUPLICATION",
            evidence_type="duplication_rule",
            default_source="knowledge_graph",
            default_evidence=str(item.get("recommendation") or "Therapeutic duplication detected."),
        )
        evidence_profile, evidence_profile_note = summarize_evidence_profile(citations)
        findings.append(
            {
                "severity": "major" if duplication_type == "same_ingredient" else "moderate",
                "display_severity": "major" if duplication_type == "same_ingredient" else "moderate",
                "title": f"Possible duplicate therapy — {item.get('drug_class') or 'same medicine group'}",
                "patient_explanation": str(item.get("recommendation") or "Possible duplicate therapy detected."),
                "doctor_explanation": str(item.get("recommendation") or "Possible duplicate therapy detected."),
                "action": "Review this combination with your doctor or pharmacist.",
                "medicines": medicines,
                "confidence": _confidence_bucket(item.get("confidence")),
                "source": source_summary_from_citations(citations) or "Knowledge Graph",
                "citations": citations,
                "evidence_profile": evidence_profile,
                "evidence_profile_note": evidence_profile_note,
            }
        )

    findings.sort(key=lambda f: _DISPLAY_SEVERITY_ORDER.get(str(f.get("display_severity") or "doctor_review"), 4))
    trimmed = findings[:15]
    for idx, finding in enumerate(trimmed, start=1):
        finding["finding_id"] = f"finding_{idx}"
    return trimmed


def _public_findings(seed_findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [dict(finding) for finding in seed_findings]


def _fallback_findings(safety_report: dict[str, Any]) -> list[dict[str, Any]]:
    return _public_findings(_build_seed_findings(safety_report))


def _fallback_report(safety_report: dict[str, Any], patient_info: dict[str, Any]) -> dict[str, Any]:
    findings = _fallback_findings(safety_report)
    acb_section = _build_acb_section(safety_report)

    # Build personalized advice based on patient data
    personalized_parts: list[str] = []
    age = patient_info.get("age", 0)
    conditions = patient_info.get("conditions", [])
    prescriber_info = patient_info.get("prescriber_info") or {}

    if age and age >= 65:
        personalized_parts.append(
            f"As a {age}-year-old patient, your body processes medicines differently. "
            "Extra caution is needed with dosages, and some medicines may not be suitable for your age group. "
            "BEERS criteria flags are especially important for you."
        )
    elif age and age > 0:
        personalized_parts.append(
            f"For a {age}-year-old patient, the following safety considerations apply to your medicines."
        )

    if conditions:
        condition_str = ", ".join(conditions)
        personalized_parts.append(
            f"Given your health conditions ({condition_str}), some medicines may need special monitoring. "
            "Please ensure your doctor is aware of all your conditions when reviewing these medicines."
        )

    # Check for self-prescribed medicines
    self_meds = [name for name, source in prescriber_info.items()
                 if str(source).strip().lower() in {"self", "medical_shop", "medical shop"}]
    if self_meds:
        personalized_parts.append(
            f"You indicated that {', '.join(self_meds)} {'was' if len(self_meds) == 1 else 'were'} "
            "not prescribed by a doctor. Self-medication can be risky, especially with drug interactions. "
            "Please consult a doctor about these medicines."
        )

    # Vitals-based advice
    systolic = patient_info.get("systolic_bp")
    fbs = patient_info.get("fasting_blood_sugar")
    creatinine = patient_info.get("serum_creatinine")

    if systolic and systolic > 140:
        personalized_parts.append(
            f"Your blood pressure ({systolic}/{patient_info.get('diastolic_bp', '?')} mmHg) is elevated. "
            "Medicines that affect blood pressure should be monitored closely."
        )
    if fbs and fbs > 126:
        personalized_parts.append(
            f"Your fasting blood sugar ({fbs} mg/dL) suggests diabetes management is important. "
            "Watch for medicines that may affect blood glucose levels."
        )
    if creatinine and creatinine > 1.3:
        personalized_parts.append(
            f"Your serum creatinine ({creatinine} mg/dL) may indicate reduced kidney function. "
            "Some medicines may need dose adjustment based on kidney function."
        )

    personalized_advice = " ".join(personalized_parts) if personalized_parts else None

    report = {
        "patient_summary": (
            "We reviewed your medicines and found some safety points that should be discussed with your doctor. "
            "Each warning below includes the supporting evidence source, which may be a study link, guideline table, dataset record, or curated rule. "
            "Please do not stop any medicine on your own."
        ),
        "findings": findings,
        "acb_section": acb_section,
        "self_prescribed_warning": _self_prescribed_warning(patient_info, findings),
        "personalized_advice": personalized_advice,
        "disclaimer": "This is for information only. Consult your doctor.",
    }
    return report


def _prepare_prompt_payload(safety_report: dict[str, Any], patient_info: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "patient_info": patient_info,
        "summary": safety_report.get("summary", {}),
        "findings": _build_seed_findings(safety_report),
        "acb_section": _build_acb_section(safety_report),
        "metadata": safety_report.get("metadata", {}),
    }
    return payload


_REPORT_SYSTEM_PROMPT = (
    "You are a caring pharmacist explaining medication safety to an elderly patient and their caregiver. "
    "Write in simple, warm language. Be specific — name actual medicines. "
    "Never recommend stopping medication — always say 'discuss with your doctor'. "
    "If uncertain, say so clearly.\n\n"
    "CRITICAL IMMUTABLE FIELDS:\n"
    "1. NEVER remove findings.\n"
    "2. NEVER change finding_id, severity, display_severity, title, medicines, confidence, source, or citations.\n"
    "3. Citations are evidence provenance and must stay attached to the same finding_id.\n"
    "4. You may only rewrite patient_explanation, doctor_explanation, action, patient_summary, acb_section.risk, and personalized_advice.\n\n"
    "5. NEVER imply that every finding has a paper-level citation. Evidence may be a study link, guideline table, dataset record, or curated rule.\n\n"
    "IMPORTANT — PERSONALIZATION RULES:\n"
    "1. If patient_info contains 'age', tailor all advice to that age group. "
    "For patients aged 65+, emphasize fall risk, kidney function, BEERS criteria concerns, "
    "and the importance of regular medication reviews.\n"
    "2. If patient_info contains 'conditions', relate each finding to how it might affect those specific conditions. "
    "For example, if the patient has diabetes and a medicine may raise blood sugar, highlight this specifically.\n"
    "3. If patient_info contains 'prescriber_info', flag medicines marked as 'self' or 'medical_shop' "
    "more strongly than doctor-prescribed ones. Self-started medicines with interactions are a higher priority concern.\n"
    "4. If vitals are provided (blood pressure, blood sugar, creatinine, SpO2, heart rate), "
    "include specific notes if any medicine might worsen those values.\n"
    "5. The 'personalized_advice' field MUST contain a 2-4 sentence summary that directly references "
    "the patient's age, conditions, and prescriber sources to give them actionable, relevant guidance.\n\n"
    "Return JSON:\n"
    "{\n"
    '  "patient_summary": "2-3 sentence overview referencing patient age and conditions if available",\n'
    '  "findings": [\n'
    "    {\n"
    '      "finding_id": "must match input exactly",\n'
    '      "patient_explanation": "simple 2-3 sentence explanation personalized to patient profile",\n'
    '      "doctor_explanation": "technical with mechanism",\n'
    '      "action": "what to do"\n'
    "    }\n"
    "  ],\n"
    '  "acb_section": {"risk": "text personalized to age"},\n'
    '  "personalized_advice": "2-4 sentence advice specific to THIS patient based on their age, conditions, self-prescribed meds, and vitals",\n'
    '  "self_prescribed_warning": "text or null",\n'
    '  "disclaimer": "This is for information only. Consult your doctor."\n'
    "}"
)


def _build_report_messages(payload: dict[str, Any]) -> list[dict[str, str]]:
    patient_info = payload.get("patient_info", {})
    personalization_context = ""

    # Build explicit personalization hints for the LLM
    age = patient_info.get("age")
    conditions = patient_info.get("conditions", [])
    prescriber_info = patient_info.get("prescriber_info", {})

    if age or conditions or prescriber_info:
        parts = []
        if age:
            parts.append(f"Patient is {age} years old.")
        if conditions:
            parts.append(f"Patient has these conditions: {', '.join(conditions)}.")
        if prescriber_info:
            self_meds = [k for k, v in prescriber_info.items() if str(v).lower() in ("self", "medical_shop")]
            doctor_meds = [k for k, v in prescriber_info.items() if str(v).lower() == "doctor"]
            if self_meds:
                parts.append(f"Self-prescribed/pharmacy medicines: {', '.join(self_meds)} — flag these with extra concern.")
            if doctor_meds:
                parts.append(f"Doctor-prescribed medicines: {', '.join(doctor_meds)}.")

        vitals_parts = []
        for vk, vl in [("systolic_bp", "Systolic BP"), ("diastolic_bp", "Diastolic BP"),
                       ("fasting_blood_sugar", "Fasting Blood Sugar"), ("spo2", "SpO2"),
                       ("heart_rate", "Heart Rate"), ("serum_creatinine", "Serum Creatinine")]:
            val = patient_info.get(vk)
            if val is not None:
                vitals_parts.append(f"{vl}: {val}")
        if vitals_parts:
            parts.append(f"Patient vitals: {'; '.join(vitals_parts)}.")

        personalization_context = (
            "\n\nCRITICAL — USE THIS PATIENT PROFILE FOR PERSONALIZATION:\n"
            + " ".join(parts)
            + "\nYou MUST reference these details in patient_summary, findings, and personalized_advice."
        )

    return [
        {"role": "system", "content": _REPORT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                "Rewrite the precomputed medication safety findings for patient readability while preserving provenance. "
                "Every finding already has severity, source, and citations. Keep those immutable and only improve the explanations."
                + personalization_context
                + f"\n\n{json.dumps(payload, ensure_ascii=False)}"
            ),
        },
    ]


def _merge_llm_report(
    payload: dict[str, Any],
    llm_data: dict[str, Any],
    patient_info: dict[str, Any],
) -> dict[str, Any]:
    seed_findings = payload.get("findings") or []
    llm_findings = llm_data.get("findings") if isinstance(llm_data.get("findings"), list) else []
    llm_by_id = {
        str(item.get("finding_id")): item
        for item in llm_findings
        if isinstance(item, dict) and item.get("finding_id")
    }

    merged_findings: list[dict[str, Any]] = []
    for seed in seed_findings:
        merged = dict(seed)
        generated = llm_by_id.get(str(seed.get("finding_id")), {})
        for field in ("patient_explanation", "doctor_explanation", "action"):
            value = generated.get(field)
            if isinstance(value, str) and value.strip():
                merged[field] = value.strip()
        merged_findings.append(merged)

    acb_seed = payload.get("acb_section") or {}
    acb_llm = llm_data.get("acb_section") if isinstance(llm_data.get("acb_section"), dict) else {}
    report = {
        "patient_summary": (
            llm_data.get("patient_summary")
            if isinstance(llm_data.get("patient_summary"), str) and llm_data.get("patient_summary", "").strip()
            else "We reviewed your medicines and found safety points that should be discussed with your doctor. Each warning below includes the supporting evidence source, which may be a study link, guideline table, dataset record, or curated rule."
        ),
        "findings": merged_findings,
        "acb_section": {
            "score": acb_seed.get("score", 0),
            "risk": (
                acb_llm.get("risk")
                if isinstance(acb_llm.get("risk"), str) and acb_llm.get("risk", "").strip()
                else acb_seed.get("risk", "No anticholinergic concern identified.")
            ),
            "drugs": acb_seed.get("drugs", []),
            "citations": acb_seed.get("citations", []),
        },
        "personalized_advice": llm_data.get("personalized_advice"),
        "self_prescribed_warning": llm_data.get("self_prescribed_warning"),
        "disclaimer": (
            llm_data.get("disclaimer")
            if isinstance(llm_data.get("disclaimer"), str) and llm_data.get("disclaimer", "").strip()
            else "This is for information only. Consult your doctor."
        ),
    }
    report["self_prescribed_warning"] = report.get("self_prescribed_warning") or _self_prescribed_warning(
        patient_info,
        report["findings"],
    )
    return report


def _llm_generate_sync(safety_report: dict[str, Any], patient_info: dict[str, Any]) -> dict[str, Any]:
    payload = _prepare_prompt_payload(safety_report, patient_info)
    messages = _build_report_messages(payload)

    # Try OpenAI GPT-4o-mini first
    openai_client = _openai_client()
    if openai_client:
        try:
            response = openai_client.chat.completions.create(
                model=_OPENAI_MODEL,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=messages,
            )
            data = _extract_json_object(response.choices[0].message.content or "{}")
            logger.info("Report generation via OpenAI GPT-4o-mini succeeded")
            return _merge_llm_report(payload, data, patient_info)
        except Exception as exc:
            logger.warning("OpenAI report generation failed (%s), trying Gemini", exc)

    # Fallback to Gemini
    gemini_client = _gemini_client()
    if gemini_client:
        try:
            response = gemini_client.chat.completions.create(
                model=_GEMINI_MODEL,
                temperature=0.2,
                response_format={"type": "json_object"},
                messages=messages,
            )
            data = _extract_json_object(response.choices[0].message.content or "{}")
            logger.info("Report generation via Gemini Flash succeeded")
            return _merge_llm_report(payload, data, patient_info)
        except Exception as exc:
            logger.warning("Gemini report generation failed (%s), trying Groq", exc)

    # Fallback to Groq
    client = _groq_client()
    response = client.chat.completions.create(
        model=_GROQ_MODEL,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=messages,
    )
    data = _extract_json_object(response.choices[0].message.content or "{}")
    logger.info("Report generation via Groq fallback succeeded")
    return _merge_llm_report(payload, data, patient_info)


async def generate_report(
    safety_report: dict[str, Any],
    patient_info: dict[str, Any],
    language: str = "en-IN",
) -> dict[str, Any]:
    """Generate English and translated patient-friendly reports."""
    try:
        english = await asyncio.to_thread(_llm_generate_sync, safety_report, patient_info)
    except Exception as exc:
        logger.warning("Report generation fell back to deterministic template: %s", exc)
        english = _fallback_report(safety_report, patient_info)

    english["self_prescribed_warning"] = english.get("self_prescribed_warning") or _self_prescribed_warning(
        patient_info,
        english.get("findings", []),
    )
    english.setdefault("disclaimer", "This is for information only. Consult your doctor.")

    translated = translate_report(english, language)
    return {
        "language": language,
        "english": english,
        "translated": translated,
    }
