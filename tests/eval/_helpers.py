"""Shared helpers for SAHAYAK evaluation tests and runners."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import math
import statistics
import sys
import time
from itertools import combinations
from pathlib import Path
from typing import Any, Iterable, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "app" / "data"
ARTIFACTS_DIR = PROJECT_ROOT / "artifacts" / "evaluation"
SEVERITY_ORDER = ("minor", "moderate", "major")
SEVERITY_RANK = {label: index for index, label in enumerate(SEVERITY_ORDER)}
SOURCE_PRIORITY = {
    "sentinel_curated": 100,
    "ddinter": 90,
    "ddid": 85,
    "curated_ayurveda": 80,
    "beers_2023": 75,
    "fda_ddi_table": 72,
    "published_literature": 70,
    "sider": 60,
    "onsides": 55,
    "primekg": 50,
    "twosides": 40,
}
COMMON_SALT_SUFFIXES = (
    "sodium",
    "hydrochloride",
    "hcl",
    "magnesium",
    "calcium",
    "potassium",
    "succinate",
    "tartrate",
    "phosphate",
)


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def safe_div(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def round_metric(value: float | None, digits: int = 4) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def binary_metrics(*, tp: int, fp: int | None = None, tn: int | None = None, fn: int | None = None) -> dict[str, float | int | None]:
    metrics: dict[str, float | int | None] = {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
    }
    if fn is not None:
        metrics["sensitivity"] = round_metric(safe_div(tp, tp + fn))
        metrics["recall"] = metrics["sensitivity"]
    else:
        metrics["sensitivity"] = None
        metrics["recall"] = None
    if fp is not None:
        metrics["ppv"] = round_metric(safe_div(tp, tp + fp))
        metrics["precision"] = metrics["ppv"]
    else:
        metrics["ppv"] = None
        metrics["precision"] = None
    if tn is not None and fp is not None:
        metrics["specificity"] = round_metric(safe_div(tn, tn + fp))
    else:
        metrics["specificity"] = None
    if tn is not None and fn is not None:
        metrics["npv"] = round_metric(safe_div(tn, tn + fn))
    else:
        metrics["npv"] = None
    precision = metrics["precision"]
    recall = metrics["recall"]
    if precision is not None and recall is not None and (precision + recall) > 0:
        metrics["f1"] = round_metric((2 * precision * recall) / (precision + recall))
    else:
        metrics["f1"] = None
    return metrics


def quadratic_weighted_kappa(
    expected: Sequence[str],
    predicted: Sequence[str],
    ordered_labels: Sequence[str] = SEVERITY_ORDER,
) -> float | None:
    if not expected or not predicted or len(expected) != len(predicted):
        return None

    labels = tuple(ordered_labels)
    label_to_index = {label: index for index, label in enumerate(labels)}
    usable_pairs = [
        (label_to_index[e], label_to_index[p])
        for e, p in zip(expected, predicted)
        if e in label_to_index and p in label_to_index
    ]
    if not usable_pairs:
        return None

    size = len(labels)
    if size <= 1:
        return None

    observed = [[0 for _ in range(size)] for _ in range(size)]
    hist_expected = [0 for _ in range(size)]
    hist_predicted = [0 for _ in range(size)]
    for exp_index, pred_index in usable_pairs:
        observed[exp_index][pred_index] += 1
        hist_expected[exp_index] += 1
        hist_predicted[pred_index] += 1

    total = float(len(usable_pairs))
    observed_weighted = 0.0
    expected_weighted = 0.0
    for i in range(size):
        for j in range(size):
            weight = ((i - j) / (size - 1)) ** 2
            observed_weighted += weight * observed[i][j]
            expected_weighted += weight * ((hist_expected[i] * hist_predicted[j]) / total)

    if math.isclose(expected_weighted, 0.0):
        return None
    return round_metric(1.0 - (observed_weighted / expected_weighted))


def percentile(values: Sequence[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * pct
    lower = math.floor(rank)
    upper = math.ceil(rank)
    if lower == upper:
        return ordered[int(rank)]
    fraction = rank - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def latency_stats_ms(samples_ms: Sequence[float]) -> dict[str, float | int | None]:
    if not samples_ms:
        return {
            "count": 0,
            "mean_ms": None,
            "median_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "p99_ms": None,
            "max_ms": None,
        }
    vals = [float(v) for v in samples_ms]
    return {
        "count": len(vals),
        "mean_ms": round_metric(statistics.fmean(vals), 2),
        "median_ms": round_metric(statistics.median(vals), 2),
        "p50_ms": round_metric(percentile(vals, 0.50), 2),
        "p95_ms": round_metric(percentile(vals, 0.95), 2),
        "p99_ms": round_metric(percentile(vals, 0.99), 2),
        "max_ms": round_metric(max(vals), 2),
    }


def hallucination_rate(total_claims: int, unsupported_claims: int) -> float | None:
    return round_metric(safe_div(unsupported_claims, total_claims))


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def run_async(awaitable: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise RuntimeError("run_async() cannot be called from an active event loop")


def dataclass_to_dict(value: Any) -> Any:
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    return value


def neo4j_available() -> bool:
    from app.graph.neo4j_connection import verify_connectivity

    return verify_connectivity()


def run_cypher(query: str, **params: Any) -> list[dict[str, Any]]:
    from app.graph.neo4j_connection import get_driver

    with get_driver().session() as session:
        return [record.data() for record in session.run(query, **params)]


def normalize_severity(value: str | None) -> str:
    cleaned = (value or "").strip().lower()
    if cleaned in {"critical", "contraindicated"}:
        return "major"
    if cleaned in {"caution"}:
        return "moderate"
    if cleaned in {"info"}:
        return "minor"
    if cleaned in {"major", "moderate", "minor", "unknown"}:
        return cleaned
    return "unknown"


def severity_from_score(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 8.0:
        return "major"
    if score >= 5.0:
        return "moderate"
    return "minor"


def canonical_drug_name(name: str) -> str:
    from app.graph.query_engine import resolve_drug_name

    resolved = resolve_drug_name(name)
    if resolved.found and resolved.generic_name:
        return resolved.generic_name
    lowered = name.strip().lower()
    tokens = lowered.split()
    if len(tokens) > 1 and tokens[-1] in COMMON_SALT_SUFFIXES:
        stripped = " ".join(tokens[:-1])
        resolved = resolve_drug_name(stripped)
        if resolved.found and resolved.generic_name:
            return resolved.generic_name
        return stripped
    return name.strip()


def canonical_drug_set(name_or_combo: str) -> set[str]:
    parts = [part.strip() for part in name_or_combo.replace("/", "+").replace(",", "+").split("+")]
    canon = {canonical_drug_name(part).lower() for part in parts if part.strip()}
    return {item for item in canon if item}


def jaccard_similarity(left: Iterable[str], right: Iterable[str]) -> float | None:
    left_set = {item.strip().lower() for item in left if item}
    right_set = {item.strip().lower() for item in right if item}
    if not left_set and not right_set:
        return 1.0
    union = left_set | right_set
    if not union:
        return None
    return round_metric(len(left_set & right_set) / len(union))


def best_source(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    return sorted(
        rows,
        key=lambda row: (
            SEVERITY_RANK.get(normalize_severity(row.get("severity")), -1),
            SOURCE_PRIORITY.get(str(row.get("source", "")).lower(), 0),
        ),
        reverse=True,
    )[0]


def best_interaction_support(drug_a: str, drug_b: str) -> dict[str, Any]:
    from app.graph.query_engine import check_direct_interactions, check_indirect_interactions

    direct = [
        dataclass_to_dict(item)
        for item in check_direct_interactions([drug_a, drug_b])
        if {canonical_drug_name(item.drug_a).lower(), canonical_drug_name(item.drug_b).lower()}
        == {canonical_drug_name(drug_a).lower(), canonical_drug_name(drug_b).lower()}
    ]
    if direct:
        top = best_source(direct)
        return {
            "supported": True,
            "kind": "direct",
            "severity": normalize_severity(top.get("severity")),
            "source": top.get("source"),
            "evidence": top,
        }

    indirect = [
        dataclass_to_dict(item)
        for item in check_indirect_interactions([drug_a, drug_b], patient_age=72)
        if {canonical_drug_name(item.drug_a).lower(), canonical_drug_name(item.drug_b).lower()}
        == {canonical_drug_name(drug_a).lower(), canonical_drug_name(drug_b).lower()}
    ]
    if indirect:
        top = sorted(indirect, key=lambda row: float(row.get("severity_score", 0.0)), reverse=True)[0]
        return {
            "supported": True,
            "kind": "indirect",
            "severity": severity_from_score(float(top.get("severity_score", 0.0))),
            "source": top.get("source_layer"),
            "evidence": top,
        }

    return {
        "supported": False,
        "kind": None,
        "severity": "unknown",
        "source": None,
        "evidence": None,
    }


def best_herb_support(herb_name: str, drug_name: str) -> dict[str, Any]:
    from app.graph.query_engine import check_herb_drug_interactions

    rows = [
        dataclass_to_dict(item)
        for item in check_herb_drug_interactions([herb_name], [drug_name])
        if canonical_drug_name(item.drug).lower() == canonical_drug_name(drug_name).lower()
    ]
    if rows:
        top = best_source(rows)
        return {
            "supported": True,
            "kind": "herb_drug",
            "severity": normalize_severity(top.get("severity")),
            "source": top.get("source"),
            "evidence": top,
        }
    return {
        "supported": False,
        "kind": None,
        "severity": "unknown",
        "source": None,
        "evidence": None,
    }


def is_known_herb(name: str) -> bool:
    rows = run_cypher(
        """
        MATCH (h:Herb)
        WHERE toLower(coalesce(h.name, '')) = toLower($name)
           OR toLower(coalesce(h.hindi_name, '')) = toLower($name)
           OR toLower(coalesce(h.tamil_name, '')) = toLower($name)
           OR toLower(coalesce(h.telugu_name, '')) = toLower($name)
           OR toLower(coalesce(h.kannada_name, '')) = toLower($name)
           OR toLower(coalesce(h.scientific_name, '')) = toLower($name)
        RETURN count(h) AS count
        """,
        name=name,
    )
    return bool(rows and rows[0]["count"] > 0)


def verify_report_findings(report_payload: dict[str, Any]) -> dict[str, Any]:
    report = report_payload.get("english", report_payload)
    findings = report.get("findings", []) or []
    supported = 0
    unsupported = 0
    details: list[dict[str, Any]] = []

    for finding in findings:
        medicines = [str(item).strip() for item in finding.get("medicines", []) if str(item).strip()]
        if len(medicines) < 2:
            details.append(
                {
                    "title": finding.get("title"),
                    "status": "ignored_singleton",
                    "medicines": medicines,
                }
            )
            continue

        match: dict[str, Any] | None = None
        for left, right in combinations(medicines, 2):
            left_is_herb = is_known_herb(left)
            right_is_herb = is_known_herb(right)
            if left_is_herb:
                herb_support = best_herb_support(left, right)
                if herb_support["supported"]:
                    match = {"pair": [left, right], **herb_support}
                    break
            if right_is_herb:
                herb_support = best_herb_support(right, left)
                if herb_support["supported"]:
                    match = {"pair": [right, left], **herb_support}
                    break
            ddi_support = best_interaction_support(left, right)
            if ddi_support["supported"]:
                match = {"pair": [left, right], **ddi_support}
                break

        if match:
            supported += 1
            details.append(
                {
                    "title": finding.get("title"),
                    "status": "supported",
                    "medicines": medicines,
                    "support": match,
                }
            )
        else:
            unsupported += 1
            details.append(
                {
                    "title": finding.get("title"),
                    "status": "unsupported",
                    "medicines": medicines,
                }
            )

    total_scored = supported + unsupported
    return {
        "supported_findings": supported,
        "unsupported_findings": unsupported,
        "total_scored_findings": total_scored,
        "relation_hallucination_rate": hallucination_rate(total_scored, unsupported),
        "details": details,
    }


def metric_gate(actual: float | None, target: float, *, higher_is_better: bool = True) -> bool | None:
    if actual is None:
        return None
    return actual >= target if higher_is_better else actual <= target


def evaluate_section_status(
    metric_checks: Sequence[bool | None],
    *,
    allow_partial: bool = True,
) -> str:
    known = [check for check in metric_checks if check is not None]
    if not known:
        return "pending-data"
    if all(known):
        return "pass"
    if allow_partial and any(known):
        return "needs-review"
    return "needs-review"


def timed_call_ms(func: Any, *args: Any, **kwargs: Any) -> tuple[Any, float]:
    start = time.perf_counter()
    result = func(*args, **kwargs)
    elapsed_ms = (time.perf_counter() - start) * 1000
    return result, elapsed_ms


def timed_async_call_ms(awaitable_factory: Any, *args: Any, **kwargs: Any) -> tuple[Any, float]:
    start = time.perf_counter()
    result = run_async(awaitable_factory(*args, **kwargs))
    elapsed_ms = (time.perf_counter() - start) * 1000
    return result, elapsed_ms
