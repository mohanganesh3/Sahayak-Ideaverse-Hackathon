"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { motion } from "motion/react"
import { CheckCircle } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { StepShell } from "@/components/layout/StepShell"
import { useAppStore } from "@/hooks/useAppStore"
import { SUPPORTED_LANGUAGES } from "@/lib/constants"
import { cn } from "@/lib/utils"

export default function LanguagePage() {
  const router = useRouter()
  const { language, setLanguage } = useAppStore()
  const [selected, setSelected] = useState(language)
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    setMounted(true)
    setSelected(language)
  }, [language])

  function handleContinue() {
    setLanguage(selected)
    router.push("/onboarding")
  }

  if (!mounted) return null

  return (
    <StepShell step={1} showBack={false}>
      <div className="pt-4 pb-6">
        <h1 className="text-3xl font-bold text-foreground">
          भाषा चुनें
        </h1>
        <p className="text-muted-foreground mt-1 text-lg">
          Choose your language
        </p>
      </div>

      <motion.div
        className="grid grid-cols-2 gap-3"
        initial="hidden"
        animate="visible"
        variants={{ visible: { transition: { staggerChildren: 0.06 } } }}
      >
        {SUPPORTED_LANGUAGES.map((lang) => {
          const isSelected = selected === lang.code
          return (
            <motion.button
              key={lang.code}
              variants={{
                hidden: { opacity: 0, y: 16 },
                visible: { opacity: 1, y: 0, transition: { type: "spring", stiffness: 260, damping: 25 } },
              }}
              onClick={() => setSelected(lang.code)}
              className={cn(
                "relative flex flex-col items-center justify-center rounded-2xl border-2 p-4",
                "min-h-[72px] w-full transition-all duration-200",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                isSelected
                  ? "border-primary bg-primary text-primary-foreground shadow-md shadow-primary/20"
                  : "border-border bg-card text-foreground hover:border-primary/40 hover:bg-muted"
              )}
              aria-pressed={isSelected}
            >
              {isSelected && (
                <CheckCircle
                  size={18}
                  weight="fill"
                  className="absolute top-2 right-2 text-primary-foreground/80"
                />
              )}
              <span
                className={cn(
                  "text-xl font-bold leading-tight",
                  lang.fontClass,
                  isSelected ? "text-primary-foreground" : "text-foreground"
                )}
              >
                {lang.nativeLabel}
              </span>
              {lang.code !== "en" && (
                <span
                  className={cn(
                    "text-xs mt-0.5",
                    isSelected ? "text-primary-foreground/70" : "text-muted-foreground"
                  )}
                >
                  {lang.label}
                </span>
              )}
            </motion.button>
          )
        })}
      </motion.div>

      <div className="mt-auto pt-8">
        <Button
          onClick={handleContinue}
          className="w-full min-h-[60px] text-lg rounded-2xl font-semibold"
          size="lg"
        >
          जारी रखें · Continue
        </Button>
      </div>
    </StepShell>
  )
}
