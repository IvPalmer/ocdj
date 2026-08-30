import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { useAddToQueue } from '../../api/hooks'
import usePreviewPlayer from '../shared/usePreviewPlayer'
import StatusBadge from '../shared/StatusBadge'
import { useState } from 'react'
import './ShazamPanel.css'

function timeAgo(iso) {
  if (!iso) return 'never'
  const diff = (Date.now() - new Date(iso).getTime()) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function whenShazamed(notes) {
  // The reader writes "shazamed <date> · on <device>"; show the date it caught
  // rather than when the row happened to be created.
  const m = /shazamed ([^·]+)/.exec(notes || '')
  return m ? m[1].trim() : ''
}

const DONE = new Set(['downloaded', 'tagged', 'organized', 'found'])

export default function ShazamPanel() {
  const qc = useQueryClient()
  const [queuingId, setQueuingId] = useState(null)
  const addToQueue = useAddToQueue()
  const { toggle: togglePreview, playingId, loadingId } = usePreviewPlayer()

  const { data: status } = useQuery({
    queryKey: ['shazam-status'],
    queryFn: () => api.get('/wanted/shazam/status/'),
    refetchInterval: 60000,
  })

  const { data, isLoading } = useQuery({
    queryKey: ['shazam-items'],
    queryFn: () => api.get('/wanted/items/?identified_via=shazam&ordering=-added&limit=100'),
    refetchInterval: 60000,
  })

  const items = data?.results || []

  const [busy, setBusy] = useState(false)

  const syncNow = async (seed) => {
    setBusy(true)
    try {
      await api.post('/wanted/shazam/sync/', seed ? { seed: true } : {})
      qc.invalidateQueries({ queryKey: ['shazam-items'] })
      qc.invalidateQueries({ queryKey: ['shazam-status'] })
    } finally {
      setBusy(false)
    }
  }
  const importBacklog = () => syncNow(false)

  const handleFind = async (id) => {
    setQueuingId(id)
    try {
      await addToQueue.mutateAsync({ wanted_item_ids: [id] })
    } finally {
      setQueuingId(null)
    }
  }

  return (
    <div className="shazam-panel">
      <div className="shazam-header">
        <h1 className="page-title">Shazam</h1>
        <span className="shazam-count">
          {status?.total ?? 0} caught
        </span>
        <button className="btn btn-xs shazam-sync" onClick={() => syncNow(false)} disabled={busy}>
          {busy ? 'Checking…' : 'Check now'}
        </button>
      </div>

      {/* Two failure modes worth separating: Spotify not linked at all, and
          linked but the poll has stopped. The second is silent by nature —
          an expired refresh token means nothing arrives and nothing complains,
          which reads as "I haven't Shazamed anything lately". */}
      {!status ? null : !status.spotify_connected ? (
        <div className="shazam-feed shazam-feed--overdue">
          <span className="shazam-feed-dot" />
          <span>
            <strong>Spotify isn't linked.</strong> Shazam syncs what you identify into one
            streaming service, and this feed reads it from there. Connect Spotify inside the
            Shazam app on your phone, then authorise it here — nothing touches Apple Music
            or your Music.app library.
          </span>
        </div>
      ) : (
        <div className={`shazam-feed ${status.overdue ? 'shazam-feed--overdue' : ''}`}>
          <span className="shazam-feed-dot" />
          <span>
            {status.overdue
              ? <><strong>Feed is overdue</strong> — last poll {timeAgo(status.last_checked)}.
                  Most likely Spotify's authorisation expired; re-authorise under Wanted → Import.</>
              : <>Feed healthy — polled {timeAgo(status.last_checked)}. Anything you Shazam on your
                  phone, Watch, or in Control Center reaches here within 10 minutes.</>}
          </span>
        </div>
      )}

      {isLoading ? (
        <p className="muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="shazam-empty">
          <p><strong>Nothing yet.</strong></p>
          <p className="muted">
            Your existing Shazams were deliberately left out — this feed starts
            from when it was switched on. Shazam something and it shows up here.
          </p>
          <p className="muted small">
            Want the backlog after all? <button className="btn btn-xs" onClick={importBacklog}
            disabled={busy}>Import everything in the playlist</button>
          </p>
        </div>
      ) : (
        <table className="shazam-table">
          <thead>
            <tr>
              <th></th>
              <th>Artist</th>
              <th>Title</th>
              <th>Shazamed</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map(item => {
              const noPreview = item.preview_checked && !item.preview_url
              return (
                <tr key={item.id} className={DONE.has(item.status) ? 'shazam-row--done' : ''}>
                  <td className="shazam-td-play">
                    <button
                      className={`btn btn-xs${playingId === item.id ? ' btn-xs--active' : ''}`}
                      onClick={() => togglePreview(item)}
                      disabled={loadingId === item.id || noPreview}
                      title={noPreview ? 'No preview — not in iTunes or Deezer' : 'Play 30s preview'}
                    >
                      {loadingId === item.id ? '…' : playingId === item.id ? '■' : noPreview ? '—' : '▶'}
                    </button>
                  </td>
                  <td className="shazam-td-artist">{item.artist || '—'}</td>
                  <td className="shazam-td-title">{item.title || '—'}</td>
                  <td className="shazam-td-when mono">{whenShazamed(item.notes)}</td>
                  <td>
                    <StatusBadge status={item.status} />
                  </td>
                  <td className="shazam-td-actions">
                    <button
                      className="btn btn-xs btn-accent"
                      onClick={() => handleFind(item.id)}
                      disabled={queuingId !== null}
                      title="Search Soulseek for this track"
                    >
                      {queuingId === item.id ? 'Finding…' : 'Find'}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}

      {/* Stated plainly because it is a real hole, not a rough edge: Shazam
          only syncs what its streaming partner has, and the tracks most worth
          catching in a club are the ones no catalogue carries. */}
      <p className="shazam-caveat">
        Only tracks Spotify has in its catalogue arrive here. Promos and white labels get
        Shazamed on your phone and never reach this list — Apple exposes no way to read the
        Shazam library itself, so a streaming playlist is the only feed there is.
      </p>
    </div>
  )
}
