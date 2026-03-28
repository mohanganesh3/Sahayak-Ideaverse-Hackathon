"""Evaluation of direct DDI detection and severity performance."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval._helpers import (
    DATA_DIR,
    binary_metrics,
    evaluate_section_status,
    latency_stats_ms,
    load_json,
    metric_gate,
    neo4j_available,
    normalize_severity,
    quadratic_weighted_kappa,
    round_metric,
    run_cypher,
    safe_div,
    timed_call_ms,
    best_interaction_support,
)

SENTINEL_PATH = DATA_DIR / "sentinel_interactions.json"
TARGETS = {
    "sensitivity": 0.95,
    "severity_exact_match": 0.85,
    "weighted_kappa": 0.60,
}
SYNONYM_CASES = [
    {"raw": "Acetylsalicylic acid", "expected": "aspirin"},
    {"raw": "Paracetamol", "expected": "acetaminophen"},
    {"raw": "Adrenaline", "expected": "epinephrine"},
    {"raw": "Metformin Hydrochloride", "expected": "metformin"},
]


def _load_ddi_cases(limit: int | None = None) -> list[dict]:
    cases = [item for item in load_json(SENTINEL_PATH) if item.get("drug_a") and item.get("drug_b")]
    return cases[:limit] if limit else cases


def evaluate_ddi_metrics(limit: int | None = None) -> dict:
    if not neo4j_available():
        return {
            "section": "ddi_metrics",
            "status": "pending-data",
            "metrics": {},
            "targets": TARGETS,
            "notes": ["Neo4j is not reachable; direct DDI evaluation requires the live graph."],
            "samples": [],
        }

    cases = _load_ddi_cases(limit)
    expected_labels: list[str] = []
    predicted_labels: list[str] = []
    found = 0
    exact_match = 0
    latencies_ms: list[float] = []
    samples: list[dict] = []

    for case in cases:
        support, latency_ms = timed_call_ms(best_interaction_support, case["drug_a"], case["drug_b"])
        latencies_ms.append(latency_ms)
        expected = normalize_severity(case["expected_severity"])
        actual = normalize_severity(support["severity"])
        expected_labels.append(expected)
        predicted_labels.append(actual)
        if support["supported"]:
            found += 1
        if support["supported"] and actual == expected:
            exact_match += 1
        if len(samples) < 12:
            samples.append(
                {
                    "pair": f'{case["drug_a"]} + {case["drug_b"]}',
                    "expected_severity": expected,
                    "found": support["supported"],
                    "actual_severity": actual,
                    "source": support["source"],
                    "kind": support["kind"],
                }
            )

    synonym_hits = 0
    synonym_total = len(SYNONYM_CASES)
    from app.graph.query_engine import resolve_drug_name

    for case in SYNONYM_CASES:
        resolved = resolve_drug_name(case["raw"])
        if resolved.found and resolved.generic_name.lower() == case["expected"]:
            synonym_hits += 1

    inferred_rows = run_cypher(
        """
        MATCH ()-[r:INTERACTS_WITH]->()
        RETURN count(r) AS total,
               count(CASE WHEN r.severity_source = 'inferred' THEN 1 END) AS inferred
        """
    )
    inferred_total = inferred_rows[0]["total"] if inferred_rows else 0
    inferred_count = inferred_rows[0]["inferred"] if inferred_rows else 0

    metrics = {
        **binary_metrics(tp=found, fn=len(cases) - found, fp=None, tn=None),
        "positive_cases": len(cases),
        "severity_exact_match": round_metric(safe_div(exact_match, len(cases))),
        "weighted_kappa": quadratic_weighted_kappa(
            expected_labels,
            predicted_labels,
            ordered_labels=("unknown", "minor", "moderate", "major"),
        ),
        "sentinel_pass_rate": round_metric(safe_div(exact_match, len(cases))),
        "synonym_rescue_rate": round_metric(safe_div(synonym_hits, synonym_total)),
        "inferred_severity_rate": round_metric(safe_div(inferred_count, inferred_total)),
        "latency": latency_stats_ms(latencies_ms),
        "specificity": None,
        "ppv": None,
        "npv": None,
    }

    checks = [
        metric_gate(metrics["sensitivity"], TARGETS["sensitivity"]),
        metric_gate(metrics["severity_exact_match"], TARGETS["severity_exact_match"]),
        metric_gate(metrics["weighted_kappa"], TARGETS["weighted_kappa"]),
    ]
    status = evaluate_section_status(checks)
    notes = [
        "Sensitivity and severity are measured against the live 50-case sentinel set in app/data/sentinel_interactions.json.",
        "Specificity/PPV/NPV are intentionally left pending until a vetted negative DDI gold set is added; the graph contains many low-confidence PrimeKG edges that make ad hoc negatives misleading.",
    ]
    return {
        "section": "ddi_metrics",
        "status": status,
        "metrics": metrics,
        "targets": TARGETS,
        "notes": notes,
        "samples": samples,
    }


def test_ddi_metrics_evaluator_runs() -> None:
    result = evaluate_ddi_metrics(limit=8)
    assert result["section"] == "ddi_metrics"
    assert "metrics" in result
    if result["status"] == "pending-data":
        pytest.skip(result["notes"][0])
    assert result["metrics"]["positive_cases"] == 8
