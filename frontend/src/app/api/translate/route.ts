export const runtime = "nodejs"

import { BACKEND_URL } from "@/lib/constants"

export async function POST(request: Request) {
  try {
    const body = await request.json()
    const response = await fetch(`${BACKEND_URL}/translate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    })
    const data = await response.json()
    return Response.json(data, { status: response.status })
  } catch (err) {
    const message = err instanceof Error ? err.message : "Backend unreachable"
    return Response.json(
      { error: "Translation service unavailable", detail: message },
      { status: 503 }
    )
  }
}
