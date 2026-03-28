export const SUPPORTED_LANGUAGES = [
  { code: "en", label: "English",    nativeLabel: "English",   fontClass: "" },
  { code: "hi", label: "Hindi",      nativeLabel: "हिन्दी",    fontClass: "font-indic" },
  { code: "ta", label: "Tamil",      nativeLabel: "தமிழ்",     fontClass: "font-indic" },
  { code: "te", label: "Telugu",     nativeLabel: "తెలుగు",    fontClass: "font-indic" },
  { code: "kn", label: "Kannada",    nativeLabel: "ಕನ್ನಡ",    fontClass: "font-indic" },
  { code: "ml", label: "Malayalam",  nativeLabel: "മലയാളം",   fontClass: "font-indic" },
  { code: "mr", label: "Marathi",    nativeLabel: "मराठी",     fontClass: "font-indic" },
  { code: "bn", label: "Bengali",    nativeLabel: "বাংলা",     fontClass: "font-indic" },
  { code: "gu", label: "Gujarati",   nativeLabel: "ગુજરાતી",  fontClass: "font-indic" },
  { code: "pa", label: "Punjabi",    nativeLabel: "ਪੰਜਾਬੀ",   fontClass: "font-indic" },
] as const

export type LangCode = typeof SUPPORTED_LANGUAGES[number]["code"]

export const PRESCRIBER_SOURCES = [
  { value: "doctor",       label: "Doctor Prescribed", labelHi: "Doctor ne di",      icon: "👨‍⚕️" },
  { value: "medical_shop", label: "From Pharmacy",     labelHi: "Medical store se",  icon: "🏪" },
  { value: "self",         label: "Self-Started",      labelHi: "Khud se li",        icon: "🙋" },
] as const

export const SEVERITY_CONFIG = {
  critical: {
    color: "text-red-700",
    bg: "bg-red-50",
    border: "border-red-500",
    badge: "bg-red-100 text-red-700",
    label: "CRITICAL",
    labelHi: "गंभीर",
    icon: "⛔",
    action: "Consult Doctor Immediately",
  },
  major: {
    color: "text-orange-700",
    bg: "bg-orange-50",
    border: "border-orange-500",
    badge: "bg-orange-100 text-orange-700",
    label: "MAJOR",
    labelHi: "प्रमुख",
    icon: "⚠️",
    action: "Discuss with Doctor",
  },
  moderate: {
    color: "text-amber-700",
    bg: "bg-amber-50",
    border: "border-amber-500",
    badge: "bg-amber-100 text-amber-700",
    label: "MODERATE",
    labelHi: "मध्यम",
    icon: "△",
    action: "Monitor Carefully",
  },
  minor: {
    color: "text-blue-700",
    bg: "bg-blue-50",
    border: "border-blue-500",
    badge: "bg-blue-100 text-blue-700",
    label: "MINOR",
    labelHi: "हल्का",
    icon: "ℹ️",
    action: "Be Aware",
  },
  unknown: {
    color: "text-purple-700",
    bg: "bg-purple-50",
    border: "border-purple-500",
    badge: "bg-purple-100 text-purple-700",
    label: "UNKNOWN",
    labelHi: "अज्ञात",
    icon: "❓",
    action: "Seek Guidance",
  },
} as const

export const STEPS = [
  { id: 1, path: "/language",    label: "Language"  },
  { id: 2, path: "/patient",     label: "Profile"   },
  { id: 3, path: "/camera",      label: "Scan"      },
  { id: 4, path: "/processing",  label: "Analyzing" },
  { id: 5, path: "/confirm",     label: "Confirm"   },
  { id: 6, path: "/categorize",  label: "Source"    },
  { id: 7, path: "/report",      label: "Report"    },
] as const

export const PATIENT_CONDITIONS = [
  { id: "diabetes",      label: "Diabetes",           labelHi: "मधुमेह" },
  { id: "hypertension",  label: "Hypertension",       labelHi: "हाई BP" },
  { id: "heart_disease", label: "Heart Disease",      labelHi: "हृदय रोग" },
  { id: "kidney_disease",label: "Kidney Disease",     labelHi: "गुर्दे की बीमारी" },
  { id: "liver_disease", label: "Liver Disease",      labelHi: "लिवर की बीमारी" },
  { id: "asthma",        label: "Asthma / COPD",      labelHi: "दमा / सीओपीडी" },
  { id: "arthritis",     label: "Arthritis",          labelHi: "गठिया" },
  { id: "thyroid",       label: "Thyroid Disorder",   labelHi: "थायरॉइड" },
  { id: "depression",    label: "Depression/Anxiety", labelHi: "अवसाद / चिंता" },
  { id: "parkinsons",    label: "Parkinson's",        labelHi: "पार्किंसन" },
  { id: "dementia",      label: "Dementia",           labelHi: "मनोभ्रंश" },
] as const

export const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000"
