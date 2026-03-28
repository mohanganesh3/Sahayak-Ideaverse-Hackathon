"""Evaluation of Indian brand and synonym resolution quality."""

from __future__ import annotations

import pytest

from tests.eval._helpers import (
    DATA_DIR,
    canonical_drug_set,
    evaluate_section_status,
    jaccard_similarity,
    latency_stats_ms,
    load_json,
    metric_gate,
    neo4j_available,
    round_metric,
    safe_div,
    timed_call_ms,
)

TARGETS = {
    "top1_accuracy": 0.95,
    "top3_accuracy": 0.98,
    "indian_brand_rescue_rate": 0.90,
}
OCR_CORRUPTED_BRANDS = [
    ("ecosprin75", "aspirin"),
    ("dolo650", "paracetamol"),
    ("pan40", "pantoprazole"),
    ("thyronorm50", "levothyroxine"),
    ("clopitaba", "aspirin + clopidogrel"),
]
SYNONYM_CASES = [
    ("Acetylsalicylic acid", "aspirin"),
    ("Paracetamol", "acetaminophen"),
    ("Adrenaline", "epinephrine"),
    ("Metformin Hydrochloride", "metformin"),
]


def _brand_cases(limit: int | None = None) -> list[tuple[str, dict]]:
    brand_map = load_json(DATA_DIR / "indian_brand_map.json")
    items = list(brand_map.items())
    return items[:limit] if limit else items


def _resolved_set(resolved: object) -> set[str]:
    ingredients = getattr(resolved, "ingredients", []) or []
    if ingredients:
        return {item.lower() for item in ingredients}
    generic_name = getattr(resolved, "generic_name", "") or ""
    return canonical_drug_set(generic_name)


def evaluate_brand_resolution(limit: int | None = None) -> dict:
    if not neo4j_available():
        return {
            "section": "brand_resolution",
            "status": "pending-data",
            "metrics": {},
            "targets": TARGETS,
            "notes": ["Neo4j is not reachable; brand resolution uses the live Indian brand graph."],
            "samples": [],
        }

    from app.graph.query_engine import resolve_drug_name, search_indian_brand

    cases = _brand_cases(limit)
    top1_hits = 0
    top3_hits = 0
    rescue_hits = 0
    combo_jaccards: list[float] = []
    false_normalizations = 0
    latencies_ms: list[float] = []
    samples: list[dict] = []

    for brand_name, expected in cases:
        resolved, latency_ms = timed_call_ms(resolve_drug_name, brand_name)
        latencies_ms.append(latency_ms)
        expected_set = canonical_drug_set(expected["generic"])
        actual_set = _resolved_set(resolved)
        exact = actual_set == expected_set
        if exact:
            top1_hits += 1
        if getattr(resolved, "found", False) and getattr(resolved, "match_type", "") in {"brand", "fuzzy"}:
            rescue_hits += 1
        if len(expected_set) > 1:
            similarity = jaccard_similarity(expected_set, actual_set)
            if similarity is not None:
                combo_jaccards.append(similarity)
        elif getattr(resolved, "found", False) and actual_set and not exact:
            false_normalizations += 1

        hits = search_indian_brand(brand_name, limit=3)
        expected_hit = False
        for hit in hits:
            hit_set = canonical_drug_set(" + ".join(hit.get("contained_drugs", [])))
            if hit_set == expected_set or expected_set.issubset(hit_set):
                expected_hit = True
                break
        if expected_hit:
            top3_hits += 1

        if len(samples) < 12:
            samples.append(
                {
                    "brand": brand_name,
                    "expected": sorted(expected_set),
                    "actual": sorted(actual_set),
                    "match_type": getattr(resolved, "match_type", ""),
                    "top3_hit": expected_hit,
                }
            )

    synonym_hits = 0
    for raw_name, expected in SYNONYM_CASES:
        resolved = resolve_drug_name(raw_name)
        if getattr(resolved, "found", False) and getattr(resolved, "generic_name", "").lower() == expected:
            synonym_hits += 1

    corrupted_hits = 0
    for raw_name, expected in OCR_CORRUPTED_BRANDS:
        resolved = resolve_drug_name(raw_name)
        if getattr(resolved, "found", False) and canonical_drug_set(getattr(resolved, "generic_name", "") or " + ".join(getattr(resolved, "ingredients", []))) == canonical_drug_set(expected):
            corrupted_hits += 1

    metrics = {
        "cases_evaluated": len(cases),
        "top1_accuracy": round_metric(safe_div(top1_hits, len(cases))),
        "top3_accuracy": round_metric(safe_div(top3_hits, len(cases))),
        "combination_jaccard_accuracy": round_metric(sum(combo_jaccards) / len(combo_jaccards)) if combo_jaccards else None,
        "false_normalization_rate": round_metric(safe_div(false_normalizations, len(cases))),
        "indian_brand_rescue_rate": round_metric(safe_div(rescue_hits, len(cases))),
        "ocr_corrupted_resolution_rate": round_metric(safe_div(corrupted_hits, len(OCR_CORRUPTED_BRANDS))),
        "synonym_rescue_rate": round_metric(safe_div(synonym_hits, len(SYNONYM_CASES))),
        "latency": latency_stats_ms(latencies_ms),
    }
    checks = [
        metric_gate(metrics["top1_accuracy"], TARGETS["top1_accuracy"]),
        metric_gate(metrics["top3_accuracy"], TARGETS["top3_accuracy"]),
        metric_gate(metrics["indian_brand_rescue_rate"], TARGETS["indian_brand_rescue_rate"]),
    ]
    return {
        "section": "brand_resolution",
        "status": evaluate_section_status(checks),
        "metrics": metrics,
        "targets": TARGETS,
        "notes": [
            "Evaluation uses the live Indian brand graph plus OCR-corrupted variants to measure real rescue behavior, not just clean exact matches.",
        ],
        "samples": samples,
    }


def test_brand_resolution_evaluator_runs() -> None:
    result = evaluate_brand_resolution(limit=12)
    assert result["section"] == "brand_resolution"
    if result["status"] == "pending-data":
        pytest.skip(result["notes"][0])
    assert result["metrics"]["cases_evaluated"] == 12
