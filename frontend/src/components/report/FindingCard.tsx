"use client"

import { useState } from "react"
import { motion, AnimatePresence } from "motion/react"
import { CaretDown, CaretUp, Stethoscope, User } from "@phosphor-icons/react"
import { Badge } from "@/components/ui/badge"
import { SEVERITY_CONFIG } from "@/lib/constants"
import { cn } from "@/lib/utils"
import type { Interaction } from "@/types/sahayak"

interface FindingCardProps {
  finding: Interaction
  translatedFinding?: Interaction
  index: number
}

export function FindingCard({ finding, translatedFinding, index }: FindingCardProps) {
  const [expanded, setExpanded] = useState(finding.severity === "critical" || finding.severity === "major")
  const [showDoctorView, setShowDoctorView] = useState(false)

  const cfg = SEVERITY_CONFIG[finding.severity] ?? SEVERITY_CONFIG.unknown
  const citations = finding.citations ?? []
  const title = translatedFinding?.title || finding.title
  const patientExplanation = translatedFinding?.patient_explanation || finding.patient_explanation
  const doctorExplanation = translatedFinding?.doctor_explanation || finding.doctor_explanation
  const action = translatedFinding?.action || finding.action

  return (
    <motion.div
      custom={index}
      initial="hidden"
      animate="visible"
      variants={{
        hidden: { opacity: 0, y: 16 },
        visible: {
          opacity: 1,
          y: 0,
          transition: { delay: index * 0.15, duration: 0.5, ease: "easeOut" },
        },
      }}
      className={cn("rounded-2xl border-2 overflow-hidden", cfg.border, cfg.bg)}
    >
      {/* Header – always visible */}
      <button
        onClick={() => setExpanded((v) => !v)}
        className={cn(
          "w-full flex items-start gap-3 p-4 text-left",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        )}
        aria-expanded={expanded}
      >
        <span className="text-2xl flex-none mt-0.5">{cfg.icon}</span>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap mb-1">
            <Badge className={cn("text-xs font-bold rounded-lg", cfg.badge)}>
              {cfg.label}
            </Badge>
            {finding.medicines.map((m) => (
              <Badge key={m} variant="outline" className="text-xs rounded-lg">
                {m}
              </Badge>
            ))}
          </div>
          <p className="font-semibold text-base text-foreground leading-snug">{title}</p>
        </div>

        <div className="flex-none text-muted-foreground mt-1">
          {expanded ? <CaretUp size={20} /> : <CaretDown size={20} />}
        </div>
      </button>

      {/* Expanded body */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ type: "spring", stiffness: 260, damping: 30 }}
            className="overflow-hidden"
          >
            <div className="px-4 pb-4 border-t border-current/10">
              {/* Patient / Doctor toggle */}
              <div className="flex gap-2 mt-3 mb-3">
                <button
                  onClick={() => setShowDoctorView(false)}
                  className={cn(
                    "flex-1 flex items-center justify-center gap-1.5 rounded-xl min-h-[40px] text-sm font-medium border transition-colors",
                    !showDoctorView
                      ? "bg-foreground text-background border-foreground"
                      : "bg-transparent border-border text-muted-foreground hover:bg-muted"
                  )}
                >
                  <User size={16} />
                  Patient View
                </button>
                <button
                  onClick={() => setShowDoctorView(true)}
                  className={cn(
                    "flex-1 flex items-center justify-center gap-1.5 rounded-xl min-h-[40px] text-sm font-medium border transition-colors",
                    showDoctorView
                      ? "bg-foreground text-background border-foreground"
                      : "bg-transparent border-border text-muted-foreground hover:bg-muted"
                  )}
                >
                  <Stethoscope size={16} />
                  Doctor View
                </button>
              </div>

              <AnimatePresence mode="wait">
                <motion.div
                  key={showDoctorView ? "doctor" : "patient"}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: -4 }}
                  transition={{ duration: 0.2 }}
                >
                  <p className="text-base text-foreground leading-relaxed mb-3">
                    {showDoctorView ? doctorExplanation : patientExplanation}
                  </p>
                </motion.div>
              </AnimatePresence>

              {/* Action */}
              <div className={cn("rounded-xl p-3 mt-2", cfg.bg)}>
                <p className="font-semibold text-sm text-foreground">
                  {cfg.action}: {action}
                </p>
              </div>

              {/* Source */}
              {(finding.source || finding.evidence_profile) && (
                <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                  {finding.source && <p>Source: {finding.source} · Confidence: {finding.confidence}</p>}
                  {finding.evidence_profile && <p>Evidence profile: {finding.evidence_profile}</p>}
                  {finding.evidence_profile_note && <p>{finding.evidence_profile_note}</p>}
                </div>
              )}

              {citations.length > 0 && (
                <div className="mt-3 space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Evidence</p>
                  {citations.map((citation, citationIndex) => (
                    <div
                      key={`${citation.source_key}-${citationIndex}`}
                      className="rounded-xl border border-border/70 bg-background/70 p-3"
                    >
                      <p className="text-sm font-semibold text-foreground">
                        {citation.source_label}
                        {citation.table ? ` · ${citation.table}` : ""}
                      </p>
                      <p className="text-sm text-muted-foreground mt-1 leading-relaxed">
                        {citation.evidence}
                      </p>
                      {(citation.evidence_scope_label || citation.evidence_scope_description) && (
                        <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                          {citation.evidence_scope_label && <p>Evidence scope: {citation.evidence_scope_label}</p>}
                          {citation.evidence_scope_description && <p>{citation.evidence_scope_description}</p>}
                        </div>
                      )}
                      {(citation.provenance_label || citation.evidence_basis || citation.backing_source_label) && (
                        <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                          {citation.provenance_label && <p>Type: {citation.provenance_label}</p>}
                          {citation.evidence_basis && <p>Evidence basis: {citation.evidence_basis}</p>}
                          {citation.backing_source_label && (
                            <p>
                              Backed by:{" "}
                              {citation.backing_source_url ? (
                                <a
                                  href={citation.backing_source_url}
                                  target="_blank"
                                  rel="noreferrer"
                                  className="underline underline-offset-4"
                                >
                                  {citation.backing_source_label}
                                </a>
                              ) : (
                                citation.backing_source_label
                              )}
                            </p>
                          )}
                        </div>
                      )}
                      {(citation.study_count || citation.doi || citation.pmid || citation.record_locator) && (
                        <div className="mt-2 space-y-1 text-xs text-muted-foreground">
                          {citation.study_count ? <p>Studies matched: {citation.study_count}</p> : null}
                          {citation.doi ? <p>DOI: {citation.doi}</p> : null}
                          {citation.pmid ? <p>PMID: {citation.pmid}</p> : null}
                          {citation.record_locator ? <p>Record IDs: {citation.record_locator}</p> : null}
                        </div>
                      )}
                      {citation.record_links && citation.record_links.length > 0 && (
                        <div className="mt-2 flex flex-wrap gap-3 text-xs font-medium">
                          {citation.record_links.map((link) => (
                            <a
                              key={link.url}
                              href={link.url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-foreground underline underline-offset-4"
                            >
                              {link.label}
                            </a>
                          ))}
                        </div>
                      )}
                      {(citation.source_url || citation.reference_url || citation.backing_source_url) && (
                        <div className="mt-2 flex flex-wrap gap-3 text-xs font-medium">
                          {citation.source_url && (
                            <a
                              href={citation.source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-foreground underline underline-offset-4"
                            >
                              Open source
                            </a>
                          )}
                          {citation.backing_source_url && citation.backing_source_url !== citation.source_url && (
                            <a
                              href={citation.backing_source_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-foreground underline underline-offset-4"
                            >
                              Open backing source
                            </a>
                          )}
                          {citation.reference_url && (
                            <a
                              href={citation.reference_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-foreground underline underline-offset-4"
                            >
                              {citation.reference_url_type === "pubmed_search" || citation.reference_url_type === "scholar_search"
                                ? "Search reference"
                                : "Open reference"}
                            </a>
                          )}
                        </div>
                      )}
                      {!citation.source_url && !citation.reference_url && !citation.backing_source_url && (!citation.record_links || citation.record_links.length === 0) && (
                        <p className="text-xs text-muted-foreground mt-1">
                          External link unavailable for this evidence class.
                        </p>
                      )}
                      {citation.provenance_note && (
                        <p className="text-xs text-muted-foreground mt-1">
                          Provenance: {citation.provenance_note}
                        </p>
                      )}
                      {citation.reference && (
                        <p className="text-xs text-muted-foreground mt-1">
                          Reference:{" "}
                          {citation.reference_url ? (
                            <a
                              href={citation.reference_url}
                              target="_blank"
                              rel="noreferrer"
                              className="underline underline-offset-4"
                            >
                              {citation.reference}
                            </a>
                          ) : (
                            citation.reference
                          )}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  )
}
