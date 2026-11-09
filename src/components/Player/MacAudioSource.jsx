import { useEffect, useState } from 'react'
import { api } from '../../api/client'

// Preparation belongs to the persistent player, never to a route or a queue row.
export default function MacAudioSource({ track, playing, onPlaying, onEnded, onError, AudioSource }) {
  const [url, setUrl] = useState('')
  useEffect(() => {
    let active = true
    let timer
    const endpoint = `/library/tracks/${track.source.libraryId}/playback/`
    const deadline = Date.now() + 185000
    async function prepare(first) {
      try {
        const result = first ? await api.post(endpoint, {}) : await api.get(endpoint)
        if (!active) return
        if (result.status === 'ready' && result.url === `/api/library/tracks/${track.source.libraryId}/audio/`) {
          setUrl(result.url)
        } else if (result.status === 'pending' && Date.now() < deadline) {
          timer = setTimeout(() => prepare(false), 2000)
        } else onError()
      } catch { if (active) onError() }
    }
    prepare(true)
    return () => { active = false; clearTimeout(timer) }
  }, [track.queueId])
  return url ? <AudioSource {...{ track: { ...track, source: { kind: 'audio', url } }, playing, onPlaying, onEnded, onError }} />
    : <small role="status">Waiting for Mac audio… The Mac connector must be running.</small>
}
