"""SAHAYAK FastAPI server — clean, portable API for frontend consumption.

Core endpoints
--------------
GET  /healthz                → Deep healthcheck (Neo4j + graph stats)
POST /resolve-drug           → Resolve drug name → canonical form
POST /resolve-herb           → Resolve herb name (supports regional languages)
POST /extract-drugs-from-text → Extract drugs from OCR / free text
POST /resolve-manual-medicine → Canonicalize a user-entered medicine during mobile review
POST /safety-check           → Full agentic safety pipeline
POST /generate-report        → Multilingual patient-friendly report

Optional endpoints
------------------
POST /translate-report       → Translate an English report to a target language
POST /speech-to-text         → Transcribe audio via Sarvam STT
POST /text-to-speech         → Synthesize speech via Sarvam TTS

Legacy / mobile endpoints
-------------------------
GET  /health                 → Simple healthcheck
POST /ocr                    → OCR a medicine image (multipart)
POST /analyze                → Extract drugs + check interactions
POST /report                 → Generate report (legacy schema)
"""

from __future__ import annotations

import asyncio
import base64
import dataclasses
import logging
from contextlib import asynccontextmanager
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Verify Neo4j on startup; close driver on shutdown."""
    from app.graph.neo4j_connection import verify_connectivity, close_driver
    from app.graph.runtime_repairs import start_runtime_graph_repairs
    if verify_connectivity():
        logger.info("Neo4j connection verified at startup")
        repair_status = start_runtime_graph_repairs()
        logger.info("Runtime graph repair startup status: %s", repair_status)
    else:
        logger.warning("Neo4j is NOT reachable — graph endpoints will fail")
    yield
    close_driver()
    logger.info("Neo4j driver closed")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="SAHAYAK API",
    description="AI-powered medication safety assistant for Indian elderly patients",
    version="3.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════════
# REQUEST / RESPONSE SCHEMAS  (Pydantic v2)
# ═══════════════════════════════════════════════════════════════════════════════

# ── /healthz ──────────────────────────────────────────────────────────────────
class HealthResponse(BaseModel):
    status: str = Field(..., examples=["ok"])
    service: str = "SAHAYAK"
    neo4j: bool
    graph_nodes: int = 0


# ── /resolve-drug ─────────────────────────────────────────────────────────────
class ResolveDrugRequest(BaseModel):
    name: str = Field(..., min_length=1, examples=["Ecosprin 75"])
    source_lang: str = Field("en-IN", examples=["kn-IN"])

class ResolvedDrugResponse(BaseModel):
    found: bool
    generic_name: str = ""
    rxcui: str = ""
    drug_class: str = ""
    is_beers: bool = False
    is_nti: bool = False
    anticholinergic_score: int = 0
    renal_dose_adjust: str = ""
    match_type: str = ""
    confidence: float = 0.0
    raw_input: str = ""
    ingredients: list[str] = []


# ── /resolve-herb ─────────────────────────────────────────────────────────────
class ResolveHerbRequest(BaseModel):
    name: str = Field(..., min_length=1, examples=["ashwagandha"])
    source_lang: str = Field("en-IN", examples=["hi-IN"])

class ResolvedHerbResponse(BaseModel):
    found: bool
    name: str = ""
    hindi_name: str = ""
    category: str = ""
    match_type: str = ""
    confidence: float = 0.0
    raw_input: str = ""
    herb_in_database: bool = False
    has_interaction_data: bool = False


# ── /extract-drugs-from-text ──────────────────────────────────────────────────
class ExtractDrugsRequest(BaseModel):
    text: str = Field(..., min_length=1, examples=["Tab Dolo 650mg\nCap Omeprazole 20mg"])

class ExtractDrugsResponse(BaseModel):
    drugs: list[dict[str, Any]]


# ── /resolve-manual-medicine ──────────────────────────────────────────────────
class ResolveManualMedicineRequest(BaseModel):
    text: str = Field(..., min_length=1, examples=["Dolo 650"])
    medicine_type: str = Field("allopathic", examples=["ayurvedic"])
    source_lang: str = Field("en-IN", examples=["hi-IN"])

class ResolveManualMedicineResponse(BaseModel):
    medicines: list[dict[str, Any]]
    resolution_stage: str
    resolved_from: str


# ── /safety-check ─────────────────────────────────────────────────────────────
class SafetyCheckRequest(BaseModel):
    drugs: list[str] = Field(default=[], examples=[["metformin", "warfarin"]])
    herbs: list[str] = Field(default=[], examples=[["ashwagandha"]])
    age: int = Field(65, ge=0, le=150)
    gender: str = ""
    weight_kg: float = 0.0
    conditions: list[str] = []
    prescriber_info: dict[str, str] = {}
    # Optional vitals / labs
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    fasting_blood_sugar: Optional[float] = None
    postprandial_blood_sugar: Optional[float] = None
    spo2: Optional[int] = None
    heart_rate: Optional[int] = None
    serum_creatinine: Optional[float] = None


# ── /generate-report ──────────────────────────────────────────────────────────
class GenerateReportRequest(BaseModel):
    safety_report: dict[str, Any]
    patient_info: dict[str, Any] = {}
    language: str = Field("en-IN", examples=["ta-IN"])


# ── /translate-report ─────────────────────────────────────────────────────────
class TranslateReportRequest(BaseModel):
    report: dict[str, Any]
    target_language: str = Field(..., examples=["hi-IN"])


# ── /speech-to-text ───────────────────────────────────────────────────────────
class SpeechToTextResponse(BaseModel):
    transcript: str
    language: str
    confidence: float


# ── /text-to-speech ───────────────────────────────────────────────────────────
class TextToSpeechRequest(BaseModel):
    text: str = Field(..., min_length=1)
    language: str = Field("hi-IN", examples=["hi-IN"])
    voice: str = "anushka"


# ── legacy /ocr ───────────────────────────────────────────────────────────────
class OcrResult(BaseModel):
    text: str
    confidence: float
    language: str
    needs_fallback: bool
    medicine_type: str

class AnalyzeRequest(BaseModel):
    ocr_results: list[dict[str, Any]]

class AnalyzeResponse(BaseModel):
    allopathic_medicines: list[dict[str, Any]]
    ayurvedic_medicines: list[dict[str, Any]]
    interactions: list[dict[str, Any]]

class ReportRequest(BaseModel):
    medicines: list[dict[str, Any]]
    prescriber_map: dict[str, str] = {}
    interactions: list[dict[str, Any]] = []
    language: str = "hi"
    age: Optional[int] = None
    gender: str = ""
    weight_kg: float = 0.0
    conditions: list[str] = []
    systolic_bp: Optional[int] = None
    diastolic_bp: Optional[int] = None
    fasting_blood_sugar: Optional[float] = None
    postprandial_blood_sugar: Optional[float] = None
    spo2: Optional[int] = None
    heart_rate: Optional[int] = None
    serum_creatinine: Optional[float] = None


# ═══════════════════════════════════════════════════════════════════════════════
# CORE ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "SAHAYAK"}


@app.get("/ping", include_in_schema=False, response_model=None)
async def ping() -> Response:
    """RunPod load-balancer health endpoint.

    Return 204 while dependencies are still warming up and 200 once Neo4j is reachable.
    """
    from app.graph.neo4j_connection import verify_connectivity

    if verify_connectivity():
        return JSONResponse({"status": "healthy"})
    return Response(status_code=204)


@app.get("/healthz", response_model=HealthResponse)
async def healthz() -> HealthResponse:
    """Deep healthcheck — Neo4j connectivity + graph node count."""
    from app.graph.neo4j_connection import verify_connectivity, get_driver
    neo4j_ok = verify_connectivity()
    graph_nodes = 0
    if neo4j_ok:
        try:
            with get_driver().session() as s:
                record = s.run("MATCH (n) RETURN count(n) AS cnt").single()
                graph_nodes = record["cnt"] if record else 0
        except Exception:
            pass
    return HealthResponse(
        status="ok" if neo4j_ok else "degraded",
        neo4j=neo4j_ok,
        graph_nodes=graph_nodes,
    )


# ── /resolve-drug ─────────────────────────────────────────────────────────────
@app.post("/resolve-drug", response_model=ResolvedDrugResponse)
async def resolve_drug_endpoint(request: ResolveDrugRequest) -> ResolvedDrugResponse:
    """Resolve a drug name (brand, generic, OCR output) to canonical form."""
    from app.graph.query_engine import resolve_drug_name
    from app.services.translation_service import translate, detect_language
    try:
        drug_input = request.name.strip()
        source = request.source_lang.strip()

        # If non-English source, translate to English first
        if source and not source.startswith("en"):
            try:
                detected = detect_language(drug_input)
                if detected != "en-IN":
                    translated = translate(drug_input, detected, "en-IN")
                    if translated and not translated.endswith("[Translation unavailable]") and translated != drug_input:
                        # Try translated name first
                        result = resolve_drug_name(translated)
                        if result.found:
                            return ResolvedDrugResponse(**dataclasses.asdict(result))
                        drug_input = translated  # Use translated name for further lookup
            except Exception as exc:
                logger.warning("Drug translation from %s failed: %s", source, exc)

        result = resolve_drug_name(drug_input)
        return ResolvedDrugResponse(**dataclasses.asdict(result))
    except Exception as exc:
        logger.exception("resolve_drug_name failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── /resolve-herb ─────────────────────────────────────────────────────────────
@app.post("/resolve-herb", response_model=ResolvedHerbResponse)
async def resolve_herb_endpoint(request: ResolveHerbRequest) -> ResolvedHerbResponse:
    """Resolve an herb name (English or regional Indian language)."""
    from app.graph.query_engine import resolve_herb_name
    from app.services.translation_service import translate_herb_to_english

    herb_input = request.name.strip()
    source = request.source_lang.strip()

    # If non-English source, try translating to English first
    if source and not source.startswith("en"):
        try:
            english_name = translate_herb_to_english(herb_input, source)
            if english_name and english_name != herb_input:
                herb_input = english_name
        except Exception as exc:
            logger.warning("Herb translation from %s failed: %s", source, exc)

    try:
        result = resolve_herb_name(herb_input)
        return ResolvedHerbResponse(**dataclasses.asdict(result))
    except Exception as exc:
        logger.exception("resolve_herb_name failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── /extract-drugs-from-text ──────────────────────────────────────────────────
@app.post("/extract-drugs-from-text", response_model=ExtractDrugsResponse)
async def extract_drugs_from_text_endpoint(request: ExtractDrugsRequest) -> ExtractDrugsResponse:
    """Extract drug names from OCR / free text."""
    from app.services.drug_extractor import extract_drugs_from_text
    try:
        drugs = await extract_drugs_from_text(request.text.strip())
        return ExtractDrugsResponse(drugs=drugs)
    except Exception as exc:
        logger.exception("extract_drugs_from_text failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/resolve-manual-medicine", response_model=ResolveManualMedicineResponse)
async def resolve_manual_medicine_endpoint(
    request: ResolveManualMedicineRequest,
) -> ResolveManualMedicineResponse:
    """Resolve a user-entered medicine name for the mobile manual-review flow."""
    from app.services.manual_resolution import resolve_manual_medicine

    medicine_type = request.medicine_type.strip().lower()
    if medicine_type not in {"allopathic", "ayurvedic"}:
        raise HTTPException(status_code=400, detail="medicine_type must be 'allopathic' or 'ayurvedic'")

    payload = await resolve_manual_medicine(
        text=request.text,
        medicine_type=medicine_type,
        source_lang=request.source_lang,
    )
    return ResolveManualMedicineResponse(**payload)


# ── /safety-check ─────────────────────────────────────────────────────────────
@app.post("/safety-check")
async def safety_check_endpoint(request: SafetyCheckRequest) -> dict[str, Any]:
    """Run the full agentic safety check pipeline."""
    if not request.drugs and not request.herbs:
        raise HTTPException(status_code=400, detail="Provide at least one drug or herb")
    from app.services.agentic_safety_checker import run_safety_check
    patient_data: dict[str, Any] = {
        "drugs": request.drugs,
        "herbs": request.herbs,
        "age": request.age,
        "gender": request.gender,
        "weight_kg": request.weight_kg,
        "conditions": request.conditions,
        "prescriber_info": request.prescriber_info,
    }
    # Pass optional vitals / labs
    for field_name in (
        "systolic_bp", "diastolic_bp", "fasting_blood_sugar",
        "postprandial_blood_sugar", "spo2", "heart_rate", "serum_creatinine",
    ):
        val = getattr(request, field_name, None)
        if val is not None:
            patient_data[field_name] = val
    try:
        report = await run_safety_check(patient_data)
        return report
    except Exception as exc:
        logger.exception("safety_check failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── /generate-report ──────────────────────────────────────────────────────────
@app.post("/generate-report")
async def generate_report_endpoint(request: GenerateReportRequest) -> dict[str, Any]:
    """Generate a patient-friendly multilingual safety report."""
    from app.services.report_generator import generate_report
    try:
        report = await generate_report(
            request.safety_report, request.patient_info, request.language,
        )
        return report
    except Exception as exc:
        logger.exception("generate_report failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# OPTIONAL ENDPOINTS  (translation, voice)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/translate-report")
async def translate_report_endpoint(request: TranslateReportRequest) -> dict[str, Any]:
    """Translate an English report dict to the target language."""
    from app.services.translation_service import translate_report
    try:
        translated = translate_report(request.report, request.target_language)
        return {"language": request.target_language, "translated": translated}
    except Exception as exc:
        logger.exception("translate_report failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/speech-to-text", response_model=SpeechToTextResponse)
async def speech_to_text_endpoint(
    file: UploadFile = File(...),
    language: str = Form("auto"),
) -> SpeechToTextResponse:
    """Transcribe audio using Sarvam speech-to-text."""
    from app.services.voice_service import speech_to_text
    audio_bytes = await file.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")
    try:
        result = await asyncio.to_thread(speech_to_text, audio_bytes, language)
        return SpeechToTextResponse(**result)
    except Exception as exc:
        logger.exception("speech_to_text failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/text-to-speech")
async def text_to_speech_endpoint(request: TextToSpeechRequest) -> Response:
    """Synthesize speech using Sarvam text-to-speech. Returns base64 audio JSON."""
    from app.services.voice_service import text_to_speech
    try:
        audio_data = await asyncio.to_thread(
            text_to_speech, request.text, request.language, request.voice,
        )
        if not audio_data:
            raise HTTPException(status_code=502, detail="TTS returned empty audio")
        audio_b64 = base64.b64encode(audio_data).decode("ascii")
        return Response(
            content='{"audio_base64":"' + audio_b64 + '"}',
            media_type="application/json",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("text_to_speech failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ═══════════════════════════════════════════════════════════════════════════════
# LEGACY / MOBILE ENDPOINTS  (kept for backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/ocr", response_model=OcrResult)
async def ocr_endpoint(
    file: UploadFile = File(...),
    type: str = Form("allopathic"),
) -> OcrResult:
    """Accept an image file and return OCR text + metadata."""
    from app.services.ocr_service import extract_text_from_image
    if type not in {"allopathic", "ayurvedic"}:
        raise HTTPException(status_code=400, detail="type must be 'allopathic' or 'ayurvedic'")
    image_bytes = await file.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Empty file upload")
    try:
        result = await extract_text_from_image(image_bytes)
    except Exception as exc:
        logger.exception("OCR failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return OcrResult(
        text=result["text"],
        confidence=result["confidence"],
        language=result.get("language", "unknown"),
        needs_fallback=result.get("needs_fallback", False),
        medicine_type=type,
    )


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_endpoint(request: AnalyzeRequest) -> AnalyzeResponse:
    """Extract drugs from OCR results and check for interactions."""
    from app.services.drug_extractor import extract_drugs_from_text as _extract
    from app.services.agentic_safety_checker import run_safety_check as _safety
    ocr_results = request.ocr_results
    if not ocr_results:
        raise HTTPException(status_code=400, detail="ocr_results must not be empty")
    allo_texts: list[str] = []
    ayur_texts: list[str] = []
    for r in ocr_results:
        text = str(r.get("text", "")).strip()
        if not text:
            continue
        mtype = str(r.get("medicine_type") or r.get("type", "allopathic")).strip().lower()
        (ayur_texts if mtype == "ayurvedic" else allo_texts).append(text)
    try:
        allo_meds = await _extract("\n".join(allo_texts)) if allo_texts else []
        ayur_meds = await _extract("\n".join(ayur_texts)) if ayur_texts else []
    except Exception as exc:
        logger.exception("Drug extraction failed")
        raise HTTPException(status_code=500, detail=f"Drug extraction error: {exc}") from exc
    all_meds = allo_meds + ayur_meds
    interactions: list[dict[str, Any]] = []
    if all_meds:
        try:
            safety = await _safety({"medicines": all_meds, "prescriber_info": {}})
            interactions = _flatten_interactions(safety)
        except Exception as exc:
            logger.warning("Safety check failed (non-fatal): %s", exc)
    return AnalyzeResponse(
        allopathic_medicines=[_drug_to_dict(m) for m in allo_meds],
        ayurvedic_medicines=[_drug_to_dict(m) for m in ayur_meds],
        interactions=interactions,
    )


@app.post("/report")
async def report_endpoint(request: ReportRequest) -> dict[str, Any]:
    """Generate a multilingual patient-safe safety report (legacy schema)."""
    from app.services.report_generator import generate_report as _gen
    if not request.medicines:
        raise HTTPException(status_code=400, detail="medicines list must not be empty")
    language = _normalize_language(request.language)
    patient_info: dict[str, Any] = {"prescriber_info": request.prescriber_map}
    # Include all patient data for personalized report
    if request.age:
        patient_info["age"] = request.age
    if request.gender:
        patient_info["gender"] = request.gender
    if request.weight_kg:
        patient_info["weight_kg"] = request.weight_kg
    if request.conditions:
        patient_info["conditions"] = request.conditions
    # Include vitals if provided
    for field_name in (
        "systolic_bp", "diastolic_bp", "fasting_blood_sugar",
        "postprandial_blood_sugar", "spo2", "heart_rate", "serum_creatinine",
    ):
        val = getattr(request, field_name, None)
        if val is not None:
            patient_info[field_name] = val
    safety_report: dict[str, Any] = {
        "findings": request.interactions or [],
        "summary": {"total_issues": len(request.interactions)},
    }
    try:
        report = await _gen(safety_report, patient_info, language)
    except Exception as exc:
        logger.exception("Report generation failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return report


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _drug_to_dict(drug: Any) -> dict[str, Any]:
    if isinstance(drug, dict):
        return drug
    try:
        return dataclasses.asdict(drug)
    except Exception:
        return vars(drug) if hasattr(drug, "__dict__") else {}


def _flatten_interactions(safety: Any) -> list[dict[str, Any]]:
    if isinstance(safety, dict):
        for key in ("findings", "direct_interactions", "interactions"):
            val = safety.get(key)
            if isinstance(val, list):
                return val
    return []


def _normalize_language(lang: str) -> str:
    mapping = {
        "hi": "hi-IN", "ta": "ta-IN", "te": "te-IN", "kn": "kn-IN",
        "ml": "ml-IN", "mr": "mr-IN", "bn": "bn-IN", "gu": "gu-IN",
        "pa": "pa-IN", "en": "en-IN",
    }
    return mapping.get(lang, lang)
