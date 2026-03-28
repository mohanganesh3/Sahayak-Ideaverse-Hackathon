"""Evaluation of CYP/QT/electrolyte/CNS multi-hop reasoning."""

from __future__ import annotations

import pytest

from tests.eval._helpers import (
    evaluate_section_status,
    metric_gate,
    neo4j_available,
    round_metric,
    run_cypher,
    safe_div,
)

TARGETS = {
    "graph_path_precision": 0.80,
    "engine_path_recall": 0.80,
    "discoverable_pairs_minimum": 5000,
}
MULTIHOP_CASES = [
    {
        "label": "Clarithromycin inhibits CYP3A4 impacting Simvastatin",
        "kind": "cyp_inhibition",
        "drug_a": "clarithromycin",
        "drug_b": "simvastatin",
        "enzyme": "CYP3A4",
    },
    {
        "label": "Rifampin induces CYP3A4 reducing Apixaban",
        "kind": "cyp_induction",
        "drug_a": "rifampin",
        "drug_b": "apixaban",
        "enzyme": "CYP3A4",
    },
    {
        "label": "Amiodarone and Digoxin via P-glycoprotein",
        "kind": "transporter",
        "drug_a": "amiodarone",
        "drug_b": "digoxin",
        "enzyme": "P-glycoprotein",
    },
    {
        "label": "Amiodarone and Ondansetron compound QT risk",
        "kind": "qt_combined",
        "drug_a": "amiodarone",
        "drug_b": "ondansetron",
    },
    {
        "label": "Furosemide potentiates Digoxin through potassium loss",
        "kind": "electrolyte_cascade",
        "drug_a": "furosemide",
        "drug_b": "digoxin",
    },
    {
        "label": "Clonazepam and Quetiapine compound CNS depression",
        "kind": "cns_combined",
        "drug_a": "clonazepam",
        "drug_b": "quetiapine",
    },
]


def _graph_support(case: dict) -> dict:
    if case["kind"] in {"cyp_inhibition", "cyp_induction"}:
        rel = "INHIBITS" if case["kind"] == "cyp_inhibition" else "INDUCES"
        rows = run_cypher(
            f"""
            MATCH (a:Drug)-[:{rel}]->(e:Enzyme)<-[:IS_SUBSTRATE_OF]-(b:Drug)
            WHERE toLower(a.generic_name) = toLower($drug_a)
              AND toLower(b.generic_name) = toLower($drug_b)
              AND e.name = $enzyme
            RETURN count(*) AS count
            """,
            drug_a=case["drug_a"],
            drug_b=case["drug_b"],
            enzyme=case["enzyme"],
        )
        return {"supported": bool(rows and rows[0]["count"] > 0), "count": rows[0]["count"] if rows else 0}
    if case["kind"] == "transporter":
        rows = run_cypher(
            """
            MATCH (a:Drug)-[:INHIBITS]->(t:Transporter)<-[:IS_SUBSTRATE_OF]-(b:Drug)
            WHERE toLower(a.generic_name) = toLower($drug_a)
              AND toLower(b.generic_name) = toLower($drug_b)
              AND t.name = $enzyme
            RETURN count(*) AS count
            """,
            drug_a=case["drug_a"],
            drug_b=case["drug_b"],
            enzyme=case["enzyme"],
        )
        return {"supported": bool(rows and rows[0]["count"] > 0), "count": rows[0]["count"] if rows else 0}
    if case["kind"] == "qt_combined":
        rows = run_cypher(
            """
            MATCH (a:Drug)-[:PROLONGS_QT]->(:AdverseEffect)<-[:PROLONGS_QT]-(b:Drug)
            WHERE toLower(a.generic_name) = toLower($drug_a)
              AND toLower(b.generic_name) = toLower($drug_b)
            RETURN count(*) AS count
            """,
            drug_a=case["drug_a"],
            drug_b=case["drug_b"],
        )
        return {"supported": bool(rows and rows[0]["count"] > 0), "count": rows[0]["count"] if rows else 0}
    if case["kind"] == "electrolyte_cascade":
        rows = run_cypher(
            """
            MATCH (a:Drug)-[:DEPLETES]->(:ElectrolyteEffect)<-[:SENSITIVE_TO]-(b:Drug)
            WHERE toLower(a.generic_name) = toLower($drug_a)
              AND toLower(b.generic_name) = toLower($drug_b)
            RETURN count(*) AS count
            """,
            drug_a=case["drug_a"],
            drug_b=case["drug_b"],
        )
        return {"supported": bool(rows and rows[0]["count"] > 0), "count": rows[0]["count"] if rows else 0}
    rows = run_cypher(
        """
        MATCH (a:Drug)-[:CAUSES_CNS_DEPRESSION]->(n)<-[:CAUSES_CNS_DEPRESSION]-(b:Drug)
        WHERE toLower(a.generic_name) = toLower($drug_a)
          AND toLower(b.generic_name) = toLower($drug_b)
        RETURN count(*) AS count
        """,
        drug_a=case["drug_a"],
        drug_b=case["drug_b"],
    )
    return {"supported": bool(rows and rows[0]["count"] > 0), "count": rows[0]["count"] if rows else 0}


def _engine_support(case: dict) -> dict:
    from app.graph.query_engine import check_indirect_interactions

    rows = [
        item
        for item in check_indirect_interactions([case["drug_a"], case["drug_b"]], patient_age=72)
        if item.interaction_type == case["kind"]
    ]
    if case["kind"] in {"cyp_inhibition", "cyp_induction"}:
        rows = [item for item in rows if item.enzyme == case["enzyme"]]
    return {"supported": bool(rows), "count": len(rows)}


def evaluate_multihop_reasoning(limit: int | None = None) -> dict:
    if not neo4j_available():
        return {
            "section": "multihop_reasoning",
            "status": "pending-data",
            "metrics": {},
            "targets": TARGETS,
            "notes": ["Neo4j is not reachable; multi-hop evaluation requires the live graph."],
            "samples": [],
        }

    cases = MULTIHOP_CASES[:limit] if limit else MULTIHOP_CASES
    graph_hits = 0
    engine_hits = 0
    samples: list[dict] = []
    for case in cases:
        graph = _graph_support(case)
        engine = _engine_support(case)
        if graph["supported"]:
            graph_hits += 1
        if engine["supported"]:
            engine_hits += 1
        samples.append(
            {
                "label": case["label"],
                "kind": case["kind"],
                "graph_supported": graph["supported"],
                "engine_supported": engine["supported"],
                "graph_count": graph["count"],
                "engine_count": engine["count"],
            }
        )

    pair_rows = run_cypher(
        """
        MATCH (a:Drug)-[:INHIBITS]->(e:Enzyme)<-[:IS_SUBSTRATE_OF]-(b:Drug)
        WHERE a <> b
        RETURN count(DISTINCT [a.generic_name, b.generic_name]) AS pairs
        """
    )
    overlap_rows = run_cypher(
        """
        MATCH (a:Drug)-[:INHIBITS]->(e:Enzyme)<-[:IS_SUBSTRATE_OF]-(b:Drug)
        MATCH (a)-[:INTERACTS_WITH]-(b)
        WHERE a <> b
        RETURN count(DISTINCT [a.generic_name, b.generic_name]) AS overlap
        """
    )
    discoverable_pairs = pair_rows[0]["pairs"] if pair_rows else 0
    validated_overlap = overlap_rows[0]["overlap"] if overlap_rows else 0

    metrics = {
        "cases_evaluated": len(cases),
        "graph_path_precision": round_metric(safe_div(graph_hits, len(cases))),
        "engine_path_recall": round_metric(safe_div(engine_hits, len(cases))),
        "discoverable_indirect_pairs": discoverable_pairs,
        "validated_indirect_overlap": validated_overlap,
    }
    checks = [
        metric_gate(metrics["graph_path_precision"], TARGETS["graph_path_precision"]),
        metric_gate(metrics["engine_path_recall"], TARGETS["engine_path_recall"]),
        metric_gate(metrics["discoverable_indirect_pairs"], TARGETS["discoverable_pairs_minimum"]),
    ]
    notes = [
        "Transporter graph paths are evaluated separately from engine inference. If graph support exists but engine support is missing, that is a real product gap rather than a data-gap.",
    ]
    return {
        "section": "multihop_reasoning",
        "status": evaluate_section_status(checks),
        "metrics": metrics,
        "targets": TARGETS,
        "notes": notes,
        "samples": samples,
    }


def test_multihop_reasoning_evaluator_runs() -> None:
    result = evaluate_multihop_reasoning(limit=3)
    assert result["section"] == "multihop_reasoning"
    if result["status"] == "pending-data":
        pytest.skip(result["notes"][0])
    assert result["metrics"]["cases_evaluated"] == 3
