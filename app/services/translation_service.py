"""Sarvam translation helpers with medical-safe fallbacks."""

from __future__ import annotations

import copy
import logging
import re
import time
from typing import Any

import requests

from app.config import SARVAM_API_KEY
from app.graph.neo4j_connection import get_driver
from app.graph.query_engine import resolve_herb_name

logger = logging.getLogger(__name__)

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"
SUPPORTED_CODES = {"en-IN", "hi-IN", "ta-IN", "te-IN", "kn-IN"}
TERM_RE = re.compile(r"\b[A-Z][a-zA-Z0-9.+/-]{2,}(?:\s+[A-Z][a-zA-Z0-9.+/-]{2,})*\b")
_ROOT_TEXT_FIELDS = ("patient_summary", "self_prescribed_warning", "personalized_advice", "disclaimer")
_FINDING_TEXT_FIELDS = ("title", "patient_explanation", "doctor_explanation", "action")
_ACB_TEXT_FIELDS = ("risk",)
_REPORT_MARKER_RE = re.compile(r"^__SAHAYAK_FIELD_\d{4}__$")
_MAX_REPORT_TRANSLATION_CHARS = 3200


def _normalize_code(language: str, default: str = "en-IN") -> str:
    if not language:
        return default
    lang = language.strip()
    if lang in SUPPORTED_CODES:
        return lang
    short = lang.split("-")[0].lower()
    mapped = {
        "en": "en-IN",
        "hi": "hi-IN",
        "ta": "ta-IN",
        "te": "te-IN",
        "kn": "kn-IN",
    }.get(short)
    return mapped or default


def _sarvam_headers() -> dict[str, str]:
    if not SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is not configured")
    return {
        "api-subscription-key": SARVAM_API_KEY,
        "Content-Type": "application/json",
    }


def translate(text: str, source_lang: str, target_lang: str) -> str:
    """Translate text with Sarvam, falling back to English-safe text on failure."""
    if not text:
        return ""

    source = _normalize_code(source_lang, default="en-IN")
    target = _normalize_code(target_lang, default="en-IN")
    if source == target:
        return text

    payload = {
        "input": text,
        "source_language_code": source,
        "target_language_code": target,
        "model": "sarvam-translate:v1",
    }
    last_error: requests.RequestException | None = None
    for attempt in range(3):
        try:
            response = requests.post(
                SARVAM_TRANSLATE_URL,
                headers=_sarvam_headers(),
                json=payload,
                timeout=30,
            )
            if response.status_code == 429 and attempt < 2:
                retry_after = response.headers.get("Retry-After")
                try:
                    sleep_seconds = float(retry_after) if retry_after else float(attempt + 1)
                except ValueError:
                    sleep_seconds = float(attempt + 1)
                time.sleep(min(max(sleep_seconds, 1.0), 4.0))
                continue

            response.raise_for_status()
            data = response.json()
            break
        except requests.RequestException as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(float(attempt + 1))
                continue
            logger.warning("Sarvam translate failed: %s", exc)
            return f"{text} [Translation unavailable]"
    else:
        if last_error is not None:
            logger.warning("Sarvam translate failed: %s", last_error)
        return f"{text} [Translation unavailable]"

    for key in ("translated_text", "output", "translation"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    if isinstance(data.get("translations"), list) and data["translations"]:
        first = data["translations"][0]
        if isinstance(first, dict):
            for key in ("translated_text", "text", "output"):
                value = first.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

    logger.warning("Sarvam translate returned unexpected payload keys: %s", sorted(data))
    return f"{text} [Translation unavailable]"


def detect_language(text: str) -> str:
    """Lightweight language detection for supported SAHAYAK languages."""
    if not text.strip():
        return "en-IN"
    if re.search(r"[\u0B80-\u0BFF]", text):
        return "ta-IN"
    if re.search(r"[\u0C00-\u0C7F]", text):
        return "te-IN"
    if re.search(r"[\u0C80-\u0CFF]", text):
        return "kn-IN"
    if re.search(r"[\u0900-\u097F]", text):
        return "hi-IN"
    return "en-IN"


def _extract_medical_terms(text: str) -> list[str]:
    terms: list[str] = []
    for match in TERM_RE.finditer(text):
        term = match.group(0)
        if any(char.isdigit() for char in term):
            continue
        if len(term.split()) > 4:
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _safe_term_translation(term: str, target_lang: str) -> str:
    translated = translate(term, "en-IN", target_lang)
    if translated.endswith("[Translation unavailable]"):
        return term
    return f"{term} ({translated})"


def _append_term_glossary(original_text: str, translated_text: str, target_lang: str) -> str:
    if _normalize_code(target_lang) == "en-IN":
        return translated_text
    terms = _extract_medical_terms(original_text)
    if not terms:
        return translated_text
    glossary = ", ".join(_safe_term_translation(term, target_lang) for term in terms)
    return f"{translated_text} [{glossary}]"


def _translate_nested(value: Any, target_lang: str) -> Any:
    if isinstance(value, dict):
        return {key: _translate_nested(val, target_lang) for key, val in value.items()}
    if isinstance(value, list):
        return [_translate_nested(item, target_lang) for item in value]
    if isinstance(value, str):
        translated = translate(value, detect_language(value), target_lang)
        return _append_term_glossary(value, translated, target_lang)
    return value


def _build_report_translation_payload(report_dict: dict[str, Any]) -> dict[str, Any]:
    findings_payload: list[dict[str, str]] = []
    for finding in report_dict.get("findings", []) or []:
        if not isinstance(finding, dict):
            continue
        finding_payload = {
            field: str(finding.get(field) or "")
            for field in _FINDING_TEXT_FIELDS
            if isinstance(finding.get(field), str) and str(finding.get(field)).strip()
        }
        finding_id = finding.get("finding_id")
        if finding_id:
            finding_payload["finding_id"] = str(finding_id)
        findings_payload.append(finding_payload)

    payload: dict[str, Any] = {
        field: report_dict.get(field)
        for field in _ROOT_TEXT_FIELDS
        if isinstance(report_dict.get(field), str) and str(report_dict.get(field)).strip()
    }
    payload["findings"] = findings_payload

    acb_section = report_dict.get("acb_section")
    if isinstance(acb_section, dict):
        payload["acb_section"] = {
            field: acb_section.get(field)
            for field in _ACB_TEXT_FIELDS
            if isinstance(acb_section.get(field), str) and str(acb_section.get(field)).strip()
        }
    return payload


def _apply_report_translation(
    report_dict: dict[str, Any],
    translated_payload: dict[str, Any],
) -> dict[str, Any]:
    translated = copy.deepcopy(report_dict)

    for field in _ROOT_TEXT_FIELDS:
        value = translated_payload.get(field)
        if isinstance(value, str) and value.strip():
            translated[field] = value.strip()

    translated_findings = translated_payload.get("findings")
    if isinstance(translated_findings, list):
        findings = translated.get("findings")
        if isinstance(findings, list):
            translated_by_id = {
                str(item.get("finding_id")): item
                for item in translated_findings
                if isinstance(item, dict) and item.get("finding_id")
            }
            for index, finding in enumerate(findings):
                if not isinstance(finding, dict):
                    continue
                finding_id = str(finding.get("finding_id") or "")
                translated_finding = translated_by_id.get(finding_id)
                if not translated_finding and index < len(translated_findings):
                    translated_finding = translated_findings[index]
                if not isinstance(translated_finding, dict):
                    continue
                for field in _FINDING_TEXT_FIELDS:
                    value = translated_finding.get(field)
                    if isinstance(value, str) and value.strip():
                        finding[field] = value.strip()

    acb_payload = translated_payload.get("acb_section")
    acb_section = translated.get("acb_section")
    if isinstance(acb_payload, dict) and isinstance(acb_section, dict):
        for field in _ACB_TEXT_FIELDS:
            value = acb_payload.get(field)
            if isinstance(value, str) and value.strip():
                acb_section[field] = value.strip()

    return translated


def _translate_text_for_report(text: str, target_lang: str) -> str:
    translated = translate(text, detect_language(text), target_lang)
    if translated.endswith("[Translation unavailable]"):
        return text
    return translated


def _collect_report_translation_entries(payload: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    for field in _ROOT_TEXT_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            entries.append({"kind": "root", "field": field, "text": value.strip()})

    findings = payload.get("findings")
    if isinstance(findings, list):
        for index, finding in enumerate(findings):
            if not isinstance(finding, dict):
                continue
            for field in _FINDING_TEXT_FIELDS:
                value = finding.get(field)
                if isinstance(value, str) and value.strip():
                    entries.append(
                        {
                            "kind": "finding",
                            "index": index,
                            "finding_id": str(finding.get("finding_id") or ""),
                            "field": field,
                            "text": value.strip(),
                        },
                    )

    acb_section = payload.get("acb_section")
    if isinstance(acb_section, dict):
        for field in _ACB_TEXT_FIELDS:
            value = acb_section.get(field)
            if isinstance(value, str) and value.strip():
                entries.append({"kind": "acb", "field": field, "text": value.strip()})

    return entries


def _pack_report_translation_chunks(entries: list[dict[str, Any]]) -> list[tuple[list[dict[str, Any]], str]]:
    chunks: list[tuple[list[dict[str, Any]], str]] = []
    current_entries: list[dict[str, Any]] = []
    current_parts: list[str] = []
    current_length = 0

    for index, entry in enumerate(entries):
        marker = f"__SAHAYAK_FIELD_{index:04d}__"
        entry["marker"] = marker
        block = f"{marker}\n{entry['text']}"
        block_length = len(block) + 1

        if current_parts and current_length + block_length > _MAX_REPORT_TRANSLATION_CHARS:
            chunks.append((current_entries, "\n".join(current_parts)))
            current_entries = []
            current_parts = []
            current_length = 0

        current_entries.append(entry)
        current_parts.append(block)
        current_length += block_length

    if current_parts:
        chunks.append((current_entries, "\n".join(current_parts)))
    return chunks


def _parse_report_translation_chunk(translated_chunk: str) -> dict[str, str]:
    translated_by_marker: dict[str, str] = {}
    current_marker: str | None = None
    current_lines: list[str] = []

    for raw_line in translated_chunk.splitlines():
        line = raw_line.strip()
        if _REPORT_MARKER_RE.fullmatch(line):
            if current_marker is not None:
                translated_by_marker[current_marker] = "\n".join(current_lines).strip()
            current_marker = line
            current_lines = []
            continue
        if current_marker is not None:
            current_lines.append(raw_line)

    if current_marker is not None:
        translated_by_marker[current_marker] = "\n".join(current_lines).strip()

    return translated_by_marker


def _set_report_translation_value(payload: dict[str, Any], entry: dict[str, Any], value: str) -> None:
    kind = entry["kind"]
    field = str(entry["field"])
    if kind == "root":
        payload[field] = value
        return
    if kind == "finding":
        findings = payload.get("findings")
        index = int(entry["index"])
        finding_id = str(entry.get("finding_id") or "")
        if isinstance(findings, list):
            if finding_id:
                for finding in findings:
                    if isinstance(finding, dict) and str(finding.get("finding_id") or "") == finding_id:
                        finding[field] = value
                        return
            if index < len(findings) and isinstance(findings[index], dict):
                findings[index][field] = value
        return
    if kind == "acb":
        acb_section = payload.get("acb_section")
        if isinstance(acb_section, dict):
            acb_section[field] = value


def _translate_report_payload_sarvam(payload: dict[str, Any], target_lang: str) -> dict[str, Any]:
    translated_payload = copy.deepcopy(payload)
    entries = _collect_report_translation_entries(translated_payload)
    if not entries:
        return translated_payload

    chunks = _pack_report_translation_chunks(entries)
    for chunk_entries, chunk_text in chunks:
        translated_chunk = _translate_text_for_report(chunk_text, target_lang)
        parsed_chunk = _parse_report_translation_chunk(translated_chunk)

        if not all(entry.get("marker") in parsed_chunk for entry in chunk_entries):
            logger.warning(
                "Sarvam report chunk lost one or more markers; falling back to per-field translation for %d fields",
                len(chunk_entries),
            )
            for entry in chunk_entries:
                translated_text = _translate_text_for_report(str(entry["text"]), target_lang)
                _set_report_translation_value(translated_payload, entry, translated_text)
            continue

        for entry in chunk_entries:
            marker = str(entry["marker"])
            translated_text = parsed_chunk.get(marker) or str(entry["text"])
            _set_report_translation_value(translated_payload, entry, translated_text)

    logger.info("Report translation via Sarvam succeeded using %d chunk(s)", len(chunks))
    return translated_payload


def translate_report(report_dict: dict[str, Any], target_lang: str) -> dict[str, Any]:
    """Translate report text without blocking safety output on failures."""
    normalized_target = _normalize_code(target_lang)
    if normalized_target == "en-IN":
        return copy.deepcopy(report_dict)

    translation_payload = _build_report_translation_payload(report_dict)
    translated_payload = _translate_report_payload_sarvam(translation_payload, normalized_target)
    return _apply_report_translation(report_dict, translated_payload)


def _lookup_herb_by_regional_name(name: str) -> str:
    driver = get_driver()
    with driver.session() as session:
        record = session.run(
            """
            MATCH (h:Herb)
            WHERE toLower(coalesce(h.hindi_name, '')) = toLower($name)
               OR toLower(coalesce(h.tamil_name, '')) = toLower($name)
               OR toLower(coalesce(h.telugu_name, '')) = toLower($name)
               OR toLower(coalesce(h.kannada_name, '')) = toLower($name)
            RETURN h.name AS name
            LIMIT 1
            """,
            name=name.strip(),
        ).single()
    return str(record["name"]) if record else ""


def translate_herb_to_english(herb_name: str, source_lang: str) -> str:
    """Translate a regional herb name to English for graph lookup."""
    if not herb_name.strip():
        return ""

    existing = resolve_herb_name(herb_name)
    if existing.found:
        return existing.name

    regional_match = _lookup_herb_by_regional_name(herb_name)
    if regional_match:
        return regional_match

    translated = translate(herb_name, _normalize_code(source_lang, detect_language(herb_name)), "en-IN")
    if translated.endswith("[Translation unavailable]"):
        return herb_name

    resolved = resolve_herb_name(translated)
    return resolved.name if resolved.found else translated
