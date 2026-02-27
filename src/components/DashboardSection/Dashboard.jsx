import { useStats, useHealth, useSlskdHealth } from '../../api/hooks'
import './Dashboard.css'

function StatCard({ label, value, color }) {
  return (
    <div className="stat-card">
      <div className="stat-value" style={{ color }}>{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

function StatusBadge({ connected, label }) {
  return (
    <div className={`status-badge ${connected ? 'status-badge--ok' : 'status-badge--error'}`}>
      <span className="status-dot" />
      {label}: {connected ? 'Connected' : 'Disconnected'}
    </div>
  )
}

function Dashboard() {
  const { data: stats, isLoading: statsLoading } = useStats()
  const { data: health } = useHealth()
  const { data: slskdHealth } = useSlskdHealth()

  const wanted = stats?.wanted || {}

  return (
    <div className="dashboard">
      <h2 className="page-title">Dashboard</h2>

      {/* Service Status */}
      <div className="status-row">
        <StatusBadge connected={!!health} label="Backend" />
        <StatusBadge connected={slskdHealth?.status === 'connected'} label="slskd" />
      </div>

      {/* Wanted Stats */}
      <h3 className="section-title">Wanted List</h3>
      {statsLoading ? (
        <div className="loading">Loading stats...</div>
      ) : (
        <div className="stats-grid">
          <StatCard label="Total" value={wanted.total || 0} color="#60a5fa" />
          <StatCard label="Pending" value={wanted.pending || 0} color="#fbbf24" />
          <StatCard label="Searching" value={wanted.searching || 0} color="#a78bfa" />
          <StatCard label="Downloading" value={wanted.downloading || 0} color="#34d399" />
          <StatCard label="Downloaded" value={wanted.downloaded || 0} color="#10b981" />
          <StatCard label="Failed" value={wanted.failed || 0} color="#f87171" />
        </div>
      )}
    </div>
  )
}

export default Dashboard
