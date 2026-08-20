import { useEffect, useState } from "react"
import FilterBar from "./components/FilterBar"
import LogTable from "./components/LogTable"
import LogDetail from "./components/LogDetail"
import Analytics from "./components/Analytics"
import { fetchLogs, streamUrl, PAGE_SIZE } from "./api"

const MAX_LIVE_LOGS = 500

function matchesFilters(log, filters) {
  if (filters.method && log.method !== filters.method) return false
  if (filters.status && String(log.status_code) !== filters.status) return false
  if (filters.urlContains && !log.url.includes(filters.urlContains)) return false
  return true
}

export default function App() {
  const [logs, setLogs] = useState([])
  const [pending, setPending] = useState([])
  const [hasMore, setHasMore] = useState(false)
  const [filters, setFilters] = useState({ method: "", status: "", urlContains: "" })
  const [selectedId, setSelectedId] = useState(null)
  const [connected, setConnected] = useState(false)
  const [view, setView] = useState("logs")

  // Initial page load: backfill whatever already happened before we connected.
  useEffect(() => {
    fetchLogs().then((page) => {
      setLogs(page)
      setHasMore(page.length === PAGE_SIZE)
    }).catch(console.error)
  }, [])

  // Live updates arrive here but are buffered, not shown immediately -- a
  // table that reorders itself while you're reading a row is bad UX, and
  // buffering also caps how much unseen data can pile up in the background.
  useEffect(() => {
    const source = new EventSource(streamUrl())
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.onmessage = (event) => {
      const log = JSON.parse(event.data)
      setPending((prev) => [log, ...prev].slice(0, MAX_LIVE_LOGS))
    }
    return () => source.close()
  }, [])

  function showPending() {
    setLogs((prev) => [...pending, ...prev].slice(0, MAX_LIVE_LOGS))
    setPending([])
  }

  async function loadOlder() {
    const oldestId = logs.at(-1)?.id
    if (oldestId == null) return
    const older = await fetchLogs({ beforeId: oldestId })
    setLogs((prev) => [...prev, ...older])
    setHasMore(older.length === PAGE_SIZE)
  }

  // Filtering runs client-side over everything we already have, so it applies
  // equally to historical rows and rows that just arrived over the stream.
  const filteredLogs = logs.filter((log) => matchesFilters(log, filters))

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-left">
          <h1>LLM Gateway</h1>
          <nav className="view-tabs">
            <button className={view === "logs" ? "active" : ""} onClick={() => setView("logs")}>
              Logs
            </button>
            <button className={view === "analytics" ? "active" : ""} onClick={() => setView("analytics")}>
              Analytics
            </button>
          </nav>
        </div>
        <div className="connection-status">
          <span className={`connection-dot ${connected ? "connected" : ""}`} />
          {connected ? "Live" : "Disconnected"}
        </div>
      </header>

      {view === "logs" ? (
        <>
          <FilterBar filters={filters} onChange={setFilters} />
          {pending.length > 0 && (
            <button className="pending-banner" onClick={showPending}>
              {pending.length} new request{pending.length === 1 ? "" : "s"} — click to show
            </button>
          )}
          <div className="main-panes">
            <LogTable
              logs={filteredLogs}
              selectedId={selectedId}
              onSelect={setSelectedId}
              hasMore={hasMore}
              onLoadOlder={loadOlder}
            />
            <LogDetail logId={selectedId} />
          </div>
        </>
      ) : (
        <Analytics logs={logs} />
      )}
    </div>
  )
}
