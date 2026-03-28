"""Backup interaction checker using a local SQLite database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import DATA_DIR

_DB_PATH: Path = DATA_DIR / "interactions.db"


def _get_connection() -> sqlite3.Connection:
    """Return a SQLite connection to the local interactions database."""
    return sqlite3.connect(str(_DB_PATH))


def check_pairwise_interactions(drug_list: list[str]) -> list[dict]:
    """SQLite fallback for pairwise interaction lookup.

    Args:
        drug_list: List of normalised generic drug or herb names.

    Returns:
        List of interaction dicts with keys:
        ``drug_a``, ``drug_b``, ``severity``, ``description``.
    """
    # TODO: implement SQLite-based pairwise query
    raise NotImplementedError


def check_single_drug(drug_name: str) -> list[dict]:
    """SQLite fallback for single-drug interaction lookup.

    Args:
        drug_name: Normalised generic name.

    Returns:
        List of interaction dicts.
    """
    # TODO: implement SQLite single-drug query
    raise NotImplementedError
