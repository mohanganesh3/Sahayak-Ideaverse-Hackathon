"""Drug extraction and normalization pipeline for OCR text."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from openai import OpenAI

from app.config import GEMINI_API_KEY, GROQ_API_KEY, OPENAI_API_KEY
from app.graph.query_engine import resolve_drug_name
from app.services.ocr_service import extract_text_from_image

logger = logging.getLogger(__name__)

_COMPOSITION_PATTERNS = [
    re.compile(
        r"Each\s+(?:film[-\s]?coated\s+)?(?:tablet|capsule|ml)\s+contains?:?\s*(.+?)(?:\.|$)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"(\w[\w\s/+().-]+?)\s+(\d+(?:\.\d+)?)\s*(mg|mcg|g|ml|IU)",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"(?:IP|BP|USP)\s+(\d+(?:\.\d+)?)\s*(mg|mcg)",
        flags=re.IGNORECASE,
    ),
]
_DOSE_RE = re.compile(r"(?P<dose>\d+(?:\.\d+)?)\s*(?P<unit>mg|mcg|g|ml|iu)\b", re.IGNORECASE)
_DOSAGE_FORM_RE = re.compile(
    r"\b(tablet|capsule|syrup|suspension|injection|cream|ointment|gel|drops|spray|patch|sachet)\b",
    flags=re.IGNORECASE,
)
_EQUIVALENT_TO_RE = re.compile(r"\bequivalent to\b", re.IGNORECASE)
_BRAND_LINE_SKIP_RE = re.compile(
    r"^(composition|each .* contains|dosage|directions|warning|storage|schedule|manufactured|marketed)\b",
    flags=re.IGNORECASE,
)
_KNOWN_GENERIC_EQUIVALENTS = {
    "paracetamol": "acetaminophen",
    "pantoprazole sodium": "pantoprazole",
    "esomeprazole magnesium": "esomeprazole",
    "rabeprazole sodium": "rabeprazole",
    "metformin hydrochloride": "metformin",
}
_PACKAGING_BRAND_TOKENS = {
    "tablet",
    "tablets",
    "tab",
    "tabs",
    "capsule",
    "capsules",
    "cap",
    "caps",
    "strip",
    "strips",
    "pack",
    "packs",
    "blister",
    "blisters",
    "bottle",
    "bottles",
    "sachet",
    "sachets",
}
_PACKAGING_ONLY_BRAND_RE = re.compile(
    r"^(?:\d+\s*)?(?:tablets?|tabs?|capsules?|caps?|strips?|packs?|blisters?|bottles?|sachets?)\b",
    flags=re.IGNORECASE,
)
_MATCH_TYPE_PRIORITY = {
    "exact": 6,
    "synonym": 5,
    "brand": 5,
    "rxnorm_api": 4,
    "ingredient_resolved": 4,
    "fuzzy": 3,
    "resolve_drug": 3,
    "resolve_herb": 3,
    "ingredient_partial": 1,
    "manual": 0,
    "not_found": 0,
    "untrusted_match": 0,
}


def _extract_json_array(content: str) -> list[dict[str, Any]]:
    text = content.strip()
    if text.startswith("[") and text.endswith("]"):
        return json.loads(text)

    match = re.search(r"\[.*\]", text, flags=re.DOTALL)
    if not match:
        raise ValueError("Model response did not contain a JSON array")
    return json.loads(match.group(0))


def _candidate_brand_name(ocr_text: str) -> str:
    for line in ocr_text.splitlines():
        cleaned = " ".join(line.split()).strip(" -:")
        if len(cleaned) < 3:
            continue
        if _BRAND_LINE_SKIP_RE.match(cleaned):
            continue
        return cleaned
    return ""


def _clean_ingredient_name(raw_name: str) -> str:
    cleaned = " ".join(raw_name.split()).strip(" :-,")
    if not cleaned:
        return ""
    if _EQUIVALENT_TO_RE.search(cleaned):
        cleaned = _EQUIVALENT_TO_RE.split(cleaned, maxsplit=1)[-1].strip(" :-,")
    cleaned = re.sub(r"\b(?:IP|BP|USP)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:tablets?|capsules?|syrup|suspension)\b", "", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip(" :-,")


def _tokenize(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def _normalized_text_key(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _normalized_dose_key(value: Any) -> str:
    return _normalized_text_key(value).replace(" ", "")


def _resolution_looks_plausible(raw_name: str, resolved_name: str, match_type: str) -> bool:
    if not resolved_name:
        return False
    if match_type in {"exact", "synonym", "rxnorm_api"}:
        return True
    raw_tokens = _tokenize(raw_name)
    resolved_tokens = _tokenize(resolved_name)
    if not raw_tokens or not resolved_tokens:
        return False
    return bool(raw_tokens & resolved_tokens)


def _extract_regex_medicines(ocr_text: str) -> list[dict[str, Any]]:
    brand_name = _candidate_brand_name(ocr_text)
    dosage_form_match = _DOSAGE_FORM_RE.search(ocr_text)
    dosage_form = dosage_form_match.group(1).lower() if dosage_form_match else ""

    ingredients: list[dict[str, str]] = []

    composition_match = _COMPOSITION_PATTERNS[0].search(ocr_text)
    if composition_match:
        composition_text = composition_match.group(1)
        for chunk in re.split(r"\s*\+\s*|,\s*", composition_text):
            chunk = chunk.strip()
            if not chunk:
                continue
            dose_match = _DOSE_RE.search(chunk)
            if dose_match:
                name = _clean_ingredient_name(chunk[:dose_match.start()])
                dose = dose_match.group("dose") + dose_match.group("unit")
            else:
                name = _clean_ingredient_name(chunk)
                dose = ""
            if name:
                ingredients.append({"name": name, "dose": dose})

    if not ingredients:
        brand_lower = brand_name.lower()
        for raw_line in ocr_text.splitlines():
            line = " ".join(raw_line.split()).strip()
            if not line:
                continue
            line_lower = line.lower()
            if line_lower == brand_lower or line_lower.startswith(brand_lower):
                continue
            if not any(token in line_lower for token in (" ip ", " bp ", " usp ", "contains", "tablet", "capsule")):
                continue
            match = _COMPOSITION_PATTERNS[1].search(line)
            if not match:
                continue
            name = _clean_ingredient_name(match.group(1))
            dose = f"{match.group(2)}{match.group(3)}"
            if name:
                ingredients.append({"name": name, "dose": dose})

    if not ingredients:
        return []

    generic_name = " + ".join(item["name"] for item in ingredients if item["name"])
    return [
        {
            "brand_name": brand_name,
            "generic_name": generic_name,
            "active_ingredients": ingredients,
            "dosage_form": dosage_form,
            "confidence": 0.72 if brand_name else 0.66,
        }
    ]


_EXTRACTOR_SYSTEM_PROMPT = (
    "You are an expert pharmaceutical label parser specializing in Indian medicines.\n\n"
    "TASK: Extract ALL drug/medicine information from the OCR text of medicine packaging.\n\n"
    "RULES:\n"
    "1. BRAND NAME: Usually the first or largest text (e.g. 'Ecosprin', 'Crocin', 'PAN-D')\n"
    "2. GENERIC NAME: The actual drug compound name (e.g. 'Aspirin', 'Paracetamol', 'Pantoprazole + Domperidone')\n"
    "   - Look for 'Each tablet/capsule contains:', 'Composition:', or lines with 'IP'/'BP'/'USP'\n"
    "   - Combo drugs: list ALL active ingredients (e.g. Amlodipine 5mg + Atorvastatin 10mg)\n"
    "3. ACTIVE INGREDIENTS: Each ingredient with its exact dose (name + mg/mcg amount)\n"
    "   - 'IP' = Indian Pharmacopoeia, 'BP' = British Pharmacopoeia — these are quality standards, NOT ingredients\n"
    "   - 'equivalent to' means use the second name as the actual ingredient\n"
    "4. DOSAGE FORM: tablet, capsule, syrup, injection, cream, etc.\n"
    "5. If multiple medicines appear in one photo, list each separately\n"
    "6. confidence: 0.0-1.0 based on how clearly you could read the text\n\n"
    "COMMON MISTAKES TO AVOID:\n"
    "- Do NOT include manufacturer names as drug names\n"
    "- Do NOT include 'IP', 'BP', 'USP' as part of ingredient names\n"
    "- Do NOT confuse brand name with generic name\n"
    "- If the composition says 'Pantoprazole Sodium Sesquihydrate eq. to Pantoprazole 40mg', "
    "the ingredient is 'Pantoprazole' at 40mg\n\n"
    'Return JSON object with key "drugs": array of '
    '{"brand_name":"","generic_name":"","active_ingredients":[{"name":"","dose":""}],'
    '"dosage_form":"","confidence":0.0}.'
)


def _call_openai_extractor_sync(ocr_text: str) -> list[dict[str, Any]]:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured")

    client = OpenAI(api_key=OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _EXTRACTOR_SYSTEM_PROMPT},
            {"role": "user", "content": f"OCR text:\n{ocr_text}"},
        ],
    )
    content = response.choices[0].message.content or "{}"
    payload = json.loads(content)
    drugs = payload.get("drugs", [])
    if not isinstance(drugs, list):
        raise ValueError("OpenAI extractor returned invalid payload")
    return [item for item in drugs if isinstance(item, dict)]


def _call_gemini_extractor_sync(ocr_text: str) -> list[dict[str, Any]]:
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not configured")

    client = OpenAI(
        api_key=GEMINI_API_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    )
    response = client.chat.completions.create(
        model="gemini-2.0-flash",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _EXTRACTOR_SYSTEM_PROMPT},
            {"role": "user", "content": f"OCR text:\n{ocr_text}"},
        ],
    )
    content = response.choices[0].message.content or "{}"
    payload = json.loads(content)
    drugs = payload.get("drugs", [])
    if not isinstance(drugs, list):
        raise ValueError("Gemini extractor returned invalid payload")
    return [item for item in drugs if isinstance(item, dict)]


def _call_groq_extractor_sync(ocr_text: str) -> list[dict[str, Any]]:
    if not GROQ_API_KEY:
        raise RuntimeError("GROQ_API_KEY is not configured")

    client = OpenAI(api_key=GROQ_API_KEY, base_url="https://api.groq.com/openai/v1")
    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": _EXTRACTOR_SYSTEM_PROMPT},
            {"role": "user", "content": f"OCR text:\n{ocr_text}"},
        ],
    )
    content = response.choices[0].message.content or "{}"
    payload = json.loads(content)
    drugs = payload.get("drugs", [])
    if not isinstance(drugs, list):
        raise ValueError("Groq extractor returned invalid payload")
    return [item for item in drugs if isinstance(item, dict)]


async def _resolve_active_ingredient(name: str) -> dict[str, Any]:
    alias_target = _KNOWN_GENERIC_EQUIVALENTS.get(name.strip().lower())
    resolved = await asyncio.to_thread(resolve_drug_name, alias_target or name)
    resolved_name = resolved.generic_name or name
    if not resolved.found or not _resolution_looks_plausible(name, resolved_name, resolved.match_type):
        return {
            "requested_name": name,
            "resolved_name": name,
            "match_found": False,
            "match_type": "untrusted_match",
            "confidence": 0.0,
            "rxcui": "",
            "drug_class": "",
        }
    return {
        "requested_name": name,
        "resolved_name": resolved_name,
        "match_found": True,
        "match_type": resolved.match_type,
        "confidence": resolved.confidence,
        "rxcui": resolved.rxcui,
        "drug_class": resolved.drug_class,
    }


async def _enrich_drug_entry(entry: dict[str, Any]) -> dict[str, Any]:
    ingredients = entry.get("active_ingredients") or []
    normalized_ingredients: list[dict[str, Any]] = []

    for ingredient in ingredients:
        raw_name = str(ingredient.get("name", "")).strip()
        dose = str(ingredient.get("dose", "")).strip()
        if not raw_name:
            continue
        resolved = await _resolve_active_ingredient(raw_name)
        normalized_ingredients.append(
            {
                "name": resolved["resolved_name"],
                "dose": dose,
                "graph_match": resolved["match_found"],
                "match_type": resolved["match_type"],
                "rxcui": resolved["rxcui"],
            }
        )

    if any(item["graph_match"] for item in normalized_ingredients):
        resolved_doses = {item["dose"].lower() for item in normalized_ingredients if item["graph_match"] and item["dose"]}
        normalized_ingredients = [
            item
            for item in normalized_ingredients
            if item["graph_match"] or item["dose"].lower() not in resolved_doses
        ]

    deduped_ingredients: list[dict[str, Any]] = []
    seen_ingredients: set[tuple[str, str]] = set()
    for item in normalized_ingredients:
        key = (_normalized_text_key(item["name"]), _normalized_dose_key(item["dose"]))
        if key not in seen_ingredients:
            seen_ingredients.add(key)
            deduped_ingredients.append(item)
    normalized_ingredients = deduped_ingredients

    generic_candidate = str(entry.get("generic_name", "")).strip()
    generic_resolution = await _resolve_active_ingredient(generic_candidate) if generic_candidate else None
    if generic_resolution and generic_resolution["match_found"]:
        generic_name = generic_resolution["resolved_name"]
        graph_match = True
        match_type = generic_resolution["match_type"]
    elif normalized_ingredients:
        generic_name = " + ".join(dict.fromkeys(ingredient["name"] for ingredient in normalized_ingredients))
        graph_match = all(ingredient["graph_match"] for ingredient in normalized_ingredients)
        match_type = "ingredient_resolved" if graph_match else "ingredient_partial"
    else:
        generic_name = generic_candidate
        graph_match = False
        match_type = "not_found"

    confidence = entry.get("confidence", 0.0)
    try:
        confidence_float = float(confidence)
    except (TypeError, ValueError):
        confidence_float = 0.0

    return {
        "brand_name": str(entry.get("brand_name", "")).strip(),
        "generic_name": generic_name,
        "active_ingredients": normalized_ingredients,
        "dosage_form": str(entry.get("dosage_form", "")).strip().lower(),
        "confidence": max(0.0, min(confidence_float, 1.0)),
        "graph_match": graph_match,
        "match_type": match_type,
    }


def _merge_entries(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    merged = dict(primary)
    if not merged.get("brand_name") and secondary.get("brand_name"):
        merged["brand_name"] = secondary["brand_name"]
    if not merged.get("generic_name") and secondary.get("generic_name"):
        merged["generic_name"] = secondary["generic_name"]
    if not merged.get("dosage_form") and secondary.get("dosage_form"):
        merged["dosage_form"] = secondary["dosage_form"]
    merged["confidence"] = max(float(primary.get("confidence", 0.0)), float(secondary.get("confidence", 0.0)))

    seen: set[tuple[str, str]] = set()
    ingredients: list[dict[str, Any]] = []
    for source_group in (primary.get("active_ingredients") or [], secondary.get("active_ingredients") or []):
        iterable = source_group if isinstance(source_group, list) else [source_group]
        for source in iterable:
            if not isinstance(source, dict):
                continue
            key = (str(source.get("name", "")).lower(), str(source.get("dose", "")).lower())
            if key not in seen and any(key):
                seen.add(key)
                ingredients.append(source)
    merged["active_ingredients"] = ingredients
    return merged


def _entry_key(entry: dict[str, Any]) -> tuple[str, str]:
    brand_key = str(entry.get("brand_name", "")).strip().lower()
    if brand_key:
        return ("brand", brand_key)
    generic_key = str(entry.get("generic_name", "")).strip().lower()
    return ("generic", generic_key)


def _normalized_ingredient_signature(entry: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    ingredients: list[tuple[str, str]] = []
    for item in entry.get("active_ingredients") or []:
        if not isinstance(item, dict):
            continue
        name = _normalized_text_key(item.get("name", ""))
        dose = _normalized_dose_key(item.get("dose", ""))
        if name or dose:
            ingredients.append((name, dose))
    return tuple(sorted(dict.fromkeys(ingredients)))


def _normalized_entry_signature(entry: dict[str, Any]) -> tuple[Any, ...]:
    ingredient_signature = _normalized_ingredient_signature(entry)
    generic_key = _normalized_text_key(entry.get("generic_name", ""))
    dosage_form_key = _normalized_text_key(entry.get("dosage_form", ""))

    if ingredient_signature:
        anchor = generic_key or " + ".join(name for name, _ in ingredient_signature if name)
        return (anchor, ingredient_signature, dosage_form_key)
    if generic_key:
        return (generic_key, (), dosage_form_key)
    return (_normalized_text_key(entry.get("brand_name", "")), (), dosage_form_key)


def _brand_quality_score(brand_name: str) -> int:
    brand = " ".join(str(brand_name or "").split()).strip()
    if not brand:
        return -5

    lowered = brand.lower()
    if _PACKAGING_ONLY_BRAND_RE.match(lowered):
        return -8

    tokens = re.findall(r"[a-z0-9]+", lowered)
    meaningful_tokens = [token for token in tokens if token not in _PACKAGING_BRAND_TOKENS and not token.isdigit()]

    score = 0
    if re.search(r"[a-z]", lowered):
        score += 3
    if re.search(r"\d", lowered):
        score += 1
    score += len(meaningful_tokens) * 2
    score -= sum(1 for token in tokens if token in _PACKAGING_BRAND_TOKENS)
    if not meaningful_tokens:
        score -= 4
    return score


def _entry_preference(entry: dict[str, Any]) -> tuple[Any, ...]:
    try:
        confidence = float(entry.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    return (
        1 if entry.get("graph_match") else 0,
        _MATCH_TYPE_PRIORITY.get(_normalized_text_key(entry.get("match_type", "")), 0),
        len(_normalized_ingredient_signature(entry)),
        1 if _normalized_text_key(entry.get("generic_name", "")) else 0,
        _brand_quality_score(str(entry.get("brand_name", ""))),
        confidence,
        len(str(entry.get("brand_name", "")).strip()),
    )


def _merge_normalized_duplicates(primary: dict[str, Any], secondary: dict[str, Any]) -> dict[str, Any]:
    preferred, other = (
        (primary, secondary)
        if _entry_preference(primary) >= _entry_preference(secondary)
        else (secondary, primary)
    )
    merged = dict(preferred)

    if _brand_quality_score(str(other.get("brand_name", ""))) > _brand_quality_score(str(merged.get("brand_name", ""))):
        merged["brand_name"] = other.get("brand_name", "")
    elif not merged.get("brand_name") and other.get("brand_name"):
        merged["brand_name"] = other["brand_name"]

    if not merged.get("generic_name") and other.get("generic_name"):
        merged["generic_name"] = other["generic_name"]
    if not merged.get("dosage_form") and other.get("dosage_form"):
        merged["dosage_form"] = other["dosage_form"]

    primary_ingredients = merged.get("active_ingredients") or []
    secondary_ingredients = other.get("active_ingredients") or []
    if len(secondary_ingredients) > len(primary_ingredients):
        merged["active_ingredients"] = secondary_ingredients

    try:
        merged["confidence"] = max(float(primary.get("confidence", 0.0)), float(secondary.get("confidence", 0.0)))
    except (TypeError, ValueError):
        merged["confidence"] = preferred.get("confidence", 0.0)

    if not merged.get("graph_match") and other.get("graph_match"):
        merged["graph_match"] = True
        merged["match_type"] = other.get("match_type", merged.get("match_type", ""))

    return merged


def _dedupe_normalized_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[tuple[Any, ...], dict[str, Any]] = {}
    for entry in entries:
        key = _normalized_entry_signature(entry)
        if key in deduped:
            deduped[key] = _merge_normalized_duplicates(deduped[key], entry)
        else:
            deduped[key] = entry
    return list(deduped.values())


async def extract_drugs_from_text(ocr_text: str) -> list[dict[str, Any]]:
    """Extract structured medicines from OCR text and normalize them to the graph."""
    text = (ocr_text or "").strip()
    if not text:
        return []

    regex_entries = _extract_regex_medicines(text)

    llm_entries: list[dict[str, Any]] = []
    try:
        llm_entries = await asyncio.to_thread(_call_openai_extractor_sync, text)
        logger.info("Drug extraction via OpenAI GPT-4o-mini succeeded")
    except Exception as exc:
        logger.warning("OpenAI extraction failed (%s), trying Gemini fallback", exc)
        try:
            llm_entries = await asyncio.to_thread(_call_gemini_extractor_sync, text)
            logger.info("Drug extraction via Gemini Flash succeeded")
        except Exception as gemini_exc:
            logger.warning("Gemini extraction failed (%s), trying Groq fallback", gemini_exc)
            try:
                llm_entries = await asyncio.to_thread(_call_groq_extractor_sync, text)
                logger.info("Drug extraction via Groq fallback succeeded")
            except (RuntimeError, ValueError, json.JSONDecodeError) as groq_exc:
                logger.warning("All LLM extractors failed: %s", groq_exc)

    combined: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in regex_entries + llm_entries:
        key = _entry_key(entry)
        if key in combined:
            combined[key] = _merge_entries(combined[key], entry)
        else:
            combined[key] = entry

    enriched: list[dict[str, Any]] = []
    for entry in combined.values():
        enriched.append(await _enrich_drug_entry(entry))

    enriched = _dedupe_normalized_entries(enriched)
    enriched.sort(key=lambda item: item.get("confidence", 0.0), reverse=True)
    return enriched


async def extract_drugs_from_image(image_bytes: bytes) -> list[dict[str, Any]]:
    """Convenience wrapper that performs OCR and drug extraction in one call."""
    ocr_result = await extract_text_from_image(image_bytes)
    drugs = await extract_drugs_from_text(ocr_result.get("text", ""))
    for item in drugs:
        item.setdefault("ocr_confidence", ocr_result.get("confidence", 0.0))
        item.setdefault("ocr_language", ocr_result.get("language", "unknown"))
        item.setdefault("ocr_needs_fallback", ocr_result.get("needs_fallback", False))
    return drugs


async def extract_drug_names(ocr_text: str, medicine_type: str = "allopathic") -> list[str]:
    """Backward-compatible helper that returns the extracted generic/brand names."""
    drugs = await extract_drugs_from_text(ocr_text)
    names: list[str] = []
    for item in drugs:
        if item.get("generic_name"):
            names.append(item["generic_name"])
        elif item.get("brand_name"):
            names.append(item["brand_name"])
    return names


async def extract_dosages(ocr_text: str) -> list[dict[str, str]]:
    """Backward-compatible helper that returns ingredient-dose pairs."""
    drugs = await extract_drugs_from_text(ocr_text)
    dosages: list[dict[str, str]] = []
    for item in drugs:
        for ingredient in item.get("active_ingredients", []):
            dosages.append({"name": ingredient.get("name", ""), "dosage": ingredient.get("dose", "")})
    return dosages
