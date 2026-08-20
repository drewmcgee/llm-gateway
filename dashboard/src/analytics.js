// Validated against the dashboard's dark surface with the dataviz skill's
// CVD-safety checker: run `node scripts/validate_palette.js` before changing.
export const MODEL_COLORS = [
  "#3987e5", // blue
  "#d95926", // orange
  "#199e70", // aqua
  "#c98500", // yellow
  "#d55181", // magenta
  "#008300", // green
]
export const OTHER_COLOR = "#5b6472"
export const SUCCESS_COLOR = "#3987e5"
export const ERROR_COLOR = "#e66767"

export function bucketRequestsByTime(logs, bucketMs = 60000) {
  const buckets = new Map()
  for (const log of logs) {
    const time = Math.floor(new Date(log.created_at).getTime() / bucketMs) * bucketMs
    const bucket = buckets.get(time) ?? { time, success: 0, error: 0 }
    if (log.status_code < 400) bucket.success += 1
    else bucket.error += 1
    buckets.set(time, bucket)
  }
  return [...buckets.values()].sort((a, b) => a.time - b.time)
}

export function topModels(logs, maxSlots = MODEL_COLORS.length) {
  const counts = new Map()
  for (const log of logs) {
    const name = log.model ?? "unknown"
    counts.set(name, (counts.get(name) ?? 0) + 1)
  }
  const sorted = [...counts.entries()].sort((a, b) => b[1] - a[1])
  const top = sorted.slice(0, maxSlots).map(([model, count]) => ({ model, count }))
  const otherCount = sorted.slice(maxSlots).reduce((sum, [, count]) => sum + count, 0)
  if (otherCount > 0) top.push({ model: "Other", count: otherCount })
  return top
}

// Colors are assigned by first-seen order and cached per model name, not by
// current rank -- so a model's color stays put even if its position in the
// "top models" list shifts as new requests arrive.
export function assignModelColors(logs) {
  const byFirstSeen = [...logs].sort((a, b) => a.id - b.id)
  const colorByModel = new Map()
  for (const log of byFirstSeen) {
    const name = log.model ?? "unknown"
    if (!colorByModel.has(name) && colorByModel.size < MODEL_COLORS.length) {
      colorByModel.set(name, MODEL_COLORS[colorByModel.size])
    }
  }
  return colorByModel
}

export function tokenSummary(logs) {
  let totalTokens = 0
  let requestsWithUsage = 0
  for (const log of logs) {
    if (log.total_tokens != null) {
      totalTokens += log.total_tokens
      requestsWithUsage += 1
    }
  }
  return {
    totalTokens,
    requestsWithUsage,
    requestsWithoutUsage: logs.length - requestsWithUsage,
  }
}
