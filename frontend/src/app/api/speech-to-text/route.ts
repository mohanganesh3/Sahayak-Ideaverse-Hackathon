export const runtime = "nodejs"

import { BACKEND_URL } from "@/lib/constants"

export async function POST(request: Request) {
  try {
    const formData = await request.formData()
    const response = await fetch(`${BACKEND_URL}/speech-to-text`, {
      method: "POST",
      body: formData,
    })
    const data = await response.json()
    return Response.json(data, { status: response.status })
  } catch (err) {
    const message = err instanceof Error ? err.message : "Backend unreachable"
    return Response.json(
      { error: "Speech-to-text service unavailable", detail: message },
      { status: 503 }
    )
  }
}
