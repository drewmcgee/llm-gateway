const API_BASE = "http://localhost:8001"
export const PAGE_SIZE = 200

export async function fetchLogs({ beforeId } = {}) {
  const params = new URLSearchParams({ limit: PAGE_SIZE })
  if (beforeId != null) params.set("before_id", beforeId)
  const res = await fetch(`${API_BASE}/logs?${params}`)
  if (!res.ok) throw new Error(`GET /logs failed: ${res.status}`)
  return res.json()
}

export async function fetchLog(id) {
  const res = await fetch(`${API_BASE}/logs/${id}`)
  if (!res.ok) throw new Error(`GET /logs/${id} failed: ${res.status}`)
  return res.json()
}

export function streamUrl() {
  return `${API_BASE}/logs/stream`
}
