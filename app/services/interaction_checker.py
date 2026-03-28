"""Drug-drug and drug-herb interaction checker via Neo4j GraphRAG."""

from __future__ import annotations

from app.graph.neo4j_connection import get_driver


def check_pairwise_interactions(
    drug_list: list[str],
) -> list[dict]:
    """Query Neo4j for all pairwise interactions among the given drugs/herbs.

    Args:
        drug_list: List of normalised generic drug or herb names.

    Returns:
        List of interaction dicts with keys:
        ``drug_a``, ``drug_b``, ``severity``, ``description``, ``source``.
    """
    # TODO: implement Cypher pairwise query via get_driver()
    raise NotImplementedError


def check_single_drug(drug_name: str) -> list[dict]:
    """Return all known interactions for a single drug.

    Args:
        drug_name: Normalised generic name.

    Returns:
        List of interaction dicts.
    """
    # TODO: implement single-node neighbourhood query
    raise NotImplementedError


def get_interaction_detail(drug_a: str, drug_b: str) -> dict | None:
    """Fetch detailed interaction metadata for a specific pair.

    Args:
        drug_a: First drug name.
        drug_b: Second drug name.

    Returns:
        Interaction detail dict or ``None``.
    """
    # TODO: implement detailed pair query
    raise NotImplementedError
