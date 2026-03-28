"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { motion } from "motion/react"
import { ArrowRight, Info } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { StepShell } from "@/components/layout/StepShell"
import { useAppStore } from "@/hooks/useAppStore"
import { PRESCRIBER_SOURCES } from "@/lib/constants"
import { cn } from "@/lib/utils"
import type { PrescriberSource } from "@/types/sahayak"

export default function CategorizePage() {
  const router = useRouter()
  const { confirmedMedicines, prescriberMap, updatePrescriberSource } = useAppStore()
  const [mounted, setMounted] = useState(false)

  useEffect(() => { setMounted(true) }, [])

  function handleContinue() {
    router.push("/report")
  }

  if (!mounted) return null

  return (
    <StepShell step={6} backHref="/confirm">
      <div className="pt-2 pb-2">
        <h1 className="text-2xl font-bold text-foreground">Dawai kisne di?</h1>
        <p className="text-muted-foreground mt-1">Who prescribed each medicine?</p>
      </div>

      {/* Why we ask */}
      <div className="flex gap-3 p-3 rounded-2xl bg-blue-50 border border-blue-200 mb-5">
        <Info size={20} weight="fill" className="text-blue-600 flex-none mt-0.5" />
        <p className="text-blue-700 text-sm leading-relaxed">
          <span className="font-indic">Isse hum better safety advice de sakte hain. </span>
          This helps us give you better safety advice. Your data stays private.
        </p>
      </div>

      <div className="flex flex-col gap-4">
        {confirmedMedicines.map((drug, i) => {
          const selected = prescriberMap[drug.generic_name] as PrescriberSource | undefined

          return (
            <motion.div
              key={`${drug.generic_name}-${i}`}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: i * 0.07, type: "spring", stiffness: 260, damping: 25 }}
              className="rounded-2xl border-2 border-border bg-card p-4"
            >
              <p className="font-bold text-lg text-foreground mb-3">
                {drug.brand_name || drug.generic_name}
              </p>

              <div className="flex flex-col gap-2">
                {PRESCRIBER_SOURCES.map((src) => {
                  const isSelected = selected === src.value
                  return (
                    <button
                      key={src.value}
                      onClick={() => updatePrescriberSource(drug.generic_name, src.value as PrescriberSource)}
                      className={cn(
                        "flex items-center gap-3 rounded-xl border-2 px-4 min-h-[56px] transition-all text-left",
                        isSelected
                          ? "border-primary bg-primary/10 text-primary"
                          : "border-border bg-background hover:border-primary/30"
                      )}
                    >
                      <span className="text-2xl">{src.icon}</span>
                      <span className="font-medium text-base">{src.labelHi}</span>
                      <span className="text-muted-foreground text-sm ml-auto">{src.label}</span>
                    </button>
                  )
                })}
              </div>
            </motion.div>
          )
        })}
      </div>

      <div className="mt-auto pt-6">
        <Button
          onClick={handleContinue}
          className="w-full min-h-[60px] text-lg rounded-2xl font-semibold"
        >
          Safety Report Dekhein <ArrowRight size={20} className="ml-2" />
        </Button>
        <button
          onClick={handleContinue}
          className="w-full text-center text-muted-foreground text-sm mt-3 min-h-[44px]"
        >
          Skip karein (baad mein kar sakte hain)
        </button>
      </div>
    </StepShell>
  )
}
