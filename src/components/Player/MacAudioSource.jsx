import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'

// Preparation belongs to the persistent player, never to a route or a queue row.
export default function MacAudioSource({ track, playing, onPlaying, onEnded, onError, AudioSource, EmbedSource, WantedOnlineSource }) {
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
    const resolveFallback = () => {
      const fallback = track.fallback
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
  const Source = resolved?.source.kind === 'wanted' ? WantedOnlineSource
    : resolved?.source.kind === 'audio' ? AudioSource : EmbedSource
  return <>
    {!fallbackSelected && track.fallback?.source &&
      <button type="button" className="btn btn-xs" onClick={() => setFallbackSelected(true)}>
        {track.fallback.source.kind === 'wanted' ? 'Use online source' : 'Use preview / mix'}
      </button>}
    {!resolved ? <small role="status">Waiting for Mac audio… The Mac connector must be running.</small> : <>
      {fallbackSelected && resolved.source.kind !== 'wanted' && <small role="status">Using fallback · {resolved.description}</small>}
      <Source {...{ track: resolved, playing, onPlaying, onEnded, AudioSource, EmbedSource }}
        onError={fallbackSelected ? onError : () => setFallbackSelected(true)} />
    </>}
  </>
}
