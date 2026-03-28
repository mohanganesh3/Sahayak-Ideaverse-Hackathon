import { useState, useRef } from "react"
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Image,
  Alert,
  ActivityIndicator,
  ScrollView,
  FlatList,
} from "react-native"
import { useRouter } from "expo-router"
import { CameraView, useCameraPermissions } from "expo-camera"
import * as ImagePicker from "expo-image-picker"
import * as Haptics from "expo-haptics"
import { useSafeAreaInsets } from "react-native-safe-area-context"
import { Ionicons } from "@expo/vector-icons"
import Animated, { FadeInDown } from "react-native-reanimated"
import { StepShell } from "../components/layout/StepShell"
import { VoiceBar } from "../components/ui/VoiceBar"
import { useAppStore } from "../hooks/useAppStore"
import { useTTS } from "../hooks/useVoice"
import { COLORS } from "../lib/constants"
import { t } from "../lib/i18n"

type Tab = "allopathic" | "ayurvedic"
type Mode = "choose" | "camera" | "preview"

// No limit on photos per category

export default function CameraScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const lang = useAppStore((s) => s.language)
  const [permission, requestPermission] = useCameraPermissions()
  const cameraRef = useRef<CameraView>(null)

  const [activeTab, setActiveTab] = useState<Tab>("allopathic")
  const [mode, setMode] = useState<Mode>("choose")
  const [capturing, setCapturing] = useState(false)
  const [previewUri, setPreviewUri] = useState<string | null>(null)

  const {
    allopathicImageUris,
    ayurvedicImageUris,
    addAllopathicImage,
    removeAllopathicImage,
    addAyurvedicImage,
    removeAyurvedicImage,
  } = useAppStore()

  const tts = useTTS()

  const currentUris = activeTab === "allopathic" ? allopathicImageUris : ayurvedicImageUris
  const totalPhotos = allopathicImageUris.length + ayurvedicImageUris.length

  async function openCamera() {
    if (!permission?.granted) {
      const result = await requestPermission()
      if (!result.granted) {
        Alert.alert(t("error_occurred", lang), t("camera_permission", lang))
        return
      }
    }
    setMode("camera")
  }

  async function pickFromGallery() {
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ["images"],
      quality: 0.92,
      allowsMultipleSelection: true,
      selectionLimit: 0, // 0 = unlimited
    })
    if (!result.canceled && result.assets.length > 0) {
      if (result.assets.length === 1) {
        setPreviewUri(result.assets[0].uri)
        setMode("preview")
      } else {
        // Multiple images selected — add all directly
        for (const asset of result.assets) {
          if (activeTab === "allopathic") addAllopathicImage(asset.uri)
          else addAyurvedicImage(asset.uri)
        }
        Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
      }
    }
  }

  async function capturePhoto() {
    if (!cameraRef.current || capturing) return
    try {
      setCapturing(true)
      await Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)
      const photo = await cameraRef.current.takePictureAsync({ quality: 0.92, skipProcessing: false })
      if (photo?.uri) {
        setPreviewUri(photo.uri)
        setMode("preview")
      }
    } catch {
      Alert.alert(t("error_occurred", lang), t("retake", lang))
    } finally {
      setCapturing(false)
    }
  }

  function retake() {
    setPreviewUri(null)
    setMode("camera")
  }

  function usePhoto() {
    if (!previewUri) return
    if (activeTab === "allopathic") {
      addAllopathicImage(previewUri)
    } else {
      addAyurvedicImage(previewUri)
    }
    setPreviewUri(null)
    setMode("choose")
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success)
  }

  function removePhoto(uri: string) {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium)
    if (activeTab === "allopathic") removeAllopathicImage(uri)
    else removeAyurvedicImage(uri)
  }

  function handleContinue() {
    if (totalPhotos === 0) {
      Alert.alert(t("error_occurred", lang), t("add_min_one_photo", lang))
      return
    }
    router.push("/processing")
  }

  return (
    <StepShell step={3} showBack>
      {mode === "camera" ? (
        // ── FULL SCREEN CAMERA ──────────────────────────────────────────────
        <View style={StyleSheet.absoluteFillObject}>
          <CameraView ref={cameraRef} style={StyleSheet.absoluteFillObject} facing="back">
            <View style={cameraStyles.overlay}>
              <View style={cameraStyles.topOverlay} />
              <View style={cameraStyles.middleRow}>
                <View style={cameraStyles.sideOverlay} />
                <View style={cameraStyles.frameBox}>
                  {(["tl", "tr", "bl", "br"] as const).map((corner) => (
                    <View key={corner} style={[cameraStyles.corner, cameraStyles[corner]]} />
                  ))}
                </View>
                <View style={cameraStyles.sideOverlay} />
              </View>
              <View style={cameraStyles.bottomOverlay} />
            </View>

            <Text style={cameraStyles.guideText}>{t("align_strip", lang)}</Text>

            <View style={[cameraStyles.shutterRow, { paddingBottom: insets.bottom + 20 }]}>
              <TouchableOpacity onPress={() => setMode("choose")} style={cameraStyles.cancelBtn}>
                <Ionicons name="close" size={28} color="#FFFFFF" />
              </TouchableOpacity>
              <TouchableOpacity
                onPress={capturePhoto}
                style={cameraStyles.shutterOuter}
                activeOpacity={0.8}
                disabled={capturing}
                accessibilityLabel={t("take_photo", lang)}
              >
                {capturing
                  ? <ActivityIndicator color={COLORS.primary} />
                  : <View style={cameraStyles.shutterInner} />
                }
              </TouchableOpacity>
              <View style={{ width: 60 }} />
            </View>
          </CameraView>
        </View>
      ) : mode === "preview" ? (
        // ── PHOTO PREVIEW ───────────────────────────────────────────────────
        <View style={previewStyles.container}>
          <Text style={previewStyles.title}>{t("photo_ok", lang)}</Text>
          <Image source={{ uri: previewUri! }} style={previewStyles.image} resizeMode="cover" />
          <View style={previewStyles.btnRow}>
            <TouchableOpacity onPress={retake} style={previewStyles.retakeBtn}>
              <Ionicons name="refresh" size={20} color={COLORS.primary} />
              <Text style={previewStyles.retakeBtnText}>{t("retake", lang)}</Text>
            </TouchableOpacity>
            <TouchableOpacity onPress={usePhoto} style={previewStyles.useBtn}>
              <Ionicons name="checkmark-circle" size={20} color="#FFFFFF" />
              <Text style={previewStyles.useBtnText}>{t("use_photo", lang)} ✓</Text>
            </TouchableOpacity>
          </View>
        </View>
      ) : (
        // ── CHOOSE MODE ─────────────────────────────────────────────────────
        <ScrollView
          contentContainerStyle={chooseStyles.scrollContent}
          showsVerticalScrollIndicator={false}
        >
          <Animated.View entering={FadeInDown.delay(50).duration(300)}>
            <Text style={chooseStyles.title}>{t("scan_medicines", lang)}</Text>
            <Text style={chooseStyles.subtitle}>{t("scan_subtitle", lang)}</Text>
            <VoiceBar
              text={t("scan_medicines", lang)}
              language={lang}
              ttsPlaying={tts.playing}
              onSpeak={tts.speak}
              onStopSpeak={tts.stop}
            />
          </Animated.View>

          {/* Tab switcher */}
          <Animated.View entering={FadeInDown.delay(100).duration(300)} style={chooseStyles.tabRow}>
            {(["allopathic", "ayurvedic"] as const).map((tab) => {
              const count = tab === "allopathic" ? allopathicImageUris.length : ayurvedicImageUris.length
              const active = activeTab === tab
              const iconName = tab === "allopathic" ? "medkit-outline" : "leaf-outline"
              return (
                <TouchableOpacity
                  key={tab}
                  onPress={() => setActiveTab(tab)}
                  style={[chooseStyles.tab, active && chooseStyles.tabActive]}
                >
                  <View style={chooseStyles.tabInner}>
                    <View style={[chooseStyles.tabIconWrap, active && chooseStyles.tabIconWrapActive]}>
                      <Ionicons
                        name={iconName}
                        size={18}
                        color={active ? COLORS.primary : COLORS.slate}
                      />
                    </View>
                    <Text
                      style={[chooseStyles.tabLabel, active && chooseStyles.tabLabelActive]}
                      numberOfLines={1}
                      adjustsFontSizeToFit
                      minimumFontScale={0.82}
                    >
                      {t(tab, lang)}
                    </Text>
                    {count > 0 ? (
                      <View style={[chooseStyles.tabCountBadge, active && chooseStyles.tabCountBadgeActive]}>
                        <Text style={[chooseStyles.tabCountText, active && chooseStyles.tabCountTextActive]}>
                          {count}
                        </Text>
                      </View>
                    ) : null}
                  </View>
                </TouchableOpacity>
              )
            })}
          </Animated.View>

          {/* Photo grid */}
          {currentUris.length > 0 && (
            <Animated.View entering={FadeInDown.delay(150).duration(300)} style={chooseStyles.photoGrid}>
              {currentUris.map((uri, idx) => (
                <View key={uri} style={chooseStyles.photoCard}>
                  <Image source={{ uri }} style={chooseStyles.photoThumb} resizeMode="cover" />
                  <TouchableOpacity
                    style={chooseStyles.photoRemove}
                    onPress={() => removePhoto(uri)}
                    hitSlop={{ top: 10, bottom: 10, left: 10, right: 10 }}
                  >
                    <Ionicons name="close-circle" size={24} color="#EF4444" />
                  </TouchableOpacity>
                  <View style={chooseStyles.photoBadge}>
                    <Text style={chooseStyles.photoBadgeText}>{idx + 1}</Text>
                  </View>
                </View>
              ))}
            </Animated.View>
          )}

          {currentUris.length > 0 && (
            <View style={chooseStyles.countRow}>
              <Ionicons name="checkmark-circle" size={18} color={COLORS.primary} />
              <Text style={chooseStyles.countText}>
                {currentUris.length} {t("photos_added", lang)}
              </Text>
            </View>
          )}

          {/* Add photo buttons — only if below limit */}
          {true && (
            <>
              <TouchableOpacity onPress={openCamera} style={chooseStyles.cameraBtn} activeOpacity={0.75}>
                <Ionicons name="camera" size={42} color={COLORS.primary} />
                <Text style={chooseStyles.cameraBtnTitle}>📷 {t("take_photo", lang)}</Text>
              </TouchableOpacity>

              <TouchableOpacity onPress={pickFromGallery} style={chooseStyles.galleryBtn} activeOpacity={0.75}>
                <Ionicons name="images-outline" size={24} color={COLORS.slate} />
                <Text style={chooseStyles.galleryBtnText}>{t("from_gallery", lang)}</Text>
              </TouchableOpacity>
            </>
          )}

          {/* Continue */}
          <TouchableOpacity
            onPress={handleContinue}
            style={[chooseStyles.continueBtn, totalPhotos === 0 && chooseStyles.continueBtnDisabled]}
            activeOpacity={0.85}
          >
            <Text style={chooseStyles.continueBtnText}>{t("start_analysis", lang)} →</Text>
          </TouchableOpacity>

          {totalPhotos === 0 && (
            <Text style={chooseStyles.hint}>{t("add_min_one_photo", lang)}</Text>
          )}

          <View style={{ height: 40 }} />
        </ScrollView>
      )}
    </StepShell>
  )
}

// ── Styles ───────────────────────────────────────────────────────────────────

const cameraStyles = StyleSheet.create({
  overlay: { flex: 1 },
  topOverlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)" },
  middleRow: { flexDirection: "row", height: 220 },
  sideOverlay: { flex: 1, backgroundColor: "rgba(0,0,0,0.55)" },
  frameBox: {
    width: 280,
    height: 220,
    borderWidth: 0,
    position: "relative",
  },
  corner: {
    position: "absolute",
    width: 28,
    height: 28,
    borderColor: "#F59E0B",
    borderWidth: 3,
  },
  tl: { top: 0, left: 0, borderRightWidth: 0, borderBottomWidth: 0 },
  tr: { top: 0, right: 0, borderLeftWidth: 0, borderBottomWidth: 0 },
  bl: { bottom: 0, left: 0, borderRightWidth: 0, borderTopWidth: 0 },
  br: { bottom: 0, right: 0, borderLeftWidth: 0, borderTopWidth: 0 },
  bottomOverlay: { flex: 1.2, backgroundColor: "rgba(0,0,0,0.55)" },
  guideText: {
    position: "absolute",
    top: "38%",
    alignSelf: "center",
    color: "rgba(255,255,255,0.9)",
    fontSize: 14,
    fontWeight: "600",
    textAlign: "center",
    paddingHorizontal: 12,
  },
  shutterRow: {
    position: "absolute",
    bottom: 0,
    left: 0,
    right: 0,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 40,
    paddingTop: 20,
    backgroundColor: "rgba(0,0,0,0.3)",
  },
  cancelBtn: {
    width: 60,
    height: 60,
    borderRadius: 30,
    backgroundColor: "rgba(255,255,255,0.2)",
    alignItems: "center",
    justifyContent: "center",
  },
  shutterOuter: {
    width: 80,
    height: 80,
    borderRadius: 40,
    backgroundColor: "#FFFFFF",
    alignItems: "center",
    justifyContent: "center",
    borderWidth: 4,
    borderColor: COLORS.primary,
  },
  shutterInner: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: COLORS.primary,
  },
})

const previewStyles = StyleSheet.create({
  container: { flex: 1, paddingHorizontal: 20, paddingTop: 8 },
  title: { fontSize: 24, fontWeight: "700", color: COLORS.charcoal, marginBottom: 16 },
  image: { width: "100%", aspectRatio: 4 / 3, borderRadius: 16, backgroundColor: "#000" },
  btnRow: { flexDirection: "row", gap: 12, marginTop: 20 },
  retakeBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 8, minHeight: 56, borderRadius: 14, borderWidth: 2, borderColor: COLORS.primary,
    backgroundColor: COLORS.surface,
  },
  retakeBtnText: { color: COLORS.primary, fontSize: 17, fontWeight: "700" },
  useBtn: {
    flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "center",
    gap: 8, minHeight: 56, borderRadius: 14, backgroundColor: COLORS.primary,
  },
  useBtnText: { color: "#FFFFFF", fontSize: 17, fontWeight: "700" },
})

const chooseStyles = StyleSheet.create({
  scrollContent: { paddingHorizontal: 20, paddingTop: 8 },
  title: { fontSize: 26, fontWeight: "800", color: COLORS.charcoal, letterSpacing: -0.5 },
  subtitle: { fontSize: 16, color: COLORS.slate, marginTop: 4, marginBottom: 4 },
  tabRow: { flexDirection: "row", gap: 10, marginTop: 16, marginBottom: 16 },
  tab: {
    flex: 1,
    minHeight: 56,
    borderRadius: 14,
    borderWidth: 2,
    borderColor: COLORS.border,
    backgroundColor: COLORS.surface,
    paddingHorizontal: 10,
  },
  tabActive: { borderColor: COLORS.primary, backgroundColor: COLORS.primary },
  tabInner: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    gap: 8,
    minHeight: 52,
  },
  tabIconWrap: {
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: "#F1F5F9",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  },
  tabIconWrapActive: {
    backgroundColor: "#FFFFFF",
  },
  tabLabel: {
    flex: 1,
    fontSize: 13,
    fontWeight: "700",
    color: COLORS.charcoal,
    textAlign: "center",
    includeFontPadding: false,
  },
  tabLabelActive: { color: "#FFFFFF" },
  tabCountBadge: {
    minWidth: 22,
    height: 22,
    borderRadius: 11,
    paddingHorizontal: 6,
    backgroundColor: "#E2E8F0",
    alignItems: "center",
    justifyContent: "center",
    flexShrink: 0,
  },
  tabCountBadgeActive: {
    backgroundColor: "#FFFFFF",
  },
  tabCountText: {
    fontSize: 12,
    fontWeight: "800",
    color: COLORS.slate,
    includeFontPadding: false,
  },
  tabCountTextActive: {
    color: COLORS.primary,
  },
  photoGrid: {
    flexDirection: "row", flexWrap: "wrap", gap: 10, marginBottom: 10,
  },
  photoCard: {
    width: "30%", aspectRatio: 1, borderRadius: 12, overflow: "hidden",
    position: "relative",
  },
  photoThumb: { width: "100%", height: "100%", borderRadius: 12 },
  photoRemove: {
    position: "absolute", top: 4, right: 4,
    backgroundColor: "rgba(255,255,255,0.9)", borderRadius: 12,
  },
  photoBadge: {
    position: "absolute", bottom: 4, left: 4,
    backgroundColor: COLORS.primary, borderRadius: 10,
    width: 22, height: 22, alignItems: "center", justifyContent: "center",
  },
  photoBadgeText: { color: "#FFF", fontSize: 12, fontWeight: "800" },
  countRow: {
    flexDirection: "row", alignItems: "center", gap: 6, marginBottom: 16,
  },
  countText: { color: COLORS.primary, fontWeight: "700", fontSize: 14 },
  cameraBtn: {
    alignItems: "center", justifyContent: "center", gap: 6,
    borderWidth: 2, borderStyle: "dashed", borderColor: `${COLORS.primary}60`,
    backgroundColor: `${COLORS.primary}0D`,
    borderRadius: 20, paddingVertical: 28, marginBottom: 10,
  },
  cameraBtnTitle: { fontSize: 17, fontWeight: "700", color: COLORS.primary },
  galleryBtn: {
    flexDirection: "row", alignItems: "center", gap: 10, minHeight: 52,
    borderRadius: 14, borderWidth: 2, borderColor: COLORS.border,
    backgroundColor: COLORS.surface, paddingHorizontal: 16, marginBottom: 20,
  },
  galleryBtnText: { fontSize: 15, color: COLORS.charcoal, fontWeight: "600" },
  continueBtn: {
    backgroundColor: COLORS.primary, borderRadius: 16, minHeight: 60,
    alignItems: "center", justifyContent: "center",
    shadowColor: COLORS.primary, shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3, shadowRadius: 8, elevation: 6,
  },
  continueBtnDisabled: { backgroundColor: "#B2DFDB", shadowOpacity: 0 },
  continueBtnText: { color: "#FFFFFF", fontSize: 18, fontWeight: "700" },
  hint: { textAlign: "center", color: COLORS.slate, fontSize: 14, marginTop: 10 },
})
