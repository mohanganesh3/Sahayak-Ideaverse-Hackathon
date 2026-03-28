"""Sarvam AI speech interfaces for SAHAYAK."""

from __future__ import annotations

import io
import logging
from typing import Any

import requests

from app.config import SARVAM_API_KEY

logger = logging.getLogger(__name__)

SARVAM_STT_URL = "https://api.sarvam.ai/speech-to-text"
SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"
SUPPORTED_SARVAM_LANGUAGES = {
    "hi-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN",
    "mr-IN", "bn-IN", "gu-IN", "pa-IN", "en-IN", "auto",
}
DEFAULT_STT_MODEL = "saarika:v2.5"
DEFAULT_TTS_MODEL = "bulbul:v2"
DEFAULT_TTS_SPEAKER = "anushka"


def _sarvam_headers() -> dict[str, str]:
    if not SARVAM_API_KEY:
        raise RuntimeError("SARVAM_API_KEY is not configured")
    return {"api-subscription-key": SARVAM_API_KEY}


def _normalize_language(language: str) -> str:
    value = (language or "auto").strip()
    return value if value in SUPPORTED_SARVAM_LANGUAGES else "auto"


def speech_to_text(audio_bytes: bytes, language: str = "auto") -> dict[str, Any]:
    """Transcribe short audio clips using Sarvam speech-to-text."""
    if not audio_bytes:
        return {"transcript": "", "language": "unknown", "confidence": 0.0}

    files = {
        "file": ("audio.wav", io.BytesIO(audio_bytes), "audio/wav"),
    }
    data = {
        "model": DEFAULT_STT_MODEL,
        "with_timestamps": "false",
    }
    normalized_language = _normalize_language(language)
    if normalized_language != "auto":
        data["language_code"] = normalized_language

    response = requests.post(
        SARVAM_STT_URL,
        headers=_sarvam_headers(),
        files=files,
        data=data,
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()

    transcript = (
        payload.get("transcript")
        or payload.get("text")
        or payload.get("output")
        or ""
    )
    detected_language = (
        payload.get("language_code")
        or payload.get("language")
        or normalized_language
        or "unknown"
    )
    confidence = payload.get("confidence")
    if confidence is None:
        alternatives = payload.get("channels") or payload.get("results") or []
        if alternatives and isinstance(alternatives, list):
            first = alternatives[0]
            if isinstance(first, dict):
                confidence = first.get("confidence")
    try:
        confidence_float = float(confidence) if confidence is not None else 0.0
    except (TypeError, ValueError):
        confidence_float = 0.0

    return {
        "transcript": str(transcript).strip(),
        "language": str(detected_language).strip() or "unknown",
        "confidence": max(0.0, min(confidence_float, 1.0)),
    }


def text_to_speech(text: str, language: str, voice: str = "") -> bytes:
    """Synthesize speech using Sarvam text-to-speech.

    Returns raw audio bytes (WAV). If the API returns base64 JSON,
    the base64 string is decoded before returning.
    """
    if not text.strip():
        return b""

    normalized_language = _normalize_language(language)
    if normalized_language == "auto":
        normalized_language = "en-IN"

    speaker = voice if voice else DEFAULT_TTS_SPEAKER

    payload = {
        "inputs": [text],
        "target_language_code": normalized_language,
        "speaker": speaker,
        "model": DEFAULT_TTS_MODEL,
        "enable_preprocessing": True,
    }
    response = requests.post(
        SARVAM_TTS_URL,
        headers={**_sarvam_headers(), "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    if "application/json" not in content_type:
        return response.content

    import base64 as _b64
    payload = response.json()
    for key in ("audios", "audio", "data"):
        value = payload.get(key)
        if isinstance(value, list) and value:
            first = value[0]
            if isinstance(first, str):
                # Sarvam returns base64-encoded audio — decode it
                try:
                    return _b64.b64decode(first)
                except Exception:
                    return first.encode("utf-8")
            if isinstance(first, dict):
                blob = first.get("audio") or first.get("audio_content")
                if isinstance(blob, str):
                    try:
                        return _b64.b64decode(blob)
                    except Exception:
                        return blob.encode("utf-8")
        if isinstance(value, str):
            try:
                return _b64.b64decode(value)
            except Exception:
                return value.encode("utf-8")

    logger.warning("Sarvam TTS returned unexpected payload keys: %s", sorted(payload))
    return b""
