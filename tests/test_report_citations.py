from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.services.citation_utils import make_citation
from app.services.report_generator import _merge_llm_report, _prepare_prompt_payload
from app.services.translation_service import _apply_report_translation, translate_report


def test_prepare_prompt_payload_builds_structured_citations() -> None:
    payload = _prepare_prompt_payload(
        {
            "findings": [
                {
                    "drug_a": "Warfarin",
                    "drug_b": "Aspirin",
                    "severity": "major",
                    "clinical_effect": "Bleeding risk may increase.",
                    "mechanism": "Additive anticoagulant and antiplatelet effect.",
                    "management": "Monitor closely and discuss with your doctor.",
                    "source": "ddinter, primekg",
                    "confidence": 0.95,
                }
            ]
        },
        {},
    )

    finding = payload["findings"][0]
    assert finding["finding_id"] == "finding_1"
    assert finding["title"] == "Warfarin + Aspirin"
    assert finding["display_severity"] == "major"
    assert {citation["source_key"] for citation in finding["citations"]} == {"ddinter", "primekg"}
    assert finding["source"] == "DDInter, PrimeKG"
    assert {citation["source_url"] for citation in finding["citations"]} == {
        "https://ddinter2.scbdd.com/",
        "https://www.nature.com/articles/s41597-023-01960-3",
    }


def test_merge_llm_report_preserves_seed_provenance() -> None:
    payload = _prepare_prompt_payload(
        {
            "findings": [
                {
                    "drug_a": "Warfarin",
                    "drug_b": "Aspirin",
                    "severity": "major",
                    "clinical_effect": "Bleeding risk may increase.",
                    "mechanism": "Additive anticoagulant and antiplatelet effect.",
                    "management": "Monitor closely and discuss with your doctor.",
                    "citations": [
                        {
                            "source_key": "ddinter",
                            "source_label": "DDInter",
                            "source_layer": "L1_direct",
                            "relation_type": "INTERACTS_WITH",
                            "evidence_type": "direct_interaction",
                            "evidence": "Bleeding risk may increase.",
                        }
                    ],
                    "source": "DDInter",
                    "confidence": 0.95,
                }
            ]
        },
        {"age": 72},
    )

    merged = _merge_llm_report(
        payload,
        {
            "patient_summary": "This combination needs review.",
            "findings": [
                {
                    "finding_id": "finding_1",
                    "patient_explanation": "This can increase bleeding risk for you.",
                    "doctor_explanation": "Additive anticoagulant and antiplatelet effect.",
                    "action": "Discuss this combination with your doctor.",
                }
            ],
            "acb_section": {"risk": "No anticholinergic concern."},
            "disclaimer": "This is for information only. Consult your doctor.",
        },
        {"age": 72},
    )

    finding = merged["findings"][0]
    assert finding["finding_id"] == "finding_1"
    assert finding["source"] == "DDInter"
    assert finding["citations"][0]["source_key"] == "ddinter"
    assert finding["citations"][0]["source_url"] == "https://ddinter2.scbdd.com/"
    assert finding["citations"][0]["evidence_scope"] == "dataset_source"
    assert finding["display_severity"] == "major"
    assert finding["patient_explanation"] == "This can increase bleeding risk for you."


def test_prepare_prompt_payload_maps_unknown_to_doctor_review_for_display() -> None:
    payload = _prepare_prompt_payload(
        {
            "findings": [
                {
                    "drug_a": "Acetaminophen",
                    "drug_b": "Amlodipine",
                    "severity": "unknown",
                    "clinical_effect": "Possible interaction detected.",
                    "source": "primekg",
                    "confidence": 0.8,
                }
            ]
        },
        {},
    )

    finding = payload["findings"][0]
    assert finding["severity"] == "unknown"
    assert finding["display_severity"] == "doctor_review"


def test_make_citation_derives_reference_links() -> None:
    citation = make_citation(
        source_key="beers_2023",
        relation_type="FLAGGED_BY",
        source_layer="L1_direct",
        evidence="Anticholinergic burden concern.",
        evidence_type="geriatric_guideline",
        extras={
            "reference": (
                "Ramos H, Moreno L, Perez-Tur J, et al. CRIDECO Anticholinergic Load Scale (CALS). "
                "J Pers Med. 2022;12(2):207. doi:10.3390/jpm12020207. PMID:35207695."
            )
        },
    )

    assert citation["source_url"] == "https://doi.org/10.1111/jgs.18372"
    assert citation["reference_url"] == "https://doi.org/10.3390/jpm12020207"
    assert citation["reference_url_type"] == "doi"
    assert citation["evidence_scope"] == "guideline_table"


def test_acb_scale_citation_has_distinct_source_label() -> None:
    citation = make_citation(
        source_key="acb_scale",
        relation_type="FLAGGED_BY",
        source_layer="L1_direct",
        evidence="Amitriptyline contributes anticholinergic burden score 3.",
        evidence_type="anticholinergic_burden",
        extras={
            "table": "table7",
            "reference": (
                "Aging Brain Care / Indiana University Center for Aging Research. "
                "Anticholinergic Cognitive Burden Scale, 2012 update "
                "(based on Boustani et al. 2008)."
            ),
        },
    )

    assert citation["source_label"] == "Anticholinergic Cognitive Burden Scale"
    assert citation["source_url"] == "https://www.tandfonline.com/doi/abs/10.2217/1745509X.4.3.311"


def test_curated_ayurveda_does_not_claim_ddid_backing_without_pair_match() -> None:
    citation = make_citation(
        source_key="curated_ayurveda",
        relation_type="INTERACTS_WITH_DRUG",
        source_layer="L1_direct",
        evidence="Brahmi (Bacopa) may increase CNS depression risk.",
        evidence_type="herb_drug_interaction",
        extras={
            "herb": "Brahmi (Bacopa)",
            "drug": "Diazepam",
            "mechanism": "Other CNS depressants",
        },
    )

    assert citation.get("backing_source_key") != "ddid"
    assert citation["evidence_basis"] == "curated"


def test_ddid_citation_recovers_local_study_reference() -> None:
    citation = make_citation(
        source_key="ddid",
        relation_type="INTERACTS_WITH_DRUG",
        source_layer="L1_direct",
        evidence="Brahmi (Bacopa) may alter tacrolimus exposure.",
        evidence_type="herb_drug_interaction",
        extras={
            "herb": "Brahmi (Bacopa)",
            "drug": "Tacrolimus",
        },
    )

    assert citation["source_url"] == "https://bddg.hznu.edu.cn/ddid/"
    assert citation["doi"] == "10.1111/jcpt.13256"
    assert citation["pmid"] == "32930420"
    assert citation["reference_url"] == "https://doi.org/10.1111/jcpt.13256"
    assert citation["evidence_scope"] == "exact_reference"
    assert {link["label"] for link in citation["record_links"]} == {
        "DDID herb page",
        "DDID drug page",
    }
    assert "Tacrolimus" not in citation["reference"]


def test_curated_ayurveda_citation_discloses_ddid_backing() -> None:
    citation = make_citation(
        source_key="curated_ayurveda",
        relation_type="INTERACTS_WITH_DRUG",
        source_layer="L1_direct",
        evidence="Brahmi (Bacopa) may alter tacrolimus exposure.",
        evidence_type="herb_drug_interaction",
        extras={
            "herb": "Brahmi (Bacopa)",
            "drug": "Tacrolimus",
            "mechanism": "Tacrolimus",
        },
    )

    assert citation["provenance_label"] == "Local curated layer"
    assert citation["evidence_basis"] == "ddid"
    assert citation["backing_source_key"] == "ddid"
    assert citation["backing_source_url"] == "https://bddg.hznu.edu.cn/ddid/"
    assert citation["reference_url"] == "https://doi.org/10.1111/jcpt.13256"
    assert citation["evidence_scope"] == "exact_reference"


def test_curated_ayurveda_citation_discloses_nccih_backing_when_present() -> None:
    citation = make_citation(
        source_key="curated_ayurveda",
        relation_type="INTERACTS_WITH_DRUG",
        source_layer="L1_direct",
        evidence="Ashwagandha may increase thyroid effects.",
        evidence_type="herb_drug_interaction",
        extras={
            "herb": "Ashwagandha",
            "drug": "Levothyroxine",
            "mechanism": "Thyroid hormone",
        },
    )

    assert citation["evidence_basis"] == "curated"
    assert citation["backing_source_key"] == "nccih"
    assert citation["backing_source_url"] == "https://www.nccih.nih.gov/"
    assert citation["evidence_scope"] == "curated_with_backing"


def test_ddinter_citation_exposes_drug_record_links() -> None:
    citation = make_citation(
        source_key="ddinter",
        relation_type="INTERACTS_WITH",
        source_layer="L1_direct",
        evidence="Dataset-level direct interaction record.",
        evidence_type="direct_interaction",
        extras={
            "drug_a": "Warfarin",
            "drug_b": "Aspirin",
            "ddinter_id_a": "DDInter211",
            "ddinter_id_b": "DDInter245",
            "record_locator": "ddinter_id_a=DDInter211 | ddinter_id_b=DDInter245",
        },
    )

    assert citation["evidence_scope"] == "dataset_record"
    assert citation["record_links"] == [
        {
            "label": "DDInter drug page: Warfarin",
            "url": "https://ddinter2.scbdd.com/server/drug-detail/DDInter211/",
        },
        {
            "label": "DDInter drug page: Aspirin",
            "url": "https://ddinter2.scbdd.com/server/drug-detail/DDInter245/",
        },
    ]


def test_named_reference_without_doi_is_not_treated_as_exact_reference() -> None:
    citation = make_citation(
        source_key="cyp450_curated",
        relation_type="INHIBITS",
        source_layer="L2_multihop",
        evidence="Black Pepper inhibits CYP3A4.",
        evidence_type="mechanism_path",
        extras={"reference": "Bhardwaj et al. 2002"},
    )

    assert citation["reference_url_type"] == "pubmed_search"
    assert citation["evidence_scope"] == "literature_mention"


def test_cyp450_curated_drug_edge_discloses_fda_and_flockhart_links() -> None:
    citation = make_citation(
        source_key="cyp450_curated",
        relation_type="IS_SUBSTRATE_OF",
        source_layer="L2_multihop",
        evidence="Simvastatin is a major substrate of CYP3A4.",
        evidence_type="mechanism_path",
        extras={
            "drug": "Simvastatin",
            "enzyme": "CYP3A4",
            "fraction": "major",
        },
    )

    assert citation["backing_source_key"] == "fda_ddi_table"
    assert {link["label"] for link in citation["record_links"]} == {
        "Flockhart CYP table",
        "FDA DDI table",
        "Flockhart references: CYP3A4",
    }


def test_cyp450_curated_herb_edge_recovers_literature_reference() -> None:
    citation = make_citation(
        source_key="cyp450_curated",
        relation_type="INHIBITS",
        source_layer="L2_multihop",
        evidence="Black Pepper inhibits CYP3A4.",
        evidence_type="mechanism_path",
        extras={
            "herb": "Black Pepper",
            "drug": "Simvastatin",
            "enzyme": "CYP3A4",
        },
    )

    assert citation["backing_source_key"] == "published_literature"
    assert citation["reference"] == "Bhardwaj et al. 2002"
    assert citation["reference_url_type"] == "pubmed_search"


def test_translate_report_sarvam_batch_preserves_metadata_and_links(monkeypatch) -> None:
    calls: list[str] = []

    def fake_translate(text: str, source_lang: str, target_lang: str) -> str:
        calls.append(text)
        translated_lines: list[str] = []
        for line in text.splitlines():
            if line.startswith("__SAHAYAK_FIELD_"):
                translated_lines.append(line)
            elif line:
                translated_lines.append(f"HI::{line}")
            else:
                translated_lines.append(line)
        return "\n".join(translated_lines)

    monkeypatch.setattr("app.services.translation_service.translate", fake_translate)

    report = {
        "patient_summary": "This combination needs review.",
        "findings": [
            {
                "severity": "major",
                "title": "Warfarin + Aspirin",
                "patient_explanation": "Bleeding risk may increase.",
                "doctor_explanation": "Additive anticoagulant effect.",
                "action": "Discuss with your doctor.",
                "medicines": ["Warfarin", "Aspirin"],
                "confidence": "high",
                "source": "DDInter",
                "citations": [
                    {
                        "source_key": "ddinter",
                        "source_label": "DDInter",
                        "source_layer": "L1_direct",
                        "relation_type": "INTERACTS_WITH",
                        "evidence_type": "direct_interaction",
                        "evidence": "Bleeding risk may increase.",
                        "source_url": "https://ddinter2.scbdd.com/",
                        "reference_url": "https://pubmed.ncbi.nlm.nih.gov/12345678/",
                        "evidence_scope": "dataset_record",
                    }
                ],
                "evidence_profile": "Dataset-backed",
            }
        ],
        "acb_section": {
            "score": 3,
            "risk": "High anticholinergic burden.",
            "drugs": ["Amitriptyline"],
            "citations": [
                {
                    "source_key": "acb_scale",
                    "source_label": "Anticholinergic Cognitive Burden Scale",
                    "source_layer": "L1_direct",
                    "relation_type": "FLAGGED_BY",
                    "evidence_type": "anticholinergic_burden",
                    "evidence": "Amitriptyline contributes score 3.",
                    "source_url": "https://www.tandfonline.com/doi/abs/10.2217/1745509X.4.3.311",
                }
            ],
        },
        "self_prescribed_warning": "This medicine appears self-started.",
        "personalized_advice": "Ask your doctor to review this combination.",
        "disclaimer": "Consult your doctor.",
    }

    translated = translate_report(report, "hi-IN")

    finding = translated["findings"][0]
    assert translated["patient_summary"] == "HI::This combination needs review."
    assert translated["self_prescribed_warning"] == "HI::This medicine appears self-started."
    assert translated["personalized_advice"] == "HI::Ask your doctor to review this combination."
    assert translated["disclaimer"] == "HI::Consult your doctor."
    assert finding["severity"] == "major"
    assert finding["source"] == "DDInter"
    assert finding["title"] == "HI::Warfarin + Aspirin"
    assert finding["patient_explanation"] == "HI::Bleeding risk may increase."
    assert finding["doctor_explanation"] == "HI::Additive anticoagulant effect."
    assert finding["action"] == "HI::Discuss with your doctor."
    assert finding["citations"][0]["source_url"] == "https://ddinter2.scbdd.com/"
    assert finding["citations"][0]["reference_url"] == "https://pubmed.ncbi.nlm.nih.gov/12345678/"
    assert translated["acb_section"]["risk"] == "HI::High anticholinergic burden."
    assert translated["acb_section"]["score"] == 3
    assert len(calls) == 1
    assert "major" not in calls[0]
    assert "DDInter" not in calls[0]
    assert "https://ddinter2.scbdd.com/" not in calls[0]


def test_apply_report_translation_uses_finding_id_not_array_position() -> None:
    report = {
        "patient_summary": "Summary",
        "findings": [
            {
                "finding_id": "finding_1",
                "severity": "major",
                "display_severity": "major",
                "title": "Drug A + Drug B",
                "patient_explanation": "Explain A",
                "doctor_explanation": "Doctor A",
                "action": "Action A",
                "medicines": ["Drug A", "Drug B"],
                "confidence": "high",
                "source": "DDInter",
                "citations": [],
            },
            {
                "finding_id": "finding_2",
                "severity": "unknown",
                "display_severity": "doctor_review",
                "title": "Drug C + Drug D",
                "patient_explanation": "Explain B",
                "doctor_explanation": "Doctor B",
                "action": "Action B",
                "medicines": ["Drug C", "Drug D"],
                "confidence": "medium",
                "source": "PrimeKG",
                "citations": [],
            },
        ],
        "acb_section": {"score": 0, "risk": "None", "drugs": [], "citations": []},
        "self_prescribed_warning": None,
        "personalized_advice": None,
        "disclaimer": "Disclaimer",
    }

    translated_payload = {
        "patient_summary": "अनुवादित सारांश",
        "findings": [
            {
                "finding_id": "finding_2",
                "title": "Drug C + Drug D",
                "patient_explanation": "अनुवादित B",
                "doctor_explanation": "डॉक्टर B",
                "action": "कार्य B",
            },
            {
                "finding_id": "finding_1",
                "title": "Drug A + Drug B",
                "patient_explanation": "अनुवादित A",
                "doctor_explanation": "डॉक्टर A",
                "action": "कार्य A",
            },
        ],
        "acb_section": {},
    }

    translated = _apply_report_translation(report, translated_payload)

    assert translated["patient_summary"] == "अनुवादित सारांश"
    assert translated["findings"][0]["patient_explanation"] == "अनुवादित A"
    assert translated["findings"][1]["patient_explanation"] == "अनुवादित B"
    assert translated["findings"][1]["display_severity"] == "doctor_review"


def test_translate_report_sarvam_marker_loss_falls_back_to_per_field_translation(monkeypatch) -> None:
    calls: list[str] = []

    def fake_translate(text: str, source_lang: str, target_lang: str) -> str:
        calls.append(text)
        if "__SAHAYAK_FIELD_" in text:
            return "broken translated response without markers"
        return f"HI::{text}"

    monkeypatch.setattr("app.services.translation_service.translate", fake_translate)

    report = {
        "patient_summary": "English summary",
        "findings": [
            {
                "severity": "major",
                "title": "Aspirin — BEERS Criteria Flag",
                "patient_explanation": "Aspirin may be risky.",
                "doctor_explanation": "Guideline flag.",
                "action": "Review with your doctor.",
                "medicines": ["Aspirin"],
                "confidence": "high",
                "source": "AGS 2023 Beers Criteria",
                "citations": [
                    {
                        "source_key": "beers_2023",
                        "source_label": "AGS 2023 Beers Criteria",
                        "source_layer": "L1_direct",
                        "relation_type": "BEERS_FLAG",
                        "evidence_type": "geriatric_guideline",
                        "evidence": "Potentially inappropriate for older adults.",
                        "source_url": "https://doi.org/10.1111/jgs.18372",
                    }
                ],
            }
        ],
        "acb_section": {"score": 0, "risk": "No risk.", "drugs": [], "citations": []},
        "self_prescribed_warning": None,
        "personalized_advice": None,
        "disclaimer": "English disclaimer",
    }

    translated = translate_report(report, "hi-IN")

    assert translated["patient_summary"] == "HI::English summary"
    assert translated["findings"][0]["severity"] == "major"
    assert translated["findings"][0]["source"] == "AGS 2023 Beers Criteria"
    assert translated["findings"][0]["title"] == "HI::Aspirin — BEERS Criteria Flag"
    assert translated["findings"][0]["citations"][0]["source_url"] == "https://doi.org/10.1111/jgs.18372"
    assert translated["acb_section"]["risk"] == "HI::No risk."
    assert translated["acb_section"]["score"] == 0
    assert len(calls) > 1
