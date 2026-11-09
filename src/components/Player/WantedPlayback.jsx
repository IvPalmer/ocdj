import { usePlayer } from './PlayerContext'
import { directSource, linkedTrackSource } from './TrackPlayback'
import LibraryMatch, { useIndexedPlayback } from './LibraryMatch'

export function WantedPlaybackStatus({ id }) {
  const player = usePlayer()
  if (player.items[player.index]?.wantedId !== id) return null
  return <small className="track-playback-state">
    {player.audible ? 'Playing' : player.playing ? 'Starting…' : 'Paused'}
  </small>
}

export default function WantedPlayback({ item, showLibraryMatch = true }) {
  const player = usePlayer()
  const saved = (item.notes || '').split('\n').map(line => linkedTrackSource(line.trim())).filter(Boolean)
  // Multiple different links can be release or set notes. Search by metadata instead.
  const linked = saved.length === 1 ? saved[0] : null
  const track = { artist: item.artist, title: item.title, wantedId: item.id, description: 'Online audio',
    source: { kind: 'wanted', linked, preview: directSource(item), previewChecked: !!item.preview_checked } }
  const indexed = useIndexedPlayback(track)
  const playable = linked ? track : indexed
  const current = player.items[player.index]?.wantedId === item.id
  const label = [item.artist, item.title].filter(Boolean).join(' — ')

  return <span className="track-controls">
    <span className="track-playback">
      <button type="button" className={current ? 'btn btn-xs btn-xs--active' : 'btn btn-xs'}
        aria-label={`Play ${label}`} aria-current={current || undefined}
        onClick={() => player.play(playable)}>Play</button>
      <button type="button" className="btn btn-xs"
        aria-label={`Queue ${label}`} onClick={() => player.enqueue(playable)}>Queue</button>
    </span>
    {showLibraryMatch && <LibraryMatch artist={item.artist} title={item.title} />}
  </span>
}
