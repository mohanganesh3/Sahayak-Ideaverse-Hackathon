"""Helpers for ordered Gemini API-key rotation."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from langchain_openai import ChatOpenAI
from openai import OpenAI

from app.config import GEMINI_API_KEYS

_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"


def has_gemini_keys() -> bool:
    return bool(GEMINI_API_KEYS)


def iter_gemini_openai_clients() -> Iterator[tuple[int, int, OpenAI]]:
    total = len(GEMINI_API_KEYS)
    for index, api_key in enumerate(GEMINI_API_KEYS, start=1):
        yield index, total, OpenAI(api_key=api_key, base_url=_GEMINI_BASE_URL)


def iter_gemini_chat_models(model: str, **kwargs: Any) -> Iterator[tuple[int, int, ChatOpenAI]]:
    total = len(GEMINI_API_KEYS)
    for index, api_key in enumerate(GEMINI_API_KEYS, start=1):
        yield index, total, ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=_GEMINI_BASE_URL,
            **kwargs,
        )
