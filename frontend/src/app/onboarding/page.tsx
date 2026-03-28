"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { motion, AnimatePresence } from "motion/react"
import { Pill, ShieldCheck, Translate } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const SLIDES = [
  {
    icon: Pill,
    color: "text-primary",
    bg: "bg-primary/10",
    titleHi: "दवाई की फ़ोटो लें",
    title: "Scan Your Medicines",
    bodyHi: "बस अपनी दवाई की strip या packet का फ़ोटो लें। SAHAYAK बाकी काम कर देगा।",
    body: "Just take a photo of your medicine strip or packet. SAHAYAK does the rest.",
  },
  {
    icon: ShieldCheck,
    color: "text-green-600",
    bg: "bg-green-50",
    titleHi: "सुरक्षा जाँच",
    title: "Safety Check",
    bodyHi: "हम जाँचते हैं कि आपकी दवाइयाँ एक साथ लेने पर कोई नुकसान तो नहीं करतीं।",
    body: "We check if your medicines are safe to take together, including Ayurvedic herbs.",
  },
  {
    icon: Translate,
    color: "text-saffron",
    bg: "bg-amber-50",
    titleHi: "आपकी भाषा में",
    title: "In Your Language",
    bodyHi: "Results 10 Indian languages में मिलते हैं — सरल और समझने योग्य।",
    body: "Get results in 10 Indian languages — clear and easy to understand.",
  },
] as const

export default function OnboardingPage() {
  const router = useRouter()
  const [current, setCurrent] = useState(0)
  const [direction, setDirection] = useState(1)

  function goNext() {
    if (current < SLIDES.length - 1) {
      setDirection(1)
      setCurrent((c) => c + 1)
    } else {
      router.push("/patient")
    }
  }

  function goPrev() {
    if (current > 0) {
      setDirection(-1)
      setCurrent((c) => c - 1)
    }
  }

  const slide = SLIDES[current]
  const Icon = slide.icon
  const isLast = current === SLIDES.length - 1

  return (
    <div
      className="flex flex-col min-h-svh bg-background px-5 py-8"
      onPointerDown={(e) => {
        const startX = e.clientX
        const handleUp = (up: PointerEvent) => {
          const delta = up.clientX - startX
          if (Math.abs(delta) > 50) {
            if (delta < 0) goNext()
            else goPrev()
          }
          window.removeEventListener("pointerup", handleUp)
        }
        window.addEventListener("pointerup", handleUp)
      }}
    >
      {/* Skip button */}
      <div className="flex justify-end mb-8">
        <button
          onClick={() => router.push("/camera")}
          className="text-muted-foreground text-base px-4 py-2 rounded-xl hover:bg-muted transition-colors min-h-[44px]"
        >
          Skip
        </button>
      </div>

      {/* Slide content */}
      <div className="flex-1 flex flex-col items-center justify-center">
        <AnimatePresence mode="wait" custom={direction}>
          <motion.div
            key={current}
            custom={direction}
            initial={{ opacity: 0, x: direction * 60 }}
            animate={{ opacity: 1, x: 0, transition: { type: "spring", stiffness: 260, damping: 28 } }}
            exit={{ opacity: 0, x: -direction * 40, transition: { duration: 0.2 } }}
            className="flex flex-col items-center text-center w-full"
          >
            {/* Icon */}
            <div className={cn("w-28 h-28 rounded-3xl flex items-center justify-center mb-8", slide.bg)}>
              <Icon size={60} weight="duotone" className={slide.color} />
            </div>

            {/* Text */}
            <h1 className="text-3xl font-bold font-indic text-foreground mb-3 leading-snug">
              {slide.titleHi}
            </h1>
            <p className="text-muted-foreground text-base mb-2">{slide.title}</p>
            <p className="text-foreground text-lg leading-relaxed max-w-xs font-indic">
              {slide.bodyHi}
            </p>
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Slide indicators */}
      <div className="flex justify-center gap-2 mb-8">
        {SLIDES.map((_, i) => (
          <button
            key={i}
            onClick={() => { setDirection(i > current ? 1 : -1); setCurrent(i) }}
            className={cn(
              "rounded-full transition-all duration-300",
              i === current ? "w-6 h-3 bg-primary" : "w-3 h-3 bg-border"
            )}
            aria-label={`Slide ${i + 1}`}
          />
        ))}
      </div>

      {/* CTA */}
      <Button
        onClick={goNext}
        className="w-full min-h-[64px] text-lg rounded-2xl font-semibold"
        size="lg"
      >
        {isLast ? "📷 शुरू करें · Get Started" : "आगे → Next"}
      </Button>
    </div>
  )
}
