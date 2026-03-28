"""OCR pipeline for medicine packaging images.

Primary OCR is GPT-4o vision when an OpenAI key is available. Groq vision is
the fallback model. Sarvam is used only when Indic-script transliteration is
needed, because that is where it adds the most value for this project.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from typing import Any

import requests
from openai import OpenAI

from app.config import GROQ_API_KEY, OPENAI_API_KEY, SARVAM_API_KEY
from app.services.gemini_utils import has_gemini_keys, iter_gemini_openai_clients

logger = logging.getLogger(__name__)

OPENAI_MODEL = "gpt-4o"
GEMINI_VISION_MODEL = "gemini-2.0-flash"
GROQ_VISION_MODEL = "llama-3.2-90b-vision-preview"
SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"
_INDIC_RE = re.compile(r"[\u0900-\u0D7F]")


def _image_data_url(image_bytes: bytes, mime_type: str = "image/jpeg") -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _extract_json_payload(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("Model response did not contain a JSON object")
    return json.loads(match.group(0))


def _normalize_ocr_result(payload: dict[str, Any]) -> dict[str, Any]:
    text = str(payload.get("text", "")).strip()
    confidence = payload.get("confidence", 0.0)
    language = str(payload.get("language", "unknown")).strip() or "unknown"

    try:
        confidence_float = float(confidence)
    except (TypeError, ValueError):
        confidence_float = 0.0

    confidence_float = max(0.0, min(confidence_float, 1.0))
    return {
        "text": text,
        "confidence": confidence_float,
        "language": language,
        "needs_fallback": confidence_float < 0.6 or not text,
    }


def _contains_indic_script(text: str) -> bool:
    return bool(_INDIC_RE.search(text))


def _sarvam_transliterate_sync(text: str, language: str) -> str:
    if not SARVAM_API_KEY or not text.strip():
        return text

    headers = {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {
        "input": text,
        "source_language_code": language if language and language != "unknown" else "auto",
        "target_language_code": "en-IN",
        "mode": "transliteration",
    }
    response = requests.post(SARVAM_TRANSLATE_URL, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    data = response.json()

    for key in ("translated_text", "output", "transliterated_text"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if isinstance(data.get("translations"), list) and data["translations"]:
        candidate = data["translations"][0]
        if isinstance(candidate, dict):
            for key in ("translated_text", "text", "output"):
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    return text


def _call_openai_ocr_sync(image_bytes: bytes) -> dict[str, Any]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model=OPENAI_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a specialist OCR engine for Indian pharmaceutical packaging. "
                    "Your job is to extract EVERY piece of text visible on the medicine box, strip, or label.\n\n"
                    "Focus especially on:\n"
                    "1. BRAND NAME (usually the largest, most prominent text)\n"
                    "2. COMPOSITION / GENERIC NAME (look for 'Each tablet/capsule contains:', 'Composition:', or the line with drug names + dosages like 'Paracetamol IP 500mg')\n"
                    "3. DOSAGE & STRENGTH (e.g. 500mg, 10mg, 75mg)\n"
                    "4. DOSAGE FORM (tablet, capsule, syrup, etc.)\n"
                    "5. MANUFACTURER NAME\n"
                    "6. Any schedule markings (H, H1, X) or 'Rx only'\n\n"
                    "Indian medicine conventions:\n"
                    "- 'IP' = Indian Pharmacopoeia, 'BP' = British Pharmacopoeia, 'USP' = US Pharmacopoeia\n"
                    "- Combo drugs list ingredients with '+' (e.g. 'Amlodipine 5mg + Atorvastatin 10mg')\n"
                    "- Regional text in Hindi, Kannada, Tamil etc. may appear — include it as-is\n\n"
                    "Return JSON with keys: text, confidence, language.\n"
                    "- text: ALL extracted text preserving line breaks\n"
                    "- confidence: float 0-1 for OCR quality\n"
                    "- language: primary language detected (e.g. 'english', 'hindi', 'kannada')"
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Read this medicine packaging image carefully. Extract ALL text, paying special "
                            "attention to the brand name, composition/generic name with dosage, and manufacturer. "
                            "Do not miss the 'Each tablet contains' or 'Composition' section — that is the most important part."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(image_bytes), "detail": "high"},
                    },
                ],
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    return _normalize_ocr_result(_extract_json_payload(content))


def _call_groq_vision_sync(image_bytes: bytes) -> dict[str, Any]:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    response = client.chat.completions.create(
        model=GROQ_VISION_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "You extract text from Indian medicine packaging images. "
                    "Focus on brand name, composition/generic name with dosage, and manufacturer. "
                    "Return JSON only with keys: text, confidence, language."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Extract ALL text from this medicine packaging photo. Focus on: "
                            "brand name, 'Each tablet contains' / composition section, dosage, manufacturer."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": _image_data_url(image_bytes)},
                    },
                ],
            },
        ],
    )
    content = response.choices[0].message.content or "{}"
    result = _normalize_ocr_result(_extract_json_payload(content))
    if result["text"] and _contains_indic_script(result["text"]):
        try:
            result["text"] = _sarvam_transliterate_sync(result["text"], result["language"])
        except requests.RequestException as exc:
            logger.warning("Sarvam transliteration failed: %s", exc)
    return result


def _call_gemini_vision_sync(image_bytes: bytes) -> dict[str, Any]:
    """Gemini 2.0 Flash vision — excellent at reading medicine packaging."""
    if not has_gemini_keys():
        raise RuntimeError("GEMINI_API_KEY is not configured")

    last_exc: Exception | None = None
    for index, total, client in iter_gemini_openai_clients():
        try:
            response = client.chat.completions.create(
                model=GEMINI_VISION_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a specialist OCR engine for Indian pharmaceutical packaging. "
                            "Extract EVERY piece of text visible on the medicine box, strip, or label.\n\n"
                            "Focus on: brand name, composition/generic name (the 'Each tablet contains' section), "
                            "dosage, manufacturer. 'IP'=Indian Pharmacopoeia, 'BP'=British Pharmacopoeia.\n\n"
                            "Return JSON: {\"text\": \"all text here\", \"confidence\": 0.0-1.0, \"language\": \"english\"}"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Read this medicine packaging image carefully. Extract ALL text, "
                                    "especially the brand name, composition with dosages, and manufacturer."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": _image_data_url(image_bytes)},
                            },
                        ],
                    },
                ],
            )
            content = response.choices[0].message.content or "{}"
            result = _normalize_ocr_result(_extract_json_payload(content))
            if result["text"] and _contains_indic_script(result["text"]):
                try:
                    result["text"] = _sarvam_transliterate_sync(result["text"], result["language"])
                except requests.RequestException as exc:
                    logger.warning("Sarvam transliteration failed for Gemini output: %s", exc)
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning("Gemini OCR failed with key %d/%d: %s", index, total, exc)

    raise RuntimeError("All Gemini API keys failed for OCR") from last_exc


def _pick_best_ocr(results: list[dict[str, Any]]) -> dict[str, Any]:
    """From a list of OCR results, pick the one with the most useful text."""
    best = {"text": "", "confidence": 0.0, "language": "unknown", "needs_fallback": True}
    for r in results:
        if not r.get("text"):
            continue
        # Prefer results that actually contain pharmaceutical keywords
        text_lower = r["text"].lower()
        has_pharma_keywords = any(kw in text_lower for kw in (
            "tablet", "capsule", "contains", "composition", "mg", "mcg",
            "ip", "bp", "usp", "manufactured", "syrup", "each",
        ))
        r_score = float(r.get("confidence", 0.0))
        if has_pharma_keywords:
            r_score += 0.15  # bonus for having relevant content
        best_score = float(best.get("confidence", 0.0))
        if best.get("text") and any(kw in best["text"].lower() for kw in (
            "tablet", "capsule", "contains", "composition", "mg",
        )):
            best_score += 0.15
        if r_score > best_score or not best.get("text"):
            best = r
    return best


async def extract_text_from_image(image_bytes: bytes) -> dict[str, Any]:
    """Run OCR over medicine packaging. GPT-4o → Gemini Flash → Groq vision."""
    if not image_bytes:
        return {"text": "", "confidence": 0.0, "language": "unknown", "needs_fallback": True}

    results: list[dict[str, Any]] = []

    # 1. Try GPT-4o (best vision model)
    if OPENAI_API_KEY:
        try:
            primary = await asyncio.to_thread(_call_openai_ocr_sync, image_bytes)
            results.append(primary)
            logger.info("GPT-4o OCR: confidence=%.2f, text_len=%d", primary["confidence"], len(primary["text"]))
            # If GPT-4o produced good text, return immediately
            if primary["confidence"] >= 0.6 and primary["text"]:
                return primary
        except Exception as exc:
            logger.warning("OpenAI OCR failed: %s", exc)

    # 2. Fallback to Gemini 2.0 Flash vision
    if has_gemini_keys():
        try:
            gemini_result = await asyncio.to_thread(_call_gemini_vision_sync, image_bytes)
            results.append(gemini_result)
            logger.info("Gemini OCR: confidence=%.2f, text_len=%d", gemini_result["confidence"], len(gemini_result["text"]))
            if gemini_result["confidence"] >= 0.6 and gemini_result["text"]:
                return _pick_best_ocr(results)
        except Exception as exc:
            logger.warning("Gemini vision OCR failed: %s", exc)

    # 3. Last resort: Groq vision
    if GROQ_API_KEY:
        try:
            groq_result = await asyncio.to_thread(_call_groq_vision_sync, image_bytes)
            results.append(groq_result)
            logger.info("Groq OCR: confidence=%.2f, text_len=%d", groq_result["confidence"], len(groq_result["text"]))
        except Exception as exc:
            logger.warning("Groq vision OCR failed: %s", exc)

    # Pick the best result from whatever succeeded
    if results:
        return _pick_best_ocr(results)

    return {"text": "", "confidence": 0.0, "language": "unknown", "needs_fallback": True}


async def extract_text(image_bytes: bytes, prefer_sarvam: bool = False) -> str:
    """Backward-compatible OCR entry point returning just the extracted text."""
    result = await extract_text_from_image(image_bytes)
    return result["text"]
