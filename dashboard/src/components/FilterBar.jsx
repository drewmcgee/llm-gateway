export default function FilterBar({ filters, onChange }) {
  function set(field, value) {
    onChange({ ...filters, [field]: value })
  }

  return (
    <div className="filter-bar">
      <select value={filters.method} onChange={(e) => set("method", e.target.value)}>
        <option value="">All methods</option>
        <option value="GET">GET</option>
        <option value="POST">POST</option>
      </select>

      <input
        type="text"
        placeholder="Status code, e.g. 200"
        value={filters.status}
        onChange={(e) => set("status", e.target.value)}
      />

      <input
        type="text"
        placeholder="Filter by URL..."
        value={filters.urlContains}
        onChange={(e) => set("urlContains", e.target.value)}
      />
    </div>
  )
}
