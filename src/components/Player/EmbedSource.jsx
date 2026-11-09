import { useEffect, useRef, useState } from 'react'

let youtubeReady
function loadYouTube() {
  if (window.YT?.Player) return Promise.resolve()
  if (!youtubeReady) youtubeReady = new Promise((resolve, reject) => {
    const previous = window.onYouTubeIframeAPIReady
    window.onYouTubeIframeAPIReady = () => { previous?.(); resolve() }
    const script = document.createElement('script')
    script.src = 'https://www.youtube.com/iframe_api'
    script.onerror = () => { youtubeReady = null; reject(new Error('YouTube unavailable')) }
    document.body.appendChild(script)
  })
  return youtubeReady
}
let soundcloudReady
function loadSoundCloud() {
  if (window.SC?.Widget) return Promise.resolve()
  if (!soundcloudReady) soundcloudReady = new Promise((resolve, reject) => {
    const script = document.createElement('script')
    script.src = 'https://w.soundcloud.com/player/api.js'
    script.onload = resolve
    script.onerror = () => { soundcloudReady = null; reject(new Error('SoundCloud unavailable')) }
    document.body.appendChild(script)
  })
  return soundcloudReady
}
let spotifyReady
function loadSpotify() {
  if (!spotifyReady) spotifyReady = new Promise((resolve, reject) => {
    window.onSpotifyIframeApiReady = resolve
    const script = document.createElement('script')
    script.src = 'https://open.spotify.com/embed/iframe-api/v1'
    script.onerror = () => { spotifyReady = null; reject(new Error('Spotify unavailable')) }
    document.body.appendChild(script)
  })
  return spotifyReady
}

export default function EmbedSource({ track, playing, onPlaying, onEnded, onError }) {
  const host = useRef(null)
  const instance = useRef(null)
  const latest = useRef({ playing, onPlaying, onEnded, onError })
  latest.current = { playing, onPlaying, onEnded, onError }
  const [ready, setReady] = useState(false)
  useEffect(() => {
    let disposed = false
    let widget
    let ended = false
    let readyTimer
    const fail = () => { if (!disposed) latest.current.onError() }
    const end = () => { if (!disposed && !ended) { ended = true; latest.current.onEnded() } }
    if (track.source.kind === 'youtube') {
      loadYouTube().then(() => {
        if (disposed) return
        const target = document.createElement('div')
        host.current.appendChild(target)
        instance.current = new window.YT.Player(target, {
          videoId: track.source.videoId,
          playerVars: { autoplay: 0, playsinline: 1, origin: window.location.origin,
            start: Math.floor(track.start || 0),
            ...(track.end > track.start ? { end: Math.floor(track.end) } : {}) },
          events: {
            onReady: () => { if (!disposed) setReady(true) },
            onStateChange: event => {
              if (disposed || ended) return
              if (event.data === 0) end()
              else if (event.data === 1 || event.data === 2) latest.current.onPlaying(event.data === 1)
            },
            onError: fail,
            onAutoplayBlocked: () => {
              if (!disposed) latest.current.onError({ name: 'NotAllowedError' })
            },
          },
        })
      }).catch(fail)
    } else if (track.source.kind === 'spotify') {
      readyTimer = setTimeout(fail, 12000)
      loadSpotify().then(api => {
        if (disposed) return
        const target = document.createElement('div')
        host.current.appendChild(target)
        api.createController(target, { uri: track.source.uri, height: 152, width: host.current.clientWidth }, controller => {
          if (disposed) { controller.destroy(); return }
          instance.current = controller
          let lastPosition = 0
          let started = false
          controller.addListener('ready', () => {
            clearTimeout(readyTimer)
            if (!disposed) setReady(true)
          })
          controller.addListener('playback_update', ({ data }) => {
            if (disposed || ended || data.playingURI !== track.source.uri || data.isBuffering) return
            const { duration, position, isPaused } = data
            if (!isPaused) started = true
            if (!started) return
            if (isPaused && duration > 0 &&
                (position >= duration - 250 || (position === 0 && lastPosition >= duration - 1000))) end()
            else latest.current.onPlaying(!isPaused)
            lastPosition = position
          })
        })
      }).catch(fail)
    } else {
      loadSoundCloud().then(() => {
        if (disposed) return
        widget = window.SC.Widget(host.current)
        instance.current = widget
        const events = window.SC.Widget.Events
        widget.bind(events.READY, () => {
          if (disposed) return
          widget.seekTo((track.start || 0) * 1000)
          setReady(true)
        })
        widget.bind(events.PLAY, () => { if (!disposed && !ended) latest.current.onPlaying(true) })
        widget.bind(events.PAUSE, () => { if (!disposed && !ended) latest.current.onPlaying(false) })
        widget.bind(events.FINISH, end)
        widget.bind(events.ERROR, fail)
        widget.bind(events.PLAY_PROGRESS, event => {
          if (track.end > track.start && event.currentPosition >= track.end * 1000) { widget.pause(); end() }
        })
      }).catch(fail)
    }
    return () => {
      disposed = true
      clearTimeout(readyTimer)
      instance.current?.destroy?.()
      if (widget) Object.values(window.SC.Widget.Events).forEach(event => widget.unbind(event))
    }
  }, [track])
  useEffect(() => {
    if (!ready) return
    const p = instance.current
    if (track.source.kind === 'youtube') playing ? p.playVideo() : p.pauseVideo()
    else if (track.source.kind === 'spotify') playing ? p.resume() : p.pause()
    else playing ? p.play() : p.pause()
  }, [playing, ready, track])
  return track.source.kind !== 'soundcloud'
    ? <div ref={host} className={`bottom-player__embed bottom-player__embed--${track.source.kind}`} />
    : <iframe ref={host} className="bottom-player__embed" title="SoundCloud source" allow="autoplay"
      src={`https://w.soundcloud.com/player/?url=${encodeURIComponent(track.source.url)}&auto_play=false&visual=false`} />
}
