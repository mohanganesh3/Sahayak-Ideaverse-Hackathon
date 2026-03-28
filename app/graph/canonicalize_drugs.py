"""Canonicalize Drug nodes with RxNorm enrichment and synonym-based deduplication."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from neo4j import Driver, GraphDatabase, ManagedTransaction

LOGGER = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 100
DEFAULT_RXCUI_WORKERS = 12
DEFAULT_NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
DEFAULT_NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
DEFAULT_NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
DEFAULT_NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")
DEFAULT_CHECKPOINT_PATH = Path(__file__).resolve().parents[1] / "data" / "canonicalize_drugs_checkpoint.json"

RXNORM_BASE = "https://rxnav.nlm.nih.gov/REST"
RXNORM_RXCUI_URL = RXNORM_BASE + "/rxcui.json?search=2&name={name}"
RXNORM_APPROX_URL = RXNORM_BASE + "/approximateTerm.json?term={name}&maxEntries=1"
RXNORM_ALLRELATED_URL = RXNORM_BASE + "/rxcui/{rxcui}/allrelated.json"
RXCLASS_URL = RXNORM_BASE + "/rxclass/class/byRxcui.json?rxcui={rxcui}&relaSource={source}"
REQUEST_HEADERS = {
    "User-Agent": "sahayak-canonicalize-drugs/1.0",
    "Accept": "application/json",
}
REQUEST_DELAY_SECONDS = 0.05

_WHITESPACE_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_DOSAGE_FORM_OR_STRENGTH_RE = re.compile(
    r"\b(tablet|capsule|syrup|solution|suspension|cream|gel|ointment|patch|injection|"
    r"pack|kit|oral|topical|nasal|rectal|mg|mcg|ml|iu|meq|%)\b",
    re.IGNORECASE,
)

FETCH_DRUGS_NEEDING_RXCUI_QUERY = """
MATCH (d:Drug)
WHERE d.rxcui IS NULL OR trim(coalesce(d.rxcui, '')) = ''
WITH d,
     COUNT { (d)--() } AS degree,
     CASE
       WHEN d.generic_name =~ '^[A-Za-z][A-Za-z \\-]{0,80}$' THEN 0
       ELSE 1
     END AS complexity
RETURN d.generic_name AS generic_name
ORDER BY complexity ASC, degree DESC, generic_name
"""

UPDATE_RXCUI_BATCH_QUERY = """
UNWIND $rows AS row
MATCH (d:Drug {generic_name: row.generic_name})
SET d.rxcui = row.rxcui,
    d.rxcui_match_type = row.match_type,
    d.rxcui_lookup_name = row.lookup_name
"""

FETCH_DISTINCT_RXCUI_GROUPS_QUERY = """
MATCH (d:Drug)
WHERE d.rxcui IS NOT NULL AND trim(coalesce(d.rxcui, '')) <> ''
RETURN d.rxcui AS rxcui,
       collect({
         generic_name: d.generic_name,
         synonyms: coalesce(d.synonyms, []),
         canonical_name: coalesce(d.canonical_name, ''),
         drug_class: coalesce(d.drug_class, ''),
         atc_code: coalesce(d.atc_code, '')
       }) AS drugs
ORDER BY rxcui
"""

UPDATE_SYNONYMS_QUERY = """
MATCH (d:Drug)
WHERE d.rxcui = $rxcui
SET d.synonyms = $synonyms,
    d.canonical_name = $canonical_name
"""

UPDATE_CLASS_QUERY = """
MATCH (d:Drug)
WHERE d.rxcui = $rxcui
  AND (d.drug_class IS NULL OR trim(coalesce(d.drug_class, '')) = '')
SET d.drug_class = $drug_class,
    d.atc_code = CASE
        WHEN $atc_code IS NULL OR trim($atc_code) = '' THEN d.atc_code
        ELSE $atc_code
    END
RETURN count(d) AS updated
"""

FETCH_DUPLICATE_GROUPS_QUERY = """
MATCH (d:Drug)
WHERE d.rxcui IS NOT NULL AND trim(coalesce(d.rxcui, '')) <> ''
WITH d.rxcui AS rxcui, d, COUNT { (d)--() } AS degree
ORDER BY rxcui, degree DESC, d.generic_name ASC
WITH rxcui, collect({
  node_id: id(d),
  generic_name: d.generic_name,
  degree: degree,
  synonyms: coalesce(d.synonyms, []),
  canonical_name: coalesce(d.canonical_name, ''),
  drug_class: coalesce(d.drug_class, ''),
  atc_code: coalesce(d.atc_code, '')
}) AS nodes
WHERE size(nodes) > 1
RETURN rxcui, nodes
ORDER BY rxcui
"""

MERGE_DUPLICATE_QUERY = """
MATCH (primary:Drug) WHERE id(primary) = $primary_id
MATCH (secondary:Drug) WHERE id(secondary) = $secondary_id
CALL apoc.refactor.mergeNodes(
  [primary, secondary],
  {
    properties: 'discard',
    mergeRels: false,
    produceSelfRef: false,
    preserveExistingSelfRels: false,
    singleElementAsArray: false
  }
) YIELD node
SET node.generic_name = $primary_name,
    node.rxcui = $rxcui,
    node.canonical_name = CASE
        WHEN $canonical_name IS NULL OR trim($canonical_name) = '' THEN node.canonical_name
        ELSE $canonical_name
    END,
    node.synonyms = $synonyms,
    node.drug_class = CASE
        WHEN $drug_class IS NULL OR trim($drug_class) = '' THEN node.drug_class
        ELSE $drug_class
    END,
    node.atc_code = CASE
        WHEN $atc_code IS NULL OR trim($atc_code) = '' THEN node.atc_code
        ELSE $atc_code
    END
RETURN id(node) AS node_id, node.generic_name AS generic_name
"""

CREATE_DRUG_SYNONYM_FULLTEXT_INDEX = """
CREATE FULLTEXT INDEX drug_synonym_fulltext IF NOT EXISTS
FOR (d:Drug) ON EACH [d.generic_name, d.synonyms]
"""

CLEANUP_RELATIONSHIP_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "cleanup_non_ddinter_relationships",
        """
        MATCH ()-[r:INTERACTS_WITH]-()
        WHERE r.source <> 'ddinter'
          AND (r.ddinter_id_a IS NOT NULL OR r.ddinter_id_b IS NOT NULL)
        REMOVE r.ddinter_id_a, r.ddinter_id_b
        RETURN count(r) AS cleaned
        """,
    ),
    (
        "cleanup_non_primekg_relationships",
        """
        MATCH ()-[r:INTERACTS_WITH]-()
        WHERE r.source <> 'primekg'
          AND (
            r.primekg_relation IS NOT NULL
            OR r.display_relation IS NOT NULL
            OR r.drug_a_id IS NOT NULL
            OR r.drug_b_id IS NOT NULL
          )
        REMOVE r.primekg_relation, r.display_relation, r.drug_a_id, r.drug_b_id
        RETURN count(r) AS cleaned
        """,
    ),
)

VERIFY_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "drugs_with_rxcui",
        "MATCH (d:Drug) WHERE d.rxcui IS NOT NULL AND d.rxcui <> '' RETURN count(d) AS count",
    ),
    (
        "drugs_with_drug_class",
        "MATCH (d:Drug) WHERE d.drug_class IS NOT NULL AND d.drug_class <> '' RETURN count(d) AS count",
    ),
    (
        "drugs_with_synonyms",
        "MATCH (d:Drug) WHERE d.synonyms IS NOT NULL RETURN count(d) AS count",
    ),
    (
        "aspirin_warfarin_via_synonyms",
        """
        MATCH (a:Drug)-[r:INTERACTS_WITH]-(b:Drug)
        WHERE ANY(s IN coalesce(a.synonyms, []) WHERE toLower(s) = 'aspirin')
          AND ANY(s IN coalesce(b.synonyms, []) WHERE toLower(s) = 'warfarin')
        RETURN a.generic_name AS drug_a, b.generic_name AS drug_b, r.severity AS severity, r.source AS source
        LIMIT 5
        """,
    ),
)


@dataclass(frozen=True, slots=True)
class RxcuiResult:
    generic_name: str
    rxcui: str | None
    match_type: str
    lookup_name: str | None = None


class RateLimiter:
    """Simple process-local rate limiter."""

    def __init__(self, delay_seconds: float) -> None:
        self._delay_seconds = delay_seconds
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
                now = time.monotonic()
            self._next_allowed = max(now, self._next_allowed) + self._delay_seconds


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    cleaned = _WHITESPACE_RE.sub(" ", value.replace("\x00", " ").replace("\xa0", " ")).strip()
    return cleaned or None


def _normalize_lookup_key(value: str | None) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    return _NON_ALNUM_RE.sub(" ", cleaned.casefold()).strip()


def _chunked(items: list[Any], size: int) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def _load_checkpoint(path: Path) -> dict[str, set[str]]:
    if not path.exists():
        return {
            "rxcui_processed": set(),
            "synonyms_processed": set(),
            "class_processed": set(),
            "merged_groups": set(),
        }

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return {
        "rxcui_processed": set(payload.get("rxcui_processed", [])),
        "synonyms_processed": set(payload.get("synonyms_processed", [])),
        "class_processed": set(payload.get("class_processed", [])),
        "merged_groups": set(payload.get("merged_groups", [])),
    }


def _save_checkpoint(path: Path, checkpoint: dict[str, set[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = {key: sorted(values) for key, values in checkpoint.items()}
    with path.open("w", encoding="utf-8") as handle:
        json.dump(serializable, handle, indent=2, sort_keys=True)


def _request_json(url: str, rate_limiter: RateLimiter) -> dict[str, Any]:
    rate_limiter.wait()
    request = urllib.request.Request(url, headers=REQUEST_HEADERS)
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def _sequence_ratio(a: str, b: str) -> float:
    return SequenceMatcher(a=a.casefold(), b=b.casefold()).ratio()


def _should_try_fuzzy(query: str) -> bool:
    normalized_query = _normalize_lookup_key(query)
    if not normalized_query:
        return False
    if any(char.isdigit() for char in query):
        return False
    if any(char in query for char in "()[]{}+/\\,"):
        return False
    tokens = normalized_query.split()
    if not 1 <= len(tokens) <= 3:
        return False
    return True


def _acceptable_fuzzy_match(query: str, candidate_name: str | None) -> bool:
    normalized_query = _normalize_lookup_key(query)
    normalized_candidate = _normalize_lookup_key(candidate_name)
    if not normalized_query or not normalized_candidate:
        return False
    query_tokens = normalized_query.split()
    candidate_tokens = normalized_candidate.split()
    if len(query_tokens) > 1 and len(candidate_tokens) < len(query_tokens):
        return False
    if normalized_query == normalized_candidate:
        return True
    if normalized_query in normalized_candidate or normalized_candidate in normalized_query:
        return True
    if not query_tokens or not candidate_tokens:
        return False
    if query_tokens[0][:4] != candidate_tokens[0][:4]:
        return False
    query_token_set = set(query_tokens)
    candidate_token_set = set(candidate_tokens)
    if query_token_set and candidate_token_set:
        overlap = len(query_token_set & candidate_token_set) / len(query_token_set | candidate_token_set)
        if overlap >= 0.6:
            return True
    return _sequence_ratio(normalized_query, normalized_candidate) >= 0.9


def _fetch_rxcui_for_name(generic_name: str, rate_limiter: RateLimiter) -> RxcuiResult:
    exact_url = RXNORM_RXCUI_URL.format(name=urllib.parse.quote(generic_name))
    try:
        payload = _request_json(exact_url, rate_limiter)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        LOGGER.warning("RxNorm exact lookup failed for %s: %s", generic_name, exc)
        payload = {}

    exact_candidates = payload.get("idGroup", {}).get("rxnormId") or []
    if exact_candidates:
        rxcui = str(exact_candidates[0])
        return RxcuiResult(
            generic_name=generic_name,
            rxcui=rxcui,
            match_type="exact",
            lookup_name=generic_name,
        )

    if not _should_try_fuzzy(generic_name):
        return RxcuiResult(generic_name=generic_name, rxcui=None, match_type="not_found")

    fuzzy_url = RXNORM_APPROX_URL.format(name=urllib.parse.quote(generic_name))
    try:
        fuzzy_payload = _request_json(fuzzy_url, rate_limiter)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        LOGGER.warning("RxNorm fuzzy lookup failed for %s: %s", generic_name, exc)
        return RxcuiResult(generic_name=generic_name, rxcui=None, match_type="not_found")

    candidates = fuzzy_payload.get("approximateGroup", {}).get("candidate") or []
    if not candidates:
        return RxcuiResult(generic_name=generic_name, rxcui=None, match_type="not_found")

    for candidate in candidates:
        candidate_name = _clean_text(candidate.get("name"))
        candidate_rxcui = _clean_text(candidate.get("rxcui"))
        if candidate_rxcui and _acceptable_fuzzy_match(generic_name, candidate_name):
            return RxcuiResult(
                generic_name=generic_name,
                rxcui=candidate_rxcui,
                match_type="fuzzy",
                lookup_name=candidate_name or generic_name,
            )

    return RxcuiResult(generic_name=generic_name, rxcui=None, match_type="not_found")


def _safe_rxnorm_alias(raw_name: str | None, canonical_name: str, tty: str) -> str | None:
    cleaned = _clean_text(raw_name)
    if not cleaned:
        return None
    if "/" in cleaned:
        return None
    if any(char.isdigit() for char in cleaned):
        return None
    if _DOSAGE_FORM_OR_STRENGTH_RE.search(cleaned):
        return None
    normalized = _normalize_lookup_key(cleaned)
    canonical_normalized = _normalize_lookup_key(canonical_name)
    if not normalized or not canonical_normalized:
        return None
    if tty in {"IN", "PIN"}:
        return cleaned
    if tty == "BN" and canonical_normalized in normalized:
        return cleaned
    return None


def _extract_synonyms_and_canonical_name(
    rxcui: str,
    payload: dict[str, Any],
    observed_names: Iterable[str],
) -> tuple[str, list[str]]:
    observed = [_clean_text(name) for name in observed_names]
    observed = [name for name in observed if name]
    concept_groups = payload.get("allRelatedGroup", {}).get("conceptGroup") or []

    canonical_name = next(
        (
            _clean_text(concept.get("name"))
            for group in concept_groups
            if group.get("tty") in {"IN", "PIN"}
            for concept in group.get("conceptProperties", []) or []
            if _clean_text(concept.get("name"))
        ),
        observed[0] if observed else rxcui,
    )

    synonyms: set[str] = set(observed)
    synonyms.add(canonical_name)

    for group in concept_groups:
        tty = _clean_text(group.get("tty")) or ""
        for concept in group.get("conceptProperties", []) or []:
            for field in ("name", "synonym"):
                alias = _safe_rxnorm_alias(concept.get(field), canonical_name, tty)
                if alias:
                    synonyms.add(alias)

    ordered = sorted(synonyms, key=lambda value: value.casefold())
    return canonical_name, ordered


def _pick_best_class(
    payload: dict[str, Any],
    *,
    preferred_source: str,
) -> tuple[str | None, str | None]:
    classes = payload.get("rxclassDrugInfoList", {}).get("rxclassDrugInfo") or []
    best_name: str | None = None
    best_id: str | None = None
    best_rank: tuple[int, int] | None = None

    for item in classes:
        class_info = item.get("rxclassMinConceptItem") or {}
        class_name = _clean_text(class_info.get("className"))
        class_id = _clean_text(class_info.get("classId"))
        class_type = _clean_text(class_info.get("classType")) or ""
        if not class_name:
            continue

        if preferred_source == "ATC":
            class_depth = len(class_id or "")
            # Prefer ATC level 4 (5 chars), then level 3 (4 chars), then everything else.
            if class_depth >= 5:
                rank = (0, -class_depth)
            elif class_depth == 4:
                rank = (1, -class_depth)
            else:
                rank = (2, -class_depth)
        else:
            # Prefer more specific FDASPL classes.
            specificity = 0 if "EPC" in class_type else 1
            rank = (specificity, 0)

        if best_rank is None or rank < best_rank:
            best_rank = rank
            best_name = class_name
            best_id = class_id

    return best_name, best_id


def _fetch_synonyms(rxcui: str, rate_limiter: RateLimiter, observed_names: Iterable[str]) -> tuple[str, list[str]]:
    url = RXNORM_ALLRELATED_URL.format(rxcui=urllib.parse.quote(rxcui))
    payload = _request_json(url, rate_limiter)
    return _extract_synonyms_and_canonical_name(rxcui, payload, observed_names)


def _fetch_class(rxcui: str, rate_limiter: RateLimiter) -> tuple[str | None, str | None]:
    atc_url = RXCLASS_URL.format(rxcui=urllib.parse.quote(rxcui), source="ATC")
    try:
        atc_payload = _request_json(atc_url, rate_limiter)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        LOGGER.warning("RxClass ATC lookup failed for RxCUI %s: %s", rxcui, exc)
        atc_payload = {}

    class_name, class_id = _pick_best_class(atc_payload, preferred_source="ATC")
    if class_name:
        return class_name, class_id

    fda_url = RXCLASS_URL.format(rxcui=urllib.parse.quote(rxcui), source="FDASPL")
    try:
        fda_payload = _request_json(fda_url, rate_limiter)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        LOGGER.warning("RxClass FDASPL lookup failed for RxCUI %s: %s", rxcui, exc)
        return None, None

    return _pick_best_class(fda_payload, preferred_source="FDASPL")


def _fetch_drugs_needing_rxcui(driver: Driver, database: str) -> list[str]:
    with driver.session(database=database) as session:
        return [record["generic_name"] for record in session.run(FETCH_DRUGS_NEEDING_RXCUI_QUERY)]


def _write_batch(tx: ManagedTransaction, query: str, **params: Any) -> None:
    tx.run(query, **params).consume()


def _apply_rxcui_updates(driver: Driver, database: str, results: list[RxcuiResult]) -> int:
    rows = [
        {
            "generic_name": result.generic_name,
            "rxcui": result.rxcui,
            "match_type": result.match_type,
            "lookup_name": result.lookup_name,
        }
        for result in results
        if result.rxcui
    ]
    if not rows:
        return 0
    with driver.session(database=database) as session:
        session.execute_write(_write_batch, UPDATE_RXCUI_BATCH_QUERY, rows=rows)
    return len(rows)


def _fetch_rxcui_groups(driver: Driver, database: str) -> list[dict[str, Any]]:
    with driver.session(database=database) as session:
        return [record.data() for record in session.run(FETCH_DISTINCT_RXCUI_GROUPS_QUERY)]


def _update_synonyms(driver: Driver, database: str, rxcui: str, canonical_name: str, synonyms: list[str]) -> None:
    with driver.session(database=database) as session:
        session.execute_write(
            _write_batch,
            UPDATE_SYNONYMS_QUERY,
            rxcui=rxcui,
            canonical_name=canonical_name,
            synonyms=synonyms,
        )


def _update_class(driver: Driver, database: str, rxcui: str, drug_class: str, atc_code: str | None) -> int:
    with driver.session(database=database) as session:
        record = session.execute_write(
            lambda tx: tx.run(
                UPDATE_CLASS_QUERY,
                rxcui=rxcui,
                drug_class=drug_class,
                atc_code=atc_code,
            ).single()
        )
    return int(record["updated"])


def _fetch_duplicate_groups(driver: Driver, database: str) -> list[dict[str, Any]]:
    with driver.session(database=database) as session:
        return [record.data() for record in session.run(FETCH_DUPLICATE_GROUPS_QUERY)]


def _flatten_synonyms(values: Iterable[Any]) -> set[str]:
    synonyms: set[str] = set()
    for value in values:
        if isinstance(value, list):
            for item in value:
                cleaned = _clean_text(item)
                if cleaned:
                    synonyms.add(cleaned)
        else:
            cleaned = _clean_text(value)
            if cleaned:
                synonyms.add(cleaned)
    return synonyms


def _merge_duplicate_group(driver: Driver, database: str, rxcui: str, nodes: list[dict[str, Any]]) -> int:
    if len(nodes) <= 1:
        return 0

    primary = nodes[0]
    merged_count = 0
    primary_synonyms = _flatten_synonyms(primary.get("synonyms", []))
    primary_synonyms.add(primary["generic_name"])
    primary_canonical_name = _clean_text(primary.get("canonical_name")) or primary["generic_name"]
    primary_drug_class = _clean_text(primary.get("drug_class"))
    primary_atc_code = _clean_text(primary.get("atc_code"))

    for secondary in nodes[1:]:
        secondary_name = secondary["generic_name"]
        primary_synonyms.add(secondary_name)
        primary_synonyms.update(_flatten_synonyms(secondary.get("synonyms", [])))
        primary_drug_class = primary_drug_class or _clean_text(secondary.get("drug_class"))
        primary_atc_code = primary_atc_code or _clean_text(secondary.get("atc_code"))
        secondary_canonical_name = _clean_text(secondary.get("canonical_name"))
        if not primary_canonical_name and secondary_canonical_name:
            primary_canonical_name = secondary_canonical_name

        with driver.session(database=database) as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    MERGE_DUPLICATE_QUERY,
                    primary_id=primary["node_id"],
                    secondary_id=secondary["node_id"],
                    primary_name=primary["generic_name"],
                    rxcui=rxcui,
                    canonical_name=primary_canonical_name,
                    synonyms=sorted(primary_synonyms, key=lambda value: value.casefold()),
                    drug_class=primary_drug_class,
                    atc_code=primary_atc_code,
                ).single()
            )

        primary["node_id"] = record["node_id"]
        merged_count += 1
        LOGGER.info(
            "Merged %s into %s (rxcui: %s).",
            secondary_name,
            primary["generic_name"],
            rxcui,
        )

    return merged_count


def _create_synonym_index(driver: Driver, database: str) -> None:
    with driver.session(database=database) as session:
        session.run(CREATE_DRUG_SYNONYM_FULLTEXT_INDEX).consume()


def _cleanup_collided_relationships(driver: Driver, database: str) -> dict[str, int]:
    cleaned: dict[str, int] = {}
    with driver.session(database=database) as session:
        for name, query in CLEANUP_RELATIONSHIP_QUERIES:
            record = session.execute_write(lambda tx, q=query: tx.run(q).single())
            cleaned[name] = int(record["cleaned"])
    return cleaned


def _run_verification(driver: Driver, database: str) -> dict[str, Any]:
    results: dict[str, Any] = {}
    with driver.session(database=database) as session:
        for name, query in VERIFY_QUERIES:
            records = [record.data() for record in session.run(query)]
            results[name] = records
    return results


def canonicalize_drugs(
    driver: Driver,
    *,
    database: str,
    batch_size: int,
    checkpoint_path: Path,
    rxcui_workers: int,
) -> dict[str, Any]:
    checkpoint = _load_checkpoint(checkpoint_path)
    rate_limiter = RateLimiter(REQUEST_DELAY_SECONDS)

    LOGGER.info("Starting RxCUI enrichment phase.")
    drugs_needing_rxcui = _fetch_drugs_needing_rxcui(driver, database)
    pending_drugs = [
        name
        for name in drugs_needing_rxcui
        if name not in checkpoint["rxcui_processed"]
    ]
    rxcui_updates_written = 0
    not_found_count = 0

    for batch in _chunked(pending_drugs, batch_size):
        results: list[RxcuiResult] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, rxcui_workers)) as executor:
            future_map = {
                executor.submit(_fetch_rxcui_for_name, generic_name, rate_limiter): generic_name
                for generic_name in batch
            }
            for future in concurrent.futures.as_completed(future_map):
                generic_name = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.warning("RxNorm lookup crashed for %s: %s", generic_name, exc)
                    result = RxcuiResult(generic_name=generic_name, rxcui=None, match_type="not_found")

                LOGGER.info(
                    "RxNorm lookup: drug=%s rxcui=%s match_type=%s lookup_name=%s",
                    generic_name,
                    result.rxcui or "",
                    result.match_type,
                    result.lookup_name or "",
                )
                checkpoint["rxcui_processed"].add(generic_name)
                if result.rxcui:
                    results.append(result)
                else:
                    not_found_count += 1

        rxcui_updates_written += _apply_rxcui_updates(driver, database, results)
        _save_checkpoint(checkpoint_path, checkpoint)
        LOGGER.info(
            "RxCUI enrichment progress: processed=%s/%s written=%s not_found=%s",
            f"{len(checkpoint['rxcui_processed']):,}",
            f"{len(drugs_needing_rxcui):,}",
            f"{rxcui_updates_written:,}",
            f"{not_found_count:,}",
        )

    LOGGER.info("Starting synonym enrichment phase.")
    rxcui_groups = _fetch_rxcui_groups(driver, database)
    pending_groups = [
        group for group in rxcui_groups if group["rxcui"] not in checkpoint["synonyms_processed"]
    ]
    synonyms_updated = 0

    for batch in _chunked(pending_groups, batch_size):
        for group in batch:
            rxcui = group["rxcui"]
            observed_names = [drug["generic_name"] for drug in group["drugs"]]
            observed_names.extend(
                synonym
                for drug in group["drugs"]
                for synonym in drug.get("synonyms", []) or []
            )
            try:
                canonical_name, synonyms = _fetch_synonyms(rxcui, rate_limiter, observed_names)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                LOGGER.warning("RxNorm allrelated lookup failed for RxCUI %s: %s", rxcui, exc)
                checkpoint["synonyms_processed"].add(rxcui)
                continue

            _update_synonyms(driver, database, rxcui, canonical_name, synonyms)
            checkpoint["synonyms_processed"].add(rxcui)
            synonyms_updated += len(group["drugs"])
            LOGGER.info(
                "Synonyms updated for rxcui=%s canonical_name=%s synonym_count=%s",
                rxcui,
                canonical_name,
                len(synonyms),
            )

        _save_checkpoint(checkpoint_path, checkpoint)

    LOGGER.info("Starting drug class enrichment phase.")
    class_updates = 0
    pending_class_groups = [
        group for group in rxcui_groups if group["rxcui"] not in checkpoint["class_processed"]
    ]

    for batch in _chunked(pending_class_groups, batch_size):
        for group in batch:
            rxcui = group["rxcui"]
            if any(_clean_text(drug.get("drug_class")) for drug in group["drugs"]):
                checkpoint["class_processed"].add(rxcui)
                continue

            class_name, atc_code = _fetch_class(rxcui, rate_limiter)
            if class_name:
                updated = _update_class(driver, database, rxcui, class_name, atc_code)
                class_updates += updated
                LOGGER.info(
                    "Drug class updated for rxcui=%s class=%s atc_code=%s updated_nodes=%s",
                    rxcui,
                    class_name,
                    atc_code or "",
                    updated,
                )
            checkpoint["class_processed"].add(rxcui)

        _save_checkpoint(checkpoint_path, checkpoint)

    LOGGER.info("Starting duplicate merge phase.")
    duplicate_groups = _fetch_duplicate_groups(driver, database)
    merged_nodes = 0
    for group in duplicate_groups:
        rxcui = group["rxcui"]
        if rxcui in checkpoint["merged_groups"]:
            continue
        merged_nodes += _merge_duplicate_group(driver, database, rxcui, group["nodes"])
        checkpoint["merged_groups"].add(rxcui)
        _save_checkpoint(checkpoint_path, checkpoint)

    _create_synonym_index(driver, database)
    cleanup_counts = _cleanup_collided_relationships(driver, database)
    verification = _run_verification(driver, database)
    return {
        "rxcui_updates_written": rxcui_updates_written,
        "rxcui_not_found": not_found_count,
        "synonyms_updated": synonyms_updated,
        "class_updates": class_updates,
        "merged_nodes": merged_nodes,
        "cleanup_counts": cleanup_counts,
        "verification": verification,
        "checkpoint_path": str(checkpoint_path),
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Canonicalize Drug nodes with RxNorm-based enrichment and deduplication.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--neo4j-uri", default=DEFAULT_NEO4J_URI, help="Neo4j Bolt URI.")
    parser.add_argument("--neo4j-user", default=DEFAULT_NEO4J_USER, help="Neo4j username.")
    parser.add_argument("--neo4j-password", default=DEFAULT_NEO4J_PASSWORD, help="Neo4j password.")
    parser.add_argument("--database", default=DEFAULT_NEO4J_DATABASE, help="Neo4j database name.")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE, help="Processing batch size.")
    parser.add_argument(
        "--rxcui-workers",
        type=int,
        default=DEFAULT_RXCUI_WORKERS,
        help="Parallel workers for RxCUI enrichment under the shared RxNorm rate limit.",
    )
    parser.add_argument(
        "--checkpoint-path",
        type=Path,
        default=DEFAULT_CHECKPOINT_PATH,
        help="Path to the JSON checkpoint file used for resume support.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Python logging level.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    driver = GraphDatabase.driver(args.neo4j_uri, auth=(args.neo4j_user, args.neo4j_password))
    try:
        driver.verify_connectivity()
        result = canonicalize_drugs(
            driver,
            database=args.database,
            batch_size=args.batch_size,
            checkpoint_path=args.checkpoint_path.expanduser().resolve(),
            rxcui_workers=args.rxcui_workers,
        )
        LOGGER.info("Canonicalization complete: %s", json.dumps(result, ensure_ascii=False, default=str))
    finally:
        driver.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
