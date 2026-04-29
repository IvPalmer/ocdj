import { useCallback, useEffect, useState } from 'react'

import './CratematePanel.css'

const API_BASE = (import.meta.env.VITE_API_BASE_URL || '/api').replace(/\/$/, '')

function CratematePanel() {
  const [selectedFile, setSelectedFile] = useState(null)
  const [previewUrl, setPreviewUrl] = useState(null)
  const [uploading, setUploading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [moduleStatus, setModuleStatus] = useState(null)
  const [isMobile, setIsMobile] = useState(false)
  const [isDragging, setIsDragging] = useState(false)

  // Detect mobile so the file input becomes a camera-capture button.
  useEffect(() => {
    const ua = navigator.userAgent || navigator.vendor || ''
    setIsMobile(/android|iphone|ipad|iPod|opera mini|iemobile|wpdesktop/i.test(ua.toLowerCase()))
  }, [])

  // Pull module status on mount so the panel can render a clear "not configured"
  // state when CRATEMATE_GEMINI_API_KEY isn't set yet (Phase 1 not run).
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

  const unconfigured = moduleStatus && moduleStatus.status !== 'operational'

  return (
    <div
      className="cratemate-panel"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      <header className="cratemate-header">
        <h1>Crate-Mate</h1>
        <p>Identify an album from its cover. Cropping helps — feed the cover, not the whole crate.</p>
      </header>

      {unconfigured && (
        <div className="cratemate-banner cratemate-banner--warn">
          <strong>Module unconfigured.</strong>{' '}
          {moduleStatus?.status === 'unreachable'
            ? 'Backend not reachable. Is the Django server running?'
            : 'Set CRATEMATE_GEMINI_API_KEY (and friends) in ~/.secrets/ocdj-cratemate.env, then restart. Phase 1 of the absorption plan rotates these credentials.'}
        </div>
      )}

      {isDragging && <div className="cratemate-drag-overlay">Drop the cover here</div>}

      {!result && (
        <section className="cratemate-upload">
          {!selectedFile && (
            <label htmlFor="cratemate-file" className="cratemate-file-label">
              <span className="cratemate-file-icon" aria-hidden="true">[ + ]</span>
              <span>{isMobile ? 'Take photo' : 'Choose file'}</span>
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
                <button type="button" className="btn btn-secondary" onClick={reset} disabled={uploading}>
                  Reset
                </button>
                <button type="button" className="btn btn-primary" onClick={handleUpload} disabled={uploading || unconfigured}>
                  {uploading ? 'Identifying…' : 'Identify'}
                </button>
              </div>
            </div>
          )}

          {error && <div className="cratemate-banner cratemate-banner--err">{error}</div>}
        </section>
      )}

      {result && (
        <section className="cratemate-result">
          {(result.album_image || result.album?.image) && (
            <img className="cratemate-cover" src={result.album_image || result.album.image} alt="Album cover" />
          )}
          <h2>
            {(result.artist_name || result.album?.artist || '?')} — {(result.album_name || result.album?.name || '?')}
          </h2>
          <dl className="cratemate-meta">
            {result.release_date && <><dt>Released</dt><dd>{result.release_date}</dd></>}
            {Array.isArray(result.genres) && result.genres.length > 0 && (
              <><dt>Genres</dt><dd>{result.genres.join(', ')}</dd></>
            )}
            {typeof result.confidence === 'number' && (
              <><dt>Confidence</dt><dd>{Math.round(result.confidence * 100)}%</dd></>
            )}
          </dl>

          <ul className="cratemate-links">
            {result.discogs_url && result.discogs_url !== 'unavailable' && (
              <li><a href={result.discogs_url} target="_blank" rel="noopener noreferrer">Discogs</a></li>
            )}
            {result.spotify_url && result.spotify_url !== 'unavailable' && (
              <li><a href={result.spotify_url} target="_blank" rel="noopener noreferrer">Spotify</a></li>
            )}
            {result.youtube_url && result.youtube_url !== 'unavailable' && (
              <li><a href={result.youtube_url} target="_blank" rel="noopener noreferrer">YouTube</a></li>
            )}
            {result.bandcamp_url && (
              <li><a href={result.bandcamp_url} target="_blank" rel="noopener noreferrer">Bandcamp</a></li>
            )}
          </ul>

          {result.tracks?.tracklist?.length > 0 && (
            <details className="cratemate-tracklist" open>
              <summary>Tracklist ({result.tracks.tracklist.length})</summary>
              <ol>
                {result.tracks.tracklist.map((t, i) => (
                  <li key={i}>
                    {t.position ? `${t.position}. ` : ''}{t.title || t.name}
                    {t.duration ? ` — ${t.duration}` : ''}
                  </li>
                ))}
              </ol>
            </details>
          )}

          <button type="button" className="btn btn-secondary" onClick={reset}>Identify another</button>
        </section>
      )}
    </div>
  )
}

export default CratematePanel
