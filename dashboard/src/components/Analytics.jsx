import RequestsChart from "./RequestsChart"
import TopModelsChart from "./TopModelsChart"
import TokenStat from "./TokenStat"

export default function Analytics({ logs }) {
  return (
    <div className="analytics-grid">
      <div className="chart-card chart-card-wide">
        <h3>Requests over time</h3>
        <RequestsChart logs={logs} />
      </div>
      <div className="chart-card">
        <TokenStat logs={logs} />
      </div>
      <div className="chart-card chart-card-wide">
        <h3>Top models</h3>
        <TopModelsChart logs={logs} />
      </div>
    </div>
  )
}
