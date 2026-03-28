"""Evaluation of graph grounding and report-level hallucination control."""

from __future__ import annotations

import pytest

from tests.eval._helpers import (
    evaluate_section_status,
    latency_stats_ms,
    metric_gate,
    neo4j_available,
    round_metric,
    safe_div,
    timed_async_call_ms,
    verify_report_findings,
)

TARGETS = {
    "grounded_finding_pass_rate": 0.95,
    "relation_hallucination_rate": 0.05,
}
SCENARIOS = [
    {
        "label": "Elderly anticoagulant case",
        "patient": {
            "drugs": ["warfarin", "aspirin", "digoxin"],
            "herbs": ["garlic"],
            "age": 78,
            "gender": "male",
            "conditions": ["atrial fibrillation", "hypertension"],
            "systolic_bp": 104,
            "diastolic_bp": 66,
            "fasting_blood_sugar": 122,
            "postprandial_blood_sugar": 186,
            "spo2": 97,
            "heart_rate": 62,
            "serum_creatinine": 1.3,
            "weight_kg": 68,
        },
    },
    {
        "label": "CYP inhibition case",
        "patient": {
            "drugs": ["clarithromycin", "simvastatin", "amiodarone"],
            "herbs": ["turmeric"],
            "age": 72,
            "gender": "female",
            "conditions": ["dyslipidemia", "arrhythmia"],
            "systolic_bp": 118,
            "diastolic_bp": 72,
            "fasting_blood_sugar": 108,
            "postprandial_blood_sugar": 142,
            "spo2": 98,
            "heart_rate": 74,
            "serum_creatinine": 0.9,
            "weight_kg": 61,
        },
    },
]


def evaluate_rag_grounding(limit: int | None = None) -> dict:
    if not neo4j_available():
        return {
            "section": "rag_grounding",
            "status": "pending-data",
            "metrics": {},
            "targets": TARGETS,
            "notes": ["Neo4j is not reachable; grounding evaluation requires the live graph."],
            "samples": [],
        }

    try:
        from app.services.agentic_safety_checker import run_safety_check
        pipeline_note = None
    except ModuleNotFoundError as exc:
        from app.graph.query_engine import get_comprehensive_safety_report
        from tests.eval._helpers import dataclass_to_dict

        async def run_safety_check(patient_data: dict) -> dict:
            return dataclass_to_dict(get_comprehensive_safety_report(patient_data))

        pipeline_note = f"Agentic dependency missing; grounding run fell back to direct graph orchestration: {exc}"
    from app.services.report_generator import generate_report

    scenarios = SCENARIOS[:limit] if limit else SCENARIOS
    supported = 0
    unsupported = 0
    scored_findings = 0
    pipeline_latencies: list[float] = []
    report_latencies: list[float] = []
    completeness_scores: list[float] = []
    l3_verified_total = 0
    samples: list[dict] = []

    for scenario in scenarios:
        safety_report, pipeline_ms = timed_async_call_ms(run_safety_check, scenario["patient"])
        report_payload, report_ms = timed_async_call_ms(generate_report, safety_report, scenario["patient"], "en-IN")
        pipeline_latencies.append(pipeline_ms)
        report_latencies.append(report_ms)

        grounding = verify_report_findings(report_payload)
        supported += grounding["supported_findings"]
        unsupported += grounding["unsupported_findings"]
        scored_findings += grounding["total_scored_findings"]

        metadata = safety_report.get("metadata", {}) if isinstance(safety_report, dict) else {}
        completeness = metadata.get("completeness_score")
        if completeness is not None:
            completeness_scores.append(float(completeness))
        l3_verified_total += int(metadata.get("l3_verified", 0) or 0)
        samples.append(
            {
                "scenario": scenario["label"],
                "supported_findings": grounding["supported_findings"],
                "unsupported_findings": grounding["unsupported_findings"],
                "pipeline_note": metadata.get("pipeline_note") or pipeline_note,
                "l3_verified": metadata.get("l3_verified"),
            }
        )

    grounded_pass_rate = safe_div(supported, scored_findings)
    hallucination = safe_div(unsupported, scored_findings)
    metrics = {
        "scenarios_evaluated": len(scenarios),
        "grounded_finding_pass_rate": round_metric(grounded_pass_rate),
        "relation_hallucination_rate": round_metric(hallucination),
        "faithfulness_proxy": round_metric(1 - hallucination) if hallucination is not None else None,
        "agentic_pipeline_latency": latency_stats_ms(pipeline_latencies),
        "report_generation_latency": latency_stats_ms(report_latencies),
        "average_completeness_score": round_metric(sum(completeness_scores) / len(completeness_scores))
        if completeness_scores
        else None,
        "ai_rescue_value": l3_verified_total,
    }
    checks = [
        metric_gate(metrics["grounded_finding_pass_rate"], TARGETS["grounded_finding_pass_rate"]),
        metric_gate(
            metrics["relation_hallucination_rate"],
            TARGETS["relation_hallucination_rate"],
            higher_is_better=False,
        ),
    ]
    return {
        "section": "rag_grounding",
        "status": evaluate_section_status(checks),
        "metrics": metrics,
        "targets": TARGETS,
        "notes": [
            "Grounding is checked finding-by-finding against the live graph and query engine, not just by trusting the LLM response format.",
            *( [pipeline_note] if pipeline_note else [] ),
        ],
        "samples": samples,
    }


def test_rag_grounding_evaluator_runs() -> None:
    result = evaluate_rag_grounding(limit=1)
    assert result["section"] == "rag_grounding"
    if result["status"] == "pending-data":
        pytest.skip(result["notes"][0])
    assert result["metrics"]["scenarios_evaluated"] == 1
