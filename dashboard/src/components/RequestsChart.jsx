import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from "recharts"
import { bucketRequestsByTime, SUCCESS_COLOR, ERROR_COLOR } from "../analytics"

export default function RequestsChart({ logs }) {
  const data = bucketRequestsByTime(logs)

  if (data.length === 0) {
    return <div className="chart-empty">No requests yet.</div>
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <AreaChart data={data}>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="time"
          tickFormatter={(t) => new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
          stroke="var(--text-muted)"
          fontSize={12}
        />
        <YAxis stroke="var(--text-muted)" fontSize={12} allowDecimals={false} />
        <Tooltip
          contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border)", borderRadius: 6 }}
          labelFormatter={(t) => new Date(t).toLocaleTimeString()}
        />
        <Legend />
        <Area type="monotone" dataKey="success" name="Success" stroke={SUCCESS_COLOR} fill={SUCCESS_COLOR} fillOpacity={0.1} strokeWidth={2} />
        <Area type="monotone" dataKey="error" name="Error" stroke={ERROR_COLOR} fill={ERROR_COLOR} fillOpacity={0.1} strokeWidth={2} />
      </AreaChart>
    </ResponsiveContainer>
  )
}
