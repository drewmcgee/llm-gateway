import { useEffect, useState } from "react"
import { fetchLog } from "../api"
import { parseSseBody } from "../sse"

// Usage is reported differently per endpoint: chat and responses give all
// three counts, embeddings give a prompt count and no completion count, and a
// streamed response gives none at all. Show whatever is actually there.
function formatTokens({ prompt_tokens, completion_tokens, total_tokens }) {
  const breakdown = [
    prompt_tokens != null && `${prompt_tokens} prompt`,
    completion_tokens != null && `${completion_tokens} completion`,
  ].filter(Boolean)

  if (total_tokens == null) return breakdown.length > 0 ? breakdown.join(" / ") : "—"
  return breakdown.length > 0 ? `${total_tokens} (${breakdown.join(" / ")})` : `${total_tokens}`
}

// Streamed bodies get a summary + the reconstructed output by default; the
// raw frames are one click away.
function ResponseBody({ body }) {
  const [showRaw, setShowRaw] = useState(false)
  const stream = parseSseBody(body)

  if (!stream) return <pre>{formatBody(body)}</pre>

  return (
    <>
      <div className="sse-toolbar">
        <span>
          {stream.frameCount.toLocaleString()} streamed events ·{" "}
          {stream.byteLength.toLocaleString()} bytes
        </span>
        <button onClick={() => setShowRaw((raw) => !raw)}>
          {showRaw ? "Show summary" : "Show raw frames"}
        </button>
      </div>

      {showRaw ? (
        <pre>{body}</pre>
      ) : (
        <>
          <ul className="sse-events">
            {stream.events.map(([name, count]) => (
              <li key={name}>
                <span className="sse-count">{count.toLocaleString()}×</span> {name}
              </li>
            ))}
          </ul>
          {stream.text !== "" && (
            <>
              <h4>Reconstructed output</h4>
              <pre className="sse-text">{stream.text}</pre>
            </>
          )}
        </>
      )}
    </>
  )
}

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
        <dt>Source</dt>
        <dd>{log.source === "gateway" ? "Gateway (rejected before OpenAI)" : "OpenAI"}</dd>
        <dt>TTFB</dt>
        <dd>{Math.round(log.ttfb_ms)} ms</dd>
        <dt>Total</dt>
        <dd>{Math.round(log.total_ms)} ms</dd>
        <dt>API key</dt>
        <dd>{log.api_key_label ?? "—"}</dd>
        <dt>Model</dt>
        <dd>{log.model ?? "—"}</dd>
        <dt>Tokens</dt>
        <dd>{formatTokens(log)}</dd>
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
      <ResponseBody key={log.id} body={log.response_body} />
    </div>
  )
}
