/* Shared so the Wanted list and the Shazam feed can't drift into showing the
   same status in two different colours. */

export const STATUS_LABELS = {
  pending: 'Pending',
  identified: 'Identified',
  searching: 'Searching',
  found: 'Found',
  downloading: 'Downloading',
  downloaded: 'Downloaded',
  tagged: 'Tagged',
  organized: 'Organized',
  not_found: 'Not Found',
  failed: 'Failed',
}

export const STATUS_COLORS = {
  pending: 'var(--accent-amber)',
  identified: '#a78bfa',
  searching: '#60a5fa',
  found: 'var(--accent-green)',
  downloading: '#34d399',
  downloaded: '#10b981',
  tagged: '#8b5cf6',
  organized: '#6366f1',
  not_found: 'var(--accent-red)',
  failed: '#f87171',
}

export default function StatusBadge({ status }) {
  const color = STATUS_COLORS[status] || 'var(--text-muted)'
  return (
    <span
      className="status-badge-sm"
      style={{
        background: `color-mix(in srgb, ${color} 12%, transparent)`,
        color: color,
      }}
    >
      {STATUS_LABELS[status] || status}
    </span>
  )
}
