import { View, TouchableOpacity, Text, StyleSheet, ActivityIndicator } from "react-native"
import { Ionicons } from "@expo/vector-icons"
import { COLORS } from "../../lib/constants"

interface VoiceBarProps {
  /** Text to read aloud */
  text: string
  /** Current language code */
  language: string
  /** TTS state */
  ttsPlaying: boolean
  onSpeak: (text: string, lang: string) => void
  onStopSpeak: () => void
  /** STT state (optional — omit for listen-only) */
  sttRecording?: boolean
  sttTranscribing?: boolean
  onStartRecord?: () => void
  onStopRecord?: () => void
}

/**
 * A compact row with Listen 🔊 and optionally Speak 🎤 buttons.
 * Used across screens to provide voice accessibility.
 */
export function VoiceBar({
  text,
  language,
  ttsPlaying,
  onSpeak,
  onStopSpeak,
  sttRecording,
  sttTranscribing,
  onStartRecord,
  onStopRecord,
}: VoiceBarProps) {
  return (
    <View style={styles.row}>
      {/* Listen button */}
      <TouchableOpacity
        style={[styles.btn, ttsPlaying && styles.btnActive]}
        onPress={() => (ttsPlaying ? onStopSpeak() : onSpeak(text, language))}
        activeOpacity={0.7}
        accessibilityLabel={ttsPlaying ? "Stop listening" : "Listen"}
      >
        <Ionicons
          name={ttsPlaying ? "stop-circle" : "volume-high"}
          size={18}
          color={ttsPlaying ? "#FFF" : COLORS.primary}
        />
        <Text style={[styles.btnText, ttsPlaying && styles.btnTextActive]}>
          {ttsPlaying ? "Stop" : "🔊"}
        </Text>
      </TouchableOpacity>

      {/* Speak/Record button */}
      {onStartRecord && onStopRecord && (
        <TouchableOpacity
          style={[
            styles.btn,
            sttRecording && styles.btnRecording,
            sttTranscribing && styles.btnTranscribing,
          ]}
          onPress={() => (sttRecording ? onStopRecord() : onStartRecord())}
          disabled={sttTranscribing}
          activeOpacity={0.7}
          accessibilityLabel={sttRecording ? "Stop recording" : "Speak"}
        >
          {sttTranscribing ? (
            <ActivityIndicator size="small" color={COLORS.primary} />
          ) : (
            <>
              <Ionicons
                name={sttRecording ? "stop-circle" : "mic"}
                size={18}
                color={sttRecording ? "#FFF" : COLORS.saffron}
              />
              <Text
                style={[
                  styles.btnText,
                  sttRecording && styles.btnTextActive,
                ]}
              >
                {sttRecording ? "Stop" : "🎤"}
              </Text>
            </>
          )}
        </TouchableOpacity>
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: 8, marginTop: 6, marginBottom: 4 },
  btn: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 10,
    backgroundColor: `${COLORS.primary}10`,
    borderWidth: 1.5,
    borderColor: `${COLORS.primary}30`,
  },
  btnActive: {
    backgroundColor: COLORS.primary,
    borderColor: COLORS.primary,
  },
  btnRecording: {
    backgroundColor: "#EF4444",
    borderColor: "#EF4444",
  },
  btnTranscribing: {
    backgroundColor: `${COLORS.saffron}15`,
    borderColor: `${COLORS.saffron}40`,
  },
  btnText: { fontSize: 13, fontWeight: "700", color: COLORS.charcoal },
  btnTextActive: { color: "#FFF" },
})
