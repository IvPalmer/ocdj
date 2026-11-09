import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'
import { directSource } from './TrackPlayback'

// Preparation belongs to the persistent player, never to a route or a queue row.
export default function MacAudioSource({ track, playing, onPlaying, onEnded, onError, AudioSource, EmbedSource }) {
  const [resolved, setResolved] = useState(null)
  const [failed, setFailed] = useState(false)
  const [fallbackSelected, setFallbackSelected] = useState(false)
  const preparation = useRef(null)
  useEffect(() => {
    let active = true
    let timer
    const endpoint = `/library/tracks/${track.source.libraryId}/playback/`
    const deadline = Date.now() + 185000
    setResolved(null)
    const resolveFallback = async () => {
      // Preserve the original preview/mix and its segment bounds. Wanted lookup
      // stays lazy: future queue entries never fetch previews or stage Mac audio.
      let fallback = track.fallback
      try {
        if (fallback && !fallback.source && fallback.wantedId && !fallback.previewChecked) {
          const preview = await api.post(`/wanted/items/${fallback.wantedId}/preview/`, {})
          fallback = { ...fallback, source: directSource({ preview_url: preview?.url }) }
        }
      } catch { /* Keep the explicit retry when both sources are unavailable. */ }
      if (!active) return
      if (fallback?.source) setResolved({ ...fallback, queueId: track.queueId })
      else { setFailed(true); onError() }
    }
    async function prepare(first) {
      try {
        // StrictMode effect replay shares the one write, including a rejection.
        const result = first ? await (preparation.current ??= api.post(endpoint, {})) : await api.get(endpoint)
        if (!active) return
        if (result.status === 'ready' && result.url === `/api/library/tracks/${track.source.libraryId}/audio/`) {
          setResolved({ ...track, source: { kind: 'audio', url: result.url } })
        } else if (result.status === 'pending' && Date.now() < deadline) {
          timer = setTimeout(() => prepare(false), 2000)
        } else setFallbackSelected(true)
      } catch { if (active) setFallbackSelected(true) }
    }
    if (fallbackSelected) resolveFallback()
    else prepare(true)
    return () => { active = false; clearTimeout(timer) }
  }, [track.queueId, fallbackSelected])
  if (failed) return null
  const Source = resolved?.source.kind === 'audio' ? AudioSource : EmbedSource
  return <>
    {!fallbackSelected && (track.fallback?.source || (track.fallback?.wantedId && !track.fallback.previewChecked)) &&
      <button type="button" className="btn btn-xs" onClick={() => setFallbackSelected(true)}>Use preview / mix</button>}
    {!resolved ? <small role="status">Waiting for Mac audio… The Mac connector must be running.</small> : <>
      {fallbackSelected && <small role="status">Using fallback · {resolved.description}</small>}
      <Source {...{ track: resolved, playing, onPlaying, onEnded }}
        onError={fallbackSelected ? onError : () => setFallbackSelected(true)} />
    </>}
  </>
}
