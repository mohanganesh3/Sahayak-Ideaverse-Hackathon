# SAHAYAK Evaluation Masterplan

## Goal
Prove that SAHAYAK is not just a demo with a knowledge graph, but a clinically-aware medication safety system tailored to:
- Indian brand names
- geriatric polypharmacy
- herb-drug interactions
- CYP450 multi-hop reasoning
- multilingual patient-safe explanation

This plan is architecture-specific to the current SAHAYAK stack:
- OCR + label parsing
- Indian brand resolution
- Neo4j direct interactions
- multi-hop CYP/QT/electrolyte reasoning
- Beers / geriatric screening
- agentic CRAG review
- multilingual report generation

## The Scoreboard To Show Judges

Show one single slide/table with these 12 numbers:

| Layer | Metric | Target |
|---|---:|---:|
| OCR | End-to-end medicine ID accuracy on Indian packages | >= 0.80 |
| Brand resolution | Top-1 Indian brand to generic accuracy | >= 0.95 |
| Direct DDI | Sensitivity on curated sentinel interactions | >= 0.95 |
| Direct DDI | Severity match accuracy | >= 0.85 |
| Herb-drug | Detection sensitivity on curated herb-drug set | >= 0.50 |
| Geriatric safety | Beers detection coverage on curated elderly list | >= 0.95 |
| Multi-hop reasoning | Path validation precision | >= 0.80 |
| Graph grounding | Relation hallucination rate | <= 0.05 |
| RAG faithfulness | Grounded answer faithfulness | >= 0.95 |
| Alert quality | Clinically important findings per 10-drug review | <= 5 |
| Usability | SUS score with elderly/caregiver users | >= 70 |
| Runtime | End-to-end P95 latency | <= 5s |

If you can show these clearly, judges will immediately understand that SAHAYAK is measured as a medical system, not as a generic chatbot.

## Evaluation Layers

### 1. OCR + Drug Label Extraction

#### What to test
- printed English strips
- bilingual English + Hindi labels
- Ayurvedic bottle labels
- blurry / angled / shadowed photos
- combination drugs
- Indian brand-only labels

#### Metrics
- Character Error Rate
- Word Error Rate
- medicine entity precision / recall / F1
- brand extraction exact match
- active ingredient extraction exact match
- dosage extraction exact match
- end-to-end medicine identification accuracy

#### SAHAYAK-specific pass criteria
- do not optimize only for raw OCR text quality
- optimize for downstream medicine correctness
- count a case as success only if the final resolved medicine list is correct enough for safety checking

#### Required datasets
- 200 easy labels
- 100 medium labels
- 100 hard labels
- at least 50 Ayurvedic/herbal products
- at least 100 Indian brand-only or India-dominant labels

### 2. Indian Brand Resolution

This is one of SAHAYAK's strongest differentiators. Evaluate it explicitly.

#### Test set
- 500 common Indian brands
- 100 OCR-corrupted versions
- 100 combination brands

#### Metrics
- exact top-1 generic match
- top-3 match
- combination ingredient Jaccard accuracy
- false normalization rate

#### Must-have examples
- Ecosprin -> aspirin
- Dolo 650 -> acetaminophen/paracetamol
- PAN 40 -> pantoprazole
- Thyronorm 50 -> levothyroxine
- Clopitab A -> aspirin + clopidogrel

#### Judge-facing claim
"Commercial global drug tools do not solve Indian brand resolution. SAHAYAK does."

### 3. Direct Drug-Drug Interaction Detection

#### Primary evaluation sets
- curated 50-sentinel interaction set
- extended 100-200 high-risk pair set
- elderly-focused interactions:
  - warfarin + aspirin
  - simvastatin + clarithromycin
  - digoxin + amiodarone
  - ACE inhibitor + spironolactone
  - metformin + contrast

#### Metrics
- sensitivity
- specificity
- PPV
- NPV
- severity exact match
- weighted kappa for severity agreement

#### SAHAYAK-specific rule
Score source-aware detection:
- direct curated edge found
- found only after synonym canonicalization
- found only after brand resolution

This demonstrates why the SAHAYAK pipeline matters.

### 4. Herb-Drug Interaction Detection

This is the most under-served area in existing tools and the best place to differentiate.

#### Separate evaluation buckets
- database-backed herb-drug edges
- curated Ayurvedic sentinel edges
- "insufficient evidence" abstentions

#### Metrics
- sensitivity on known herb-drug interactions
- abstention correctness
- dangerous false reassurance rate

#### SAHAYAK-specific safety rule
Never reward the system for saying "safe" when evidence is absent.

Use three-way scoring:
- known interaction detected
- insufficient evidence correctly abstained
- unsafe false negative

### 5. Geriatric Screening

#### Evaluate separately
- Beers Criteria
- anticholinergic burden
- renal-dose related warnings
- therapeutic duplication

#### Metrics
- Beers coverage on curated positive set
- Beers miss rate on high-risk elderly drugs
- ACB score agreement on selected drugs
- duplication precision on same-class therapies

#### High-value inputs to include
- age
- conditions
- systolic/diastolic BP
- fasting blood sugar
- post-meal blood sugar
- SpO2
- heart rate
- serum creatinine
- weight

These should be optional in the product, but part of evaluation because they materially affect clinical relevance.

### 6. Multi-hop Pharmacological Reasoning

This is the other major SAHAYAK differentiator.

#### Evaluate path types separately
- CYP inhibition
- CYP induction
- transporter mediation
- QT compounding
- potassium/hypokalemia cascades
- CNS depression compounding

#### Metrics
- path precision
- path recall on curated mechanism cases
- mechanism explanation quality
- indirect interaction discovery count
- validated indirect-to-known interaction overlap

#### Concrete test cases
- clarithromycin -> CYP3A4 -> simvastatin
- amiodarone -> P-gp -> digoxin
- rifampin -> CYP3A4 induction -> apixaban
- black pepper -> CYP3A4 -> CYP substrate drug
- turmeric -> CYP2C9 -> warfarin-like anticoagulant scenarios

#### Judge-facing claim
"We do not only say two drugs interact. We can show the biological path."

### 7. Graph Grounding / Hallucination Control

#### Why this matters
If the system explains an interaction not supported by the graph, it becomes dangerous.

#### Metrics
- relation hallucination rate
- unsupported mechanism rate
- groundedness of cited drugs/herbs/enzymes
- AI-assessed finding verification pass rate

#### How to compute
- extract every claimed relation from the generated explanation
- query Neo4j for each relation
- score supported / unsupported

### 8. Agentic RAG / CRAG Quality

#### Metrics
- completeness score calibration
- faithfulness
- answer relevancy
- context precision
- context recall
- hallucination rate
- L3 findings upgraded to verified graph-supported findings

#### SAHAYAK-specific metric
`AI rescue value = dangerous findings found by CRAG/deep analysis that direct graph lookup missed`

This is a strong demo metric because it shows the agent layer is adding value instead of just rephrasing graph output.

### 9. Latency / Reliability

Measure:
- OCR latency
- extraction latency
- graph query latency
- report generation latency
- translation latency
- full pipeline latency

Use:
- P50
- P95
- failure rate
- fallback success rate

#### Target
- direct graph answer path: < 3s
- full agentic path: < 8s
- never fail closed when translation/voice fails

### 10. Usability / Elderly Acceptance

#### Test with
- 5 to 8 elderly users or caregivers
- 3 to 5 pharmacists/medical students for clinical credibility review

#### Metrics
- SUS
- NASA-TLX
- task completion rate
- time to complete medicine scan
- correction rate after OCR
- trust score

#### Key tasks
- upload a strip
- confirm detected medicines
- understand one critical warning
- listen to translated explanation
- share report to caregiver

## The Three Most Important Judge-Demo Claims

1. "We resolve Indian brands that global tools miss."
2. "We detect herb-drug and geriatric risks that routine DDI checkers often miss."
3. "We can explain the mechanism path, not just show a red alert."

## The Ablation Study You Should Show

| Configuration | What removed | What it proves |
|---|---|---|
| Full SAHAYAK | None | Final system |
| Minus brand resolution | Indian brand map + graph brand search | Indian brand resolution matters |
| Minus CYP layer | multi-hop enzyme/transporter edges | mechanistic reasoning matters |
| Minus herb layer | DDID + curated Ayurveda | Ayurveda/herb support matters |
| Minus Beers layer | Beers + ACB | geriatric specialization matters |
| Graph only | no agentic CRAG | whether agentic review adds value |
| LLM only | no graph grounding | why grounded medical AI is safer |

## The Single Best Demo Table

| Case | GPT-only | Basic DDI checker | SAHAYAK |
|---|---|---|---|
| Indian brand combination | weak | misses | correct |
| Herb-drug risk | inconsistent | usually misses | correct |
| Elderly Beers risk | generic | limited | correct |
| CYP mechanism explanation | vague | no path | explicit path |
| Multilingual explanation | generic | none | yes |

## Architecture-Specific Metrics To Add Now

Because of SAHAYAK's current implementation, track these additional metrics:

- synonym rescue rate
  - interactions found only after canonicalization
- brand rescue rate
  - interactions found only after Indian brand mapping
- herb regional-name rescue rate
  - herb lookups recovered via Hindi/Tamil/Telugu/Kannada names
- sentinel pass rate
  - how many curated sentinel interactions are found with correct severity
- source provenance distribution
  - ddinter vs primekg vs beers vs curated vs inferred
- inferred severity rate
  - share of DDI severities inferred rather than source-native
- evidence abstention quality
  - when the system correctly says evidence is insufficient

## Optional Clinical Inputs To Add To Product + Evaluation

Use as optional, not required:
- gender
- weight_kg
- systolic_bp
- diastolic_bp
- fasting_blood_sugar
- postprandial_blood_sugar
- spo2
- heart_rate
- serum_creatinine

Use them for:
- renal risk escalation
- hypotension risk escalation
- hypoglycemia relevance
- frailty / physiological reserve interpretation
- urgency tuning in report generation

## What To Measure First In 72 Hours

If time is short, do these first:

1. 50 sentinel DDI/herb-drug cases
2. 100 Indian brand resolution cases
3. 50 photographed label OCR cases
4. 20 multi-hop CYP mechanism cases
5. 20 Beers / geriatric cases
6. 5 real full-pipeline elderly scenarios

## Primary References To Cite

- MedHELM benchmark for medical LLM evaluation
- RAGAS for faithfulness / context metrics
- TruLens RAG Triad
- DeepEval hallucination metrics
- DDInter 2.0
- PrimeKG
- 2023 AGS Beers Criteria
- STOPP/START v3
- alert-override literature and DDI clinical relevance literature

## Final Positioning

Do not claim:
- "best medical AI"
- "diagnosis"
- "doctor replacement"

Claim instead:
- "India-specific medication safety copilot for elderly polypharmacy"
- "grounded, explainable, multilingual drug safety assistant"
- "designed to reduce missed risks and reduce meaningless alerts"
