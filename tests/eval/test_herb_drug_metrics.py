"""Evaluation of herb-drug detection, abstention safety, and regional-name rescue."""

from __future__ import annotations

import pytest

from tests.eval._helpers import (
    DATA_DIR,
    dataclass_to_dict,
    evaluate_section_status,
    latency_stats_ms,
    load_json,
    metric_gate,
    neo4j_available,
    round_metric,
    safe_div,
    timed_call_ms,
    best_herb_support,
)

TARGETS = {
    "herb_detection_sensitivity": 0.50,
    "regional_name_rescue_rate": 0.80,
    "dangerous_false_reassurance_rate": 0.05,
}
ABSTENTION_CASES = [
    ("Shankhpushpi", "metformin"),
    ("Punarnava", "warfarin"),
    ("Cardamom", "levothyroxine"),
]


def _sentinel_herb_cases(limit: int | None = None) -> list[dict]:
    cases = [item for item in load_json(DATA_DIR / "sentinel_interactions.json") if item.get("herb") and item.get("drug")]
    return cases[:limit] if limit else cases


def evaluate_herb_drug_metrics(limit: int | None = None) -> dict:
    if not neo4j_available():
        return {
            "section": "herb_drug_metrics",
            "status": "pending-data",
            "metrics": {},
            "targets": TARGETS,
            "notes": ["Neo4j is not reachable; herb-drug evaluation requires the live graph."],
            "samples": [],
        }

    from app.graph.query_engine import get_comprehensive_safety_report, resolve_herb_name

    cases = _sentinel_herb_cases(limit)
    latencies_ms: list[float] = []
    found = 0
    exact_match = 0
    samples: list[dict] = []

    for case in cases:
        support, latency_ms = timed_call_ms(best_herb_support, case["herb"], case["drug"])
        latencies_ms.append(latency_ms)
        if support["supported"]:
            found += 1
        if support["supported"] and support["severity"] == case["expected_severity"]:
            exact_match += 1
        if len(samples) < 10:
            samples.append(
                {
                    "pair": f'{case["herb"]} -> {case["drug"]}',
                    "expected_severity": case["expected_severity"],
                    "found": support["supported"],
                    "actual_severity": support["severity"],
                    "source": support["source"],
                }
            )

    herb_entries = load_json(DATA_DIR / "ayurvedic_herbs.json")
    regional_fields = ("hindi_name", "tamil_name", "telugu_name", "kannada_name")
    regional_total = 0
    regional_hits = 0
    for herb in herb_entries:
        for field in regional_fields:
            value = herb.get(field)
            if not value:
                continue
            regional_total += 1
            resolved = resolve_herb_name(value)
            if resolved.found:
                regional_hits += 1

    abstention_safe = 0
    false_reassurance = 0
    abstention_samples: list[dict] = []
    for herb_name, drug_name in ABSTENTION_CASES:
        report = dataclass_to_dict(
            get_comprehensive_safety_report(
                {
                    "drugs": [drug_name],
                    "herbs": [herb_name],
                    "age": 72,
                    "conditions": ["diabetes"],
                }
            )
        )
        classification = (report.get("unresolved_herbs") or [{}])[0].get("classification")
        if classification in {"insufficient_data", "not_in_database", "studied_interactions_present"}:
            abstention_safe += 1
        if classification == "safe":
            false_reassurance += 1
        abstention_samples.append(
            {"herb": herb_name, "drug": drug_name, "classification": classification}
        )

    metrics = {
        "sentinel_herb_cases": len(cases),
        "herb_detection_sensitivity": round_metric(safe_div(found, len(cases))),
        "herb_severity_exact_match": round_metric(safe_div(exact_match, len(cases))),
        "regional_name_rescue_rate": round_metric(safe_div(regional_hits, regional_total)),
        "abstention_correctness": round_metric(safe_div(abstention_safe, len(ABSTENTION_CASES))),
        "dangerous_false_reassurance_rate": round_metric(safe_div(false_reassurance, len(ABSTENTION_CASES))),
        "latency": latency_stats_ms(latencies_ms),
    }
    checks = [
        metric_gate(metrics["herb_detection_sensitivity"], TARGETS["herb_detection_sensitivity"]),
        metric_gate(metrics["regional_name_rescue_rate"], TARGETS["regional_name_rescue_rate"]),
        metric_gate(
            metrics["dangerous_false_reassurance_rate"],
            TARGETS["dangerous_false_reassurance_rate"],
            higher_is_better=False,
        ),
    ]
    samples.extend(abstention_samples[:3])
    return {
        "section": "herb_drug_metrics",
        "status": evaluate_section_status(checks),
        "metrics": metrics,
        "targets": TARGETS,
        "notes": [
            "Abstention safety is scored explicitly: the system never gets credit for saying an unstudied herb is safe.",
        ],
        "samples": samples,
    }


def test_herb_drug_metrics_evaluator_runs() -> None:
    result = evaluate_herb_drug_metrics(limit=2)
    assert result["section"] == "herb_drug_metrics"
    if result["status"] == "pending-data":
        pytest.skip(result["notes"][0])
    assert result["metrics"]["sentinel_herb_cases"] == 2
