import { useEffect, useState } from 'react'
import { api } from '../../api/client'
import { directSource, linkedTrackSource } from './TrackPlayback'

const PROVIDER_LABELS = { youtube: 'YouTube', spotify: 'Spotify', soundcloud: 'SoundCloud', audio: 'Catalog preview' }

export default function WantedOnlineSource({ track, playing, onPlaying, onEnded, onError, AudioSource, EmbedSource }) {
  const [resolved, setResolved] = useState(null)
  const [skip, setSkip] = useState(null)
  const [failed, setFailed] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    const options = { signal: controller.signal }
    const endpoint = `/wanted/items/${track.wantedId}`
    async function resolve() {
      let source = skip ? null : track.source.linked || track.source.preview
      if (!source && !skip && !track.source.previewChecked) {
        try {
          const preview = await api.post(`${endpoint}/preview/`, {}, options)
          source = directSource({ preview_url: preview?.url })
        } catch { /* Other providers can still supply a track. */ }
      }
      if (controller.signal.aborted) return
      const searched = !source
      try {
        if (searched) {
          const query = skip === 'youtube' ? '?skip=youtube' : ''
          const result = await api.get(`${endpoint}/online-source/${query}`, options)
          source = linkedTrackSource(result?.url)
        }
        if (controller.signal.aborted) return
        if (source) setResolved({ track: { ...track, source }, searched })
        else {
          setFailed(true)
          onError('No playable recording found for this track.')
        }
      } catch {
        if (controller.signal.aborted) return
        setFailed(true)
        onError('Could not reach the online sources. Press Play to retry.')
      }
    }
    // StrictMode can immediately discard a mount before it starts a lookup.
    queueMicrotask(() => { if (!controller.signal.aborted) resolve() })
    return () => controller.abort()
  }, [track, skip])

  if (failed) return null
  if (!resolved) return <small role="status">Looking for online audio…</small>
  const Source = resolved.track.source.kind === 'audio' ? AudioSource : EmbedSource
  function sourceFailed(error) {
    if (error?.name === 'NotAllowedError' || (resolved.searched && resolved.track.source.kind !== 'youtube')) {
      return onError('This source could not start. Press Play to retry.')
    }
    setResolved(null)
    setSkip(resolved.searched ? 'youtube' : 'saved')
  }
  return <>
    <small className="bottom-player__provider">{PROVIDER_LABELS[resolved.track.source.kind]}</small>
    <Source key={resolved.track.source.kind} track={resolved.track}
      {...{ playing, onPlaying, onEnded }} onError={sourceFailed} />
  </>
}
