# SAHAYAK Evaluation Results Template

Use this template after running `python3 scripts/run_full_evaluation.py`.

## Executive Summary

- Run date:
- Commit / build:
- Graph version:
- Evaluation mode: `quick` / `full`
- Overall interpretation:

## Judge Scoreboard

| Layer | Metric | Actual | Target | Status | Judge Message |
|---|---|---:|---:|---|---|
| OCR | End-to-end medicine ID accuracy on Indian packages |  | >= 0.80 |  |  |
| Brand resolution | Top-1 Indian brand to generic accuracy |  | >= 0.95 |  |  |
| Direct DDI | Sensitivity on curated sentinel interactions |  | >= 0.95 |  |  |
| Direct DDI | Severity match accuracy |  | >= 0.85 |  |  |
| Herb-drug | Detection sensitivity on curated herb-drug set |  | >= 0.50 |  |  |
| Geriatric safety | Beers detection coverage on curated elderly list |  | >= 0.95 |  |  |
| Multi-hop reasoning | Path validation precision |  | >= 0.80 |  |  |
| Graph grounding | Relation hallucination rate |  | <= 0.05 |  |  |
| RAG faithfulness | Grounded answer faithfulness |  | >= 0.95 |  |  |
| Alert quality | Clinically important findings per 10-drug review |  | <= 5 |  |  |
| Usability | SUS score with elderly/caregiver users |  | >= 70 | `pending-data` until user study |  |
| Runtime | End-to-end P95 latency |  | <= 5s |  |  |

## Layer-by-Layer Results

### 1. OCR + Label Extraction

- Dataset:
- CER / WER:
- Text-fixture medicine ID accuracy:
- Dosage exact match:
- Notes:

### 2. Indian Brand Resolution

- Top-1 accuracy:
- Top-3 accuracy:
- Combination Jaccard accuracy:
- OCR-corrupted rescue rate:
- Notes:

### 3. Direct Drug-Drug Interactions

- Sentinel sensitivity:
- Severity exact match:
- Weighted kappa:
- Inferred severity rate:
- Notes:

### 4. Herb-Drug Safety

- Herb sentinel sensitivity:
- Regional-name rescue rate:
- Abstention correctness:
- Dangerous false reassurance rate:
- Notes:

### 5. Geriatric Safety

- Beers coverage:
- ACB sample accuracy:
- Duplication precision:
- Renal warning case support:
- Notes:

### 6. Multi-hop Pharmacological Reasoning

- Graph path precision:
- Engine path recall:
- Discoverable indirect pairs:
- Validated indirect overlap:
- Notes:

### 7. Graph Grounding / Hallucination Control

- Grounded finding pass rate:
- Relation hallucination rate:
- Faithfulness proxy:
- Notes:

### 8. Full Pipeline Cases

- Scenario pass rate:
- Alerts per 10 drugs:
- Report generation success rate:
- Clinical context acceptance rate:
- Notes:

## Ablation / Baseline Ideas

- Remove Indian brand resolution
- Remove synonym canonicalization
- Remove multi-hop reasoning
- Remove agentic review
- Remove herb layer

## Remaining Gaps

- Missing datasets:
- Pending-data metrics:
- Known product limitations:

## Final Claim

Write the one-paragraph version you will say to judges:

> SAHAYAK is not just a chatbot. We evaluated it as a layered medical safety system for Indian elderly patients, from OCR and Indian brand resolution through graph-grounded DDI detection, herb safety, geriatric screening, multi-hop CYP reasoning, and multilingual report generation.
