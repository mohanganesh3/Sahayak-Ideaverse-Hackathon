import { useState } from "react"
import {
  View,
  Text,
  TextInput,
  TouchableOpacity,
  StyleSheet,
  ScrollView,
  Alert,
} from "react-native"
import { useRouter } from "expo-router"
import Animated, { FadeInDown } from "react-native-reanimated"
import * as Haptics from "expo-haptics"
import { Ionicons } from "@expo/vector-icons"
import { StepShell } from "../components/layout/StepShell"
import { VoiceBar } from "../components/ui/VoiceBar"
import { useAppStore } from "../hooks/useAppStore"
import { useTTS, useSTT } from "../hooks/useVoice"
import { COLORS } from "../lib/constants"
import { t } from "../lib/i18n"

const CONDITIONS = [
  "kidney_disease",
  "liver_disease",
  "diabetes",
  "high_bp",
  "heart_disease",
  "thyroid",
] as const

const GENDERS = ["male", "female", "other_gender"] as const

export default function PatientInfoScreen() {
  const router = useRouter()
  const lang = useAppStore((s) => s.language)
  const { patientInfo, setPatientInfo } = useAppStore()

  const [name, setName] = useState(patientInfo.name)
  const [age, setAge] = useState(patientInfo.age?.toString() ?? "")
  const [gender, setGender] = useState(patientInfo.gender)
  const [conditions, setConditions] = useState<string[]>(patientInfo.conditions)
  const [showVitals, setShowVitals] = useState(false)

  // Vitals
  const [systolicBp, setSystolicBp] = useState(patientInfo.systolic_bp?.toString() ?? "")
  const [diastolicBp, setDiastolicBp] = useState(patientInfo.diastolic_bp?.toString() ?? "")
  const [fastingSugar, setFastingSugar] = useState(patientInfo.fasting_blood_sugar?.toString() ?? "")
  const [spo2, setSpo2] = useState(patientInfo.spo2?.toString() ?? "")
  const [heartRate, setHeartRate] = useState(patientInfo.heart_rate?.toString() ?? "")
  const [creatinine, setCreatinine] = useState(patientInfo.serum_creatinine?.toString() ?? "")
  const [weightKg, setWeightKg] = useState(patientInfo.weight_kg?.toString() ?? "")

  const tts = useTTS()
  const stt = useSTT()

  function toggleCondition(cond: string) {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
    if (cond === "none") {
      setConditions([])
      return
    }
    setConditions((prev) =>
      prev.includes(cond) ? prev.filter((c) => c !== cond) : [...prev, cond]
    )
  }

  function handleContinue() {
    const ageNum = parseInt(age, 10)
    if (!name.trim()) {
      Alert.alert(t("error_occurred", lang), t("patient_name", lang))
      return
    }
    if (!age || isNaN(ageNum) || ageNum < 1 || ageNum > 150) {
      Alert.alert(t("error_occurred", lang), t("patient_age", lang))
      return
    }

    const toNum = (v: string) => {
      const n = parseFloat(v)
      return isNaN(n) ? null : n
    }

    setPatientInfo({
      name: name.trim(),
      age: ageNum,
      gender: gender as PatientInfo["gender"],
      conditions,
      weight_kg: toNum(weightKg),
      systolic_bp: toNum(systolicBp),
      diastolic_bp: toNum(diastolicBp),
      fasting_blood_sugar: toNum(fastingSugar),
      spo2: toNum(spo2),
      heart_rate: toNum(heartRate),
      serum_creatinine: toNum(creatinine),
    })

    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
    router.push("/camera")
  }

  async function handleVoiceName() {
    if (stt.recording) {
      const transcript = await stt.stopRecording(lang)
      if (transcript) setName(transcript)
    } else {
      stt.startRecording()
    }
  }

  return (
    <StepShell step={2} showBack>
      <ScrollView
        contentContainerStyle={styles.scroll}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
      >
        {/* Title */}
        <Animated.View entering={FadeInDown.delay(50).duration(300)}>
          <Text style={styles.title}>{t("patient_info_title", lang)}</Text>
          <VoiceBar
            text={t("patient_info_title", lang)}
            language={lang}
            ttsPlaying={tts.playing}
            onSpeak={tts.speak}
            onStopSpeak={tts.stop}
          />
        </Animated.View>

        {/* Name */}
        <Animated.View entering={FadeInDown.delay(100).duration(300)} style={styles.field}>
          <Text style={styles.label}>{t("patient_name", lang)}</Text>
          <View style={styles.inputRow}>
            <TextInput
              style={styles.input}
              value={name}
              onChangeText={setName}
              placeholder={t("patient_name", lang)}
              placeholderTextColor={COLORS.slate}
              autoCapitalize="words"
            />
            <TouchableOpacity
              style={[styles.micBtn, stt.recording && styles.micBtnActive]}
              onPress={handleVoiceName}
            >
              <Ionicons
                name={stt.recording ? "stop-circle" : "mic"}
                size={22}
                color={stt.recording ? "#FFF" : COLORS.saffron}
              />
            </TouchableOpacity>
          </View>
        </Animated.View>

        {/* Age */}
        <Animated.View entering={FadeInDown.delay(150).duration(300)} style={styles.field}>
          <Text style={styles.label}>{t("patient_age", lang)}</Text>
          <TextInput
            style={styles.input}
            value={age}
            onChangeText={setAge}
            placeholder="65"
            placeholderTextColor={COLORS.slate}
            keyboardType="numeric"
            maxLength={3}
          />
        </Animated.View>

        {/* Gender */}
        <Animated.View entering={FadeInDown.delay(200).duration(300)} style={styles.field}>
          <Text style={styles.label}>{t("gender", lang)}</Text>
          <View style={styles.chipRow}>
            {GENDERS.map((g) => {
              const val = g === "other_gender" ? "other" : g
              const isActive = gender === val
              return (
                <TouchableOpacity
                  key={g}
                  style={[styles.chip, isActive && styles.chipActive]}
                  onPress={() => {
                    setGender(val as "male" | "female" | "other")
                    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
                  }}
                >
                  <Text style={[styles.chipText, isActive && styles.chipTextActive]}>
                    {g === "male" ? "👨 " : g === "female" ? "👩 " : "🧑 "}
                    {t(g, lang)}
                  </Text>
                </TouchableOpacity>
              )
            })}
          </View>
        </Animated.View>

        {/* Health Conditions */}
        <Animated.View entering={FadeInDown.delay(250).duration(300)} style={styles.field}>
          <Text style={styles.label}>{t("health_conditions", lang)}</Text>
          <Text style={styles.hint}>{t("select_conditions", lang)}</Text>
          <VoiceBar
            text={t("health_conditions", lang)}
            language={lang}
            ttsPlaying={tts.playing}
            onSpeak={tts.speak}
            onStopSpeak={tts.stop}
          />
          <View style={styles.condGrid}>
            {CONDITIONS.map((cond) => {
              const isActive = conditions.includes(cond)
              return (
                <TouchableOpacity
                  key={cond}
                  style={[styles.condChip, isActive && styles.condChipActive]}
                  onPress={() => toggleCondition(cond)}
                >
                  <Ionicons
                    name={isActive ? "checkbox" : "square-outline"}
                    size={20}
                    color={isActive ? COLORS.primary : COLORS.slate}
                  />
                  <Text style={[styles.condText, isActive && styles.condTextActive]}>
                    {t(cond, lang)}
                  </Text>
                </TouchableOpacity>
              )
            })}
            <TouchableOpacity
              style={[styles.condChip, conditions.length === 0 && styles.condChipActive]}
              onPress={() => toggleCondition("none")}
            >
              <Ionicons
                name={conditions.length === 0 ? "checkbox" : "square-outline"}
                size={20}
                color={conditions.length === 0 ? COLORS.primary : COLORS.slate}
              />
              <Text
                style={[
                  styles.condText,
                  conditions.length === 0 && styles.condTextActive,
                ]}
              >
                {t("none_above", lang)}
              </Text>
            </TouchableOpacity>
          </View>
        </Animated.View>

        {/* Optional Vitals Toggle */}
        <Animated.View entering={FadeInDown.delay(300).duration(300)}>
          <TouchableOpacity
            style={styles.vitalsToggle}
            onPress={() => setShowVitals(!showVitals)}
          >
            <Ionicons
              name={showVitals ? "chevron-up" : "chevron-down"}
              size={20}
              color={COLORS.primary}
            />
            <View style={{ flex: 1 }}>
              <Text style={styles.vitalsToggleTitle}>
                {t("optional_vitals", lang)}
              </Text>
              <Text style={styles.vitalsToggleHint}>
                {t("optional_vitals_hint", lang)}
              </Text>
            </View>
          </TouchableOpacity>
        </Animated.View>

        {/* Vitals Fields */}
        {showVitals && (
          <Animated.View entering={FadeInDown.duration(300)} style={styles.vitalsGrid}>
            <VitalInput label={t("weight_kg", lang)} value={weightKg} onChange={setWeightKg} />
            <VitalInput label={t("systolic_bp", lang)} value={systolicBp} onChange={setSystolicBp} />
            <VitalInput label={t("diastolic_bp", lang)} value={diastolicBp} onChange={setDiastolicBp} />
            <VitalInput label={t("fasting_sugar", lang)} value={fastingSugar} onChange={setFastingSugar} />
            <VitalInput label={t("spo2", lang)} value={spo2} onChange={setSpo2} />
            <VitalInput label={t("heart_rate", lang)} value={heartRate} onChange={setHeartRate} />
            <VitalInput label={t("serum_creatinine", lang)} value={creatinine} onChange={setCreatinine} />
          </Animated.View>
        )}

        <View style={{ height: 120 }} />
      </ScrollView>

      {/* Fixed CTA */}
      <View style={styles.footer}>
        <TouchableOpacity
          style={styles.ctaBtn}
          onPress={handleContinue}
          activeOpacity={0.85}
        >
          <Text style={styles.ctaBtnText}>
            {t("continue", lang)} →
          </Text>
        </TouchableOpacity>
      </View>
    </StepShell>
  )
}

// Small vitals input component
function VitalInput({
  label,
  value,
  onChange,
}: {
  label: string
  value: string
  onChange: (v: string) => void
}) {
  return (
    <View style={styles.vitalField}>
      <Text style={styles.vitalLabel}>{label}</Text>
      <TextInput
        style={styles.vitalInput}
        value={value}
        onChangeText={onChange}
        keyboardType="numeric"
        placeholderTextColor={COLORS.slate}
        placeholder="—"
      />
    </View>
  )
}

// Need this import for type
import type { PatientInfo } from "../types/sahayak"

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: 20, paddingTop: 8, paddingBottom: 32 },
  title: {
    fontSize: 26,
    fontWeight: "800",
    color: COLORS.charcoal,
    letterSpacing: -0.5,
    marginBottom: 4,
  },
  field: { marginTop: 20 },
  label: { fontSize: 16, fontWeight: "700", color: COLORS.charcoal, marginBottom: 8 },
  hint: { fontSize: 13, color: COLORS.slate, marginBottom: 6 },
  inputRow: { flexDirection: "row", gap: 8 },
  input: {
    flex: 1,
    minHeight: 52,
    borderRadius: 14,
    borderWidth: 2,
    borderColor: COLORS.border,
    backgroundColor: COLORS.surface,
    paddingHorizontal: 16,
    fontSize: 17,
    color: COLORS.charcoal,
    fontWeight: "600",
  },
  micBtn: {
    width: 52,
    height: 52,
    borderRadius: 14,
    borderWidth: 2,
    borderColor: `${COLORS.saffron}40`,
    backgroundColor: `${COLORS.saffron}10`,
    alignItems: "center",
    justifyContent: "center",
  },
  micBtnActive: {
    backgroundColor: "#EF4444",
    borderColor: "#EF4444",
  },
  chipRow: { flexDirection: "row", gap: 8 },
  chip: {
    flex: 1,
    paddingVertical: 12,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: COLORS.border,
    backgroundColor: COLORS.surface,
    alignItems: "center",
  },
  chipActive: {
    borderColor: COLORS.primary,
    backgroundColor: `${COLORS.primary}10`,
  },
  chipText: { fontSize: 14, fontWeight: "700", color: COLORS.charcoal },
  chipTextActive: { color: COLORS.primary },
  condGrid: { gap: 8, marginTop: 6 },
  condChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingVertical: 12,
    paddingHorizontal: 14,
    borderRadius: 12,
    borderWidth: 2,
    borderColor: COLORS.border,
    backgroundColor: COLORS.surface,
  },
  condChipActive: {
    borderColor: COLORS.primary,
    backgroundColor: `${COLORS.primary}08`,
  },
  condText: { fontSize: 15, fontWeight: "600", color: COLORS.charcoal },
  condTextActive: { color: COLORS.primary },
  vitalsToggle: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginTop: 24,
    paddingVertical: 14,
    paddingHorizontal: 16,
    borderRadius: 14,
    borderWidth: 2,
    borderColor: `${COLORS.primary}25`,
    backgroundColor: `${COLORS.primary}06`,
  },
  vitalsToggleTitle: { fontSize: 15, fontWeight: "700", color: COLORS.primary },
  vitalsToggleHint: { fontSize: 12, color: COLORS.slate, marginTop: 2 },
  vitalsGrid: { gap: 10, marginTop: 12 },
  vitalField: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
  },
  vitalLabel: {
    flex: 1,
    fontSize: 14,
    fontWeight: "600",
    color: COLORS.charcoal,
  },
  vitalInput: {
    width: 100,
    minHeight: 44,
    borderRadius: 10,
    borderWidth: 2,
    borderColor: COLORS.border,
    backgroundColor: COLORS.surface,
    paddingHorizontal: 12,
    fontSize: 16,
    color: COLORS.charcoal,
    fontWeight: "600",
    textAlign: "center",
  },
  footer: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    paddingHorizontal: 20,
    paddingVertical: 16,
    backgroundColor: COLORS.cream,
    borderTopWidth: 1,
    borderTopColor: COLORS.border,
  },
  ctaBtn: {
    backgroundColor: COLORS.primary,
    borderRadius: 16,
    minHeight: 60,
    alignItems: "center",
    justifyContent: "center",
    shadowColor: COLORS.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 6,
  },
  ctaBtnText: { color: "#FFF", fontSize: 18, fontWeight: "700" },
})
