import { useCallback, useEffect, useRef, useState } from 'react'

import './CratematePanel.css'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

// Process at most N identifies concurrently. Set to 1 because the Claude
// Max subscription's Opus quota gets eaten fast at higher concurrency
// (see backend logs — "out of extra usage" errors at 2+ parallel).
const CONCURRENCY = 1

let nextId = 1
const newId = () => `it-${nextId++}`

function extractIdentity(result) {
  if (!result) return { artist: '', album: '' }
  const artist = result.artist_name || result.album?.artist || ''
  const album = result.album_name || result.album?.name || ''
  return { artist, album }
}

function extractLinks(result) {
  if (!result) return {}
  return {
    discogs: result.discogs_url || result.links?.discogs,
    spotify: result.spotify_url || result.links?.spotify,
    youtube: result.youtube_url || result.links?.youtube,
    bandcamp: result.bandcamp_url || result.links?.bandcamp,
  }
}

// Prefer the youtube_tracks array (direct YouTube video links per track)
// over plain tracklist (titles only). The user explicitly wants per-track
// direct YouTube links — search-page fallbacks are noise.
function extractTracks(result) {
  if (!result) return []
  const yt = result.tracks?.youtube_tracks
  if (Array.isArray(yt) && yt.length > 0) return yt
  const tl = result.tracks?.tracklist
  if (Array.isArray(tl) && tl.length > 0) return tl
  return []
}

// One row in the upload queue / results list.
function makeItem(file) {
  return {
    id: newId(),
    file,
    name: file.name,
    previewUrl: URL.createObjectURL(file),
    status: 'pending',     // 'pending' | 'uploading' | 'done' | 'error'
    result: null,
    error: null,
  }
}

function CratematePanel() {
  // Items queue: each item is an image to identify + its result. Single-
  // image flow is just "queue of length 1" so the UX is uniform.
  const [items, setItems] = useState([])
  const [moduleStatus, setModuleStatus] = useState(null)
  const [isMobile, setIsMobile] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [manualMode, setManualMode] = useState(false)
  const [manualArtist, setManualArtist] = useState('')
  const [manualAlbum, setManualAlbum] = useState('')
  const [manualLoading, setManualLoading] = useState(false)
  const [manualError, setManualError] = useState(null)
  const [manualResult, setManualResult] = useState(null)
  const cameraInputRef = useRef(null)
  const libraryInputRef = useRef(null)

  useEffect(() => {
    const ua = navigator.userAgent || navigator.vendor || ''
    setIsMobile(/android|iphone|ipad|iPod|opera mini|iemobile|wpdesktop/i.test(ua.toLowerCase()))
  }, [])

  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/cratemate/status/`)
      .then((r) => r.json())
      .then((data) => { if (!cancelled) setModuleStatus(data) })
      .catch(() => { if (!cancelled) setModuleStatus({ status: 'unreachable' }) })
    return () => { cancelled = true }
  }, [])

  // Cleanup object URLs on unmount.
  useEffect(() => {
    return () => { items.forEach((it) => it.previewUrl && URL.revokeObjectURL(it.previewUrl)) }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const unconfigured = moduleStatus && moduleStatus.status !== 'operational'

  const enqueue = useCallback((files) => {
    const valid = Array.from(files).filter((f) => f && f.type.startsWith('image/'))
    if (valid.length === 0) return
    setItems((prev) => [...prev, ...valid.map(makeItem)])
  }, [])

  // Queue runner — ref-guarded so the effect doesn't keep spawning new
  // runners on every items[] mutation. The earlier version (effect dep on
  // items + closure-captured `cancelled` flag) deadlocked: marking an
  // item 'uploading' mutated items, which triggered cleanup that set
  // cancelled=true on the in-flight fetch's closure. When the fetch
  // resolved, the `if (cancelled) return` early-out left the item stuck
  // in 'uploading' forever, blocking the rest of the queue. Per codex
  // review — real bug at scale.
  //
  // New design: one persistent runner started on mount; it walks the
  // queue using the latest items via the itemsRef. Effect just pokes it
  // whenever there might be more pending work.
  const itemsRef = useRef(items)
  useEffect(() => { itemsRef.current = items }, [items])
  const runnerActive = useRef(false)
  const unmountedRef = useRef(false)
  useEffect(() => () => { unmountedRef.current = true }, [])

  const tickQueue = useCallback(() => {
    if (runnerActive.current) return
    runnerActive.current = true
    ;(async () => {
      while (!unmountedRef.current) {
        const inflight = itemsRef.current.filter((it) => it.status === 'uploading').length
        if (inflight >= CONCURRENCY) break
        const next = itemsRef.current.find((it) => it.status === 'pending')
        if (!next) break
        const id = next.id
        // Mark uploading. Use functional update so we don't race with concurrent setItems.
        setItems((prev) => prev.map((it) => it.id === id ? { ...it, status: 'uploading' } : it))
        try {
          const formData = new FormData()
          formData.append('image', next.file)
          const res = await fetch(`${API_BASE}/cratemate/identify/`, {
            method: 'POST',
            body: formData,
          })
          const data = await res.json().catch(() => ({}))
          if (unmountedRef.current) break
          if (!res.ok) {
            const msg = data.error || data.detail || `HTTP ${res.status}`
            setItems((prev) => prev.map((it) => it.id === id ? { ...it, status: 'error', error: msg } : it))
          } else {
            setItems((prev) => prev.map((it) => it.id === id ? { ...it, status: 'done', result: data } : it))
          }
        } catch (err) {
          if (unmountedRef.current) break
          setItems((prev) => prev.map((it) => it.id === id ? { ...it, status: 'error', error: err.message || 'Network error' } : it))
        }
      }
      runnerActive.current = false
    })()
  }, [])

  // Whenever items changes (especially: a new pending item was added),
  // poke the runner. It's a no-op when already running.
  useEffect(() => { tickQueue() }, [items, tickQueue])

  const removeItem = (id) => {
    setItems((prev) => {
      const target = prev.find((it) => it.id === id)
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
      return prev.filter((it) => it.id !== id)
    })
  }

  const clearAll = () => {
    items.forEach((it) => it.previewUrl && URL.revokeObjectURL(it.previewUrl))
    setItems([])
    setManualMode(false)
    setManualResult(null)
  }

  const handleCameraChange = (e) => { enqueue(e.target.files); e.target.value = '' }
  const handleLibraryChange = (e) => { enqueue(e.target.files); e.target.value = '' }

  const handleDrop = useCallback((e) => {
    e.preventDefault()
    setIsDragging(false)
    if (e.dataTransfer?.files?.length) enqueue(e.dataTransfer.files)
  }, [enqueue])
  const handleDragOver = useCallback((e) => { e.preventDefault(); setIsDragging(true) }, [])
  const handleDragLeave = useCallback((e) => { e.preventDefault(); setIsDragging(false) }, [])

  const handleManualLookup = async (e) => {
    e?.preventDefault?.()
    if (!manualArtist.trim() || !manualAlbum.trim()) return
    setManualLoading(true)
    setManualError(null)
    try {
      const res = await fetch(`${API_BASE}/cratemate/lookup/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artist: manualArtist.trim(), album: manualAlbum.trim() }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setManualError(data.error || data.detail || `HTTP ${res.status}`)
      } else {
        setManualResult(data)
      }
    } catch (err) {
      setManualError(err.message || 'Network error')
    } finally {
      setManualLoading(false)
    }
  }

  const totalDone = items.filter((it) => it.status === 'done').length
  const totalErr = items.filter((it) => it.status === 'error').length
  const totalQueued = items.filter((it) => it.status === 'pending').length
  const totalActive = items.filter((it) => it.status === 'uploading').length
  const hasItems = items.length > 0

  return (
    <div
      className="cratemate-panel"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <header className="cratemate-header">
        <h1>Crate-Mate</h1>
        <p>
          Snap covers (one or many). Get the artist, album, Discogs link, and
          a clickable tracklist with direct YouTube videos.
        </p>
      </header>

      {unconfigured && (
        <div className="cratemate-banner cratemate-banner--warn">
          <strong>Module unconfigured.</strong>{' '}
          {moduleStatus?.status === 'unreachable'
            ? 'Backend not reachable.'
            : 'Set CLAUDE_CODE_OAUTH_TOKEN in the backend env, then restart.'}
        </div>
      )}

      {isDragging && <div className="cratemate-drag-overlay">Drop covers here</div>}

      {/* Upload zone — always visible so you can keep adding to the queue. */}
      <section className="cratemate-upload-zone">
        <div className="cratemate-pickers">
          {isMobile && (
            <button
              type="button"
              className="cratemate-picker cratemate-picker--camera"
              onClick={() => cameraInputRef.current?.click()}
              disabled={unconfigured}
            >
              <span className="cratemate-picker__icon" aria-hidden="true">◉</span>
              <span className="cratemate-picker__title">Take photo</span>
              <span className="cratemate-picker__hint">Open camera</span>
            </button>
          )}
          <button
            type="button"
            className="cratemate-picker cratemate-picker--library"
            onClick={() => libraryInputRef.current?.click()}
            disabled={unconfigured}
          >
            <span className="cratemate-picker__icon" aria-hidden="true">◎</span>
            <span className="cratemate-picker__title">
              {isMobile ? 'From library' : 'Choose files'}
            </span>
            <span className="cratemate-picker__hint">
              {isMobile ? 'Pick one or many' : 'or drop them here'}
            </span>
          </button>
        </div>

        {/* Hidden inputs — camera = single, library = multiple. */}
        <input
          ref={cameraInputRef}
          type="file"
          accept="image/*"
          capture="environment"
          onChange={handleCameraChange}
          style={{ display: 'none' }}
        />
        <input
          ref={libraryInputRef}
          type="file"
          accept="image/*"
          multiple
          onChange={handleLibraryChange}
          style={{ display: 'none' }}
        />

        {!hasItems && !manualMode && (
          <button
            type="button"
            className="cratemate-manual-link"
            onClick={() => { setManualMode(true); setManualResult(null); setManualError(null) }}
          >
            Or type the artist and album manually →
          </button>
        )}

        {hasItems && (
          <div className="cratemate-queue-status">
            <span>
              {totalDone}/{items.length} identified
              {totalActive > 0 ? ` · ${totalActive} working` : ''}
              {totalQueued > 0 ? ` · ${totalQueued} queued` : ''}
              {totalErr > 0 ? ` · ${totalErr} error` : ''}
            </span>
            <button type="button" className="cratemate-link-btn" onClick={clearAll}>
              Clear all
            </button>
          </div>
        )}
      </section>

      {/* Manual lookup form (always offered as alternative). */}
      {manualMode && !manualResult && (
        <section className="cratemate-manual-section">
          <form className="cratemate-manual" onSubmit={handleManualLookup}>
            <label className="cratemate-field">
              <span>Artist</span>
              <input
                type="text"
                value={manualArtist}
                onChange={(e) => setManualArtist(e.target.value)}
                placeholder="Theo Parrish"
                autoFocus
              />
            </label>
            <label className="cratemate-field">
              <span>Album</span>
              <input
                type="text"
                value={manualAlbum}
                onChange={(e) => setManualAlbum(e.target.value)}
                placeholder="Parallel Dimensions"
              />
            </label>
            <div className="cratemate-actions">
              <button
                type="button"
                className="btn"
                onClick={() => { setManualMode(false); setManualArtist(''); setManualAlbum('') }}
                disabled={manualLoading}
              >
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={manualLoading || !manualArtist.trim() || !manualAlbum.trim()}
              >
                {manualLoading ? 'Looking up…' : 'Look up'}
              </button>
            </div>
          </form>
          {manualError && <div className="cratemate-banner cratemate-banner--err">{manualError}</div>}
        </section>
      )}

      {manualResult && (
        <ResultCard
          result={manualResult}
          previewUrl={null}
          onDismiss={() => { setManualResult(null); setManualMode(false); setManualArtist(''); setManualAlbum('') }}
        />
      )}

      {/* Stacked result cards for the queue. */}
      <section className="cratemate-results">
        {items.map((it) => (
          <QueueItem
            key={it.id}
            item={it}
            onRemove={() => removeItem(it.id)}
            onManualEditFromVisionOnly={(artist, album) => {
              setManualArtist(artist || '')
              setManualAlbum(album || '')
              setManualMode(true)
              setManualResult(null)
            }}
          />
        ))}
      </section>
    </div>
  )
}


function QueueItem({ item, onRemove, onManualEditFromVisionOnly }) {
  if (item.status === 'pending' || item.status === 'uploading') {
    return (
      <article className="cratemate-card cratemate-card--working">
        <img className="cratemate-card__thumb" src={item.previewUrl} alt={item.name} />
        <div className="cratemate-card__body">
          <div className="cratemate-card__title">{item.name}</div>
          <div className="cratemate-card__status">
            {item.status === 'pending' ? 'Queued…' : 'Identifying…'}
            <span className="cratemate-spinner" />
          </div>
        </div>
        <button type="button" className="cratemate-card__remove" onClick={onRemove} aria-label="Remove">
          ×
        </button>
      </article>
    )
  }
  if (item.status === 'error') {
    return (
      <article className="cratemate-card cratemate-card--error">
        <img className="cratemate-card__thumb" src={item.previewUrl} alt={item.name} />
        <div className="cratemate-card__body">
          <div className="cratemate-card__title">{item.name}</div>
          <div className="cratemate-card__status">{item.error}</div>
        </div>
        <button type="button" className="cratemate-card__remove" onClick={onRemove} aria-label="Remove">×</button>
      </article>
    )
  }
  return (
    <ResultCard
      result={item.result}
      previewUrl={item.previewUrl}
      onDismiss={onRemove}
      onManualEditFromVisionOnly={onManualEditFromVisionOnly}
    />
  )
}


// Format a Discogs price + currency for compact display.
function fmtPrice(value, currency) {
  if (value == null) return null
  const sym = { USD: '$', EUR: '€', GBP: '£', JPY: '¥', BRL: 'R$' }[currency] || ''
  return `${sym}${Number(value).toFixed(2)}${sym ? '' : ` ${currency || ''}`}`.trim()
}

// Single result card — used both for queue items and the manual-lookup result.
function ResultCard({ result, previewUrl, onDismiss, onManualEditFromVisionOnly }) {
  const { artist, album } = extractIdentity(result)
  const recognized = result && (artist || album) && !result.error
  const links = extractLinks(result)
  const tracks = extractTracks(result)
  const coverImage = result?.album?.image || result?.album_image
  const confidence = result?.identification?.confidence ?? result?.confidence
  const visionOnly = !!result?.vision_only
  const visibleText = result?.vision_visible_text
  const evidence = result?.vision_evidence

  // Discogs-detail extras for the "more info" strip.
  const releaseYear = result?.album?.release_date
  const releaseLabel = result?.album?.label
  const releaseCountry = result?.album?.country
  const numForSale = result?.num_for_sale ?? result?.release_overview?.num_for_sale
  const lowestPrice = fmtPrice(
    result?.lowest_price ?? result?.release_overview?.lowest_price,
    result?.price_currency ?? result?.release_overview?.currency
  )
  const medianPrice = fmtPrice(result?.median_price, result?.price_currency)
  const coverMismatch = !!result?.cover_mismatch_warning

  if (!recognized) {
    // Couldn't identify — surface evidence + retry affordances.
    return (
      <article className="cratemate-card cratemate-card--miss">
        {previewUrl && <img className="cratemate-card__thumb" src={previewUrl} alt="Uploaded cover" />}
        <div className="cratemate-card__body">
          <h3>Couldn't identify this cover.</h3>
          <p className="cratemate-card__sub">
            {result?.error
              ? result.error
              : 'Use the text below to refine a manual search, or try a tighter crop.'}
          </p>
          {(visibleText || evidence) && (
            <div className="cratemate-evidence">
              {visibleText && (
                <>
                  <span className="cratemate-evidence__label">Text on cover</span>
                  <span className="cratemate-evidence__value">{visibleText}</span>
                </>
              )}
              {evidence && (
                <>
                  <span className="cratemate-evidence__label">What I saw</span>
                  <span className="cratemate-evidence__value">{evidence}</span>
                </>
              )}
            </div>
          )}
          <div className="cratemate-actions">
            {visibleText && (
              <a
                className="btn"
                href={`https://www.discogs.com/search/?q=${encodeURIComponent(visibleText.replace(/\s*\|\s*/g, ' '))}&type=release`}
                target="_blank"
                rel="noopener noreferrer"
              >
                Search Discogs
              </a>
            )}
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => onManualEditFromVisionOnly?.('', '')}
            >
              Enter manually
            </button>
          </div>
        </div>
        <button type="button" className="cratemate-card__remove" onClick={onDismiss} aria-label="Dismiss">×</button>
      </article>
    )
  }

  return (
    <article className={`cratemate-card cratemate-card--result${visionOnly ? ' cratemate-card--vision-only' : ''}`}>
      {(coverImage || previewUrl) && (
        <img className="cratemate-card__thumb" src={coverImage || previewUrl} alt={`${artist} — ${album}`} />
      )}
      <div className="cratemate-card__body">
        <div className="cratemate-identity">
          <span className="cratemate-identity__artist">{artist || '—'}</span>
          <h3 className="cratemate-identity__album">{album || '—'}</h3>
          {typeof confidence === 'number' && (
            <span className="cratemate-confidence">
              {Math.round(confidence * 100)}% confident{visionOnly ? ' · vision only' : ''}
            </span>
          )}
        </div>

        {visionOnly && (
          <div className="cratemate-banner cratemate-banner--warn">
            {result.warning || 'No Discogs match — verify before adding to wantlist.'}
          </div>
        )}

        {coverMismatch && !visionOnly && (
          <div className="cratemate-banner cratemate-banner--info">
            Discogs cover doesn't visually match — likely a different pressing of the same release. Verify before trusting the link.
          </div>
        )}

        {/* Discogs detail strip — year, label, country, market info. */}
        {(releaseYear || releaseLabel || releaseCountry || numForSale != null || lowestPrice) && (
          <dl className="cratemate-meta-strip">
            {releaseYear && (<><dt>Year</dt><dd>{releaseYear}</dd></>)}
            {releaseLabel && (<><dt>Label</dt><dd>{releaseLabel}</dd></>)}
            {releaseCountry && (<><dt>Country</dt><dd>{releaseCountry}</dd></>)}
            {numForSale != null && (
              <>
                <dt>For sale</dt>
                <dd>
                  {numForSale} on Discogs
                  {lowestPrice ? ` · from ${lowestPrice}` : ''}
                </dd>
              </>
            )}
            {medianPrice && !lowestPrice && (<><dt>Median</dt><dd>{medianPrice}</dd></>)}
          </dl>
        )}

        {/* Platform links — Discogs/Spotify/YouTube/Bandcamp pills. */}
        <ul className="cratemate-links">
          {links.discogs && links.discogs !== 'unavailable' && (
            <li><a href={links.discogs} target="_blank" rel="noopener noreferrer">Discogs</a></li>
          )}
          {links.spotify && links.spotify !== 'unavailable' && (
            <li><a href={links.spotify} target="_blank" rel="noopener noreferrer">Spotify</a></li>
          )}
          {links.youtube && links.youtube !== 'unavailable' && (
            <li><a href={links.youtube} target="_blank" rel="noopener noreferrer">YouTube</a></li>
          )}
          {links.bandcamp && (
            <li><a href={links.bandcamp} target="_blank" rel="noopener noreferrer">Bandcamp</a></li>
          )}
        </ul>

        {/* Tracklist — each row is a clickable link to the direct YouTube
            video for that track when available. This is the headline
            feature: tap a track, hear it. Search-page fallbacks render as
            a smaller "search" link so they're visually distinct. */}
        {tracks.length > 0 && (
          <Tracklist tracks={tracks} />
        )}
      </div>
      <button type="button" className="cratemate-card__remove" onClick={onDismiss} aria-label="Dismiss">×</button>
    </article>
  )
}


function Tracklist({ tracks }) {
  return (
    <details className="cratemate-tracklist" open>
      <summary>Tracklist · {tracks.length}</summary>
      <ol>
        {tracks.map((t, i) => {
          const yt = t.youtube
          const isDirect = yt?.url && yt.is_search === false
          const isSearch = yt?.url && yt.is_search === true
          const url = yt?.url
          const titleNode = (
            <>
              {t.position && <span className="cratemate-track-pos">{t.position}</span>}
              <span className="cratemate-track-title">{t.title || t.name}</span>
              {t.duration && <span className="cratemate-track-duration">{t.duration}</span>}
            </>
          )
          return (
            <li key={i} className={`cratemate-track${isDirect ? ' cratemate-track--direct' : isSearch ? ' cratemate-track--search' : ''}`}>
              {url ? (
                <a href={url} target="_blank" rel="noopener noreferrer" className="cratemate-track-link">
                  {titleNode}
                  <span className="cratemate-track-badge" aria-hidden="true">
                    {isDirect ? '▶' : '⌕'}
                  </span>
                </a>
              ) : (
                <div className="cratemate-track-row">{titleNode}</div>
              )}
            </li>
          )
        })}
      </ol>
    </details>
  )
}

export default CratematePanel
