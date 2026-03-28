"use client"

import { useEffect } from "react"
import { useRouter } from "next/navigation"
import { motion } from "motion/react"
import { CheckCircle, Circle, Spinner, WarningCircle } from "@phosphor-icons/react"
import { useAppStore } from "@/hooks/useAppStore"
import { useProcessing } from "@/hooks/useProcessing"
import { cn } from "@/lib/utils"

export default function ProcessingPage() {
  const router = useRouter()
  const {
    allopathicImages,
    ayurvedicImages,
    setAllopathicMedicines,
    setAyurvedicMedicines,
    setInteractions,
    setOcrFailures,
    setAllImageResults,
  } = useAppStore()

  const { steps, isRunning, result, error, runPipeline } = useProcessing()

  useEffect(() => {
    runPipeline(allopathicImages, ayurvedicImages)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (result) {
      setAllopathicMedicines(result.allopathicMedicines)
      setAyurvedicMedicines(result.ayurvedicMedicines)
      setInteractions(result.interactions)
      setOcrFailures(result.ocrFailures)
      setAllImageResults(result.allImageResults)
      const allMeds = [...result.allopathicMedicines, ...result.ayurvedicMedicines]
      useAppStore.getState().setConfirmedMedicines(allMeds)
      // Persist scan metadata (no image data URLs) so confirm page survives refresh
      useAppStore.getState().setScanMeta({
        totalScanned: result.totalImages,
        detectedCount: result.totalMedicinesDetected,
        failedImages: result.ocrFailures.map((f) => ({
          imageIndex: f.imageIndex,
          type: f.type,
          reason: f.reason,
          failureType: f.failureType ?? "ocr",
        })),
      })
      setTimeout(() => router.push("/confirm"), 600)
    }
  }, [result, router, setAllopathicMedicines, setAyurvedicMedicines, setInteractions, setOcrFailures, setAllImageResults])

  const totalImages = allopathicImages.length + ayurvedicImages.length

  return (
    <div className="flex flex-col min-h-svh bg-background items-center justify-center px-5 py-12">
      {/* Pulsing pill icon */}
      <motion.div
        animate={{ scale: [1, 1.06, 1] }}
        transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
        className="w-24 h-24 rounded-3xl bg-primary/10 flex items-center justify-center mb-10"
      >
        <span className="text-5xl">💊</span>
      </motion.div>

      <h1 className="text-2xl font-bold text-foreground mb-1 font-indic">
        Jaanch ho rahi hai...
      </h1>
      <p className="text-muted-foreground text-base mb-2">Analyzing your medicines</p>
      <p className="text-muted-foreground text-sm mb-10">
        {totalImages} image{totalImages !== 1 ? "s" : ""} being processed
      </p>

      {/* Steps */}
      <div className="w-full max-w-sm flex flex-col gap-4">
        {steps.map((step, i) => (
          <motion.div
            key={step.id}
            initial={{ opacity: 0, x: -16 }}
            animate={{ opacity: 1, x: 0 }}
            transition={{ delay: i * 0.12, type: "spring", stiffness: 260, damping: 25 }}
            className={cn(
              "flex items-center gap-4 p-4 rounded-2xl border-2 transition-all duration-300",
              step.status === "active"
                ? "border-primary bg-primary/5"
                : step.status === "done"
                ? "border-green-500/40 bg-green-50"
                : step.status === "error"
                ? "border-amber-400/40 bg-amber-50"
                : "border-transparent bg-muted/50"
            )}
          >
            {/* Status icon */}
            <div className="flex-none">
              {step.status === "done" && (
                <motion.div
                  initial={{ scale: 0 }}
                  animate={{ scale: 1 }}
                  transition={{ type: "spring", stiffness: 400, damping: 20 }}
                >
                  <CheckCircle size={28} weight="fill" className="text-green-600" />
                </motion.div>
              )}
              {step.status === "active" && (
                <motion.div animate={{ rotate: 360 }} transition={{ duration: 1, repeat: Infinity, ease: "linear" }}>
                  <Spinner size={28} className="text-primary" />
                </motion.div>
              )}
              {step.status === "error" && (
                <WarningCircle size={28} weight="fill" className="text-amber-600" />
              )}
              {step.status === "pending" && (
                <Circle size={28} className="text-muted-foreground/40" />
              )}
            </div>

            {/* Labels */}
            <div className="flex-1">
              <p
                className={cn(
                  "font-semibold text-base font-indic",
                  step.status === "done"
                    ? "text-green-700"
                    : step.status === "active"
                    ? "text-primary"
                    : step.status === "error"
                    ? "text-amber-700"
                    : "text-muted-foreground"
                )}
              >
                {step.labelHi}
              </p>
              <p className="text-sm text-muted-foreground">{step.label}</p>
              {/* Show per-image failure info on verify step */}
              {step.id === "verify" && step.status === "error" && result && (
                <p className="text-xs text-amber-600 mt-1">
                  {result.ocrFailures.filter((f) => f.failureType === "extraction").length > 0
                    ? `${result.ocrFailures.length} image${result.ocrFailures.length !== 1 ? "s" : ""} need manual entry — you can enter them on the next screen`
                    : `${result.ocrFailures.length} image${result.ocrFailures.length !== 1 ? "s" : ""} could not be read — you can enter them manually`
                  }
                </p>
              )}
            </div>
          </motion.div>
        ))}
      </div>

      {/* Error state */}
      {error && (
        <div className="mt-8 p-4 rounded-2xl bg-red-50 border border-red-200 text-center max-w-sm">
          <p className="text-red-700 font-indic mb-3">
            Kuch problem aayi. Phir se try karein.
          </p>
          <p className="text-red-600 text-sm mb-4">{error}</p>
          <button
            onClick={() => router.push("/camera")}
            className="text-primary font-semibold underline"
          >
            Wapas jaayein
          </button>
        </div>
      )}

      {!isRunning && !result && !error && (
        <p className="mt-6 text-muted-foreground text-sm font-indic">
          Shuru ho raha hai...
        </p>
      )}
    </div>
  )
}
