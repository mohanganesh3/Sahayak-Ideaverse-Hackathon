"""Beers Criteria screening for potentially inappropriate medications in the elderly."""

from __future__ import annotations

import json

from app.config import DATA_DIR


def load_beers_criteria() -> list[dict]:
    """Load the digitised Beers Criteria from the local JSON file.

    Returns:
        List of Beers Criteria entries.
    """
    path = DATA_DIR / "beers_criteria.json"
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return []


def screen_medicines(
    drug_list: list[str],
    patient_age: int,
    patient_conditions: list[str] | None = None,
) -> list[dict]:
    """Screen a medicine list against Beers Criteria.

    Args:
        drug_list: Normalised generic drug names.
        patient_age: Patient age in years.
        patient_conditions: Active medical conditions.

    Returns:
        List of flagged entries with ``drug``, ``reason``, ``recommendation``.
    """
    # TODO: implement Beers Criteria matching logic
    raise NotImplementedError


def get_alternatives(drug_name: str) -> list[str]:
    """Suggest safer alternatives for a Beers-flagged drug.

    Args:
        drug_name: The flagged drug.

    Returns:
        List of safer alternative drug names.
    """
    # TODO: implement alternative suggestion logic
    raise NotImplementedError
