"use client"

import { useState, useRef } from "react"
import { useRouter } from "next/navigation"
import { motion, AnimatePresence } from "motion/react"
import { Camera, Images, ArrowCounterClockwise, CheckCircle, Pill, Plant, X } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { StepShell } from "@/components/layout/StepShell"
import { useAppStore } from "@/hooks/useAppStore"
import { useCamera } from "@/hooks/useCamera"
import { cn } from "@/lib/utils"
import { toast } from "sonner"

type MedicineType = "allopathic" | "ayurvedic"

export default function CameraPage() {
  const router = useRouter()
  const {
    allopathicImages,
    ayurvedicImages,
    addAllopathicImage,
    removeAllopathicImage,
    addAyurvedicImage,
    removeAyurvedicImage,
  } = useAppStore()
  const [activeTab, setActiveTab] = useState<MedicineType>("allopathic")
  const [mode, setMode] = useState<"choose" | "camera" | "upload">("choose")
  const fileInputRef = useRef<HTMLInputElement>(null)
  const camera = useCamera()

  const currentImages = activeTab === "allopathic" ? allopathicImages : ayurvedicImages
  const alloCount = allopathicImages.length
  const ayurCount = ayurvedicImages.length

  function blobToDataUrl(blob: Blob): Promise<string> {
    return new Promise((resolve) => {
      const reader = new FileReader()
      reader.onload = () => resolve(reader.result as string)
      reader.readAsDataURL(blob)
    })
  }

  async function handleUsePhoto(blob: Blob | null, dataUrl?: string) {
    const url = dataUrl ?? (blob ? await blobToDataUrl(blob) : null)
    if (!url) return

    if (activeTab === "allopathic") {
      addAllopathicImage(url)
      toast.success(`Allopathic photo #${alloCount + 1} saved ✓`)
    } else {
      addAyurvedicImage(url)
      toast.success(`Ayurvedic photo #${ayurCount + 1} saved ✓`)
    }
    setMode("choose")
    camera.stopCamera()
  }

  async function handleFileUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const files = e.target.files
    if (!files || files.length === 0) return
    // Support selecting multiple files at once
    for (let i = 0; i < files.length; i++) {
      const url = await blobToDataUrl(files[i])
      await handleUsePhoto(files[i], url)
    }
    // Reset input so re-selecting the same file works
    e.target.value = ""
  }

  function handleRemoveImage(index: number) {
    if (activeTab === "allopathic") {
      removeAllopathicImage(index)
      toast("Allopathic photo removed")
    } else {
      removeAyurvedicImage(index)
      toast("Ayurvedic photo removed")
    }
  }

  function handleContinue() {
    if (alloCount === 0 && ayurCount === 0) {
      toast.error("Please add at least one medicine photo")
      return
    }
    router.push("/processing")
  }

  return (
    <StepShell step={3} backHref="/patient">
      <div className="pt-2 pb-4">
        <h1 className="text-2xl font-bold text-foreground">दवाई का फ़ोटो लें</h1>
        <p className="text-muted-foreground mt-1">Scan your medicine strips</p>
      </div>

      {/* Tab switcher */}
      <div className="flex gap-2 mb-5">
        {(["allopathic", "ayurvedic"] as const).map((tab) => {
          const count = tab === "allopathic" ? alloCount : ayurCount
          return (
            <button
              key={tab}
              onClick={() => { setActiveTab(tab); setMode("choose") }}
              className={cn(
                "flex-1 flex items-center justify-center gap-2 rounded-2xl min-h-[52px] border-2 font-semibold transition-all",
                activeTab === tab
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-border bg-card text-foreground hover:border-primary/30"
              )}
            >
              {tab === "allopathic"
                ? <Pill size={20} weight="duotone" />
                : <Plant size={20} weight="duotone" />}
              {tab === "allopathic" ? "Allopathic" : "Ayurvedic"}
              {count > 0 && (
                <span className={cn(
                  "inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold",
                  activeTab === tab ? "bg-white/20 text-primary-foreground" : "bg-primary/10 text-primary"
                )}>
                  {count}
                </span>
              )}
            </button>
          )
        })}
      </div>

      {/* Content area */}
      <AnimatePresence mode="wait">
        {mode === "choose" && (
          <motion.div
            key="choose"
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            className="flex flex-col gap-4"
          >
            {/* Captured images grid */}
            {currentImages.length > 0 && (
              <div className="grid grid-cols-3 gap-2">
                {currentImages.map((url, idx) => (
                  <div key={idx} className="relative rounded-xl overflow-hidden aspect-square border-2 border-border group">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={url} alt={`${activeTab} #${idx + 1}`} className="w-full h-full object-cover" />
                    <button
                      onClick={() => handleRemoveImage(idx)}
                      className="absolute top-1 right-1 w-6 h-6 rounded-full bg-red-500 text-white flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity shadow-md"
                      aria-label={`Remove photo ${idx + 1}`}
                    >
                      <X size={14} weight="bold" />
                    </button>
                    <span className="absolute bottom-1 left-1 bg-black/60 text-white text-xs px-2 py-0.5 rounded-md">
                      #{idx + 1}
                    </span>
                  </div>
                ))}
              </div>
            )}

            <p className="text-center text-muted-foreground text-base px-4">
              {activeTab === "allopathic"
                ? "Allopathic (English) dawa ki strip ka photo lein"
                : "Ayurvedic / herbal dawa ka photo lein"}
              {currentImages.length > 0 && (
                <span className="block text-sm mt-1 text-primary font-medium">
                  {currentImages.length} photo{currentImages.length !== 1 ? "s" : ""} added — add more or continue
                </span>
              )}
            </p>

            {/* Camera button */}
            <button
              onClick={() => { setMode("camera"); camera.startCamera() }}
              className="flex flex-col items-center justify-center gap-3 rounded-3xl border-2 border-dashed border-primary/40 bg-primary/5 min-h-[140px] hover:bg-primary/10 transition-colors w-full"
            >
              <Camera size={48} weight="duotone" className="text-primary" />
              <span className="text-lg font-semibold text-primary">📷 Camera se lein</span>
              <span className="text-sm text-muted-foreground">Take a new photo</span>
            </button>

            {/* Upload button */}
            <button
              onClick={() => fileInputRef.current?.click()}
              className="flex items-center justify-center gap-3 rounded-2xl border-2 border-border bg-card min-h-[60px] hover:bg-muted transition-colors w-full"
            >
              <Images size={28} weight="duotone" className="text-muted-foreground" />
              <span className="font-medium text-foreground">Gallery se chunein</span>
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              className="hidden"
              onChange={handleFileUpload}
            />
          </motion.div>
        )}

        {mode === "camera" && (
          <motion.div
            key="camera"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="flex flex-col gap-4"
          >
            {camera.state === "requesting" && (
              <div className="flex items-center justify-center min-h-[240px] rounded-2xl bg-muted">
                <p className="text-muted-foreground font-indic">Camera khul raha hai...</p>
              </div>
            )}

            {camera.state === "error" && (
              <div className="flex flex-col items-center gap-3 min-h-[240px] rounded-2xl bg-red-50 p-6">
                <p className="text-red-700 text-center font-indic">
                  Camera nahi khula. Gallery se photo chunein.
                </p>
                <Button variant="outline" onClick={() => setMode("choose")}>
                  Wapas jaayein
                </Button>
              </div>
            )}

            {(camera.state === "active" || camera.state === "captured") && (
              <>
                {camera.state === "active" && (
                  <div className="relative rounded-2xl overflow-hidden bg-black aspect-[4/3]">
                    <video
                      ref={camera.videoRef}
                      autoPlay
                      playsInline
                      muted
                      className="w-full h-full object-cover"
                    />
                    {/* Frame guide */}
                    <div className="absolute inset-6 rounded-xl border-2 border-dashed border-amber-400/80 pointer-events-none" />
                    <p className="absolute bottom-3 left-0 right-0 text-center text-white text-sm font-medium bg-black/40 py-1">
                      Strip ko frame के अंदर रखें
                    </p>
                  </div>
                )}

                {camera.state === "captured" && camera.capturedUrl && (
                  <div className="rounded-2xl overflow-hidden aspect-[4/3] bg-black">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={camera.capturedUrl} alt="Captured" className="w-full h-full object-cover" />
                  </div>
                )}

                <canvas ref={camera.canvasRef} className="hidden" />

                <div className="flex gap-3">
                  {camera.state === "active" ? (
                    <>
                      <Button variant="outline" className="flex-1 min-h-[56px]" onClick={() => { camera.stopCamera(); setMode("choose") }}>
                        रद्द करें
                      </Button>
                      {/* Giant shutter button */}
                      <button
                        onClick={camera.capture}
                        className="flex-none w-[72px] h-[72px] rounded-full bg-white border-4 border-primary flex items-center justify-center shadow-lg active:scale-95 transition-transform"
                        aria-label="Capture photo"
                      >
                        <div className="w-14 h-14 rounded-full bg-primary" />
                      </button>
                      <div className="flex-1" />
                    </>
                  ) : (
                    <>
                      <Button
                        variant="outline"
                        className="flex-1 min-h-[56px]"
                        onClick={camera.retake}
                      >
                        <ArrowCounterClockwise size={20} className="mr-2" />
                        Dobara lein
                      </Button>
                      <Button
                        className="flex-1 min-h-[56px]"
                        onClick={() => camera.capturedBlob && handleUsePhoto(camera.capturedBlob)}
                      >
                        <CheckCircle size={20} className="mr-2" />
                        Use karein ✓
                      </Button>
                    </>
                  )}
                </div>
              </>
            )}
          </motion.div>
        )}
      </AnimatePresence>

      {/* Continue button */}
      <div className="mt-auto pt-6">
        <Button
          onClick={handleContinue}
          disabled={alloCount === 0 && ayurCount === 0}
          className="w-full min-h-[60px] text-lg rounded-2xl font-semibold"
        >
          Safety jaanch karein →
        </Button>
        {alloCount === 0 && ayurCount === 0 ? (
          <p className="text-center text-muted-foreground text-sm mt-2">
            Kam se kam ek dawa ka photo zaroori hai
          </p>
        ) : (
          <p className="text-center text-muted-foreground text-sm mt-2">
            {alloCount + ayurCount} photo{alloCount + ayurCount !== 1 ? "s" : ""} ready
            {alloCount > 0 && ` · ${alloCount} allopathic`}
            {ayurCount > 0 && ` · ${ayurCount} ayurvedic`}
          </p>
        )}
      </div>
    </StepShell>
  )
}
