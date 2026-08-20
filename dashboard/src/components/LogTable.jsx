function statusClass(status) {
  if (status >= 500) return "status-error"
  if (status >= 400) return "status-warn"
  return "status-ok"
}

export default function LogTable({ logs, selectedId, onSelect, hasMore, onLoadOlder }) {
  if (logs.length === 0) {
    return (
      <div className="empty-state">
        No requests yet — send one through the proxy to see it here.
      </div>
    )
  }

  return (
    <div className="table-scroll">
      <table className="log-table">
        <thead>
          <tr>
            <th>Method</th>
            <th>URL</th>
            <th>Status</th>
            <th>Duration</th>
            <th>Key</th>
            <th>Time</th>
          </tr>
        </thead>
        <tbody>
          {logs.map((log) => (
            <tr
              key={log.id}
              className={log.id === selectedId ? "selected" : ""}
              onClick={() => onSelect(log.id)}
            >
              <td>
                <span className={`method-badge method-${log.method.toLowerCase()}`}>
                  {log.method}
                </span>
              </td>
              <td className="url-cell">{log.url}</td>
              <td>
                <span className={`status-badge ${statusClass(log.status_code)}`}>
                  {log.status_code}
                </span>
                {log.source === "gateway" && (
                  <span className="source-badge" title="Response authored by the gateway (auth rejection or upstream failure) — never completed against OpenAI">
                    GATEWAY
                  </span>
                )}
              </td>
              <td>{Math.round(log.total_ms)} ms</td>
              <td>{log.api_key_label ?? "—"}</td>
              <td>{new Date(log.created_at).toLocaleTimeString()}</td>
            </tr>
          ))}
          {hasMore && (
            <tr className="load-older-row">
              <td colSpan={6}>
                <button onClick={onLoadOlder}>Load older requests</button>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
