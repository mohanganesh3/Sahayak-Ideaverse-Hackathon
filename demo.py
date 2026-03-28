#!/usr/bin/env python3
"""SAHAYAK Demo — Rich terminal visualization for geriatric drug safety checks.

Usage:
    python demo.py                # runs scenario 1
    python demo.py --scenario 1  # polypharmacy scenario
    python demo.py --scenario 2  # severe DDI scenario
    python demo.py --interactive # enter drugs manually
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from rich import box
from rich.columns import Columns
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# Custom theme
SAHAYAK_THEME = Theme({
    "critical": "bold red",
    "major": "bold yellow",
    "moderate": "yellow",
    "minor": "cyan",
    "safe": "bold green",
    "info": "blue",
    "dim_text": "dim white",
    "header": "bold white on dark_blue",
    "banner": "bold cyan",
})

console = Console(theme=SAHAYAK_THEME, width=100)


# ── Scenarios ─────────────────────────────────────────────────────────────────

SCENARIO_1 = {
    "name": "Complex Polypharmacy — Elderly Diabetic with Cardiac Issues",
    "patient": {
        "name": "Ram Prasad Sharma",
        "age": 78,
        "gender": "Male",
        "diagnoses": ["Type 2 Diabetes", "Hypertension", "Atrial Fibrillation", "Osteoarthritis"],
        "drugs": ["warfarin", "metformin", "amlodipine", "aspirin", "ibuprofen", "digoxin"],
        "herbs": ["ashwagandha", "ginger", "garlic"],
    }
}

SCENARIO_2 = {
    "name": "Critical CYP3A4 Inhibition — Post-Op Infection",
    "patient": {
        "name": "Lakshmi Devi",
        "age": 72,
        "gender": "Female",
        "diagnoses": ["Dyslipidemia", "Bacterial Infection", "Atrial Fibrillation"],
        "drugs": ["atorvastatin", "clarithromycin", "warfarin", "amiodarone"],
        "herbs": ["turmeric", "ginkgo"],
    }
}


def _stdin_is_interactive() -> bool:
    return bool(sys.stdin and sys.stdin.isatty())


def _input_available() -> bool:
    return _stdin_is_interactive()


def _plain_prompt(prompt: str) -> str:
    return re.sub(r"\[/?[^\]]+\]", "", prompt)


def _read_prompt(prompt: str) -> str:
    plain_prompt = _plain_prompt(prompt)

    # Simplified check to avoid issues in some environments
    if not sys.stdin:
        raise RuntimeError(
            "Interactive mode requires an available stdin. Run `python demo.py --interactive` "
            "directly in Terminal, or use `python demo.py --scenario 1` for the scripted demo."
        )

    return input(plain_prompt)


def prompt_input(prompt: str, *, default: str = "") -> str:
    try:
        value = _read_prompt(prompt)
    except EOFError as exc:
        raise RuntimeError(
            "Interactive input closed unexpectedly. Re-run the demo in a terminal session."
        ) from exc

    return value.strip() or default


def pause_for_step(message: str) -> None:
    if not _input_available():
        console.print(f"[dim]{message} (auto-continued in non-interactive mode)[/dim]")
        return

    try:
        _read_prompt(message)
    except EOFError:
        console.print("[dim]Input stream closed; auto-continuing.[/dim]")
    except RuntimeError:
        console.print(f"[dim]{message} (auto-continued in non-interactive mode)[/dim]")


# ── Banner ────────────────────────────────────────────────────────────────────

def show_banner() -> None:
    banner_text = Text()
    banner_text.append("  SSSS  AAA  HH  HH  AAA  Y   Y  AAA  KK KK\n", style="bold cyan")
    banner_text.append("  S    A   A HH  HH A   A  Y Y  A   A KK K \n", style="bold cyan")
    banner_text.append("  SSSS AAAAA HHHHHH AAAAA   Y   AAAAA KKK  \n", style="bold cyan")
    banner_text.append("     S A   A HH  HH A   A   Y   A   A KK K \n", style="bold cyan")
    banner_text.append("  SSSS A   A HH  HH A   A   Y   A   A KK KK\n", style="bold cyan")
    banner_text.append("\n  AI-Powered Geriatric Telecare Companion — Drug Safety Module\n", style="bold white")
    banner_text.append("  Neo4j Knowledge Graph  |  LangGraph  |  CYP450 Multi-hop Reasoning\n", style="dim white")

    console.print(Panel(banner_text, border_style="cyan", padding=(0, 2)))


# ── KG Stats ──────────────────────────────────────────────────────────────────

def show_kg_stats() -> None:
    console.print(Rule("[bold cyan]Knowledge Graph Statistics[/bold cyan]", style="cyan"))
    
    try:
        from app.graph.neo4j_connection import get_driver
        driver = get_driver()
        with driver.session() as session:
            stats = {}
            queries = {
                "Drug nodes": "MATCH (n:Drug) RETURN count(n) AS c",
                "Herb nodes": "MATCH (n:Herb) RETURN count(n) AS c",
                "Indian Brands": "MATCH (n:IndianBrand) RETURN count(n) AS c",
                "DDI edges": "MATCH ()-[r:INTERACTS_WITH]->() RETURN count(r) AS c",
                "CYP Inhibitions": "MATCH ()-[r:INHIBITS]->() RETURN count(r) AS c",
                "CYP Substrates": "MATCH ()-[r:IS_SUBSTRATE_OF]->() RETURN count(r) AS c",
            }
            for label, q in queries.items():
                try:
                    result = session.run(q).single()
                    stats[label] = f"{result['c']:,}" if result else "N/A"
                except Exception:
                    stats[label] = "N/A"
    except Exception as e:
        stats = {
            "Drug nodes": "8,877", "Herb nodes": "1,337",
            "Indian Brands": "249,149", "DDI edges": "1,493,771",
            "CYP Inhibitions": "~52,000", "CYP Substrates": "~35,000",
        }

    table = Table(box=box.ROUNDED, border_style="cyan", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="bold white", width=20)
    table.add_column("Count", style="bold green", justify="right", width=15)
    table.add_column("Source", style="dim white", width=35)

    source_map = {
        "Drug nodes": "DDInter + PrimeKG + DDID",
        "Herb nodes": "Traditional Med + AYUSH DB",
        "Indian Brands": "CDSCO + PharmEasy scrape",
        "DDI edges": "DDInter + SIDER + TwoSIDES",
        "CYP Inhibitions": "CYP450 annotations",
        "CYP Substrates": "PharmGKB + CYPAlleles",
    }

    for label, count in stats.items():
        table.add_row(label, count, source_map.get(label, ""))

    console.print(table)
    console.print()


# ── Patient Input ─────────────────────────────────────────────────────────────

def show_patient_info(scenario: dict) -> None:
    p = scenario["patient"]
    console.print(Rule("[bold white]Patient Profile[/bold white]", style="white"))

    info_table = Table(box=box.SIMPLE, show_header=False, padding=(0, 2))
    info_table.add_column("Field", style="bold cyan", width=18)
    info_table.add_column("Value", style="white")

    info_table.add_row("Patient Name", p["name"])
    info_table.add_row("Age", f"{p['age']} years")
    info_table.add_row("Gender", p["gender"])
    info_table.add_row("Diagnoses", ", ".join(p["diagnoses"]))
    info_table.add_row("Medications", ", ".join(p["drugs"]))
    info_table.add_row("Herbal Supplements", ", ".join(p["herbs"]) if p["herbs"] else "None")

    console.print(Panel(info_table, title="[bold]Case Details[/bold]", border_style="white", padding=(0, 1)))
    console.print()


# ── Drug Resolution Animation ─────────────────────────────────────────────────

def show_drug_resolution(drugs: list[str], herbs: list[str]) -> tuple[list, list]:
    console.print(Rule("[bold cyan]Drug Name Resolution[/bold cyan]", style="cyan"))

    from app.graph.query_engine import resolve_drug_name, resolve_herb_name

    resolved_drugs = []
    resolved_herbs = []

    res_table = Table(box=box.ROUNDED, border_style="cyan")
    res_table.add_column("Input Name", style="bold white", width=22)
    res_table.add_column("Resolved As", style="bold green", width=22)
    res_table.add_column("Method", style="cyan", width=20)
    res_table.add_column("Confidence", style="yellow", justify="right", width=10)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Resolving drug names...", total=len(drugs) + len(herbs))

        for drug in drugs:
            progress.update(task, description=f"[cyan]Resolving: {drug}...")
            r = resolve_drug_name(drug)
            resolved_drugs.append(r)
            time.sleep(0.05)
            progress.advance(task)
            if r.resolved:
                res_table.add_row(
                    drug, r.generic_name,
                    r.resolution_method or "exact_match",
                    f"{r.confidence:.2f}"
                )
            else:
                res_table.add_row(drug, "[red]UNRESOLVED[/red]", "—", "0.00")

        for herb in herbs:
            progress.update(task, description=f"[cyan]Resolving herb: {herb}...")
            r = resolve_herb_name(herb)
            resolved_herbs.append(r)
            time.sleep(0.05)
            progress.advance(task)
            res_table.add_row(
                f"[italic]{herb}[/italic]",
                r.canonical_name if r.resolved else "[red]UNRESOLVED[/red]",
                "herb_db",
                f"{r.confidence:.2f}"
            )

    console.print(res_table)
    console.print()
    return resolved_drugs, resolved_herbs


# ── L1 Direct Checks ──────────────────────────────────────────────────────────

def show_direct_interactions(drug_names: list[str]) -> list:
    console.print(Rule("[bold red]L1 — Direct DDI Checks[/bold red]", style="red"))

    from app.graph.query_engine import check_direct_interactions

    with console.status("[bold red]Querying DDI database...[/bold red]"):
        interactions = check_direct_interactions(drug_names)

    if not interactions:
        console.print(Panel("[green]No direct drug-drug interactions found.[/green]", border_style="green"))
    else:
        table = Table(box=box.ROUNDED, border_style="red", title=f"[bold red]{len(interactions)} Direct Interactions[/bold red]")
        table.add_column("Drug A", style="bold white", width=18)
        table.add_column("Drug B", style="bold white", width=18)
        table.add_column("Severity", width=10, justify="center")
        table.add_column("Mechanism", style="dim white", width=30)
        table.add_column("Conf.", justify="right", width=6)

        severity_styles = {
            "major": "bold yellow", "moderate": "yellow",
            "minor": "cyan", "unknown": "dim white",
        }

        for ix in sorted(interactions, key=lambda x: {"major": 0, "moderate": 1, "minor": 2, "unknown": 3}.get(x.severity.lower() if x.severity else "unknown", 3)):
            sev = (ix.severity or "unknown").lower()
            sev_style = severity_styles.get(sev, "dim white")
            table.add_row(
                ix.drug_a.title(),
                ix.drug_b.title(),
                f"[{sev_style}]{sev.upper()}[/{sev_style}]",
                ix.mechanism[:28] + "..." if ix.mechanism and len(ix.mechanism) > 28 else (ix.mechanism or "—"),
                f"{ix.confidence:.2f}"
            )

        console.print(table)
    console.print()
    return interactions


# ── L2 CYP Multi-Hop Visualization ───────────────────────────────────────────

def show_cyp_interactions(drug_names: list[str]) -> list:
    console.print(Rule("[bold yellow]L2 — CYP450 Multi-Hop Reasoning[/bold yellow]", style="yellow"))

    from app.graph.query_engine import check_indirect_interactions

    with console.status("[bold yellow]Running CYP450 pathway analysis...[/bold yellow]"):
        indirect = check_indirect_interactions(drug_names)

    if not indirect:
        console.print(Panel("[green]No CYP450 indirect interactions detected.[/green]", border_style="green"))
    else:
        # Group by enzyme
        by_enzyme: dict[str, list] = {}
        for ix in indirect:
            by_enzyme.setdefault(ix.enzyme, []).append(ix)

        for enzyme, ixs in sorted(by_enzyme.items()):
            console.print(f"\n  [bold yellow]Enzyme: {enzyme}[/bold yellow]")

            # ASCII pathway visualization
            for ix in ixs:
                risk_label = "[bold red]HIGH RISK[/bold red]" if ix.victim_is_nti else ""
                pathway = (
                    f"  [bold white]{ix.perpetrator.title()}[/bold white] "
                    f"[red]--INHIBITS-->[/red] "
                    f"[yellow]{ix.enzyme}[/yellow] "
                    f"[dim]<--substrate--[/dim] "
                    f"[bold white]{ix.victim.title()}[/bold white] "
                    f"[bold cyan](increased exposure)[/bold cyan] "
                    f"{risk_label}"
                )
                console.print(pathway)
                details = (
                    f"    [dim]Inhibition: {ix.inhibitor_strength or 'unknown'} | "
                    f"Substrate fraction: {ix.substrate_fraction or 'unknown'} | "
                    f"Confidence: {ix.confidence:.2f}[/dim]"
                )
                console.print(details)

        # Summary table
        table = Table(box=box.SIMPLE, border_style="yellow")
        table.add_column("Inhibitor", style="bold white", width=18)
        table.add_column("Enzyme", style="yellow", width=10)
        table.add_column("Victim Drug", style="bold white", width=18)
        table.add_column("NTI?", width=6, justify="center")
        table.add_column("Risk", width=12, justify="center")

        for ix in sorted(indirect, key=lambda x: -x.confidence):
            nti_label = "[bold red]YES[/bold red]" if ix.victim_is_nti else "no"
            risk_score = _cyp_risk_label(ix)
            table.add_row(
                ix.perpetrator.title(),
                ix.enzyme,
                ix.victim.title(),
                nti_label,
                risk_score,
            )

        console.print(table)

    console.print()
    return indirect


def _cyp_risk_label(ix) -> str:
    score = 0
    if (ix.inhibitor_strength or "").lower() == "strong":
        score += 2
    elif (ix.inhibitor_strength or "").lower() == "moderate":
        score += 1
    if (ix.substrate_fraction or "").lower() == "major":
        score += 2
    elif (ix.substrate_fraction or "").lower() == "moderate":
        score += 1
    if ix.victim_is_nti:
        score += 3
    if score >= 5:
        return "[bold red]CRITICAL[/bold red]"
    elif score >= 3:
        return "[bold yellow]HIGH[/bold yellow]"
    elif score >= 1:
        return "[yellow]MODERATE[/yellow]"
    else:
        return "[cyan]LOW[/cyan]"


# ── CRAG Evaluation Display ───────────────────────────────────────────────────

def show_crag_result(crag_eval: dict | None) -> None:
    console.print(Rule("[bold blue]CRAG Evaluation (LLM Completeness Check)[/bold blue]", style="blue"))

    if not crag_eval:
        console.print("[dim]CRAG evaluation not available (graph-only mode)[/dim]")
        console.print()
        return

    score = crag_eval.get("completeness_score", 0.0)
    score_pct = int(score * 100)

    # Score bar
    bar_filled = int(score_pct / 5)
    bar = "#" * bar_filled + "." * (20 - bar_filled)
    score_color = "green" if score >= 0.8 else "yellow" if score >= 0.6 else "red"
    console.print(f"  Completeness: [{score_color}]{bar}[/{score_color}] [bold {score_color}]{score_pct}%[/bold {score_color}]")
    console.print()

    missing = crag_eval.get("missing_interactions", [])
    if missing:
        console.print(f"  [bold yellow]LLM flagged {len(missing)} potentially missed interaction(s):[/bold yellow]")
        for m in missing[:3]:
            console.print(f"    - [bold]{m.get('drug_a', '')} + {m.get('drug_b', '')}[/bold]: {m.get('mechanism', m.get('reasoning', ''))[:70]}")

    guesses = crag_eval.get("unresolved_guesses", [])
    if guesses:
        console.print(f"\n  [bold cyan]Resolution suggestions:[/bold cyan]")
        for g in guesses[:3]:
            console.print(f"    - \"{g.get('original','')}\" -> likely [bold]{g.get('likely_identity','')}[/bold]")

    deeper = crag_eval.get("needs_deeper_check", False)
    if deeper:
        console.print(f"\n  [bold red]WARNING: CRAG triggered deep analysis pass[/bold red]")
    else:
        console.print(f"\n  [bold green]OK: Graph results deemed sufficiently complete[/bold green]")

    console.print()


# ── Final Safety Report Table ─────────────────────────────────────────────────

def show_final_report(report) -> None:
    console.print(Rule("[bold white]Final Safety Report[/bold white]", style="white"))

    # Summary panel
    summary_parts = []

    if hasattr(report, 'total_findings'):
        total = report.total_findings
        critical = getattr(report, 'critical_count', 0)
        color = "red" if critical > 0 else "yellow" if total > 5 else "green"
        summary_parts.append(f"[bold {color}]{total} total findings[/bold {color}]")
        if critical:
            summary_parts.append(f"[bold red]{critical} CRITICAL[/bold red]")
    elif isinstance(report, dict):
        findings = report.get("findings", [])
        total = len(findings)
        critical = sum(1 for f in findings if (f.get("severity") or "").lower() in ("critical", "major"))
        color = "red" if critical > 0 else "yellow" if total > 5 else "green"
        summary_parts.append(f"[bold {color}]{total} total findings[/bold {color}]")

    console.print(Panel("  " + "  |  ".join(summary_parts), border_style="white"))

    # Build findings table
    table = Table(
        box=box.ROUNDED, border_style="white",
        title="[bold]Comprehensive Drug Safety Findings[/bold]",
        show_lines=True,
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Category", style="bold cyan", width=14)
    table.add_column("Finding", style="white", width=35)
    table.add_column("Severity", width=10, justify="center")
    table.add_column("Source", style="dim", width=12)
    table.add_column("Conf.", justify="right", width=6)

    sev_styles = {
        "critical": "bold red",
        "major": "bold yellow",
        "moderate": "yellow",
        "minor": "cyan",
        "unknown": "dim white",
        "": "dim white",
    }

    findings_list = []

    if hasattr(report, 'direct_interactions'):
        for ix in (report.direct_interactions or []):
            findings_list.append({
                "cat": "Direct DDI",
                "finding": f"{ix.drug_a.title()} + {ix.drug_b.title()}",
                "detail": ix.mechanism or ix.clinical_effect or "",
                "severity": ix.severity or "unknown",
                "source": ix.source_layer or "L1_direct",
                "confidence": ix.confidence,
            })
        for ix in (report.indirect_interactions or []):
            findings_list.append({
                "cat": "CYP450",
                "finding": f"{ix.perpetrator.title()} -> {ix.victim.title()}",
                "detail": f"via {ix.enzyme}",
                "severity": "major" if ix.victim_is_nti else "moderate",
                "source": "L2_multihop",
                "confidence": ix.confidence,
            })
        for f in (report.beers_flags or []):
            findings_list.append({
                "cat": "Beers 2023",
                "finding": f.drug_name.title() + (" + " + f.condition if f.condition else ""),
                "detail": f.recommendation or "",
                "severity": f.severity if hasattr(f, 'severity') else "moderate",
                "source": "L1_direct",
                "confidence": f.confidence,
            })
        if report.acb_result and report.acb_result.total_score > 0:
            findings_list.append({
                "cat": "ACB Score",
                "finding": f"Total ACB = {report.acb_result.total_score}",
                "detail": f"Risk: {report.acb_result.risk_level}",
                "severity": "major" if report.acb_result.total_score >= 3 else "moderate",
                "source": "L1_direct",
                "confidence": 0.95,
            })
        for h in (report.herb_drug_interactions or []):
            findings_list.append({
                "cat": "Herb-Drug",
                "finding": f"{h.herb_name.title()} + {h.drug_name.title()}",
                "detail": h.interaction_type or "",
                "severity": h.severity or "unknown",
                "source": "L1_direct",
                "confidence": h.confidence,
            })
    elif isinstance(report, dict):
        for f in report.get("findings", []):
            findings_list.append({
                "cat": f.get("type", "Finding"),
                "finding": f.get("description", "")[:38],
                "detail": f.get("mechanism", "")[:38],
                "severity": f.get("severity", "unknown"),
                "source": f.get("source_layer", "unknown"),
                "confidence": f.get("confidence", 0.5),
            })

    # Sort by severity
    sev_order = {"critical": 0, "major": 1, "moderate": 2, "minor": 3, "unknown": 4, "": 4}
    findings_list.sort(key=lambda x: sev_order.get((x["severity"] or "").lower(), 4))

    for i, f in enumerate(findings_list, 1):
        sev = (f["severity"] or "unknown").lower()
        sev_style = sev_styles.get(sev, "dim white")
        table.add_row(
            str(i),
            f["cat"],
            f["finding"] + ("\n" + "[dim]" + f["detail"][:35] + "[/dim]" if f["detail"] else ""),
            f"[{sev_style}]{sev.upper()}[/{sev_style}]",
            f["source"],
            f"{f['confidence']:.2f}",
        )

    console.print(table)
    console.print()


# ── Interactive Mode ──────────────────────────────────────────────────────────

def interactive_mode() -> dict:
    console.print(Rule("[bold cyan]Interactive Patient Input[/bold cyan]", style="cyan"))
    console.print("[cyan]Enter patient details (press Enter to skip optional fields)[/cyan]\n")

    name = prompt_input("[bold]Patient Name[/bold]: ", default="Patient")
    age_str = prompt_input("[bold]Age[/bold]: ")
    age = int(age_str) if age_str.isdigit() else 70
    gender = prompt_input("[bold]Gender[/bold] (M/F): ", default="M")
    diagnoses_raw = prompt_input("[bold]Diagnoses[/bold] (comma-separated): ")
    diagnoses = [d.strip() for d in diagnoses_raw.split(",") if d.strip()] if diagnoses_raw else []
    drugs_raw = prompt_input("[bold]Medications[/bold] (comma-separated): ")
    drugs = [d.strip() for d in drugs_raw.split(",") if d.strip()] if drugs_raw else []
    herbs_raw = prompt_input("[bold]Herbal supplements[/bold] (comma-separated, optional): ")
    herbs = [h.strip() for h in herbs_raw.split(",") if h.strip()] if herbs_raw else []

    return {
        "name": name,
        "age": age,
        "gender": "Male" if gender.upper().startswith("M") else "Female",
        "diagnoses": diagnoses,
        "drugs": drugs,
        "herbs": herbs,
    }


# ── Main Flow ─────────────────────────────────────────────────────────────────

def run_demo(scenario: dict) -> None:
    p = scenario["patient"]
    drugs = p["drugs"]
    herbs = p["herbs"]
    age = p["age"]
    diagnoses = p["diagnoses"]

    console.print()
    show_banner()
    console.print()
    show_kg_stats()

    pause_for_step("\n[dim]Press Enter to begin safety analysis...[/dim]")
    console.print()

    show_patient_info(scenario)

    pause_for_step("[dim]Press Enter to resolve drug names...[/dim]")
    console.print()

    resolved_drugs, resolved_herbs = show_drug_resolution(drugs, herbs)
    generic_names = [r.generic_name for r in resolved_drugs if r.resolved]
    herb_names = [r.canonical_name for r in resolved_herbs if r.resolved]

    pause_for_step("[dim]Press Enter to run L1 direct interaction checks...[/dim]")
    console.print()

    direct = show_direct_interactions(generic_names)

    pause_for_step("[dim]Press Enter to run L2 CYP450 multi-hop analysis...[/dim]")
    console.print()

    indirect = show_cyp_interactions(generic_names)

    pause_for_step("[dim]Press Enter to run agentic CRAG evaluation...[/dim]")
    console.print()

    # Run agentic checker
    crag_eval = None
    final_report = None

    with console.status("[bold blue]Running LangGraph agentic pipeline (30s max)...[/bold blue]"):
        try:
            from app.services.agentic_safety_checker import run_safety_check
            result = asyncio.run(run_safety_check({
                "drugs": drugs,
                "herbs": herbs,
                "age": age,
                "diagnoses": diagnoses,
            }))
            crag_eval = result.get("metadata", {}).get("crag_evaluation")
            final_report = result
        except Exception as e:
            console.print(f"[yellow]Agentic pipeline unavailable ({e}), using graph-only mode[/yellow]")
            try:
                from app.graph.query_engine import get_comprehensive_safety_report
                final_report_obj = get_comprehensive_safety_report(
                    drugs=generic_names,
                    herbs=herb_names,
                    age=age,
                    diagnoses=diagnoses,
                )
                final_report = final_report_obj
            except Exception as e2:
                console.print(f"[red]Graph query also failed: {e2}[/red]")
                final_report = None

    show_crag_result(crag_eval)

    pause_for_step("[dim]Press Enter to see final safety report...[/dim]")
    console.print()

    if final_report is not None:
        show_final_report(final_report)

    # Footer
    console.print(Rule(style="cyan"))
    console.print(Panel(
        "[bold cyan]SAHAYAK[/bold cyan] [white]— Safe AI Health Assistant for Yaksha (Geriatric Care)[/white]\n"
        "[dim]This system is for clinical decision support only. Always consult a physician.[/dim]",
        border_style="cyan",
        padding=(0, 2),
    ))
    console.print()


def main() -> int:
    parser = argparse.ArgumentParser(description="SAHAYAK Drug Safety Demo")
    parser.add_argument("--scenario", type=int, choices=[1, 2], default=1)
    parser.add_argument("--interactive", action="store_true")
    args = parser.parse_args()

    try:
        if args.interactive:
            patient = interactive_mode()
            scenario = {"name": "Interactive Session", "patient": patient}
        elif args.scenario == 2:
            scenario = SCENARIO_2
        else:
            scenario = SCENARIO_1

        run_demo(scenario)
    except KeyboardInterrupt:
        console.print("\n[yellow]Demo interrupted.[/yellow]")
        return 130
    except RuntimeError as exc:
        console.print(f"\n[red]{exc}[/red]")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
