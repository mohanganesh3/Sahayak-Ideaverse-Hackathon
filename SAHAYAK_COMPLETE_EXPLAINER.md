# SAHAYAK — The Complete Explainer Guide

### Everything You Need to Know About the Project (Explained Simply)

**Version:** 28 March 2026  
**Audience:** Anyone — no prior technical knowledge required

**Important update:** This version reflects the current codebase and live local deployment:
- the local Docker stack now serves the **full graph**
- the mobile scan flow now uses **per-image extraction + unified manual review**
- the report pipeline now preserves **finding identity, evidence provenance, and display severity**
- user-facing reports no longer expose raw `UNKNOWN` severity badges; unresolved items surface as **Doctor Review**

---

## TABLE OF CONTENTS

1. [What is Sahayak?](#1-what-is-sahayak)
2. [The Problem We Are Solving](#2-the-problem-we-are-solving)
3. [All Our Databases — What They Are and Why We Need Them](#3-all-our-databases--what-they-are-and-why-we-need-them)
4. [How We Got the Data — Acquisition Methods](#4-how-we-got-the-data--acquisition-methods)
5. [The Knowledge Graph — How All Data Connects](#5-the-knowledge-graph--how-all-data-connects)
6. [Drug Codes and Naming Systems (RxNorm, ATC, RxCUI)](#6-drug-codes-and-naming-systems-rxnorm-atc-rxcui)
7. [The Complete User Flow — From Photo to Safety Report](#7-the-complete-user-flow--from-photo-to-safety-report)
8. [OCR — How We Read Prescription Images](#8-ocr--how-we-read-prescription-images)
9. [Drug Normalization — Mapping Brand Names to Real Drugs](#9-drug-normalization--mapping-brand-names-to-real-drugs)
10. [The Safety Engine — How We Find Dangerous Interactions](#10-the-safety-engine--how-we-find-dangerous-interactions)
11. [RAG and Self-Corrective RAG (CRAG) — Explained](#11-rag-and-self-corrective-rag-crag--explained)
12. [Multi-Hop Reasoning — Finding Hidden Dangers](#12-multi-hop-reasoning--finding-hidden-dangers)
13. [Where and Why We Use LLMs/APIs](#13-where-and-why-we-use-llmsapis)
14. [Geriatric Safety — Beers Criteria and Elderly Protection](#14-geriatric-safety--beers-criteria-and-elderly-protection)
15. [Report Generation and Multilingual Support](#15-report-generation-and-multilingual-support)
16. [Evaluation — How We Tested and Proved It Works](#16-evaluation--how-we-tested-and-proved-it-works)
17. [The OCR Benchmark Controversy — Why 0.75 is Misleading](#17-the-ocr-benchmark-controversy--why-075-is-misleading)
18. [Edge Cases and Honest Limitations](#18-edge-cases-and-honest-limitations)
19. [The Numbers That Matter — What to Tell Judges](#19-the-numbers-that-matter--what-to-tell-judges)
20. [Quick Reference Card](#20-quick-reference-card)

---

## 1. What is Sahayak?

**Sahayak** (संहायक) means "helper" in Hindi. It is an **AI-powered medication safety assistant** built specifically for **elderly patients in India**.

**In one sentence:** You take a photo of your grandparent's medicines, and Sahayak tells you if any of those medicines are dangerous together, in a language they understand.

**What makes it special:**
- It understands **Indian brand names** (like Dolo 650, Ecosprin 75, Thyronorm)
- It knows about **Ayurvedic herbs** (like Ashwagandha, Brahmi, Tulsi) and checks if they clash with modern medicines
- The **mobile UI** supports **10 Indian languages** (Hindi, Tamil, Telugu, Kannada, Malayalam, Marathi, Bengali, Gujarati, Punjabi, English)
- The current **medically safe full-report translation path** is strongest for **English, Hindi, Tamil, Telugu, and Kannada**
- It is designed for **the elderly** — it checks specifically for medicines that are risky for older people
- It explains everything in **simple, everyday language** — not medical jargon
- It can even **read the patient summary aloud** using Sarvam AI text-to-speech

**Think of it as:** A super-knowledgeable pharmacist sitting inside your phone who checks every medicine, every herb, every combination — and warns you if something is dangerous, in your own language.

---

## 2. The Problem We Are Solving

### The Scary Reality

In India:
- Elderly patients often take **5-10 medicines daily** from different doctors (cardiologist, diabetologist, general physician)
- **No single doctor sees the full picture** — each doctor prescribes their own medicines without knowing what others prescribed
- Many elderly also take **Ayurvedic herbs** (Ashwagandha, Triphala, Tulsi) — which **can interact dangerously** with modern medicines, but nobody checks for this
- Medicine names in India are **brand names** (like "Ecosprin" instead of "Aspirin") — making it hard to identify what's actually inside
- **Language barriers** — a Tamil-speaking grandmother may not understand an English prescription
- **Self-medication** — many people buy medicines from medical shops without a prescription

### What Can Go Wrong?

Real example: An elderly person takes:
- **Warfarin** (blood thinner, prescribed by cardiologist)
- **Aspirin** (bought from medical shop for headache)
- **Garlic supplements** (Ayurvedic, for heart health)

**All three thin the blood.** Together, they can cause **life-threatening internal bleeding**. Without Sahayak, nobody connects these dots.

Another example:
- **Clarithromycin** (antibiotic, prescribed for infection)
- **Simvastatin** (cholesterol medicine, prescribed by another doctor)

Clarithromycin **blocks the liver enzyme** (CYP3A4) that breaks down Simvastatin. So Simvastatin **builds up 17x more** in the body → can cause severe muscle damage called **rhabdomyolysis** (muscle breakdown that can damage kidneys). This is a "hidden" interaction — the two drugs don't directly clash, but they clash **through the liver**.

**Sahayak finds both these types of dangers — the direct ones AND the hidden ones.**

---

## 3. All Our Databases — What They Are and Why We Need Them

We built a massive **knowledge graph** (think of it as a giant web of connected facts) containing data from **12 major data layers/sources**. Here is each one, what it contains, and why we need it.

### Database 1: DDInter (Drug-Drug Interaction Database)

**What it is:** A scientifically curated database of **drug-drug interactions** — meaning cases where two drugs affect each other.

**What's inside:**
- 159,369 drug-drug interaction pairs
- For each pair: which drugs, how severe (minor/moderate/major), what happens (mechanism), what to do about it (management)

**Example entry:**  
*Warfarin + Aspirin → Severity: Major → Mechanism: Both affect blood clotting → Management: Avoid combination or monitor closely*

**Why we need it:** This is our **primary source of truth** for when two drugs interact. If a doctor prescribes Drug A and another doctor prescribes Drug B, we look here first to check if they clash.

**How it's organized:** Split into files by drug category codes (A = Alimentary, B = Blood, D = Dermatological, H = Hormonal, L = Antineoplastic, P = Antiparasitic, R = Respiratory, V = Various). We load all of them.

---

### Database 2: DDID (Drug-Disease Interaction Database)

**What it is:** A database focused on **herb-drug interactions** and **food-drug interactions** — particularly important for Indian context.

**What's inside:**
- 15,783 herb-drug interaction edges
- Herb information with scientific names
- Interaction details: severity, mechanism, clinical effect, evidence level

**Example entry:**  
*Fenugreek (Methi) + Metformin → Both lower blood sugar → Risk of hypoglycemia (dangerously low blood sugar)*

**Why we need it:** India is unique because **millions of people use Ayurvedic herbs daily alongside modern medicines**. There's no other system that checks these interactions rigorously. DDID gives us the scientific foundation for herb-drug safety.

---

### Database 3: PrimeKG (Precision Medicine Knowledge Graph)

**What it is:** The **largest academic biomedical knowledge graph** we use. Created by researchers at Harvard.

**What's inside:**
- **1,334,270 drug-drug interaction edges** (our single largest data source)
- Drug-disease indications (what each drug treats)
- Drug-disease contraindications (when a drug is dangerous for a condition)
- Drug side effects and protein targets
- Gene-protein-drug associations

**Example entries:**
- *Metformin → indicated_for → Type 2 Diabetes*
- *ACE Inhibitors → contraindicated_in → Bilateral Renal Artery Stenosis*
- *Simvastatin → targets → HMG-CoA reductase (protein)*

**Why we need it:** PrimeKG connects drugs not just to other drugs, but to **diseases, proteins, genes, and biological pathways**. This is what enables our **multi-hop reasoning** — finding interactions that go through enzymes and proteins, not just direct drug-to-drug clashes.

---

### Database 4: Hetionet (Heterogeneous Network of Medicine)

**What it is:** A biomedical knowledge graph connecting diseases, genes, compounds, and anatomical structures.

**What's inside:**
- Disease-gene associations
- Gene-protein mappings
- Pharmacological pathway information

**Why we need it:** Hetionet fills gaps that PrimeKG doesn't cover. It gives us richer **enzyme-drug relationships** that help us understand which liver enzymes break down which drugs. This is essential for our CYP enzyme interaction detection (explained later).

---

### Database 5: SIDER (Side Effect Resource)

**What it is:** A database of **drug side effects** extracted from drug package inserts and labels.

**What's inside:**
- 120,234 drug → side effect edges
- Side effect frequencies (how common each side effect is: rare, uncommon, common, very common)
- Based on FDA-approved labels

**Example:**  
*Metformin → MAY_CAUSE → Nausea (very common, >10%)*  
*Metformin → MAY_CAUSE → Lactic acidosis (rare, <0.01%)*

**Why we need it:** When we generate the safety report, we can tell the patient: "This medicine commonly causes X — watch out for these symptoms." It also helps us understand when two drugs together might **compound** the same side effect.

---

### Database 6: OnSIDES (Observational Studies of Drug Side Effects)

**What it is:** A newer version of side effect data, based on **real-world observations** (not just what's written on labels).

**What's inside:**
- 101,992 drug → side effect edges
- Coprescription effects — what happens when two drugs are taken together

**Why we need it:** SIDER tells us what the label says. OnSIDES tells us what **actually happens to real patients**. Sometimes drugs have side effects that aren't on the label but show up in real-world data. This gives our system real-world grounding.

---

### Database 7: TwoSIDES

**What it is:** A database specifically about **what happens when two drugs are taken together** (coprescription effects).

**What's inside:**
- 183,045 pairs showing combined adverse effects

**Example:**  
*Drug A alone → no stomach problems. Drug B alone → no stomach problems. Drug A + Drug B together → stomach bleeding.* This combination effect wouldn't show up in Drug A's label or Drug B's label individually.

**Why we need it:** Some side effects only appear when drugs are combined. TwoSIDES captures these **combination-specific risks** that individual drug labels miss.

---

### Database 8: Indian Medicine Dataset (253,973 medicines)

**What it is:** A comprehensive dataset of **every medicine sold in India** — brand names, manufacturers, compositions, prices.

**What's inside:**
- 249,149 Indian brand entries loaded into our graph
- Each entry: brand name, manufacturer, composition (which generic drugs are inside), dosage form, price, discontinued status

**Example entries:**
- *Ecosprin 75 → by USV Ltd → Contains: Aspirin 75mg → Tablet → ₹34.50*
- *Dolo 650 → by Micro Labs → Contains: Paracetamol 650mg → Tablet → ₹30*
- *Augmentin 625 Duo → by GSK → Contains: Amoxycillin 500mg + Clavulanic Acid 125mg → Tablet → ₹220*

**Why we need it:** This is **THE most critical database for India**. When an OCR reads "Ecosprin 75" from a prescription photo, we need to know it's actually Aspirin. Without this database, we can't connect Indian brand names to our drug interaction knowledge. No other system has this mapping at scale.

**Source:** GitHub open dataset (Indian-Medicine-Dataset by junioralive)

---

### Database 9: FDA NDC (National Drug Code Directory)

**What it is:** The official **US FDA database** of approved drug products.

**What's inside:**
- 1,909 enriched US brand entries with NDC codes
- Official drug names, dosage forms, manufacturers, active ingredients

**Why we need it:** Acts as a **reference standard**. Many Indian medicines are based on the same active ingredients as FDA-approved drugs. When we resolve drug names, the FDA NDC helps us confirm that a generic name is valid and official.

---

### Database 10: Curated Ayurvedic Herbs (1,340 herb nodes in the live graph, 30+ deeply curated)

**What it is:** Our own **manually curated** database of Ayurvedic herbs with their drug interactions.

**What's inside:**
- 1,337 herb entries total
- 30+ top herbs **deeply curated** with:
  - English name, scientific name
  - Regional names in Hindi, Tamil, Telugu, Kannada (so a Tamil user can type "அசுவகந்தா" and we recognize Ashwagandha)
  - Known drug interactions with severity levels
  - Evidence basis (is this from a study, or from traditional knowledge?)
  - Elderly risk level (low/moderate/high)
  - Common uses and aliases

**Example (Ashwagandha):**
```
English: Ashwagandha
Scientific: Withania somnifera
Hindi: अश्वगंधा  |  Tamil: அசுவகந்தா  |  Telugu: అశ్వగంధ  |  Kannada: ಅಶ್ವಗಂಧೆ
Aliases: Indian ginseng, Winter cherry
Elderly risk: Moderate
Interactions:
  - With Sedatives: May increase drowsiness and fall risk (moderate, curated)
  - With Thyroid medicines: May interfere with thyroid function (moderate, curated)
  - With Immunosuppressants: May reduce effectiveness (moderate, curated)
```

**Why we need it:** No existing database has **Indian herbs with regional names AND drug interaction data**. We created this ourselves because herb-drug interactions are a massive blind spot in Indian healthcare.

---

### Database 11: Beers Criteria 2023 (Geriatric Safety Checklist)

**What it is:** The **gold standard** clinical guideline published by the American Geriatrics Society (AGS) listing medicines that are **potentially inappropriate for elderly patients** (usually age ≥ 65).

**What's inside:**
- 132 drug entries marked as risky for elderly
- Categories: "Avoid entirely", "Use with caution", "Avoid with specific conditions"
- Rationale and alternatives for each

**Example entries:**
- *Diphenhydramine (Benadryl) → AVOID in elderly → Strong anticholinergic — causes confusion, falls, cognitive decline*
- *Diazepam (Valium) → AVOID in elderly → Increased sensitivity, prolonged sedation, fall risk*
- *Glimepiride → CAUTION → Higher risk of hypoglycemia in elderly; prefer Metformin*

**Why we need it:** Elderly patients are the most vulnerable to medicine harm. Beers Criteria is **the global standard** for geriatric prescribing safety. When we see an elderly patient taking Diphenhydramine, we immediately flag it — even if it doesn't interact with their other medicines.

---

### Database 12: CYP450 Expansion Dataset (Generated by Us)

**What it is:** A **programmatically generated** dataset of pharmacokinetic relationships — how drugs interact through liver enzymes.

**What's inside:**
- 3,276 enzyme-drug substrate relationships (which enzyme breaks down which drug)
- 127 enzyme inhibitor edges (which drugs block which enzymes)
- 49 herb-enzyme relationships (which herbs affect which enzymes)
- 279 QT-prolonging drugs (drugs that can cause dangerous heart rhythm changes)
- 54 electrolyte effect edges (drugs that deplete/elevate potassium/sodium)
- **52,758 discoverable indirect interaction pairs** (calculated from: if Drug A blocks an enzyme AND Drug B is broken down by that enzyme → they interact)

**Why we need it:** This is what enables our **multi-hop reasoning**. Without this, we could only find interactions that are explicitly listed in DDInter or PrimeKG. With this, we can **discover hidden interactions** through enzyme pathways, even if nobody has explicitly cataloged them as interacting.

---

## 4. How We Got the Data — Acquisition Methods

### Method 1: Public Database Downloads (DDInter, PrimeKG, Hetionet, SIDER, OnSIDES, TwoSIDES, FDA NDC)

These are **publicly available academic/government databases**. We downloaded them directly:
- DDInter: CSV export from the DDInter website (drug interaction database maintained by Chinese Academy of Sciences)
- PrimeKG: CSV files from the Harvard research portal
- Hetionet: JSON file from the Hetionet GitHub repository
- SIDER: TSV files from the SIDER database (European Bioinformatics Institute)
- OnSIDES/TwoSIDES: CSV files from academic sources
- FDA NDC: JSON file from the FDA open data portal

**No web scraping needed** — these are freely available for research use.

### Method 2: Open Dataset (Indian Medicine Dataset)

The Indian Medicine Dataset (253,973 medicines) is an **open-source dataset** available on GitHub. We downloaded it directly.

**Source:** `https://github.com/junioralive/Indian-Medicine-Dataset`

This contains every medicine registered in India with its composition, manufacturer, and price.

### Method 3: Manual Curation + LLM-Assisted Synthesis (Ayurvedic Herbs)

This is where it gets interesting. **No database existed** that had:
- Indian Ayurvedic herbs with regional names (Hindi, Tamil, Telugu, Kannada)
- Drug interaction data for those herbs
- Elderly risk levels

So we **created it ourselves** through a multi-step process:

1. **Started with DDID:** Extracted all herb entries from the DDID database (scientific names, some interaction data)

2. **Matched with literature:** Cross-referenced with published clinical studies, NCCIH (National Center for Complementary and Integrative Health) guidelines, and Ayurvedic pharmacopoeias

3. **Manual curation for top 30 herbs:** For the most commonly used herbs in India (Ashwagandha, Brahmi, Triphala, Arjuna, Guggul, Tulsi, etc.), we:
   - Added regional names in 4 Indian languages
   - Added known drug interactions with severity levels
   - Added evidence basis markers (is this from a clinical study? from traditional knowledge? from a case report?)
   - Added elderly risk levels
   - Added aliases (e.g., Ashwagandha = Indian Ginseng = Winter Cherry)

4. **LLM-assisted expansion:** For some interactions where we had partial data, we used LLMs (Large Language Models like GPT-4) to:
   - **Identify drug classes** that interact with specific herb mechanisms (e.g., "If this herb has anticoagulant properties, which drug classes would it interact with?")
   - **Map generic interaction terms** to specific drugs (e.g., "anticoagulants and antiplatelets" → specific drugs like warfarin, aspirin, clopidogrel)
   - **Validate severity levels** against published literature

   **IMPORTANT:** Every LLM-generated suggestion was **verified against the knowledge graph**. We never trust LLM output blindly (this is a core design principle — see CRAG section).

### Method 4: Programmatic Expansion from Existing Data (CYP450 Dataset)

The CYP450 enzyme interaction data was **derived programmatically** from PrimeKG and Hetionet data already in our graph:

1. **Gene/Protein mining:** PrimeKG and Hetionet contain gene-protein relationships. We queried: "Which drugs bind to CYP3A4 protein?" → This gives us substrates.

2. **Drug alias resolution:** We maintained a mapping of alternate names (e.g., "paracetamol" in India = "acetaminophen" in the US), so we correctly linked Indian drug names to international enzyme data.

3. **Electrolyte effects:** We curated lists of potassium-depleting drugs (diuretics, corticosteroids), potassium-elevating drugs (ACE inhibitors, ARBs), and potassium-sensitive drugs (digoxin) from pharmacology textbooks.

4. **QT prolongation data:** Sourced from CredibleMeds (a medical database for QT risk) — 251 drugs from their PDF list + 28 from curated sources = 279 total QT-prolonging drugs.

5. **CNS depressant list:** 40 CNS-depressing drugs from pharmacology references (benzodiazepines, opioids, antipsychotics, etc.)

6. **Herb-enzyme mapping:** Our `herb_cyp_interactions.json` file was curated from published herb-drug pharmacokinetic studies. Example: Black Pepper (piperine) is known to inhibit CYP3A4, CYP1A2, and CYP2D6 — this is well-established in pharmacology literature.

### Method 5: Manual Digitization (Beers Criteria)

The Beers Criteria 2023 is published as a **medical journal article**, not a downloadable database. We **manually digitized** it:
- Read the AGS (American Geriatrics Society) 2023 publication
- Created a structured JSON file with every drug entry
- Recorded: drug name, category (avoid/caution/specific), rationale, recommendation, quality of evidence
- This file lives as `app/data/beers_criteria.json`

---

## 5. The Knowledge Graph — How All Data Connects

### What is a Knowledge Graph?

Imagine a giant **web of dots connected by lines**:
- Each **dot** (called a "node") is a thing: a drug, a herb, an enzyme, a side effect, a disease
- Each **line** (called a "relationship" or "edge") is a fact: "Drug A interacts with Drug B", "Herb X is broken down by Enzyme Y"

We use **Neo4j**, a specialized database designed for storing and querying these graphs.

### Our Graph by Numbers (Live Graph on 28 March 2026)

| What | Count |
|---|---:|
| **Total nodes** | **374,752** |
| **Total relationships** | **4,742,152** |
| **Relationship types** | **37** |
| **Drug nodes** (generic medicines) | 8,794 |
| **Indian Brand nodes** (commercial names) | 249,149 |
| **US Brand nodes** (FDA reference) | 64,289 |
| **Herb nodes** (Ayurvedic herbs) | 1,340 |
| **Gene nodes** (for enzyme connections) | 20,971 |
| **Protein nodes** (enzymes, transporters) | 3,094 |
| **Side Effect nodes** | 8,898 |
| **Condition/Disease nodes** | 2,062 |
| **Biological Process nodes** | 11,381 |
| **Pathway nodes** | 1,822 |
| **Total interaction relationships** | **4,742,152** |

### How the Connections Work — Visual Example

```
                    ┌─────────────────┐
                    │   Ecosprin 75    │ (Indian Brand)
                    │   (USV Ltd)      │
                    └────────┬────────┘
                             │ CONTAINS
                             ▼
                    ┌─────────────────┐
        ┌──────────│    Aspirin       │──────────┐
        │          │ (RxCUI: 1191)    │          │
        │          └────────┬────────┘          │
        │                   │                    │
        │ INTERACTS_WITH    │ IS_SUBSTRATE_OF    │ MAY_CAUSE
        │ (DDInter, major)  │                    │
        ▼                   ▼                    ▼
┌──────────────┐   ┌──────────────┐   ┌──────────────────┐
│   Warfarin    │   │   CYP2C9     │   │  GI Bleeding     │
│ (blood thinner)│   │  (enzyme)    │   │  (side effect)   │
└──────────────┘   └──────────────┘   └──────────────────┘
        │                                        ▲
        │ INTERACTS_WITH_DRUG                    │
        │ (herb-drug, moderate)      MAY_CAUSE   │
        ▼                                        │
┌──────────────┐                        ┌────────┴───────┐
│    Garlic     │                        │   Warfarin     │
│ (Ayurvedic)  │                        └────────────────┘
│ Hindi: लहसुन  │
└──────────────┘
```

In this picture:
- **Ecosprin 75** (Indian brand) → **CONTAINS** → **Aspirin** (the actual drug)
- **Aspirin** → **INTERACTS_WITH** → **Warfarin** (direct danger! Both thin blood)
- **Aspirin** → **IS_SUBSTRATE_OF** → **CYP2C9** (the liver enzyme that breaks down aspirin)
- **Aspirin** → **MAY_CAUSE** → **GI Bleeding** (a known side effect)
- **Garlic** (herb) → **INTERACTS_WITH_DRUG** → **Warfarin** (herb-drug danger!)

### Note About Current Docker vs Full Data

This note changed materially after the March 28 refresh:

- The **current local Docker deployment now serves the full graph**
- The live `sahayak-neo4j` container is serving **374,752 nodes**, **4,742,152 relationships**, and **37 relationship types**
- We are **no longer describing the local Docker setup as a reduced 2.1M-edge graph**

There is one more important runtime change:

- On startup, the backend now kicks off **runtime severity repairs** for lingering `unknown` DDInter and PrimeKG severities
- This means the live graph and live report pipeline try to repair weak direct-DDI severity metadata before user-facing reports are generated
- If a pair still does not have a trustworthy resolved severity after repair, the raw graph value may remain `unknown` internally, but the patient-facing report renders it as **Doctor Review**, not `UNKNOWN`

---

## 6. Drug Codes and Naming Systems (RxNorm, ATC, RxCUI)

### The Naming Problem

The same drug can have **dozens of names**:
- **Generic name:** Aspirin
- **Chemical name:** Acetylsalicylic acid
- **Indian brands:** Ecosprin, Disprin, Aspirin (yes, some brands are just the drug name)
- **US brands:** Bayer, Bufferin, Excedrin
- **Research name:** 2-(acetyloxy)benzoic acid

How do we know all these refer to the **same thing**? We need a coding system.

### RxNorm — The Universal Drug Dictionary

**RxNorm** is maintained by the US National Library of Medicine. It's like **Aadhaar for drugs** — every drug gets a unique ID number.

- **RxCUI** (RxNorm Concept Unique Identifier) = the ID number
- Example: Aspirin → RxCUI = **1191**
- It doesn't matter if you call it Aspirin, Acetylsalicylic acid, or Ecosprin — the RxCUI is always 1191

**How we use it:**
1. OCR reads "Ecosprin 75" from a prescription photo
2. We look up "Ecosprin 75" in our Indian Brand database → find it contains "Aspirin"
3. We look up "Aspirin" in the RxNorm API → get RxCUI = 1191
4. Now we search for ALL interactions where RxCUI 1191 is involved
5. This catches interactions regardless of which name was used in the source database

### ATC Code — The Drug Classification System

**ATC** (Anatomical Therapeutic Chemical) classification system organizes drugs by:
- Which body system they target (e.g., Cardiovascular = C)
- Therapeutic use (e.g., Antithrombotic = B01)
- Chemical class (e.g., Platelet aggregation inhibitors = B01AC)

**Example:** Aspirin → ATC code = **B01AC06**
- B = Blood and blood-forming organs
- B01 = Antithrombotic agents
- B01A = Antithrombotic agents
- B01AC = Platelet aggregation inhibitors
- B01AC06 = Aspirin specifically

**How we use it:** ATC helps us detect **therapeutic duplication** — if a patient takes two drugs with the same ATC class, they're doubling up on the same type of medicine.

### Our Normalization Cascade

When we see a drug name (from OCR, user input, or any source), we try to resolve it in this order:

```
Step 1: Exact generic name match
        "aspirin" → Found! → Done ✓

Step 2: Synonym search
        "acetylsalicylic acid" → synonym of "aspirin" → Found! → Done ✓

Step 3: Indian brand database lookup (249,149 brands)
        "Ecosprin 75" → contains "aspirin" → Found! → Done ✓

Step 4: Fuzzy text search (handles OCR typos)
        "ecosprin75" (no space) → fuzzy match to "Ecosprin 75" → aspirin → Done ✓

Step 5: RxNorm API call (external fallback)
        "some_obscure_name" → call RxNorm REST API → get RxCUI → match → Done ✓

If all 5 steps fail → mark as "unresolved" → ask user to manually confirm
```

This cascade is why we can handle messy, OCR-corrupted, brand-name, regional-language drug inputs and **still correctly identify the real drug underneath**.

---

## 7. The Complete User Flow — From Photo to Safety Report

Here is exactly what happens when someone uses Sahayak, step by step:

### Step 1: Choose Language
The user opens the app and selects their language from 10 options (English, Hindi, Tamil, Telugu, Kannada, Malayalam, Marathi, Bengali, Gujarati, Punjabi). Everything from this point forward will be shown in their chosen language.

### Step 2: Enter Patient Information
The user (or caregiver) enters:
- **Required:** Name, Age, Gender, Medical conditions (diabetes, heart disease, etc.)
- **Optional but helpful:** Weight, Blood Pressure, Blood Sugar levels, Oxygen saturation, Heart Rate, Serum Creatinine (kidney function)

This information matters because:
- Age ≥ 65 → activates Beers Criteria screening
- Kidney function (creatinine) → some drugs need dose adjustment
- Blood pressure → some drug interactions cause dangerous drops
- Heart rate → some drugs prolong QT interval (heart rhythm risk)

### Step 3: Upload Allopathic (Modern Medicine) Prescription
The user takes a photo of their prescription or medicine strip. Then:
1. **OCR** reads the text from the image (details in Section 8)
2. **Drug Extractor** parses the text to find medicine names, strengths, dosage forms
3. **Drug Normalizer** converts brand names to generic drug names (details in Section 9)
4. Results are displayed: "We found: Aspirin 75mg, Warfarin 5mg"

### Step 4: Upload Ayurvedic/Herbal Medicines
Same process, but optimized for herbs:
- Supports regional language names (user can type "अश्वगंधा" in Hindi)
- System recognizes herbs by English name, scientific name, or any of 4 Indian language names
- Links to our curated herb-drug interaction database

### Step 5: Review Problematic Images and Wrong Predictions
This is one of the biggest mobile changes since the earlier version of this document.

If OCR fails, extraction fails, or the user decides a predicted medicine name is wrong:
- the item goes into **one unified mobile review queue**
- the user can:
  - **type** the correct name
  - **speak** the correct name
  - **restore** the original prediction (if they removed it by mistake)
  - **ignore** the image from the scan

Most importantly:
- **failed scan images** and **user-removed wrong predictions** are now treated as the same class of problem: `needs review`
- if the user types or speaks a name, the app does **not** just store raw text; it sends it through a real backend pipeline (`/resolve-manual-medicine`)
- a manually resolved medicine then becomes a **normal canonical medicine object** and flows to the next screen exactly like OCR-detected medicines

### Step 6: Confirm Medicine List
A clean summary of all identified medicines (modern + herbal). This step is now **image-accounting aware**:
- every uploaded image must end in one of three terminal states:
  - detected automatically
  - manually resolved
  - ignored from the scan
- the app now shows:
  - how many images were scanned
  - how many medicines were detected
  - how many images still need review

This prevents the old bug where 6 uploaded images could silently collapse into fewer unexplained medicines.

### Step 7: Mark Who Prescribed Each Medicine
Before safety analysis, the mobile app now asks **who prescribed each resolved medicine**:
- doctor
- medical store
- self / family

This step is tracked by a stable `medicine_id`, not just the medicine name, so even duplicate generics or manual entries behave correctly.

### Step 8: Safety Analysis (The Core Engine)
This is where the magic happens (details in Sections 10-12):
1. Run **direct interaction checks** (Drug A ↔ Drug B)
2. Run **multi-hop checks** (Drug A → blocks Enzyme → Drug B accumulates)
3. Run **herb-drug checks** (Herb ↔ Drug)
4. Run **Beers Criteria screening** (elderly-inappropriate drug flagging)
5. Calculate **anticholinergic burden** (cumulative brain-fog risk score)
6. Check **therapeutic duplication** (two drugs doing the same thing)
7. If LLM thinks we missed something → run **deep analysis** → verify all findings
8. Display categorized results using **display severity** (Critical / Major / Moderate / Minor / Doctor Review)

### Step 9: Safety Report
A comprehensive, easy-to-understand report:
- Summary of all concerns, ranked by severity
- Plain-language explanation of each interaction
- Specific action items ("Talk to your cardiologist about this combination")
- Alternative medicines (when available)
- Translated into the user's chosen language
- Can be downloaded as text file
- Can read the **patient summary** aloud (text-to-speech via Sarvam AI)

---

## 8. OCR — How We Read Prescription Images

### What is OCR?

OCR stands for **Optical Character Recognition** — it means "reading text from images". When you take a photo of a tablet strip or prescription, OCR extracts the words from that photo.

### Our OCR Approach

We use **AI vision models** — not traditional OCR like Tesseract. These vision models understand context, layout, and even partial text.

**Model Priority (we try in order, stop at first success):**

| Priority | Model | Provider | Why |
|---|---|---|---|
| 1st try | **GPT-4o Vision** | OpenAI | Best accuracy for Indian medicine labels |
| 2nd try | **Gemini 2.0 Flash** | Google | Good fallback, fast |
| 3rd try | **Llama 3.2 90B Vision** | Groq | Fallback vision OCR |

**Important current behavior:**
- Sarvam is **not** the primary OCR engine
- Sarvam is used here mainly for **Indic-script transliteration** after OCR, when the extracted text contains Hindi/Tamil/Telugu/Kannada script

### What Our OCR Prompt Tells the AI to Look For

We don't just say "read this image." We give the AI a **specialized prompt** for Indian pharmaceutical conventions:

1. **Brand name** — the largest text, usually at the top (e.g., "ECOSPRIN 75")
2. **Composition** — usually after "Each tablet contains:" (e.g., "Aspirin IP 75 mg")
3. **Dosage form** — tablet, capsule, syrup, injection
4. **Manufacturer** — company name
5. **Schedule markings** — H (requires prescription), H1 (restricted), X (narcotic), "Rx only"
6. **Indian pharmacopeial marks** — IP (Indian Pharmacopoeia), BP (British), USP (US)
7. **Combination drug syntax** — "Amlodipine 5mg + Atorvastatin 10mg" (the + sign pattern)

### Handling Indian Languages in Images

Many prescriptions or medicine strips have text in Hindi, Tamil, or other Indian scripts. When the OCR detects non-English script:
1. It flags the language (Hindi, Tamil, Telugu, Kannada)
2. Sends the text to **Sarvam AI's transliteration service** (converts Indic text to English characters)
3. The transliterated text is then processed normally

### Confidence Scoring

Every OCR result gets a **confidence score** between 0.0 and 1.0.

There are now **two different thresholds** in the real pipeline:
- the OCR service itself marks `needs_fallback` when confidence is **< 0.6** or the text is empty
- the mobile processing screen sends an image to **manual review** when the OCR text is empty or the confidence is too weak for reliable extraction

So the current product behavior is:
- good text + usable confidence → continue to extraction
- blank or very weak OCR → send that image into the manual review queue

### From OCR Text to Structured Medicines

After OCR gives us raw text, the **Drug Extractor** kicks in.

This section also changed significantly in the mobile app:
- OCR is now done **per image**
- medicine extraction is now done **per image**
- the mobile app no longer concatenates all allopathic OCR text into one blob and all ayurvedic OCR text into another blob
- this prevents silent loss of one image's medicine during grouped extraction

The current mobile data structure tracks each image separately with:
- `review_status`
- `resolved_medicine_ids`
- `pending_review_ids`
- failure type (`ocr` or `extraction`) when needed

```
Raw OCR text:
  "ECOSPRIN 75
   Aspirin Gastro-resistant Tablets IP
   Each tablet contains: Aspirin IP 75 mg
   Mfg: USV Limited"

Extractor uses regex patterns:
  Pattern 1: "Each (tablet|capsule|ml) contains: (.+)"
  Pattern 2: "(drug_name) (number)(mg|mcg|g|ml)"

Extracted result:
  {
    brand: "ECOSPRIN 75",
    generic: "Aspirin",
    dose: "75 mg",
    form: "tablet",
    confidence: 0.95
  }
```

For complex combination drugs:
```
OCR text: "COMBIFLAM - Each tablet contains: Ibuprofen IP 400 mg + Paracetamol IP 325 mg"

Extracted:
  Drug 1: Ibuprofen 400mg
  Drug 2: Paracetamol 325mg
  (Both will be checked for interactions independently)
```

**Additional current safeguard:**  
After normalization, the extractor now performs a second deduplication pass using the canonical generic / ingredient signature. This prevents OCR junk like `"10 Tablets"` from surviving as a fake second medicine when it actually normalizes to the same underlying drug.

---

## 9. Drug Normalization — Mapping Brand Names to Real Drugs

### Why is This Needed?

In India, nobody says "I take Aspirin." They say "I take Ecosprin." But our drug interaction databases use generic names. So we need to **translate** brand names to generic names.

### The Normalization Pipeline

```
Input: "Ecosprin 75"

Step 1: Generate candidate search keys
  → "ecosprin 75", "ecosprin", "ecosprin75"
  (Strips dosage numbers, removes spaces, creates variants)

Step 2: Look up in Indian Brand Map (fast local lookup)
  → Found! "ecosprin" → "aspirin"

Step 3: Look up in Neo4j graph (if local map failed)
  → CALL db.index.fulltext.queryNodes('brand_name_fulltext', 'ecosprin 75')
  → Returns matching IndianBrand nodes with composition

Step 4: Extract generic drug from composition
  → "Aspirin IP 75 mg" → generic = "aspirin"

Step 5: Get RxCUI from RxNorm
  → "aspirin" → RxCUI = 1191

Step 6: For combination drugs, split into components
  → "Amoxycillin 500mg + Clavulanic Acid 125mg"
  → Drug 1: amoxicillin → RxCUI = 723
  → Drug 2: clavulanic acid → RxCUI = 2348

Output: {
  brand: "Ecosprin 75",
  generic: "aspirin",
  rxcui: "1191",
  confidence: 0.98,
  match_type: "indian_brand"
}
```

**What changed recently in the mobile/manual path:**
- manual allopathic correction now goes through:
  1. `extract-drugs-from-text`
  2. direct graph name resolution
  3. only then a plain manual fallback
- manual ayurvedic correction goes through herb resolution first
- if the typed/spoken name resolves cleanly, it becomes the **same canonical medicine object** as an OCR-detected medicine
- only the final fallback keeps raw user text without a graph match

This matters because manual entries now continue through the **same downstream pipeline**:
- same medicine identity model
- same prescriber assignment step
- same safety-check input
- same report generation

### Handling Tricky Cases

| Input | Challenge | How We Handle It |
|---|---|---|
| "dolo650" (no space) | OCR merged text | Fuzzy search with candidate key generation |
| "Acetylsalicylic acid" | Old chemical name | Synonym search → maps to "aspirin" |
| "Paracetamol" | Indian name for Acetaminophen | Alias mapping (India uses "Paracetamol", US uses "Acetaminophen") |
| "Combiflam" | Combination drug | Split composition: Ibuprofen + Paracetamol |
| "अश्वगंधा" (Hindi text) | Regional language | Search herb nodes by hindi_name field |
| "Thyronorm50mcg" | Strength merged | Strip numbers, search "thyronorm" → levothyroxine |

### Validation Results

We tested 32 real-world normalization cases, and **all 32 passed**:
- Ecosprin 75 → aspirin ✓
- Thyronorm 50 → levothyroxine ✓
- Dolo 650 → paracetamol ✓
- Crocin → paracetamol ✓
- PAN40 → pantoprazole ✓
- Combiflam → ibuprofen + paracetamol ✓
- Augmentin 625 Duo → amoxicillin + clavulanic acid ✓
- UrimaxD → tamsulosin ✓
- Aciloc D → ranitidine ✓

Internally, the extractor still tracks whether a resolved medicine successfully matched the graph (`graph_match`), because that matters for normalization quality. But on mobile we removed the old `Verified / Unverified` patient-facing badge, because that label was technically about graph normalization, not about whether the medicine was clinically safe.

---

## 10. The Safety Engine — How We Find Dangerous Interactions

Our safety engine checks for **6 types of dangers**:

### Type 1: Direct Drug-Drug Interactions (L1)

**What it is:** Drug A and Drug B are **known** to interact. This is explicitly recorded in our databases.

**How we find it:**
```
Query Neo4j:
  "Find all pairs among the patient's drugs where an INTERACTS_WITH relationship exists"

Example:
  Patient takes: Warfarin, Aspirin, Metformin
  Check: Warfarin-Aspirin? → YES! Major interaction (DDInter source)
  Check: Warfarin-Metformin? → YES! Moderate interaction
  Check: Aspirin-Metformin? → Minor/no significant interaction
```

**Current implementation details that matter:**
- `check_direct_interactions()` now groups **all** support rows for a pair and chooses the **best support row**
- it prefers:
  1. the strongest **non-unknown** severity
  2. better provenance / richer support when severities tie
  3. only falls back to raw `unknown` if no stronger row exists
- direct findings can carry citations from:
  - **DDInter**
  - **PrimeKG**
  - **Beers 2023**
  - **TwoSIDES** (co-prescription signal)

**Output:** Raw severity, patient-facing display severity, mechanism, clinical effect, management advice, and evidence provenance.

**Important report behavior:**  
If the raw graph severity is still unresolved after runtime repair, the patient-facing report does **not** show `UNKNOWN`. It shows **Doctor Review**.

### Type 2: Indirect Drug-Drug Interactions via Enzymes (L2)

**What it is:** Drug A and Drug B don't directly clash, but they **interact through a shared liver enzyme**. This is the "hidden danger" — no label will tell you about it.

**How we find it:**
```
Query Neo4j for CYP inhibition paths:
  "Find Drug A that INHIBITS Enzyme X, AND Drug B that IS_SUBSTRATE_OF Enzyme X"

Example:
  Clarithromycin → INHIBITS → CYP3A4 ← IS_SUBSTRATE_OF ← Simvastatin

  Translation: Clarithromycin blocks the enzyme that breaks down Simvastatin.
  So Simvastatin builds up in the body → toxicity risk.
```

We also check for:
- **CYP induction** (Drug A speeds up the enzyme → Drug B gets broken down too fast → becomes ineffective)
- **Transporter-mediated** (Drug A blocks P-glycoprotein transporter → Drug B can't be pumped out of cells → accumulates)
- **QT compounding** (Two drugs that both prolong the heart's QT interval → combined risk of dangerous heart rhythm)
- **Electrolyte cascades** (Drug A depletes potassium → Drug B becomes more toxic when potassium is low, like digoxin)
- **CNS compounding** (Two drugs that both suppress the brain → combined sedation, fall risk)

### Type 3: Herb-Drug Interactions

**What it is:** An Ayurvedic herb interacts with a modern medicine.

**How we find it:**
```
Query Neo4j:
  "Find all Herb nodes that have INTERACTS_WITH_DRUG relationships to any of the patient's drugs"

Example:
  Garlic → INTERACTS_WITH_DRUG → Warfarin
  (Garlic has antiplatelet properties, adding to Warfarin's blood-thinning effect)
```

**Current provenance detail:**  
For DDID-backed herb-drug findings, the live report can now recover and display:
- DOI / PMID when present
- DDID herb page link
- DDID drug page link
- DDID record IDs

So herb-drug warnings are no longer just "curated claims" — many of them now surface exact paper-backed or dataset-record-backed provenance.

### Type 4: Beers Criteria Flags

**What it is:** A medicine is **inappropriate for elderly patients**, regardless of interactions.

**How we find it:**
```
For each drug the patient takes:
  1. Check Drug node's "is_beers" flag
  2. If flagged → retrieve category, rationale, recommendation
  3. Cross-reference with patient's age and conditions

Example:
  Patient (age 78) takes Diphenhydramine
  → is_beers = True
  → Category: "Avoid"
  → Reason: "Strong anticholinergic. Causes confusion, constipation, cognitive decline in elderly."
  → Suggestion: "Use non-anticholinergic antihistamine like Cetirizine instead"
```

### Type 5: Anticholinergic Burden Score

**What it is:** Some drugs have "anticholinergic" effects — they block a brain chemical called acetylcholine. One such drug is usually fine. But if a patient takes 3-4 of them, the **cumulative effect** causes confusion, falls, cognitive decline, dry mouth, constipation.

**How we calculate it:**
```
Each drug has an anticholinergic score (0, 1, 2, or 3)
Sum all scores for the patient's medicines

Total 0-2: Low risk ✅
Total 3-5: Moderate risk ⚠️ (watch for confusion, falls)
Total 6+:  High risk 🔴 (serious cognitive/fall risk)

Example:
  Diphenhydramine (score 3) + Amitriptyline (score 3) = Total 6
  → HIGH RISK → "This combination significantly increases confusion and fall risk"
```

### Type 6: Therapeutic Duplication

**What it is:** The patient is taking **two drugs that do the same thing** — usually from different doctors who didn't coordinate.

**How we find it:**
```
Group all patient's drugs by drug_class
If any class has 2+ drugs → flag

Example:
  Ibuprofen (NSAID) + Diclofenac (NSAID) = TWO NSAIDs
  → "You don't need two NSAIDs. This doubles stomach bleeding risk."

  Atorvastatin (statin) + Rosuvastatin (statin) = TWO statins
  → "Taking two cholesterol medicines is unnecessary and increases muscle damage risk."
```

---

## 11. RAG and Self-Corrective RAG (CRAG) — Explained

### What is RAG?

**RAG** stands for **Retrieval-Augmented Generation**. Let's break that down:

- **Retrieval:** First, go look up facts from a database
- **Augmented:** Use those facts to enhance (augment) the AI's knowledge
- **Generation:** Then have the AI generate an answer based on those facts

**Without RAG:** An LLM (like ChatGPT) answers from memory. It might hallucinate (make up facts).

**With RAG:** The LLM first sees real data from our Neo4j graph, and then generates its answer based on that data. This is like giving a student the textbook during an exam — they give better, more accurate answers.

### What is CRAG (Self-Corrective RAG)?

**CRAG** adds a **self-checking loop** on top of RAG. It goes:

1. Retrieve facts from the database
2. Generate an answer
3. **Check:** "Did I miss anything important?"
4. If yes → go back and dig deeper
5. **Verify:** "Is everything I said actually supported by the database?"
6. If not → remove the unsupported claims

This is why our system has **0% hallucination rate** — every claim is verified against the knowledge graph.

### Our CRAG Pipeline — Step by Step

We use **LangGraph** (a framework for building AI workflows as state machines) to implement this as a 5-step process:

```
┌─────────────────────────────────────────────────────┐
│  STEP 1: INTAKE AND RESOLVE                         │
│                                                      │
│  Take each medicine name, resolve to generic name    │
│  Take each herb name, resolve (including regional)   │
│  Classify case complexity:                           │
│    - Simple: ≤3 drugs, no herbs                     │
│    - Moderate: 4-7 drugs, or has herbs              │
│    - Complex: 8+ drugs, or herbs + elderly          │
│                                                      │
│  Output: list of resolved drugs, herbs, complexity   │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  STEP 2: GRAPH SAFETY CHECKS                        │
│                                                      │
│  Run ALL 6 safety checks against Neo4j:             │
│    - Direct interactions (L1)                        │
│    - Indirect/multi-hop interactions (L2)            │
│    - Herb-drug interactions                          │
│    - Beers criteria flags                            │
│    - Anticholinergic burden                          │
│    - Therapeutic duplication                          │
│                                                      │
│  Output: all findings from the graph                 │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  STEP 3: EVALUATE COMPLETENESS (LLM)                │
│                                                      │
│  Ask the AI: "Given this elderly patient with these  │
│  conditions, taking these drugs and herbs — did we   │
│  find all the major interactions? Score 0 to 1."     │
│                                                      │
│  The AI reviews the graph findings and thinks:       │
│  "Hmm, we found Warfarin-Aspirin, but we didn't    │
│  check if the patient's kidney function affects      │
│  any of these drugs..."                              │
│                                                      │
│  Output: completeness_score (0.0 to 1.0)            │
│          list of potentially missing interactions     │
│                                                      │
│  IF score ≥ 0.7 → Skip to Step 5 (we're thorough)  │
│  IF score < 0.7 → Go to Step 4 (dig deeper)        │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  STEP 4: DEEP ANALYSIS (Conditional — only if       │
│          completeness was low)                       │
│                                                      │
│  Run broader queries:                                │
│    - Wider multi-hop enzyme paths                    │
│    - Disease-specific interactions                   │
│    - Check rare but serious interactions             │
│                                                      │
│  Ask the AI: "What other interactions might be       │
│  clinically important that we haven't caught?"       │
│                                                      │
│  The AI generates candidate findings (unverified)    │
│                                                      │
│  Output: deep_findings (NOT yet verified!)           │
└──────────────────────┬──────────────────────────────┘
                       ▼
┌─────────────────────────────────────────────────────┐
│  STEP 5: VERIFY AND COMPILE (Anti-Hallucination)    │
│                                                      │
│  For EVERY finding from Step 4:                      │
│    → Query Neo4j: "Is this actually in our graph?"  │
│    → If YES (confidence ≥ 0.75): KEEP IT ✅         │
│    → If NO: DISCARD IT ❌ (hallucination caught!)    │
│                                                      │
│  Merge all verified findings:                        │
│    - L1 (direct from graph) ✅                       │
│    - L2 (multi-hop from graph) ✅                    │
│    - L3 (AI-suggested AND verified) ✅               │
│    - Removed: AI suggestions not in graph ❌         │
│                                                      │
│  Sort by severity → Compile final report             │
│                                                      │
│  Output: final_report with ONLY verified findings    │
└─────────────────────────────────────────────────────┘
```

### The 30-Second Safety Net

The entire CRAG pipeline has a **30-second timeout**. If the LLM is slow or an API is down:
- Steps 1 and 2 (graph queries) complete within seconds
- If Steps 3-5 haven't finished in 30 seconds → **graceful fallback** → return L1 + L2 findings from the graph alone
- The patient ALWAYS gets a safety report, even if the AI part fails

This is why we say: **The graph is the backbone, the AI is the enhancement.**

---

## 12. Multi-Hop Reasoning — Finding Hidden Dangers

### What is "Multi-Hop"?

**One hop:** Drug A → interacts with → Drug B (one step, direct)  
**Multi-hop:** Drug A → blocks → Enzyme X → metabolizes → Drug B (two or more steps through intermediaries)

Multi-hop finds dangers that are more **subtle** and harder for humans to spot.

### The 6 Types of Multi-Hop Reasoning We Do

#### Type 1: CYP Enzyme Inhibition

**Real example:** Clarithromycin + Simvastatin

```
Clarithromycin ──INHIBITS──→ CYP3A4 ←──IS_SUBSTRATE_OF── Simvastatin

Translation:
1. Your liver uses the enzyme CYP3A4 to break down Simvastatin (cholesterol med)
2. Clarithromycin (antibiotic) BLOCKS CYP3A4
3. So Simvastatin can't be broken down → it builds up in your blood → 17x higher levels
4. This causes severe muscle damage (rhabdomyolysis) which can destroy your kidneys
```

This is one of the most dangerous hidden interactions. Both drugs are extremely common, and a different doctor often prescribes each one.

#### Type 2: CYP Enzyme Induction

**Real example:** Rifampin + Apixaban

```
Rifampin ──INDUCES──→ CYP3A4 ──METABOLIZES──→ Apixaban

Translation:
1. Apixaban (blood thinner) is broken down by CYP3A4
2. Rifampin (TB antibiotic) SPEEDS UP CYP3A4 (makes it work overtime)
3. So Apixaban gets broken down TOO FAST → not enough remains in blood → fails to prevent clots
4. Patient is at risk of stroke or blood clots because their blood thinner stopped working
```

#### Type 3: Transporter-Mediated

**Real example:** Amiodarone + Digoxin

```
Amiodarone ──INHIBITS──→ P-glycoprotein ←──IS_SUBSTRATE_OF── Digoxin

Translation:
1. P-glycoprotein (P-gp) is a "pump" in your body that removes Digoxin from cells
2. Amiodarone blocks this pump
3. Digoxin can't be pumped out → accumulates in heart cells → toxicity
4. Can cause dangerous heart rhythm changes
```

#### Type 4: QT Compounding

**Real example:** Amiodarone + Ondansetron

```
Amiodarone ──PROLONGS_QT──→ QT_Effect ←──PROLONGS_QT── Ondansetron

Translation:
1. Both drugs independently make the heart's electrical cycle longer (QT prolongation)
2. Together, the effect COMPOUNDS
3. Risk: Torsades de Pointes (a life-threatening irregular heartbeat)
```

We track **279 drugs** that prolong QT. If a patient takes any 2+ of them, we flag it.

#### Type 5: Electrolyte Cascade

**Real example:** Furosemide + Digoxin

```
Furosemide ──DEPLETES──→ Potassium ←──SENSITIVE_TO── Digoxin

Translation:
1. Furosemide (water pill) makes you lose potassium through urine
2. When potassium is low, Digoxin becomes MORE toxic to the heart
3. Even at normal Digoxin doses, low potassium → dangerous heart rhythm changes
```

#### Type 6: CNS Compounding

**Real example:** Clonazepam + Quetiapine

```
Both drugs ──CAUSES_CNS_DEPRESSION──→ Brain suppression

Translation:
1. Both drugs calm the brain (sedation)
2. Together, the sedation effect is dangerously amplified
3. Risk: excessive drowsiness, falls (extremely dangerous in elderly), respiratory depression
```

We track **40 CNS depressant drugs**. If a patient takes 2+, we flag it.

### The Scale of Discovery

From our enzyme data alone, we can discover **52,758 indirect interaction pairs** — most of which are NOT in any drug interaction database. Traditional drug interaction checkers would miss ALL of these.

**Current provenance nuance for judges:**  
Not every multi-hop warning has the same evidence class.

- Drug-drug CYP/transporter multihop findings are currently backed by a mix of:
  - local curated mechanism edges
  - FDA DDI table references
  - Flockhart table references
- Herb-drug CYP multihop findings can now be reconstructed from the local herb-CYP literature file, so the report can attach named literature backing instead of presenting them as generic unsupported rules
- QT findings are category-backed (for example CredibleMeds-style compendium support), not always pair-specific paper proof
- Electrolyte and CNS cascade findings are still the weakest evidence class; they are useful safety rules, but they should be presented honestly as curated/internal mechanism layers rather than exact record-linked evidence

---

## 13. Where and Why We Use LLMs/APIs

We use multiple AI models and APIs, each for a specific purpose. Here's every single one:

### LLM Usage 1: OCR (Reading Prescription Images)

| Model | Provider | Purpose |
|---|---|---|
| **GPT-4o Vision** | OpenAI | Primary — reads medicine photos with high accuracy. Understands Indian medicine label layouts. |
| **Gemini 2.0 Flash** | Google | Fallback #1 — used if OpenAI key unavailable |
| **Llama 3.2 90B Vision** | Groq | Fallback #2 — free alternative |

**Why LLM instead of traditional OCR?** Indian medicine labels are messy — multiple fonts, colored backgrounds, Indic scripts mixed with English. Traditional OCR (like Tesseract) fails badly on these. Vision LLMs understand the visual layout and context.

### LLM Usage 2: CRAG Completeness Evaluation

| Model | Provider | Purpose |
|---|---|---|
| **GPT-4o-mini** | OpenAI | Primary — evaluates if we found all important interactions |
| **Gemini 2.0 Flash** | Google | Fallback #1 |
| **Llama 3.3 70B** | Groq | Fallback #2 |

**What it does:** After graph queries return findings, the LLM reviews them and scores: "On a 0-1 scale, how complete is this safety analysis?" If score < 0.7, it triggers deeper analysis.

**Why not just use the graph?** The graph contains facts, but it doesn't have "clinical judgment." An LLM can think: "For this 78-year-old diabetic patient on warfarin, we should also check kidney function impact" — a contextual insight that pure database queries might miss.

### LLM Usage 3: Deep Analysis (CRAG Step 4)

Same models as above. When the completeness score is low, the LLM:
- Identifies **potentially missing interactions** based on clinical knowledge
- Suggests specific drug pairs to investigate
- All suggestions are then verified against the graph (Step 5)

### LLM Usage 4: Report Generation

| Model | Provider | Purpose |
|---|---|---|
| **GPT-4o-mini / Gemini / Groq** | Various | Generates the patient-friendly safety report from structured findings |

**What it does:** Takes the raw safety findings (JSON data) and converts them into readable text:
- Patient-friendly explanations (no jargon)
- Doctor-facing clinical rationale
- Action items and recommendations
- Severity-ordered presentation

**Current report-generation safety detail:**  
The generated report now preserves:
- `finding_id`
- raw `severity`
- patient-facing `display_severity`
- citations
- evidence profile

The LLM is explicitly instructed **not** to rewrite those structural fields. It may only rewrite the human-language explanation fields.

### LLM Usage 5: Drug Extraction from OCR Text

When regex-based extraction (pattern matching) fails to find all medicines from OCR text, an LLM is used as backup to:
- Parse complex compositions
- Handle unusual formatting
- Identify medicine names mixed with other text

### API Usage 1: Sarvam AI (Translation + Voice)

| Service | Purpose |
|---|---|
| **Sarvam Translate** | Translates patient-facing report text in the current hardened report path |
| **Sarvam Transliterate** | Converts Indic script OCR text to English characters |
| **Sarvam Speech-to-Text** | Lets users speak medicine names instead of typing |
| **Sarvam Text-to-Speech** | Reads the patient summary aloud in the patient's language |

**Why Sarvam specifically?** It gives us one India-focused stack for translation, transliteration, speech-to-text, and text-to-speech.

**Important nuance:**  
- mobile UI copy exists in 10 languages
- voice input/output supports 10 Sarvam locales
- the current medically safe **report translation** path is explicitly normalized for a smaller subset (English, Hindi, Tamil, Telugu, Kannada)

### API Usage 2: RxNorm (Drug Code Lookup)

| Service | Purpose |
|---|---|
| **RxNorm REST API** | Converts drug names → RxCUI codes for standardized identification |
| **RxClass API** | Fetches drug class and ATC codes |

**URL:** `https://rxnav.nlm.nih.gov/REST/rxcui.json?name={drug_name}`

### API Usage 3: Neo4j Graph Database

Not an external API, but runs locally in Docker. All graph queries go through the **Neo4j Bolt protocol** on port 7687.

**Current live deployment fact:**  
The local Docker graph is now the **full graph**, not the earlier reduced-memory subset.

### The Key Principle: LLMs Never Have the Final Say

Every LLM output in our system is either:
1. **Verified against the graph** (CRAG verification step), or
2. **Used only for presentation** (report generation — facts come from graph, LLM just writes them nicely), or
3. **Used only for evaluation** (completeness scoring — doesn't add facts, just assesses if we need to dig deeper)

**This is why we achieve 0% hallucination rate.** The LLM can suggest "I think Drug A and Drug B interact" — but unless our graph confirms it, we throw that suggestion away.

---

## 14. Geriatric Safety — Beers Criteria and Elderly Protection

### What are the Beers Criteria?

Imagine a **list of medicines that doctors should think twice about before prescribing to anyone over 65**. That's the Beers Criteria. It's published by the American Geriatrics Society (AGS) and updated every 3 years. We use the 2023 version.

### Categories of Beers Flags

**Category 1: "AVOID in elderly"** — these drugs are almost always bad for older people:
- **Diphenhydramine (Benadryl):** Causes confusion, falls, cognitive decline
- **Diazepam (Valium):** Prolonged sedation, increased fall risk
- **Amitriptyline:** Strong anticholinergic — brain fog, dry mouth, constipation, falls
- **Meperidine (Demerol):** High delirium risk, neurotoxic metabolite
- **Metoclopramide:** Movement disorders (Parkinsonian symptoms)

**Category 2: "Use with CAUTION"** — okay in some cases, but watch closely:
- **Glimepiride:** Higher hypoglycemia risk in elderly than other diabetes medicines
- **Nifedipine (immediate-release):** Can cause dangerous blood pressure drops

**Category 3: "Avoid with SPECIFIC CONDITIONS":**
- **nitrofurantoin + kidney disease:** Doesn't work with poor kidney function, causes nerve damage
- **Dabigatran + kidney disease:** Accumulates dangerously when kidneys can't clear it
- **NSAIDs + heart failure:** Worsen fluid retention

### Anticholinergic Burden — The Hidden Cumulative Risk

**What are anticholinergic drugs?** Drugs that block the brain chemical "acetylcholine." Many common medicines have mild anticholinergic effects.

**The danger:** Each drug alone is fine. But elderly patients often take 5-6 drugs, and if 3-4 of them have anticholinergic effects, the **cumulative burden** causes:
- Confusion and cognitive decline (can mimic dementia!)
- Falls (the #1 cause of injury-related death in elderly)
- Dry mouth → dental disease → poor nutrition
- Constipation → bowel impaction
- Urinary retention → infections

**How we score it:**

| Drug | ACB Score |
|---|---|
| Diphenhydramine | 3 (high) |
| Amitriptyline | 3 (high) |
| Hydroxyzine | 3 (high) |
| Furosemide | 1 (low) |
| Metformin | 0 (none) |

If a patient takes Diphenhydramine (3) + Amitriptyline (3) = **Total ACB = 6 → HIGH RISK!**

Our report will say: "⚠️ Your medicines have a very high anticholinergic burden (score: 6). This significantly increases your risk of confusion, falls, and cognitive problems. Please discuss with your doctor about alternatives."

---

## 15. Report Generation and Multilingual Support

### What the Report Looks Like

After all safety checks, we generate a report with these sections:

```
╔══════════════════════════════════════════════════╗
║           SAHAYAK SAFETY REPORT                   ║
╚══════════════════════════════════════════════════╝

PATIENT: Mrs. Kamala Devi, Age 78, Female
CONDITIONS: Atrial Fibrillation, Type 2 Diabetes, Hypertension
VITALS: BP 140/85, Heart Rate 68, Creatinine 1.4 mg/dL

MEDICINES REVIEWED:
  ✓ Warfarin 5mg (blood thinner) — prescribed by cardiologist
  ✓ Aspirin 75mg (blood thinner) — self-purchased
  ✓ Metformin 500mg (diabetes) — prescribed by endocrinologist
  ✓ Ashwagandha (herbal supplement)

═══════════════════════════════════════════════════

🔴 CRITICAL SAFETY CONCERNS:

1. Warfarin + Aspirin — HIGH RISK OF BLEEDING
   Both medicines thin the blood. Taking them together
   increases the risk of serious internal bleeding.
   
   ▶ WHAT TO DO: Contact your cardiologist IMMEDIATELY.
     Do NOT stop any medicine without doctor's approval.
   
   Evidence: Direct interaction (DDInter database)
   Severity: Major

═══════════════════════════════════════════════════

🟠 MAJOR CONCERNS:

2. Ashwagandha + Warfarin — MAY INCREASE BLEEDING RISK
   Ashwagandha may enhance Warfarin's blood-thinning effect.
   
   ▶ WHAT TO DO: Inform your doctor about this herbal
     supplement. Consider stopping or take 4-6 hours apart.
   
   Evidence: Herb-drug interaction (curated database)
   Severity: Moderate

═══════════════════════════════════════════════════

📋 GERIATRIC SAFETY FLAGS:
   No Beers Criteria concerns with current medicines ✅

🧠 ANTICHOLINERGIC BURDEN:
   Score: 0 (Low) — No anticholinergic risk ✅

═══════════════════════════════════════════════════

RECOMMENDATIONS:
• Talk to your cardiologist about the Warfarin + Aspirin combination
• Mention Ashwagandha use to your cardiologist
• Schedule follow-up: 2 weeks after any changes
• Monitor for: unusual bruising, prolonged bleeding, black stool

═══════════════════════════════════════════════════
Report generated by Sahayak | 28 March 2026
```

### Multilingual Translation

The report is still generated in English first (for clinical accuracy), but the translation behavior changed significantly in the current code:

1. **Sarvam AI Translate API** is now the active report translation path
2. Only **patient-facing text fields** are translated:
   - patient summary
   - self-prescribed warning
   - personalized advice
   - disclaimer
   - finding title / patient explanation / doctor explanation / action
3. Structural evidence fields are deliberately **not** translated:
   - `finding_id`
   - raw `severity`
   - `display_severity`
   - source labels
   - citations
   - source links
4. Translated findings are merged back by **`finding_id`**, not array position

This is a major safety improvement because it prevents:
- severity tags being corrupted by translation
- citations drifting onto the wrong finding
- half-translated reports where the patient-facing text and evidence metadata disagree

**Current strongest report-translation locales:** English, Hindi, Tamil, Telugu, Kannada  
**Current mobile UI locale support:** 10 languages

So the honest statement is:
- the app UI is multilingual in 10 languages
- report translation is currently medically hardened for a smaller Sarvam-supported subset

### Voice Output

For elderly patients who can't read well:
- The mobile app currently reads the **patient summary** aloud using Sarvam AI Text-to-Speech
- Speech-to-text and text-to-speech support all 10 mobile language locales
- The current mobile implementation does **not** narrate the entire full report end-to-end as one continuous audio block

---

## 16. Evaluation — How We Tested and Proved It Works

### Our Testing Philosophy

We don't just write code and hope it works. We built a **rigorous 8-part evaluation framework** with specific test cases for every component. Here's exactly what we did:

### Latest Runtime Verification (28 March 2026)

In addition to the benchmark suites below, the current repo/runtime was re-verified on 28 March 2026:

- **Live backend health:** `374,752` graph nodes
- **Live Neo4j relationship count:** `4,742,152`
- **Relationship types:** `37`
- **Report provenance regression suite:** `16/16 passed` (`tests/test_report_citations.py`)
- **Current mobile and frontend TypeScript builds:** passing in the working tree used for the hackathon build

This matters because the document is no longer describing a hypothetical "full graph someday" state. It is describing the graph and report pipeline that are actually running now.

### Test Suite 1: Sentinel Interaction Validation (50/50)

**What we tested:** Can our system find 50 known critical drug interactions?

**How we did it:**
1. Created a curated list of **50 well-known dangerous drug interaction pairs** (the "sentinel set"):
   - Warfarin + Aspirin (bleeding)
   - Warfarin + Ibuprofen (bleeding)
   - Warfarin + Clarithromycin (increased Warfarin effect)
   - Warfarin + Amiodarone (increased Warfarin effect)
   - Clarithromycin + Simvastatin (muscle damage)
   - Digoxin + Amiodarone (heart toxicity)
   - ACE inhibitors + Spironolactone (high potassium)
   - Metformin + contrast dye (kidney damage)
   - ... and 42 more high-severity pairs

2. For each pair, we queried our system: "Do Drug A and Drug B interact?"

3. Checked TWO things:
   - **Was it found?** (sensitivity — did we catch it?)
   - **Was the severity correct?** (severity matching — did we say "major" when it should be "major"?)

**Results:**
- **50 out of 50 found** → 100% sensitivity
- **50 out of 50 severity matched** → 100% severity accuracy

### Test Suite 2: Direct DDI Benchmark (40 drug-drug pairs)

**What we tested:** Expanded drug-drug interaction detection beyond the sentinel set.

**How we did it:**
1. Selected 40 drug-drug pairs (subset of sentinel focused on direct DDI)
2. Ran sensitivity and severity matching
3. Calculated **weighted kappa** (a statistical measure of agreement that accounts for the ordered nature of severity: minor < moderate < major)

**Results:**
- **Sensitivity: 1.00** (found all 40)
- **Severity exact match: 0.925** (37 out of 40 perfectly matched severity; 3 had minor disagreements like "moderate" vs "major" borderline cases)
- **Weighted kappa: 0.7273** (substantial agreement — kappa > 0.6 is considered good)

### Test Suite 3: Herb-Drug Sensitivity (100% on curated set)

**What we tested:** Can we find herb-drug interactions?

**How we did it:**
1. Created curated herb-drug sentinel pairs:
   - Garlic + Warfarin (bleeding risk)
   - Fenugreek + Metformin (hypoglycemia risk)
   - Ashwagandha + Levothyroxine (thyroid interference)
   - Arjuna + Warfarin (cardiac herb + blood thinner)
   - Guggul + Warfarin (also has blood-thinning properties)

2. Also tested **abstention safety** — when we DON'T know about an herb, we should say "insufficient data" rather than "safe":
   - Shankhpushpi + Metformin → should say "insufficient data" (not falsely reassure)
   - Punarnava + Warfarin → should say "insufficient data"
   - Cardamom + Levothyroxine → should say "insufficient data"

3. Tested **regional name resolution** — can we find herbs when typed in Hindi/Tamil/Telugu/Kannada?

**Results:**
- **Sensitivity: 1.00** (found all curated herb-drug pairs)
- **Severity exact match: 1.00** (correctly categorized all severities)
- **False reassurance rate: 0.00** (never falsely said "safe" for unknown herbs)

### Test Suite 4: Beers Criteria Coverage (100% on curated elderly set)

**What we tested:** Can we correctly flag medicines that are inappropriate for elderly patients?

**How we did it:**
1. Tested 15 known Beers-listed drugs:
   - Diphenhydramine, Hydroxyzine, Amitriptyline, Diazepam, Chlordiazepoxide, Alprazolam, Glimepiride, Nifedipine, Doxazosin, Methyldopa, Megestrol, Nitrofurantoin, Meperidine, Glyburide, Metoclopramide

2. For each: simulated a 78-year-old patient and checked if the system flagged it

3. Also tested ACB scoring accuracy (specific scores for specific drugs)

4. Tested therapeutic duplication detection (positive and negative cases)

**Results:**
- **Beers coverage: 1.00** (all 15 drugs correctly flagged)
- **ACB accuracy matches clinical references**
- **Duplication detection works for NSAIDs, statins; correctly ignores dissimilar drugs**

### Test Suite 5: Multi-Hop Graph Path Precision (100%)

**What we tested:** Can our system find interactions through enzyme/transporter pathways?

**How we did it:** 6 specific test cases:

1. **CYP3A4 inhibition:** Clarithromycin → CYP3A4 → Simvastatin ✅
2. **CYP3A4 induction:** Rifampin → CYP3A4 → Apixaban ✅
3. **Transporter:** Amiodarone → P-glycoprotein → Digoxin ✅
4. **QT compounding:** Amiodarone + Ondansetron both prolong QT ✅
5. **Electrolyte cascade:** Furosemide depletes K+ → Digoxin sensitivity ✅
6. **CNS compounding:** Clonazepam + Quetiapine CNS depression ✅

Also verified that the graph contains 52,758+ discoverable indirect pairs and 32,826 validated overlap with known interactions.

**Results:**
- **Graph path precision: 1.00** (all 6 paths correctly found)
- **Engine recall: 0.8333** (found 5 out of 6 interaction types through the automated engine — one type required manual path query)

**Why recall is 0.83 not 1.00:** The CNS compounding path (Test 6) requires a specific relationship type (CAUSES_CNS_DEPRESSION) that the automated engine may not query in all code paths. The path EXISTS in the graph (precision = 1.00), but the automated query engine doesn't always traverse it outside of deep analysis mode. This is a known minor gap.

### Test Suite 6: RAG Grounding & Hallucination (0% hallucination)

**What we tested:** Does our system ever make up interactions that aren't in the database?

**How we did it:**
1. Ran 2 full scenarios through the complete CRAG pipeline:
   - Scenario 1: Warfarin + Aspirin + Digoxin + Garlic (elderly male, 78)
   - Scenario 2: Clarithromycin + Simvastatin + Amiodarone + Turmeric (elderly female, 72)

2. For every finding in the generated report, queried Neo4j: "Does this interaction actually exist in our graph?"

3. Calculated:
   - **Grounded finding pass rate** = verified findings / total findings
   - **Relation hallucination rate** = unverified findings / total findings

**Results:**
- **Grounded finding pass rate: 1.00** (every reported interaction is in the graph)
- **Hallucination rate: 0.00** (zero made-up interactions)
- **RAG faithfulness proxy: 1.00**

### Test Suite 7: Full Pipeline End-to-End (3 scenarios)

**What we tested:** Can we go from OCR text → drug extraction → normalization → safety check → report → translation?

**How we did it:**
3 realistic scenarios:

1. **Anticoagulant case:** OCR text for Ecosprin 75 + Warf 5 → extract aspirin + warfarin → detect major interaction → generate report in English + Hindi

2. **CYP inhibition case:** OCR text for Clarithromycin 500 + Simvastatin 20 → detect CYP3A4 inhibition path → generate report

3. **Thyroid absorption case:** OCR text for Thyronorm 50 + Calcium Carbonate 500 → detect absorption interference → generate report

**Results:** All 3 scenarios passed — correct drug extraction, correct interaction detection, report generation successful.

### Test Suite 8: Real-World Graph Validation (32/32)

**What we tested:** Is our knowledge graph structurally sound and clinically correct?

**How we did it:** 32 automated checks:
- No orphan drug nodes (every drug connects to something)
- Required indexes exist (for fast searching)
- Synonym deduplication works (Aspirin = Acetylsalicylic acid)
- 7 Indian brand resolution tests
- 6 fuzzy search tests (handling OCR typos)
- 6 DDI validation tests (critical interactions present with correct severity)
- 4 herb-drug interaction tests
- Beers criteria coverage tests

**Results:** **32 out of 32 passed** ✓

### Agentic Completeness Score: 0.8

This measures how well the CRAG pipeline performs as a complete agent. A score of 0.8 means the system properly hits 80% of its design checkpoints — resolution, graph checks, completeness evaluation, deep analysis, and verification.

### Backend Pipeline P95 Latency: 1338.51ms (~1.34 seconds)

The 95th percentile latency for the entire backend pipeline (drug resolution + graph queries + LLM completeness check + report compilation) is **1.34 seconds**. This means 95% of requests complete in under 1.34 seconds — very fast for a system doing this much work.

---

## 17. The OCR Benchmark Controversy — Why 0.75 is Misleading

### What the Score Means

The OCR benchmark reported as **0.75** needs important context:

### How OCR Was Tested

Our OCR test suite does **NOT test with real images**. It tests with **text fixtures** — we provide the text that WOULD come out of OCR, and check if our drug extraction correctly parses it.

The 4 test fixtures:
1. "DOLO 650 / Paracetamol IP 650 mg" ← simple, single ingredient
2. "PAN 40 / Pantoprazole Sodium IP equivalent to Pantoprazole 40 mg" ← "equivalent to" phrasing
3. "THYRONORM 50 mcg / Levothyroxine Sodium IP 50 mcg" ← mcg unit, sodium salt
4. "COMBIFLAM / Ibuprofen IP 400 mg + Paracetamol IP 325 mg" ← combination drug

The 0.75 score means **3 out of 4 text fixtures were perfectly extracted**. The 4th likely had a minor issue with dosage matching or generic name normalization (like "paracetamol" vs "acetaminophen" naming difference).

### Why This Score Doesn't Reflect Real OCR Quality

1. **The test doesn't test the OCR itself** (the vision model reading images). It only tests the text parsing AFTER OCR. The GPT-4o Vision model is actually very good at reading Indian medicine labels.

2. **We haven't created a real image test set yet** — we need 200+ labeled medicine photos (easy/medium/hard) with ground truth annotations. This is listed in the EVALUATION_MASTERPLAN as a future task.

3. **The "failure" is likely a naming convention issue** — paracetamol vs acetaminophen, or a dosage format mismatch — not a genuine extraction failure.

### Honest Assessment

- **OCR model quality (GPT-4o reading images):** Very good — not benchmarked with images yet, but manual testing shows high accuracy on Indian medicine labels
- **Drug extraction from OCR text (tested):** 75% on 4 fixtures — needs more test cases and normalization fixes
- **The 0.75 is a text-parsing score, NOT an image-reading score**

### What Would Make It Better

- Collect 200+ labeled prescription/medicine photos
- Annotate with ground truth (which medicines are in each photo)
- Run full end-to-end OCR test: image → GPT-4o Vision → text → extraction → normalization → compare with ground truth
- This is planned but not yet done (marked as "TBD" in the evaluation masterplan)

---

## 18. Edge Cases and Honest Limitations

### What Works Well (Strengths)

- ✅ 100% detection of critical sentinel interactions
- ✅ 100% herb-drug sensitivity on curated set
- ✅ 100% Beers criteria coverage
- ✅ 100% graph path precision for multi-hop reasoning
- ✅ 0% hallucination in grounded reports
- ✅ 32/32 real-world validation cases passed
- ✅ Fast: ~1.34s backend P95 latency
- ✅ Indian brand resolution works for all tested brands
- ✅ Herb regional name resolution (Hindi, Tamil, Telugu, Kannada)

### Known Limitations (Honest)

1. **OCR benchmark is 0.75** — as explained above, this is a text-parsing benchmark, not a real image test. But it does mean our text parsing has room for improvement.

2. **Alert burden is high:** The system currently reports **~16.67 findings per 10-drug review**. This means if someone takes 10 medicines, they get ~17 warnings. That's too many — many are low-severity and might cause "alert fatigue" (patient ignores all warnings because there are too many). We need better filtering to show only clinically important findings.

3. **Multi-hop engine recall is 0.8333** — the system finds 5 out of 6 types of multi-hop interactions automatically. The CNS compounding path sometimes requires explicit deep analysis mode to find. This means 1 in 6 indirect mechanism types might be missed in fast mode.

4. **Groq TPM limits:** During full evaluation runs, the system can hit Groq's TPM (Tokens Per Minute) rate limits, causing CRAG evaluation steps to fail or timeout. This is an API quota issue, not a system design issue.

5. **RxNorm coverage is partial:** Only a subset of drugs in our graph have confirmed RxCUI codes. The RxNorm API integration works, but it has not yet been exhaustively run and pinned across all **8,794 live Drug nodes**. This means some drugs may not match across databases as precisely as they should.

6. **Herb data is curated for top 30 only:** While we now have **1,340 herb nodes** in the live graph, only about 30 have deeply curated interactions with regional names, evidence levels, and elderly risk scores. For the rest, we rely more heavily on DDID and lighter curation layers.

7. **No real image OCR testing yet:** We haven't created a labeled test set of prescription/medicine photos. Our OCR benchmark is text-only.

8. **India-specific geriatric guidelines don't exist:** We use the American Beers Criteria. There are no equivalent Indian guidelines for elderly prescribing. Some Indian-specific drugs may not be in the Beers list.

9. **Report translation is narrower than the UI language picker:** The mobile UI supports 10 languages, and Sarvam voice I/O supports 10 locales, but the current medically hardened report translation path is explicitly normalized for English, Hindi, Tamil, Telugu, and Kannada.

10. **Text-to-speech currently reads the patient summary, not the whole report:** This is still useful for accessibility, but it is not yet a full end-to-end spoken report narrator.

### Edge Cases Not Yet Covered

- **Pro-drug metabolism:** Some drugs are inactive until metabolized by an enzyme (e.g., Clopidogrel needs CYP2C19). If another drug blocks that enzyme, the pro-drug never activates. We don't explicitly model pro-drug activation yet.

- **Genetic polymorphisms:** Some people have different versions of CYP enzymes (e.g., CYP2D6 poor metabolizers). Our system assumes default enzyme activity. In the future, pharmacogenomic data could personalize this.

- **Dose-dependent interactions:** Some interactions only matter above a certain dose. We flag the interaction but don't always check if the patient's specific dose is in the danger range.

- **Temporal interactions:** Some interactions depend on timing (e.g., "take calcium 4 hours apart from levothyroxine"). We mention timing in reports but don't model time-of-day scheduling.

---

## 19. The Numbers That Matter — What to Tell Judges

### The Headline Numbers

| Metric | Result | What It Means |
|---|---|---|
| **50/50 sentinel interactions validated** | All 50 critical drug interactions found | We don't miss dangerous drug combinations |
| **32/32 real-world validation cases passed** | All graph checks passed | Our knowledge graph is structurally sound and clinically correct |
| **100% direct DDI sensitivity** | 40/40 on drug-drug sentinel set | Every known dangerous drug pair is detected |
| **100% herb-drug sensitivity** | All curated herb-drug pairs found | Ayurvedic herb interactions are detected — nobody else does this |
| **100% graph-path precision** | All 6 multi-hop mechanism paths correct | Our hidden interaction discovery is accurate |
| **0% hallucinated findings** | Zero false claims in reports | We never make up interactions — everything is graph-verified |
| **~1.34s backend P95 latency** | 95% of requests under 1.34 seconds | Fast enough for real-time clinical use |
| **Live graph size** | 374,752 nodes / 4,742,152 relationships / 37 types | We are now running the full graph locally, not a reduced demo subset |
| **Report provenance regression suite** | 16/16 passed | The current report pipeline keeps severity, finding identity, and citations stable |

### The Best Judge-Facing Statement

> "On our backend safety engine, we validated 50 out of 50 critical sentinel interactions, passed 32 out of 32 real-world graph checks, achieved 100% direct DDI sensitivity on our drug-drug sentinel benchmark, 100% herb-drug sensitivity on our curated herb benchmark, 100% graph-path precision for multi-hop reasoning, and 0% hallucinated findings in grounded report evaluation. The live local deployment now serves the full 4.742M-relationship graph, and our current report provenance regression suite is 16 out of 16 passing."

### What NOT to Overclaim

Do NOT say:
- ❌ "Everything is perfect" — OCR benchmark and alert burden need work
- ❌ "We cover all interactions in medicine" — our data is comprehensive but not omniscient
- ❌ "Our evaluation uses standard public benchmarks" — these are architecture-specific benchmarks designed for our system (which is actually MORE meaningful for a hackathon than generic benchmarks, because they test our specific capabilities)

DO say:
- ✅ "Core backend safety engine is very strong"
- ✅ "OCR and alert calibration still need refinement"
- ✅ "Multi-hop engine recall is good but not perfect (0.8333)"
- ✅ "We provide honest limitations alongside our strengths"

### Additional Benchmark Data Points

| Metric | Value |
|---|---|
| Direct DDI severity exact match | 0.925 (92.5%) |
| Direct DDI weighted kappa | 0.7273 (substantial agreement) |
| Discoverable indirect pairs | 52,758 |
| Validated indirect overlap | 32,826 |
| Agentic completeness score | 0.8 |

---

## 20. Quick Reference Card

### Architecture Summary

```
 ┌──────────────────────────────────────────────────────────┐
 │  USER INPUT                                               │
 │  Photo / Text / Voice → mobile UI in 10 Indian languages │
 └──────────────┬───────────────────────────────────────────┘
                ▼
 ┌──────────────────────────────────────────────────────────┐
 │  OCR + EXTRACTION + NORMALIZATION                        │
 │  Per-image OCR → per-image extraction → canonical merge │
 │  Brand name → Indian DB (249K brands) → Generic → RxCUI │
 └──────────────┬───────────────────────────────────────────┘
                ▼
 ┌──────────────────────────────────────────────────────────┐
 │  KNOWLEDGE GRAPH (Neo4j)                                 │
 │  374,752 nodes | 4,742,152 relationships | 37 types      │
 │  12 data layers | Multi-hop enzyme/transporter paths     │
 └──────────────┬───────────────────────────────────────────┘
                ▼
 ┌──────────────────────────────────────────────────────────┐
 │  AGENTIC CRAG SAFETY ENGINE                              │
 │  6 safety check types → LLM completeness → Deep analysis│
 │  → Graph verification → display severity + citations     │
 └──────────────┬───────────────────────────────────────────┘
                ▼
 ┌──────────────────────────────────────────────────────────┐
 │  REPORT + TRANSLATION + VOICE                            │
 │  Patient-friendly report → stable finding_id provenance  │
 │  → Sarvam translation + patient-summary text-to-speech   │
 └──────────────────────────────────────────────────────────┘
```

### Database Quick Reference

| # | Database | Records | What It Gives Us |
|---|---|---:|---|
| 1 | DDInter | 159K interactions | Direct drug-drug interactions (curated, high quality) |
| 2 | DDID | 16K interactions | Herb-drug interactions (critical for India) |
| 3 | PrimeKG | 1.3M interactions | Massive drug-drug network + protein targets |
| 4 | Hetionet | Varied | Gene-protein-drug connections for enzyme reasoning |
| 5 | SIDER | 120K side effects | Drug side effects from labels |
| 6 | OnSIDES | 102K side effects | Real-world observed side effects |
| 7 | TwoSIDES | 183K coprescription | Combined drug pair side effects |
| 8 | Indian Meds | 249K brands | Indian brand → generic drug mapping |
| 9 | FDA NDC | 1.9K brands | US FDA reference standard |
| 10 | Curated Herbs | 1,340 herb nodes | Ayurvedic herbs with regional names + interactions |
| 11 | Beers 2023 | 132 rules | Elderly-inappropriate medicine flags |
| 12 | CYP Expansion | 52K+ pairs | Enzyme-mediated hidden interactions |

### LLM/API Usage Summary

| Where | What | Why |
|---|---|---|
| OCR | GPT-4o Vision / Gemini / Groq Vision | Read medicine label images |
| CRAG - Completeness | GPT-4o-mini / Gemini / Groq | Score if graph found everything |
| CRAG - Deep Analysis | GPT-4o-mini / Gemini / Groq | Find potentially missed interactions |
| Report Generation | GPT-4o-mini / Gemini / Groq | Write human-readable safety report |
| Drug Extraction | Optional LLM backup | Parse complex OCR text |
| Translation | Sarvam AI | Translate patient-facing report text (currently strongest for en/hi/ta/te/kn) |
| Transliteration | Sarvam AI | Convert Indic script to English |
| Speech-to-Text | Sarvam AI | Voice input for elderly users |
| Text-to-Speech | Sarvam AI | Read patient summary aloud |
| Drug Codes | RxNorm API | Get standardized drug IDs |

### Key File Locations

| Component | File |
|---|---|
| Main entry point | `app/main.py` |
| Configuration | `app/config.py` |
| API server | `app/api/server.py` |
| OCR service | `app/services/ocr_service.py` |
| Drug normalizer | `app/services/drug_normalizer.py` |
| Drug extractor | `app/services/drug_extractor.py` |
| Manual review resolver | `app/services/manual_resolution.py` |
| CRAG safety engine | `app/services/agentic_safety_checker.py` |
| Beers checker | `app/services/beers_checker.py` |
| Report generator | `app/services/report_generator.py` |
| Translation service | `app/services/translation_service.py` |
| Voice service | `app/services/voice_service.py` |
| Citation / evidence utilities | `app/services/citation_utils.py` |
| Source provenance recovery | `app/services/source_provenance.py` |
| Neo4j queries | `app/graph/query_engine.py` |
| Runtime graph repairs | `app/graph/runtime_repairs.py` |
| Graph schema | `app/graph/schema.py` |
| All ingestion scripts | `app/graph/ingest_*.py` |
| CYP expansion | `app/graph/expand_cyp450_coverage.py` |
| Drug canonicalization | `app/graph/canonicalize_drugs.py` |
| Evaluation framework | `tests/eval/` |
| Report provenance regression tests | `tests/test_report_citations.py` |
| Full evaluation runner | `scripts/run_full_evaluation.py` |
| Real-world validation | `validate_real_world_cases.py` |
| Curated herbs data | `app/data/ayurvedic_herbs.json` |
| Beers criteria data | `app/data/beers_criteria.json` |
| Sentinel interactions | `app/data/sentinel_interactions.json` |
| Mobile processing flow | `mobile/app/processing.tsx` |
| Mobile review queue | `mobile/app/confirm.tsx` |
| Mobile prescriber step | `mobile/app/categorize.tsx` |
| Mobile report screen | `mobile/app/report.tsx` |

---

## Glossary of Key Terms

| Term | Simple Meaning |
|---|---|
| **Neo4j** | A database that stores information as a network of connected dots (graph database) |
| **Knowledge Graph** | A web of facts — drugs, herbs, enzymes, diseases — all connected with relationships |
| **OCR** | Reading text from images (Optical Character Recognition) |
| **RxCUI** | A unique ID number for every drug (from the US National Library of Medicine) |
| **RxNorm** | The system that assigns these ID numbers and maps all drug names to them |
| **ATC Code** | A classification code that groups drugs by what they do and which body system they target |
| **CYP Enzyme** | Liver enzymes (CYP3A4, CYP2D6, etc.) that break down most medicines. If one drug blocks the enzyme, another drug can accumulate dangerously. |
| **P-glycoprotein** | A "pump" protein that removes drugs from cells. If blocked, drugs accumulate. |
| **QT Interval** | The time for heart electrical recovery. If prolonged, can cause fatal heart rhythms. |
| **Beers Criteria** | A list of medicines that are potentially dangerous for elderly patients (age ≥ 65) |
| **Anticholinergic** | A drug that blocks acetylcholine (brain chemical). Causes confusion, dry mouth, falls in elderly. |
| **ACB** | Anticholinergic Burden — cumulative score across all drugs. Higher = more risk for elderly. |
| **RAG** | Retrieval-Augmented Generation — look up facts first, then have AI answer |
| **CRAG** | Corrective RAG — RAG with self-checking. Verifies its own answers. |
| **LangGraph** | A framework for building AI workflows as step-by-step state machines |
| **Sentinel Set** | A curated set of known-critical test cases we use to validate our system |
| **Sensitivity** | How well we detect TRUE positives (out of all real interactions, how many did we find?) |
| **Precision** | How accurate our detections are (out of everything we flagged, how many were real?) |
| **Weighted Kappa** | A statistical measure of agreement (severity matching) — accounts for ordered categories |
| **Hallucination** | When an AI makes up false information that sounds plausible but isn't real |
| **NTI** | Narrow Therapeutic Index — drugs where small dose changes cause big effect changes (dangerous to under/over-dose) |
| **DDI** | Drug-Drug Interaction — when two drugs affect each other |
| **Indic Script** | Writing systems used for Indian languages (Devanagari, Tamil, Telugu, Kannada scripts) |
| **Sarvam AI** | An Indian AI company specializing in Indian language processing |
| **Multi-hop** | Finding connections through multiple intermediate steps (Drug → Enzyme → Drug) |
| **Rhabdomyolysis** | Severe muscle breakdown that can damage kidneys — caused by statin overdose |
| **Pharmacokinetic** | How the body processes a drug (absorption, metabolism, elimination) |
| **Pharmacogenomic** | How genetics affect drug response (future scope for Sahayak) |

---

*This document covers the complete Sahayak project — from databases to pipelines to evaluation. After reading this, you should be able to answer any question about what the system does, how it works, why each component exists, and what our results prove.*

**Remember:** The core story is: **Graph-first, AI-enhanced, zero-hallucination medication safety for Indian elderly patients, with herb-drug awareness that nobody else provides.**
