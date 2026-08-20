const API_BASE = "http://localhost:8000"

export async function fetchLogs() {
  const res = await fetch(`${API_BASE}/logs`)
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
