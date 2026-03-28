import { create } from "zustand"
import type {
  ExtractedDrug,
  Interaction,
  SafetyReport,
  PrescriberSource,
  PatientInfo,
  OcrFailure,
  ImageProcessingResult,
  ScanMeta,
} from "../types/sahayak"

const EMPTY_PATIENT: PatientInfo = {
  name: "",
  age: null,
  gender: "",
  conditions: [],
  weight_kg: null,
  systolic_bp: null,
  diastolic_bp: null,
  fasting_blood_sugar: null,
  spo2: null,
  heart_rate: null,
  serum_creatinine: null,
}

interface AppState {
  language: string
  patientInfo: PatientInfo

  // Multiple photos per category
  allopathicImageUris: string[]
  ayurvedicImageUris: string[]

  allopathicMedicines: ExtractedDrug[]
  ayurvedicMedicines: ExtractedDrug[]
  confirmedMedicines: ExtractedDrug[]
  ocrFailures: OcrFailure[]
  allImageResults: ImageProcessingResult[]
  scanMeta: ScanMeta | null
  prescriberMap: Record<string, PrescriberSource>
  interactions: Interaction[]
  safetyReport: SafetyReport | null

  // Raw safety-check response (used by report generator)
  safetyCheckResult: Record<string, unknown> | null

  setLanguage: (lang: string) => void
  setPatientInfo: (info: Partial<PatientInfo>) => void
  addAllopathicImage: (uri: string) => void
  removeAllopathicImage: (uri: string) => void
  addAyurvedicImage: (uri: string) => void
  removeAyurvedicImage: (uri: string) => void
  setAllopathicMedicines: (medicines: ExtractedDrug[]) => void
  setAyurvedicMedicines: (medicines: ExtractedDrug[]) => void
  setConfirmedMedicines: (medicines: ExtractedDrug[]) => void
  setOcrFailures: (failures: OcrFailure[]) => void
  setAllImageResults: (results: ImageProcessingResult[]) => void
  setScanMeta: (meta: ScanMeta | null) => void
  updatePrescriberSource: (medicineId: string, source: PrescriberSource) => void
  setInteractions: (interactions: Interaction[]) => void
  setSafetyReport: (report: SafetyReport | null) => void
  setSafetyCheckResult: (result: Record<string, unknown> | null) => void
  reset: () => void
}

export const useAppStore = create<AppState>((set) => ({
  language: "hi",
  patientInfo: { ...EMPTY_PATIENT },
  allopathicImageUris: [],
  ayurvedicImageUris: [],
  allopathicMedicines: [],
  ayurvedicMedicines: [],
  confirmedMedicines: [],
  ocrFailures: [],
  allImageResults: [],
  scanMeta: null,
  prescriberMap: {},
  interactions: [],
  safetyReport: null,
  safetyCheckResult: null,

  setLanguage: (language) => set({ language }),
  setPatientInfo: (info) =>
    set((state) => ({
      patientInfo: { ...state.patientInfo, ...info },
    })),
  addAllopathicImage: (uri) =>
    set((state) => ({
      allopathicImageUris: [...state.allopathicImageUris, uri],
    })),
  removeAllopathicImage: (uri) =>
    set((state) => ({
      allopathicImageUris: state.allopathicImageUris.filter((u) => u !== uri),
    })),
  addAyurvedicImage: (uri) =>
    set((state) => ({
      ayurvedicImageUris: [...state.ayurvedicImageUris, uri],
    })),
  removeAyurvedicImage: (uri) =>
    set((state) => ({
      ayurvedicImageUris: state.ayurvedicImageUris.filter((u) => u !== uri),
    })),
  setAllopathicMedicines: (allopathicMedicines) => set({ allopathicMedicines }),
  setAyurvedicMedicines: (ayurvedicMedicines) => set({ ayurvedicMedicines }),
  setConfirmedMedicines: (confirmedMedicines) => set({ confirmedMedicines }),
  setOcrFailures: (ocrFailures) => set({ ocrFailures }),
  setAllImageResults: (allImageResults) => set({ allImageResults }),
  setScanMeta: (scanMeta) => set({ scanMeta }),
  updatePrescriberSource: (medicineId, source) =>
    set((state) => ({
      prescriberMap: { ...state.prescriberMap, [medicineId]: source },
    })),
  setInteractions: (interactions) => set({ interactions }),
  setSafetyReport: (safetyReport) => set({ safetyReport }),
  setSafetyCheckResult: (safetyCheckResult) => set({ safetyCheckResult }),
  reset: () =>
    set({
      patientInfo: { ...EMPTY_PATIENT },
      allopathicImageUris: [],
      ayurvedicImageUris: [],
      allopathicMedicines: [],
      ayurvedicMedicines: [],
      confirmedMedicines: [],
      ocrFailures: [],
      allImageResults: [],
      scanMeta: null,
      prescriberMap: {},
      interactions: [],
      safetyReport: null,
      safetyCheckResult: null,
    }),
}))
