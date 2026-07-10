import { useState } from 'react'
import {
  useYtJobs, useYtFetch, useYtRetry, useYtDeleteJob,
} from '../../api/hooks'
import './YouTubePanel.css'

const STATUS_LABELS = {
  queued: 'Queued',
  fetching: 'Fetching',
  downloaded: 'Downloaded',
  failed: 'Failed',
}

function StatusPill({ status }) {
  if (status === 'fetching') {
    return (
      <span className="yt-pill yt-pill--fetching">
        <span className="yt-spinner" /> Fetching
      </span>
    )
  }
  return (
    <span className={`yt-pill yt-pill--${status}`}>
      {STATUS_LABELS[status] || status}
    </span>
  )
}

function YouTubePanel() {
  const { data: jobsData } = useYtJobs()
  const fetchMutation = useYtFetch()
  const retryMutation = useYtRetry()
  const deleteMutation = useYtDeleteJob()

  const [url, setUrl] = useState('')
  const jobs = jobsData?.results || []

  const handleFetch = async (e) => {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed) return
    try {
      await fetchMutation.mutateAsync(trimmed)
      setUrl('')
    } catch (err) {
      const detail = err?.data?.url?.[0] || err?.data?.error || err.message
      alert('Fetch failed: ' + detail)
    }
  }

  return (
    <div className="yt-panel">
      <div className="yt-header">
        <h2 className="page-title">YouTube</h2>
      </div>

      <p className="yt-intro">
        Paste a YouTube link — the server downloads the best-quality audio with
        yt-dlp and feeds it straight into the organize pipeline
        (tag → rename → convert → publish).
      </p>

      <form onSubmit={handleFetch} className="yt-fetch-form">
        <input
          type="text"
          className="yt-input"
          placeholder="https://www.youtube.com/watch?v=…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <button
          type="submit"
          className="btn btn-primary"
          disabled={fetchMutation.isPending || !url.trim()}
        >
          {fetchMutation.isPending ? 'Fetching…' : 'Fetch'}
        </button>
      </form>

      {jobs.length > 0 ? (
        <table className="yt-table">
          <thead>
            <tr>
              <th>Title</th>
              <th>Uploader</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {jobs.map(job => (
              <tr key={job.id} className={job.status === 'failed' ? 'yt-row--failed' : ''}>
                <td className="yt-td-title">
                  <span className="yt-title" title={job.url}>
                    {job.title || job.url}
                  </span>
                  {job.pipeline_item && (
                    <span className="yt-pipeline-link" title="Ingested into the organize pipeline">
                      in pipeline #{job.pipeline_item}
                    </span>
                  )}
                  {job.status === 'failed' && job.error_message && (
                    <span className="yt-error" title={job.error_message}>
                      {job.error_message}
                    </span>
                  )}
                </td>
                <td className="yt-td-uploader">{job.uploader || '—'}</td>
                <td>
                  <StatusPill status={job.status} />
                </td>
                <td className="yt-td-actions">
                  {job.status === 'failed' && (
                    <button
                      className="btn btn-xs btn-primary"
                      onClick={() => retryMutation.mutate(job.id)}
                      disabled={retryMutation.isPending}
                    >
                      Retry
                    </button>
                  )}
                  {(job.status === 'downloaded' || job.status === 'failed') && (
                    <button
                      className="btn btn-xs btn-ghost"
                      onClick={() => deleteMutation.mutate(job.id)}
                      disabled={deleteMutation.isPending}
                      title="Remove from list (files unaffected)"
                    >
                      ✕
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="yt-empty">
          No fetches yet. Paste a YouTube URL above to download its audio into
          the pipeline.
        </div>
      )}
    </div>
  )
}

export default YouTubePanel
