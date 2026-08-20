import { useEffect, useState } from "react"
import FilterBar from "./components/FilterBar"
import LogTable from "./components/LogTable"
import LogDetail from "./components/LogDetail"
import Analytics from "./components/Analytics"
import { fetchLogs, streamUrl } from "./api"

function matchesFilters(log, filters) {
  if (filters.method && log.method !== filters.method) return false
  if (filters.status && String(log.status_code) !== filters.status) return false
  if (filters.urlContains && !log.url.includes(filters.urlContains)) return false
  return true
}

export default function App() {
  const [logs, setLogs] = useState([])
  const [filters, setFilters] = useState({ method: "", status: "", urlContains: "" })
  const [selectedId, setSelectedId] = useState(null)
  const [connected, setConnected] = useState(false)
  const [view, setView] = useState("logs")

  // Initial page load: backfill whatever already happened before we connected.
  useEffect(() => {
    fetchLogs().then(setLogs).catch(console.error)
  }, [])

  // Live updates: the backend pushes one event per completed request.
  useEffect(() => {
    const source = new EventSource(streamUrl())
    source.onopen = () => setConnected(true)
    source.onerror = () => setConnected(false)
    source.onmessage = (event) => {
      const log = JSON.parse(event.data)
      setLogs((prev) => [log, ...prev])
    }
    return () => source.close()
  }, [])

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
          <div className="main-panes">
            <LogTable logs={filteredLogs} selectedId={selectedId} onSelect={setSelectedId} />
            <LogDetail logId={selectedId} />
          </div>
        </>
      ) : (
        <Analytics logs={logs} />
      )}
    </div>
  )
}
