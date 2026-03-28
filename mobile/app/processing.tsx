import { useEffect, useRef, useState } from "react"
import { View, Text, StyleSheet, TouchableOpacity } from "react-native"
import { useRouter } from "expo-router"
import Animated, {
  FadeInLeft,
  useSharedValue,
  withRepeat,
  withTiming,
  useAnimatedStyle,
} from "react-native-reanimated"
import { useSafeAreaInsets } from "react-native-safe-area-context"
import { Ionicons } from "@expo/vector-icons"
import { useAppStore } from "../hooks/useAppStore"
import { ocrImage, analyzeMedicines, extractDrugsFromText } from "../lib/api"
import { buildSourceImageKey, withMedicineIdentityList } from "../lib/medicines"
import { COLORS } from "../lib/constants"
import { t } from "../lib/i18n"
import type { ExtractedDrug, ImageProcessingResult, OcrFailure, OcrResult, ScanMeta } from "../types/sahayak"

type StepStatus = "pending" | "active" | "done" | "error"
interface Step { id: string; label: string; status: StepStatus }
interface ImageTask {
  imageUri: string
  type: "allopathic" | "ayurvedic"
  imageIndex: number
}
interface OcrSingleResult extends OcrResult {
  imageIndex: number
  imageUri: string
}

function SpinnerIcon() {
  const rotation = useSharedValue(0)
  useEffect(() => {
    rotation.value = withRepeat(withTiming(360, { duration: 900 }), -1, false)
  }, [rotation])
  const style = useAnimatedStyle(() => ({ transform: [{ rotate: `${rotation.value}deg` }] }))
  return (
    <Animated.View style={style}>
      <Ionicons name="sync" size={26} color={COLORS.primary} />
    </Animated.View>
  )
}

function PulseIcon() {
  const scale = useSharedValue(1)
  useEffect(() => {
    scale.value = withRepeat(withTiming(1.12, { duration: 900 }), -1, true)
  }, [scale])
  const style = useAnimatedStyle(() => ({ transform: [{ scale: scale.value }] }))
  return (
    <Animated.View style={style}>
      <Text style={{ fontSize: 40 }}>💊</Text>
    </Animated.View>
  )
}

export default function ProcessingScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const lang = useAppStore((s) => s.language)

  const initialSteps: Step[] = [
    { id: "ocr",      label: t("step_ocr", lang),       status: "pending" },
    { id: "extract",  label: t("step_extract", lang),    status: "pending" },
  ]

  const [steps, setSteps] = useState<Step[]>(initialSteps)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const started = useRef(false)

  const {
    allopathicImageUris,
    ayurvedicImageUris,
    setAllopathicMedicines,
    setAyurvedicMedicines,
    setOcrFailures,
    setAllImageResults,
    setScanMeta,
    setInteractions,
  } = useAppStore()

  function setStatus(id: string, status: StepStatus) {
    setSteps((prev) => prev.map((s) => (s.id === id ? { ...s, status } : s)))
  }

  async function run() {
    try {
      setAllopathicMedicines([])
      setAyurvedicMedicines([])
      setOcrFailures([])
      setAllImageResults([])
      setScanMeta(null)
      setInteractions([])

      const imageTasks: ImageTask[] = [
        ...allopathicImageUris.map((imageUri, imageIndex) => ({
          imageUri,
          imageIndex,
          type: "allopathic" as const,
        })),
        ...ayurvedicImageUris.map((imageUri, imageIndex) => ({
          imageUri,
          imageIndex,
          type: "ayurvedic" as const,
        })),
      ]

      // ── Step 1: OCR all images ────────────────────────────────────────
      setStatus("ocr", "active")
      const ocrAttempts = await Promise.all(
        imageTasks.map(async (task) => {
          try {
            const ocr = await ocrImage(task.imageUri, task.type)
            return {
              ok: true as const,
              result: {
                ...ocr,
                medicine_type: task.type,
                imageIndex: task.imageIndex,
                imageUri: task.imageUri,
              } satisfies OcrSingleResult,
            }
          } catch (error) {
            return { ok: false as const, task, error }
          }
        })
      )

      const successfulOcr: OcrSingleResult[] = []
      const ocrFailures: OcrFailure[] = []
      for (const attempt of ocrAttempts) {
        if (!attempt.ok) {
          ocrFailures.push({
            imageIndex: attempt.task.imageIndex,
            type: attempt.task.type,
            imageUri: attempt.task.imageUri,
            sourceImageKey: buildSourceImageKey(
              attempt.task.type,
              attempt.task.imageIndex,
              attempt.task.imageUri,
            ),
            reason: "This image could not be processed. Please review it manually.",
            failureType: "ocr",
          })
          continue
        }

        const ocr = attempt.result
        const hasText = ocr.text.trim().length > 0
        const hasGoodConfidence = ocr.confidence >= 0.3
        if (hasText && hasGoodConfidence) {
          successfulOcr.push(ocr)
          continue
        }

        ocrFailures.push({
          imageIndex: ocr.imageIndex,
          type: ocr.medicine_type,
          imageUri: ocr.imageUri,
          sourceImageKey: buildSourceImageKey(ocr.medicine_type, ocr.imageIndex, ocr.imageUri),
          reason: !hasText
            ? "No readable text was found in this image. Please review it manually."
            : `This image was read with very low confidence (${Math.round(ocr.confidence * 100)}%). Please review it manually.`,
          failureType: "ocr",
        })
      }
      setStatus("ocr", "done")

      // ── Step 2: Extract / identify medicines per image ────────────────
      setStatus("extract", "active")
      const extractionAttempts = await Promise.all(
        successfulOcr.map(async (ocr) => {
          try {
            const drugs = await extractDrugsFromText(ocr.text)
            return { ok: true as const, ocr, drugs }
          } catch (error) {
            return { ok: false as const, ocr, error }
          }
        })
      )

      const allopathicMedicines: ExtractedDrug[] = []
      const ayurvedicMedicines: ExtractedDrug[] = []
      const extractionFailures: OcrFailure[] = []
      const allImageResults: ImageProcessingResult[] = []

      for (const failure of ocrFailures) {
        allImageResults.push({
          imageIndex: failure.imageIndex,
          type: failure.type,
          imageUri: failure.imageUri,
          review_status: "manual_pending",
          resolved_medicine_ids: [],
          pending_review_ids: [],
          failureType: failure.failureType,
          failureReason: failure.reason,
        })
      }

      for (const attempt of extractionAttempts) {
        if (!attempt.ok) {
          const failure: OcrFailure = {
            imageIndex: attempt.ocr.imageIndex,
            type: attempt.ocr.medicine_type,
            imageUri: attempt.ocr.imageUri,
            sourceImageKey: buildSourceImageKey(
              attempt.ocr.medicine_type,
              attempt.ocr.imageIndex,
              attempt.ocr.imageUri,
            ),
            reason: "Text was read from this image, but the medicine name could not be identified. Please review it manually.",
            failureType: "extraction",
          }
          extractionFailures.push(failure)
          allImageResults.push({
            imageIndex: failure.imageIndex,
            type: failure.type,
            imageUri: failure.imageUri,
            review_status: "manual_pending",
            resolved_medicine_ids: [],
            pending_review_ids: [],
            failureType: failure.failureType,
            failureReason: failure.reason,
          })
          continue
        }

        const enrichedDrugs = withMedicineIdentityList(
          attempt.drugs
            .filter((drug) => (drug.brand_name || drug.generic_name || "").trim().length > 0)
            .map((drug) => ({
              ...drug,
              image_uri: attempt.ocr.imageUri,
              medicine_type: attempt.ocr.medicine_type,
              ocr_confidence: attempt.ocr.confidence,
              ocr_language: attempt.ocr.language,
              ocr_needs_fallback: attempt.ocr.needs_fallback,
            })),
          {
            entryOrigin: "ocr",
            type: attempt.ocr.medicine_type,
            imageIndex: attempt.ocr.imageIndex,
            imageUri: attempt.ocr.imageUri,
          },
        )

        if (enrichedDrugs.length === 0) {
          const failure: OcrFailure = {
            imageIndex: attempt.ocr.imageIndex,
            type: attempt.ocr.medicine_type,
            imageUri: attempt.ocr.imageUri,
            sourceImageKey: buildSourceImageKey(
              attempt.ocr.medicine_type,
              attempt.ocr.imageIndex,
              attempt.ocr.imageUri,
            ),
            reason: "Text was read from this image, but no medicine name could be extracted. Please review it manually.",
            failureType: "extraction",
          }
          extractionFailures.push(failure)
          allImageResults.push({
            imageIndex: failure.imageIndex,
            type: failure.type,
            imageUri: failure.imageUri,
            review_status: "manual_pending",
            resolved_medicine_ids: [],
            pending_review_ids: [],
            failureType: failure.failureType,
            failureReason: failure.reason,
          })
          continue
        }

        if (attempt.ocr.medicine_type === "allopathic") {
          allopathicMedicines.push(...enrichedDrugs)
        } else {
          ayurvedicMedicines.push(...enrichedDrugs)
        }
        allImageResults.push({
          imageIndex: attempt.ocr.imageIndex,
          type: attempt.ocr.medicine_type,
          imageUri: attempt.ocr.imageUri,
          medicines: enrichedDrugs,
          review_status: "detected",
          resolved_medicine_ids: enrichedDrugs.map((drug) => drug.medicine_id ?? ""),
          pending_review_ids: [],
        })
      }

      allImageResults.sort((left, right) => {
        if (left.type !== right.type) return left.type === "allopathic" ? -1 : 1
        return left.imageIndex - right.imageIndex
      })

      const allFailures = [...ocrFailures, ...extractionFailures]
      const scanMeta: ScanMeta = {
        totalScanned: imageTasks.length,
        detectedCount: allopathicMedicines.length + ayurvedicMedicines.length,
        manualReviewCount: allImageResults.filter((result) => result.review_status === "manual_pending").length,
        ignoredCount: 0,
        failedImages: allFailures.map((failure) => ({
          imageIndex: failure.imageIndex,
          type: failure.type,
          reason: failure.reason,
          imageUri: failure.imageUri,
          sourceImageKey: failure.sourceImageKey,
          failureType: failure.failureType ?? "ocr",
        })),
      }

      setAllopathicMedicines(allopathicMedicines)
      setAyurvedicMedicines(ayurvedicMedicines)
      setOcrFailures(allFailures)
      setAllImageResults(allImageResults)
      setScanMeta(scanMeta)

      const analysisPayload: OcrResult[] = successfulOcr.map((ocr) => ({
        text: ocr.text,
        confidence: ocr.confidence,
        language: ocr.language,
        needs_fallback: ocr.needs_fallback,
        medicine_type: ocr.medicine_type,
      }))
      if (analysisPayload.length > 0) {
        try {
          const analyzed = await analyzeMedicines(analysisPayload)
          setInteractions(analyzed.interactions ?? [])
        } catch {
          setInteractions([])
        }
      }
      setStatus("extract", "done")

      // Navigate to confirm screen for user review
      setTimeout(() => router.push("/confirm"), 600)
    } catch (err) {
      const msg = err instanceof Error ? err.message : t("error_occurred", lang)
      setErrorMsg(msg)
      setSteps((prev) => prev.map((s) => (s.status === "active" ? { ...s, status: "error" } : s)))
    }
  }

  useEffect(() => {
    if (started.current) return
    started.current = true
    run()
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <View style={[styles.container, { paddingTop: insets.top + 32, paddingBottom: insets.bottom + 20 }]}>
      <PulseIcon />

      <Text style={styles.title}>{t("analyzing", lang)}</Text>

      <View style={styles.stepsList}>
        {steps.map((step, i) => (
          <Animated.View
            key={step.id}
            entering={FadeInLeft.delay(i * 100).duration(300)}
            style={[
              styles.stepRow,
              step.status === "active" && styles.stepRowActive,
              step.status === "done" && styles.stepRowDone,
              step.status === "error" && styles.stepRowError,
            ]}
          >
            <View style={styles.stepIcon}>
              {step.status === "done" && (
                <Ionicons name="checkmark-circle" size={28} color="#2E7D32" />
              )}
              {step.status === "active" && <SpinnerIcon />}
              {step.status === "error" && (
                <Ionicons name="warning" size={28} color="#D32F2F" />
              )}
              {step.status === "pending" && (
                <Ionicons name="ellipse-outline" size={28} color={COLORS.border} />
              )}
            </View>
            <Text
              style={[
                styles.stepLabel,
                step.status === "active" && styles.stepLabelActive,
                step.status === "done" && styles.stepLabelDone,
                step.status === "error" && styles.stepLabelError,
              ]}
            >
              {step.label}
            </Text>
          </Animated.View>
        ))}
      </View>

      {errorMsg && (
        <View style={styles.errorBox}>
          <Text style={styles.errorTitle}>{t("error_occurred", lang)}</Text>
          <Text style={styles.errorBody}>{errorMsg}</Text>
          <TouchableOpacity onPress={() => router.push("/camera")}>
            <Text style={styles.errorLink}>← {t("back", lang)}</Text>
          </TouchableOpacity>
        </View>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.cream, alignItems: "center", paddingHorizontal: 24 },
  title: { fontSize: 22, fontWeight: "700", color: COLORS.charcoal, marginTop: 24, marginBottom: 36, textAlign: "center" },
  stepsList: { width: "100%", gap: 12 },
  stepRow: {
    flexDirection: "row", alignItems: "center", gap: 14,
    padding: 16, borderRadius: 16, backgroundColor: COLORS.surface,
    borderWidth: 2, borderColor: "transparent",
  },
  stepRowActive: { borderColor: COLORS.primary, backgroundColor: `${COLORS.primary}0D` },
  stepRowDone: { borderColor: "#A5D6A7", backgroundColor: "#E8F5E9" },
  stepRowError: { borderColor: "#EF9A9A", backgroundColor: "#FFEBEE" },
  stepIcon: { width: 36, alignItems: "center" },
  stepLabel: { flex: 1, fontSize: 16, fontWeight: "700", color: COLORS.slate },
  stepLabelActive: { color: COLORS.primary },
  stepLabelDone: { color: "#2E7D32" },
  stepLabelError: { color: "#C62828" },
  errorBox: {
    marginTop: 28, padding: 20, borderRadius: 16,
    backgroundColor: "#FFEBEE", borderWidth: 2, borderColor: "#EF9A9A",
    width: "100%", alignItems: "center",
  },
  errorTitle: { fontSize: 18, fontWeight: "700", color: "#C62828", marginBottom: 8 },
  errorBody: { fontSize: 14, color: "#B71C1C", textAlign: "center", marginBottom: 12 },
  errorLink: { fontSize: 16, color: COLORS.primary, fontWeight: "600" },
})
