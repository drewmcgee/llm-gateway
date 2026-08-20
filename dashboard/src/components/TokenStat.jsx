import { tokenSummary } from "../analytics"

export default function TokenStat({ logs }) {
  const { totalTokens, requestsWithUsage, requestsWithoutUsage } = tokenSummary(logs)

  return (
    <div className="stat-tile">
      <div className="stat-label">Total tokens</div>
      <div className="stat-value">{totalTokens.toLocaleString()}</div>
      <div className="stat-sub">
        {requestsWithUsage} request{requestsWithUsage === 1 ? "" : "s"} with usage data
        {requestsWithoutUsage > 0 && ` · ${requestsWithoutUsage} without (streamed or errored)`}
      </div>
    </div>
  )
}
