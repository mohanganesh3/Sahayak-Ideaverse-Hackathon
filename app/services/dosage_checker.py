"""Geriatric dosage verification service."""

from __future__ import annotations


def check_dosage(
    drug_name: str,
    dosage_mg: float,
    patient_age: int,
    patient_weight_kg: float | None = None,
    renal_function: str | None = None,
) -> dict:
    """Verify whether a dosage is appropriate for an elderly patient.

    Args:
        drug_name: Normalised generic drug name.
        dosage_mg: Prescribed dosage in milligrams.
        patient_age: Patient age in years.
        patient_weight_kg: Patient weight (optional).
        renal_function: eGFR category (optional).

    Returns:
        Dict with ``is_safe``, ``max_recommended``, ``warning`` keys.
    """
    # TODO: implement geriatric dosage lookup and verification
    raise NotImplementedError


def batch_check(
    prescriptions: list[dict],
    patient_age: int,
) -> list[dict]:
    """Check dosages for a list of prescriptions.

    Args:
        prescriptions: List of dicts with ``name`` and ``dosage_mg``.
        patient_age: Patient age in years.

    Returns:
        List of dosage-check result dicts.
    """
    # TODO: iterate and call check_dosage for each
    raise NotImplementedError
