"""Neo4j driver singleton."""

from __future__ import annotations

from neo4j import Driver, GraphDatabase

from app.config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER

_driver: Driver | None = None


def get_driver() -> Driver:
    """Return a singleton Neo4j driver instance.

    Creates the driver on first call and reuses it afterwards.

    Returns:
        Active ``neo4j.Driver``.
    """
    global _driver
    if _driver is None:
        _driver = GraphDatabase.driver(
            NEO4J_URI,
            auth=(NEO4J_USER, NEO4J_PASSWORD),
        )
    return _driver


def close_driver() -> None:
    """Close the Neo4j driver and reset the singleton."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def verify_connectivity() -> bool:
    """Check that Neo4j is reachable.

    Returns:
        ``True`` if the driver can reach the server.
    """
    try:
        get_driver().verify_connectivity()
        return True
    except Exception:
        return False
