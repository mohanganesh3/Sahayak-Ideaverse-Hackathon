"""Evaluation of OCR-text parsing and extraction readiness."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.eval._helpers import (
    canonical_drug_set,
    PROJECT_ROOT,
    evaluate_section_status,
    jaccard_similarity,
    latency_stats_ms,
    metric_gate,
    round_metric,
    run_async,
    safe_div,
    timed_async_call_ms,
)

TARGETS = {
    "text_fixture_drug_id_accuracy": 0.85,
    "dosage_exact_match_rate": 0.80,
}
IMAGE_FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "ocr"
TEXT_FIXTURES = [
    {
        "label": "Dolo 650",
        "ocr_text": "DOLO 650\nParacetamol Tablets IP\nEach tablet contains: Paracetamol IP 650 mg",
        "expected_brand": "DOLO 650",
        "expected_generic_set": {"acetaminophen"},
        "expected_doses": {"650mg"},
    },
    {
        "label": "PAN 40",
        "ocr_text": "PAN 40\nPantoprazole Gastro-resistant Tablets IP\nEach tablet contains: Pantoprazole Sodium IP equivalent to Pantoprazole 40 mg",
        "expected_brand": "PAN 40",
        "expected_generic_set": {"pantoprazole"},
        "expected_doses": {"40mg"},
    },
    {
        "label": "Thyronorm 50",
        "ocr_text": "THYRONORM 50 mcg\nLevothyroxine Sodium Tablets IP\nEach tablet contains: Levothyroxine Sodium IP 50 mcg",
        "expected_brand": "THYRONORM 50 mcg",
        "expected_generic_set": {"levothyroxine"},
        "expected_doses": {"50mcg"},
    },
    {
        "label": "Combiflam",
        "ocr_text": "COMBIFLAM\nEach tablet contains: Ibuprofen IP 400 mg + Paracetamol IP 325 mg",
        "expected_brand": "COMBIFLAM",
        "expected_generic_set": {"ibuprofen", "acetaminophen"},
        "expected_doses": {"400mg", "325mg"},
    },
]


def _normalize_dose_set(doses: set[str]) -> set[str]:
    return {"".join(str(dose).lower().split()) for dose in doses if str(dose).strip()}


def evaluate_ocr_pipeline(limit: int | None = None) -> dict:
    from app.services.drug_extractor import extract_drugs_from_text

    fixtures = TEXT_FIXTURES[:limit] if limit else TEXT_FIXTURES
    drug_id_hits = 0
    dosage_hits = 0
    ingredient_precisions: list[float] = []
    latencies_ms: list[float] = []
    samples: list[dict] = []

    for fixture in fixtures:
        extracted, latency_ms = timed_async_call_ms(extract_drugs_from_text, fixture["ocr_text"])
        latencies_ms.append(latency_ms)
        top = extracted[0] if extracted else {}
        actual_generics = {
            name
            for item in top.get("active_ingredients", [])
            for name in canonical_drug_set(item.get("name", ""))
        }
        if not actual_generics and top.get("generic_name"):
            actual_generics = canonical_drug_set(str(top["generic_name"]))
        actual_doses = _normalize_dose_set(
            {
                item.get("dose", "").strip().lower()
                for item in top.get("active_ingredients", [])
                if item.get("dose")
            }
        )
        expected_generics = {item.lower() for item in fixture["expected_generic_set"]}
        expected_doses = _normalize_dose_set(fixture["expected_doses"])
        if actual_generics == expected_generics:
            drug_id_hits += 1
        if expected_doses == actual_doses:
            dosage_hits += 1
        similarity = jaccard_similarity(expected_generics, actual_generics)
        if similarity is not None:
            ingredient_precisions.append(similarity)
        samples.append(
            {
                "label": fixture["label"],
                "expected_generics": sorted(expected_generics),
                "actual_generics": sorted(actual_generics),
                "expected_doses": sorted(expected_doses),
                "actual_doses": sorted(actual_doses),
                "graph_match": top.get("graph_match"),
                "match_type": top.get("match_type"),
            }
        )

    image_fixture_count = 0
    if IMAGE_FIXTURE_DIR.exists():
        image_fixture_count = len(list(IMAGE_FIXTURE_DIR.glob("*")))

    metrics = {
        "text_fixture_cases": len(fixtures),
        "text_fixture_drug_id_accuracy": round_metric(safe_div(drug_id_hits, len(fixtures))),
        "dosage_exact_match_rate": round_metric(safe_div(dosage_hits, len(fixtures))),
        "ingredient_jaccard_accuracy": round_metric(sum(ingredient_precisions) / len(ingredient_precisions))
        if ingredient_precisions
        else None,
        "text_extraction_latency": latency_stats_ms(latencies_ms),
        "image_fixture_count": image_fixture_count,
        "image_ocr_status": "pending-data" if image_fixture_count == 0 else "ready",
    }
    checks = [
        metric_gate(metrics["text_fixture_drug_id_accuracy"], TARGETS["text_fixture_drug_id_accuracy"]),
        metric_gate(metrics["dosage_exact_match_rate"], TARGETS["dosage_exact_match_rate"]),
    ]
    status = evaluate_section_status(checks)
    if image_fixture_count == 0 and status == "pass":
        status = "pending-data"
    notes = [
        "This evaluator measures the text-to-drug parsing stage deterministically using OCR-like label text fixtures from Indian medicines.",
    ]
    if image_fixture_count == 0:
        notes.append("Gold-image OCR CER/WER metrics remain pending until a labeled image fixture set is added under tests/fixtures/ocr/.")
    return {
        "section": "ocr_pipeline",
        "status": status,
        "metrics": metrics,
        "targets": TARGETS,
        "notes": notes,
        "samples": samples,
    }


def test_ocr_pipeline_evaluator_runs() -> None:
    result = evaluate_ocr_pipeline(limit=3)
    assert result["section"] == "ocr_pipeline"
    assert result["metrics"]["text_fixture_cases"] == 3
