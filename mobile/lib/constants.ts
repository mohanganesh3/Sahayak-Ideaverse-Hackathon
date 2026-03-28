import Constants from "expo-constants"
import { Platform } from "react-native"

export const SUPPORTED_LANGUAGES = [
  { code: "en", nativeLabel: "English",   label: "English"   },
  { code: "hi", nativeLabel: "हिन्दी",    label: "Hindi"     },
  { code: "ta", nativeLabel: "தமிழ்",     label: "Tamil"     },
  { code: "te", nativeLabel: "తెలుగు",    label: "Telugu"    },
  { code: "kn", nativeLabel: "ಕನ್ನಡ",    label: "Kannada"   },
  { code: "ml", nativeLabel: "മലയാളം",   label: "Malayalam" },
  { code: "mr", nativeLabel: "मराठी",     label: "Marathi"   },
  { code: "bn", nativeLabel: "বাংলা",     label: "Bengali"   },
  { code: "gu", nativeLabel: "ગુજરાતી",  label: "Gujarati"  },
  { code: "pa", nativeLabel: "ਪੰਜਾਬੀ",   label: "Punjabi"   },
] as const

export const PRESCRIBER_SOURCES = [
  { value: "doctor",       labelHi: "Doctor ne di",     label: "Doctor Prescribed", icon: "👨‍⚕️" },
  { value: "medical_shop", labelHi: "Medical store se", label: "From Pharmacy",     icon: "🏪" },
  { value: "self",         labelHi: "Khud se li",       label: "Self-Started",      icon: "🙋" },
] as const

export const SEVERITY_CONFIG = {
  critical: {
    border: "#D32F2F",
    bg: "#FFEBEE",
    text: "#B71C1C",
    badgeBg: "#FFCDD2",
    icon: "⛔",
    label: "CRITICAL",
    action: "Consult Doctor Immediately",
  },
  major: {
    border: "#E64A19",
    bg: "#FBE9E7",
    text: "#BF360C",
    badgeBg: "#FFCCBC",
    icon: "⚠️",
    label: "MAJOR",
    action: "Discuss with Doctor",
  },
  moderate: {
    border: "#F9A825",
    bg: "#FFFDE7",
    text: "#F57F17",
    badgeBg: "#FFF9C4",
    icon: "△",
    label: "MODERATE",
    action: "Monitor Carefully",
  },
  minor: {
    border: "#0288D1",
    bg: "#E1F5FE",
    text: "#01579B",
    badgeBg: "#B3E5FC",
    icon: "ℹ️",
    label: "MINOR",
    action: "Be Aware",
  },
  doctor_review: {
    border: "#8D6E63",
    bg: "#FBE9E7",
    text: "#5D4037",
    badgeBg: "#EFDBD2",
    icon: "🩺",
    label: "DOCTOR REVIEW",
    action: "Discuss with Doctor",
  },
  unknown: {
    border: "#7B1FA2",
    bg: "#F3E5F5",
    text: "#4A148C",
    badgeBg: "#E1BEE7",
    icon: "❓",
    label: "UNKNOWN",
    action: "Seek Guidance",
  },
} as const

function resolveApiHost(): string {
  const expoHost =
    Constants.expoConfig?.hostUri?.split(":")[0] ??
    Constants.expoGoConfig?.debuggerHost?.split(":")[0]

  if (expoHost) {
    return expoHost
  }

  if (Platform.OS === "android") {
    return "10.0.2.2"
  }

  return "127.0.0.1"
}

const explicitApiBaseUrl =
  process.env.EXPO_PUBLIC_API_BASE_URL?.trim() ||
  String(Constants.expoConfig?.extra?.apiBaseUrl ?? "").trim()

export const API_BASE_URL = explicitApiBaseUrl.replace(/\/+$/, "") || `http://${resolveApiHost()}:8000`

export const COLORS = {
  primary: "#0D9488",
  primaryLight: "#5EEAD4",
  primaryDark: "#0F766E",
  saffron: "#F59E0B",
  cream: "#FAF9F6",
  surface: "#F5F0EB",
  charcoal: "#1E293B",
  slate: "#64748B",
  border: "#E2D9D0",
  white: "#FFFFFF",
  black: "#000000",
} as const
