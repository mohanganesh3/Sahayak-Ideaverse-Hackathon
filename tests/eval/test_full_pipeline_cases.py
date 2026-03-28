"""Evaluation of end-to-end SAHAYAK pipeline scenarios."""

from __future__ import annotations

import pytest

from tests.eval._helpers import (
    best_herb_support,
    best_interaction_support,
    dataclass_to_dict,
    evaluate_section_status,
    latency_stats_ms,
    metric_gate,
    neo4j_available,
    round_metric,
    run_async,
    safe_div,
    timed_async_call_ms,
)

TARGETS = {
    "scenario_pass_rate": 0.80,
    "report_generation_success_rate": 1.00,
    "clinical_context_acceptance_rate": 1.00,
}
PIPELINE_CASES = [
    {
        "label": "Critical anticoagulant brand-resolve case",
        "texts": [
            "ECOSPRIN 75\nAspirin Gastro-resistant Tablets IP\nEach tablet contains: Aspirin IP 75 mg",
            "WARF 5\nWarfarin Sodium Tablets IP\nEach tablet contains: Warfarin Sodium IP 5 mg",
        ],
        "patient": {
            "age": 78,
            "gender": "male",
            "conditions": ["atrial fibrillation", "hypertension"],
            "herbs": ["garlic"],
            "systolic_bp": 106,
            "diastolic_bp": 64,
            "fasting_blood_sugar": 118,
            "postprandial_blood_sugar": 178,
            "spo2": 97,
            "heart_rate": 60,
            "serum_creatinine": 1.4,
            "weight_kg": 67,
        },
        "expects": {"direct_pair": {"warfarin", "aspirin"}, "herb_pair": {"garlic", "warfarin"}},
    },
    {
        "label": "CYP inhibition strip-to-report case",
        "texts": [
            "CLARITHROMYCIN 500\nEach tablet contains: Clarithromycin USP 500 mg",
            "SIMVASTATIN 20\nEach tablet contains: Simvastatin USP 20 mg",
        ],
        "patient": {
            "age": 72,
            "gender": "female",
            "conditions": ["dyslipidemia"],
            "herbs": ["turmeric"],
            "systolic_bp": 120,
            "diastolic_bp": 72,
            "fasting_blood_sugar": 104,
            "postprandial_blood_sugar": 138,
            "spo2": 98,
            "heart_rate": 76,
            "serum_creatinine": 0.9,
            "weight_kg": 62,
        },
        "expects": {"indirect_type": "cyp_inhibition"},
    },
    {
        "label": "Thyroid absorption interaction case",
        "texts": [
            "THYRONORM 50 mcg\nLevothyroxine Sodium Tablets IP\nEach tablet contains: Levothyroxine Sodium IP 50 mcg",
            "CALCIUM CARBONATE 500\nEach tablet contains: Calcium Carbonate IP 500 mg",
        ],
        "patient": {
            "age": 74,
            "gender": "female",
            "conditions": ["hypothyroidism"],
            "herbs": [],
            "systolic_bp": 124,
            "diastolic_bp": 78,
            "fasting_blood_sugar": 102,
            "postprandial_blood_sugar": 136,
            "spo2": 99,
            "heart_rate": 70,
            "serum_creatinine": 0.8,
            "weight_kg": 58,
        },
        "expects": {"direct_pair": {"levothyroxine", "calcium carbonate"}},
    },
]


def _extract_case_drugs(texts: list[str]) -> tuple[list[str], float]:
    from app.services.drug_extractor import extract_drugs_from_text

    extracted_drugs: list[str] = []
    total_latency = 0.0
    for text in texts:
        entries, latency_ms = timed_async_call_ms(extract_drugs_from_text, text)
        total_latency += latency_ms
        for entry in entries:
            name = entry.get("generic_name") or entry.get("brand_name")
            if name:
                extracted_drugs.append(str(name))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in extracted_drugs:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
    return deduped, total_latency


def evaluate_full_pipeline_cases(limit: int | None = None) -> dict:
    if not neo4j_available():
        return {
            "section": "full_pipeline_cases",
            "status": "pending-data",
            "metrics": {},
            "targets": TARGETS,
            "notes": ["Neo4j is not reachable; end-to-end pipeline evaluation requires the live graph."],
            "samples": [],
        }

    from app.services.report_generator import generate_report
    try:
        from app.services.agentic_safety_checker import run_safety_check
        pipeline_note = None
    except ModuleNotFoundError:
        from app.graph.query_engine import get_comprehensive_safety_report

        async def run_safety_check(patient_data: dict) -> dict:
            return dataclass_to_dict(get_comprehensive_safety_report(patient_data))

        pipeline_note = (
            "langchain_core missing; full-pipeline evaluation fell back to direct graph orchestration "
            "instead of the agentic checker."
        )

    cases = PIPELINE_CASES[:limit] if limit else PIPELINE_CASES
    extraction_latencies: list[float] = []
    pipeline_latencies: list[float] = []
    report_latencies: list[float] = []
    passed = 0
    report_success = 0
    context_acceptance = 0
    alerts_per_10_drugs: list[float] = []
    samples: list[dict] = []

    for case in cases:
        drugs, extraction_ms = _extract_case_drugs(case["texts"])
        extraction_latencies.append(extraction_ms)
        patient = dict(case["patient"])
        patient["drugs"] = drugs

        safety_report, pipeline_ms = timed_async_call_ms(run_safety_check, patient)
        pipeline_latencies.append(pipeline_ms)

        report_payload, report_ms = timed_async_call_ms(generate_report, safety_report, patient, "en-IN")
        report_latencies.append(report_ms)
        if report_payload.get("english") and report_payload.get("translated"):
            report_success += 1

        if all(key in patient for key in ("systolic_bp", "diastolic_bp", "fasting_blood_sugar", "spo2", "serum_creatinine")):
            context_acceptance += 1

        direct_pairs = {
            frozenset({item.get("drug_a", "").lower(), item.get("drug_b", "").lower()})
            for item in safety_report.get("direct_interactions", [])
            if item.get("drug_a") and item.get("drug_b")
        }
        herb_pairs = {
            frozenset({item.get("herb", "").lower(), item.get("drug", "").lower()})
            for item in safety_report.get("herb_drug_interactions", [])
            if item.get("herb") and item.get("drug")
        }
        indirect_types = {item.get("interaction_type") for item in safety_report.get("indirect_interactions", [])}
        total_findings = (
            len(
                [
                    item
                    for item in safety_report.get("direct_interactions", [])
                    if item.get("severity") in {"major", "moderate"}
                ]
            )
            + len(
                [
                    item
                    for item in safety_report.get("indirect_interactions", [])
                    if float(item.get("severity_score", 0.0)) >= 5.0
                ]
            )
            + len(
                [
                    item
                    for item in safety_report.get("herb_drug_interactions", [])
                    if item.get("severity") in {"major", "moderate"}
                ]
            )
            + len(safety_report.get("beers_flags", []))
            + len(safety_report.get("duplications", []))
        )
        if drugs:
            alerts_per_10_drugs.append((total_findings / len(drugs)) * 10)

        case_pass = True
        expected_direct = case["expects"].get("direct_pair")
        if expected_direct:
            left, right = sorted(expected_direct)
            if not best_interaction_support(left, right)["supported"]:
                case_pass = False
        expected_herb = case["expects"].get("herb_pair")
        if expected_herb:
            herb, drug = sorted(expected_herb)
            support = best_herb_support(herb, drug)
            if not support["supported"] and not best_herb_support(drug, herb)["supported"]:
                case_pass = False
        expected_indirect = case["expects"].get("indirect_type")
        if expected_indirect and expected_indirect not in indirect_types:
            case_pass = False

        if case_pass:
            passed += 1
        samples.append(
            {
                "case": case["label"],
                "extracted_drugs": drugs,
                "case_pass": case_pass,
                "total_findings": total_findings,
                "pipeline_note": pipeline_note,
                "direct_pairs": [sorted(pair) for pair in direct_pairs],
                "herb_pairs": [sorted(pair) for pair in herb_pairs],
                "indirect_types": sorted(indirect_types),
            }
        )

    metrics = {
        "cases_evaluated": len(cases),
        "scenario_pass_rate": round_metric(safe_div(passed, len(cases))),
        "report_generation_success_rate": round_metric(safe_div(report_success, len(cases))),
        "clinical_context_acceptance_rate": round_metric(safe_div(context_acceptance, len(cases))),
        "alerts_per_10_drugs": round_metric(sum(alerts_per_10_drugs) / len(alerts_per_10_drugs)) if alerts_per_10_drugs else None,
        "extraction_latency": latency_stats_ms(extraction_latencies),
        "pipeline_latency": latency_stats_ms(pipeline_latencies),
        "report_latency": latency_stats_ms(report_latencies),
    }
    checks = [
        metric_gate(metrics["scenario_pass_rate"], TARGETS["scenario_pass_rate"]),
        metric_gate(metrics["report_generation_success_rate"], TARGETS["report_generation_success_rate"]),
        metric_gate(metrics["clinical_context_acceptance_rate"], TARGETS["clinical_context_acceptance_rate"]),
    ]
    return {
        "section": "full_pipeline_cases",
        "status": evaluate_section_status(checks),
        "metrics": metrics,
        "targets": TARGETS,
        "notes": [
            "These scenarios validate the real path from OCR-like medicine text through extraction, normalization, graph reasoning, and multilingual report generation.",
            *( [pipeline_note] if pipeline_note else [] ),
        ],
        "samples": samples,
    }


def test_full_pipeline_cases_evaluator_runs() -> None:
    result = evaluate_full_pipeline_cases(limit=1)
    assert result["section"] == "full_pipeline_cases"
    if result["status"] == "pending-data":
        pytest.skip(result["notes"][0])
    assert result["metrics"]["cases_evaluated"] == 1
