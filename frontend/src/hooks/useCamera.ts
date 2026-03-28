"use client"

import { useRef, useState, useCallback, useEffect } from "react"

export type CameraState = "idle" | "requesting" | "active" | "error" | "captured"

export function useCamera() {
  const videoRef = useRef<HTMLVideoElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const [state, setState] = useState<CameraState>("idle")
  const [error, setError] = useState<string | null>(null)
  const [capturedBlob, setCapturedBlob] = useState<Blob | null>(null)
  const [capturedUrl, setCapturedUrl] = useState<string | null>(null)

  const startCamera = useCallback(async () => {
    setState("requesting")
    setError(null)

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: {
          facingMode: "environment",
          width: { ideal: 1920 },
          height: { ideal: 1080 },
        },
      })
      streamRef.current = stream
      if (videoRef.current) {
        videoRef.current.srcObject = stream
        await videoRef.current.play()
      }
      setState("active")
    } catch {
      // Fallback: try any camera
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: true })
        streamRef.current = stream
        if (videoRef.current) {
          videoRef.current.srcObject = stream
          await videoRef.current.play()
        }
        setState("active")
      } catch (err) {
        const msg = err instanceof Error ? err.message : "Camera access denied"
        setError(msg)
        setState("error")
      }
    }
  }, [])

  const stopCamera = useCallback(() => {
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop())
      streamRef.current = null
    }
    setState("idle")
  }, [])

  const capture = useCallback(() => {
    const video = videoRef.current
    const canvas = canvasRef.current
    if (!video || !canvas) return

    canvas.width = video.videoWidth
    canvas.height = video.videoHeight
    const ctx = canvas.getContext("2d")
    if (!ctx) return

    ctx.drawImage(video, 0, 0, canvas.width, canvas.height)

    canvas.toBlob(
      (blob) => {
        if (!blob) return
        if (capturedUrl) URL.revokeObjectURL(capturedUrl)
        const url = URL.createObjectURL(blob)
        setCapturedBlob(blob)
        setCapturedUrl(url)
        setState("captured")
        stopCamera()
      },
      "image/jpeg",
      0.92
    )
  }, [capturedUrl, stopCamera])

  const retake = useCallback(() => {
    if (capturedUrl) {
      URL.revokeObjectURL(capturedUrl)
      setCapturedUrl(null)
    }
    setCapturedBlob(null)
    startCamera()
  }, [capturedUrl, startCamera])

  useEffect(() => {
    return () => {
      if (streamRef.current) {
        streamRef.current.getTracks().forEach((t) => t.stop())
      }
      if (capturedUrl) {
        URL.revokeObjectURL(capturedUrl)
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return {
    videoRef,
    canvasRef,
    state,
    error,
    capturedBlob,
    capturedUrl,
    startCamera,
    stopCamera,
    capture,
    retake,
  }
}
