/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./app/**/*.{js,jsx,ts,tsx}", "./components/**/*.{js,jsx,ts,tsx}"],
  presets: [require("nativewind/preset")],
  theme: {
    extend: {
      colors: {
        primary: "#0D9488",      // Warm teal
        "primary-light": "#5EEAD4",
        "primary-dark": "#0F766E",
        saffron: "#F59E0B",      // Cultural accent
        cream: "#FAF9F6",        // Background
        surface: "#F5F0EB",      // Card background
        charcoal: "#1E293B",     // Body text
        slate: "#64748B",        // Secondary text

        // Severity
        "severity-critical": "#D32F2F",
        "severity-major": "#E64A19",
        "severity-caution": "#F9A825",
        "severity-safe": "#0288D1",
        "severity-info": "#7B1FA2",
      },
      fontFamily: {
        sans: ["Inter", "System"],
        indic: ["NotoSansDevanagari", "System"],
      },
      fontSize: {
        "body-lg": ["20px", { lineHeight: "32px" }],
        "body": ["18px", { lineHeight: "28px" }],
        "caption": ["16px", { lineHeight: "24px" }],
      },
      minHeight: {
        touch: "60px",
        shutter: "72px",
      },
      minWidth: {
        touch: "60px",
      },
      borderRadius: {
        "2xl": "16px",
        "3xl": "24px",
        "4xl": "32px",
      },
    },
  },
  plugins: [],
}
