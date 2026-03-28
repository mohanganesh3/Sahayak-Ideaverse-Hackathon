from __future__ import annotations

import dataclasses
import logging
from typing import Any

from app.graph.query_engine import resolve_drug_name, resolve_herb_name
from app.services.drug_extractor import extract_drugs_from_text
from app.services.translation_service import detect_language, translate, translate_herb_to_english

logger = logging.getLogger(__name__)


def _resolve_drug_text(name: str, source_lang: str) -> dict[str, Any]:
    drug_input = name.strip()
    source = source_lang.strip()

    if source and not source.startswith("en"):
        try:
            detected = detect_language(drug_input)
            if detected != "en-IN":
                translated = translate(drug_input, detected, "en-IN")
                if translated and not translated.endswith("[Translation unavailable]") and translated != drug_input:
                    result = resolve_drug_name(translated)
                    if result.found:
                        return dataclasses.asdict(result)
                    drug_input = translated
        except Exception as exc:
            logger.warning("Drug translation from %s failed during manual resolution: %s", source, exc)

    return dataclasses.asdict(resolve_drug_name(drug_input))


def _resolve_herb_text(name: str, source_lang: str) -> dict[str, Any]:
    herb_input = name.strip()
    source = source_lang.strip()

    if source and not source.startswith("en"):
        try:
            english_name = translate_herb_to_english(herb_input, source)
            if english_name and english_name != herb_input:
                herb_input = english_name
        except Exception as exc:
            logger.warning("Herb translation from %s failed during manual resolution: %s", source, exc)

    return dataclasses.asdict(resolve_herb_name(herb_input))


async def resolve_manual_medicine(text: str, medicine_type: str, source_lang: str) -> dict[str, Any]:
    normalized_type = medicine_type.strip().lower()
    if normalized_type not in {"allopathic", "ayurvedic"}:
        raise ValueError("medicine_type must be 'allopathic' or 'ayurvedic'")

    text = text.strip()

    if normalized_type == "allopathic":
        extracted_candidates: list[dict[str, Any]] = []
        try:
            extracted_candidates = await extract_drugs_from_text(text)
            if extracted_candidates and any(bool(item.get("graph_match")) for item in extracted_candidates):
                return {
                    "medicines": extracted_candidates,
                    "resolution_stage": "extract",
                    "resolved_from": "extract_drugs_from_text",
                }
        except Exception as exc:
            logger.warning("manual allopathic extraction failed for '%s': %s", text, exc)

        resolved = _resolve_drug_text(text, source_lang)
        if resolved.get("found"):
            ingredients = [
                {"name": ingredient, "dose": "", "graph_match": True}
                for ingredient in resolved.get("ingredients", [])
                if ingredient
            ]
            return {
                "medicines": [
                    {
                        "brand_name": text,
                        "generic_name": resolved.get("generic_name") or text,
                        "active_ingredients": ingredients,
                        "dosage_form": "",
                        "confidence": resolved.get("confidence") or 0.6,
                        "graph_match": True,
                        "match_type": resolved.get("match_type") or "resolve_drug",
                    }
                ],
                "resolution_stage": "resolve",
                "resolved_from": "resolve_drug",
            }

        if extracted_candidates:
            logger.info(
                "manual allopathic resolution for '%s' returned unverified extraction only; using extracted fallback",
                text,
            )
            return {
                "medicines": extracted_candidates,
                "resolution_stage": "extract",
                "resolved_from": "extract_drugs_from_text",
            }

    else:
        resolved = _resolve_herb_text(text, source_lang)
        if resolved.get("found"):
            return {
                "medicines": [
                    {
                        "brand_name": resolved.get("name") or text,
                        "generic_name": resolved.get("name") or text,
                        "active_ingredients": [],
                        "dosage_form": "",
                        "confidence": resolved.get("confidence") or 0.6,
                        "graph_match": True,
                        "match_type": resolved.get("match_type") or "resolve_herb",
                    }
                ],
                "resolution_stage": "resolve",
                "resolved_from": "resolve_herb",
            }

    return {
        "medicines": [
            {
                "brand_name": text,
                "generic_name": text,
                "active_ingredients": [],
                "dosage_form": "",
                "confidence": 0.35,
                "graph_match": False,
                "match_type": "manual",
            }
        ],
        "resolution_stage": "manual_fallback",
        "resolved_from": "manual_fallback",
    }
