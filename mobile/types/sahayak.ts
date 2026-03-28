export interface ExtractedDrug {
  brand_name: string
  generic_name: string
  active_ingredients: { name: string; dose: string; graph_match: boolean }[]
  dosage_form: string
  confidence: number
  graph_match: boolean
  match_type: string
  medicine_id?: string
  entry_origin?: "ocr" | "manual" | "restored"
  source_image_key?: string
  ocr_confidence?: number
  ocr_language?: string
  ocr_needs_fallback?: boolean
  image_uri?: string
  medicine_type?: "allopathic" | "ayurvedic"
}

export type InteractionSeverity = "critical" | "major" | "moderate" | "minor" | "unknown"
export type InteractionDisplaySeverity = "critical" | "major" | "moderate" | "minor" | "doctor_review"
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
  finding_id?: string
  severity: InteractionSeverity
  display_severity?: InteractionDisplaySeverity
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

export interface PatientInfo {
  name: string
  age: number | null
  gender: "" | "male" | "female" | "other"
  conditions: string[]
  weight_kg: number | null
  systolic_bp: number | null
  diastolic_bp: number | null
  fasting_blood_sugar: number | null
  spo2: number | null
  heart_rate: number | null
  serum_creatinine: number | null
}

export interface OcrResult {
  text: string
  confidence: number
  language: string
  needs_fallback: boolean
  medicine_type: "allopathic" | "ayurvedic"
}

export interface OcrFailure {
  imageIndex: number
  type: "allopathic" | "ayurvedic"
  reason: string
  imageUri: string
  sourceImageKey?: string
  failureType?: "ocr" | "extraction"
}

export interface ImageProcessingResult {
  imageIndex: number
  type: "allopathic" | "ayurvedic"
  imageUri: string
  medicines?: ExtractedDrug[]
  review_status?: "detected" | "manual_pending" | "manually_resolved" | "ignored"
  resolved_medicine_ids?: string[]
  pending_review_ids?: string[]
  failureType?: "ocr" | "extraction"
  failureReason?: string
}

export interface ScanMeta {
  totalScanned: number
  detectedCount: number
  manualReviewCount?: number
  ignoredCount?: number
  failedImages: Array<{
    imageIndex: number
    type: "allopathic" | "ayurvedic"
    reason: string
    imageUri?: string
    sourceImageKey?: string
    failureType: "ocr" | "extraction"
  }>
}

export interface AnalyzeResult {
  allopathic_medicines: ExtractedDrug[]
  ayurvedic_medicines: ExtractedDrug[]
  interactions: Interaction[]
}

export interface ManualResolutionResponse {
  medicines: ExtractedDrug[]
  resolution_stage: "extract" | "resolve" | "manual_fallback"
  resolved_from: "extract_drugs_from_text" | "resolve_drug" | "resolve_herb" | "manual_fallback"
}
