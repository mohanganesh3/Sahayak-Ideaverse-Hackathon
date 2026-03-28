"""Helpers for turning graph provenance into user-facing report citations."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote_plus

from app.services.source_provenance import resolve_local_provenance

_SOURCE_LABELS: dict[str, str] = {
    "acb_scale": "Anticholinergic Cognitive Burden Scale",
    "ai_assisted": "AI-assisted Pharmacology Review",
    "beers_2023": "AGS 2023 Beers Criteria",
    "crediblemeds_pdf": "CredibleMeds QTDrugs List",
    "crediblemeds_curated": "CredibleMeds Curated",
    "cns_depressant_curated": "Curated CNS Depressant Rules",
    "curated_ayurveda": "Curated Ayurveda",
    "cyp450_curated": "CYP450 Curated",
    "ddid": "DDID",
    "ddinter": "DDInter",
    "electrolyte_curated": "Curated Electrolyte Rules",
    "electrolyte_expanded": "Curated Electrolyte Rules",
    "flockhart_table": "Flockhart CYP Table",
    "fda_ddi_table": "FDA DDI Table",
    "graph_verification": "Knowledge Graph Verification",
    "hetionet": "Hetionet",
    "knowledge_graph": "Knowledge Graph",
    "mechanism_bridge": "Mechanism Bridge Rules",
    "nccih": "NCCIH",
    "onsides": "OnSIDES",
    "primekg": "PrimeKG",
    "primekg_derived": "PrimeKG Derived Mechanism Mapping",
    "primekg_target_derived": "PrimeKG Derived Target Mapping",
    "primekg_transporter_derived": "PrimeKG Derived Transporter Mapping",
    "published_literature": "Published Literature",
    "sentinel_curated": "Sentinel Curated",
    "sider": "SIDER",
    "transporter_curated": "Transporter Curated",
    "twosides": "TWOSIDES",
    "unknown": "Unknown Source",
}

_SOURCE_URLS: dict[str, str] = {
    "acb_scale": "https://www.tandfonline.com/doi/abs/10.2217/1745509X.4.3.311",
    "beers_2023": "https://doi.org/10.1111/jgs.18372",
    "crediblemeds_pdf": "https://crediblemeds.org/",
    "crediblemeds_curated": "https://crediblemeds.org/",
    "cyp450_curated": (
        "https://www.fda.gov/drugs/drug-interactions-labeling/"
        "drug-development-and-drug-interactions-table-substrates-inhibitors-and-inducers"
    ),
    "ddid": "https://bddg.hznu.edu.cn/ddid/",
    "ddinter": "https://ddinter2.scbdd.com/",
    "electrolyte_curated": None,
    "fda_ddi_table": (
        "https://www.fda.gov/drugs/drug-interactions-labeling/"
        "drug-development-and-drug-interactions-table-substrates-inhibitors-and-inducers"
    ),
    "flockhart_table": "https://drug-interactions.medicine.iu.edu/Main-Table.aspx",
    "hetionet": "https://github.com/hetio/hetionet",
    "nccih": "https://www.nccih.nih.gov/",
    "onsides": "https://onsidesdb.org/",
    "primekg": "https://www.nature.com/articles/s41597-023-01960-3",
    "primekg_derived": "https://www.nature.com/articles/s41597-023-01960-3",
    "primekg_target_derived": "https://www.nature.com/articles/s41597-023-01960-3",
    "primekg_transporter_derived": "https://www.nature.com/articles/s41597-023-01960-3",
    "published_literature": "https://pubmed.ncbi.nlm.nih.gov/",
    "sider": "https://pubmed.ncbi.nlm.nih.gov/26481350/",
    "transporter_curated": (
        "https://www.fda.gov/drugs/drug-interactions-labeling/"
        "drug-development-and-drug-interactions-table-substrates-inhibitors-and-inducers"
    ),
    "twosides": "https://nsides.io/",
}

_PROVENANCE_LABELS: dict[str, str] = {
    "ai_review": "AI-assisted review",
    "clinical_guideline": "Clinical guideline",
    "curated_compendium": "Curated from public sources",
    "derived_internal_rule": "Internal derived rule",
    "external_dataset": "External dataset",
    "internal_graph_verification": "Internal graph verification",
    "local_curated_layer": "Local curated layer",
    "published_literature": "Published literature",
}

_PROVENANCE_TYPES: dict[str, str] = {
    "acb_scale": "published_literature",
    "ai_assisted": "ai_review",
    "beers_2023": "clinical_guideline",
    "crediblemeds_curated": "curated_compendium",
    "crediblemeds_pdf": "external_dataset",
    "cns_depressant_curated": "derived_internal_rule",
    "curated_ayurveda": "local_curated_layer",
    "cyp450_curated": "curated_compendium",
    "ddid": "external_dataset",
    "ddinter": "external_dataset",
    "electrolyte_curated": "local_curated_layer",
    "electrolyte_expanded": "derived_internal_rule",
    "flockhart_table": "curated_compendium",
    "fda_ddi_table": "clinical_guideline",
    "graph_verification": "internal_graph_verification",
    "hetionet": "external_dataset",
    "knowledge_graph": "internal_graph_verification",
    "mechanism_bridge": "derived_internal_rule",
    "onsides": "external_dataset",
    "primekg": "external_dataset",
    "primekg_derived": "derived_internal_rule",
    "primekg_target_derived": "derived_internal_rule",
    "primekg_transporter_derived": "derived_internal_rule",
    "published_literature": "published_literature",
    "sentinel_curated": "local_curated_layer",
    "sider": "external_dataset",
    "transporter_curated": "curated_compendium",
    "twosides": "external_dataset",
}

_URL_RE = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Z0-9]+)\b", re.IGNORECASE)
_PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d+)\b", re.IGNORECASE)
_EXACT_REFERENCE_LINK_TYPES = {"raw_url", "doi", "pmid"}

_EVIDENCE_SCOPE_LABELS: dict[str, str] = {
    "exact_reference": "Exact study/reference",
    "literature_mention": "Named literature reference",
    "guideline_table": "Guideline/table evidence",
    "dataset_record": "Dataset record",
    "dataset_source": "Dataset source page",
    "curated_with_backing": "Curated with external backing",
    "local_curated_rule": "Local curated rule",
    "internal_rule": "Internal derived rule",
    "graph_verification": "Internal graph verification",
    "source_only": "Source-level provenance",
}

_EVIDENCE_SCOPE_DESCRIPTIONS: dict[str, str] = {
    "exact_reference": "A direct paper, PMID, DOI, or exact reference URL is available.",
    "literature_mention": "A study or literature citation is named, but only a search link is available.",
    "guideline_table": "The warning is grounded in a clinical guideline or a named guideline table.",
    "dataset_record": "The warning is backed by a specific dataset record, page, or record identifier.",
    "dataset_source": "The warning is backed by an external dataset, but only the dataset landing page is linked.",
    "curated_with_backing": "The warning comes from a curated layer that discloses an external backing source.",
    "local_curated_rule": "The warning comes from the local curated layer without a direct external record link.",
    "internal_rule": "The warning comes from an internal derived mechanism or graph expansion rule.",
    "graph_verification": "The warning is verified inside the internal knowledge graph.",
    "source_only": "Only high-level provenance is currently available.",
}

_EVIDENCE_SCOPE_RANKS: dict[str, int] = {
    "exact_reference": 8,
    "guideline_table": 7,
    "literature_mention": 6,
    "dataset_record": 5,
    "dataset_source": 4,
    "curated_with_backing": 3,
    "local_curated_rule": 2,
    "graph_verification": 1,
    "internal_rule": 1,
    "source_only": 0,
}


def normalize_source_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text or "unknown"


def source_label(source_key: Any) -> str:
    normalized = normalize_source_key(source_key)
    if normalized in _SOURCE_LABELS:
        return _SOURCE_LABELS[normalized]
    return normalized.replace("_", " ").title()


def source_url(source_key: Any) -> str | None:
    return _SOURCE_URLS.get(normalize_source_key(source_key))


def provenance_type(source_key: Any) -> str | None:
    return _PROVENANCE_TYPES.get(normalize_source_key(source_key))


def provenance_label(source_key: Any) -> str | None:
    kind = provenance_type(source_key)
    if not kind:
        return None
    return _PROVENANCE_LABELS.get(kind, kind.replace("_", " ").title())


def split_source_keys(raw_source: Any) -> list[str]:
    if raw_source is None:
        return []
    if isinstance(raw_source, list):
        values = raw_source
    else:
        values = re.split(r"[;,]", str(raw_source))

    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_source_key(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def build_evidence_text(*parts: Any) -> str:
    snippets: list[str] = []
    seen: set[str] = set()
    for part in parts:
        text = str(part or "").strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        snippets.append(text)
    return " ".join(snippets)


def _first_match(pattern: re.Pattern[str], value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    match = pattern.search(text)
    if not match:
        return None
    return match.group(1) if match.groups() else match.group(0)


def reference_resolution(reference: Any) -> tuple[str | None, str | None]:
    text = str(reference or "").strip()
    if not text:
        return None, None

    raw_url = _first_match(_URL_RE, text)
    if raw_url:
        return raw_url.rstrip(").,;"), "raw_url"

    doi = _first_match(_DOI_RE, text)
    if doi:
        cleaned = doi.rstrip(").,;")
        return f"https://doi.org/{cleaned}", "doi"

    pmid = _first_match(_PMID_RE, text)
    if pmid:
        return f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/", "pmid"

    return f"https://pubmed.ncbi.nlm.nih.gov/?term={quote_plus(text)}", "pubmed_search"


def reference_url(reference: Any) -> str | None:
    url, _kind = reference_resolution(reference)
    return url


def _normalize_record_links(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    normalized: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        url = str(item.get("url") or "").strip()
        if not label or not url or url in seen:
            continue
        seen.add(url)
        normalized.append({"label": label, "url": url})
    return normalized


def _build_record_links(citation: dict[str, Any]) -> list[dict[str, str]]:
    links = _normalize_record_links(citation.get("record_links"))
    seen = {item["url"] for item in links}

    if normalize_source_key(citation.get("source_key")) == "ddinter":
        for field, fallback_label in (
            ("ddinter_id_a", str(citation.get("drug_a") or "DDInter drug A")),
            ("ddinter_id_b", str(citation.get("drug_b") or "DDInter drug B")),
        ):
            ddinter_id = str(citation.get(field) or "").strip()
            if not ddinter_id:
                continue
            url = f"https://ddinter2.scbdd.com/server/drug-detail/{ddinter_id}/"
            if url in seen:
                continue
            seen.add(url)
            links.append({"label": f"DDInter drug page: {fallback_label}", "url": url})

    return links


def _classify_evidence_scope(citation: dict[str, Any]) -> tuple[str, str, str]:
    provenance = str(citation.get("provenance_type") or "").strip().lower()
    reference_kind = str(citation.get("reference_url_type") or "").strip().lower()

    if provenance == "clinical_guideline" or citation.get("table") or citation.get("beers_table"):
        scope = "guideline_table"
    elif reference_kind in _EXACT_REFERENCE_LINK_TYPES:
        scope = "exact_reference"
    elif citation.get("reference"):
        scope = "literature_mention"
    elif provenance == "external_dataset" and (
        citation.get("record_locator") or citation.get("record_links")
    ):
        scope = "dataset_record"
    elif provenance == "external_dataset":
        scope = "dataset_source"
    elif citation.get("backing_source_key"):
        scope = "curated_with_backing"
    elif provenance == "local_curated_layer":
        scope = "local_curated_rule"
    elif provenance == "derived_internal_rule":
        scope = "internal_rule"
    elif provenance == "internal_graph_verification":
        scope = "graph_verification"
    else:
        scope = "source_only"

    return (
        scope,
        _EVIDENCE_SCOPE_LABELS[scope],
        _EVIDENCE_SCOPE_DESCRIPTIONS[scope],
    )


def summarize_evidence_profile(citations: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    if not citations:
        return None, None

    ordered_scopes: list[str] = []
    seen: set[str] = set()
    for citation in citations:
        scope = str(citation.get("evidence_scope") or "").strip()
        if not scope or scope in seen:
            continue
        seen.add(scope)
        ordered_scopes.append(scope)

    if not ordered_scopes:
        return None, None

    ordered_scopes.sort(key=lambda scope: _EVIDENCE_SCOPE_RANKS.get(scope, 0), reverse=True)
    if len(ordered_scopes) == 1:
        scope = ordered_scopes[0]
        return _EVIDENCE_SCOPE_LABELS[scope], _EVIDENCE_SCOPE_DESCRIPTIONS[scope]

    top_labels = [_EVIDENCE_SCOPE_LABELS[scope] for scope in ordered_scopes[:2]]
    return (
        " + ".join(top_labels),
        f"Mixed evidence profile: {', '.join(_EVIDENCE_SCOPE_LABELS[scope] for scope in ordered_scopes[:3])}.",
    )


def enrich_citation(citation: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(citation)
    normalized_key = normalize_source_key(
        enriched.get("source_key") or enriched.get("source") or "unknown"
    )
    enriched["source_key"] = normalized_key
    if not enriched.get("source_label"):
        enriched["source_label"] = source_label(normalized_key)
    if not enriched.get("source_url"):
        resolved_source_url = source_url(normalized_key)
        if resolved_source_url:
            enriched["source_url"] = resolved_source_url
    if not enriched.get("provenance_type"):
        resolved_provenance_type = provenance_type(normalized_key)
        if resolved_provenance_type:
            enriched["provenance_type"] = resolved_provenance_type
    if not enriched.get("provenance_label"):
        resolved_provenance_label = provenance_label(normalized_key)
        if resolved_provenance_label:
            enriched["provenance_label"] = resolved_provenance_label

    resolved_provenance = resolve_local_provenance(enriched)
    for key, value in resolved_provenance.items():
        if value in (None, "", [], {}):
            continue
        enriched.setdefault(key, value)

    backing_source_key = normalize_source_key(enriched.get("backing_source_key"))
    if backing_source_key != "unknown":
        enriched["backing_source_key"] = backing_source_key
        if not enriched.get("backing_source_label"):
            enriched["backing_source_label"] = source_label(backing_source_key)
        if not enriched.get("backing_source_url"):
            resolved_backing_url = source_url(backing_source_key)
            if resolved_backing_url:
                enriched["backing_source_url"] = resolved_backing_url
    if enriched.get("reference") and not enriched.get("reference_url"):
        resolved_reference_url, resolved_reference_kind = reference_resolution(
            enriched.get("reference")
        )
        if resolved_reference_url:
            enriched["reference_url"] = resolved_reference_url
        if resolved_reference_kind:
            enriched["reference_url_type"] = resolved_reference_kind
    elif enriched.get("reference_url") and not enriched.get("reference_url_type"):
        resolved_reference_url, resolved_reference_kind = reference_resolution(
            enriched.get("reference")
        )
        if resolved_reference_url == enriched.get("reference_url") and resolved_reference_kind:
            enriched["reference_url_type"] = resolved_reference_kind

    record_links = _build_record_links(enriched)
    if record_links:
        enriched["record_links"] = record_links

    evidence_scope, evidence_scope_label, evidence_scope_description = _classify_evidence_scope(
        enriched
    )
    enriched["evidence_scope"] = evidence_scope
    enriched["evidence_scope_label"] = evidence_scope_label
    enriched["evidence_scope_description"] = evidence_scope_description
    return enriched


def make_citation(
    *,
    source_key: Any,
    relation_type: str,
    source_layer: str,
    evidence: str,
    evidence_type: str,
    confidence: Any | None = None,
    source_label_override: str | None = None,
    extras: dict[str, Any] | None = None,
) -> dict[str, Any]:
    citation: dict[str, Any] = {
        "source_key": normalize_source_key(source_key),
        "source_label": source_label_override or source_label(source_key),
        "source_layer": source_layer,
        "relation_type": relation_type,
        "evidence_type": evidence_type,
        "evidence": (evidence or "Structured evidence in SAHAYAK knowledge graph.").strip(),
    }
    if confidence not in (None, ""):
        try:
            citation["confidence"] = round(float(confidence), 3)
        except (TypeError, ValueError):
            pass
    for key, value in (extras or {}).items():
        if value in (None, "", [], {}):
            continue
        citation[key] = value
    return enrich_citation(citation)


def dedupe_citations(citations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str, str]] = set()
    for raw_citation in citations:
        citation = enrich_citation(raw_citation)
        key = (
            str(citation.get("source_key") or ""),
            str(citation.get("relation_type") or ""),
            str(citation.get("table") or ""),
            str(citation.get("reference") or ""),
            str(citation.get("record_locator") or ""),
            str(citation.get("evidence") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(citation)
    return deduped


def source_summary_from_citations(citations: list[dict[str, Any]]) -> str:
    labels: list[str] = []
    seen: set[str] = set()
    for citation in citations:
        label = str(citation.get("source_label") or "").strip()
        if not label or label in seen:
            continue
        seen.add(label)
        labels.append(label)
    return ", ".join(labels)
