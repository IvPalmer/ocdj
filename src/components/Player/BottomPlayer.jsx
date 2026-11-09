import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAddToQueue, useCreateWantedItem } from '../../api/hooks'
import { usePlayer } from './PlayerContext'
import EmbedSource from './EmbedSource'
import MacAudioSource from './MacAudioSource'
import './BottomPlayer.css'

function AudioSource({ track, playing, onPlaying, onEnded, onError }) {
  const audio = useRef(null)
  useEffect(() => {
    let active = true
    if (playing) audio.current.play().catch(() => { if (active) onError() })
    else audio.current.pause()
    return () => { active = false }
  }, [playing, track])
  return <audio ref={audio} controls preload="none" src={track.source.url} aria-label="Shared track audio"
    onPlay={() => onPlaying(true)} onPause={() => onPlaying(false)} onEnded={onEnded} onError={onError} />
}

export default function BottomPlayer() {
  const player = usePlayer()
  const track = player.items[player.index]
  const container = useRef(null)
  const [attempt, setAttempt] = useState(0)
  const ended = useRef(false)
  useEffect(() => {
    const observer = new ResizeObserver(() => {
      document.documentElement.style.setProperty('--player-height', `${container.current.offsetHeight}px`)
    })
    observer.observe(container.current)
    return () => { observer.disconnect(); document.documentElement.style.removeProperty('--player-height') }
  }, [])
  const [error, setError] = useState('')
  const [actionMessage, setActionMessage] = useState('')
  const wanted = useCreateWantedItem()
  const soulseek = useAddToQueue()
  const navigate = useNavigate()
  useEffect(() => { setError(''); setActionMessage(''); ended.current = false }, [track?.queueId])
  useEffect(() => {
    if (player.playing) document.querySelectorAll('video, audio').forEach(media => {
      if (!media.closest('.bottom-player')) media.pause()
    })
  }, [player.playing, track?.queueId])
  const fail = () => { setError(track?.source.kind === 'mac'
    ? 'Mac audio unavailable, expired, or playback blocked. Start the private Mac connector, then press Play to retry.'
    : 'Audio source unavailable or playback blocked. Press Play to retry.'); player.pause() }
  const onPlaying = value => player.dispatch({ type: 'playing', value })
  const onEnded = () => {
    ended.current = player.index === player.items.length - 1
    player.dispatch({ type: 'next' })
  }
  async function action(kind) {
    const queueId = track.queueId
    try {
      if (kind === 'wanted') {
        await wanted.mutateAsync({ artist: track.artist, title: track.title })
        // Keep feedback attached to the track that initiated the request.
        setActionMessage({ queueId, text: 'Added to Wanted' })
      } else {
        await soulseek.mutateAsync(track.wantedId ? { wanted_item_ids: [track.wantedId] }
          : { query: `${track.artist || ''} ${track.title || ''}`.trim() })
        setActionMessage({ queueId, text: 'Added to Soulseek search queue' })
        navigate('/soulseek')
      }
    } catch { setActionMessage({ queueId, text: 'Action failed. Try again.' }) }
  }
  const identified = !!(track?.artist && track?.title)
  return <section ref={container} className="bottom-player" aria-label="Persistent player">
    <div className="bottom-player__main">
      <div className="bottom-player__track"><strong>{track ? [track.artist, track.title].filter(Boolean).join(' — ') : 'Nothing playing'}</strong>
        <small>{track?.description || 'Play or queue a track from any workspace.'}</small></div>
      <div className="bottom-player__controls">
        <button disabled={player.index <= 0} aria-label="Previous track" onClick={() => player.dispatch({ type: 'previous' })}>Previous</button>
        <button disabled={!track} aria-label={player.playing ? 'Pause playback' : 'Play playback'} onClick={() => {
          if (error || ended.current) setAttempt(value => value + 1)
          ended.current = false
          setError(''); onPlaying(!player.playing)
        }}>{player.playing ? 'Pause' : 'Play'}</button>
        <button disabled={player.index >= player.items.length - 1} aria-label="Next track" onClick={onEnded}>Next</button>
      </div>
      {track && <div className="bottom-player__actions">
        <button disabled={!identified || wanted.isPending || track.wantedId || (actionMessage?.queueId === track.queueId && actionMessage.text === 'Added to Wanted')}
          onClick={() => action('wanted')}>{track.wantedId ? 'In Wanted' : 'Add to Wanted'}</button>
        <button disabled={!identified || soulseek.isPending} onClick={() => action('soulseek')}>Find on Soulseek</button>
      </div>}
      <details className="bottom-player__queue"><summary>Queue ({player.items.length})</summary>
        <ol>{player.items.map((item, index) => <li key={item.queueId} aria-current={index === player.index ? 'true' : undefined}>
          <button onClick={() => player.dispatch({ type: 'select', index })}>{index === player.index ? 'Current: ' : index > player.index ? 'Up next: ' : ''}{item.artist} — {item.title}</button>
        </li>)}</ol>
      </details>
    </div>
    {track && (track.source.kind === 'mac'
      ? <MacAudioSource key={`${track.queueId}:${attempt}`} {...{ track, playing: player.playing, onPlaying, onEnded, onError: fail, AudioSource, EmbedSource }} />
      : track.source.kind === 'audio'
      ? <AudioSource key={`${track.queueId}:${attempt}`} {...{ track, playing: player.playing, onPlaying, onEnded, onError: fail }} />
      : <EmbedSource key={`${track.queueId}:${attempt}`} {...{ track, playing: player.playing, onPlaying, onEnded, onError: fail }} />)}
    {error && <p role="alert">{error}</p>}
    {actionMessage && actionMessage.queueId === track?.queueId && <p role="status">{actionMessage.text}</p>}
  </section>
}
