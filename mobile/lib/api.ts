import { API_BASE_URL } from "./constants"
import { toSarvamLocale } from "./i18n"
import type {
  ExtractedDrug,
  Interaction,
  SafetyReport,
  PrescriberSource,
  OcrResult,
  AnalyzeResult,
  PatientInfo,
  ManualResolutionResponse,
} from "../types/sahayak"

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text().catch(() => "Unknown error")
    throw new Error(`API error ${res.status}: ${text}`)
  }
  return res.json() as Promise<T>
}

// ── OCR ────────────────────────────────────────────────────────────────────

export async function ocrImage(
  imageUri: string,
  type: "allopathic" | "ayurvedic"
): Promise<OcrResult> {
  const formData = new FormData()
  const filename = imageUri.split("/").pop() ?? "image.jpg"
  formData.append("file", { uri: imageUri, name: filename, type: "image/jpeg" } as unknown as Blob)
  formData.append("type", type)

  const res = await fetch(`${API_BASE_URL}/ocr`, {
    method: "POST",
    body: formData,
  })
  return handleResponse(res)
}

// ── Analyze (extract drugs from OCR text) ──────────────────────────────────

export async function analyzeMedicines(ocrResults: OcrResult[]): Promise<AnalyzeResult> {
  const res = await fetch(`${API_BASE_URL}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ocr_results: ocrResults }),
  })
  return handleResponse(res)
}

export async function extractDrugsFromText(text: string): Promise<ExtractedDrug[]> {
  const res = await fetch(`${API_BASE_URL}/extract-drugs-from-text`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  })
  const data = await handleResponse<{ drugs?: ExtractedDrug[] }>(res)
  return data.drugs ?? []
}

export async function resolveManualMedicine(params: {
  text: string
  medicine_type: "allopathic" | "ayurvedic"
  source_lang?: string
}): Promise<ManualResolutionResponse> {
  const res = await fetch(`${API_BASE_URL}/resolve-manual-medicine`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text: params.text,
      medicine_type: params.medicine_type,
      source_lang: toSarvamLocale(params.source_lang ?? "en"),
    }),
  })
  return handleResponse(res)
}

// ── Safety Check (full agentic pipeline) ───────────────────────────────────

export async function safetyCheck(params: {
  drugs: string[]
  herbs: string[]
  age: number
  gender?: string
  conditions?: string[]
  prescriber_info?: Record<string, string>
  weight_kg?: number
  systolic_bp?: number | null
  diastolic_bp?: number | null
  fasting_blood_sugar?: number | null
  spo2?: number | null
  heart_rate?: number | null
  serum_creatinine?: number | null
}): Promise<Record<string, unknown>> {
  const res = await fetch(`${API_BASE_URL}/safety-check`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  })
  return handleResponse(res)
}

// ── Generate Report ────────────────────────────────────────────────────────

export async function generateReport(params: {
  safety_report: Record<string, unknown>
  patient_info: Record<string, unknown>
  language: string
}): Promise<SafetyReport> {
  const res = await fetch(`${API_BASE_URL}/generate-report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      ...params,
      language: toSarvamLocale(params.language),
    }),
  })
  return handleResponse(res)
}

// ── Legacy Report (used by categorize → report flow) ───────────────────────

export async function generateReportLegacy(params: {
  medicines: ExtractedDrug[]
  prescriber_map: Record<string, PrescriberSource>
  interactions: Interaction[]
  language: string
}): Promise<SafetyReport> {
  const res = await fetch(`${API_BASE_URL}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  })
  return handleResponse(res)
}

// ── Text-to-Speech ─────────────────────────────────────────────────────────

export async function textToSpeech(
  text: string,
  language: string
): Promise<{ audio_base64: string }> {
  const res = await fetch(`${API_BASE_URL}/text-to-speech`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      text,
      language: toSarvamLocale(language),
    }),
  })
  return handleResponse(res)
}

// ── Speech-to-Text ─────────────────────────────────────────────────────────

export async function speechToText(
  audioUri: string,
  language: string = "auto"
): Promise<{ transcript: string; language: string; confidence: number }> {
  const formData = new FormData()
  const filename = audioUri.split("/").pop() ?? "audio.m4a"
  formData.append("file", { uri: audioUri, name: filename, type: "audio/m4a" } as unknown as Blob)
  formData.append("language", language)

  const res = await fetch(`${API_BASE_URL}/speech-to-text`, {
    method: "POST",
    body: formData,
  })
  return handleResponse(res)
}

// ── Translate Report ───────────────────────────────────────────────────────

export async function translateReport(
  report: Record<string, unknown>,
  targetLanguage: string
): Promise<{ language: string; translated: Record<string, unknown> }> {
  const res = await fetch(`${API_BASE_URL}/translate-report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      report,
      target_language: toSarvamLocale(targetLanguage),
    }),
  })
  return handleResponse(res)
}

// ── Resolve Drug ───────────────────────────────────────────────────────────

export async function resolveDrug(
  name: string,
  sourceLang: string = "en-IN"
): Promise<{
  found: boolean
  generic_name: string
  match_type: string
  confidence: number
}> {
  const res = await fetch(`${API_BASE_URL}/resolve-drug`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, source_lang: toSarvamLocale(sourceLang) }),
  })
  return handleResponse(res)
}

// ── Resolve Herb ───────────────────────────────────────────────────────────

export async function resolveHerb(
  name: string,
  sourceLang: string = "en-IN"
): Promise<{
  found: boolean
  name: string
  hindi_name: string
  match_type: string
  confidence: number
}> {
  const res = await fetch(`${API_BASE_URL}/resolve-herb`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, source_lang: toSarvamLocale(sourceLang) }),
  })
  return handleResponse(res)
}
