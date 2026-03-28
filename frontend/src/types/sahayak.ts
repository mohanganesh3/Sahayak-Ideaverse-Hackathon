export interface ExtractedDrug {
  brand_name: string
  generic_name: string
  active_ingredients: { name: string; dose: string; graph_match: boolean }[]
  dosage_form: string
  confidence: number           // 0.0 – 1.0
  graph_match: boolean
  match_type: string
  ocr_confidence?: number
  ocr_language?: string
  ocr_needs_fallback?: boolean
}

export type InteractionSeverity = "critical" | "major" | "moderate" | "minor" | "unknown"
export type PrescriberSource = "doctor" | "self" | "medical_shop"

export interface ReportLink {
  label: string
  url: string
}

export interface ReportCitation {
  source_key: string
  source_label: string
  source_url?: string
  provenance_type?: string
  provenance_label?: string
  evidence_scope?: string
  evidence_scope_label?: string
  evidence_scope_description?: string
  source_layer: string
  relation_type: string
  evidence_type: string
  evidence: string
  backing_source_key?: string
  backing_source_label?: string
  backing_source_url?: string
  confidence?: number
  table?: string
  reference?: string
  reference_url?: string
  record_locator?: string
  note?: string
  provenance_note?: string
  evidence_basis?: string
  doi?: string
  pmid?: string
  study_count?: number
  reference_url_type?: string
  record_links?: ReportLink[]
}

export interface Interaction {
  severity: InteractionSeverity
  title: string
  patient_explanation: string
  doctor_explanation: string
  action: string
  medicines: string[]
  confidence: "high" | "medium" | "low"
  source: string
  citations?: ReportCitation[]
  evidence_profile?: string
  evidence_profile_note?: string
}

export interface AcbSection {
  score: number
  risk: string
  drugs: string[]
  citations?: ReportCitation[]
}

export interface ReportContent {
  patient_summary: string
  findings: Interaction[]
  acb_section: AcbSection
  self_prescribed_warning: string | null
  personalized_advice?: string | null
  disclaimer: string
}

export interface SafetyReport {
  language: string
  english: ReportContent
  translated: ReportContent
}

export interface OcrResult {
  text: string
  confidence: number
  language: string
  needs_fallback: boolean
}

export interface OcrFailure {
  imageIndex: number
  type: "allopathic" | "ayurvedic"
  reason: string
  imageDataUrl: string   // thumbnail for display on confirm page
  failureType?: "ocr" | "extraction"  // "ocr" = couldn't read text, "extraction" = text read but no medicine identified
}

export interface OcrImageResult {
  imageIndex: number
  type: "allopathic" | "ayurvedic"
  text: string
  confidence: number
  language: string
  needs_fallback: boolean
  imageDataUrl: string
}

// Tracks per-image extraction result (both successes and failures)
export interface ImageProcessingResult {
  imageIndex: number
  type: "allopathic" | "ayurvedic"
  imageDataUrl: string
  medicine?: ExtractedDrug          // set if extraction succeeded
  failureType?: "ocr" | "extraction"
  failureReason?: string
}

export type ProcessingStepStatus = "pending" | "active" | "done" | "error"

export interface ProcessingStep {
  id: string
  label: string
  labelHi: string
  status: ProcessingStepStatus
}
