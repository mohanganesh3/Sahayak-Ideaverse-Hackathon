"use client"

import { useState, useEffect } from "react"
import { useRouter } from "next/navigation"
import { motion, AnimatePresence } from "motion/react"
import { ArrowRight, CaretDown, CaretUp, User } from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { StepShell } from "@/components/layout/StepShell"
import { useAppStore } from "@/hooks/useAppStore"
import { PATIENT_CONDITIONS } from "@/lib/constants"
import { cn } from "@/lib/utils"

export default function PatientPage() {
  const router = useRouter()
  const {
    patientName,
    patientAge,
    patientConditions,
    systolicBp,
    diastolicBp,
    fastingBloodSugar,
    spo2,
    heartRate,
    serumCreatinine,
    setPatientName,
    setPatientAge,
    setPatientConditions,
    setVitals,
  } = useAppStore()

  const [name, setName] = useState(patientName)
  const [age, setAge] = useState(patientAge > 0 ? String(patientAge) : "")
  const [conditions, setConditions] = useState<string[]>(patientConditions)
  const [showVitals, setShowVitals] = useState(false)
  const [mounted, setMounted] = useState(false)

  // Vitals local state
  const [systolic, setSystolic] = useState(systolicBp ? String(systolicBp) : "")
  const [diastolic, setDiastolic] = useState(diastolicBp ? String(diastolicBp) : "")
  const [fbs, setFbs] = useState(fastingBloodSugar ? String(fastingBloodSugar) : "")
  const [spO2, setSpO2] = useState(spo2 ? String(spo2) : "")
  const [hr, setHr] = useState(heartRate ? String(heartRate) : "")
  const [creatinine, setCreatinine] = useState(serumCreatinine ? String(serumCreatinine) : "")

  useEffect(() => { setMounted(true) }, [])

  function toggleCondition(id: string) {
    setConditions((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]
    )
  }

  function handleContinue() {
    setPatientName(name.trim())
    setPatientAge(parseInt(age) || 0)
    setPatientConditions(conditions)
    setVitals({
      systolicBp: parseInt(systolic) || undefined,
      diastolicBp: parseInt(diastolic) || undefined,
      fastingBloodSugar: parseFloat(fbs) || undefined,
      spo2: parseInt(spO2) || undefined,
      heartRate: parseInt(hr) || undefined,
      serumCreatinine: parseFloat(creatinine) || undefined,
    })
    router.push("/camera")
  }

  if (!mounted) return null

  return (
    <StepShell step={2} backHref="/language">
      <div className="pt-2 pb-4">
        <div className="flex items-center gap-3 mb-1">
          <div className="w-10 h-10 rounded-2xl bg-primary/10 flex items-center justify-center">
            <User size={22} weight="duotone" className="text-primary" />
          </div>
          <h1 className="text-2xl font-bold text-foreground">Aapki jankari</h1>
        </div>
        <p className="text-muted-foreground text-sm mt-1">
          Better safety advice ke liye · For personalized results
        </p>
      </div>

      {/* Name */}
      <div className="mb-4">
        <label className="text-sm font-semibold text-foreground mb-1.5 block">
          Naam (optional) · Name
        </label>
        <Input
          placeholder="Aapka naam"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="min-h-[52px] rounded-2xl text-base"
        />
      </div>

      {/* Age */}
      <div className="mb-5">
        <label className="text-sm font-semibold text-foreground mb-1.5 block">
          Aayu · Age <span className="text-muted-foreground font-normal">(important for safety)</span>
        </label>
        <Input
          type="number"
          inputMode="numeric"
          placeholder="e.g. 65"
          value={age}
          onChange={(e) => setAge(e.target.value)}
          min={1}
          max={120}
          className="min-h-[52px] rounded-2xl text-base"
        />
      </div>

      {/* Conditions */}
      <div className="mb-5">
        <p className="text-sm font-semibold text-foreground mb-2">
          Bimariyan · Health Conditions <span className="text-muted-foreground font-normal">(optional)</span>
        </p>
        <div className="flex flex-wrap gap-2">
          {PATIENT_CONDITIONS.map((cond) => {
            const selected = conditions.includes(cond.id)
            return (
              <motion.button
                key={cond.id}
                whileTap={{ scale: 0.96 }}
                onClick={() => toggleCondition(cond.id)}
                className={cn(
                  "px-3 py-2 rounded-2xl border-2 text-sm font-medium transition-all",
                  selected
                    ? "border-primary bg-primary text-primary-foreground"
                    : "border-border bg-card text-foreground hover:border-primary/40"
                )}
              >
                <span className="font-indic">{cond.labelHi}</span>
                <span className="text-xs ml-1.5 opacity-70">{cond.label}</span>
              </motion.button>
            )
          })}
        </div>
      </div>

      {/* Optional vitals toggle */}
      <button
        onClick={() => setShowVitals((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 rounded-2xl bg-muted/60 border border-border text-sm font-medium text-foreground mb-2"
      >
        <span>
          🩺 <span className="font-indic">Vitals / Lab values</span>
          <span className="text-muted-foreground ml-1.5">(optional)</span>
        </span>
        {showVitals ? <CaretUp size={18} /> : <CaretDown size={18} />}
      </button>

      <AnimatePresence>
        {showVitals && (
          <motion.div
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            className="overflow-hidden"
          >
            <div className="rounded-2xl border border-border bg-card p-4 mb-4 flex flex-col gap-3">
              <p className="text-xs text-muted-foreground mb-1">
                These values help identify medicine risks specific to your health numbers.
              </p>

              {/* Blood Pressure */}
              <div>
                <label className="text-xs font-semibold text-foreground mb-1 block">
                  Blood Pressure (mmHg)
                </label>
                <div className="flex gap-2 items-center">
                  <Input
                    type="number"
                    inputMode="numeric"
                    placeholder="Systolic (e.g. 130)"
                    value={systolic}
                    onChange={(e) => setSystolic(e.target.value)}
                    className="flex-1 min-h-[48px] rounded-xl text-sm"
                  />
                  <span className="text-muted-foreground font-bold">/</span>
                  <Input
                    type="number"
                    inputMode="numeric"
                    placeholder="Diastolic (e.g. 80)"
                    value={diastolic}
                    onChange={(e) => setDiastolic(e.target.value)}
                    className="flex-1 min-h-[48px] rounded-xl text-sm"
                  />
                </div>
              </div>

              {/* Blood Sugar */}
              <div>
                <label className="text-xs font-semibold text-foreground mb-1 block">
                  Fasting Blood Sugar (mg/dL)
                </label>
                <Input
                  type="number"
                  inputMode="decimal"
                  placeholder="e.g. 110"
                  value={fbs}
                  onChange={(e) => setFbs(e.target.value)}
                  className="min-h-[48px] rounded-xl text-sm"
                />
              </div>

              {/* SpO2 + Heart Rate */}
              <div className="flex gap-2">
                <div className="flex-1">
                  <label className="text-xs font-semibold text-foreground mb-1 block">SpO2 (%)</label>
                  <Input
                    type="number"
                    inputMode="numeric"
                    placeholder="e.g. 98"
                    value={spO2}
                    onChange={(e) => setSpO2(e.target.value)}
                    className="min-h-[48px] rounded-xl text-sm"
                  />
                </div>
                <div className="flex-1">
                  <label className="text-xs font-semibold text-foreground mb-1 block">Heart Rate (bpm)</label>
                  <Input
                    type="number"
                    inputMode="numeric"
                    placeholder="e.g. 72"
                    value={hr}
                    onChange={(e) => setHr(e.target.value)}
                    className="min-h-[48px] rounded-xl text-sm"
                  />
                </div>
              </div>

              {/* Serum Creatinine */}
              <div>
                <label className="text-xs font-semibold text-foreground mb-1 block">
                  Serum Creatinine (mg/dL) — kidney function
                </label>
                <Input
                  type="number"
                  inputMode="decimal"
                  placeholder="e.g. 1.1"
                  value={creatinine}
                  onChange={(e) => setCreatinine(e.target.value)}
                  className="min-h-[48px] rounded-xl text-sm"
                />
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <div className="mt-auto pt-4">
        <Button
          onClick={handleContinue}
          className="w-full min-h-[60px] text-lg rounded-2xl font-semibold"
        >
          Aage chalein <ArrowRight size={20} className="ml-2" />
        </Button>
        <button
          onClick={() => {
            setPatientName("")
            setPatientAge(0)
            setPatientConditions([])
            router.push("/camera")
          }}
          className="w-full text-center text-muted-foreground text-sm mt-3 min-h-[44px]"
        >
          Skip karein
        </button>
      </div>
    </StepShell>
  )
}
