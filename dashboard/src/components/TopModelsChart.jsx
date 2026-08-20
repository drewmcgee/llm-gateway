import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Cell, ResponsiveContainer,
} from "recharts"
import { topModels, assignModelColors, OTHER_COLOR } from "../analytics"

export default function TopModelsChart({ logs }) {
  const data = topModels(logs)
  const colorByModel = assignModelColors(logs)

  if (data.length === 0) {
    return <div className="chart-empty">No requests yet.</div>
  }

  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} layout="vertical" margin={{ left: 24 }}>
        <CartesianGrid stroke="var(--border)" horizontal={false} />
        <XAxis type="number" stroke="var(--text-muted)" fontSize={12} allowDecimals={false} />
        <YAxis type="category" dataKey="model" stroke="var(--text-muted)" fontSize={12} width={140} />
        <Tooltip contentStyle={{ background: "var(--surface-raised)", border: "1px solid var(--border)", borderRadius: 6 }} />
        <Bar dataKey="count" radius={[0, 4, 4, 0]} maxBarSize={24}>
          {data.map((entry) => (
            <Cell key={entry.model} fill={entry.model === "Other" ? OTHER_COLOR : colorByModel.get(entry.model)} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
