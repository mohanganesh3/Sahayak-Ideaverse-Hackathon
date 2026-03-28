import { useState } from "react"
import {
  View,
  Text,
  TouchableOpacity,
  ScrollView,
  StyleSheet,
  FlatList,
} from "react-native"
import { useRouter } from "expo-router"
import Animated, { FadeInDown } from "react-native-reanimated"
import { useSafeAreaInsets } from "react-native-safe-area-context"
import { Ionicons } from "@expo/vector-icons"
import { useAppStore } from "../hooks/useAppStore"
import { SUPPORTED_LANGUAGES, COLORS } from "../lib/constants"
import { t } from "../lib/i18n"

export default function LanguageScreen() {
  const router = useRouter()
  const insets = useSafeAreaInsets()
  const { language, setLanguage } = useAppStore()
  const [selected, setSelected] = useState(language)

  function handleContinue() {
    setLanguage(selected)
    router.push("/onboarding")
  }

  const languages = [...SUPPORTED_LANGUAGES]

  return (
    <View style={[styles.container, { paddingTop: insets.top + 24, paddingBottom: insets.bottom + 16 }]}>
      <View style={styles.heading}>
        <Text style={styles.title}>{t("choose_language", selected)}</Text>
      </View>

      <FlatList
        data={languages}
        keyExtractor={(item) => item.code}
        numColumns={2}
        contentContainerStyle={styles.grid}
        columnWrapperStyle={styles.row}
        renderItem={({ item, index }) => {
          const isSelected = selected === item.code
          return (
            <Animated.View
              entering={FadeInDown.delay(index * 60).duration(300)}
              style={styles.tileWrapper}
            >
              <TouchableOpacity
                onPress={() => setSelected(item.code)}
                activeOpacity={0.75}
                accessibilityRole="radio"
                accessibilityState={{ checked: isSelected }}
                style={[
                  styles.tile,
                  isSelected && styles.tileSelected,
                ]}
              >
                {isSelected && (
                  <Ionicons
                    name="checkmark-circle"
                    size={18}
                    color="rgba(255,255,255,0.85)"
                    style={styles.checkIcon}
                  />
                )}
                <Text style={[styles.nativeLabel, isSelected && styles.nativeLabelSelected]}>
                  {item.nativeLabel}
                </Text>
                {item.code !== "en" && (
                  <Text style={[styles.latinLabel, isSelected && styles.latinLabelSelected]}>
                    {item.label}
                  </Text>
                )}
              </TouchableOpacity>
            </Animated.View>
          )
        }}
      />

      <View style={styles.footer}>
        <TouchableOpacity
          onPress={handleContinue}
          style={styles.continueBtn}
          activeOpacity={0.85}
          accessibilityRole="button"
          accessibilityLabel="Continue"
        >
          <Text style={styles.continueBtnText}>{t("continue", selected)}</Text>
        </TouchableOpacity>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: COLORS.cream },
  heading: { paddingHorizontal: 20, marginBottom: 20 },
  title: { fontSize: 32, fontWeight: "700", color: COLORS.charcoal },
  subtitle: { fontSize: 18, color: COLORS.slate, marginTop: 4 },
  grid: { paddingHorizontal: 16, paddingBottom: 8 },
  row: { gap: 12 },
  tileWrapper: { flex: 1 },
  tile: {
    flex: 1,
    minHeight: 72,
    borderRadius: 16,
    borderWidth: 2,
    borderColor: COLORS.border,
    backgroundColor: COLORS.surface,
    alignItems: "center",
    justifyContent: "center",
    paddingVertical: 14,
    paddingHorizontal: 8,
    marginBottom: 12,
    position: "relative",
  },
  tileSelected: {
    borderColor: COLORS.primary,
    backgroundColor: COLORS.primary,
    shadowColor: COLORS.primary,
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.3,
    shadowRadius: 8,
    elevation: 6,
  },
  checkIcon: { position: "absolute", top: 8, right: 8 },
  nativeLabel: { fontSize: 22, fontWeight: "700", color: COLORS.charcoal, textAlign: "center" },
  nativeLabelSelected: { color: "#FFFFFF" },
  latinLabel: { fontSize: 13, color: COLORS.slate, marginTop: 3, textAlign: "center" },
  latinLabelSelected: { color: "rgba(255,255,255,0.75)" },
  footer: { paddingHorizontal: 20, paddingTop: 8 },
  continueBtn: {
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
  continueBtnText: { color: "#FFFFFF", fontSize: 18, fontWeight: "700" },
})
