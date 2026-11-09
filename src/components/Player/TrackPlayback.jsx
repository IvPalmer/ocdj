import LibraryMatch, { useIndexedPlayback } from './LibraryMatch'
import { usePlayer } from './PlayerContext'

// Page links and local filesystem paths are deliberately not media sources.
export function directSource(track) {
  const url = track.preview_url || track.playback_url
  if (url && /^(https?:\/\/|\/api\/)/.test(url)) return { kind: 'audio', url }
  return Number.isSafeInteger(track.playback_id) && track.playback_id > 0
    ? { kind: 'mac', libraryId: track.playback_id } : null
}

function mixSource(value) {
  let url
  try { url = new URL(value) } catch { return null }
  if (!['https:', 'http:'].includes(url.protocol)) return null

  const [, section, id] = url.pathname.split('/')
  let videoId
  if (url.hostname === 'youtu.be') {
    videoId = section
  } else if (['youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'].includes(url.hostname)) {
    videoId = section === 'watch' ? url.searchParams.get('v')
      : ['embed', 'shorts', 'live'].includes(section) ? id : null
  }
  if (videoId && /^[a-zA-Z0-9_-]+$/.test(videoId)) {
    return { kind: 'youtube', url: url.href, videoId }
  }
  if (['soundcloud.com', 'www.soundcloud.com', 'on.soundcloud.com'].includes(url.hostname)) {
    return { kind: 'soundcloud', url: url.href }
  }
  return null
}

export function recognizedTrack(track, job, index) {
  const preview = directSource(track)
  const source = preview || mixSource(job.url)
  const start = track.timestamp_start || 0
  const end = track.timestamp_end > start ? track.timestamp_end : job.tracklist[index + 1]?.timestamp_start
  return { artist: track.artist, title: track.title, source,
    start: preview ? 0 : start, end: preview ? undefined : end,
    description: preview ? 'Catalog preview' : 'Original mix · identified segment' }
}

export default function TrackPlayback({ track, unavailable = 'No browser-playable audio source supplied.' }) {
  const player = usePlayer()
  const playable = useIndexedPlayback(track)
  const label = [track.artist, track.title].filter(Boolean).join(' — ')
  return <span className="track-controls">
    {!playable.source ? <small className="track-no-source">{unavailable}</small> : <span className="track-playback">
      <button type="button" className="btn btn-xs" aria-label={`Play ${label}`} onClick={() => player.play(playable)}>Play</button>
      <button type="button" className="btn btn-xs" aria-label={`Queue ${label}`} onClick={() => player.enqueue(playable)}>Queue</button>
    </span>}
    <LibraryMatch artist={track.artist} title={track.title} />
  </span>
}
