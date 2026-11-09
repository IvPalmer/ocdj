import { useState } from 'react'
import { api } from '../../api/client'
import { usePlayer } from './PlayerContext'
import { directSource } from './TrackPlayback'
import LibraryMatch from './LibraryMatch'

export default function WantedPlayback({ item, showLibraryMatch = true }) {
  const player = usePlayer()
  const [pending, setPending] = useState(false)
  const [message, setMessage] = useState('')
  const label = [item.artist, item.title].filter(Boolean).join(' — ')

  async function audition(play) {
    setPending(true)
    setMessage('')
    try {
      let source = directSource(item)
      if (!source && !item.preview_checked) {
        const preview = await api.post(`/wanted/items/${item.id}/preview/`, {})
        source = directSource({ preview_url: preview?.url })
      }
      if (!source) {
        setMessage('No catalog preview found.')
        return
      }
      const track = { artist: item.artist, title: item.title, wantedId: item.id,
        source, description: 'Catalog preview' }
      if (play) player.play(track)
      else player.enqueue(track)
    } catch {
      setMessage('Could not load preview. Try again.')
    } finally {
      setPending(false)
    }
  }

  return <span className="track-controls">
    {showLibraryMatch && <LibraryMatch artist={item.artist} title={item.title} />}
    {!directSource(item) && item.preview_checked
      ? <small className="track-no-source">No catalog preview found.</small>
      : <span className="track-playback">
        <button type="button" className="btn btn-xs" disabled={pending}
          aria-label={`Play ${label}`} onClick={() => audition(true)}>Play</button>
        <button type="button" className="btn btn-xs" disabled={pending}
          aria-label={`Queue ${label}`} onClick={() => audition(false)}>Queue</button>
        {pending && <small role="status">Looking up preview…</small>}
        {message && <small role="status">{message}</small>}
      </span>}
  </span>
}
