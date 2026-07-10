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
  const lowered = text.toLowerCase()
  if (
    lowered.includes('blocked the production server')
    || lowered.includes('cookies are configured')
  ) {
    return {
      title: 'YouTube blocked this server',
      detail: 'The video is available from your local session, but YouTube is refusing the production server. Refreshing the same job will not change that network block.',
    }
  }
  if (lowered.includes('bot-check')) {
    return {
      title: 'YouTube authentication required',
      detail: 'The production worker needs a current YouTube session cookie before this job can be retried.',
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
          aria-label="YouTube URL"
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

      {jobs.some(job => job.status === 'failed' && (
        job.error_message || '').toLowerCase().includes('blocked the production server')) && (
        <div className="yt-auth-banner" role="status">
          <strong>Production downloads are currently blocked by YouTube.</strong>
          <span>Jobs that work in Chrome can still be completed from the local session. Retrying here will remain blocked until the server network is accepted.</span>
        </div>
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
                <td className="yt-td-uploader" data-label="Uploader">{job.uploader || '—'}</td>
                <td className="yt-td-status" data-label="Status">
                  <StatusPill status={job.status} />
                </td>
                <td className="yt-td-actions">
                  {job.status === 'failed' && (
                    <button
                      className="btn btn-xs btn-primary"
                      onClick={() => retryMutation.mutate(job.id)}
                      disabled={retryMutation.isPending}
                      aria-label={`Retry ${job.title || job.url}`}
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
                      aria-label={`Remove ${job.title || job.url} from the list`}
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
