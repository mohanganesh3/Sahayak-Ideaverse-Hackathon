"use client"

import { create } from "zustand"
import { persist, createJSONStorage } from "zustand/middleware"
import type { ExtractedDrug, Interaction, SafetyReport, PrescriberSource, OcrFailure, ImageProcessingResult } from "@/types/sahayak"

interface ScanMeta {
  totalScanned: number
  detectedCount: number
  failedImages: Array<{
    imageIndex: number
    type: "allopathic" | "ayurvedic"
    reason: string
    failureType: "ocr" | "extraction"
  }>
}

interface AppState {
  // Session
  language: string
  step: number

  // Patient
  patientName: string
  patientAge: number
  patientConditions: string[]

  // Optional vitals / labs
  systolicBp?: number
  diastolicBp?: number
  fastingBloodSugar?: number
  postprandialBloodSugar?: number
  spo2?: number
  heartRate?: number
  serumCreatinine?: number

  // Upload — arrays of base64 data URLs for multiple images per type
  allopathicImages: string[]
  ayurvedicImages: string[]

  // Extracted
  allopathicMedicines: ExtractedDrug[]
  ayurvedicMedicines: ExtractedDrug[]
  confirmedMedicines: ExtractedDrug[]

  // OCR failures — images whose text could not be extracted
  ocrFailures: OcrFailure[]

  // Per-image processing results (all images with their extraction status)
  allImageResults: ImageProcessingResult[]

  // Prescriber categorization: medicine generic_name -> source
  prescriberMap: Record<string, PrescriberSource>

  // Analysis
  interactions: Interaction[]
  safetyReport: SafetyReport | null

  // Persisted scan metadata (survives page reload — no image data URLs)
  scanMeta: ScanMeta | null

  // Actions
  setLanguage: (lang: string) => void
  setStep: (step: number) => void
  setPatientName: (name: string) => void
  setPatientAge: (age: number) => void
  setPatientConditions: (conditions: string[]) => void
  setVitals: (vitals: {
    systolicBp?: number
    diastolicBp?: number
    fastingBloodSugar?: number
    postprandialBloodSugar?: number
    spo2?: number
    heartRate?: number
    serumCreatinine?: number
  }) => void
  addAllopathicImage: (url: string) => void
  removeAllopathicImage: (index: number) => void
  addAyurvedicImage: (url: string) => void
  removeAyurvedicImage: (index: number) => void
  setAllopathicMedicines: (medicines: ExtractedDrug[]) => void
  setAyurvedicMedicines: (medicines: ExtractedDrug[]) => void
  setConfirmedMedicines: (medicines: ExtractedDrug[]) => void
  setOcrFailures: (failures: OcrFailure[]) => void
  setAllImageResults: (results: ImageProcessingResult[]) => void
  updatePrescriberSource: (genericName: string, source: PrescriberSource) => void
  setInteractions: (interactions: Interaction[]) => void
  setSafetyReport: (report: SafetyReport | null) => void
  setScanMeta: (meta: ScanMeta) => void
  reset: () => void
}

const initialState = {
  language: "hi",
  step: 1,
  patientName: "",
  patientAge: 0,
  patientConditions: [] as string[],
  systolicBp: undefined as number | undefined,
  diastolicBp: undefined as number | undefined,
  fastingBloodSugar: undefined as number | undefined,
  postprandialBloodSugar: undefined as number | undefined,
  spo2: undefined as number | undefined,
  heartRate: undefined as number | undefined,
  serumCreatinine: undefined as number | undefined,
  allopathicImages: [] as string[],
  ayurvedicImages: [] as string[],
  allopathicMedicines: [] as ExtractedDrug[],
  ayurvedicMedicines: [] as ExtractedDrug[],
  confirmedMedicines: [] as ExtractedDrug[],
  ocrFailures: [] as OcrFailure[],
  allImageResults: [] as ImageProcessingResult[],
  prescriberMap: {} as Record<string, PrescriberSource>,
  interactions: [] as Interaction[],
  safetyReport: null as SafetyReport | null,
  scanMeta: null as ScanMeta | null,
}

export const useAppStore = create<AppState>()(
  persist(
    (set) => ({
      ...initialState,

      setLanguage: (lang) => set({ language: lang }),
      setStep: (step) => set({ step }),
      setPatientName: (patientName) => set({ patientName }),
      setPatientAge: (patientAge) => set({ patientAge }),
      setPatientConditions: (patientConditions) => set({ patientConditions }),
      setVitals: (vitals) => set(vitals),
      addAllopathicImage: (url) =>
        set((state) => ({ allopathicImages: [...state.allopathicImages, url] })),
      removeAllopathicImage: (index) =>
        set((state) => ({ allopathicImages: state.allopathicImages.filter((_, i) => i !== index) })),
      addAyurvedicImage: (url) =>
        set((state) => ({ ayurvedicImages: [...state.ayurvedicImages, url] })),
      removeAyurvedicImage: (index) =>
        set((state) => ({ ayurvedicImages: state.ayurvedicImages.filter((_, i) => i !== index) })),
      setAllopathicMedicines: (allopathicMedicines) => set({ allopathicMedicines }),
      setAyurvedicMedicines: (ayurvedicMedicines) => set({ ayurvedicMedicines }),
      setConfirmedMedicines: (confirmedMedicines) => set({ confirmedMedicines }),
      setOcrFailures: (ocrFailures) => set({ ocrFailures }),
      setAllImageResults: (allImageResults) => set({ allImageResults }),
      updatePrescriberSource: (genericName, source) =>
        set((state) => ({
          prescriberMap: { ...state.prescriberMap, [genericName]: source },
        })),
      setInteractions: (interactions) => set({ interactions }),
      setSafetyReport: (safetyReport) => set({ safetyReport }),
      setScanMeta: (scanMeta) => set({ scanMeta }),
      reset: () => set(initialState),
    }),
    {
      name: "sahayak-store",
      storage: createJSONStorage(() => {
        if (typeof window !== "undefined") return localStorage
        return {
          getItem: () => null,
          setItem: () => {},
          removeItem: () => {},
        }
      }),
      // Don't persist large image data URLs — they bloat localStorage
      partialize: (state) => ({
        language: state.language,
        step: state.step,
        patientName: state.patientName,
        patientAge: state.patientAge,
        patientConditions: state.patientConditions,
        systolicBp: state.systolicBp,
        diastolicBp: state.diastolicBp,
        fastingBloodSugar: state.fastingBloodSugar,
        postprandialBloodSugar: state.postprandialBloodSugar,
        spo2: state.spo2,
        heartRate: state.heartRate,
        serumCreatinine: state.serumCreatinine,
        confirmedMedicines: state.confirmedMedicines,
        prescriberMap: state.prescriberMap,
        scanMeta: state.scanMeta,
      }),
    }
  )
)
