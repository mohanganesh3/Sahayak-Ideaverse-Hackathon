import { useState, useRef } from "react"
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  Dimensions,
  PanResponder,
} from "react-native"
import { useRouter, type Href } from "expo-router"
import Animated, {
  FadeIn,
  FadeOut,
  SlideInRight,
  SlideOutLeft,
  SlideInLeft,
  SlideOutRight,
} from "react-native-reanimated"
import { useSafeAreaInsets } from "react-native-safe-area-context"
import { COLORS } from "../lib/constants"
import { t } from "../lib/i18n"
import { useAppStore } from "../hooks/useAppStore"

const { width: SCREEN_WIDTH } = Dimensions.get("window")

export default function OnboardingScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const [current, setCurrent] = useState(0)
  const [direction, setDirection] = useState<1 | -1>(1)
  const swipeStartX = useRef(0)
  const lang = useAppStore((s) => s.language)

  const SLIDES = [
    {
      emoji: "💊",
      bgColor: "#E8F5E9",
      accentColor: COLORS.primary,
      title: t("onboarding_scan_title", lang),
      body: t("onboarding_scan_body", lang),
    },
    {
      emoji: "🛡️",
      bgColor: "#FFF8E1",
      accentColor: "#F59E0B",
      title: t("onboarding_safety_title", lang),
      body: t("onboarding_safety_body", lang),
    },
    {
      emoji: "🌐",
      bgColor: "#FCE4EC",
      accentColor: "#E91E63",
      title: t("onboarding_language_title", lang),
      body: t("onboarding_language_body", lang),
    },
  ] as const

  const panResponder = PanResponder.create({
    onStartShouldSetPanResponder: () => true,
    onPanResponderGrant: (e) => { swipeStartX.current = e.nativeEvent.pageX },
    onPanResponderRelease: (e) => {
      const delta = e.nativeEvent.pageX - swipeStartX.current
      if (delta < -50 && current < SLIDES.length - 1) {
        setDirection(1)
        setCurrent((c) => c + 1)
      } else if (delta > 50 && current > 0) {
        setDirection(-1)
        setCurrent((c) => c - 1)
      }
    },
  })

  function goNext() {
    if (current < SLIDES.length - 1) {
      setDirection(1)
      setCurrent((c) => c + 1)
    } else {
      router.push("/patient-info" as Href)
    }
  }

  const slide = SLIDES[current]
  const isLast = current === SLIDES.length - 1

  const SlideIn = direction === 1 ? SlideInRight : SlideInLeft
  const SlideOut = direction === 1 ? SlideOutLeft : SlideOutRight

  return (
    <View style={[styles.container, { paddingTop: insets.top + 8, paddingBottom: insets.bottom + 16 }]}>
      {/* Skip */}
      <View style={styles.topBar}>
        <TouchableOpacity onPress={() => router.push("/patient-info" as Href)} style={styles.skipBtn}>
          <Text style={styles.skipText}>{t("skip", lang)}</Text>
        </TouchableOpacity>
      </View>

      {/* Slide */}
      <View style={styles.slideArea} {...panResponder.panHandlers}>
        <Animated.View
          key={current}
          entering={SlideIn.duration(280)}
          exiting={SlideOut.duration(200)}
          style={styles.slideContent}
        >
          {/* Emoji in colored circle */}
          <View style={[styles.emojiCircle, { backgroundColor: slide.bgColor }]}>
            <Text style={styles.emoji}>{slide.emoji}</Text>
          </View>

          <Text style={styles.titleHi}>{slide.title}</Text>
          <Text style={styles.bodyHi}>{slide.body}</Text>
        </Animated.View>
      </View>

      {/* Dots */}
      <View style={styles.dotsRow}>
        {SLIDES.map((_, i) => (
          <TouchableOpacity key={i} onPress={() => { setDirection(i > current ? 1 : -1); setCurrent(i) }}>
            <View style={[styles.dot, i === current && styles.dotActive]} />
          </TouchableOpacity>
        ))}
      </View>

      {/* CTA */}
      <View style={styles.footer}>
        <TouchableOpacity onPress={goNext} style={styles.ctaBtn} activeOpacity={0.85}>
          <Text style={styles.ctaBtnText}>
            {isLast ? `📷 ${t("get_started", lang)}` : `${t("next", lang)} →`}
          </Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.cream },
  topBar: { alignItems: "flex-end", paddingHorizontal: 20, marginBottom: 8 },
  skipBtn: { paddingHorizontal: 16, paddingVertical: 10, borderRadius: 12, backgroundColor: COLORS.surface },
  skipText: { color: COLORS.slate, fontSize: 16, fontWeight: "600" },
  slideArea: { flex: 1, justifyContent: "center", overflow: "hidden" },
  slideContent: { alignItems: "center", paddingHorizontal: 28 },
  emojiCircle: {
    width: 120,
    height: 120,
    borderRadius: 32,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 32,
  },
  emoji: { fontSize: 56 },
  titleHi: {
    fontSize: 30,
    fontWeight: "800",
    color: COLORS.charcoal,
    textAlign: "center",
    marginBottom: 4,
  },
  titleEn: {
    fontSize: 16,
    color: COLORS.slate,
    textAlign: "center",
    marginBottom: 16,
  },
  bodyHi: {
    fontSize: 19,
    lineHeight: 30,
    color: COLORS.charcoal,
    textAlign: "center",
    marginBottom: 8,
  },
  bodyEn: {
    fontSize: 15,
    lineHeight: 22,
    color: COLORS.slate,
    textAlign: "center",
  },
  dotsRow: { flexDirection: "row", justifyContent: "center", gap: 8, marginVertical: 24 },
  dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: COLORS.border },
  dotActive: { width: 24, borderRadius: 5, backgroundColor: COLORS.primary },
  footer: { paddingHorizontal: 20 },
  ctaBtn: {
    backgroundColor: COLORS.primary,
    borderRadius: 16,
    minHeight: 64,
    alignItems: "center",
    justifyContent: "center",
    shadowColor: COLORS.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 6,
  },
  ctaBtnText: { color: "#FFFFFF", fontSize: 18, fontWeight: "700" },
})
