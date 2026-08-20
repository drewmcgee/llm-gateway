// A streamed response body is hundreds of SSE frames -- one per token, plus
// encrypted reasoning blobs -- so rendering it raw makes the detail pane
// unreadable. Parse it into something worth looking at, and keep the raw text
// available for when you actually need to inspect the wire format.

function textDelta(data) {
  // Responses API: {type: "response.output_text.delta", delta: "..."}
  if (typeof data.delta === "string") return data.delta
  // Chat Completions: {choices: [{delta: {content: "..."}}]}
  const content = data.choices?.[0]?.delta?.content
  return typeof content === "string" ? content : ""
}

export function parseSseBody(body) {
  if (typeof body !== "string" || !body.includes("data:")) return null

  const frames = body.split(/\n\n+/).filter((frame) => frame.trim() !== "")
  const counts = new Map()
  const deltas = []
  let parsedAny = false

  for (const frame of frames) {
    let name = null
    let payload = null
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) name = line.slice("event:".length).trim()
      else if (line.startsWith("data:")) payload = line.slice("data:".length).trim()
    }

    if (payload && payload !== "[DONE]") {
      try {
        const data = JSON.parse(payload)
        parsedAny = true
        name = name ?? data.type ?? "message"
        deltas.push(textDelta(data))
      } catch {
        // Unparseable frame still counts -- just under its event name.
      }
    }

    const key = name ?? (payload === "[DONE]" ? "[DONE]" : "data")
    counts.set(key, (counts.get(key) ?? 0) + 1)
  }

  // A body that merely mentions "data:" isn't necessarily a stream.
  if (!parsedAny) return null

  return {
    frameCount: frames.length,
    byteLength: body.length,
    events: [...counts.entries()].sort((a, b) => b[1] - a[1]),
    text: deltas.join(""),
  }
}
