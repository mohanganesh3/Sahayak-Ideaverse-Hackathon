"use client"

import { useEffect, useState, useRef } from "react"
import { useRouter } from "next/navigation"
import { motion } from "motion/react"
import {
  CheckCircle,
  WarningCircle,
  XCircle,
  ArrowCounterClockwise,
  ShareNetwork,
  Download,
} from "@phosphor-icons/react"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { StepShell } from "@/components/layout/StepShell"
import { FindingCard } from "@/components/report/FindingCard"
import { CriticalAlertModal } from "@/components/report/CriticalAlertModal"
import { useAppStore } from "@/hooks/useAppStore"
import { SEVERITY_CONFIG } from "@/lib/constants"
import { toast } from "sonner"
import type { Interaction, ReportContent, SafetyReport } from "@/types/sahayak"

function mergeDisplayFinding(finding: Interaction, translatedFinding?: Interaction): Interaction {
  if (!translatedFinding) return finding
  return {
    ...finding,
    title: translatedFinding.title || finding.title,
    patient_explanation: translatedFinding.patient_explanation || finding.patient_explanation,
    doctor_explanation: translatedFinding.doctor_explanation || finding.doctor_explanation,
    action: translatedFinding.action || finding.action,
  }
}

function mergeDisplayContent(content?: ReportContent | null, translated?: ReportContent | null): ReportContent | null {
  const base = content ?? translated
  if (!base) return null
  return {
    ...base,
    patient_summary: translated?.patient_summary || base.patient_summary,
    self_prescribed_warning: translated?.self_prescribed_warning ?? base.self_prescribed_warning,
    personalized_advice: translated?.personalized_advice ?? base.personalized_advice,
    disclaimer: translated?.disclaimer || base.disclaimer,
    acb_section: {
      ...base.acb_section,
      risk: translated?.acb_section?.risk || base.acb_section.risk,
    },
    findings: (base.findings ?? []).map((finding, index) =>
      mergeDisplayFinding(finding, translated?.findings?.[index]),
    ),
  }
}

export default function ReportPage() {
  const router = useRouter()
  const {
    confirmedMedicines,
    prescriberMap,
    interactions,
    safetyReport,
    setSafetyReport,
    language,
    patientName,
    patientAge,
    patientConditions,
    systolicBp,
    diastolicBp,
    fastingBloodSugar,
    postprandialBloodSugar,
    spo2,
    heartRate,
    serumCreatinine,
  } = useAppStore()

  const [loading, setLoading] = useState(false)
  const [mounted, setMounted] = useState(false)
  const reportBlobRef = useRef<string | null>(null)

  useEffect(() => { setMounted(true) }, [])

  useEffect(() => {
    if (!mounted) return
    if (safetyReport) return

    async function fetchReport() {
      setLoading(true)
      try {
        const res = await fetch("/api/report", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            medicines: confirmedMedicines,
            prescriber_map: prescriberMap,
            interactions,
            language,
            age: patientAge || undefined,
            conditions: patientConditions.length > 0 ? patientConditions : undefined,
            systolic_bp: systolicBp || undefined,
            diastolic_bp: diastolicBp || undefined,
            fasting_blood_sugar: fastingBloodSugar || undefined,
            postprandial_blood_sugar: postprandialBloodSugar || undefined,
            spo2: spo2 || undefined,
            heart_rate: heartRate || undefined,
            serum_creatinine: serumCreatinine || undefined,
          }),
        })
        const data: SafetyReport = await res.json()
        setSafetyReport(data)
      } catch {
        toast.error("Report generate nahi ho saki. Interactions neeche dikhaye hain.")
      } finally {
        setLoading(false)
      }
    }

    fetchReport()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mounted, safetyReport])

  const canonicalContent = safetyReport?.english ?? safetyReport?.translated ?? null
  const translatedContent = safetyReport?.translated ?? null
  const content = mergeDisplayContent(canonicalContent, translatedContent)
  const findings = content?.findings ?? canonicalContent?.findings ?? interactions
  const criticalFindings = findings.filter((f) => f.severity === "critical")
  const sortedFindings = [...findings].sort((a, b) => {
    const order = { critical: 0, major: 1, moderate: 2, minor: 3, unknown: 4 }
    return (order[a.severity] ?? 4) - (order[b.severity] ?? 4)
  })

  const criticalCount = findings.filter((f) => f.severity === "critical").length
  const majorCount = findings.filter((f) => f.severity === "major").length
  const safeCount = findings.filter((f) => f.severity === "minor" || f.severity === "unknown").length

  function handleShare() {
    const text = [
      "SAHAYAK Safety Report",
      `Medicines checked: ${confirmedMedicines.length}`,
      criticalCount > 0 ? `⛔ Critical: ${criticalCount}` : "",
      majorCount > 0 ? `⚠️ Major: ${majorCount}` : "",
      "",
      ...(findings.slice(0, 3).map((f) => [
        `${SEVERITY_CONFIG[f.severity]?.icon ?? "•"} ${f.title}`,
        f.source ? `Source: ${f.source}` : "",
        f.evidence_profile ? `Evidence profile: ${f.evidence_profile}` : "",
        ...(f.citations ?? []).flatMap((citation) => [
          `- ${citation.source_label}${citation.evidence_scope_label ? ` (${citation.evidence_scope_label})` : ""}`,
          citation.source_url ? `  ${citation.source_url}` : "",
          citation.reference_url ? `  ${citation.reference_url}` : "",
        ]),
      ].filter(Boolean).join("\n"))),
    ]
      .filter(Boolean)
      .join("\n")

    if (navigator.share) {
      navigator.share({ title: "SAHAYAK Report", text }).catch(() => {})
    } else {
      window.open(`https://wa.me/?text=${encodeURIComponent(text)}`)
    }
  }

  function handleDownload() {
    const personalizedSection: string[] = []
    const hasProfile = patientAge > 0 || patientConditions.length > 0 || systolicBp || fastingBloodSugar || spo2 || serumCreatinine
    if (hasProfile) {
      personalizedSection.push("PATIENT PROFILE:")
      personalizedSection.push("-".repeat(20))
      if (patientName) personalizedSection.push(`Name: ${patientName}`)
      if (patientAge > 0) personalizedSection.push(`Age: ${patientAge} years`)
      if (patientConditions.length > 0) personalizedSection.push(`Conditions: ${patientConditions.join(", ")}`)
      if (systolicBp) personalizedSection.push(`Blood Pressure: ${systolicBp}/${diastolicBp ?? "?"} mmHg`)
      if (fastingBloodSugar) personalizedSection.push(`Fasting Blood Sugar: ${fastingBloodSugar} mg/dL`)
      if (spo2) personalizedSection.push(`SpO2: ${spo2}%`)
      if (heartRate) personalizedSection.push(`Heart Rate: ${heartRate} bpm`)
      if (serumCreatinine) personalizedSection.push(`Serum Creatinine: ${serumCreatinine} mg/dL`)
      personalizedSection.push("")
    }

    // Prescriber info section
    const prescriberEntries = Object.entries(prescriberMap)
    const prescriberSection: string[] = []
    if (prescriberEntries.length > 0) {
      prescriberSection.push("PRESCRIBER INFO:")
      prescriberSection.push("-".repeat(20))
      for (const [med, source] of prescriberEntries) {
        const sourceLabel = source === "doctor" ? "Doctor Prescribed" : source === "self" ? "Self-Started" : "From Pharmacy"
        prescriberSection.push(`${med}: ${sourceLabel}`)
      }
      prescriberSection.push("")
    }

    const adviceSection: string[] = []
    const personalizedAdvice = content?.personalized_advice ?? undefined
    if (personalizedAdvice) {
      adviceSection.push("PERSONALIZED ADVICE:")
      adviceSection.push("-".repeat(20))
      adviceSection.push(personalizedAdvice)
      adviceSection.push("")
    }

    const text = [
      "SAHAYAK Medication Safety Report",
      "=".repeat(40),
      `Date: ${new Date().toLocaleDateString("en-IN")}`,
      `Medicines: ${confirmedMedicines.map((m) => m.brand_name || m.generic_name).join(", ")}`,
      "",
      ...personalizedSection,
      content?.patient_summary ?? "",
      "",
      ...prescriberSection,
      "FINDINGS:",
      "-".repeat(20),
      ...sortedFindings.map((f) => {
        const citationLines = (f.citations ?? []).flatMap((citation) => [
          `  - ${citation.source_label}${citation.evidence_scope_label ? ` (${citation.evidence_scope_label})` : ""}`,
          citation.source_url ? `    Source link: ${citation.source_url}` : "",
          citation.backing_source_url && citation.backing_source_url !== citation.source_url
            ? `    Backing link: ${citation.backing_source_url}`
            : "",
          citation.reference_url ? `    Reference link: ${citation.reference_url}` : "",
        ]).filter(Boolean)

        return [
          `[${f.severity.toUpperCase()}] ${f.title}`,
          f.patient_explanation,
          `Action: ${f.action}`,
          `Source: ${f.source}`,
          f.evidence_profile ? `Evidence profile: ${f.evidence_profile}` : "",
          citationLines.length > 0 ? "Evidence details:" : "",
          ...citationLines,
          "",
        ].filter(Boolean).join("\n")
      }),
      "",
      ...adviceSection,
      content?.disclaimer ?? "Consult your doctor before making any changes to your medication.",
    ].join("\n")

    if (reportBlobRef.current) URL.revokeObjectURL(reportBlobRef.current)
    const blob = new Blob([text], { type: "text/plain" })
    const url = URL.createObjectURL(blob)
    reportBlobRef.current = url
    const a = document.createElement("a")
    a.href = url
    a.download = "sahayak_safety_report.txt"
    a.click()
  }

  if (!mounted) return null

  return (
    <StepShell step={7} backHref="/categorize">
      {/* Critical alert modal */}
      <CriticalAlertModal criticalFindings={criticalFindings} />

      <div className="pt-2 pb-4">
        <h1 className="text-2xl font-bold text-foreground">Safety Report</h1>
        <p className="text-muted-foreground mt-1">
          {confirmedMedicines.length} dawaiyan jaanchi gayi
        </p>
      </div>

      {/* Patient profile summary — shown only if we have patient data */}
      {(patientAge > 0 || patientConditions.length > 0 || systolicBp || fastingBloodSugar) && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          className="rounded-2xl p-4 mb-4 bg-blue-50 border-2 border-blue-200"
        >
          <p className="font-semibold text-blue-800 text-sm mb-2">
            👤 {patientName ? patientName + " ka" : "Aapka"} Profile — Report personalized hai
          </p>
          <div className="flex flex-wrap gap-2">
            {patientAge > 0 && (
              <span className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-lg font-medium">
                Age: {patientAge} yrs
              </span>
            )}
            {patientConditions.map((c) => (
              <span key={c} className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-lg">
                {c.replace(/_/g, " ")}
              </span>
            ))}
            {systolicBp && (
              <span className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-lg">
                BP: {systolicBp}/{diastolicBp}
              </span>
            )}
            {fastingBloodSugar && (
              <span className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-lg">
                FBS: {fastingBloodSugar} mg/dL
              </span>
            )}
            {spo2 && (
              <span className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-lg">
                SpO2: {spo2}%
              </span>
            )}
            {serumCreatinine && (
              <span className="text-xs bg-blue-100 text-blue-700 px-2 py-1 rounded-lg">
                Creatinine: {serumCreatinine}
              </span>
            )}
          </div>
        </motion.div>
      )}

      {loading && (
        <div className="flex flex-col items-center py-16 gap-4">
          <motion.div
            animate={{ rotate: 360 }}
            transition={{ duration: 1, repeat: Infinity, ease: "linear" }}
            className="w-12 h-12 border-4 border-primary border-t-transparent rounded-full"
          />
          <p className="text-muted-foreground font-indic">Report ban rahi hai...</p>
        </div>
      )}

      {!loading && (
        <>
          {/* Summary banner */}
          <motion.div
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            className="rounded-2xl p-4 mb-5 flex items-center gap-4 bg-card border-2 border-border"
          >
            <div className="flex gap-3 flex-wrap">
              {criticalCount > 0 && (
                <div className="flex items-center gap-1.5">
                  <XCircle size={22} weight="fill" className="text-red-600" />
                  <span className="font-bold text-red-700">{criticalCount} Critical</span>
                </div>
              )}
              {majorCount > 0 && (
                <div className="flex items-center gap-1.5">
                  <WarningCircle size={22} weight="fill" className="text-orange-600" />
                  <span className="font-bold text-orange-700">{majorCount} Major</span>
                </div>
              )}
              {safeCount > 0 && (
                <div className="flex items-center gap-1.5">
                  <CheckCircle size={22} weight="fill" className="text-green-600" />
                  <span className="font-bold text-green-700">{safeCount} Minor/Safe</span>
                </div>
              )}
              {findings.length === 0 && (
                <div className="flex items-center gap-1.5">
                  <CheckCircle size={22} weight="fill" className="text-green-600" />
                  <span className="font-bold text-green-700">No interactions found</span>
                </div>
              )}
            </div>
          </motion.div>

          {/* Patient summary */}
          {content?.patient_summary && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, transition: { delay: 0.2 } }}
              className="rounded-2xl p-4 mb-4 bg-muted/50 border border-border"
            >
              <p className="text-base text-foreground leading-relaxed font-indic">
                {content.patient_summary}
              </p>
            </motion.div>
          )}

          {/* Personalized advice section */}
          {content?.personalized_advice && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, transition: { delay: 0.25 } }}
              className="rounded-2xl p-4 mb-4 bg-indigo-50 border-2 border-indigo-300"
            >
              <p className="font-semibold text-indigo-800 text-sm mb-2">🩺 Personalized Advice for You</p>
              <p className="text-indigo-700 text-base leading-relaxed font-indic">
                {content.personalized_advice}
              </p>
            </motion.div>
          )}

          {/* Prescriber info summary */}
          {Object.keys(prescriberMap).length > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, transition: { delay: 0.28 } }}
              className="rounded-2xl p-4 mb-4 bg-slate-50 border border-slate-200"
            >
              <p className="font-semibold text-slate-700 text-sm mb-2">💊 Prescriber Summary</p>
              <div className="flex flex-col gap-1">
                {Object.entries(prescriberMap).map(([med, source]) => (
                  <div key={med} className="flex items-center justify-between text-sm">
                    <span className="text-foreground font-medium">{med}</span>
                    <span className={`text-xs px-2 py-0.5 rounded-lg ${
                      source === "doctor"
                        ? "bg-green-100 text-green-700"
                        : source === "self"
                        ? "bg-red-100 text-red-700"
                        : "bg-amber-100 text-amber-700"
                    }`}>
                      {source === "doctor" ? "👨‍⚕️ Doctor" : source === "self" ? "🙋 Self" : "🏪 Pharmacy"}
                    </span>
                  </div>
                ))}
              </div>
            </motion.div>
          )}

          {/* Self-prescribed warning */}
          {content?.self_prescribed_warning && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, transition: { delay: 0.3 } }}
              className="rounded-2xl p-4 mb-4 bg-amber-50 border-2 border-amber-400"
            >
              <p className="text-amber-800 font-medium text-base">
                ⚠️ {content.self_prescribed_warning}
              </p>
            </motion.div>
          )}

          {/* Findings */}
          {sortedFindings.length > 0 ? (
            <div className="flex flex-col gap-3 mb-5">
              {sortedFindings.map((finding, i) => (
                <FindingCard key={i} finding={finding} index={i} />
              ))}
            </div>
          ) : (
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="flex flex-col items-center py-10 text-center"
            >
              <CheckCircle size={64} weight="fill" className="text-green-500 mb-3" />
              <p className="text-xl font-bold text-green-700 font-indic">
                Koi harmful interaction nahi mila! 🎉
              </p>
              <p className="text-muted-foreground mt-1">No concerning interactions found.</p>
            </motion.div>
          )}

          {/* ACB Section */}
          {content?.acb_section && content.acb_section.score > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, transition: { delay: 0.5 } }}
              className="rounded-2xl p-4 mb-5 border-2 border-purple-300 bg-purple-50"
            >
              <div className="flex items-center gap-2 mb-2">
                <Badge className="bg-purple-100 text-purple-700 rounded-lg">
                  ACB Score: {content.acb_section.score}
                </Badge>
                <span className="font-semibold text-purple-800">{content.acb_section.risk}</span>
              </div>
              <p className="text-purple-700 text-sm">
                Anticholinergic burden from: {content.acb_section.drugs.join(", ")}
              </p>
              {content.acb_section.citations && content.acb_section.citations.length > 0 && (
                <div className="mt-3 space-y-2">
                  {content.acb_section.citations.map((citation, index) => (
                    <div key={`${citation.source_key}-${index}`} className="rounded-xl border border-purple-200 bg-white/70 p-3">
                      <p className="text-sm font-semibold text-purple-900">{citation.source_label}</p>
                      <p className="text-sm text-purple-700 mt-1">{citation.evidence}</p>
                      {citation.evidence_scope_label && (
                        <p className="text-xs text-purple-700 mt-2">Evidence scope: {citation.evidence_scope_label}</p>
                      )}
                      {citation.reference && (
                        <p className="text-xs text-purple-700 mt-1">Reference: {citation.reference}</p>
                      )}
                      {(citation.source_url || citation.reference_url) && (
                        <div className="mt-2 flex flex-wrap gap-3 text-xs font-medium text-purple-900">
                          {citation.source_url && (
                            <a href={citation.source_url} target="_blank" rel="noreferrer" className="underline underline-offset-4">
                              Open source
                            </a>
                          )}
                          {citation.reference_url && (
                            <a href={citation.reference_url} target="_blank" rel="noreferrer" className="underline underline-offset-4">
                              {citation.reference_url_type === "pubmed_search" || citation.reference_url_type === "scholar_search"
                                ? "Search reference"
                                : "Open reference"}
                            </a>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </motion.div>
          )}

          {/* Disclaimer */}
          {content?.disclaimer && (
            <p className="text-xs text-muted-foreground text-center px-4 mb-4 leading-relaxed">
              {content.disclaimer}
            </p>
          )}

          {/* Action bar */}
          <div className="mt-auto flex flex-col gap-3 pt-4">
            {criticalCount > 0 ? (
              <Button className="w-full min-h-[60px] rounded-2xl bg-red-600 hover:bg-red-700 text-white text-base font-semibold">
                📞 Doctor se baat karein
              </Button>
            ) : majorCount > 0 ? (
              <Button className="w-full min-h-[60px] rounded-2xl bg-amber-600 hover:bg-amber-700 text-white text-base font-semibold">
                👨‍⚕️ Doctor ko dikhaayein
              </Button>
            ) : (
              <Button className="w-full min-h-[60px] rounded-2xl text-base font-semibold" onClick={handleShare}>
                <ShareNetwork size={20} className="mr-2" />
                Report share karein ✅
              </Button>
            )}

            <div className="flex gap-3">
              <Button
                variant="outline"
                className="flex-1 min-h-[52px] rounded-2xl"
                onClick={handleShare}
              >
                <ShareNetwork size={18} className="mr-2" />
                Share
              </Button>
              <Button
                variant="outline"
                className="flex-1 min-h-[52px] rounded-2xl"
                onClick={handleDownload}
              >
                <Download size={18} className="mr-2" />
                Download
              </Button>
              <Button
                variant="outline"
                className="flex-1 min-h-[52px] rounded-2xl"
                onClick={() => router.push("/camera")}
              >
                <ArrowCounterClockwise size={18} className="mr-2" />
                New
              </Button>
            </div>
          </div>
        </>
      )}
    </StepShell>
  )
}
