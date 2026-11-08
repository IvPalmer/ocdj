import { useEffect, useState } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import api from '../../api/client'
import './InstagramPanel.css'

const filters = ['all', 'pending', 'identified', 'reviewed', 'wishlist', 'error']
const matches = (m, filter) => filter === 'all' || (filter === 'reviewed' ? m.reviewed : filter === 'wishlist' ? !!m.wanted_id : filter === 'pending' ? ['pending', 'running'].includes(m.status) && !m.reviewed : m.status === filter)

function Editor({ media, act, busy }) {
  const [artist, setArtist] = useState(media.artist)
  const [title, setTitle] = useState(media.title)
  useEffect(() => { setArtist(media.artist); setTitle(media.title) }, [media.artist, media.title])
  const dirty = artist !== media.artist || title !== media.title
  return <div className="ig-editor">
    <div className="ig-status"><span>{media.kind === 'image' ? 'Image · not counted in recognition' : media.status}</span>{media.reviewed && <span>Reviewed</span>}{media.wanted_id && <span>In wishlist</span>}</div>
    {media.kind === 'video' && <>
      <button disabled={busy || media.queued} onClick={() => act('recognize')}>{media.queued ? 'Recognition queued · waiting for Mac' : ['error', 'no-match'].includes(media.status) ? 'Retry Shazam' : 'Recognize with Shazam'}</button>
      {media.status === 'no-match' && <p>No match in the sampled audio. You can retry or enter a correction.</p>}
      {media.status === 'error' && <p role="status">Recognition failed. Check the Mac connector, then retry.</p>}
      {media.candidates.length > 0 && <div className="ig-candidates"><p>Shazam candidates · select to correct</p>{media.candidates.map((c, i) => <button key={i} disabled={busy} onClick={() => act('', c)}>{c.artist} — {c.title}</button>)}</div>}
      <form onSubmit={e => { e.preventDefault(); act('', { artist, title }) }}>
        <label>Artist<input value={artist} maxLength={500} onChange={e => setArtist(e.target.value)} /></label>
        <label>Track title<input value={title} maxLength={500} onChange={e => setTitle(e.target.value)} /></label>
        <button disabled={busy || !dirty}>Save correction</button>
      </form>
      <div className="ig-actions"><button disabled={busy || !media.artist || !media.title || !!media.wanted_id || dirty} onClick={() => act('wishlist')}>{media.wanted_id ? 'Added to wishlist' : 'Add to wishlist'}</button>
      <button disabled={busy} onClick={() => act('', { reviewed: !media.reviewed })}>{media.reviewed ? 'Mark unreviewed' : 'Mark reviewed'}</button></div>
    </>}
    <p className="ig-note">Reviewed is your workspace progress. Instagram saves stay untouched.</p>
  </div>
}

export default function InstagramPanel() {
  const client = useQueryClient()
  const [selectedPost, setSelectedPost] = useState(null)
  const [selectedMedia, setSelectedMedia] = useState(null)
  const [filter, setFilter] = useState('all')
  const [playbackError, setPlaybackError] = useState(false)
  const query = useQuery({ queryKey: ['instagram'], queryFn: () => api.get('/instagram/'), refetchInterval: 5000 })
  const mutation = useMutation({ mutationFn: ({ id, action, data }) => action ? api.post(`/instagram/media/${id}/${action}/`, {}) : api.patch(`/instagram/media/${id}/`, data), onSuccess: () => client.invalidateQueries({ queryKey: ['instagram'] }) })
  const posts = query.data?.posts || []
  const visiblePosts = posts.filter(p => p.media.some(m => matches(m, filter)))
  const post = visiblePosts.find(p => p.id === selectedPost) || visiblePosts[0]
  const media = post?.media.find(m => m.id === selectedMedia) || post?.media.find(m => matches(m, filter))
  const videos = posts.flatMap(p => p.media).filter(m => m.kind === 'video')
  const reviewed = videos.filter(m => m.reviewed).length
  const identified = videos.filter(m => m.status === 'identified').length
  const act = (action, data) => mutation.mutate({ id: media.id, action, data })
  useEffect(() => { setPlaybackError(false); mutation.reset() }, [media?.id])

  return <section className="ig-workspace">
    <header className="ig-heading"><div><p className="ig-eyebrow">Capture / Instagram</p><h1>Track Downloads</h1><p>Five newest saved posts. Every carousel video, one at a time.</p></div><button onClick={() => query.refetch()} disabled={query.isFetching}>Refresh status</button></header>
    {query.isPending ? <p role="status">Loading collection…</p> : query.isError ? <div role="alert">Could not load Instagram workspace. <button onClick={() => query.refetch()}>Retry</button></div> : <>
      {query.data.fixture && <p className="ig-banner">Offline fixture · synthetic video and recognition results · this is not live Instagram QA.</p>}
      <div className="ig-summary"><span>Mac connector: <strong>{query.data.connector_status}</strong></span><span>{identified} / {videos.length} videos identified</span><span>{reviewed} / {videos.length} reviewed</span></div>
      {query.data.connector_status !== 'ready' && <p className="ig-banner" role="status">{({ offline: 'Mac is offline. Cached videos remain available until expiry; recognition waits for the connector.', 'session-expired': 'Instagram session expired. Reopen the existing Chrome session on Mac and rerun the connector.', restricted: 'Instagram restricted collection access. Check the existing Mac session before retrying.', 'rate-limited': 'Instagram rate limit reached. Wait before the next connector run.', error: 'Mac connector reported an error. Check its preflight and retry.' })[query.data.connector_status]}</p>}
      <progress className="ig-progress" aria-label="Videos reviewed" value={reviewed} max={videos.length || 1} />
      {!posts.length ? <div className="ig-empty"><h2>Your collection is ready to connect</h2><p>Run the Instagram connector on your Mac with its existing Chrome session. The first sync brings the five newest saved posts and all their carousel children.</p></div> : <>
        <div className="ig-filters" aria-label="Filter media">{filters.map(f => <button key={f} aria-pressed={filter === f} onClick={() => { setFilter(f); setSelectedMedia(null) }}>{f[0].toUpperCase()+f.slice(1)}</button>)}</div>
        {!post ? <p>No media match this filter.</p> : <div className="ig-columns">
          <nav className="ig-queue" aria-label="Saved posts">{visiblePosts.map((p) => {
            const v = p.media.filter(m => m.kind === 'video')
            return <button key={p.id} aria-current={post.id === p.id ? 'true' : undefined} onClick={() => { setSelectedPost(p.id); setSelectedMedia(null) }}><span className="ig-post-number">{String(posts.indexOf(p)+1).padStart(2, '0')}</span><span><strong>@{p.author || 'Instagram'}</strong><span className="ig-caption">{p.caption || 'Saved post'}</span><small>{v.length} videos · {v.filter(m => m.reviewed).length} reviewed</small></span></button>
          })}</nav>
          <article className="ig-focus"><div className="ig-source"><strong>@{post.author}</strong><a href={post.permalink} target="_blank" rel="noopener noreferrer">Open Instagram</a></div>
            {media && <>
              <div className="ig-player">{media.kind === 'image' ? <p>Image {media.position+1} · open the original post to view.</p> : media.playback_url && !playbackError ? <video key={media.id} controls preload="metadata" playsInline src={media.playback_url} aria-label={`Post ${posts.indexOf(post)+1}, video ${media.position+1}`} onError={() => setPlaybackError(true)} /> : <p role="status">{playbackError ? 'Playback unavailable or cache expired.' : 'Video waiting for Mac upload or cache refresh.'} Rerun the connector to refresh this media.</p>}</div>
              <div className="ig-child-heading">Carousel · {media.position+1} of {post.media.length}</div>
              <div className="ig-children" aria-label="Carousel children">{post.media.map(m => <button key={m.id} aria-label={`${m.kind} ${m.position+1}, ${m.status}${m.reviewed ? ', reviewed' : ''}`} aria-pressed={m.id === media.id} onClick={() => setSelectedMedia(m.id)}><span>{m.kind === 'video' ? 'Video' : 'Image'} {m.position+1}</span><small>{m.reviewed ? 'Reviewed' : m.kind === 'image' ? 'Image' : m.status}</small></button>)}</div>
              {mutation.isError && <p role="alert">{mutation.error.data ? JSON.stringify(mutation.error.data) : 'Could not save. Try again.'}</p>}
              <Editor key={media.id} media={media} act={act} busy={mutation.isPending} />
            </>}
          </article>
        </div>}
      </>}
      {query.data.synced_at && <p className="ig-note">Last sync: {new Date(query.data.synced_at).toLocaleString()} · newest saved first</p>}
    </>}
  </section>
}
