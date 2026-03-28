"""Indian brand name → generic name → RxCUI normalisation pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path

from app.config import DATA_DIR
from app.graph.query_engine import search_indian_brand

BrandMapEntry = str | dict[str, str]
_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
_ALNUM_BOUNDARY_RE = re.compile(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])")
_DOSAGE_FORM_RE = re.compile(
    r"\b(tablet|tablets|capsule|capsules|syrup|suspension|drops|drop|injection|cream|"
    r"ointment|gel|inhaler|respules|spray|patch|oral|liquid|mr|sr|xr|er|dt|md)\b"
)
_STRENGTH_UNIT_RE = re.compile(r"(?<=\d)\s*(mg|mcg|g|gm|ml)\b")


def _candidate_brand_keys(brand_name: str) -> list[str]:
    cleaned = _CAMEL_BOUNDARY_RE.sub(" ", brand_name).strip().lower()
    cleaned = _ALNUM_BOUNDARY_RE.sub(" ", cleaned)
    cleaned = _NON_ALNUM_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return []

    candidates = [cleaned]
    simplified = _WHITESPACE_RE.sub(" ", _DOSAGE_FORM_RE.sub(" ", cleaned)).strip()
    if simplified and simplified not in candidates:
        candidates.append(simplified)

    compact = simplified.replace(" mg", "mg").replace(" mcg", "mcg")
    if compact and compact not in candidates:
        candidates.append(compact)

    without_units = _WHITESPACE_RE.sub(" ", _STRENGTH_UNIT_RE.sub("", compact)).strip()
    if without_units and without_units not in candidates:
        candidates.append(without_units)

    source_tokens = (without_units or simplified or cleaned).split()
    word_tokens = [token for token in source_tokens if not any(char.isdigit() for char in token)]
    strength_tokens = [token for token in source_tokens if any(char.isdigit() for char in token)]
    if word_tokens:
        candidates.append(" ".join(word_tokens))
    if word_tokens and strength_tokens:
        candidates.append(" ".join(word_tokens + strength_tokens))
        candidates.append(" ".join(strength_tokens + word_tokens))

    ordered_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.add(candidate)
            ordered_candidates.append(candidate)

    return ordered_candidates


def _collapse_generic_names(drug_names: list[str]) -> str | None:
    normalized = [
        _WHITESPACE_RE.sub(" ", drug_name.casefold()).strip()
        for drug_name in drug_names
        if drug_name
    ]
    if not normalized:
        return None
    return " + ".join(normalized)


def _lookup_brand_via_graph(brand_name: str) -> str | None:
    query_keys = set(_candidate_brand_keys(brand_name))
    matches = search_indian_brand(brand_name, limit=5)
    if not matches:
        return None

    exact_generics: list[str] = []
    all_generics: list[str] = []
    for match in matches:
        generic_name = _collapse_generic_names(match.get("contained_drugs", []))
        if generic_name is None:
            continue
        all_generics.append(generic_name)
        brand_keys = set(_candidate_brand_keys(match.get("brand_name", "")))
        if query_keys & brand_keys:
            exact_generics.append(generic_name)

    if exact_generics and len(set(exact_generics)) == 1:
        return exact_generics[0]
    if all_generics and len(set(all_generics)) == 1:
        return all_generics[0]

    return None


def load_brand_map() -> dict[str, BrandMapEntry]:
    """Load the Indian brand → generic quick-lookup map.

    Returns:
        Dict mapping brand names (lowercased) to generic metadata.
    """
    candidate_paths = (
        DATA_DIR / "indian_brand_map.json",
        Path(__file__).resolve().parents[1] / "data" / "indian_brand_map.json",
    )
    for path in candidate_paths:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    return {}


def brand_to_generic(brand_name: str) -> str | None:
    """Resolve an Indian brand name to its generic equivalent.

    Args:
        brand_name: Commercial brand name (e.g. ``"Crocin"``).

    Returns:
        Generic name or ``None`` if unresolved.
    """
    brand_map = load_brand_map()
    candidate_keys = _candidate_brand_keys(brand_name)
    for candidate_key in candidate_keys:
        entry = brand_map.get(candidate_key)
        if isinstance(entry, dict):
            generic = entry.get("generic")
            if generic:
                return generic
        elif entry:
            return entry

    # If the caller omits the strength, fall back to a unique brand-family match.
    for candidate_key in candidate_keys:
        prefix_matches = []
        for map_key, entry in brand_map.items():
            if map_key == candidate_key or map_key.startswith(f"{candidate_key} "):
                generic = entry.get("generic") if isinstance(entry, dict) else entry
                if generic:
                    prefix_matches.append(generic)
        if prefix_matches and len(set(prefix_matches)) == 1:
            return prefix_matches[0]

    return _lookup_brand_via_graph(brand_name)


def generic_to_rxcui(generic_name: str) -> str | None:
    """Look up the RxNorm CUI for a generic drug name.

    Args:
        generic_name: Generic drug name (e.g. ``"paracetamol"``).

    Returns:
        RxCUI string or ``None``.
    """
    # TODO: call RxNorm REST API or local lookup
    raise NotImplementedError


def normalize(drug_name: str) -> dict[str, str | None]:
    """Full normalisation pipeline: brand → generic → RxCUI.

    Args:
        drug_name: Raw drug name from OCR extraction.

    Returns:
        Dict with ``brand``, ``generic``, and ``rxcui`` keys.
    """
    generic = brand_to_generic(drug_name)
    rxcui = generic_to_rxcui(generic) if generic else None
    return {
        "brand": drug_name,
        "generic": generic,
        "rxcui": rxcui,
    }
