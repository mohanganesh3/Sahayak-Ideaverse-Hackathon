from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services import drug_extractor
from app.services import manual_resolution


def test_extract_drugs_from_text_dedupes_same_normalized_medicine(monkeypatch) -> None:
    monkeypatch.setattr(drug_extractor, "_extract_regex_medicines", lambda text: [])
    monkeypatch.setattr(
        drug_extractor,
        "_call_openai_extractor_sync",
        lambda text: [
            {
                "brand_name": "AMLOPIN-5",
                "generic_name": "Amlodipine",
                "active_ingredients": [{"name": "Amlodipine", "dose": "5 mg"}],
                "dosage_form": "tablet",
                "confidence": 0.9,
            },
            {
                "brand_name": "10 Tablets",
                "generic_name": "Amlodipine",
                "active_ingredients": [{"name": "Amlodipine", "dose": "5MG"}],
                "dosage_form": "tablet",
                "confidence": 0.72,
            },
        ],
    )

    async def fake_resolve_active_ingredient(name: str) -> dict[str, object]:
        return {
            "requested_name": name,
            "resolved_name": "Amlodipine",
            "match_found": True,
            "match_type": "exact",
            "confidence": 0.98,
            "rxcui": "197361",
            "drug_class": "calcium_channel_blocker",
        }

    monkeypatch.setattr(drug_extractor, "_resolve_active_ingredient", fake_resolve_active_ingredient)

    extracted = asyncio.run(drug_extractor.extract_drugs_from_text("AMLOPIN-5\n10 Tablets\nAmlodipine Tablets IP 5 MG"))

    assert len(extracted) == 1
    assert extracted[0]["brand_name"] == "AMLOPIN-5"
    assert extracted[0]["generic_name"] == "Amlodipine"
    assert extracted[0]["graph_match"] is True
    assert len(extracted[0]["active_ingredients"]) == 1


def test_manual_resolution_tries_resolve_drug_after_unverified_extract(monkeypatch) -> None:
    async def fake_extract_drugs_from_text(text: str) -> list[dict[str, object]]:
        return [
            {
                "brand_name": text,
                "generic_name": text,
                "active_ingredients": [],
                "dosage_form": "",
                "confidence": 0.41,
                "graph_match": False,
                "match_type": "not_found",
            }
        ]

    def fake_resolve_drug_text(name: str, source_lang: str) -> dict[str, object]:
        return {
            "found": True,
            "generic_name": "Amlodipine",
            "rxcui": "197361",
            "drug_class": "calcium_channel_blocker",
            "match_type": "brand",
            "confidence": 0.88,
            "raw_input": name,
            "ingredients": ["Amlodipine"],
        }

    monkeypatch.setattr(manual_resolution, "extract_drugs_from_text", fake_extract_drugs_from_text)
    monkeypatch.setattr(manual_resolution, "_resolve_drug_text", fake_resolve_drug_text)

    response = asyncio.run(
        manual_resolution.resolve_manual_medicine(
            text="Amlopin-5",
            medicine_type="allopathic",
            source_lang="en-IN",
        )
    )

    assert response["resolution_stage"] == "resolve"
    assert response["resolved_from"] == "resolve_drug"
    assert response["medicines"][0]["generic_name"] == "Amlodipine"
    assert response["medicines"][0]["graph_match"] is True
