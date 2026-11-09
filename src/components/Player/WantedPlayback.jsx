import { useState } from 'react'
import { api } from '../../api/client'
import { usePlayer } from './PlayerContext'
import { directSource } from './TrackPlayback'
import LibraryMatch, { useIndexedPlayback } from './LibraryMatch'

export default function WantedPlayback({ item, showLibraryMatch = true }) {
  const player = usePlayer()
  const track = useIndexedPlayback({ artist: item.artist, title: item.title, wantedId: item.id,
    source: directSource(item), previewChecked: item.preview_checked, description: 'Catalog preview' })
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const label = [item.artist, item.title].filter(Boolean).join(' — ')

  async function audition(play) {
    setPending(true)
    setMessage('')
    try {
      if (track.source?.kind === 'mac') {
        if (play) player.play(track)
        else player.enqueue(track)
        return
      }
      let source = directSource(item)
      if (!source && !item.preview_checked) {
        const preview = await api.post(`/wanted/items/${item.id}/preview/`, {})
        source = directSource({ preview_url: preview?.url })
      }
      if (!source) {
        setMessage('No catalog preview found.')
        return
      }
      const previewTrack = { artist: item.artist, title: item.title, wantedId: item.id,
        source, description: 'Catalog preview' }
      if (play) player.play(previewTrack)
      else player.enqueue(previewTrack)
    } catch {
      setMessage('Could not load preview. Try again.')
    } finally {
      setPending(false)
    }
  }

  return <span className="track-controls">
    {!track.source && item.preview_checked
      ? <small className="track-no-source">No catalog preview found.</small>
      : <span className="track-playback">
        <button type="button" className="btn btn-xs" disabled={pending}
          aria-label={`Play ${label}`} onClick={() => audition(true)}>Play</button>
        <button type="button" className="btn btn-xs" disabled={pending}
          aria-label={`Queue ${label}`} onClick={() => audition(false)}>Queue</button>
        {pending && <small role="status">Looking up preview…</small>}
        {message && <small role="status">{message}</small>}
      </span>}
    {showLibraryMatch && <LibraryMatch artist={item.artist} title={item.title} />}
  </span>
}
