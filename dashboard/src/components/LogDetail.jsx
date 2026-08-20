import { useEffect, useState } from "react"
import { fetchLog } from "../api"

function formatBody(body) {
  if (!body) return "(empty)"
  try {
    return JSON.stringify(JSON.parse(body), null, 2)
  } catch {
    return body
  }
}

export default function LogDetail({ logId }) {
  const [log, setLog] = useState(null)

  useEffect(() => {
    if (logId == null) {
      setLog(null)
      return
    }
    let cancelled = false
    fetchLog(logId).then((data) => {
      if (!cancelled) setLog(data)
    })
    return () => {
      cancelled = true
    }
  }, [logId])

  if (logId == null) {
    return <div className="detail-placeholder">Select a request to see full details.</div>
  }

  if (!log) {
    return <div className="detail-placeholder">Loading…</div>
  }

  return (
    <div className="log-detail">
      <h2>
        {log.method} <span className="detail-url">{log.url}</span>
      </h2>

      <dl className="detail-meta">
        <dt>Status</dt>
        <dd>{log.status_code}</dd>
        <dt>TTFB</dt>
        <dd>{Math.round(log.ttfb_ms)} ms</dd>
        <dt>Total</dt>
        <dd>{Math.round(log.total_ms)} ms</dd>
        <dt>API key</dt>
        <dd>{log.api_key_label ?? "—"}</dd>
        <dt>Time</dt>
        <dd>{new Date(log.created_at).toLocaleString()}</dd>
      </dl>

      <h3>Request headers</h3>
      <pre>{JSON.stringify(log.request_headers, null, 2)}</pre>

      <h3>Request body</h3>
      <pre>{formatBody(log.request_body)}</pre>

      <h3>Response headers</h3>
      <pre>{JSON.stringify(log.response_headers, null, 2)}</pre>

      <h3>Response body</h3>
      <pre>{formatBody(log.response_body)}</pre>
    </div>
  )
}
