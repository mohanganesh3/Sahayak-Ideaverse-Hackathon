#!/usr/bin/env python3
"""Run the full SAHAYAK evaluation suite and emit JSON/Markdown summaries."""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import traceback
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tests.eval._helpers import ARTIFACTS_DIR, now_iso

EVALUATORS = [
    ("tests.eval.test_ddi_metrics", "evaluate_ddi_metrics"),
    ("tests.eval.test_brand_resolution", "evaluate_brand_resolution"),
    ("tests.eval.test_ocr_pipeline", "evaluate_ocr_pipeline"),
    ("tests.eval.test_herb_drug_metrics", "evaluate_herb_drug_metrics"),
    ("tests.eval.test_beers_geriatric", "evaluate_beers_geriatric"),
    ("tests.eval.test_multihop_reasoning", "evaluate_multihop_reasoning"),
    ("tests.eval.test_rag_grounding", "evaluate_rag_grounding"),
    ("tests.eval.test_full_pipeline_cases", "evaluate_full_pipeline_cases"),
]
SCOREBOARD_METRICS = [
    ("OCR", "End-to-end medicine ID accuracy on Indian packages", "ocr_pipeline", "text_fixture_drug_id_accuracy", ">=", 0.80),
    ("Brand resolution", "Top-1 Indian brand to generic accuracy", "brand_resolution", "top1_accuracy", ">=", 0.95),
    ("Direct DDI", "Sensitivity on curated sentinel interactions", "ddi_metrics", "sensitivity", ">=", 0.95),
    ("Direct DDI", "Severity match accuracy", "ddi_metrics", "severity_exact_match", ">=", 0.85),
    ("Herb-drug", "Detection sensitivity on curated herb-drug set", "herb_drug_metrics", "herb_detection_sensitivity", ">=", 0.50),
    ("Geriatric safety", "Beers detection coverage on curated elderly list", "beers_geriatric", "beers_coverage", ">=", 0.95),
    ("Multi-hop reasoning", "Path validation precision", "multihop_reasoning", "graph_path_precision", ">=", 0.80),
    ("Graph grounding", "Relation hallucination rate", "rag_grounding", "relation_hallucination_rate", "<=", 0.05),
    ("RAG faithfulness", "Grounded answer faithfulness", "rag_grounding", "faithfulness_proxy", ">=", 0.95),
    ("Alert quality", "Clinically important findings per 10-drug review", "full_pipeline_cases", "alerts_per_10_drugs", "<=", 5.0),
    ("Usability", "SUS score with elderly/caregiver users", "usability", "sus_score", ">=", 70.0),
    ("Runtime", "End-to-end P95 latency (ms)", "full_pipeline_cases", "pipeline_latency.p95_ms", "<=", 5000.0),
]


def _lookup_metric(results_by_section: dict[str, dict[str, Any]], section: str, metric_path: str) -> Any:
    payload = results_by_section.get(section)
    if not payload:
        return None
    value: Any = payload.get("metrics", {})
    for part in metric_path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _scoreboard_rows(results_by_section: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for layer, metric_name, section, metric_path, comparator, target in SCOREBOARD_METRICS:
        actual = _lookup_metric(results_by_section, section, metric_path)
        if actual is None:
            status = "pending-data"
        elif comparator == ">=":
            status = "pass" if actual >= target else "needs-review"
        else:
            status = "pass" if actual <= target else "needs-review"
        rows.append(
            {
                "layer": layer,
                "metric": metric_name,
                "section": section,
                "metric_path": metric_path,
                "actual": actual,
                "target": target,
                "comparator": comparator,
                "status": status,
            }
        )
    return rows


def _render_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# SAHAYAK Evaluation Summary",
        "",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Mode: `{summary['mode']}`",
        f"- Sections: `{summary['section_count']}`",
        "",
        "## Scoreboard",
        "",
        "| Layer | Metric | Actual | Target | Status |",
        "|---|---|---:|---:|---|",
    ]
    for row in summary["scoreboard"]:
        target_repr = f"{row['comparator']} {row['target']}"
        actual_repr = "pending-data" if row["actual"] is None else row["actual"]
        lines.append(f"| {row['layer']} | {row['metric']} | {actual_repr} | {target_repr} | {row['status']} |")

    lines.extend(
        [
            "",
            "## Layer Results",
            "",
            "| Section | Status | Key Metrics |",
            "|---|---|---|",
        ]
    )
    for section in summary["sections"]:
        key_metrics = []
        for key, value in list(section.get("metrics", {}).items())[:5]:
            if isinstance(value, dict):
                continue
            key_metrics.append(f"`{key}={value}`")
        lines.append(f"| {section['section']} | {section['status']} | {'; '.join(key_metrics)} |")

    lines.extend(["", "## Notes", ""])
    for section in summary["sections"]:
        if not section.get("notes"):
            continue
        lines.append(f"### {section['section']}")
        for note in section["notes"]:
            lines.append(f"- {note}")
        lines.append("")

    lines.extend(["## Pending Data / Gaps", ""])
    pending = [row for row in summary["scoreboard"] if row["status"] == "pending-data"]
    if not pending:
        lines.append("- None")
    else:
        for row in pending:
            lines.append(f"- {row['layer']}: {row['metric']}")
    lines.append("")
    return "\n".join(lines)


def run_suite(*, quick: bool = False) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for module_name, function_name in EVALUATORS:
        limit = 3 if quick else None
        try:
            module = importlib.import_module(module_name)
            evaluator = getattr(module, function_name)
            results.append(evaluator(limit=limit))
        except Exception as exc:
            results.append(
                {
                    "section": function_name.removeprefix("evaluate_"),
                    "status": "needs-review",
                    "metrics": {},
                    "targets": {},
                    "notes": [
                        f"Evaluator crashed: {exc}",
                        traceback.format_exc(limit=5),
                    ],
                    "samples": [],
                }
            )

    results_by_section = {item["section"]: item for item in results}
    scoreboard = _scoreboard_rows(results_by_section)
    status_counts = {"pass": 0, "needs-review": 0, "pending-data": 0}
    for row in scoreboard:
        status_counts[row["status"]] = status_counts.get(row["status"], 0) + 1

    return {
        "generated_at": now_iso(),
        "mode": "quick" if quick else "full",
        "section_count": len(results),
        "sections": results,
        "scoreboard": scoreboard,
        "status_counts": status_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the SAHAYAK evaluation suite.")
    parser.add_argument("--quick", action="store_true", help="Run a reduced sample of each evaluator.")
    parser.add_argument("--output-dir", type=Path, default=ARTIFACTS_DIR, help="Directory for JSON/Markdown summaries.")
    args = parser.parse_args()

    summary = run_suite(quick=args.quick)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = summary["generated_at"].replace(":", "-")
    json_path = args.output_dir / f"evaluation_summary_{stamp}.json"
    md_path = args.output_dir / f"evaluation_summary_{stamp}.md"
    json_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_render_markdown(summary), encoding="utf-8")

    print(f"JSON summary: {json_path}")
    print(f"Markdown summary: {md_path}")
    print("Scoreboard status counts:", summary["status_counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
