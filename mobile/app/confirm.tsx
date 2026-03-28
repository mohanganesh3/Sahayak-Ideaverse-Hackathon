import { useState } from "react"
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Alert,
  Image,
  TextInput,
} from "react-native"
import { useRouter } from "expo-router"
import Animated, {
  FadeInDown,
  FadeInUp,
  FadeOutRight,
  Layout,
  useSharedValue,
  useAnimatedStyle,
  withTiming,
} from "react-native-reanimated"
import * as Haptics from "expo-haptics"
import { Ionicons } from "@expo/vector-icons"
import { StepShell } from "../components/layout/StepShell"
import { VoiceBar } from "../components/ui/VoiceBar"
import { useAppStore } from "../hooks/useAppStore"
import { useTTS, useSTT } from "../hooks/useVoice"
import { resolveManualMedicine } from "../lib/api"
import {
  buildSourceImageKey,
  getMedicineDisplayName,
  withMedicineIdentityList,
} from "../lib/medicines"
import { COLORS } from "../lib/constants"
import { t } from "../lib/i18n"
import type { ExtractedDrug, ImageProcessingResult, OcrFailure } from "../types/sahayak"

// ── Confidence Arc Component ──────────────────────────────────────────────────
function ConfidenceRing({ value }: { value: number }) {
  const pct = Math.round(value * 100)
  const color =
    pct >= 85 ? "#2E7D32" : pct >= 60 ? COLORS.saffron : "#C62828"
  return (
    <View style={ringStyles.container}>
      <View style={[ringStyles.ring, { borderColor: color }]}>
        <Text style={[ringStyles.label, { color }]}>{pct}%</Text>
      </View>
    </View>
  )
}

const ringStyles = StyleSheet.create({
  container: { alignItems: "center", justifyContent: "center" },
  ring: {
    width: 54,
    height: 54,
    borderRadius: 27,
    borderWidth: 3,
    alignItems: "center",
    justifyContent: "center",
  },
  label: { fontSize: 13, fontWeight: "800" },
})

// ── Medicine Card ─────────────────────────────────────────────────────────────
function MedicineCard({
  drug,
  onRemove,
  index,
  lang,
}: {
  drug: ExtractedDrug
  onRemove: () => void
  index: number
  lang: string
}) {
  const scale = useSharedValue(1)
  const animStyle = useAnimatedStyle(() => ({
    transform: [{ scale: scale.value }],
  }))

  return (
    <Animated.View
      entering={FadeInDown.delay(index * 80).duration(300)}
      exiting={FadeOutRight.duration(220).duration(300)}
      layout={Layout.duration(300)}
      style={animStyle}
    >
      <View style={cardStyles.card}>
        {/* Color stripe */}
        <View style={[cardStyles.stripe, { backgroundColor: COLORS.primary }]} />

        <View style={cardStyles.body}>
          {/* Top row: names + confidence */}
          <View style={cardStyles.topRow}>
            <View style={cardStyles.nameBlock}>
              <Text style={cardStyles.brandName} numberOfLines={1}>
                {drug.brand_name || drug.generic_name}
              </Text>
              {drug.brand_name && drug.generic_name && drug.brand_name !== drug.generic_name && (
                <Text style={cardStyles.genericName} numberOfLines={1}>
                  {drug.generic_name}
                </Text>
              )}
            </View>
            <ConfidenceRing value={drug.confidence ?? 0} />
          </View>

          {/* Ingredients */}
          {drug.active_ingredients && drug.active_ingredients.length > 0 && (
            <View style={cardStyles.ingredientsRow}>
              {drug.active_ingredients.slice(0, 3).map((ing, i) => (
                <View key={i} style={cardStyles.ingredientChip}>
                  <Text style={cardStyles.ingredientText}>
                    {ing.name}{ing.dose ? ` ${ing.dose}` : ""}
                  </Text>
                </View>
              ))}
              {drug.active_ingredients.length > 3 && (
                <Text style={cardStyles.moreText}>
                  +{drug.active_ingredients.length - 3} more
                </Text>
              )}
            </View>
          )}

          {/* Image thumbnail if available */}
          {drug.image_uri && (
            <Image source={{ uri: drug.image_uri }} style={cardStyles.thumb} resizeMode="cover" />
          )}

          {/* Dosage form */}
          <View style={cardStyles.metaRow}>
            {drug.dosage_form && (
              <View style={cardStyles.formBadge}>
                <Text style={cardStyles.formBadgeText}>{drug.dosage_form}</Text>
              </View>
            )}
          </View>
        </View>

        {/* Remove button */}
        <TouchableOpacity
          style={cardStyles.removeBtn}
          onPress={() => {
            scale.value = withTiming(0.92, { duration: 120 }, () => {
              scale.value = withTiming(1, { duration: 120 })
            })
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)
            onRemove()
          }}
          accessibilityLabel={`Remove ${drug.brand_name}`}
        >
          <Ionicons name="trash-outline" size={18} color="#EF9A9A" />
        </TouchableOpacity>
      </View>
    </Animated.View>
  )
}

const cardStyles = StyleSheet.create({
  card: {
    flexDirection: "row",
    borderRadius: 16,
    marginBottom: 12,
    overflow: "hidden",
    backgroundColor: COLORS.surface,
    borderWidth: 1.5,
    borderColor: "transparent",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 6,
    elevation: 2,
  },
  stripe: { width: 5, borderRadius: 4 },
  body: { flex: 1, padding: 14, gap: 8 },
  topRow: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  nameBlock: { flex: 1 },
  brandName: {
    fontSize: 17,
    fontWeight: "800",
    color: COLORS.charcoal,
    letterSpacing: -0.2,
  },
  genericName: { fontSize: 13, color: COLORS.slate, marginTop: 2 },
  ingredientsRow: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  ingredientChip: {
    borderRadius: 8,
    paddingHorizontal: 8,
    paddingVertical: 3,
    backgroundColor: `${COLORS.primary}18`,
  },
  ingredientText: { fontSize: 12, color: COLORS.primary, fontWeight: "600" },
  moreText: { fontSize: 12, color: COLORS.slate, alignSelf: "center" },
  metaRow: { flexDirection: "row", gap: 8, flexWrap: "wrap" },
  formBadge: {
    paddingHorizontal: 10,
    paddingVertical: 3,
    borderRadius: 8,
    backgroundColor: `${COLORS.saffron}22`,
  },
  formBadgeText: { fontSize: 12, color: "#B45309", fontWeight: "600" },
  thumb: {
    width: "100%", height: 80, borderRadius: 10, backgroundColor: "#E0E0E0", marginTop: 4,
  },
  removeBtn: {
    paddingHorizontal: 12,
    paddingVertical: 12,
    alignSelf: "flex-start",
    marginTop: 12,
  },
})

interface BaseReviewEntry {
  id: string
  source_kind: "scan_failure" | "removed_detection"
  type: "allopathic" | "ayurvedic"
  imageIndex: number
  imageUri?: string
  sourceImageKey: string
  reason: string
  status: "pending" | "ignored"
  failureType: "ocr" | "extraction"
  manualName: string
  resolving: boolean
}

interface ScanFailureReviewEntry extends BaseReviewEntry {
  source_kind: "scan_failure"
}

interface RemovedDetectionReviewEntry extends Omit<BaseReviewEntry, "failureType"> {
  source_kind: "removed_detection"
  originalDrug: ExtractedDrug
  originalMedicineId: string
}

type ReviewEntry = ScanFailureReviewEntry | RemovedDetectionReviewEntry

function getImageKey(
  type: "allopathic" | "ayurvedic",
  imageIndex: number,
  imageUri?: string
) {
  return buildSourceImageKey(type, imageIndex, imageUri)
}

function SummaryStatCard({
  icon,
  iconColor,
  count,
  label,
  warning = false,
}: {
  icon: keyof typeof Ionicons.glyphMap
  iconColor: string
  count: number
  label: string
  warning?: boolean
}) {
  return (
    <View style={[styles.summaryCard, warning && styles.summaryCardWarning]}>
      <View style={[styles.summaryIconChip, warning && styles.summaryIconChipWarning]}>
        <Ionicons name={icon} size={18} color={iconColor} />
      </View>
      <Text style={[styles.summaryCount, warning && styles.summaryCountWarning]}>{count}</Text>
      <Text
        style={[styles.summaryLabel, warning && styles.summaryLabelWarning]}
        numberOfLines={2}
        adjustsFontSizeToFit
        minimumFontScale={0.8}
      >
        {label}
      </Text>
    </View>
  )
}

// ── Review Card (Manual review / removed items) ──────────────────────────────
function ReviewCard({
  entry,
  onRestoreOriginal,
  onRestoreIgnored,
  onManualNameChange,
  onResolve,
  onIgnore,
  onMicPress,
  sttRecording,
  sttTranscribing,
  lang,
}: {
  entry: ReviewEntry
  onRestoreOriginal?: () => void
  onRestoreIgnored?: () => void
  onManualNameChange?: (text: string) => void
  onResolve?: () => void
  onIgnore?: () => void
  onMicPress?: () => void
  sttRecording: boolean
  sttTranscribing: boolean
  lang: string
}) {
  const isRemovedEntry = entry.source_kind === "removed_detection"
  const isIgnored = entry.status === "ignored"
  const displayName = isRemovedEntry
    ? getMedicineDisplayName(entry.originalDrug)
    : `${entry.type === "ayurvedic" ? "Ayurvedic" : "Allopathic"} #${entry.imageIndex + 1}`
  const contextLabel = isIgnored
    ? t("ignored_from_scan", lang)
    : isRemovedEntry
    ? t("correction_pending", lang)
    : t("manual_review", lang)
  const promptText = isRemovedEntry ? t("type_or_speak_name", lang) : t("type_or_speak_name", lang)
  const canResolve = entry.manualName.trim().length > 0 && !entry.resolving

  return (
    <Animated.View entering={FadeInUp.duration(300)} layout={Layout.springify()}>
      <View style={rcStyles.card}>
        <View style={rcStyles.topRow}>
          {entry.imageUri ? (
            <Image source={{ uri: entry.imageUri }} style={rcStyles.image} />
          ) : (
            <View style={rcStyles.imagePlaceholder}>
              <Text style={{ fontSize: 20 }}>{entry.type === "ayurvedic" ? "🌿" : "💊"}</Text>
            </View>
          )}
          <View style={rcStyles.nameCol}>
            <Text style={isRemovedEntry ? rcStyles.removedName : rcStyles.reviewName}>{displayName}</Text>
            <Text style={rcStyles.reviewReason}>{contextLabel}</Text>
          </View>
          {isIgnored && onRestoreIgnored ? (
            <TouchableOpacity style={rcStyles.restoreBtn} onPress={onRestoreIgnored}>
              <Ionicons name="arrow-undo" size={16} color={COLORS.primary} />
              <Text style={rcStyles.restoreTxt}>{t("restore", lang)}</Text>
            </TouchableOpacity>
          ) : (
            <View style={rcStyles.reviewBadge}>
              <Text style={rcStyles.reviewBadgeText}>{contextLabel}</Text>
            </View>
          )}
        </View>

        {isIgnored ? (
          <Text style={rcStyles.promptText}>{t("ignored_from_scan", lang)}</Text>
        ) : (
          <>
            <View style={rcStyles.inputRow}>
              <TextInput
                style={rcStyles.input}
                placeholder={t("type_medicine_name", lang)}
                placeholderTextColor={COLORS.slate}
                value={entry.manualName}
                onChangeText={onManualNameChange}
                editable={!entry.resolving && !sttTranscribing}
              />
              <TouchableOpacity
                style={[rcStyles.micBtn, sttRecording && rcStyles.micBtnActive]}
                onPress={onMicPress}
                disabled={entry.resolving || sttTranscribing}
              >
                <Ionicons
                  name={sttRecording ? "stop" : "mic"}
                  size={18}
                  color={sttRecording ? "#FFF" : COLORS.primary}
                />
              </TouchableOpacity>
            </View>
            {sttTranscribing && (
              <Text style={rcStyles.transcribingHint}>Transcribing...</Text>
            )}
            <Text style={rcStyles.promptText}>{promptText}</Text>
            <TouchableOpacity
              style={[rcStyles.primaryBtn, !canResolve && rcStyles.primaryBtnDisabled]}
              onPress={onResolve}
              disabled={!canResolve}
            >
              <Ionicons name="checkmark-circle" size={18} color="#FFF" />
              <Text style={rcStyles.primaryBtnTxt}>
                {entry.resolving ? "..." : t("use_this_name", lang)}
              </Text>
            </TouchableOpacity>
            <View style={rcStyles.secondaryRow}>
              {isRemovedEntry && onRestoreOriginal ? (
                <TouchableOpacity
                  style={[rcStyles.secondaryBtn, rcStyles.restoreActionBtn]}
                  onPress={onRestoreOriginal}
                  disabled={entry.resolving}
                >
                  <Ionicons name="arrow-undo" size={18} color={COLORS.primary} />
                  <Text style={rcStyles.restoreActionBtnTxt}>{t("restore_original", lang)}</Text>
                </TouchableOpacity>
              ) : null}
              <TouchableOpacity
                style={[
                  rcStyles.secondaryBtn,
                  rcStyles.ignoreBtn,
                  !isRemovedEntry && rcStyles.secondaryBtnFull,
                ]}
                onPress={onIgnore}
                disabled={entry.resolving}
              >
                <Ionicons name="close-circle-outline" size={18} color="#92400E" />
                <Text style={rcStyles.ignoreBtnTxt}>{t("ignore_from_scan", lang)}</Text>
              </TouchableOpacity>
            </View>
          </>
        )}
      </View>
    </Animated.View>
  )
}

const rcStyles = StyleSheet.create({
  card: {
    backgroundColor: "#FFF5F5",
    borderRadius: 14,
    padding: 12,
    marginBottom: 10,
    borderWidth: 1.5,
    borderColor: "#FFCDD2",
    borderStyle: "dashed",
    gap: 10,
  },
  topRow: { flexDirection: "row", alignItems: "center", gap: 10 },
  image: { width: 48, height: 48, borderRadius: 10, backgroundColor: "#E0E0E0" },
  imagePlaceholder: {
    width: 48, height: 48, borderRadius: 10, backgroundColor: "#F0F0F0",
    alignItems: "center", justifyContent: "center",
  },
  nameCol: { flex: 1 },
  removedName: {
    fontSize: 15, fontWeight: "700", color: COLORS.slate,
    textDecorationLine: "line-through",
  },
  reviewName: {
    fontSize: 15,
    fontWeight: "800",
    color: COLORS.charcoal,
  },
  reviewReason: {
    fontSize: 12,
    color: COLORS.slate,
    marginTop: 4,
    lineHeight: 17,
  },
  restoreBtn: {
    flexDirection: "row", alignItems: "center", gap: 4,
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 10,
    backgroundColor: `${COLORS.primary}15`,
  },
  restoreTxt: { fontSize: 12, fontWeight: "700", color: COLORS.primary },
  reviewBadge: {
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: 10,
    backgroundColor: `${COLORS.saffron}18`,
  },
  reviewBadgeText: { fontSize: 12, fontWeight: "700", color: "#B45309" },
  inputRow: { flexDirection: "row", gap: 8, alignItems: "center" },
  input: {
    flex: 1, height: 42, borderRadius: 10, borderWidth: 1.5, borderColor: COLORS.border,
    paddingHorizontal: 12, fontSize: 14, color: COLORS.charcoal, backgroundColor: "#FFF",
  },
  micBtn: {
    width: 42, height: 42, borderRadius: 21, borderWidth: 1.5, borderColor: COLORS.primary,
    alignItems: "center", justifyContent: "center",
  },
  micBtnActive: { backgroundColor: "#C62828", borderColor: "#C62828" },
  transcribingHint: { fontSize: 12, color: COLORS.slate, fontStyle: "italic" },
  promptText: { fontSize: 12, color: COLORS.slate },
  primaryBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 6,
    backgroundColor: "#2E7D32", borderRadius: 10, paddingVertical: 10,
  },
  primaryBtnDisabled: { opacity: 0.45 },
  primaryBtnTxt: { color: "#FFF", fontSize: 14, fontWeight: "700" },
  secondaryRow: { flexDirection: "row", gap: 8 },
  secondaryBtn: {
    flex: 1,
    minHeight: 46,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
    borderRadius: 10,
    paddingHorizontal: 12,
  },
  secondaryBtnFull: { flex: 1 },
  restoreActionBtn: {
    backgroundColor: `${COLORS.primary}15`,
    borderWidth: 1.5,
    borderColor: `${COLORS.primary}40`,
  },
  restoreActionBtnTxt: { color: COLORS.primary, fontSize: 13, fontWeight: "700" },
  ignoreBtn: {
    backgroundColor: "#FFF7ED",
    borderWidth: 1.5,
    borderColor: "#FCD34D",
  },
  ignoreBtnTxt: { color: "#92400E", fontSize: 13, fontWeight: "700" },
})

// ── Screen ────────────────────────────────────────────────────────────────────

export default function ConfirmScreen() {
  const router = useRouter()
  const lang = useAppStore((s) => s.language)
  const {
    allopathicMedicines,
    ayurvedicMedicines,
    allopathicImageUris,
    ayurvedicImageUris,
    ocrFailures,
    allImageResults,
    scanMeta,
    setConfirmedMedicines,
    setAllopathicMedicines,
    setAyurvedicMedicines,
    setOcrFailures,
    setAllImageResults,
    setScanMeta,
  } = useAppStore()

  const tts = useTTS()
  const stt = useSTT()

  const [alloList, setAlloList] = useState<ExtractedDrug[]>(allopathicMedicines)
  const [ayurList, setAyurList] = useState<ExtractedDrug[]>(ayurvedicMedicines)
  const [imageResults, setImageResults] = useState<ImageProcessingResult[]>(() => {
    if (allImageResults.length > 0) return allImageResults
    return [
      ...allopathicImageUris.map((imageUri, imageIndex) => ({
        imageIndex,
        imageUri,
        type: "allopathic" as const,
        review_status: "manual_pending" as const,
        resolved_medicine_ids: [],
        pending_review_ids: [],
      })),
      ...ayurvedicImageUris.map((imageUri, imageIndex) => ({
        imageIndex,
        imageUri,
        type: "ayurvedic" as const,
        review_status: "manual_pending" as const,
        resolved_medicine_ids: [],
        pending_review_ids: [],
      })),
    ]
  })
  const [reviewItems, setReviewItems] = useState<ReviewEntry[]>(() => {
    if (allImageResults.length > 0) {
      return allImageResults
        .filter((result) => result.review_status === "manual_pending" || result.failureType)
        .map((result) => ({
          id: `${result.type}-${result.imageIndex}-${result.failureType ?? "ocr"}`,
          source_kind: "scan_failure" as const,
          type: result.type,
          imageIndex: result.imageIndex,
          imageUri: result.imageUri,
          sourceImageKey: getImageKey(result.type, result.imageIndex, result.imageUri),
          reason: result.failureReason ?? "This image needs manual review.",
          failureType: result.failureType ?? "ocr",
          manualName: "",
          resolving: false,
          status: "pending" as const,
        }))
    }

    const failureSource = ocrFailures.length > 0 ? ocrFailures : (scanMeta?.failedImages ?? [])
    return failureSource.map((failure) => ({
      id: `${failure.type}-${failure.imageIndex}-${failure.failureType}`,
      source_kind: "scan_failure" as const,
      type: failure.type,
      imageIndex: failure.imageIndex,
      imageUri: "imageUri" in failure ? failure.imageUri : undefined,
      sourceImageKey:
        ("sourceImageKey" in failure && failure.sourceImageKey) ||
        getImageKey(failure.type, failure.imageIndex, "imageUri" in failure ? failure.imageUri : undefined),
      reason: failure.reason,
      failureType: failure.failureType ?? "ocr",
      manualName: "",
      resolving: false,
      status: "pending" as const,
    }))
  })
  const [activeSTTKey, setActiveSTTKey] = useState<string | null>(null)

  const allMeds = [...alloList, ...ayurList]
  const totalScanned =
    scanMeta?.totalScanned ??
    (imageResults.length > 0
      ? imageResults.length
      : allopathicImageUris.length + ayurvedicImageUris.length)
  const displayedImageResults = imageResults
  const unexplainedMissing = Math.max(0, totalScanned - imageResults.length)
  const pendingReviewItems = reviewItems.filter((entry) => entry.status === "pending")
  const ignoredReviewItems = reviewItems.filter((entry) => entry.status === "ignored")
  const pendingManualReviewCount = pendingReviewItems.length + unexplainedMissing
  const hasAny =
    allMeds.length > 0 ||
    reviewItems.length > 0 ||
    imageResults.length > 0

  function getImageContextForDrug(
    drug: ExtractedDrug,
    type: "allopathic" | "ayurvedic",
  ): { imageIndex: number; imageUri?: string; sourceImageKey: string } {
    const matchedResult = imageResults.find((result) => {
      const resultKey = getImageKey(result.type, result.imageIndex, result.imageUri)
      return result.type === type && (
        (drug.source_image_key && drug.source_image_key === resultKey) ||
        (!!drug.medicine_id && result.resolved_medicine_ids?.includes(drug.medicine_id)) ||
        (!!drug.image_uri && result.imageUri === drug.image_uri)
      )
    })

    if (matchedResult) {
      return {
        imageIndex: matchedResult.imageIndex,
        imageUri: matchedResult.imageUri,
        sourceImageKey: getImageKey(matchedResult.type, matchedResult.imageIndex, matchedResult.imageUri),
      }
    }

    const sourceUris = type === "allopathic" ? allopathicImageUris : ayurvedicImageUris
    const fallbackIndex = drug.image_uri ? sourceUris.indexOf(drug.image_uri) : -1
    return {
      imageIndex: fallbackIndex,
      imageUri: drug.image_uri,
      sourceImageKey: drug.source_image_key ?? getImageKey(type, fallbackIndex, drug.image_uri),
    }
  }

  function deriveReviewStatus(
    medicines: ExtractedDrug[],
    pendingReviewIds: string[],
  ): ImageProcessingResult["review_status"] {
    if (pendingReviewIds.length > 0 && medicines.length === 0) {
      return "manual_pending"
    }
    if (medicines.length === 0) {
      return "ignored"
    }
    if (medicines.some((drug) => drug.entry_origin === "manual" || drug.entry_origin === "restored")) {
      return "manually_resolved"
    }
    return "detected"
  }

  function updateReviewItemName(entryId: string, text: string) {
    setReviewItems((prev) =>
      prev.map((entry) => (entry.id === entryId ? { ...entry, manualName: text } : entry))
    )
  }

  // ── Handle mic press (STT) ────────────────────────────────────────────────
  async function handleMicPress(targetKey: string, onTranscript: (text: string) => void) {
    if (stt.recording && activeSTTKey === targetKey) {
      const transcript = await stt.stopRecording(lang)
      setActiveSTTKey(null)
      if (transcript) {
        onTranscript(transcript)
      }
    } else {
      if (stt.recording) {
        await stt.cancelRecording()
      }
      setActiveSTTKey(targetKey)
      await stt.startRecording()
    }
  }

  function queueRemovedDetection(drug: ExtractedDrug, type: "allopathic" | "ayurvedic") {
    const context = getImageContextForDrug(drug, type)
    const originalMedicineId = drug.medicine_id ?? `${context.sourceImageKey}:removed:${Date.now()}`

    setReviewItems((prev) => [
      ...prev,
      {
        id: `review-${originalMedicineId}`,
        source_kind: "removed_detection",
        type,
        imageIndex: context.imageIndex,
        imageUri: context.imageUri,
        sourceImageKey: context.sourceImageKey,
        reason: t("type_or_speak_name", lang),
        manualName: getMedicineDisplayName(drug),
        resolving: false,
        status: "pending",
        originalDrug: drug,
        originalMedicineId,
      },
    ])

    setImageResults((prev) =>
      prev.map((result) => {
        if (getImageKey(result.type, result.imageIndex, result.imageUri) !== context.sourceImageKey) {
          return result
        }

        const medicines = (result.medicines ?? []).filter((item) => item.medicine_id !== drug.medicine_id)
        const resolvedIds = (result.resolved_medicine_ids ?? []).filter((id) => id !== drug.medicine_id)
        const pendingIds = Array.from(new Set([...(result.pending_review_ids ?? []), originalMedicineId]))

        return {
          ...result,
          medicines,
          resolved_medicine_ids: resolvedIds,
          pending_review_ids: pendingIds,
          review_status: deriveReviewStatus(medicines, pendingIds),
        }
      }),
    )

    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
  }

  function removeAllopathic(index: number) {
    const drug = alloList[index]
    setAlloList((prev) => prev.filter((_, i) => i !== index))
    queueRemovedDetection(drug, "allopathic")
  }

  function removeAyurvedic(index: number) {
    const drug = ayurList[index]
    setAyurList((prev) => prev.filter((_, i) => i !== index))
    queueRemovedDetection(drug, "ayurvedic")
  }

  async function resolveReviewEntry(entryId: string) {
    const entry = reviewItems.find((item) => item.id === entryId)
    if (!entry || entry.status !== "pending") return
    const name = entry.manualName.trim()
    if (!name) return

    setReviewItems((prev) =>
      prev.map((item) => (item.id === entryId ? { ...item, resolving: true } : item))
    )

    try {
      const response = await resolveManualMedicine({
        text: name,
        medicine_type: entry.type,
        source_lang: lang,
      })
      const resolved = withMedicineIdentityList(response.medicines, {
        entryOrigin: "manual",
        type: entry.type,
        imageIndex: entry.imageIndex,
        imageUri: entry.imageUri,
      })

      if (entry.type === "allopathic") {
        setAlloList((prev) => [...prev, ...resolved])
      } else {
        setAyurList((prev) => [...prev, ...resolved])
      }
      setReviewItems((prev) => prev.filter((item) => item.id !== entryId))
      setImageResults((prev) =>
        prev.map((result) => {
          if (getImageKey(result.type, result.imageIndex, result.imageUri) !== entry.sourceImageKey) {
            return result
          }

          const carriedMedicines = entry.source_kind === "removed_detection"
            ? (result.medicines ?? []).filter((drug) => drug.medicine_id !== entry.originalMedicineId)
            : []
          const medicines = [...carriedMedicines, ...resolved]
          const pendingIds = entry.source_kind === "removed_detection"
            ? (result.pending_review_ids ?? []).filter((id) => id !== entry.originalMedicineId)
            : []
          const resolvedIds = Array.from(
            new Set([
              ...(result.resolved_medicine_ids ?? []).filter((id) =>
                entry.source_kind === "removed_detection" ? id !== entry.originalMedicineId : true,
              ),
              ...resolved.map((drug) => drug.medicine_id ?? ""),
            ].filter(Boolean)),
          )

          return {
            ...result,
            medicines,
            review_status: deriveReviewStatus(medicines, pendingIds),
            resolved_medicine_ids: resolvedIds,
            pending_review_ids: pendingIds,
            failureType: undefined,
            failureReason: undefined,
          }
        }),
      )
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
    } catch {
      setReviewItems((prev) =>
        prev.map((item) => (item.id === entryId ? { ...item, resolving: false } : item)),
      )
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Warning)
    }
  }

  function restoreOriginalEntry(entryId: string) {
    const entry = reviewItems.find((item) => item.id === entryId)
    if (!entry || entry.source_kind !== "removed_detection") return

    const restoredDrug: ExtractedDrug = {
      ...entry.originalDrug,
      entry_origin: entry.originalDrug.entry_origin === "ocr" ? "restored" : entry.originalDrug.entry_origin,
    }

    if (entry.type === "allopathic") {
      setAlloList((prev) => [...prev, restoredDrug])
    } else {
      setAyurList((prev) => [...prev, restoredDrug])
    }

    setReviewItems((prev) => prev.filter((item) => item.id !== entryId))
    setImageResults((prev) =>
      prev.map((result) => {
        if (getImageKey(result.type, result.imageIndex, result.imageUri) !== entry.sourceImageKey) {
          return result
        }

        const medicines = [...(result.medicines ?? []), restoredDrug]
        const pendingIds = (result.pending_review_ids ?? []).filter((id) => id !== entry.originalMedicineId)
        const resolvedIds = Array.from(new Set([...(result.resolved_medicine_ids ?? []), entry.originalMedicineId]))

        return {
          ...result,
          medicines,
          review_status: deriveReviewStatus(medicines, pendingIds),
          resolved_medicine_ids: resolvedIds,
          pending_review_ids: pendingIds,
          failureType: undefined,
          failureReason: undefined,
        }
      }),
    )
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
  }

  function ignoreReviewEntry(entryId: string) {
    const entry = reviewItems.find((item) => item.id === entryId)
    if (!entry) return

    setReviewItems((prev) =>
      prev.map((item) => (item.id === entryId ? { ...item, status: "ignored", resolving: false } : item))
    )
    setImageResults((prev) =>
      prev.map((result) => {
        if (getImageKey(result.type, result.imageIndex, result.imageUri) !== entry.sourceImageKey) {
          return result
        }

        const medicines = entry.source_kind === "scan_failure" ? [] : (result.medicines ?? [])
        const pendingIds = entry.source_kind === "removed_detection"
          ? (result.pending_review_ids ?? []).filter((id) => id !== entry.originalMedicineId)
          : []
        const resolvedIds = entry.source_kind === "scan_failure" ? [] : (result.resolved_medicine_ids ?? [])

        return {
          ...result,
          medicines,
          review_status: deriveReviewStatus(medicines, pendingIds),
          resolved_medicine_ids: resolvedIds,
          pending_review_ids: pendingIds,
          failureType: undefined,
          failureReason: entry.reason,
        }
      }),
    )
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
  }

  function restoreIgnoredEntry(entryId: string) {
    const entry = reviewItems.find((item) => item.id === entryId)
    if (!entry) return

    setReviewItems((prev) =>
      prev.map((item) => (item.id === entryId ? { ...item, status: "pending" } : item))
    )
    setImageResults((prev) =>
      prev.map((result) => {
        if (getImageKey(result.type, result.imageIndex, result.imageUri) !== entry.sourceImageKey) {
          return result
        }

        const pendingIds = entry.source_kind === "removed_detection"
          ? Array.from(new Set([...(result.pending_review_ids ?? []), entry.originalMedicineId]))
          : []
        const medicines = result.medicines ?? []

        return {
          ...result,
          review_status:
            entry.source_kind === "scan_failure" && medicines.length === 0
              ? "manual_pending"
              : deriveReviewStatus(medicines, pendingIds),
          pending_review_ids: pendingIds,
          failureType: entry.source_kind === "scan_failure" ? entry.failureType : result.failureType,
          failureReason: entry.reason,
        }
      }),
    )
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
  }

  // ── Confirm ───────────────────────────────────────────────────────────────
  function handleConfirm() {
    const unresolvedCount = pendingReviewItems.length + unexplainedMissing
    if (unresolvedCount > 0) {
      return
    }
    doConfirm()
  }

  function doConfirm() {
    const finalAll = [...alloList, ...ayurList]
    if (finalAll.length === 0) {
      Alert.alert(t("error_occurred", lang), t("no_medicines_found", lang))
      return
    }
    const remainingFailures = imageResults
      .filter((result) => result.review_status === "manual_pending")
      .map((result) => ({
        imageIndex: result.imageIndex,
        type: result.type,
        reason: result.failureReason ?? "Manual review pending",
        imageUri: result.imageUri,
        sourceImageKey: getImageKey(result.type, result.imageIndex, result.imageUri),
        failureType: result.failureType ?? "ocr",
      }))

    setAllopathicMedicines(alloList)
    setAyurvedicMedicines(ayurList)
    setConfirmedMedicines(finalAll)
    setOcrFailures(
      remainingFailures.map((entry) => ({
        imageIndex: entry.imageIndex,
        type: entry.type,
        reason: entry.reason,
        imageUri: entry.imageUri,
        sourceImageKey: entry.sourceImageKey,
        failureType: entry.failureType,
      }))
    )
    setAllImageResults(imageResults)
    setScanMeta({
      totalScanned,
      detectedCount: finalAll.length,
      manualReviewCount: pendingReviewItems.length,
      ignoredCount: ignoredReviewItems.length,
      failedImages: remainingFailures.map((entry) => ({
        imageIndex: entry.imageIndex,
        type: entry.type,
        reason: entry.reason,
        imageUri: entry.imageUri,
        sourceImageKey: entry.sourceImageKey,
        failureType: entry.failureType,
      })),
    })
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
    router.push("/categorize")
  }

  return (
    <StepShell step={5} showBack>
      <ScrollView
        contentContainerStyle={styles.scroll}
        showsVerticalScrollIndicator={false}
      >
        {/* Header */}
        <View style={styles.header}>
          <Text style={styles.title}>{t("review_medicines", lang)}</Text>
          <Text style={styles.subtitle}>
            {totalScanned > 0
              ? `${totalScanned} ${t("images_scanned", lang)} · ${allMeds.length} ${t("medicines_detected", lang)}`
              : `${allMeds.length} ${t("medicines_found", lang)}`}
          </Text>
          <VoiceBar
            text={`${t("review_medicines", lang)}. ${totalScanned} ${t("images_scanned", lang)}. ${allMeds.length} ${t("medicines_detected", lang)}.`}
            language={lang}
            ttsPlaying={tts.playing}
            onSpeak={tts.speak}
            onStopSpeak={tts.stop}
          />
        </View>

        {/* Summary strip */}
        <View style={styles.summaryRow}>
          <SummaryStatCard
            icon="medkit-outline"
            iconColor={COLORS.primary}
            count={alloList.length}
            label={t("allopathic", lang)}
          />
          <SummaryStatCard
            icon="leaf-outline"
            iconColor="#2E7D32"
            count={ayurList.length}
            label={t("ayurvedic", lang)}
          />
          <SummaryStatCard
            icon="create-outline"
            iconColor="#B45309"
            count={pendingManualReviewCount}
            label={t("need_manual_review", lang)}
            warning={pendingManualReviewCount > 0}
          />
        </View>

        {pendingManualReviewCount > 0 && (
          <View style={styles.warningBox}>
            <Ionicons name="warning" size={18} color="#B45309" />
            <Text style={styles.warningText}>
              {pendingManualReviewCount} {t("manual_review_pending_hint", lang)}
            </Text>
          </View>
        )}

        {/* Source images with exact per-image status */}
        {displayedImageResults.length > 0 && (
          <View style={styles.imageSection}>
            <Text style={styles.imageSectionLabel}>{t("scan_status", lang)}</Text>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.imageRow}>
              {displayedImageResults.map((result) => {
                const hasPendingCorrections = (result.pending_review_ids?.length ?? 0) > 0
                const statusLabel = result.review_status === "ignored"
                  ? t("ignored_from_scan", lang)
                  : result.review_status === "manual_pending" || result.failureType
                  ? t("manual_review", lang)
                  : hasPendingCorrections
                  ? t("correction_pending", lang)
                  : result.medicines && result.medicines.length > 1
                  ? `${result.medicines.length} ${t("medicines_detected", lang)}`
                  : result.medicines?.[0] ? getMedicineDisplayName(result.medicines[0]) : t("medicines_detected", lang)
                const statusStyle =
                  hasPendingCorrections || result.review_status === "manual_pending" || result.failureType
                    ? styles.statusThumbWarning
                    : result.review_status === "ignored"
                    ? styles.statusThumbIgnored
                    : styles.statusThumbSuccess

                return (
                  <View key={`${result.type}-${result.imageIndex}`} style={styles.statusCard}>
                    <View style={[styles.statusThumbWrap, statusStyle]}>
                      {result.imageUri ? (
                        <Image source={{ uri: result.imageUri }} style={styles.sourceThumb} resizeMode="cover" />
                      ) : (
                        <View style={styles.sourceThumbPlaceholder}>
                          <Text style={styles.sourceThumbPlaceholderText}>
                            {result.type === "ayurvedic" ? "🌿" : "💊"}
                          </Text>
                        </View>
                      )}
                    </View>
                    <View style={styles.statusTypeRow}>
                      <Ionicons
                        name={result.type === "ayurvedic" ? "leaf-outline" : "medkit-outline"}
                        size={12}
                        color={result.type === "ayurvedic" ? "#2E7D32" : COLORS.primary}
                      />
                      <Text
                        style={styles.statusCardType}
                        numberOfLines={1}
                        adjustsFontSizeToFit
                        minimumFontScale={0.82}
                      >
                        {result.type === "ayurvedic" ? t("ayurvedic", lang) : t("allopathic", lang)}
                      </Text>
                    </View>
                    <Text style={styles.statusCardLabel} numberOfLines={2}>
                      {statusLabel}
                    </Text>
                  </View>
                )
              })}
            </ScrollView>
          </View>
        )}

        {unexplainedMissing > 0 && (
          <View style={styles.warningBox}>
            <Ionicons name="alert-circle" size={18} color="#B45309" />
            <Text style={styles.warningText}>
              {unexplainedMissing} {t("images_need_review", lang)}
            </Text>
          </View>
        )}

        {/* Allopathic section */}
        {alloList.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>💊 {t("allopathic", lang)}</Text>
            {alloList.map((drug, i) => (
              <MedicineCard
                key={drug.medicine_id ?? `allo-${drug.generic_name}-${i}`}
                drug={drug}
                onRemove={() => removeAllopathic(i)}
                index={i}
                lang={lang}
              />
            ))}
          </View>
        )}

        {/* Ayurvedic section */}
        {ayurList.length > 0 && (
          <View style={styles.section}>
            <Text style={styles.sectionLabel}>🌿 {t("ayurvedic", lang)}</Text>
            {ayurList.map((drug, i) => (
              <MedicineCard
                key={drug.medicine_id ?? `ayur-${drug.generic_name}-${i}`}
                drug={drug}
                onRemove={() => removeAyurvedic(i)}
                index={i}
                lang={lang}
              />
            ))}
          </View>
        )}

        {pendingReviewItems.length > 0 && (
          <View style={styles.cartSection}>
            <View style={styles.cartHeader}>
              <Ionicons name="warning-outline" size={20} color="#B45309" />
              <Text style={[styles.cartTitle, styles.manualReviewTitle]}>{t("manual_review", lang)}</Text>
            </View>
            <Text style={styles.cartHint}>{t("manual_review_hint", lang)}</Text>
            {pendingReviewItems.map((entry) => (
              <ReviewCard
                key={entry.id}
                entry={entry}
                onManualNameChange={(text) => updateReviewItemName(entry.id, text)}
                onResolve={() => resolveReviewEntry(entry.id)}
                onIgnore={() => ignoreReviewEntry(entry.id)}
                onRestoreOriginal={
                  entry.source_kind === "removed_detection"
                    ? () => restoreOriginalEntry(entry.id)
                    : undefined
                }
                onMicPress={() =>
                  handleMicPress(`manual-${entry.id}`, (text) => updateReviewItemName(entry.id, text))
                }
                sttRecording={stt.recording && activeSTTKey === `manual-${entry.id}`}
                sttTranscribing={stt.transcribing && activeSTTKey === `manual-${entry.id}`}
                lang={lang}
              />
            ))}
          </View>
        )}

        {ignoredReviewItems.length > 0 && (
          <View style={styles.cartSection}>
            <View style={styles.cartHeader}>
              <Ionicons name="remove-circle-outline" size={20} color={COLORS.slate} />
              <Text style={styles.cartTitle}>{t("ignored_from_scan", lang)}</Text>
            </View>
            <Text style={styles.cartHint}>{t("removed_medicines_hint", lang)}</Text>
            {ignoredReviewItems.map((entry) => (
              <ReviewCard
                key={entry.id}
                entry={entry}
                onRestoreIgnored={() => restoreIgnoredEntry(entry.id)}
                sttRecording={false}
                sttTranscribing={false}
                lang={lang}
              />
            ))}
          </View>
        )}

        {/* Empty state */}
        {!hasAny && (
          <Animated.View entering={FadeInDown.duration(300)} style={styles.emptyBox}>
            <Text style={styles.emptyIcon}>🔍</Text>
            <Text style={styles.emptyTitle}>{t("no_medicines_found", lang)}</Text>
            <TouchableOpacity style={styles.backBtn} onPress={() => router.back()}>
              <Text style={styles.backBtnText}>← {t("go_back", lang)}</Text>
            </TouchableOpacity>
          </Animated.View>
        )}

        {/* Bottom spacer */}
        <View style={{ height: 100 }} />
      </ScrollView>

      {/* Fixed CTA */}
      {hasAny && (
        <View style={styles.footer}>
          {pendingManualReviewCount > 0 ? (
            <Text style={styles.footerHint}>{t("resolve_or_ignore_to_continue", lang)}</Text>
          ) : null}
          <TouchableOpacity
            style={[styles.confirmBtn, pendingManualReviewCount > 0 && styles.confirmBtnDisabled]}
            onPress={handleConfirm}
            disabled={pendingManualReviewCount > 0}
            activeOpacity={0.85}
          >
            <Ionicons name="checkmark-circle" size={22} color="#FFF" />
            <Text style={styles.confirmBtnText}>
              {t("confirm_medicines", lang)} →
            </Text>
          </TouchableOpacity>
        </View>
      )}
    </StepShell>
  )
}

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 32 },
  header: { marginBottom: 16 },
  title: { fontSize: 26, fontWeight: "800", color: COLORS.charcoal, letterSpacing: -0.5 },
  subtitle: { fontSize: 16, color: COLORS.slate, marginTop: 4 },
  summaryRow: { flexDirection: "row", gap: 10, marginBottom: 16, alignItems: "stretch" },
  summaryCard: {
    flex: 1,
    minHeight: 108,
    borderRadius: 16,
    borderWidth: 1.5,
    borderColor: COLORS.border,
    backgroundColor: COLORS.surface,
    paddingHorizontal: 10,
    paddingVertical: 12,
    alignItems: "center",
    justifyContent: "center",
    gap: 6,
  },
  summaryCardWarning: {
    borderColor: "#F59E0B",
    backgroundColor: "#FFF7ED",
  },
  summaryIconChip: {
    width: 34,
    height: 34,
    borderRadius: 17,
    backgroundColor: "#E6FFFB",
    alignItems: "center",
    justifyContent: "center",
  },
  summaryIconChipWarning: {
    backgroundColor: "#FFEDD5",
  },
  summaryCount: {
    fontSize: 32,
    fontWeight: "800",
    color: COLORS.charcoal,
    includeFontPadding: false,
  },
  summaryCountWarning: {
    color: "#92400E",
  },
  summaryLabel: {
    fontSize: 12,
    lineHeight: 16,
    fontWeight: "700",
    color: COLORS.charcoal,
    textAlign: "center",
    includeFontPadding: false,
    width: "100%",
  },
  summaryLabelWarning: {
    color: "#92400E",
  },
  warningBox: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    padding: 12,
    borderRadius: 14,
    backgroundColor: "#FFF7ED",
    borderWidth: 1.5,
    borderColor: "#FCD34D",
    marginBottom: 16,
  },
  warningText: { flex: 1, fontSize: 13, fontWeight: "600", color: "#92400E" },
  imageSection: { marginBottom: 20 },
  imageSectionLabel: { fontSize: 14, fontWeight: "700", color: COLORS.slate, marginBottom: 10 },
  imageRow: { marginBottom: 20 },
  statusCard: { width: 108, marginRight: 10 },
  statusThumbWrap: {
    borderRadius: 12,
    borderWidth: 2,
    padding: 2,
    marginBottom: 8,
  },
  statusThumbSuccess: { borderColor: "#86EFAC", backgroundColor: "#F0FDF4" },
  statusThumbWarning: { borderColor: "#FCD34D", backgroundColor: "#FFF7ED" },
  statusThumbIgnored: { borderColor: "#CBD5E1", backgroundColor: "#F8FAFC" },
  sourceThumb: {
    width: 96, height: 74, borderRadius: 10, backgroundColor: "#E0E0E0",
  },
  sourceThumbPlaceholder: {
    width: 96,
    height: 74,
    borderRadius: 10,
    backgroundColor: COLORS.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  sourceThumbPlaceholderText: { fontSize: 26 },
  statusTypeRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 4,
    marginBottom: 4,
  },
  statusCardType: {
    flex: 1,
    fontSize: 10.5,
    fontWeight: "700",
    color: COLORS.slate,
    textAlign: "center",
    includeFontPadding: false,
  },
  statusCardLabel: {
    fontSize: 11.5,
    color: COLORS.charcoal,
    lineHeight: 15,
    textAlign: "center",
    minHeight: 30,
  },
  section: { marginBottom: 20 },
  sectionLabel: { fontSize: 17, fontWeight: "700", color: COLORS.charcoal, marginBottom: 12 },
  cartSection: {
    marginTop: 8,
    marginBottom: 20,
    padding: 14,
    borderRadius: 16,
    borderWidth: 2,
    borderColor: "#FFCDD2",
    borderStyle: "dashed",
    backgroundColor: "#FFF8F8",
  },
  cartHeader: { flexDirection: "row", alignItems: "center", gap: 8, marginBottom: 4 },
  cartTitle: { fontSize: 16, fontWeight: "800", color: "#C62828" },
  manualReviewTitle: { color: "#B45309" },
  cartHint: { fontSize: 13, color: COLORS.slate, marginBottom: 12 },
  emptyBox: {
    alignItems: "center", padding: 32, marginTop: 40,
    borderRadius: 20, backgroundColor: COLORS.surface,
    borderWidth: 2, borderColor: COLORS.border, borderStyle: "dashed",
  },
  emptyIcon: { fontSize: 52, marginBottom: 16 },
  emptyTitle: { fontSize: 20, fontWeight: "700", color: COLORS.charcoal, marginBottom: 8 },
  backBtn: {
    marginTop: 20, paddingHorizontal: 20, paddingVertical: 12,
    borderRadius: 12, backgroundColor: `${COLORS.primary}18`,
  },
  backBtnText: { color: COLORS.primary, fontWeight: "700", fontSize: 16 },
  footer: {
    position: "absolute", bottom: 0, left: 0, right: 0,
    paddingHorizontal: 20, paddingVertical: 16,
    backgroundColor: COLORS.cream,
    borderTopWidth: 1, borderTopColor: COLORS.border,
  },
  footerHint: {
    fontSize: 12,
    lineHeight: 18,
    color: "#92400E",
    textAlign: "center",
    marginBottom: 10,
    fontWeight: "600",
  },
  confirmBtn: {
    flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 10,
    backgroundColor: COLORS.primary, borderRadius: 16, minHeight: 60,
    shadowColor: COLORS.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3, shadowRadius: 8, elevation: 6,
  },
  confirmBtnDisabled: {
    opacity: 0.45,
  },
  confirmBtnText: { color: "#FFF", fontSize: 18, fontWeight: "700" },
})
