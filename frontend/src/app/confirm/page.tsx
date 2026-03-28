"use client"

import { useState, useEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { motion, AnimatePresence } from "motion/react"
import { Plus, ArrowRight, WarningCircle, Microphone, Spinner, CheckCircle } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet"
import { StepShell } from "@/components/layout/StepShell"
import { MedicineCard } from "@/components/confirm/MedicineCard"
import { useAppStore } from "@/hooks/useAppStore"
import { toast } from "sonner"
import type { ExtractedDrug, OcrFailure } from "@/types/sahayak"

export default function ConfirmPage() {
  const router = useRouter()
  const {
    confirmedMedicines,
    setConfirmedMedicines,
    ocrFailures,
    allImageResults,
    scanMeta,
  } = useAppStore()

  const [medicines, setMedicines] = useState<ExtractedDrug[]>(confirmedMedicines)
  const [addName, setAddName] = useState("")
  const [sheetOpen, setSheetOpen] = useState(false)
  const [mounted, setMounted] = useState(false)

  // Track which failure cards have been resolved (by index)
  const [resolvedFailureIdxs, setResolvedFailureIdxs] = useState<Set<number>>(new Set())

  // Per-failure manual input + loading state
  const [failureInputs, setFailureInputs] = useState<Record<number, string>>({})
  const [failureResolving, setFailureResolving] = useState<Record<number, boolean>>({})

  // "Unexplained missing" manual entry (count-based fallback)
  const [missingInput, setMissingInput] = useState("")
  const [missingResolving, setMissingResolving] = useState(false)

  // Voice input state
  const [isRecording, setIsRecording] = useState(false)
  const [activeVoiceTarget, setActiveVoiceTarget] = useState<number | "missing" | null>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  useEffect(() => { setMounted(true) }, [])
  useEffect(() => { setMedicines(confirmedMedicines) }, [confirmedMedicines])

  function handleRemove(index: number) {
    setMedicines((prev) => prev.filter((_, i) => i !== index))
  }

  function handleUpdate(index: number, updated: ExtractedDrug) {
    setMedicines((prev) => prev.map((m, i) => (i === index ? updated : m)))
  }

  // Run entered text through the full backend pipeline (LLM extraction with fallbacks)
  async function resolveAndAddMedicine(text: string, onDone: () => void) {
    try {
      const res = await fetch("/api/extract-drug", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      })
      const data = await res.json()
      const drugs: ExtractedDrug[] = data.drugs ?? []

      if (drugs.length > 0) {
        setMedicines((prev) => [...prev, ...drugs])
        toast.success(`"${drugs[0].brand_name || drugs[0].generic_name}" identified and added`)
      } else {
        setMedicines((prev) => [...prev, {
          brand_name: text, generic_name: text,
          active_ingredients: [], dosage_form: "",
          confidence: 1.0, graph_match: false, match_type: "manual",
        }])
        toast.success(`"${text}" added manually`)
      }
    } catch {
      setMedicines((prev) => [...prev, {
        brand_name: text, generic_name: text,
        active_ingredients: [], dosage_form: "",
        confidence: 1.0, graph_match: false, match_type: "manual",
      }])
      toast.success(`"${text}" added`)
    } finally {
      onDone()
    }
  }

  function handleAddFromFailure(failIdx: number) {
    const name = failureInputs[failIdx]?.trim()
    if (!name) { toast.error("Please enter the medicine name"); return }
    setFailureResolving((prev) => ({ ...prev, [failIdx]: true }))
    resolveAndAddMedicine(name, () => {
      setFailureResolving((prev) => ({ ...prev, [failIdx]: false }))
      setResolvedFailureIdxs((prev) => new Set([...prev, failIdx]))
    })
  }

  function handleAddMissing() {
    const name = missingInput.trim()
    if (!name) { toast.error("Please enter the medicine name"); return }
    setMissingResolving(true)
    resolveAndAddMedicine(name, () => {
      setMissingResolving(false)
      setMissingInput("")
    })
  }

  async function startVoiceInput(target: number | "missing") {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" })
      mediaRecorderRef.current = mediaRecorder
      chunksRef.current = []
      setActiveVoiceTarget(target)
      setIsRecording(true)

      mediaRecorder.ondataavailable = (e) => { if (e.data.size > 0) chunksRef.current.push(e.data) }

      mediaRecorder.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop())
        setIsRecording(false)
        setActiveVoiceTarget(null)

        const audioBlob = new Blob(chunksRef.current, { type: "audio/webm" })
        if (audioBlob.size === 0) return

        try {
          toast.info("Processing voice input...")
          const formData = new FormData()
          formData.append("file", audioBlob, "voice.webm")
          formData.append("language", "auto")
          const res = await fetch("/api/speech-to-text", { method: "POST", body: formData })
          if (!res.ok) throw new Error("Speech-to-text failed")
          const sttData = await res.json()
          if (sttData.transcript) {
            if (target === "missing") {
              setMissingInput(sttData.transcript)
            } else {
              setFailureInputs((prev) => ({ ...prev, [target]: sttData.transcript }))
            }
            toast.success("Voice detected: " + sttData.transcript)
          }
        } catch {
          toast.error("Could not process voice. Please type manually.")
        }
      }

      mediaRecorder.start()
      setTimeout(() => { if (mediaRecorder.state === "recording") mediaRecorder.stop() }, 5000)
    } catch {
      toast.error("Microphone access denied. Please type the name manually.")
      setIsRecording(false)
      setActiveVoiceTarget(null)
    }
  }

  function stopVoiceInput() {
    if (mediaRecorderRef.current?.state === "recording") mediaRecorderRef.current.stop()
  }

  async function handleAddMedicine() {
    if (!addName.trim()) return
    const toastId = toast.loading("Identifying medicine...")
    try {
      const res = await fetch("/api/extract-drug", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: addName.trim() }),
      })
      const data = await res.json()
      const drugs: ExtractedDrug[] = data.drugs ?? []
      if (drugs.length > 0) {
        setMedicines((prev) => [...prev, ...drugs])
        toast.success(`"${drugs[0].brand_name || drugs[0].generic_name}" added`, { id: toastId })
      } else {
        setMedicines((prev) => [...prev, {
          brand_name: addName.trim(), generic_name: addName.trim(),
          active_ingredients: [], dosage_form: "",
          confidence: 1.0, graph_match: false, match_type: "manual",
        }])
        toast.success(`"${addName.trim()}" added`, { id: toastId })
      }
    } catch {
      setMedicines((prev) => [...prev, {
        brand_name: addName.trim(), generic_name: addName.trim(),
        active_ingredients: [], dosage_form: "",
        confidence: 1.0, graph_match: false, match_type: "manual",
      }])
      toast.success(`"${addName.trim()}" added`, { id: toastId })
    }
    setAddName("")
    setSheetOpen(false)
  }

  function handleContinue() {
    setConfirmedMedicines(medicines)
    router.push("/categorize")
  }

  if (!mounted) return null

  // ── Derive effective failures from two sources ──
  // Primary: in-memory ocrFailures (has image thumbnails, from current session)
  // Fallback: persisted scanMeta.failedImages (no thumbnails, survives page refresh)
  const effectiveFailures: (OcrFailure & { _fromMeta?: boolean })[] =
    ocrFailures.length > 0
      ? ocrFailures
      : (scanMeta?.failedImages ?? []).map((f) => ({
          imageIndex: f.imageIndex,
          type: f.type,
          reason: f.reason,
          failureType: f.failureType,
          imageDataUrl: "",  // no thumbnail in fallback mode
          _fromMeta: true,
        }))

  const pendingFailures = effectiveFailures.filter((_, i) => !resolvedFailureIdxs.has(i))

  // Count-based mismatch: images where NEITHER a medicine NOR a known failure was recorded
  const totalScanned = scanMeta?.totalScanned ?? allImageResults.length
  const knownFailures = effectiveFailures.length
  const unexplainedMissing = Math.max(0, totalScanned - medicines.length - knownFailures)

  const hasMismatch = pendingFailures.length > 0 || unexplainedMissing > 0
  const totalImages = allImageResults.length

  return (
    <StepShell step={5} backHref="/processing">
      <div className="pt-2 pb-4">
        <h1 className="text-2xl font-bold text-foreground font-indic">Dawaiyan confirm karein</h1>
        <p className="text-muted-foreground mt-1">
          {totalScanned > 0
            ? `${totalScanned} images scanned · ${medicines.length} medicines detected`
            : `${medicines.length} medicines found`
          }
        </p>
      </div>

      {/* All images strip — shows every uploaded image with its status (current session only) */}
      {totalImages > 0 && (
        <motion.div
          initial={{ opacity: 0, y: -8 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-4"
        >
          {hasMismatch && (
            <div className="flex items-start gap-2 p-3 rounded-2xl bg-amber-50 border border-amber-300 mb-3">
              <WarningCircle size={20} weight="fill" className="text-amber-600 flex-none mt-0.5" />
              <p className="text-amber-800 text-sm">
                <span className="font-semibold font-indic">
                  {pendingFailures.length + unexplainedMissing} image{pendingFailures.length + unexplainedMissing !== 1 ? "s" : ""} manual entry chahti hai
                </span>
                {" — "}
                <span>please enter the medicine name below for each flagged image</span>
              </p>
            </div>
          )}

          <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
            <span className="font-indic">Sabhi images</span> · All scanned ({totalImages})
          </p>
          <div className="flex gap-2 overflow-x-auto pb-2">
            {allImageResults.map((img, i) => {
              const isFailure = !!img.failureType
              const failIdx = isFailure
                ? effectiveFailures.findIndex((f) => f.imageIndex === img.imageIndex && f.type === img.type)
                : -1
              const isResolved = failIdx >= 0 && resolvedFailureIdxs.has(failIdx)

              return (
                <div key={i} className="flex-none flex flex-col items-center gap-1 w-16">
                  <div className={`relative w-14 h-14 rounded-xl overflow-hidden border-2 ${
                    isResolved || !isFailure ? "border-green-400" : "border-red-400"
                  }`}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img
                      src={img.imageDataUrl}
                      alt={`${img.type} #${img.imageIndex + 1}`}
                      className="w-full h-full object-cover"
                    />
                    <div className={`absolute bottom-0 right-0 w-5 h-5 rounded-tl-lg flex items-center justify-center ${
                      isResolved || !isFailure ? "bg-green-500" : "bg-red-500"
                    }`}>
                      {isResolved || !isFailure ? (
                        <CheckCircle size={12} weight="fill" className="text-white" />
                      ) : (
                        <span className="text-white font-bold text-[10px]">!</span>
                      )}
                    </div>
                  </div>
                  <p className="text-[10px] text-center leading-tight w-full truncate text-muted-foreground font-indic">
                    {isResolved
                      ? "Joda ✓"
                      : isFailure
                      ? "Likhein ↓"
                      : (img.medicine?.brand_name || img.medicine?.generic_name || "✓")
                    }
                  </p>
                </div>
              )
            })}
          </div>
        </motion.div>
      )}

      {/* Failure cards — specific images that need manual entry */}
      {pendingFailures.length > 0 && (
        <div className="flex flex-col gap-3 mb-5">
          {effectiveFailures.map((failure, failIdx) => {
            if (resolvedFailureIdxs.has(failIdx)) return null
            const inputVal = failureInputs[failIdx] || ""
            const isVoiceActive = isRecording && activeVoiceTarget === failIdx
            const isExtractionFailure = failure.failureType === "extraction"
            const isResolving = failureResolving[failIdx] ?? false
            const hasThumb = !!failure.imageDataUrl

            return (
              <motion.div
                key={`fail-${failIdx}`}
                initial={{ opacity: 0, x: -12 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: failIdx * 0.08, type: "spring", stiffness: 260, damping: 25 }}
                className={`rounded-2xl border-2 p-4 ${
                  isExtractionFailure ? "border-orange-300 bg-orange-50" : "border-amber-300 bg-amber-50"
                }`}
              >
                <div className="flex gap-3 mb-3">
                  {/* Image thumbnail or placeholder */}
                  <div className={`w-16 h-16 rounded-xl overflow-hidden border-2 flex-none ${
                    isExtractionFailure ? "border-orange-200" : "border-amber-200"
                  }`}>
                    {hasThumb ? (
                      // eslint-disable-next-line @next/next/no-img-element
                      <img src={failure.imageDataUrl} alt="Failed image" className="w-full h-full object-cover" />
                    ) : (
                      <div className="w-full h-full bg-muted/60 flex items-center justify-center">
                        <span className="text-2xl">📷</span>
                      </div>
                    )}
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap mb-0.5">
                      <p className={`font-semibold text-sm ${isExtractionFailure ? "text-orange-800" : "text-amber-800"}`}>
                        {failure.type === "allopathic" ? "Allopathic" : "Ayurvedic"} #{failure.imageIndex + 1}
                      </p>
                      <span className={`text-xs px-2 py-0.5 rounded-lg font-medium ${
                        isExtractionFailure ? "bg-orange-100 text-orange-700" : "bg-amber-100 text-amber-700"
                      }`}>
                        {isExtractionFailure ? "Text read, name unclear" : "Image unreadable"}
                      </span>
                    </div>
                    <p className={`text-xs leading-relaxed ${isExtractionFailure ? "text-orange-700" : "text-amber-700"}`}>
                      {failure.reason}
                    </p>
                  </div>
                </div>

                <p className={`text-sm font-medium mb-2 font-indic ${isExtractionFailure ? "text-orange-800" : "text-amber-800"}`}>
                  Dawa ka naam likhein ya awaaz se bolein:
                </p>
                <div className="flex gap-2">
                  <Input
                    placeholder="e.g. Dolo 650mg"
                    value={inputVal}
                    onChange={(e) => setFailureInputs((prev) => ({ ...prev, [failIdx]: e.target.value }))}
                    onKeyDown={(e) => e.key === "Enter" && handleAddFromFailure(failIdx)}
                    className="flex-1 min-h-[48px] text-sm rounded-xl bg-white"
                    disabled={isResolving}
                  />
                  <button
                    onClick={() => isVoiceActive ? stopVoiceInput() : startVoiceInput(failIdx)}
                    disabled={(isRecording && activeVoiceTarget !== failIdx) || isResolving}
                    className={`min-w-[48px] min-h-[48px] rounded-xl flex items-center justify-center transition-colors ${
                      isVoiceActive ? "bg-red-500 text-white animate-pulse" : "bg-primary/10 text-primary hover:bg-primary/20"
                    }`}
                    aria-label="Voice input"
                  >
                    {isVoiceActive
                      ? <Spinner size={20} className="animate-spin" />
                      : <Microphone size={20} weight="fill" />
                    }
                  </button>
                  <Button
                    onClick={() => handleAddFromFailure(failIdx)}
                    className="min-h-[48px] min-w-[48px] rounded-xl"
                    disabled={!inputVal.trim() || isResolving}
                  >
                    {isResolving
                      ? <Spinner size={18} className="animate-spin" />
                      : <Plus size={18} weight="bold" />
                    }
                  </Button>
                </div>
                <p className="text-xs text-muted-foreground mt-1.5">
                  <span className="font-indic">Naam likhne par dawa database se match hogi</span>
                  {" · "}Medicine will be identified automatically.
                </p>
              </motion.div>
            )
          })}
        </div>
      )}

      {/* Unexplained missing banner — count-based fallback when failure cards aren't available */}
      {unexplainedMissing > 0 && (
        <motion.div
          initial={{ opacity: 0, x: -12 }}
          animate={{ opacity: 1, x: 0 }}
          className="rounded-2xl border-2 border-amber-300 bg-amber-50 p-4 mb-5"
        >
          <div className="flex items-start gap-2 mb-3">
            <WarningCircle size={20} weight="fill" className="text-amber-600 flex-none mt-0.5" />
            <div>
              <p className="font-semibold text-amber-800 text-sm font-indic">
                {unexplainedMissing} aur dawa{unexplainedMissing !== 1 ? "yan" : ""} nahi mili
              </p>
              <p className="text-amber-700 text-xs mt-0.5">
                {unexplainedMissing} more medicine{unexplainedMissing !== 1 ? "s" : ""} could not be detected — please enter the name manually
              </p>
            </div>
          </div>
          <p className="text-sm font-medium mb-2 font-indic text-amber-800">
            Dawa ka naam likhein ya awaaz se bolein:
          </p>
          <div className="flex gap-2">
            <Input
              placeholder="e.g. Ashwagandha, Metformin..."
              value={missingInput}
              onChange={(e) => setMissingInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleAddMissing()}
              className="flex-1 min-h-[48px] text-sm rounded-xl bg-white"
              disabled={missingResolving}
            />
            <button
              onClick={() => (isRecording && activeVoiceTarget === "missing") ? stopVoiceInput() : startVoiceInput("missing")}
              disabled={(isRecording && activeVoiceTarget !== "missing") || missingResolving}
              className={`min-w-[48px] min-h-[48px] rounded-xl flex items-center justify-center transition-colors ${
                isRecording && activeVoiceTarget === "missing"
                  ? "bg-red-500 text-white animate-pulse"
                  : "bg-primary/10 text-primary hover:bg-primary/20"
              }`}
              aria-label="Voice input"
            >
              {isRecording && activeVoiceTarget === "missing"
                ? <Spinner size={20} className="animate-spin" />
                : <Microphone size={20} weight="fill" />
              }
            </button>
            <Button
              onClick={handleAddMissing}
              className="min-h-[48px] min-w-[48px] rounded-xl"
              disabled={!missingInput.trim() || missingResolving}
            >
              {missingResolving
                ? <Spinner size={18} className="animate-spin" />
                : <Plus size={18} weight="bold" />
              }
            </Button>
          </div>
          <p className="text-xs text-muted-foreground mt-1.5">
            <span className="font-indic">Naam likhne par dawa database se match hogi</span>
            {" · "}Identified from the medicine database automatically.
          </p>
        </motion.div>
      )}

      {/* Detected medicines list */}
      {medicines.length === 0 ? (
        <div className="flex flex-col items-center justify-center flex-1 py-12 text-center">
          <span className="text-5xl mb-4">💊</span>
          <p className="text-foreground font-indic text-lg font-semibold">Koi dawa nahi mili</p>
          <p className="text-muted-foreground text-base mt-1">No medicines detected. Add them manually above or using the button below.</p>
        </div>
      ) : (
        <AnimatePresence>
          <div className="flex flex-col gap-3">
            {medicines.map((drug, i) => (
              <MedicineCard
                key={`${drug.brand_name}-${i}`}
                drug={drug}
                index={i}
                onRemove={() => handleRemove(i)}
                onUpdate={(updated) => handleUpdate(i, updated)}
              />
            ))}
          </div>
        </AnimatePresence>
      )}

      {/* Add medicine manually (sheet) */}
      <Sheet open={sheetOpen} onOpenChange={setSheetOpen}>
        <SheetTrigger
          render={
            <motion.button
              whileTap={{ scale: 0.97 }}
              className="flex items-center justify-center gap-2 w-full mt-4 min-h-[56px] rounded-2xl border-2 border-dashed border-primary/40 text-primary font-medium hover:bg-primary/5 transition-colors"
            />
          }
        >
          <Plus size={22} weight="bold" />
          <span className="font-indic">Aur dawa jodein</span>
          <span className="text-primary/70">· Add medicine</span>
        </SheetTrigger>
        <SheetContent side="bottom" className="rounded-t-3xl pb-safe">
          <SheetHeader className="mb-6">
            <SheetTitle className="font-indic text-xl">Dawa ka naam likhein</SheetTitle>
          </SheetHeader>
          <div className="flex gap-3">
            <Input
              placeholder="e.g. Metformin 500mg"
              value={addName}
              onChange={(e) => setAddName(e.target.value)}
              className="flex-1 min-h-[56px] text-lg rounded-xl"
              onKeyDown={(e) => e.key === "Enter" && handleAddMedicine()}
              autoFocus
            />
            <Button onClick={handleAddMedicine} className="min-h-[56px] min-w-[56px] rounded-xl">
              <Plus size={22} weight="bold" />
            </Button>
          </div>
          <p className="text-xs text-muted-foreground mt-2 text-center">
            <span className="font-indic">Naam likhne par dawa apne aap pehchani jaayegi</span>
            {" · "}Identified from the medicine database automatically.
          </p>
        </SheetContent>
      </Sheet>

      <div className="mt-auto pt-6">
        <Button
          onClick={handleContinue}
          disabled={medicines.length === 0}
          className="w-full min-h-[60px] text-lg rounded-2xl font-semibold"
        >
          <span className="font-indic">Confirm karein</span>
          <span className="ml-1 opacity-80 text-base">· Confirm</span>
          <ArrowRight size={20} className="ml-2" />
        </Button>
        {(pendingFailures.length > 0 || unexplainedMissing > 0) && (
          <p className="text-center text-amber-600 text-xs mt-2 font-indic">
            {pendingFailures.length + unexplainedMissing} image{pendingFailures.length + unexplainedMissing !== 1 ? "s" : ""} baaki hain — aap aage badh sakte hain
          </p>
        )}
      </div>
    </StepShell>
  )
}
