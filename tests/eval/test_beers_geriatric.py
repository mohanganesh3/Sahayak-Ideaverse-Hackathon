"""Evaluation of Beers coverage, anticholinergic burden, and duplication checks."""

from __future__ import annotations

import pytest

from tests.eval._helpers import (
    dataclass_to_dict,
    evaluate_section_status,
    metric_gate,
    neo4j_available,
    round_metric,
    safe_div,
)

TARGETS = {
    "beers_coverage": 0.95,
    "acb_sample_accuracy": 0.80,
    "duplication_precision": 0.80,
}
BEERS_POSITIVE_DRUGS = [
    "diphenhydramine",
    "hydroxyzine",
    "amitriptyline",
    "diazepam",
    "chlordiazepoxide",
    "alprazolam",
    "glimepiride",
    "nifedipine",
    "doxazosin",
    "methyldopa",
    "megestrol",
    "nitrofurantoin",
    "meperidine",
    "glyburide",
    "metoclopramide",
]
ACB_CASES = [
    (["diphenhydramine"], 3),
    (["hydroxyzine"], 3),
    (["amitriptyline"], 3),
    (["metformin"], 0),
    (["diphenhydramine", "amitriptyline"], 6),
]
DUPLICATION_POSITIVE_CASES = [
    ["ibuprofen", "diclofenac"],
    ["atorvastatin", "rosuvastatin"],
]
DUPLICATION_NEGATIVE_CASES = [
    ["metformin", "amlodipine"],
    ["warfarin", "levothyroxine"],
]
RENAL_CASES = [
    (["nitrofurantoin"], ["kidney disease"]),
    (["dabigatran"], ["kidney disease"]),
    (["spironolactone"], ["kidney disease"]),
]


def evaluate_beers_geriatric(limit: int | None = None) -> dict:
    if not neo4j_available():
        return {
            "section": "beers_geriatric",
            "status": "pending-data",
            "metrics": {},
            "targets": TARGETS,
            "notes": ["Neo4j is not reachable; geriatric evaluation requires the live graph."],
            "samples": [],
        }

    from app.graph.query_engine import (
        calculate_anticholinergic_burden,
        check_beers_criteria,
        check_therapeutic_duplication,
    )

    positives = BEERS_POSITIVE_DRUGS[:limit] if limit else BEERS_POSITIVE_DRUGS
    beers_hits = 0
    beers_samples: list[dict] = []
    for drug in positives:
        flags = [dataclass_to_dict(flag) for flag in check_beers_criteria([drug], 75, ["hypertension"])]
        flagged = bool(flags)
        if flagged:
            beers_hits += 1
        beers_samples.append(
            {
                "drug": drug,
                "flagged": flagged,
                "categories": [flag.get("category") for flag in flags],
                "flag_types": [flag.get("flag_type") for flag in flags],
            }
        )

    acb_hits = 0
    acb_samples: list[dict] = []
    for drugs, expected_total in ACB_CASES:
        result = dataclass_to_dict(calculate_anticholinergic_burden(drugs))
        observed = int(result.get("total_score", 0))
        if observed == expected_total:
            acb_hits += 1
        acb_samples.append(
            {
                "drugs": drugs,
                "expected_total": expected_total,
                "observed_total": observed,
                "risk_level": result.get("risk_level"),
            }
        )

    duplication_tp = 0
    duplication_fp = 0
    for drugs in DUPLICATION_POSITIVE_CASES:
        if check_therapeutic_duplication(drugs):
            duplication_tp += 1
    for drugs in DUPLICATION_NEGATIVE_CASES:
        if check_therapeutic_duplication(drugs):
            duplication_fp += 1

    renal_hits = 0
    renal_samples: list[dict] = []
    for drugs, conditions in RENAL_CASES:
        flags = [dataclass_to_dict(flag) for flag in check_beers_criteria(drugs, 75, conditions)]
        renal_flagged = any(flag.get("flag_type") == "renal_adjust" for flag in flags)
        if renal_flagged:
            renal_hits += 1
        renal_samples.append({"drugs": drugs, "conditions": conditions, "renal_flagged": renal_flagged})

    metrics = {
        "beers_positive_cases": len(positives),
        "beers_coverage": round_metric(safe_div(beers_hits, len(positives))),
        "beers_miss_rate": round_metric(safe_div(len(positives) - beers_hits, len(positives))),
        "acb_sample_accuracy": round_metric(safe_div(acb_hits, len(ACB_CASES))),
        "duplication_precision": round_metric(safe_div(duplication_tp, duplication_tp + duplication_fp)),
        "duplication_true_positives": duplication_tp,
        "duplication_false_positives": duplication_fp,
        "renal_warning_case_support": round_metric(safe_div(renal_hits, len(RENAL_CASES))),
    }
    checks = [
        metric_gate(metrics["beers_coverage"], TARGETS["beers_coverage"]),
        metric_gate(metrics["acb_sample_accuracy"], TARGETS["acb_sample_accuracy"]),
        metric_gate(metrics["duplication_precision"], TARGETS["duplication_precision"]),
    ]
    return {
        "section": "beers_geriatric",
        "status": evaluate_section_status(checks),
        "metrics": metrics,
        "targets": TARGETS,
        "notes": [
            "Renal-dose support is reported separately because the graph currently has sparse renal metadata compared with Beers and ACB coverage.",
        ],
        "samples": beers_samples[:10] + acb_samples[:5] + renal_samples[:3],
    }


def test_beers_geriatric_evaluator_runs() -> None:
    result = evaluate_beers_geriatric(limit=8)
    assert result["section"] == "beers_geriatric"
    if result["status"] == "pending-data":
        pytest.skip(result["notes"][0])
    assert result["metrics"]["beers_positive_cases"] == 8
