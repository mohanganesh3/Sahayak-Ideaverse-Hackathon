"use client"

import { useState, useCallback } from "react"
import type { ProcessingStep, ProcessingStepStatus } from "@/types/sahayak"
import type { ExtractedDrug, Interaction, OcrFailure, ImageProcessingResult } from "@/types/sahayak"

const INITIAL_STEPS: ProcessingStep[] = [
  { id: "ocr",      label: "Reading medicine strips...",  labelHi: "Dawa padh rahe hain...",         status: "pending" },
  { id: "extract",  label: "Identifying medicines...",    labelHi: "Dawai pahchaan rahe hain...",    status: "pending" },
  { id: "verify",   label: "Verifying detection count...",labelHi: "Ginti mil rahi hai jaanch rahe hain...", status: "pending" },
  { id: "graph",    label: "Matching to database...",     labelHi: "Database se milaa rahe hain...", status: "pending" },
  { id: "interact", label: "Checking interactions...",    labelHi: "Suraksha jaanch rahe hain...",   status: "pending" },
  { id: "report",   label: "Generating report...",        labelHi: "Report taiyaar kar rahe hain...",  status: "pending" },
]

interface OcrSingleResult {
  text: string
  confidence: number
  language: string
  needs_fallback: boolean
  medicine_type: string
  imageIndex: number
  imageDataUrl: string
}

interface ProcessingResult {
  allopathicMedicines: ExtractedDrug[]
  ayurvedicMedicines: ExtractedDrug[]
  interactions: Interaction[]
  ocrFailures: OcrFailure[]
  allImageResults: ImageProcessingResult[]
  totalImages: number
  totalMedicinesDetected: number
}

export function useProcessing() {
  const [steps, setSteps] = useState<ProcessingStep[]>(INITIAL_STEPS)
  const [isRunning, setIsRunning] = useState(false)
  const [result, setResult] = useState<ProcessingResult | null>(null)
  const [error, setError] = useState<string | null>(null)

  function updateStep(id: string, status: ProcessingStepStatus) {
    setSteps((prev) =>
      prev.map((s) => (s.id === id ? { ...s, status } : s))
    )
  }

  const runPipeline = useCallback(
    async (allopathicImages: string[], ayurvedicImages: string[]) => {
      setIsRunning(true)
      setError(null)
      setSteps(INITIAL_STEPS)

      try {
        // ── Step 1: OCR — send each image individually (parallel) ──
        updateStep("ocr", "active")

        const ocrPromises: Promise<OcrSingleResult>[] = []

        allopathicImages.forEach((dataUrl, idx) => {
          ocrPromises.push(
            (async () => {
              const formData = new FormData()
              const blob = await (await fetch(dataUrl)).blob()
              formData.append("file", blob, `allopathic_${idx}.jpg`)
              formData.append("type", "allopathic")
              const res = await fetch("/api/ocr", { method: "POST", body: formData })
              const data = await res.json()
              return { ...data, medicine_type: "allopathic", imageIndex: idx, imageDataUrl: dataUrl } as OcrSingleResult
            })()
          )
        })

        ayurvedicImages.forEach((dataUrl, idx) => {
          ocrPromises.push(
            (async () => {
              const formData = new FormData()
              const blob = await (await fetch(dataUrl)).blob()
              formData.append("file", blob, `ayurvedic_${idx}.jpg`)
              formData.append("type", "ayurvedic")
              const res = await fetch("/api/ocr", { method: "POST", body: formData })
              const data = await res.json()
              return { ...data, medicine_type: "ayurvedic", imageIndex: idx, imageDataUrl: dataUrl } as OcrSingleResult
            })()
          )
        })

        const allOcrResults = await Promise.all(ocrPromises)
        updateStep("ocr", "done")

        // ── Step 2: Per-image extraction — each OCR text extracted individually ──
        // This gives exact image→medicine mapping (no heuristics)
        updateStep("extract", "active")

        const successfulOcr: OcrSingleResult[] = []
        const ocrFailures: OcrFailure[] = []

        for (const ocr of allOcrResults) {
          const hasText = ocr.text && ocr.text.trim().length > 0
          const hasGoodConfidence = ocr.confidence >= 0.3
          if (hasText && hasGoodConfidence) {
            successfulOcr.push(ocr)
          } else {
            ocrFailures.push({
              imageIndex: ocr.imageIndex,
              type: ocr.medicine_type as "allopathic" | "ayurvedic",
              reason: !hasText
                ? "No readable text found. The image may be too blurry, dark, or not a medicine label."
                : `Very low confidence (${Math.round(ocr.confidence * 100)}%) — text could not be reliably read.`,
              imageDataUrl: ocr.imageDataUrl,
              failureType: "ocr",
            })
          }
        }

        // Extract medicines per-image in parallel via /api/extract-drug
        const perImageResults = await Promise.all(
          successfulOcr.map(async (ocr) => {
            try {
              const res = await fetch("/api/extract-drug", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text: ocr.text }),
              })
              const data = await res.json()
              return { ocr, drugs: (data.drugs ?? []) as ExtractedDrug[] }
            } catch {
              return { ocr, drugs: [] as ExtractedDrug[] }
            }
          })
        )

        updateStep("extract", "done")

        // ── Step 3: Verify — build allImageResults with exact failure info ──
        updateStep("verify", "active")

        const alloMeds: ExtractedDrug[] = []
        const ayurMeds: ExtractedDrug[] = []
        const extractionFailures: OcrFailure[] = []
        const allImageResults: ImageProcessingResult[] = []

        // Add OCR failure images first
        for (const f of ocrFailures) {
          allImageResults.push({
            imageIndex: f.imageIndex,
            type: f.type,
            imageDataUrl: f.imageDataUrl,
            failureType: "ocr",
            failureReason: f.reason,
          })
        }

        // Process per-image extraction results
        for (const { ocr, drugs } of perImageResults) {
          if (drugs.length === 0) {
            // Extraction failure — we know EXACTLY which image failed
            const reason = "Text was read from this image but no medicine name could be identified. The label may be partial, in an unusual format, or the text may be ambiguous."
            extractionFailures.push({
              imageIndex: ocr.imageIndex,
              type: ocr.medicine_type as "allopathic" | "ayurvedic",
              reason,
              imageDataUrl: ocr.imageDataUrl,
              failureType: "extraction",
            })
            allImageResults.push({
              imageIndex: ocr.imageIndex,
              type: ocr.medicine_type as "allopathic" | "ayurvedic",
              imageDataUrl: ocr.imageDataUrl,
              failureType: "extraction",
              failureReason: reason,
            })
          } else {
            // Success — add all drugs from this image
            for (const drug of drugs) {
              if (ocr.medicine_type === "allopathic") alloMeds.push(drug)
              else ayurMeds.push(drug)
            }
            allImageResults.push({
              imageIndex: ocr.imageIndex,
              type: ocr.medicine_type as "allopathic" | "ayurvedic",
              imageDataUrl: ocr.imageDataUrl,
              medicine: drugs[0],  // show primary medicine for this image
            })
          }
        }

        // Sort allImageResults: allopathic first, then ayurvedic, each ordered by imageIndex
        allImageResults.sort((a, b) => {
          if (a.type !== b.type) return a.type === "allopathic" ? -1 : 1
          return a.imageIndex - b.imageIndex
        })

        const allFailures = [...ocrFailures, ...extractionFailures]
        updateStep("verify", allFailures.length > 0 ? "error" : "done")

        // ── Step 4: Batch analyze for interactions (uses all successful OCR texts) ──
        // If per-image extraction produced no medicines at all (API down), fall back to batch medicines too
        updateStep("graph", "active")

        const ocrForAnalysis = successfulOcr.map((r) => ({
          text: r.text,
          confidence: r.confidence,
          language: r.language,
          needs_fallback: r.needs_fallback,
          medicine_type: r.medicine_type,
        }))

        let interactions: Interaction[] = []
        let batchAlloMeds: ExtractedDrug[] = []
        let batchAyurMeds: ExtractedDrug[] = []

        if (ocrForAnalysis.length > 0) {
          const analyzeRes = await fetch("/api/analyze", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ ocr_results: ocrForAnalysis }),
          })
          const analyzeData = await analyzeRes.json()
          interactions = analyzeData.interactions ?? []
          batchAlloMeds = analyzeData.allopathic_medicines ?? []
          batchAyurMeds = analyzeData.ayurvedic_medicines ?? []
        }

        updateStep("graph", "done")
        updateStep("interact", "active")

        // Use per-image medicines (accurate); fall back to batch if per-image found nothing
        const finalAlloMeds = alloMeds.length > 0 ? alloMeds : batchAlloMeds
        const finalAyurMeds = ayurMeds.length > 0 ? ayurMeds : batchAyurMeds

        await new Promise((r) => setTimeout(r, 400))
        updateStep("interact", "done")
        updateStep("report", "active")
        await new Promise((r) => setTimeout(r, 300))
        updateStep("report", "done")

        const totalImages = allopathicImages.length + ayurvedicImages.length

        setResult({
          allopathicMedicines: finalAlloMeds,
          ayurvedicMedicines: finalAyurMeds,
          interactions,
          ocrFailures: allFailures,
          allImageResults,
          totalImages,
          totalMedicinesDetected: finalAlloMeds.length + finalAyurMeds.length,
        })
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Pipeline failed"
        setError(msg)
        setSteps((prev) =>
          prev.map((s) => (s.status === "active" ? { ...s, status: "error" } : s))
        )
      } finally {
        setIsRunning(false)
      }
    },
    []
  )

  return { steps, isRunning, result, error, runPipeline }
}
