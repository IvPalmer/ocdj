import { useStats, useHealth, useSlskdHealth, useAutomationStatus } from '../../api/hooks'
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

const PIPELINE_STAGES = [
  { key: 'wanted_pending', label: 'Wanted', color: '#fbbf24' },
  { key: 'searching', label: 'Searching', color: '#a78bfa' },
  { key: 'found', label: 'Found', color: '#60a5fa' },
  { key: 'downloading', label: 'Downloading', color: '#2dd4bf' },
  { key: 'downloaded', label: 'Downloaded', color: '#34d399' },
  { key: 'organizing', label: 'Organizing', color: '#818cf8' },
  { key: 'ready', label: 'Ready', color: '#10b981' },
]

function PipelineFlow({ pipeline }) {
  if (!pipeline) return null

  return (
    <div className="pipeline-flow">
      {PIPELINE_STAGES.map((stage, i) => {
        const count = pipeline[stage.key] || 0
        return (
          <div key={stage.key} className="pipeline-flow-stage">
            <div className="pipeline-flow-count" style={{ color: count > 0 ? stage.color : 'var(--text-muted)' }}>
              {count}
            </div>
            <div className="pipeline-flow-label">{stage.label}</div>
            {i < PIPELINE_STAGES.length - 1 && (
              <div className="pipeline-flow-arrow" />
            )}
          </div>
        )
      })}
      {(pipeline.failed || 0) > 0 && (
        <div className="pipeline-flow-stage">
          <div className="pipeline-flow-count" style={{ color: '#f87171' }}>
            {pipeline.failed}
          </div>
          <div className="pipeline-flow-label">Failed</div>
        </div>
      )}
    </div>
  )
}

function Dashboard() {
  const { data: stats, isLoading: statsLoading } = useStats()
  const { data: health } = useHealth()
  const { data: slskdHealth } = useSlskdHealth()
  const { data: automationStatus } = useAutomationStatus()

  const wanted = stats?.wanted || {}
  const pipeline = automationStatus?.pipeline

  return (
    <div className="dashboard">
      <h2 className="page-title">Dashboard</h2>

      {/* Service Status */}
      <div className="status-row">
        <StatusBadge connected={!!health} label="Backend" />
        <StatusBadge connected={slskdHealth?.status === 'connected'} label="slskd" />
      </div>

      {/* Pipeline Flow */}
      <h3 className="section-title">Pipeline</h3>
      <PipelineFlow pipeline={pipeline} />

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
