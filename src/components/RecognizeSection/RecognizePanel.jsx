import { useState } from 'react'
import {
  useRecognizeJobs,
  useRecognizeJob,
  useCreateRecognizeJob,
  useAddRecognizeToWanted,
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

function StatusBadge({ status }) {
  const isActive = status === 'downloading' || status === 'recognizing'
  return (
    <span className={`recognize-status recognize-status--${status}`}>
      {isActive && <span className="recognize-spinner" />}
      {status}
    </span>
  )
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

  if (!job) return null

  const isActive = job.status === 'downloading' || job.status === 'recognizing'

  return (
    <>
      <div className="recognize-active">
        <div className="recognize-active-header">
          <span className="recognize-active-title">
            {job.title || job.url}
          </span>
          <StatusBadge status={job.status} />
        </div>

        {job.status === 'failed' && (
          <div className="recognize-error">{job.error_message || 'Recognition failed'}</div>
        )}

        {isActive && job.segments_total > 0 && <ProgressBar job={job} />}

        <div className="recognize-active-stats">
          {job.duration_seconds > 0 && <span>Duration: {formatDuration(job.duration_seconds)}</span>}
          {job.tracks_found > 0 && <span>Tracks: {job.tracks_found}</span>}
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

function JobHistory({ jobs, activeJobId, onSelect }) {
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
            <div className="recognize-job-title">{job.title || job.url}</div>
            <div className="recognize-job-meta">
              <StatusBadge status={job.status} />
              {job.tracks_found > 0 && <span className="mono">{job.tracks_found} tracks</span>}
              {job.duration_seconds > 0 && <span>{formatDuration(job.duration_seconds)}</span>}
              <span>{timeAgo(job.created)}</span>
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function RecognizePanel() {
  const [url, setUrl] = useState('')
  const [activeJobId, setActiveJobId] = useState(null)

  const { data: jobsData } = useRecognizeJobs()
  const createJob = useCreateRecognizeJob()

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

  return (
    <div className="recognize-panel">
      <div className="recognize-header">
        <h1 className="page-title">Recognize</h1>
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

      {currentJobId && <ActiveJob jobId={currentJobId} />}

      <JobHistory
        jobs={jobs}
        activeJobId={currentJobId}
        onSelect={setActiveJobId}
      />
    </div>
  )
}

export default RecognizePanel
