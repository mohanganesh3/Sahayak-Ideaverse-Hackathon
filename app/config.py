"""Central configuration – loads all env vars and exposes app-wide constants."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ── Load .env from project root ──────────────────────────────────────────────
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_ENV_PATH)

# ── Sarvam AI ────────────────────────────────────────────────────────────────
SARVAM_API_KEY: str = os.getenv("SARVAM_API_KEY", "")

# ── OpenAI ───────────────────────────────────────────────────────────────────
OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

# ── Google Gemini ────────────────────────────────────────────────────────────
def _collect_gemini_api_keys() -> tuple[str, ...]:
    keys: list[str] = []
    seen: set[str] = set()

    for env_name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"):
        value = os.getenv(env_name, "").strip()
        if value and value not in seen:
            seen.add(value)
            keys.append(value)

    extra_keys = os.getenv("GEMINI_API_KEYS", "")
    for value in extra_keys.split(","):
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            keys.append(cleaned)

    return tuple(keys)


GEMINI_API_KEYS: tuple[str, ...] = _collect_gemini_api_keys()
GEMINI_API_KEY: str = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else ""

# ── Groq ─────────────────────────────────────────────────────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

# ── Google Cloud Vision ──────────────────────────────────────────────────────
GOOGLE_CLOUD_VISION_KEY: str = os.getenv("GOOGLE_CLOUD_VISION_KEY", "")

# ── OpenFDA ──────────────────────────────────────────────────────────────────
OPENFDA_API_KEY: str = os.getenv("OPENFDA_API_KEY", "")

# ── Neo4j ────────────────────────────────────────────────────────────────────
NEO4J_URI: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER: str = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD: str = os.getenv("NEO4J_PASSWORD", "")

# ── App Settings ─────────────────────────────────────────────────────────────
DEFAULT_LANGUAGE: str = os.getenv("DEFAULT_LANGUAGE", "en")
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

# ── Supported Languages ─────────────────────────────────────────────────────
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en": "English",
    "hi": "हिन्दी",
    "ta": "தமிழ்",
    "te": "తెలుగు",
    "kn": "ಕನ್ನಡ",
    "ml": "മലയാളം",
    "mr": "मराठी",
    "bn": "বাংলা",
    "gu": "ગુજરાતી",
    "pa": "ਪੰਜਾਬੀ",
}

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR: Path = Path(os.getenv("DATA_DIR", "/data"))
