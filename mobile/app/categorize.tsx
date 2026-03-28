import { useState } from "react"
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Alert,
  ActivityIndicator,
  Image,
} from "react-native"
import { useRouter } from "expo-router"
import Animated, { FadeInDown } from "react-native-reanimated"
import * as Haptics from "expo-haptics"
import { Ionicons } from "@expo/vector-icons"
import { StepShell } from "../components/layout/StepShell"
import { VoiceBar } from "../components/ui/VoiceBar"
import { useAppStore } from "../hooks/useAppStore"
import { useTTS } from "../hooks/useVoice"
import { safetyCheck, generateReport } from "../lib/api"
import {
  buildPrescriberInfoByName,
  getMedicineDisplayName,
  getMedicinePrescriberSource,
} from "../lib/medicines"
import { COLORS, PRESCRIBER_SOURCES } from "../lib/constants"
import { t } from "../lib/i18n"
import type { ExtractedDrug, PrescriberSource } from "../types/sahayak"

// ── Medicine Card with image + 3 source buttons ──────────────────────────────
function MedicineCard({
  drug,
  index,
  selectedSource,
  onSelectSource,
  lang,
}: {
  drug: ExtractedDrug
  index: number
  selectedSource?: PrescriberSource
  onSelectSource: (source: PrescriberSource) => void
  lang: string
}) {
  const imageUri = drug.image_uri
  const displayName = getMedicineDisplayName(drug)
  const subtitle = drug.brand_name && drug.generic_name && drug.brand_name !== drug.generic_name
    ? drug.generic_name
    : undefined
  const isAyurvedic = drug.medicine_type === "ayurvedic"
  const isManual = drug.entry_origin === "manual"

  return (
    <Animated.View entering={FadeInDown.delay(index * 80).duration(300)}>
      <View style={cardStyles.card}>
        {/* Top: Image + Name Row */}
        <View style={cardStyles.topRow}>
          {imageUri ? (
            <Image source={{ uri: imageUri }} style={cardStyles.image} />
          ) : (
            <View style={[cardStyles.imagePlaceholder, isAyurvedic && { backgroundColor: "#E8F5E9" }]}>
              <Text style={cardStyles.placeholderEmoji}>{isAyurvedic ? "🌿" : "💊"}</Text>
            </View>
          )}
          <View style={cardStyles.nameCol}>
            <Text style={cardStyles.drugName} numberOfLines={2}>{displayName}</Text>
            {subtitle && <Text style={cardStyles.genericName}>{subtitle}</Text>}
            {drug.dosage_form ? (
              <View style={[cardStyles.typeBadge, isAyurvedic && { backgroundColor: "#E8F5E9" }]}>
                <Text style={[cardStyles.typeText, isAyurvedic && { color: "#2E7D32" }]}>
                  {isAyurvedic ? "🌿 " : "💊 "}{drug.dosage_form}
                </Text>
              </View>
            ) : null}
            {isManual ? (
              <View style={cardStyles.manualBadge}>
                <Text style={cardStyles.manualBadgeText}>{t("manual_entry", lang)}</Text>
              </View>
            ) : null}
          </View>
        </View>

        {/* Source question */}
        <Text style={cardStyles.question}>{t("who_prescribed", lang)}</Text>

        {/* 3 Source buttons */}
        <View style={cardStyles.sourceRow}>
          {PRESCRIBER_SOURCES.map((src) => {
            const isSelected = selectedSource === src.value
            const labelKey = src.value === "self" ? "self_prescribed" : src.value
            return (
              <TouchableOpacity
                key={src.value}
                style={[cardStyles.sourceBtn, isSelected && cardStyles.sourceBtnActive]}
                onPress={() => onSelectSource(src.value as PrescriberSource)}
                activeOpacity={0.75}
              >
                <Text style={cardStyles.sourceIcon}>{src.icon}</Text>
                <Text
                  style={[cardStyles.sourceLabel, isSelected && cardStyles.sourceLabelActive]}
                  numberOfLines={2}
                >
                  {t(labelKey, lang)}
                </Text>
                {isSelected && (
                  <View style={cardStyles.checkCircle}>
                    <Ionicons name="checkmark" size={12} color="#FFF" />
                  </View>
                )}
              </TouchableOpacity>
            )
          })}
        </View>
      </View>
    </Animated.View>
  )
}

const cardStyles = StyleSheet.create({
  card: {
    backgroundColor: "#FFF",
    borderRadius: 18,
    padding: 16,
    marginBottom: 16,
    borderWidth: 1.5,
    borderColor: COLORS.border,
    gap: 14,
  },
  topRow: { flexDirection: "row", gap: 14, alignItems: "center" },
  image: { width: 72, height: 72, borderRadius: 14, backgroundColor: COLORS.surface },
  imagePlaceholder: {
    width: 72, height: 72, borderRadius: 14, backgroundColor: "#F0F4FF",
    alignItems: "center", justifyContent: "center",
  },
  placeholderEmoji: { fontSize: 30 },
  nameCol: { flex: 1, gap: 4 },
  drugName: { fontSize: 18, fontWeight: "800", color: COLORS.charcoal },
  genericName: { fontSize: 14, color: COLORS.slate },
  typeBadge: {
    alignSelf: "flex-start", paddingHorizontal: 8, paddingVertical: 3,
    borderRadius: 8, backgroundColor: "#EEF2FF", marginTop: 2,
  },
  typeText: { fontSize: 11, fontWeight: "700", color: "#4338CA" },
  manualBadge: {
    alignSelf: "flex-start",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 8,
    backgroundColor: "#FFF7ED",
    marginTop: 2,
  },
  manualBadgeText: { fontSize: 11, fontWeight: "700", color: "#B45309" },
  question: { fontSize: 14, fontWeight: "600", color: COLORS.slate },
  sourceRow: { flexDirection: "row", gap: 10 },
  sourceBtn: {
    flex: 1, alignItems: "center", justifyContent: "center",
    paddingVertical: 14, paddingHorizontal: 6, borderRadius: 14,
    borderWidth: 2, borderColor: COLORS.border, backgroundColor: "#FAFAFA",
    gap: 6, position: "relative",
  },
  sourceBtnActive: { borderColor: COLORS.primary, backgroundColor: `${COLORS.primary}10` },
  sourceIcon: { fontSize: 26 },
  sourceLabel: { fontSize: 12, fontWeight: "700", color: COLORS.charcoal, textAlign: "center" },
  sourceLabelActive: { color: COLORS.primary },
  checkCircle: {
    position: "absolute", top: 6, right: 6, width: 18, height: 18,
    borderRadius: 9, backgroundColor: COLORS.primary, alignItems: "center", justifyContent: "center",
  },
})

// ── Screen ────────────────────────────────────────────────────────────────────

export default function CategorizeScreen() {
  const router = useRouter()
  const lang = useAppStore((s) => s.language)
  const {
    confirmedMedicines,
    patientInfo,
    allopathicImageUris,
    ayurvedicImageUris,
    updatePrescriberSource,
    prescriberMap,
    setSafetyReport,
    setSafetyCheckResult,
  } = useAppStore()

  const tts = useTTS()
  const [loading, setLoading] = useState(false)
  const [loadingStep, setLoadingStep] = useState("")

  function handleSelectSource(medicineId: string, source: PrescriberSource) {
    updatePrescriberSource(medicineId, source)
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
  }

  async function handleGenerateReport() {
    setLoading(true)
    try {
      // Phase 2: safety check + report generation (after user confirmed medicines)
      const alloDrugs = confirmedMedicines.filter((m) => m.medicine_type !== "ayurvedic")
      const ayurDrugs = confirmedMedicines.filter((m) => m.medicine_type === "ayurvedic")
      const drugs = alloDrugs.map((d) => d.generic_name || d.brand_name)
      const herbs = ayurDrugs.map((d) => d.generic_name || d.brand_name)
      const prescriberInfo = buildPrescriberInfoByName(confirmedMedicines, prescriberMap)

      // Step 1: Safety check
      setLoadingStep(t("step_interact", lang))
      const safetyResult = await safetyCheck({
        drugs,
        herbs,
        age: patientInfo.age ?? 65,
        gender: patientInfo.gender || undefined,
        conditions: patientInfo.conditions.length > 0 ? patientInfo.conditions : undefined,
        prescriber_info: prescriberInfo,
        weight_kg: patientInfo.weight_kg ?? undefined,
        systolic_bp: patientInfo.systolic_bp,
        diastolic_bp: patientInfo.diastolic_bp,
        fasting_blood_sugar: patientInfo.fasting_blood_sugar,
        spo2: patientInfo.spo2,
        heart_rate: patientInfo.heart_rate,
        serum_creatinine: patientInfo.serum_creatinine,
      })
      setSafetyCheckResult(safetyResult)

      // Step 2: Generate report
      setLoadingStep(t("step_report", lang))
      const report = await generateReport({
        safety_report: safetyResult,
        patient_info: {
          name: patientInfo.name,
          age: patientInfo.age,
          gender: patientInfo.gender,
          conditions: patientInfo.conditions,
          weight_kg: patientInfo.weight_kg,
          prescriber_info: prescriberInfo,
          // Vitals for personalized report
          systolic_bp: patientInfo.systolic_bp,
          diastolic_bp: patientInfo.diastolic_bp,
          fasting_blood_sugar: patientInfo.fasting_blood_sugar,
          spo2: patientInfo.spo2,
          heart_rate: patientInfo.heart_rate,
          serum_creatinine: patientInfo.serum_creatinine,
        },
        language: lang,
      })

      setSafetyReport(report)
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
      router.push("/report")
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("error_occurred", lang)
      Alert.alert(t("error_occurred", lang), msg)
    } finally {
      setLoading(false)
      setLoadingStep("")
    }
  }

  const allSet = confirmedMedicines.every((m) => !!(m.medicine_id && prescriberMap[m.medicine_id]))
  const setCount = confirmedMedicines.filter((m) => !!(m.medicine_id && prescriberMap[m.medicine_id])).length

  return (
    <StepShell step={6} showBack>
      <ScrollView contentContainerStyle={styles.scroll} showsVerticalScrollIndicator={false}>
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>{t("who_prescribed", lang)}</Text>
          <Text style={styles.subtitle}>{t("select_source_for_each", lang)}</Text>
          <VoiceBar
            text={t("who_prescribed", lang)}
            language={lang}
            ttsPlaying={tts.playing}
            onSpeak={tts.speak}
            onStopSpeak={tts.stop}
          />
        </View>

        {/* Progress */}
        <View style={styles.progressBar}>
          <View style={[styles.progressFill, { width: `${(setCount / Math.max(confirmedMedicines.length, 1)) * 100}%` }]} />
        </View>
        <Text style={styles.progressLabel}>
          {setCount} / {confirmedMedicines.length} {t("categorized", lang)}
        </Text>

        {/* Source photo thumbnails */}
        {(allopathicImageUris.length > 0 || ayurvedicImageUris.length > 0) && (
          <View style={styles.photoStrip}>
            {[...allopathicImageUris, ...ayurvedicImageUris].map((uri, i) => (
              <Image key={i} source={{ uri }} style={styles.photoThumb} />
            ))}
            <Text style={styles.photoLabel}>{t("your_photos", lang)}</Text>
          </View>
        )}

        {/* Medicine cards with 3 source buttons each */}
        {confirmedMedicines.map((drug, i) => (
          <MedicineCard
            key={drug.medicine_id ?? `${drug.generic_name}-${i}`}
            drug={drug}
            index={i}
            selectedSource={getMedicinePrescriberSource(drug, prescriberMap)}
            onSelectSource={(src) => handleSelectSource(drug.medicine_id ?? `${drug.generic_name}-${i}`, src)}
            lang={lang}
          />
        ))}

        {confirmedMedicines.length === 0 && (
          <View style={styles.emptyBox}>
            <Ionicons name="medical" size={40} color={COLORS.border} />
            <Text style={styles.emptyText}>{t("no_medicines_detected", lang)}</Text>
          </View>
        )}

        <View style={{ height: 120 }} />
      </ScrollView>

      {/* Footer CTA */}
      <View style={styles.footer}>
        {!allSet && (
          <Text style={styles.footerHint}>
            {confirmedMedicines.length - setCount} {t("needs_categorisation", lang)}
          </Text>
        )}
        <TouchableOpacity
          style={[styles.ctaBtn, loading && styles.ctaBtnLoading]}
          onPress={handleGenerateReport}
          disabled={loading}
          activeOpacity={0.85}
        >
          {loading ? (
            <><ActivityIndicator color="#FFF" /><Text style={styles.ctaBtnText}>{loadingStep}</Text></>
          ) : (
            <>
              <Ionicons name="shield-checkmark" size={22} color="#FFF" />
              <Text style={styles.ctaBtnText}>{t("generate_report", lang)} →</Text>
            </>
          )}
        </TouchableOpacity>
      </View>
    </StepShell>
  )
}

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 32 },
  header: { marginBottom: 16, gap: 4 },
  title: { fontSize: 26, fontWeight: "800", color: COLORS.charcoal, letterSpacing: -0.5 },
  subtitle: { fontSize: 15, color: COLORS.slate, marginBottom: 4 },

  progressBar: {
    height: 8, borderRadius: 4,
    backgroundColor: COLORS.border, overflow: "hidden", marginBottom: 6,
  },
  progressFill: { height: "100%", borderRadius: 4, backgroundColor: COLORS.primary },
  progressLabel: { fontSize: 13, color: COLORS.slate, marginBottom: 16 },

  photoStrip: {
    flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 20,
    paddingBottom: 12, borderBottomWidth: 1, borderBottomColor: COLORS.border,
  },
  photoThumb: { width: 48, height: 48, borderRadius: 10, backgroundColor: COLORS.surface },
  photoLabel: { fontSize: 12, color: COLORS.slate, fontWeight: "600", marginLeft: 4 },

  emptyBox: { alignItems: "center", justifyContent: "center", paddingVertical: 60, gap: 12 },
  emptyText: { fontSize: 16, color: COLORS.slate, textAlign: "center" },

  footer: {
    position: "absolute", bottom: 0, left: 0, right: 0,
    paddingHorizontal: 20, paddingVertical: 16,
    backgroundColor: COLORS.cream, borderTopWidth: 1, borderTopColor: COLORS.border, gap: 8,
  },
  footerHint: { fontSize: 13, color: COLORS.slate, textAlign: "center" },
  ctaBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 10,
    backgroundColor: COLORS.primary, borderRadius: 16, minHeight: 60,
    shadowColor: COLORS.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3, shadowRadius: 8, elevation: 6,
  },
  ctaBtnLoading: { opacity: 0.7 },
  ctaBtnText: { color: "#FFF", fontSize: 18, fontWeight: "700" },
})
