import { useEffect, useRef, useState } from 'react'
import { api } from '../../api/client'

/* One <audio> per table, shared by every row. Per-row elements would let two
   previews overlap, and the fix for that ends up being this anyway. */
export default function usePreviewPlayer() {
  const audioRef = useRef(null)
  const [playingId, setPlayingId] = useState(null)
  const [loadingId, setLoadingId] = useState(null)

  useEffect(() => {
    const el = new Audio()
    el.addEventListener('ended', () => setPlayingId(null))
    el.addEventListener('error', () => setPlayingId(null))
    audioRef.current = el
    return () => { el.pause(); audioRef.current = null }
  }, [])

  const stop = () => {
    if (audioRef.current) audioRef.current.pause()
    setPlayingId(null)
  }

  const toggle = async (item) => {
    if (playingId === item.id) return stop()
    stop()
    setLoadingId(item.id)
    try {
      // Resolve through the backend rather than calling iTunes from here: the
      // answer is cached on the item, so the second play costs nothing.
      const res = await api.post(`/wanted/items/${item.id}/preview/`, {})
      if (!res?.url) return   // nobody has it — the button says so
      audioRef.current.src = res.url
      await audioRef.current.play()
      setPlayingId(item.id)
    } catch {
      setPlayingId(null)
    } finally {
      setLoadingId(null)
    }
  }

  return { toggle, playingId, loadingId, stop }
}
