import { useRef, useState, useCallback } from "react"
import { Audio } from "expo-av"
import { textToSpeech, speechToText } from "../lib/api"

/**
 * Hook for Text-to-Speech: plays translated text via Sarvam AI TTS.
 */
export function useTTS() {
  const [playing, setPlaying] = useState(false)
  const soundRef = useRef<Audio.Sound | null>(null)

  const speak = useCallback(async (text: string, language: string) => {
    try {
      // Stop any previous playback
      if (soundRef.current) {
        await soundRef.current.unloadAsync()
        soundRef.current = null
      }
      setPlaying(true)

      const { audio_base64 } = await textToSpeech(text, language)
      const { sound } = await Audio.Sound.createAsync(
        { uri: `data:audio/wav;base64,${audio_base64}` },
        { shouldPlay: true }
      )
      soundRef.current = sound

      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) {
          setPlaying(false)
          sound.unloadAsync()
          soundRef.current = null
        }
      })
    } catch {
      setPlaying(false)
    }
  }, [])

  const stop = useCallback(async () => {
    if (soundRef.current) {
      await soundRef.current.stopAsync()
      await soundRef.current.unloadAsync()
      soundRef.current = null
    }
    setPlaying(false)
  }, [])

  return { speak, stop, playing }
}

/**
 * Hook for Speech-to-Text: records audio and transcribes via Sarvam AI STT.
 */
export function useSTT() {
  const [recording, setRecording] = useState(false)
  const [transcribing, setTranscribing] = useState(false)
  const recordingRef = useRef<Audio.Recording | null>(null)

  const startRecording = useCallback(async () => {
    try {
      const permission = await Audio.requestPermissionsAsync()
      if (!permission.granted) return

      await Audio.setAudioModeAsync({
        allowsRecordingIOS: true,
        playsInSilentModeIOS: true,
      })

      const { recording: rec } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY
      )
      recordingRef.current = rec
      setRecording(true)
    } catch {
      setRecording(false)
    }
  }, [])

  const stopRecording = useCallback(
    async (language: string = "auto"): Promise<string | null> => {
      if (!recordingRef.current) return null
      try {
        setRecording(false)
        setTranscribing(true)

        await recordingRef.current.stopAndUnloadAsync()
        const uri = recordingRef.current.getURI()
        recordingRef.current = null

        await Audio.setAudioModeAsync({ allowsRecordingIOS: false })

        if (!uri) return null

        const result = await speechToText(uri, language)
        return result.transcript
      } catch {
        return null
      } finally {
        setTranscribing(false)
      }
    },
    []
  )

  const cancelRecording = useCallback(async () => {
    if (recordingRef.current) {
      try {
        await recordingRef.current.stopAndUnloadAsync()
      } catch {
        /* ignore */
      }
      recordingRef.current = null
    }
    setRecording(false)
    setTranscribing(false)
  }, [])

  return { recording, transcribing, startRecording, stopRecording, cancelRecording }
}
