import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import api from '../../api/client'
import './InstagramPanel.css'

const filters = ['all', 'pending', 'identified', 'reviewed', 'wishlist', 'error']
const matches = (m, filter) => filter === 'all' || (filter === 'reviewed' ? m.reviewed : filter === 'wishlist' ? m.wanted_matches_current : filter === 'pending' ? ['pending', 'running'].includes(m.status) && !m.reviewed : m.status === filter)

function Editor({ media, act, busy, audioRef, pauseVideo }) {
  const [artist, setArtist] = useState(media.artist)
  const [title, setTitle] = useState(media.title)
  useEffect(() => { setArtist(media.artist); setTitle(media.title) }, [media.artist, media.title])
  const [previewPlaybackError, setPreviewPlaybackError] = useState(false)
  const preview = useMutation({
    mutationFn: candidate => api.post(`/instagram/media/${media.id}/candidate-preview/`, candidate),
  })
  const selectedCandidate = preview.variables
  const showPreview = selectedCandidate && media.candidates.some(candidate =>
    candidate.artist === selectedCandidate.artist && candidate.title === selectedCandidate.title)
  const dirty = artist !== media.artist || title !== media.title

  function listen(candidate) {
    setPreviewPlaybackError(false)
    preview.mutate(candidate)
  }

  return <div className="ig-editor">
    <div className="ig-status"><span>{media.kind === 'image' ? 'Image · not counted in recognition' : media.status}</span>{media.reviewed && <span>Reviewed</span>}{media.wanted_matches_current && <span>In wishlist</span>}</div>
    {media.kind === 'video' && <>
      <button disabled={busy || media.queued} onClick={() => act('recognize')}>{media.queued ? 'Recognition queued' : ['error', 'no-match'].includes(media.status) ? 'Retry recognition' : 'Recognize'}</button>
      {media.recognition?.source && <p className="ig-note">Evidence: {media.recognition.source} · {media.recognition.certainty}. Comments: {media.recognition.comments}. {media.recognition.source === 'caption' ? 'Caption IDs refer to the post or numbered carousel position; verify against the audio.' : 'Review the candidate against the source before confirming.'}</p>}
      {media.recognition?.records?.length > 0 && <details className="ig-note"><summary>Source evidence</summary>{media.recognition.records.map((record, index) => <p key={index}>{record.source}{record.comment_id ? ` · comment ${record.comment_id}` : ''}: {record.text}</p>)}</details>}
      {media.status === 'no-match' && <p>No match in the sampled audio. You can retry or enter a correction.</p>}
      {media.status === 'error' && <p role="status">Recognition failed during transport, decoding or service access. Retry when the source is available.</p>}
      {media.candidates.length > 0 && <section className="ig-candidates" aria-label="Identification candidates">
        <p>Identification candidates</p>
        {media.candidates.map(candidate => {
          const label = `${candidate.artist} — ${candidate.title}`
          return <div className="ig-candidate" key={JSON.stringify([candidate.artist, candidate.title])}>
            <strong>{label}</strong>
            <button type="button" disabled={preview.isPending} onClick={() => listen(candidate)}
              aria-label={`Listen to preview of ${label}`}>Listen</button>
            <button type="button" disabled={busy} onClick={() => act('', { artist: candidate.artist, title: candidate.title })}
              aria-label={`Use identification ${label}`}>Use match</button>
          </div>
        })}
        {showPreview && <div className="ig-preview" aria-live="polite">
          {preview.isPending && <p>Looking for a preview…</p>}
          {preview.isError && <p>Could not load the preview. Select Listen to try again.</p>}
          {preview.isSuccess && !preview.data.url && <p>No catalog preview is available for this match.</p>}
          {preview.data?.url && <>
            <strong>{selectedCandidate.artist} — {selectedCandidate.title}</strong>
            <audio ref={audioRef} controls autoPlay preload="none" src={preview.data.url}
              aria-label="Recognized track preview" onPlay={pauseVideo}
              onError={() => setPreviewPlaybackError(true)} />
            {previewPlaybackError && <p>Could not play the preview. Select Listen to try again.</p>}
            <small>Preview courtesy of {preview.data.provider === 'itunes' ? 'iTunes' : 'Deezer'}</small>
            {preview.data.track_url && <a href={preview.data.track_url} target="_blank" rel="noopener noreferrer">
              Open catalog track
            </a>}
          </>}
        </div>}
      </section>}
      <form onSubmit={e => { e.preventDefault(); act('', { artist, title }) }}>
        <label>Artist<input value={artist} maxLength={500} onChange={e => setArtist(e.target.value)} /></label>
        <label>Track title<input value={title} maxLength={500} onChange={e => setTitle(e.target.value)} /></label>
        <button disabled={busy || !dirty}>Save correction</button>
      </form>
      <div className="ig-actions"><button disabled={busy || !media.artist || !media.title || !!media.wanted_matches_current || dirty} onClick={() => act('wishlist')}>{media.wanted_matches_current ? 'Added to wishlist' : 'Add to wishlist'}</button>
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
  const [postView, setPostView] = useState('grid')
  const videoRef = useRef(null)
  const audioRef = useRef(null)
  const postDetailRef = useRef(null)
  const [playbackError, setPlaybackError] = useState(false)
  const [playbackAttempt, setPlaybackAttempt] = useState(0)
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

  useEffect(() => { setPlaybackError(false) }, [media?.playback_version])

  function choosePost(id) {
    setSelectedPost(id)
    setSelectedMedia(null)
    if (postView === 'grid') postDetailRef.current?.scrollIntoView({ block: 'start' })
  }

  const retryPlayback = async () => {
    if (!media.playback_url || playbackError) await api.post(`/instagram/media/${media.id}/playback/`, {})
    await query.refetch()
    setPlaybackAttempt(attempt => attempt + 1)
    setPlaybackError(false)
  }

  return <section className="ig-workspace">
    <header className="ig-heading"><div><p className="ig-eyebrow">Capture / Instagram</p><h1>Track Downloads</h1><p>Your saved collection. Every carousel video, one at a time.</p></div><button onClick={() => query.refetch()} disabled={query.isFetching}>Refresh status</button></header>
    {query.isPending ? <p role="status">Loading collection…</p> : query.isError ? <div role="alert">Could not load Instagram workspace. <button onClick={() => query.refetch()}>Retry</button></div> : <>
      {query.data.fixture && <p className="ig-banner">Offline fixture · synthetic video and recognition results · this is not live Instagram QA.</p>}
      <div className="ig-summary"><span>Mac connector: <strong>{query.data.connector_status}</strong></span><span>{identified} / {videos.length} videos identified</span><span>{reviewed} / {videos.length} reviewed</span></div>
      {query.data.connector_status !== 'ready' && <p className="ig-banner" role="status">{({ offline: 'Mac is offline. Cached videos remain available until expiry; cached source recognition runs on the server. Uncached sources need an explicit Mac connector run.', 'session-expired': 'Instagram session expired. Reopen the existing Chrome session on Mac and rerun the connector.', restricted: 'Instagram restricted collection access. Check the existing Mac session before retrying.', 'rate-limited': 'Instagram rate limit reached. Wait before the next connector run.', error: 'Mac connector reported an error. Check its preflight and retry.' })[query.data.connector_status]}</p>}
      <progress className="ig-progress" aria-label="Videos reviewed" value={reviewed} max={videos.length || 1} />
      {!posts.length ? <div className="ig-empty"><h2>Your collection is ready to connect</h2><p>Run the Instagram connector on your Mac with its existing Chrome session. The first sync imports the full collection and every carousel child. Videos load only when requested.</p></div> : <>
        <div className="ig-controls">
        <div className="ig-filters" role="group" aria-label="Filter media">
          {filters.map(f => <button key={f} aria-pressed={filter === f} onClick={() => { setFilter(f); setSelectedMedia(null) }}>{f[0].toUpperCase()+f.slice(1)}</button>)}
        </div>
        <div className="ig-view-switch" role="group" aria-label="Post layout">
          <button aria-pressed={postView === 'list'} onClick={() => setPostView('list')}>List</button>
          <button aria-pressed={postView === 'grid'} onClick={() => setPostView('grid')}>Grid</button>
        </div>
      </div>
        {!post ? <p>No media match this filter.</p> : <div className={postView === 'grid' ? 'ig-grid-layout' : 'ig-columns'}>
          <nav className={postView === 'grid' ? 'ig-queue ig-grid' : 'ig-queue'} aria-label="Saved posts">
            {visiblePosts.map(p => {
              const postVideos = p.media.filter(m => m.kind === 'video')
              const cover = postView === 'grid' && p.id === post?.id && postVideos.find(m => m.playback_url)
              return <button key={p.id} aria-current={post.id === p.id ? 'true' : undefined} onClick={() => choosePost(p.id)}>
                {postView === 'grid' && <span className="ig-cover" aria-hidden="true">
                  {cover ? <video preload="metadata" muted playsInline src={`${cover.playback_url}#t=0.1`} /> : <span>Instagram</span>}
                </span>}
                <span className="ig-post-info">
                  <span className="ig-post-number">{String(posts.indexOf(p)+1).padStart(2, '0')}</span>
                  <span>
                    <strong>@{p.author || 'Instagram'}</strong>
                    <span className="ig-caption">{p.caption || 'Saved post'}</span>
                    <small>{postVideos.length} videos · {postVideos.filter(m => m.reviewed).length} reviewed</small>
                  </span>
                </span>
              </button>
            })}
          </nav>
          <article className="ig-focus" ref={postDetailRef}><div className="ig-source"><strong>@{post.author}</strong><a href={post.permalink} target="_blank" rel="noopener noreferrer">Open Instagram</a></div>
            {media && <>
              <div className="ig-player">{media.kind === 'image' && media.playback_url && !playbackError ? <img key={media.playback_version} src={media.playback_url} alt={`Carousel image ${media.position+1}`} onError={() => setPlaybackError(true)} /> : media.playback_url && !playbackError ? <video key={`${media.id}:${playbackAttempt}:${media.playback_version}`} ref={videoRef} data-playback-version={media.playback_version} controls preload="metadata" playsInline src={media.playback_url} onPlay={() => audioRef.current?.pause()} aria-label={`Post ${posts.indexOf(post)+1}, video ${media.position+1}`} onError={() => setPlaybackError(true)} /> : <div><p role="status">{playbackError ? 'Playback unavailable or cache expired.' : media.playback_requested ? 'Selected video requested. Waiting for the authorized Mac transport.' : media.playback_error ? 'Selected video could not be loaded.' : 'Load this selected video through the private cache.'}</p><button disabled={query.isFetching || media.playback_requested} onClick={() => retryPlayback().catch(() => setPlaybackError(true))}>{playbackError ? 'Retry playback' : media.playback_requested ? 'Video requested' : media.kind === 'image' ? 'Load image' : 'Load video'}</button></div>}</div>
              <div className="ig-child-heading">Carousel · {media.position+1} of {post.media.length}</div>
              <div className="ig-children" aria-label="Carousel children">{post.media.map(m => <button key={m.id} aria-label={`${m.kind} ${m.position+1}, ${m.status}${m.reviewed ? ', reviewed' : ''}`} aria-pressed={m.id === media.id} onClick={() => setSelectedMedia(m.id)}><span>{m.kind === 'video' ? 'Video' : 'Image'} {m.position+1}</span><small>{m.reviewed ? 'Reviewed' : m.kind === 'image' ? 'Image' : m.status}</small></button>)}</div>
              {mutation.isError && <p role="alert">{mutation.error.data ? JSON.stringify(mutation.error.data) : 'Could not save. Try again.'}</p>}
              <Editor key={media.id} media={media} act={act} busy={mutation.isPending} audioRef={audioRef} pauseVideo={() => videoRef.current?.pause()} />
            </>}
          </article>
        </div>}
      </>}
      {query.data.synced_at && <p className="ig-note">Last sync: {new Date(query.data.synced_at).toLocaleString()} · newest saved first</p>}
    </>}
  </section>
}
