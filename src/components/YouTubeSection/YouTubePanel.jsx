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

function failureSummary(message) {
  const text = message || ''
  if (text.toLowerCase().includes('blocked the production server')) {
    return {
      title: 'YouTube blocked the production server',
      detail: 'This video works from the local Mac but YouTube is refusing the VPS request. Retrying unchanged will likely fail.',
    }
  }
  if (text.toLowerCase().includes('bot-check')) {
    return {
      title: 'YouTube sign-in required',
      detail: 'The server needs a fresh YouTube session cookie. Retry after the server authentication is refreshed.',
    }
  }
  return {
    title: 'Download failed',
    detail: text || 'yt-dlp could not download this video.',
  }
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
  const [submitError, setSubmitError] = useState('')
  const jobs = jobsData?.results || []

  const handleFetch = async (e) => {
    e.preventDefault()
    const trimmed = url.trim()
    if (!trimmed) return
    setSubmitError('')
    try {
      await fetchMutation.mutateAsync(trimmed)
      setUrl('')
    } catch (err) {
      const detail = err?.data?.url?.[0] || err?.data?.error || err.message
      setSubmitError(detail)
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
      {submitError && (
        <div className="yt-submit-error" role="alert">{submitError}</div>
      )}

      {jobs.length > 0 ? (
        <div className="yt-table-wrap">
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
                  {job.status === 'failed' && (() => {
                    const summary = failureSummary(job.error_message)
                    return (
                      <span className="yt-error" role="alert">
                        <strong>{summary.title}</strong>
                        <span>{summary.detail}</span>
                      </span>
                    )
                  })()}
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
        </div>
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
