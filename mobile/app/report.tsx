import { useState } from "react"
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Share,
  Linking,
  Alert,
} from "react-native"
import { useRouter } from "expo-router"
import Animated, { FadeInDown, FadeInUp } from "react-native-reanimated"
import * as Haptics from "expo-haptics"
import * as Print from "expo-print"
import { Ionicons } from "@expo/vector-icons"
import { StepShell } from "../components/layout/StepShell"
import { VoiceBar } from "../components/ui/VoiceBar"
import { useAppStore } from "../hooks/useAppStore"
import { useTTS } from "../hooks/useVoice"
import {
  getMedicineDisplayName,
  getMedicinePrescriberSource,
  getPrescriberSummaryRows,
} from "../lib/medicines"
import { COLORS, SEVERITY_CONFIG } from "../lib/constants"
import { t } from "../lib/i18n"
import type {
  ExtractedDrug,
  Interaction,
  InteractionDisplaySeverity,
  InteractionSeverity,
  PatientInfo,
  PrescriberSource,
  ReportCitation,
  ReportContent,
} from "../types/sahayak"

// ── Severity header bar ───────────────────────────────────────────────────────
const SEVERITY_ORDER: InteractionDisplaySeverity[] = [
  "critical", "major", "moderate", "minor", "doctor_review",
]

function getDisplaySeverity(finding: Interaction): InteractionDisplaySeverity {
  if (finding.display_severity) return finding.display_severity
  if (finding.severity === "critical" || finding.severity === "major" || finding.severity === "moderate" || finding.severity === "minor") {
    return finding.severity
  }
  return "doctor_review"
}

function getSeverityConfig(finding: Interaction) {
  const displaySeverity = getDisplaySeverity(finding)
  return SEVERITY_CONFIG[displaySeverity] ?? SEVERITY_CONFIG.doctor_review
}

function getSeverityLabel(finding: Interaction, lang: string): string {
  const displaySeverity = getDisplaySeverity(finding)
  if (displaySeverity === "doctor_review") {
    return t("doctor_review", lang).toUpperCase()
  }
  return (SEVERITY_CONFIG[displaySeverity]?.label ?? displaySeverity).toUpperCase()
}

function getFindingId(finding: Interaction, index: number): string {
  return (
    finding.finding_id ||
    [
      finding.title,
      finding.source,
      finding.medicines.join("|"),
      index,
    ].join("::")
  )
}

function SeverityBar({ findings, lang }: { findings: Interaction[]; lang: string }) {
  const counts = findings.reduce(
    (acc, f) => {
      const sev = getDisplaySeverity(f)
      acc[sev] = (acc[sev] ?? 0) + 1
      return acc
    },
    {} as Record<InteractionDisplaySeverity, number>
  )

  const hasCritical = (counts.critical ?? 0) > 0

  return (
    <Animated.View
      entering={FadeInUp.duration(300)}
      style={[severityBarStyles.container, hasCritical && severityBarStyles.critical]}
    >
      {hasCritical ? (
        <View style={severityBarStyles.alertRow}>
          <Text style={severityBarStyles.alertIcon}>⛔</Text>
          <View>
            <Text style={severityBarStyles.alertTitle}>{t("critical_found", lang)}</Text>
            <Text style={severityBarStyles.alertSub}>
              {t("consult_doctor", lang)}
            </Text>
          </View>
        </View>
      ) : (
        <Text style={severityBarStyles.safeText}>
          {findings.length === 0
            ? `✅ ${t("no_significant_interactions", lang)}`
            : `${findings.length} ${t("interactions_found", lang)}`}
        </Text>
      )}

      {/* Severity chips */}
      {Object.keys(counts).length > 0 && (
        <View style={severityBarStyles.chips}>
          {SEVERITY_ORDER.filter((s) => counts[s]).map((sev) => {
            const cfg = SEVERITY_CONFIG[sev]
            return (
              <View key={sev} style={[severityBarStyles.chip, { backgroundColor: cfg.badgeBg }]}>
                <Text style={[severityBarStyles.chipText, { color: cfg.text }]}>
                  {cfg.icon} {counts[sev]} {sev === "doctor_review" ? t("doctor_review", lang) : cfg.label}
                </Text>
              </View>
            )
          })}
        </View>
      )}
    </Animated.View>
  )
}

function citationSourceSummary(citations?: ReportCitation[]): string | undefined {
  if (!citations || citations.length === 0) return undefined
  return Array.from(new Set(citations.map((citation) => citation.source_label).filter(Boolean))).join(", ")
}

function mergeDisplayFinding(finding: Interaction, translated?: Interaction): Interaction {
  if (!translated) return finding
  return {
    ...finding,
    title: translated.title || finding.title,
    patient_explanation: translated.patient_explanation || finding.patient_explanation,
    doctor_explanation: translated.doctor_explanation || finding.doctor_explanation,
    action: translated.action || finding.action,
  }
}

function mergeFindingsById(baseFindings: Interaction[], translatedFindings?: Interaction[]): Interaction[] {
  const translatedById = new Map<string, Interaction>()
  for (const finding of translatedFindings ?? []) {
    translatedById.set(getFindingId(finding, translatedById.size), finding)
  }
  return baseFindings.map((finding, index) =>
    mergeDisplayFinding(finding, translatedById.get(getFindingId(finding, index))),
  )
}

function mergeAcbSection(
  section?: ReportContent["acb_section"] | null,
  translated?: ReportContent["acb_section"] | null,
): ReportContent["acb_section"] | null {
  if (!section) return translated ?? null
  if (!translated) return section
  return {
    ...section,
    risk: translated.risk || section.risk,
  }
}

function mergeDisplayContent(
  content?: ReportContent | null,
  translated?: ReportContent | null,
): ReportContent | null {
  const base = content ?? translated
  if (!base) return null
  return {
    ...base,
    patient_summary: translated?.patient_summary || base.patient_summary,
    self_prescribed_warning: translated?.self_prescribed_warning ?? base.self_prescribed_warning,
    personalized_advice: translated?.personalized_advice ?? base.personalized_advice,
    disclaimer: translated?.disclaimer || base.disclaimer,
    acb_section: mergeAcbSection(base.acb_section, translated?.acb_section) ?? base.acb_section,
    findings: mergeFindingsById(base.findings ?? [], translated?.findings),
  }
}

async function openExternalUrl(url: string) {
  const supported = await Linking.canOpenURL(url)
  if (!supported) {
    Alert.alert("Unable to open link", url)
    return
  }
  await Linking.openURL(url)
}

const severityBarStyles = StyleSheet.create({
  container: {
    padding: 16, borderRadius: 16, backgroundColor: "#E8F5E9",
    borderWidth: 2, borderColor: "#A5D6A7", gap: 10,
  },
  critical: { backgroundColor: "#FFEBEE", borderColor: "#EF9A9A" },
  alertRow: { flexDirection: "row", alignItems: "center", gap: 12 },
  alertIcon: { fontSize: 28 },
  alertTitle: { fontSize: 17, fontWeight: "800", color: "#C62828" },
  alertSub: { fontSize: 13, color: "#B71C1C", marginTop: 2 },
  safeText: { fontSize: 16, fontWeight: "700", color: "#2E7D32" },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  chip: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8 },
  chipText: { fontSize: 12, fontWeight: "700" },
})

// ── Interaction Finding Card ──────────────────────────────────────────────────
function FindingCard({
  finding,
  index,
  lang,
}: {
  finding: Interaction
  index: number
  lang: string
}) {
  const [expanded, setExpanded] = useState(false)
  const cfg = getSeverityConfig(finding)
  const citations = finding.citations ?? []

  return (
    <Animated.View
      entering={FadeInDown.delay(index * 100).duration(300)}
      style={[findingStyles.card, { borderColor: cfg.border, backgroundColor: cfg.bg }]}
    >
      {/* Badge + title */}
      <View style={findingStyles.header}>
        <View style={[findingStyles.badge, { backgroundColor: cfg.badgeBg }]}>
          <Text style={[findingStyles.badgeText, { color: cfg.text }]}>
            {cfg.icon} {getSeverityLabel(finding, lang)}
          </Text>
        </View>
        <TouchableOpacity
          onPress={() => {
            setExpanded((e) => !e)
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
          }}
          style={findingStyles.expandBtn}
        >
          <Ionicons
            name={expanded ? "chevron-up" : "chevron-down"}
            size={18}
            color={cfg.text}
          />
        </TouchableOpacity>
      </View>

      <Text style={[findingStyles.title, { color: cfg.text }]}>
        {finding.title}
      </Text>

      {/* Medicine chips */}
      <View style={findingStyles.medsRow}>
        {finding.medicines.map((med, i) => (
          <View key={i} style={[findingStyles.medChip, { borderColor: cfg.border }]}>
            <Text style={[findingStyles.medChipText, { color: cfg.text }]}>{med}</Text>
          </View>
        ))}
      </View>

      {/* Patient explanation */}
      <Text style={[findingStyles.patientExp, { color: cfg.text }]}>
        {finding.patient_explanation}
      </Text>

      {/* Expanded: doctor explanation + action */}
      {expanded && (
        <Animated.View entering={FadeInDown.duration(300)} style={findingStyles.expanded}>
          <Text style={findingStyles.expandLabel}>{t("for_doctor", lang)}:</Text>
          <Text style={findingStyles.doctorExp}>{finding.doctor_explanation}</Text>
          <View style={findingStyles.actionRow}>
            <Ionicons name="arrow-forward-circle" size={18} color={cfg.text} />
            <Text style={[findingStyles.action, { color: cfg.text }]}>
              {finding.action}
            </Text>
          </View>
          <Text style={findingStyles.source}>Source: {finding.source} · {finding.confidence} confidence</Text>
          {finding.evidence_profile ? (
            <Text style={findingStyles.source}>Evidence profile: {finding.evidence_profile}</Text>
          ) : null}
          {finding.evidence_profile_note ? (
            <Text style={findingStyles.source}>{finding.evidence_profile_note}</Text>
          ) : null}
          {citations.length > 0 && (
            <View style={findingStyles.citations}>
              <Text style={findingStyles.expandLabel}>Evidence</Text>
              {citations.map((citation, i) => (
                <View key={`${citation.source_key}-${i}`} style={findingStyles.citationCard}>
                  <Text style={findingStyles.citationTitle}>
                    {citation.source_label}
                    {citation.table ? ` · ${citation.table}` : ""}
                  </Text>
                  <Text style={findingStyles.citationBody}>{citation.evidence}</Text>
                  {citation.evidence_scope_label || citation.evidence_scope_description ? (
                    <View style={findingStyles.metaBlock}>
                      {citation.evidence_scope_label ? (
                        <Text style={findingStyles.citationMeta}>Evidence scope: {citation.evidence_scope_label}</Text>
                      ) : null}
                      {citation.evidence_scope_description ? (
                        <Text style={findingStyles.citationMeta}>{citation.evidence_scope_description}</Text>
                      ) : null}
                    </View>
                  ) : null}
                  {(citation.provenance_label || citation.evidence_basis || citation.backing_source_label) ? (
                    <View style={findingStyles.metaBlock}>
                      {citation.provenance_label ? (
                        <Text style={findingStyles.citationMeta}>Type: {citation.provenance_label}</Text>
                      ) : null}
                      {citation.evidence_basis ? (
                        <Text style={findingStyles.citationMeta}>Evidence basis: {citation.evidence_basis}</Text>
                      ) : null}
                      {citation.backing_source_label ? (
                        <Text style={findingStyles.citationMeta}>Backed by: {citation.backing_source_label}</Text>
                      ) : null}
                    </View>
                  ) : null}
                  {citation.study_count || citation.doi || citation.pmid || citation.record_locator ? (
                    <View style={findingStyles.metaBlock}>
                      {citation.study_count ? (
                        <Text style={findingStyles.citationMeta}>Studies matched: {citation.study_count}</Text>
                      ) : null}
                      {citation.doi ? (
                        <Text style={findingStyles.citationMeta}>DOI: {citation.doi}</Text>
                      ) : null}
                      {citation.pmid ? (
                        <Text style={findingStyles.citationMeta}>PMID: {citation.pmid}</Text>
                      ) : null}
                      {citation.record_locator ? (
                        <Text style={findingStyles.citationMeta}>Record IDs: {citation.record_locator}</Text>
                      ) : null}
                    </View>
                  ) : null}
                  {citation.record_links && citation.record_links.length > 0 ? (
                    <View style={findingStyles.linkRow}>
                      {citation.record_links.map((link) => (
                        <TouchableOpacity key={link.url} onPress={() => void openExternalUrl(link.url)} style={findingStyles.linkChip}>
                          <Ionicons name="link-outline" size={12} color={COLORS.primary} />
                          <Text style={findingStyles.linkText}>{link.label}</Text>
                        </TouchableOpacity>
                      ))}
                    </View>
                  ) : null}
                  {(citation.source_url || citation.reference_url || citation.backing_source_url) ? (
                    <View style={findingStyles.linkRow}>
                      {citation.source_url ? (
                        <TouchableOpacity onPress={() => void openExternalUrl(citation.source_url!)} style={findingStyles.linkChip}>
                          <Ionicons name="open-outline" size={12} color={COLORS.primary} />
                          <Text style={findingStyles.linkText}>Open source</Text>
                        </TouchableOpacity>
                      ) : null}
                      {citation.backing_source_url && citation.backing_source_url !== citation.source_url ? (
                        <TouchableOpacity onPress={() => void openExternalUrl(citation.backing_source_url!)} style={findingStyles.linkChip}>
                          <Ionicons name="library-outline" size={12} color={COLORS.primary} />
                          <Text style={findingStyles.linkText}>Open backing source</Text>
                        </TouchableOpacity>
                      ) : null}
                      {citation.reference_url ? (
                        <TouchableOpacity onPress={() => void openExternalUrl(citation.reference_url!)} style={findingStyles.linkChip}>
                          <Ionicons name="document-text-outline" size={12} color={COLORS.primary} />
                          <Text style={findingStyles.linkText}>
                            {citation.reference_url_type === "pubmed_search" || citation.reference_url_type === "scholar_search"
                              ? "Search reference"
                              : "Open reference"}
                          </Text>
                        </TouchableOpacity>
                      ) : null}
                    </View>
                  ) : null}
                  {!citation.source_url && !citation.reference_url && !citation.backing_source_url && (!citation.record_links || citation.record_links.length === 0) ? (
                    <Text style={findingStyles.citationMeta}>
                      External link unavailable for this evidence class.
                    </Text>
                  ) : null}
                  {citation.reference ? (
                    <Text style={findingStyles.citationMeta}>
                      Reference: {citation.reference}
                    </Text>
                  ) : null}
                  {citation.provenance_note ? (
                    <Text style={findingStyles.citationMeta}>Provenance: {citation.provenance_note}</Text>
                  ) : null}
                </View>
              ))}
            </View>
          )}
        </Animated.View>
      )}
    </Animated.View>
  )
}

const findingStyles = StyleSheet.create({
  card: {
    borderRadius: 16, borderWidth: 2, padding: 16, marginBottom: 12, gap: 8,
  },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8 },
  badgeText: { fontSize: 12, fontWeight: "800" },
  expandBtn: { padding: 4 },
  title: { fontSize: 17, fontWeight: "800", letterSpacing: -0.3 },
  medsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  medChip: {
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8,
    borderWidth: 1.5, backgroundColor: "rgba(255,255,255,0.6)",
  },
  medChipText: { fontSize: 12, fontWeight: "700" },
  patientExp: { fontSize: 15, lineHeight: 22 },
  expanded: { gap: 8, marginTop: 4 },
  expandLabel: { fontSize: 12, fontWeight: "800", color: "#64748B", textTransform: "uppercase" },
  doctorExp: { fontSize: 13, color: "#475569", lineHeight: 20 },
  actionRow: { flexDirection: "row", alignItems: "flex-start", gap: 8 },
  action: { flex: 1, fontSize: 14, fontWeight: "700" },
  source: { fontSize: 11, color: "#94A3B8" },
  citations: { gap: 8, marginTop: 2 },
  citationCard: {
    borderRadius: 12,
    padding: 10,
    backgroundColor: "rgba(255,255,255,0.7)",
    borderWidth: 1,
    borderColor: "#E2E8F0",
    gap: 4,
  },
  citationTitle: { fontSize: 12, fontWeight: "800", color: "#334155" },
  citationBody: { fontSize: 12, color: "#475569", lineHeight: 18 },
  citationMeta: { fontSize: 11, color: "#64748B" },
  metaBlock: { gap: 2, marginTop: 2 },
  linkRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 2 },
  linkChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 999,
    backgroundColor: "rgba(37,99,235,0.08)",
  },
  linkText: { fontSize: 11, fontWeight: "700", color: COLORS.primary },
})

// ── ACB Section ───────────────────────────────────────────────────────────────
function ACBSection({ section }: { section: { score: number; risk: string; drugs: string[]; citations?: ReportCitation[] } }) {
  if (!section || section.score === 0) return null
  const severity = section.score >= 3 ? "high" : section.score >= 2 ? "moderate" : "low"
  const bg = severity === "high" ? "#FFEBEE" : severity === "moderate" ? "#FFF8E1" : "#E1F5FE"
  const border = severity === "high" ? "#EF9A9A" : severity === "moderate" ? "#FFE082" : "#81D4FA"
  const text = severity === "high" ? "#C62828" : severity === "moderate" ? "#F57F17" : "#01579B"
  const citations = section.citations ?? []

  return (
    <View style={[acbStyles.container, { backgroundColor: bg, borderColor: border }]}>
      <View style={acbStyles.header}>
        <Text style={[acbStyles.score, { color: text }]}>ACB Score: {section.score}</Text>
        <View style={[acbStyles.badge, { backgroundColor: border }]}>
          <Text style={[acbStyles.badgeText, { color: text }]}>{severity.toUpperCase()} RISK</Text>
        </View>
      </View>
      <Text style={[acbStyles.risk, { color: text }]}>{section.risk}</Text>
      {section.drugs.length > 0 && (
        <Text style={acbStyles.drugs}>Anticholinergic drugs: {section.drugs.join(", ")}</Text>
      )}
      {citations.length > 0 && (
        <View style={acbStyles.citations}>
          {citations.map((citation, i) => (
            <View key={`${citation.source_key}-${i}`} style={findingStyles.citationCard}>
              <Text style={findingStyles.citationTitle}>{citation.source_label}</Text>
              <Text style={findingStyles.citationBody}>{citation.evidence}</Text>
              {citation.evidence_scope_label ? (
                <Text style={findingStyles.citationMeta}>Evidence scope: {citation.evidence_scope_label}</Text>
              ) : null}
              {citation.reference ? (
                <Text style={findingStyles.citationMeta}>Reference: {citation.reference}</Text>
              ) : null}
              {(citation.source_url || citation.reference_url) ? (
                <View style={findingStyles.linkRow}>
                  {citation.source_url ? (
                    <TouchableOpacity onPress={() => void openExternalUrl(citation.source_url!)} style={findingStyles.linkChip}>
                      <Ionicons name="open-outline" size={12} color={COLORS.primary} />
                      <Text style={findingStyles.linkText}>Open source</Text>
                    </TouchableOpacity>
                  ) : null}
                  {citation.reference_url ? (
                    <TouchableOpacity onPress={() => void openExternalUrl(citation.reference_url!)} style={findingStyles.linkChip}>
                      <Ionicons name="document-text-outline" size={12} color={COLORS.primary} />
                      <Text style={findingStyles.linkText}>
                        {citation.reference_url_type === "pubmed_search" || citation.reference_url_type === "scholar_search"
                          ? "Search reference"
                          : "Open reference"}
                      </Text>
                    </TouchableOpacity>
                  ) : null}
                </View>
              ) : null}
            </View>
          ))}
        </View>
      )}
    </View>
  )
}

const acbStyles = StyleSheet.create({
  container: { padding: 16, borderRadius: 16, borderWidth: 2, gap: 6, marginBottom: 12 },
  header: { flexDirection: "row", justifyContent: "space-between", alignItems: "center" },
  score: { fontSize: 17, fontWeight: "800" },
  badge: { paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8 },
  badgeText: { fontSize: 11, fontWeight: "800" },
  risk: { fontSize: 14, lineHeight: 20 },
  drugs: { fontSize: 13, color: "#64748B" },
  citations: { gap: 8, marginTop: 8 },
})

// ── Main Screen ───────────────────────────────────────────────────────────────
export default function ReportScreen() {
  const router = useRouter()
  const lang = useAppStore((s) => s.language)
  const { safetyReport, safetyCheckResult, patientInfo, confirmedMedicines, prescriberMap, reset } = useAppStore()
  const tts = useTTS()

  const english: ReportContent | null = safetyReport?.english ?? null
  const translated: ReportContent | null = safetyReport?.translated ?? null

  const [showTranslated, setShowTranslated] = useState(true)
  const translatedEnabled = showTranslated && !!translated
  const baseContent = english ?? translated
  const active = mergeDisplayContent(baseContent, translatedEnabled ? translated : null)

  // Use findings from LLM report; if empty, build from raw safety check result
  let findings = (active?.findings ?? []) as Interaction[]
  if (findings.length === 0 && safetyCheckResult) {
    const raw = safetyCheckResult as Record<string, unknown>
    const rawFindings = (raw.findings ?? raw.direct_interactions ?? []) as Record<string, unknown>[]
    const herbInteractions = (raw.herb_drug_interactions ?? []) as Record<string, unknown>[]
    const beersFlags = (raw.beers_flags ?? []) as Record<string, unknown>[]
    const built: Interaction[] = []
    for (const [index, item] of rawFindings.entries()) {
      const citations = (Array.isArray(item.citations) ? item.citations : undefined) as ReportCitation[] | undefined
      const severity = String(item.severity ?? "unknown").toLowerCase() as InteractionSeverity
      const displaySeverity = String(item.display_severity ?? (severity === "unknown" ? "doctor_review" : severity)) as InteractionDisplaySeverity
      built.push({
        finding_id: String(item.finding_id ?? `raw-direct-${index}-${item.drug_a ?? ""}-${item.drug_b ?? ""}`),
        severity,
        display_severity: displaySeverity,
        title: `${item.drug_a ?? ""} + ${item.drug_b ?? ""}`.trim().replace(/^\+\s*|\s*\+$/g, ""),
        patient_explanation: String(item.clinical_effect ?? item.pathway ?? "Possible interaction detected."),
        doctor_explanation: String(item.mechanism ?? item.pathway ?? ""),
        action: String(item.management ?? "Discuss with your doctor."),
        medicines: [item.drug_a, item.drug_b, item.herb].filter(Boolean) as string[],
        confidence: Number(item.confidence ?? 0.8) >= 0.85 ? "high" : "medium",
        source: citationSourceSummary(citations) ?? String(item.source ?? item.source_layer ?? "graph"),
        citations,
      })
    }
    for (const [index, item] of herbInteractions.entries()) {
      const citations = (Array.isArray(item.citations) ? item.citations : undefined) as ReportCitation[] | undefined
      const severity = String(item.severity ?? "unknown").toLowerCase() as InteractionSeverity
      built.push({
        finding_id: String(item.finding_id ?? `raw-herb-${index}-${item.herb ?? ""}-${item.drug ?? ""}`),
        severity,
        display_severity: String(item.display_severity ?? (severity === "unknown" ? "doctor_review" : severity)) as InteractionDisplaySeverity,
        title: `${item.herb ?? ""} + ${item.drug ?? ""}`.trim().replace(/^\+\s*|\s*\+$/g, ""),
        patient_explanation: String(item.clinical_effect ?? "Possible herb-drug interaction."),
        doctor_explanation: String(item.clinical_effect ?? ""),
        action: "Tell your doctor about herbal medicines you take.",
        medicines: [item.herb, item.drug].filter(Boolean) as string[],
        confidence: "medium",
        source: citationSourceSummary(citations) ?? String(item.source ?? "herb database"),
        citations,
      })
    }
    for (const [index, item] of beersFlags.entries()) {
      const citations = (Array.isArray(item.citations) ? item.citations : undefined) as ReportCitation[] | undefined
      built.push({
        finding_id: String(item.finding_id ?? `raw-beers-${index}-${item.drug ?? "unknown"}`),
        severity: "major",
        display_severity: "major",
        title: `${item.drug ?? "Unknown"} — BEERS Criteria Flag`,
        patient_explanation: `${item.drug ?? "This medicine"} may not be safe for you. ${item.concern ?? ""}`,
        doctor_explanation: `BEERS criteria flag: ${item.concern ?? ""}`,
        action: "Discuss with your doctor.",
        medicines: [item.drug].filter(Boolean) as string[],
        confidence: "high",
        source: citationSourceSummary(citations) ?? "BEERS Criteria",
        citations,
      })
    }
    if (built.length > 0) findings = built
  }

  async function handleShare() {
    const prescriberRows = getPrescriberSummaryRows(confirmedMedicines, prescriberMap)
    // Build prescriber info text
    const prescriberText = prescriberRows.length > 0
      ? "\nPRESCRIBER INFO:\n" + prescriberRows.map(({ name, source, manual }) => {
          const label = source === "doctor" ? "Doctor" : source === "self" ? "Self" : "Pharmacy"
          return `  ${name}${manual ? " (Manual entry)" : ""}: ${label}`
        }).join("\n")
      : ""

    const personalizedAdvice = active?.personalized_advice ?? undefined
    const adviceText = personalizedAdvice ? `\nPERSONALIZED ADVICE:\n${personalizedAdvice}` : ""

    const text = [
      "SAHAYAK " + t("your_report", lang),
      "",
      `Patient: ${patientInfo.name || "Unknown"} · ${patientInfo.age ? patientInfo.age + " years" : ""} · ${patientInfo.gender || ""}`,
      patientInfo.conditions.length > 0 ? `Conditions: ${patientInfo.conditions.join(", ")}` : "",
      "",
      active?.patient_summary ?? "",
      prescriberText,
      "",
      ...findings.map((f: Interaction) => [
        `${getSeverityLabel(f, lang)}: ${f.title}`,
        f.patient_explanation,
        f.source ? `Source: ${f.source}` : "",
        f.evidence_profile ? `Evidence profile: ${f.evidence_profile}` : "",
        ...(f.citations ?? []).flatMap((citation) => [
          `- ${citation.source_label}${citation.evidence_scope_label ? ` (${citation.evidence_scope_label})` : ""}`,
          citation.source_url ? `  ${citation.source_url}` : "",
          citation.reference_url ? `  ${citation.reference_url}` : "",
        ]),
      ].filter(Boolean).join("\n")),
      adviceText,
      "",
      active?.disclaimer ?? "",
    ].filter(Boolean).join("\n")

    await Share.share({ message: text, title: "SAHAYAK Report" })
  }

  async function handleDownloadPdf() {
    try {
      const html = buildReportHtml(active, patientInfo, confirmedMedicines, prescriberMap, findings)
      await Print.printAsync({ html })
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
    } catch (err) {
      Alert.alert(t("error_occurred", lang), String(err))
    }
  }

  function handleCallDoctor() {
    Linking.openURL("tel://*")
  }

  function handleStartOver() {
    reset()
    router.replace("/language")
  }

  if (!safetyReport || !english) {
    return (
      <StepShell step={7}>
        <View style={styles.centerState}>
          <Text style={styles.centerIcon}>⚠️</Text>
          <Text style={styles.centerTitle}>{t("error_occurred", lang)}</Text>
          <TouchableOpacity style={styles.retryBtn} onPress={() => router.back()}>
            <Text style={styles.retryBtnText}>← {t("go_back", lang)}</Text>
          </TouchableOpacity>
        </View>
      </StepShell>
    )
  }

  return (
    <StepShell step={7}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        showsVerticalScrollIndicator={false}
      >
        {/* Language toggle */}
        {translated && (
          <View style={styles.langToggle}>
            <TouchableOpacity
              style={[styles.langBtn, showTranslated && styles.langBtnActive]}
              onPress={() => setShowTranslated(true)}
            >
              <Text style={[styles.langBtnText, showTranslated && styles.langBtnTextActive]}>
                🌐 {t("in_your_language", lang)}
              </Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.langBtn, !showTranslated && styles.langBtnActive]}
              onPress={() => setShowTranslated(false)}
            >
              <Text style={[styles.langBtnText, !showTranslated && styles.langBtnTextActive]}>
                🇬🇧 {t("english", lang)}
              </Text>
            </TouchableOpacity>
          </View>
        )}

        {/* Patient summary */}
        <Animated.View entering={FadeInDown.delay(50).duration(300)} style={styles.summaryCard}>
          <Text style={styles.summaryTitle}>📋 {t("your_report", lang)}</Text>
          <Text style={styles.summaryText}>{active?.patient_summary}</Text>
          {active?.patient_summary && (
            <VoiceBar
              text={active.patient_summary}
              language={lang}
              ttsPlaying={tts.playing}
              onSpeak={tts.speak}
              onStopSpeak={tts.stop}
            />
          )}
        </Animated.View>

        {/* Severity bar */}
        <SeverityBar findings={findings} lang={lang} />
        <View style={{ height: 16 }} />

        {/* ACB section */}
        {active?.acb_section && <ACBSection section={active.acb_section} />}

        {/* Self-prescribed warning */}
        {active?.self_prescribed_warning && (
          <Animated.View entering={FadeInDown.delay(150).duration(300)} style={styles.selfWarnBox}>
            <Text style={styles.selfWarnIcon}>⚠️</Text>
            <Text style={styles.selfWarnText}>{active.self_prescribed_warning}</Text>
          </Animated.View>
        )}

        {/* Personalized advice — from LLM */}
        {active?.personalized_advice && (
          <Animated.View entering={FadeInDown.delay(180).duration(300)} style={styles.personalizedBox}>
            <Text style={styles.personalizedIcon}>🩺</Text>
            <View style={{ flex: 1 }}>
              <Text style={styles.personalizedTitle}>Personalized Advice</Text>
              <Text style={styles.personalizedText}>
                {active.personalized_advice}
              </Text>
            </View>
          </Animated.View>
        )}

        {/* Prescriber summary */}
        {getPrescriberSummaryRows(confirmedMedicines, prescriberMap).length > 0 && (
          <Animated.View entering={FadeInDown.delay(200).duration(300)} style={styles.prescriberBox}>
            <Text style={styles.prescriberTitle}>💊 Prescriber Summary</Text>
            {getPrescriberSummaryRows(confirmedMedicines, prescriberMap).map(({ key, name, source, manual }) => (
              <View key={key} style={styles.prescriberRow}>
                <View style={styles.prescriberNameWrap}>
                  <Text style={styles.prescriberMedName} numberOfLines={1}>{name}</Text>
                  {manual ? (
                    <View style={styles.prescriberManualBadge}>
                      <Text style={styles.prescriberManualBadgeText}>{t("manual_entry", lang)}</Text>
                    </View>
                  ) : null}
                </View>
                <View style={[
                  styles.prescriberBadge,
                  source === "doctor" && { backgroundColor: "#E8F5E9" },
                  source === "self" && { backgroundColor: "#FFEBEE" },
                  source === "medical_shop" && { backgroundColor: "#FFF8E1" },
                ]}>
                  <Text style={[
                    styles.prescriberBadgeText,
                    source === "doctor" && { color: "#2E7D32" },
                    source === "self" && { color: "#C62828" },
                    source === "medical_shop" && { color: "#E65100" },
                  ]}>
                    {source === "doctor" ? "👨‍⚕️ Doctor" : source === "self" ? "🙋 Self" : "🏪 Pharmacy"}
                  </Text>
                </View>
              </View>
            ))}
          </Animated.View>
        )}

        {/* Findings */}
        {findings.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>{t("interactions_found", lang)}</Text>
            {findings.map((finding, i) => (
              <FindingCard
                key={getFindingId(finding, i)}
                finding={finding}
                index={i}
                lang={lang}
              />
            ))}
          </View>
        )}

        {/* No interactions */}
        {findings.length === 0 && (
          <Animated.View entering={FadeInDown.delay(200).duration(300)} style={styles.noInteractionBox}>
            <Text style={styles.noInteractionIcon}>✅</Text>
            <Text style={styles.noInteractionTitle}>{t("no_issues_found", lang)}</Text>
            <Text style={styles.noInteractionBody}>
              {t("no_significant_interactions", lang)}
            </Text>
          </Animated.View>
        )}

        {/* Disclaimer */}
        {active?.disclaimer && (
          <Text style={styles.disclaimer}>{active.disclaimer}</Text>
        )}

        {/* PDF download */}
        <TouchableOpacity style={styles.pdfBtn} onPress={handleDownloadPdf} activeOpacity={0.85}>
          <Ionicons name="document-text-outline" size={20} color={COLORS.primary} />
          <Text style={styles.pdfBtnText}>{t("download_pdf", lang)}</Text>
        </TouchableOpacity>

        <View style={{ height: 140 }} />
      </ScrollView>

      {/* Fixed action bar */}
      <View style={styles.actionBar}>
        <TouchableOpacity style={styles.shareBtn} onPress={handleShare} activeOpacity={0.8}>
          <Ionicons name="share-social-outline" size={20} color={COLORS.primary} />
          <Text style={styles.shareBtnText}>{t("share", lang)}</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.doctorBtn} onPress={handleCallDoctor} activeOpacity={0.8}>
          <Ionicons name="call-outline" size={20} color="#FFF" />
          <Text style={styles.doctorBtnText}>{t("call_doctor", lang)}</Text>
        </TouchableOpacity>

        <TouchableOpacity style={styles.restartBtn} onPress={handleStartOver} activeOpacity={0.8}>
          <Ionicons name="refresh" size={20} color={COLORS.slate} />
        </TouchableOpacity>
      </View>
    </StepShell>
  )
}

// ── PDF HTML Builder ──────────────────────────────────────────────────────────

function buildReportHtml(
  content: ReportContent | null,
  patient: PatientInfo,
  medicines: ExtractedDrug[],
  prescriberMap: Record<string, PrescriberSource>,
  findings: Interaction[]
): string {
  if (!content) return "<h1>Report unavailable</h1>"

  const sevColor = (severity: InteractionDisplaySeverity) =>
    severity === "critical"
      ? "#C62828"
      : severity === "major"
      ? "#E64A19"
      : severity === "moderate"
      ? "#F57F17"
      : severity === "minor"
      ? "#1565C0"
      : "#6D4C41"

  const findingsHtml = findings
    .map((f) => {
      const displaySeverity = getDisplaySeverity(f)
      const citationsHtml = (f.citations ?? [])
        .map((citation) => {
          const links = [
            citation.source_url ? `<a href="${escapeHtml(citation.source_url)}">Open source</a>` : "",
            citation.backing_source_url && citation.backing_source_url !== citation.source_url
              ? `<a href="${escapeHtml(citation.backing_source_url)}">Open backing source</a>`
              : "",
            citation.reference_url
              ? `<a href="${escapeHtml(citation.reference_url)}">${
                  citation.reference_url_type === "pubmed_search" || citation.reference_url_type === "scholar_search"
                    ? "Search reference"
                    : "Open reference"
                }</a>`
              : "",
          ].filter(Boolean).join(" · ")

          return `
            <div style="margin-top:8px;padding:8px;border:1px solid #e5e7eb;border-radius:8px;background:#f8fafc;">
              <p style="margin:0 0 4px 0;font-weight:700;">${escapeHtml(citation.source_label)}</p>
              <p style="margin:0 0 4px 0;">${escapeHtml(citation.evidence)}</p>
              ${citation.evidence_scope_label ? `<p style="margin:0 0 4px 0;color:#64748B;font-size:12px;">Evidence scope: ${escapeHtml(citation.evidence_scope_label)}</p>` : ""}
              ${citation.reference ? `<p style="margin:0 0 4px 0;color:#64748B;font-size:12px;">Reference: ${escapeHtml(citation.reference)}</p>` : ""}
              ${links ? `<p style="margin:0;color:#2563EB;font-size:12px;">${links}</p>` : ""}
            </div>`
        })
        .join("")

      return `
        <div class="finding-card" style="border:1px solid #ccc;border-radius:8px;padding:12px;margin-bottom:10px;">
          <strong style="color:${sevColor(displaySeverity)}">
            ${escapeHtml(displaySeverity === "doctor_review" ? "DOCTOR REVIEW" : displaySeverity.toUpperCase())}: ${escapeHtml(f.title)}
          </strong>
          <p>${escapeHtml(f.patient_explanation)}</p>
          <p style="color:#666;font-size:12px;">${f.medicines.map(escapeHtml).join(", ")}</p>
          <p style="color:#475569;font-size:12px;">Source: ${escapeHtml(f.source)}</p>
          ${f.evidence_profile ? `<p style="color:#475569;font-size:12px;">Evidence profile: ${escapeHtml(f.evidence_profile)}</p>` : ""}
          ${citationsHtml}
        </div>`
    })
    .join("")

  // Patient demographics
  const genderLabel = patient.gender ? patient.gender.charAt(0).toUpperCase() + patient.gender.slice(1) : "Not specified"
  const ageStr = patient.age ? `${patient.age} years` : "Not specified"

  // Vitals rows
  const vitals: [string, string][] = []
  if (patient.systolic_bp && patient.diastolic_bp) vitals.push(["Blood Pressure", `${patient.systolic_bp}/${patient.diastolic_bp} mmHg`])
  if (patient.heart_rate) vitals.push(["Heart Rate", `${patient.heart_rate} bpm`])
  if (patient.spo2) vitals.push(["SpO2", `${patient.spo2}%`])
  if (patient.fasting_blood_sugar) vitals.push(["Fasting Blood Sugar", `${patient.fasting_blood_sugar} mg/dL`])
  if (patient.serum_creatinine) vitals.push(["Serum Creatinine", `${patient.serum_creatinine} mg/dL`])
  if (patient.weight_kg) vitals.push(["Weight", `${patient.weight_kg} kg`])

  const vitalsHtml = vitals.length > 0
    ? `<table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
        <tr style="background:#f0fdfa;"><th colspan="2" style="text-align:left;padding:8px;color:#0D9488;font-size:14px;">Vitals & Measurements</th></tr>
        ${vitals.map(([k, v]) => `<tr><td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;color:#475569;font-size:13px;">${escapeHtml(k)}</td><td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-weight:600;font-size:13px;">${escapeHtml(v)}</td></tr>`).join("")}
       </table>`
    : ""

  // Conditions
  const conditionsHtml = patient.conditions.length > 0
    ? `<p style="margin:4px 0;"><strong>Known Conditions:</strong> ${patient.conditions.map(escapeHtml).join(", ")}</p>`
    : ""

  // Medicine list
  const sourceLabel: Record<string, string> = { doctor: "👨‍⚕️ Doctor", medical_shop: "🏪 Pharmacy", self: "🙋 Self-prescribed" }
  const medsHtml = medicines.length > 0
    ? `<table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
        <tr style="background:#f0fdfa;">
          <th style="text-align:left;padding:8px;color:#0D9488;font-size:14px;">Medicine</th>
          <th style="text-align:left;padding:8px;color:#0D9488;font-size:14px;">Dosage</th>
          <th style="text-align:left;padding:8px;color:#0D9488;font-size:14px;">Source</th>
        </tr>
        ${medicines.map((m) => {
          const src = getMedicinePrescriberSource(m, prescriberMap) ?? "unknown"
          return `<tr>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;"><strong>${escapeHtml(getMedicineDisplayName(m))}</strong><br/><span style="color:#64748B;font-size:11px;">${escapeHtml(m.generic_name)}</span>${m.entry_origin === "manual" ? `<br/><span style="color:#B45309;font-size:11px;">${escapeHtml("Manual entry")}</span>` : ""}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;">${escapeHtml(m.dosage_form || "—")}</td>
            <td style="padding:6px 8px;border-bottom:1px solid #e5e7eb;font-size:13px;">${sourceLabel[src] ?? src}</td>
          </tr>`
        }).join("")}
       </table>`
    : ""

  // ACB section
  const acbHtml = content.acb_section && content.acb_section.score > 0
    ? `<div class="acb-box" style="background:${content.acb_section.score >= 3 ? "#FFEBEE" : "#FFF8E1"};border:1px solid ${content.acb_section.score >= 3 ? "#EF9A9A" : "#FFE082"};border-radius:8px;padding:12px;margin-bottom:16px;">
        <strong>Anticholinergic Burden (ACB) Score: ${content.acb_section.score}</strong>
        <p style="margin:4px 0;">${escapeHtml(content.acb_section.risk)}</p>
        ${content.acb_section.drugs.length > 0 ? `<p style="color:#666;font-size:12px;">Drugs: ${content.acb_section.drugs.map(escapeHtml).join(", ")}</p>` : ""}
        ${(content.acb_section.citations ?? []).map((citation) => {
          const links = [
            citation.source_url ? `<a href="${escapeHtml(citation.source_url)}">Open source</a>` : "",
            citation.reference_url ? `<a href="${escapeHtml(citation.reference_url)}">${
              citation.reference_url_type === "pubmed_search" || citation.reference_url_type === "scholar_search"
                ? "Search reference"
                : "Open reference"
            }</a>` : "",
          ].filter(Boolean).join(" · ")

          return `
            <div style="margin-top:8px;padding:8px;border:1px solid #e5e7eb;border-radius:8px;background:#fff;">
              <p style="margin:0 0 4px 0;font-weight:700;">${escapeHtml(citation.source_label)}</p>
              <p style="margin:0 0 4px 0;">${escapeHtml(citation.evidence)}</p>
              ${citation.evidence_scope_label ? `<p style="margin:0 0 4px 0;color:#64748B;font-size:12px;">Evidence scope: ${escapeHtml(citation.evidence_scope_label)}</p>` : ""}
              ${citation.reference ? `<p style="margin:0 0 4px 0;color:#64748B;font-size:12px;">Reference: ${escapeHtml(citation.reference)}</p>` : ""}
              ${links ? `<p style="margin:0;color:#2563EB;font-size:12px;">${links}</p>` : ""}
            </div>`
        }).join("")}
       </div>`
    : ""

  // Self-prescribed warning
  const selfWarnHtml = content.self_prescribed_warning
    ? `<div class="warn-box" style="background:#FFF8E1;border:1px solid #FFE082;border-radius:8px;padding:12px;margin-bottom:16px;">
        <strong>⚠️ Self-Medication Warning</strong>
        <p>${escapeHtml(content.self_prescribed_warning)}</p>
       </div>`
    : ""

  // Personalized advice from LLM
  const personalizedAdvice = content?.personalized_advice ?? undefined
  const personalizedHtml = personalizedAdvice
    ? `<div style="background:#EEF2FF;border:2px solid #818CF8;border-radius:8px;padding:14px;margin-bottom:16px;">
        <strong style="color:#4338CA;">🩺 Personalized Advice for You</strong>
        <p style="color:#3730A3;line-height:1.6;">${escapeHtml(personalizedAdvice)}</p>
       </div>`
    : ""

  return `<!DOCTYPE html>
<html>
<head><meta charset="utf-8"/><style>
  @page { margin: 20mm 15mm; }
  body { font-family: -apple-system, sans-serif; padding: 0; margin: 0; color: #1E293B; font-size: 14px; line-height: 1.5; }
  h1 { color: #0D9488; margin-bottom: 4px; }
  h2 { color: #334155; font-size: 16px; margin-top: 20px; margin-bottom: 8px; border-bottom: 2px solid #0D9488; padding-bottom: 4px; page-break-after: avoid; }
  table { page-break-inside: auto; }
  tr { page-break-inside: avoid; page-break-after: auto; }
  .finding-card { page-break-inside: avoid; }
  .summary { background: #f0fdfa; padding: 16px; border-radius: 12px; margin-bottom: 16px; page-break-inside: avoid; }
  .acb-box { page-break-inside: avoid; }
  .warn-box { page-break-inside: avoid; }
  .disclaimer { color: #94A3B8; font-size: 11px; margin-top: 20px; border-top: 1px solid #e5e7eb; padding-top: 12px; }
  .timestamp { color: #94A3B8; font-size: 11px; }
  p { orphans: 3; widows: 3; }
</style></head>
<body>
  <h1>SAHAYAK Safety Report</h1>
  <p class="timestamp">Generated: ${new Date().toLocaleDateString("en-IN", { day: "numeric", month: "long", year: "numeric", hour: "2-digit", minute: "2-digit" })}</p>

  <h2>Patient Information</h2>
  <p><strong>${escapeHtml(patient.name || "Unknown")}</strong> · ${escapeHtml(ageStr)} · ${escapeHtml(genderLabel)}</p>
  ${conditionsHtml}
  ${vitalsHtml}

  <h2>Medicines (${medicines.length})</h2>
  ${medsHtml || "<p>No medicines recorded.</p>"}

  <div class="summary">
    <strong>Summary</strong>
    <p>${escapeHtml(content.patient_summary ?? "")}</p>
  </div>

  ${acbHtml}
  ${selfWarnHtml}
  ${personalizedHtml}

  ${findings.length > 0 ? `<h2>Safety Findings (${findings.length})</h2>${findingsHtml}` : "<h2>Safety Findings</h2><p>✅ No significant interactions found.</p>"}

  ${content.disclaimer ? `<p class="disclaimer">${escapeHtml(content.disclaimer)}</p>` : ""}
</body>
</html>`
}

function escapeHtml(str: string): string {
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 32 },

  langToggle: {
    flexDirection: "row", gap: 8, marginBottom: 16,
    backgroundColor: COLORS.surface, borderRadius: 12, padding: 4,
  },
  langBtn: {
    flex: 1, paddingVertical: 8, paddingHorizontal: 6,
    borderRadius: 10, alignItems: "center",
  },
  langBtnActive: { backgroundColor: COLORS.primary },
  langBtnText: { fontSize: 13, fontWeight: "700", color: COLORS.slate },
  langBtnTextActive: { color: "#FFF" },

  summaryCard: {
    padding: 18, borderRadius: 18, marginBottom: 16,
    backgroundColor: `${COLORS.primary}14`,
    borderWidth: 2, borderColor: `${COLORS.primary}30`,
  },
  summaryTitle: { fontSize: 16, fontWeight: "800", color: COLORS.primary, marginBottom: 8 },
  summaryText: { fontSize: 16, lineHeight: 26, color: COLORS.charcoal },

  section: { marginBottom: 16 },
  sectionLabel: {
    fontSize: 18, fontWeight: "800", color: COLORS.charcoal,
    marginBottom: 12, letterSpacing: -0.3,
  },

  selfWarnBox: {
    flexDirection: "row", gap: 10, alignItems: "flex-start",
    padding: 14, borderRadius: 14, marginBottom: 14,
    backgroundColor: "#FFF8E1", borderWidth: 2, borderColor: "#FFE082",
  },
  selfWarnIcon: { fontSize: 22 },
  selfWarnText: { flex: 1, fontSize: 14, color: "#B45309", lineHeight: 20 },

  personalizedBox: {
    flexDirection: "row", gap: 10, alignItems: "flex-start",
    padding: 16, borderRadius: 16, marginBottom: 14,
    backgroundColor: "#EEF2FF", borderWidth: 2, borderColor: "#818CF8",
  },
  personalizedIcon: { fontSize: 24 },
  personalizedTitle: { fontSize: 15, fontWeight: "800", color: "#4338CA", marginBottom: 4 },
  personalizedText: { fontSize: 14, color: "#3730A3", lineHeight: 22 },

  prescriberBox: {
    padding: 16, borderRadius: 16, marginBottom: 14,
    backgroundColor: "#F8FAFC", borderWidth: 1.5, borderColor: COLORS.border, gap: 8,
  },
  prescriberTitle: { fontSize: 15, fontWeight: "800", color: COLORS.charcoal, marginBottom: 4 },
  prescriberRow: {
    flexDirection: "row", justifyContent: "space-between", alignItems: "center",
    paddingVertical: 4,
  },
  prescriberNameWrap: { flex: 1, gap: 4, paddingRight: 12 },
  prescriberMedName: { flex: 1, fontSize: 14, fontWeight: "600", color: COLORS.charcoal },
  prescriberManualBadge: {
    alignSelf: "flex-start",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 8,
    backgroundColor: "#FFF7ED",
  },
  prescriberManualBadgeText: { fontSize: 11, fontWeight: "700", color: "#B45309" },
  prescriberBadge: {
    paddingHorizontal: 10, paddingVertical: 4, borderRadius: 8,
    backgroundColor: "#F1F5F9",
  },
  prescriberBadgeText: { fontSize: 12, fontWeight: "700", color: COLORS.slate },

  noInteractionBox: {
    alignItems: "center", padding: 32, borderRadius: 20,
    backgroundColor: "#E8F5E9", borderWidth: 2, borderColor: "#A5D6A7",
    marginBottom: 16,
  },
  noInteractionIcon: { fontSize: 52, marginBottom: 12 },
  noInteractionTitle: { fontSize: 22, fontWeight: "800", color: "#2E7D32", marginBottom: 8 },
  noInteractionBody: { fontSize: 15, color: "#388E3C", textAlign: "center", lineHeight: 22 },

  disclaimer: {
    fontSize: 12, color: COLORS.slate, textAlign: "center", lineHeight: 18, marginTop: 8,
  },

  pdfBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    marginTop: 16, paddingVertical: 14, borderRadius: 14,
    borderWidth: 2, borderColor: COLORS.primary,
    backgroundColor: `${COLORS.primary}10`,
  },
  pdfBtnText: { fontSize: 16, fontWeight: "700", color: COLORS.primary },

  actionBar: {
    position: "absolute", bottom: 0, left: 0, right: 0,
    flexDirection: "row", gap: 10, paddingHorizontal: 20, paddingVertical: 14,
    backgroundColor: COLORS.cream, borderTopWidth: 1, borderTopColor: COLORS.border,
  },
  shareBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    borderRadius: 14, borderWidth: 2, borderColor: COLORS.primary,
    paddingVertical: 14, backgroundColor: `${COLORS.primary}10`,
  },
  shareBtnText: { fontSize: 16, fontWeight: "700", color: COLORS.primary },
  doctorBtn: {
    flex: 2, flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8,
    borderRadius: 14, backgroundColor: "#2E7D32", paddingVertical: 14,
    shadowColor: "#2E7D32", shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.3, shadowRadius: 6, elevation: 4,
  },
  doctorBtnText: { fontSize: 16, fontWeight: "700", color: "#FFF" },
  restartBtn: {
    width: 52, alignItems: "center", justifyContent: "center",
    borderRadius: 14, borderWidth: 2, borderColor: COLORS.border,
  },

  centerState: { flex: 1, alignItems: "center", justifyContent: "center", gap: 16 },
  centerIcon: { fontSize: 56 },
  centerTitle: { fontSize: 20, fontWeight: "700", color: COLORS.charcoal },
  retryBtn: { paddingHorizontal: 24, paddingVertical: 14, borderRadius: 14, backgroundColor: COLORS.primary },
  retryBtnText: { color: "#FFF", fontWeight: "700", fontSize: 17 },
})
