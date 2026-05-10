import { useCallback, useEffect, useState } from 'react'

import './CratematePanel.css'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

// A result counts as "recognized" only when there's both an artist and an
// album string. Without this gate the UI rendered "? — ?" on failed IDs
// (the bug the user hit on mobile). The backend already returns evidence
// text in those cases; we surface it explicitly below.
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

function CratematePanel() {
  const [selectedFile, setSelectedFile] = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [moduleStatus, setModuleStatus] = useState(null)
  const [isMobile, setIsMobile] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [manualMode, setManualMode] = useState(false)
  const [manualArtist, setManualArtist] = useState('')
  const [manualAlbum, setManualAlbum] = useState('')

  // Detect mobile so the file input becomes a camera-capture button.
  useEffect(() => {
    const ua = navigator.userAgent || navigator.vendor || ''
    setIsMobile(/android|iphone|ipad|iPod|opera mini|iemobile|wpdesktop/i.test(ua.toLowerCase()))
  }, [])

  // Pull module status on mount so the panel can render a clear "not configured"
  // state when CLAUDE_CODE_OAUTH_TOKEN isn't set yet.
  useEffect(() => {
    let cancelled = false
    fetch(`${API_BASE}/cratemate/status/`)
      .then((r) => r.json())
      .then((data) => { if (!cancelled) setModuleStatus(data) })
      .catch(() => { if (!cancelled) setModuleStatus({ status: 'unreachable' }) })
    return () => { cancelled = true }
  }, [])

  // Clean up object URL when preview changes / unmounts.
  useEffect(() => {
    return () => { if (previewUrl) URL.revokeObjectURL(previewUrl) }
  }, [previewUrl])

  const reset = () => {
    setSelectedFile(null)
    setPreviewUrl(null)
    setResult(null)
    setError(null)
    setManualMode(false)
    setManualArtist('')
    setManualAlbum('')
  }

  const processFile = (file) => {
    if (!file || !file.type.startsWith('image/')) {
      setError('Please pick an image file.')
      return
    }
    setSelectedFile(file)
    setPreviewUrl(URL.createObjectURL(file))
    setError(null)
    setResult(null)
  }

  const handleFileChange = (e) => {
    const file = e.target.files?.[0]
    if (file) processFile(file)
  }

  const handleDrop = useCallback((e) => {
    e.preventDefault()
    setIsDragging(false)
    const file = e.dataTransfer?.files?.[0]
    if (file) processFile(file)
  }, [])

  const handleDragOver = useCallback((e) => { e.preventDefault(); setIsDragging(true) }, [])
  const handleDragLeave = useCallback((e) => { e.preventDefault(); setIsDragging(false) }, [])

  const handleUpload = async () => {
    if (!selectedFile) return
    setUploading(true)
    setError(null)
    setResult(null)

    const formData = new FormData()
    formData.append('image', selectedFile)

    try {
      const res = await fetch(`${API_BASE}/cratemate/identify/`, {
        method: 'POST',
        body: formData,
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError(data.error || data.detail || `Identify failed: HTTP ${res.status}`)
      } else {
        setResult(data)
      }
    } catch (err) {
      setError(err.message || 'Network error.')
    } finally {
      setUploading(false)
    }
  }

  // Manual fallback: hits /api/cratemate/lookup/ with artist+album.
  const handleManualLookup = async (e) => {
    e?.preventDefault?.()
    if (!manualArtist.trim() || !manualAlbum.trim()) return
    setUploading(true)
    setError(null)
    try {
      const res = await fetch(`${API_BASE}/cratemate/lookup/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artist: manualArtist.trim(), album: manualAlbum.trim() }),
      })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setError(data.error || data.detail || `Lookup failed: HTTP ${res.status}`)
      } else {
        setResult(data)
      }
    } catch (err) {
      setError(err.message || 'Network error.')
    } finally {
      setUploading(false)
    }
  }

  const unconfigured = moduleStatus && moduleStatus.status !== 'operational'

  // Recognition can succeed at the HTTP level but fail at the identification
  // level — e.g. Claude returned no artist/album, or the backend's
  // _select_best_match couldn't reach the 0.3 confidence threshold. Detect
  // that state so we can show a useful "couldn't identify" UI instead of the
  // old "? — ?" placeholder.
  const { artist: identifiedArtist, album: identifiedAlbum } = extractIdentity(result)
  const recognized = result && (identifiedArtist || identifiedAlbum) && !result.error
  const links = extractLinks(result)
  const tracklist = result?.tracks?.tracklist || []
  const coverImage = result?.album?.image || result?.album_image
  const confidence = result?.identification?.confidence ?? result?.confidence

  return (
    <div
      className="cratemate-panel"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <header className="cratemate-header">
        <h1>Crate-Mate</h1>
        <p>Snap a cover. Get the artist, album, and every link in one place.</p>
      </header>

      {unconfigured && (
        <div className="cratemate-banner cratemate-banner--warn">
          <strong>Module unconfigured.</strong>{' '}
          {moduleStatus?.status === 'unreachable'
            ? 'Backend not reachable. Is the Django server running?'
            : 'Set CLAUDE_CODE_OAUTH_TOKEN in the backend env, then restart.'}
        </div>
      )}

      {isDragging && <div className="cratemate-drag-overlay">Drop the cover here</div>}

      {!result && !manualMode && (
        <section className="cratemate-upload">
          {!selectedFile && (
            <label htmlFor="cratemate-file" className="cratemate-dropzone">
              <span className="cratemate-dropzone__icon" aria-hidden="true">◎</span>
              <span className="cratemate-dropzone__title">
                {isMobile ? 'Take photo' : 'Choose a cover'}
              </span>
              <span className="cratemate-dropzone__hint">
                {isMobile ? 'Or pick from camera roll' : 'or drop a file here'}
              </span>
              <input
                id="cratemate-file"
                type="file"
                accept="image/*"
                capture={isMobile ? 'environment' : undefined}
                onChange={handleFileChange}
                style={{ display: 'none' }}
              />
            </label>
          )}

          {previewUrl && (
            <div className="cratemate-preview">
              <img src={previewUrl} alt="Selected cover preview" />
              <div className="cratemate-actions">
                <button type="button" className="btn" onClick={reset} disabled={uploading}>
                  Reset
                </button>
                <button type="button" className="btn btn-primary" onClick={handleUpload} disabled={uploading || unconfigured}>
                  {uploading ? 'Identifying…' : 'Identify'}
                </button>
              </div>
            </div>
          )}

          {!selectedFile && (
            <button
              type="button"
              className="cratemate-manual-link"
              onClick={() => setManualMode(true)}
            >
              No photo? Type the artist and album instead →
            </button>
          )}

          {error && <div className="cratemate-banner cratemate-banner--err">{error}</div>}
        </section>
      )}

      {/* Manual lookup form — alternative to image upload, or rescue path
          when Claude can't ID the cover (offered from the not-recognized UI). */}
      {!result && manualMode && (
        <section className="cratemate-upload">
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
              <button type="button" className="btn" onClick={reset} disabled={uploading}>
                Cancel
              </button>
              <button
                type="submit"
                className="btn btn-primary"
                disabled={uploading || !manualArtist.trim() || !manualAlbum.trim()}
              >
                {uploading ? 'Looking up…' : 'Look up'}
              </button>
            </div>
          </form>
          {error && <div className="cratemate-banner cratemate-banner--err">{error}</div>}
        </section>
      )}

      {/* Not-recognized result — replaces the old "? — ?" UI. Shows the
          model's evidence (text it OCR'd + visual description) so the user
          can see what was read and try a manual lookup or Discogs search
          with the salvageable signal. */}
      {result && !recognized && (
        <section className="cratemate-result cratemate-result--miss">
          <div className="cratemate-miss">
            <h2>Couldn’t identify this cover.</h2>
            <p>
              {result.error
                ? result.error
                : 'The vision model didn’t recognize the artwork. Use the text it read below to refine a manual search, or try a tighter crop / better lighting.'}
            </p>
            {previewUrl && (
              <img className="cratemate-cover cratemate-cover--small" src={previewUrl} alt="The image you uploaded" />
            )}
            {(result.vision_visible_text || result.vision_evidence) && (
              <div className="cratemate-evidence">
                {result.vision_visible_text && (
                  <>
                    <span className="cratemate-evidence__label">Text on cover</span>
                    <span className="cratemate-evidence__value">{result.vision_visible_text}</span>
                  </>
                )}
                {result.vision_evidence && (
                  <>
                    <span className="cratemate-evidence__label">What I saw</span>
                    <span className="cratemate-evidence__value">{result.vision_evidence}</span>
                  </>
                )}
              </div>
            )}
            <div className="cratemate-actions cratemate-actions--center">
              <button type="button" className="btn" onClick={reset}>
                Try another photo
              </button>
              {result.vision_visible_text && (
                <a
                  className="btn"
                  href={`https://www.discogs.com/search/?q=${encodeURIComponent(result.vision_visible_text.replace(/\s*\|\s*/g, ' '))}&type=release`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Search Discogs with this text
                </a>
              )}
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => { setResult(null); setManualMode(true) }}
              >
                Enter manually
              </button>
            </div>
          </div>
        </section>
      )}

      {/* Vision-only result — Claude got an artist/album but Discogs found
          no match. Show the guess + the model's evidence + a one-click
          "search Discogs / look up manually" so the user can verify. */}
      {result && recognized && result.vision_only && (
        <section className="cratemate-result">
          {previewUrl && (
            <img className="cratemate-cover" src={previewUrl} alt="The cover you uploaded" />
          )}
          <div className="cratemate-banner cratemate-banner--warn" style={{ width: '100%' }}>
            {result.warning || 'Vision identified this cover but Discogs returned no match.'}
          </div>
          <div className="cratemate-identity">
            {identifiedArtist && (
              <span className="cratemate-identity__artist">{identifiedArtist}</span>
            )}
            <h2 className="cratemate-identity__album">{identifiedAlbum || '—'}</h2>
            {typeof confidence === 'number' && (
              <span className="cratemate-confidence">
                {Math.round(confidence * 100)}% confident · vision only
              </span>
            )}
          </div>
          {(result.vision_visible_text || result.vision_evidence) && (
            <div className="cratemate-evidence">
              {result.vision_visible_text && (
                <>
                  <span className="cratemate-evidence__label">Text on cover</span>
                  <span className="cratemate-evidence__value">{result.vision_visible_text}</span>
                </>
              )}
              {result.vision_evidence && (
                <>
                  <span className="cratemate-evidence__label">What I saw</span>
                  <span className="cratemate-evidence__value">{result.vision_evidence}</span>
                </>
              )}
            </div>
          )}
          <div className="cratemate-actions cratemate-actions--center">
            <button
              type="button"
              className="btn"
              onClick={() => {
                // Pre-fill manual lookup with what Claude saw, let user edit + retry.
                setManualArtist(identifiedArtist || '')
                setManualAlbum(identifiedAlbum || '')
                setResult(null)
                setManualMode(true)
              }}
            >
              Edit + retry lookup
            </button>
            <a
              className="btn btn-primary"
              href={`https://www.discogs.com/search/?q=${encodeURIComponent(`${identifiedArtist || ''} ${identifiedAlbum || ''}`.trim())}&type=release`}
              target="_blank"
              rel="noopener noreferrer"
            >
              Search Discogs
            </a>
          </div>
          <button type="button" className="cratemate-reset btn" onClick={reset}>
            Identify another
          </button>
        </section>
      )}

      {result && recognized && !result.vision_only && (
        <section className="cratemate-result">
          {coverImage && (
            <img className="cratemate-cover" src={coverImage} alt={`Cover art for ${identifiedAlbum}`} />
          )}
          <div className="cratemate-identity">
            <span className="cratemate-identity__artist">{identifiedArtist || '—'}</span>
            <h2 className="cratemate-identity__album">{identifiedAlbum || '—'}</h2>
            {typeof confidence === 'number' && (
              <span className="cratemate-confidence">
                {Math.round(confidence * 100)}% confident
              </span>
            )}
          </div>

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

          {tracklist.length > 0 && (
            <details className="cratemate-tracklist">
              <summary>Tracklist · {tracklist.length}</summary>
              <ol>
                {tracklist.map((t, i) => (
                  <li key={i}>
                    <span className="cratemate-track-title">
                      {t.position ? `${t.position} · ` : ''}{t.title || t.name}
                    </span>
                    {t.duration && <span className="cratemate-track-duration">{t.duration}</span>}
                  </li>
                ))}
              </ol>
            </details>
          )}

          <button type="button" className="btn cratemate-reset" onClick={reset}>
            Identify another
          </button>
        </section>
      )}
    </div>
  )
}

export default CratematePanel
