import { usePlayer } from './PlayerContext'

// Page links and local filesystem paths are deliberately not media sources.
export function directSource(track) {
  const url = track.preview_url || track.playback_url
  return url && /^(https?:\/\/|\/api\/)/.test(url) ? { kind: 'audio', url } : null
}

export function recognizedTrack(track, job, index) {
  const youtube = job.url?.match(/(?:youtu\.be\/|youtube\.com\/watch\?v=|youtube\.com\/embed\/)([^&?/]+)/)
  const source = directSource(track) || (youtube
    ? { kind: 'youtube', url: job.url, videoId: youtube[1] }
    : /^https?:\/\/(?:www\.)?soundcloud\.com\//.test(job.url || '') ? { kind: 'soundcloud', url: job.url } : null)
  return { artist: track.artist, title: track.title, source,
    start: source?.kind === 'audio' ? 0 : track.timestamp_start || 0,
    end: source?.kind === 'audio' ? undefined : job.tracklist[index + 1]?.timestamp_start,
    description: source?.kind === 'audio' ? 'Catalog preview' : 'Original mix · identified segment' }
}

export default function TrackPlayback({ track, unavailable = 'No browser-playable audio source supplied.' }) {
  const player = usePlayer()
  if (!track.source) return <small className="track-no-source">{unavailable}</small>
  const label = [track.artist, track.title].filter(Boolean).join(' — ')
  return <span className="track-playback">
    <button type="button" className="btn btn-xs" aria-label={`Play ${label}`} onClick={() => player.play(track)}>Play</button>
    <button type="button" className="btn btn-xs" aria-label={`Queue ${label}`} onClick={() => player.enqueue(track)}>Queue</button>
  </span>
}
