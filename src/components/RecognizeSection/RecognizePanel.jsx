import { useState } from 'react'
import {
  useRecognizeJobs,
  useRecognizeJob,
  useCreateRecognizeJob,
  useAddRecognizeToWanted,
  useResumeRecognizeJob,
  useRerunRecognizeJob,
  useReclusterRecognizeJob,
  useDeleteRecognizeJob,
  useACRCloudUsage,
} from '../../api/hooks'
import './RecognizePanel.css'

function formatTimestamp(seconds) {
  if (seconds == null) return '--:--'
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

function formatDuration(seconds) {
  if (!seconds) return ''
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

function timeAgo(isoStr) {
  if (!isoStr) return ''
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function getPlatform(url) {
  if (!url) return null
  if (url.includes('soundcloud.com') || url.includes('on.soundcloud.com')) return { name: 'SoundCloud', icon: '\u2601', cls: 'platform--soundcloud' }
  if (url.includes('youtube.com') || url.includes('youtu.be')) return { name: 'YouTube', icon: '\u25B6', cls: 'platform--youtube' }
  if (url.includes('mixcloud.com')) return { name: 'Mixcloud', icon: '\u266B', cls: 'platform--mixcloud' }
  if (url.includes('bandcamp.com')) return { name: 'Bandcamp', icon: '\u266A', cls: 'platform--bandcamp' }
  return null
}

function shortenUrl(url) {
  if (!url) return ''
  try {
    const u = new URL(url)
    // e.g. "soundcloud.com/dsrptvrec/dsrptv-sou..."
    const path = u.pathname.replace(/^\//, '')
    const short = path.length > 40 ? path.slice(0, 40) + '\u2026' : path
    return `${u.hostname}/${short}`
  } catch {
    return url.length > 60 ? url.slice(0, 60) + '\u2026' : url
  }
}

function JobTitle({ title, url, size = 'normal' }) {
  const platform = getPlatform(url)
  const displayTitle = title || shortenUrl(url)
  const showUrl = title && url

  if (size === 'small') {
    return (
      <div className="recognize-job-title-wrap">
        <div className="recognize-job-title">
          {platform && <span className={`platform-icon ${platform.cls}`} title={platform.name}>{platform.icon}</span>}
          {displayTitle}
        </div>
      </div>
    )
  }

  return (
    <div className="recognize-active-title-wrap">
      <span className="recognize-active-title">
        {platform && <span className={`platform-icon ${platform.cls}`} title={platform.name}>{platform.icon}</span>}
        {displayTitle}
      </span>
      {showUrl && (
        <a href={url} target="_blank" rel="noopener noreferrer" className="recognize-active-url">
          {shortenUrl(url)}
        </a>
      )}
    </div>
  )
}

function StatusBadge({ status }) {
  const isActive = status === 'downloading' || status === 'recognizing'
  return (
    <span className={`recognize-status recognize-status--${status}`}>
      {isActive && <span className="recognize-spinner" />}
      {status}
    </span>
  )
}

function EngineBadge({ engine }) {
  if (!engine || engine === 'shazam') return null
  const labels = { trackid: 'TrackID.net', hybrid: 'Hybrid', acrcloud: 'ACRCloud', dual: 'Dual Engine' }
  const classes = { trackid: 'engine-badge--trackid', hybrid: 'engine-badge--hybrid', acrcloud: 'engine-badge--acrcloud', dual: 'engine-badge--hybrid' }
  return <span className={`engine-badge ${classes[engine] || 'engine-badge--hybrid'}`}>{labels[engine] || engine}</span>
}

function ProgressBar({ job }) {
  const { segments_done, segments_total } = job
  const pct = segments_total > 0 ? Math.round((segments_done / segments_total) * 100) : 0

  return (
    <div className="recognize-progress">
      <div className="recognize-progress-bar">
        <div className="recognize-progress-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="recognize-progress-meta">
        <span>Segments: {segments_done} / {segments_total}</span>
        <span>{pct}%</span>
      </div>
    </div>
  )
}

function DescriptionTracks({ tracks }) {
  const [expanded, setExpanded] = useState(false)

  if (!tracks || tracks.length === 0) return null

  return (
    <div className="recognize-description">
      <div className="recognize-description-header" onClick={() => setExpanded(e => !e)}>
        <h3>From description ({tracks.length} tracks)</h3>
        <span className={`recognize-description-toggle ${expanded ? 'recognize-description-toggle--open' : ''}`}>
          &#9654;
        </span>
      </div>
      {expanded && (
        <div className="recognize-description-body">
          <table className="recognize-description-table">
            <tbody>
              {tracks.map((t, i) => (
                <tr key={i}>
                  <td className="track-timestamp">{formatTimestamp(t.timestamp)}</td>
                  <td>
                    {t.artist && <span className="track-artist">{t.artist}</span>}
                    {t.artist && t.title && ' \u2014 '}
                    <span className="track-title">{t.title}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function Tracklist({ job }) {
  const [selected, setSelected] = useState(() =>
    new Set(job.tracklist.map((_, i) => i))
  )
  const [successMsg, setSuccessMsg] = useState('')
  const addToWanted = useAddRecognizeToWanted()

  const tracklist = job.tracklist || []

  function toggleTrack(idx) {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  function toggleAll() {
    if (selected.size === tracklist.length) {
      setSelected(new Set())
    } else {
      setSelected(new Set(tracklist.map((_, i) => i)))
    }
  }

  function handleAddToWanted() {
    const indices = Array.from(selected).sort((a, b) => a - b)
    addToWanted.mutate(
      { id: job.id, track_indices: indices },
      {
        onSuccess: (data) => {
          setSuccessMsg(`Added ${data.created} track${data.created !== 1 ? 's' : ''} to Wanted List`)
          setTimeout(() => setSuccessMsg(''), 5000)
        },
      }
    )
  }

  if (tracklist.length === 0) return null

  return (
    <div className="recognize-tracklist">
      <div className="recognize-tracklist-header">
        <h3>Tracklist ({tracklist.length} tracks)</h3>
        <EngineBadge engine={job.engine} />
      </div>
      {successMsg && <div className="recognize-success">{successMsg}</div>}
      <table className="recognize-table">
        <thead>
          <tr>
            <th>
              <input
                type="checkbox"
                checked={selected.size === tracklist.length}
                onChange={toggleAll}
              />
            </th>
            <th>Time</th>
            <th>Track</th>
            <th>Album / Label</th>
            <th>Confidence</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {tracklist.map((track, i) => (
            <tr key={i}>
              <td>
                <input
                  type="checkbox"
                  checked={selected.has(i)}
                  onChange={() => toggleTrack(i)}
                />
              </td>
              <td className="track-timestamp">
                {formatTimestamp(track.timestamp_start)}
                {track.timestamp_end > track.timestamp_start && (
                  <>{'\u2013'}{formatTimestamp(track.timestamp_end)}</>
                )}
              </td>
              <td>
                <span className="track-artist">{track.artist}</span>
                {track.artist && track.title && ' \u2014 '}
                <span className="track-title">{track.title}</span>
              </td>
              <td className="track-meta">
                {[track.album, track.label].filter(Boolean).join(' / ') || '\u2014'}
              </td>
              <td>
                <span className={`confidence-badge confidence-badge--${track.confidence}`}>
                  {track.confidence}
                </span>
              </td>
              <td className="track-meta">
                {(track.engines || []).join(', ') || 'shazam'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="recognize-actions">
        <button
          className="btn btn-accent btn-sm"
          onClick={handleAddToWanted}
          disabled={selected.size === 0 || addToWanted.isPending}
        >
          {addToWanted.isPending ? 'Adding...' : `Add ${selected.size} to Wanted`}
        </button>
        <span className="select-info">
          {selected.size} of {tracklist.length} selected
        </span>
      </div>
    </div>
  )
}

function ActiveJob({ jobId }) {
  const { data: job } = useRecognizeJob(jobId)
  const resumeJob = useResumeRecognizeJob()
  const rerunJob = useRerunRecognizeJob()
  const reclusterJob = useReclusterRecognizeJob()

  if (!job) return null

  const isActive = job.status === 'downloading' || job.status === 'recognizing'
  const canRerun = job.status === 'completed' || job.status === 'failed'

  // Detect stuck: active but hasn't updated in > 60s
  const updatedAgo = job.updated ? (Date.now() - new Date(job.updated).getTime()) / 1000 : 0
  const isStuck = isActive && updatedAgo > 60

  return (
    <>
      <div className="recognize-active">
        <div className="recognize-active-header">
          <JobTitle title={job.title} url={job.url} />
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            {isStuck && (
              <button
                className="btn btn-sm"
                onClick={() => resumeJob.mutate(job.id)}
                disabled={resumeJob.isPending}
              >
                {resumeJob.isPending ? 'Resuming...' : 'Resume'}
              </button>
            )}
            {canRerun && (
              <>
                <button
                  className="btn btn-sm"
                  onClick={() => reclusterJob.mutate(job.id)}
                  disabled={reclusterJob.isPending}
                  title="Re-cluster existing results + merge TrackID.net"
                >
                  {reclusterJob.isPending ? 'Merging...' : 'Recluster'}
                </button>
                <button
                  className="btn btn-sm"
                  onClick={() => rerunJob.mutate(job.id)}
                  disabled={rerunJob.isPending}
                  title="Re-download and re-recognize from scratch"
                >
                  {rerunJob.isPending ? 'Restarting...' : 'Rerun'}
                </button>
              </>
            )}
            <StatusBadge status={job.status} />
          </div>
        </div>

        {isStuck && (
          <div className="recognize-warning">
            Job appears stuck — last updated {Math.floor(updatedAgo / 60)}m ago.
            Click Resume to restart from where it left off.
          </div>
        )}

        {job.status === 'failed' && (
          <div className="recognize-error">{job.error_message || 'Recognition failed'}</div>
        )}

        {isActive && job.segments_total > 0 && <ProgressBar job={job} />}

        <div className="recognize-active-stats">
          {job.duration_seconds > 0 && <span>Duration: {formatDuration(job.duration_seconds)}</span>}
          {job.tracks_found > 0 && <span>Tracks: {job.tracks_found}</span>}
          {job.acrcloud_calls > 0 && <span>ACRCloud: {job.acrcloud_calls} calls</span>}
          {isActive && job.status === 'downloading' && <span>Downloading audio...</span>}
        </div>
      </div>

      {job.status === 'completed' && (
        <>
          <Tracklist job={job} />
          <DescriptionTracks tracks={job.description_tracks} />
        </>
      )}
    </>
  )
}

function JobHistory({ jobs, activeJobId, onSelect, onDelete }) {
  if (!jobs || jobs.length === 0) return null

  return (
    <div className="recognize-history">
      <div className="recognize-history-header">
        <h3>Past Jobs ({jobs.length})</h3>
      </div>
      {jobs.map(job => (
        <div
          key={job.id}
          className={`recognize-job-card ${job.id === activeJobId ? 'recognize-job-card--active' : ''}`}
          onClick={() => onSelect(job.id)}
        >
          <div className="recognize-job-info">
            <JobTitle title={job.title} url={job.url} size="small" />
            <div className="recognize-job-meta">
              <StatusBadge status={job.status} />
              <EngineBadge engine={job.engine} />
              {job.tracks_found > 0 && <span className="mono">{job.tracks_found} tracks</span>}
              {job.duration_seconds > 0 && <span>{formatDuration(job.duration_seconds)}</span>}
              <span>{timeAgo(job.created)}</span>
            </div>
          </div>
          <button
            className="recognize-job-delete"
            title="Delete job"
            onClick={(e) => { e.stopPropagation(); onDelete(job.id) }}
          >
            &times;
          </button>
        </div>
      ))}
    </div>
  )
}

function ACRCloudUsage() {
  const { data: usage } = useACRCloudUsage()

  if (!usage || !usage.configured) return null

  const plan = usage.plan
  // Prefer Console API data, but fall back to local if Console reports 0
  // and we have local tracking (Console API stats can lag behind)
  const consoleToday = plan?.calls_today
  const today = (consoleToday > 0 ? consoleToday : null) ?? usage.local_calls_today
  const dayLimit = plan?.day_limit
  const remaining = plan?.remaining_today
  const validMonth = (plan?.valid_month > 0 ? plan.valid_month : null) ?? usage.local_calls_month
  const estCost = plan?.est_cost_month
  const isTrial = plan?.is_trial

  // Usage percentage for the day (only when there's a limit)
  const dayPct = dayLimit > 0 ? Math.min(100, Math.round((today / dayLimit) * 100)) : null
  const isLow = dayPct != null && dayPct >= 80

  return (
    <div className="acrcloud-usage">
      <div className="acrcloud-usage__header">
        <span className="acrcloud-usage__title">Powered by ACRCloud</span>
        {isTrial && <span className="acrcloud-usage__trial">Trial</span>}
      </div>
      <div className="acrcloud-usage__stats">
        {dayLimit > 0 ? (
          <>
            <div className="acrcloud-usage__stat">
              <span className={`acrcloud-usage__value ${isLow ? 'acrcloud-usage__value--warn' : ''}`}>
                {remaining != null ? remaining.toLocaleString() : '?'}
              </span>
              <span className="acrcloud-usage__label">left today</span>
            </div>
            <div className="acrcloud-usage__bar-wrap">
              <div className="acrcloud-usage__bar">
                <div
                  className={`acrcloud-usage__bar-fill ${isLow ? 'acrcloud-usage__bar-fill--warn' : ''}`}
                  style={{ width: `${dayPct}%` }}
                />
              </div>
              <span className="acrcloud-usage__bar-label">
                {today.toLocaleString()} / {dayLimit.toLocaleString()}
              </span>
            </div>
          </>
        ) : (
          <div className="acrcloud-usage__stat">
            <span className="acrcloud-usage__value">{today.toLocaleString()}</span>
            <span className="acrcloud-usage__label">today</span>
          </div>
        )}
        {validMonth != null && (
          <div className="acrcloud-usage__stat">
            <span className="acrcloud-usage__value">{Math.round(validMonth).toLocaleString()}</span>
            <span className="acrcloud-usage__label">billable</span>
          </div>
        )}
        {estCost != null && estCost > 0 && (
          <div className="acrcloud-usage__stat">
            <span className="acrcloud-usage__value">${estCost.toFixed(2)}</span>
            <span className="acrcloud-usage__label">est. cost</span>
          </div>
        )}
      </div>
      {!usage.has_console_api && (
        <span className="acrcloud-usage__hint" title="Add ACRCLOUD_BEARER_TOKEN in Settings to see plan limits and real usage">
          ?
        </span>
      )}
    </div>
  )
}

function ActiveJobs({ jobs, activeJobId, onSelect }) {
  const activeJobs = jobs.filter(j => j.status === 'downloading' || j.status === 'recognizing')

  if (activeJobs.length <= 1) return null

  return (
    <div className="recognize-active-jobs">
      <div className="recognize-active-jobs__header">
        <span className="recognize-spinner" />
        {activeJobs.length} jobs running
      </div>
      <div className="recognize-active-jobs__list">
        {activeJobs.map(job => {
          const pct = job.segments_total > 0
            ? Math.round((job.segments_done / job.segments_total) * 100)
            : 0
          return (
            <button
              key={job.id}
              className={`recognize-active-jobs__item ${job.id === activeJobId ? 'recognize-active-jobs__item--selected' : ''}`}
              onClick={() => onSelect(job.id)}
            >
              <span className="recognize-active-jobs__title">
                {(() => { const p = getPlatform(job.url); return p ? <span className={`platform-icon ${p.cls}`}>{p.icon}</span> : null })()}
                {job.title || shortenUrl(job.url)}
              </span>
              <span className="recognize-active-jobs__pct">
                {job.status === 'downloading' ? 'DL' : `${pct}%`}
              </span>
            </button>
          )
        })}
      </div>
    </div>
  )
}

function RecognizePanel() {
  const [url, setUrl] = useState('')
  const [activeJobId, setActiveJobId] = useState(null)

  const { data: jobsData } = useRecognizeJobs()
  const createJob = useCreateRecognizeJob()
  const deleteJob = useDeleteRecognizeJob()

  const jobs = jobsData?.results || []

  // Auto-select latest active job
  const latestActive = jobs.find(j => j.status === 'downloading' || j.status === 'recognizing')
  const currentJobId = activeJobId || latestActive?.id || (jobs.length > 0 ? jobs[0].id : null)

  function handleSubmit(e) {
    e.preventDefault()
    if (!url.trim()) return

    createJob.mutate(
      { url: url.trim() },
      {
        onSuccess: (data) => {
          setActiveJobId(data.id)
          setUrl('')
        },
      }
    )
  }

  function handleDelete(id) {
    if (id === currentJobId) setActiveJobId(null)
    deleteJob.mutate(id)
  }

  return (
    <div className="recognize-panel">
      <div className="recognize-header">
        <h1 className="page-title">Recognize</h1>
        <ACRCloudUsage />
      </div>

      <form className="recognize-input-row" onSubmit={handleSubmit}>
        <input
          type="text"
          className="input"
          placeholder="Paste a mix URL (YouTube, SoundCloud, Mixcloud...)"
          value={url}
          onChange={e => setUrl(e.target.value)}
        />
        <button
          className="btn btn-accent"
          type="submit"
          disabled={!url.trim() || createJob.isPending}
        >
          {createJob.isPending ? 'Starting...' : 'Recognize'}
        </button>
      </form>

      {createJob.isError && (
        <div className="recognize-error">
          {createJob.error?.data?.url?.[0] || createJob.error?.message || 'Failed to start recognition'}
        </div>
      )}

      <ActiveJobs jobs={jobs} activeJobId={currentJobId} onSelect={setActiveJobId} />

      {currentJobId && <ActiveJob jobId={currentJobId} />}

      <JobHistory
        jobs={jobs}
        activeJobId={currentJobId}
        onSelect={setActiveJobId}
        onDelete={handleDelete}
      />
    </div>
  )
}

export default RecognizePanel
