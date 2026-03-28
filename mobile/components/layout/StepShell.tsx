import { View, TouchableOpacity, StyleSheet } from "react-native"
import { useSafeAreaInsets } from "react-native-safe-area-context"
import { useRouter } from "expo-router"
import { Ionicons } from "@expo/vector-icons"
import { COLORS } from "../../lib/constants"

interface StepShellProps {
  step: number
  totalSteps?: number
  showBack?: boolean
  children: React.ReactNode
}

export function StepShell({ step, totalSteps = 8, showBack = true, children }: StepShellProps) {
  const router = useRouter()
  const insets = useSafeAreaInsets()

  return (
    <View style={[styles.container, { paddingTop: insets.top + 8 }]}>
      {/* Header */}
      <View style={styles.header}>
        {showBack ? (
          <TouchableOpacity
            onPress={() => router.back()}
            style={styles.backBtn}
            accessibilityLabel="Go back"
            accessibilityRole="button"
          >
            <Ionicons name="arrow-back" size={26} color={COLORS.charcoal} />
          </TouchableOpacity>
        ) : (
          <View style={styles.backBtn} />
        )}

        {/* Progress dots */}
        <View style={styles.dotsRow}>
          {Array.from({ length: totalSteps }).map((_, i) => {
            const filled = i + 1 <= step
            const active = i + 1 === step
            return (
              <View
                key={i}
                style={[
                  styles.dot,
                  active && styles.dotActive,
                  filled && !active && styles.dotFilled,
                ]}
              />
            )
          })}
        </View>

        <View style={styles.backBtn} />
      </View>

      {/* Content */}
      <View style={[styles.content, { paddingBottom: insets.bottom + 16 }]}>
        {children}
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: COLORS.cream,
  },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingBottom: 8,
  },
  backBtn: {
    width: 60,
    height: 60,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: 16,
  },
  dotsRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
  },
  dot: {
    width: 10,
    height: 10,
    borderRadius: 5,
    backgroundColor: COLORS.border,
  },
  content: {
    flex: 1,
  },
  dotActive: {
    width: 24,
    height: 10,
    borderRadius: 5,
    backgroundColor: COLORS.primary,
  },
  dotFilled: {
    backgroundColor: COLORS.primaryLight,
  },
})
