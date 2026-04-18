import { useState } from 'react'
import {
  useLibraryTracks, useLibraryStats, useScanLibrary, useUpdateLibraryTrack, usePromoteTrack,
} from '../../api/hooks'
import './LibraryPanel.css'

const FORMATS = ['', 'mp3', 'flac', 'aiff', 'wav', 'ogg', 'm4a']

const EDITABLE_FIELDS = [
  { key: 'artist', label: 'Artist' },
  { key: 'title', label: 'Title' },
  { key: 'album', label: 'Album' },
  { key: 'label', label: 'Label' },
  { key: 'catalog_number', label: 'Catalog #' },
  { key: 'genre', label: 'Genre' },
  { key: 'year', label: 'Year' },
]

function formatDuration(seconds) {
  if (!seconds) return '--:--'
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function formatSize(bytes) {
  if (!bytes) return '--'
  if (bytes >= 1073741824) return `${(bytes / 1073741824).toFixed(1)} GB`
  if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(0)} KB`
}

function StatsBar({ stats }) {
  if (!stats) return null
  return (
    <div className="library-stats">
      <div className="library-stats__item">
        <span className="library-stats__value">{stats.total_tracks}</span>
        <span className="library-stats__label">Tracks</span>
      </div>
      {Object.entries(stats.by_format || {}).map(([fmt, count]) => (
        <div key={fmt} className="library-stats__item">
          <span className="library-stats__value">{count}</span>
          <span className="library-stats__label">{fmt.toUpperCase()}</span>
        </div>
      ))}
      <div className="library-stats__item">
        <span className="library-stats__value">{formatSize(stats.total_size_bytes)}</span>
        <span className="library-stats__label">Total Size</span>
      </div>
    </div>
  )
}

function EditModal({ track, onClose }) {
  const [form, setForm] = useState(() => {
    const init = {}
    EDITABLE_FIELDS.forEach(f => { init[f.key] = track[f.key] || '' })
    return init
  })
  const updateTrack = useUpdateLibraryTrack()

  const handleSave = () => {
    updateTrack.mutate({ id: track.id, ...form }, {
      onSuccess: () => onClose(),
    })
  }

  const set = (key, val) => setForm(prev => ({ ...prev, [key]: val }))

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal edit-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Edit Track Metadata</h3>
          <button className="btn-close" onClick={onClose} />
        </div>
        <div className="edit-modal__body">
          <div className="edit-modal__filename">{track.filename}</div>
          <div className="edit-modal__fields">
            {EDITABLE_FIELDS.map(f => (
              <div key={f.key} className="form-group">
                <label>{f.label}</label>
                <input
                  value={form[f.key]}
                  onChange={e => set(f.key, e.target.value)}
                  placeholder={f.label}
                />
              </div>
            ))}
          </div>
        </div>
        <div className="edit-modal__footer">
          <button className="btn btn-sm" onClick={onClose}>Cancel</button>
          <button
            className="btn btn-sm btn-accent"
            onClick={handleSave}
            disabled={updateTrack.isPending}
          >
            {updateTrack.isPending ? 'Saving...' : 'Save & Write Tags'}
          </button>
        </div>
      </div>
    </div>
  )
}

function PromoteButton({ track }) {
  const promote = usePromoteTrack()
  const [msg, setMsg] = useState('')
  const handle = async () => {
    try {
      const res = await promote.mutateAsync(track.id)
      if (res.skipped?.some(s => s.includes('already_exists'))) {
        setMsg('Already in review')
      } else if (res.review_path) {
        setMsg('Sent to review')
      } else {
        setMsg('Done')
      }
      setTimeout(() => setMsg(''), 3000)
    } catch (e) {
      setMsg('Failed')
      setTimeout(() => setMsg(''), 3000)
    }
  }
  return (
    <>
      <button
        className="btn btn-xs"
        onClick={handle}
        disabled={promote.isPending}
        title="Copy file into the review staging folder"
      >
        {promote.isPending ? '...' : 'Send to Review'}
      </button>
      {msg && <span className="promote-msg">{msg}</span>}
    </>
  )
}


function LibraryPanel() {
  const [search, setSearch] = useState('')
  const [format, setFormat] = useState('')
  const [genre, setGenre] = useState('')
  const [page, setPage] = useState(1)
  const [editingTrack, setEditingTrack] = useState(null)
  const [expandedId, setExpandedId] = useState(null)

  const params = { search: search || undefined, format: format || undefined, genre: genre || undefined, page }
  const { data: tracksData, isLoading } = useLibraryTracks(params)
  const { data: stats } = useLibraryStats()
  const scanLibrary = useScanLibrary()

  const tracks = tracksData?.results || []
  const totalPages = tracksData?.count ? Math.ceil(tracksData.count / 50) : 1

  const handleSearch = (e) => {
    setSearch(e.target.value)
    setPage(1)
  }

  return (
    <div className="library-panel">
      <div className="library-header">
        <h2 className="page-title">Library</h2>
        <div className="library-header__actions">
          <button
            className="btn btn-sm btn-primary"
            onClick={() => scanLibrary.mutate()}
            disabled={scanLibrary.isPending}
          >
            {scanLibrary.isPending ? 'Scanning...' : 'Scan Library'}
          </button>
        </div>
      </div>

      <StatsBar stats={stats} />

      <div className="library-filters">
        <input
          type="text"
          className="library-search"
          placeholder="Search artist, title, album, label..."
          value={search}
          onChange={handleSearch}
        />
        <select
          className="library-filter-select"
          value={format}
          onChange={e => { setFormat(e.target.value); setPage(1) }}
        >
          <option value="">All Formats</option>
          {FORMATS.filter(Boolean).map(f => (
            <option key={f} value={f}>{f.toUpperCase()}</option>
          ))}
        </select>
        <input
          type="text"
          className="library-filter-genre"
          placeholder="Filter genre..."
          value={genre}
          onChange={e => { setGenre(e.target.value); setPage(1) }}
        />
      </div>

      {isLoading ? (
        <div className="empty-state">Loading...</div>
      ) : tracks.length === 0 ? (
        <div className="empty-state">
          {search || format || genre
            ? 'No tracks match your filters.'
            : 'No tracks in library. Click "Scan Library" to import from ready folder.'}
        </div>
      ) : (
        <>
          <div className="library-table">
            <div className="library-table__header">
              <span className="col-artist">Artist</span>
              <span className="col-title">Title</span>
              <span className="col-album">Album</span>
              <span className="col-label">Label</span>
              <span className="col-format">Format</span>
              <span className="col-bitrate">Bitrate</span>
              <span className="col-duration">Duration</span>
              <span className="col-size">Size</span>
              <span className="col-actions">Actions</span>
            </div>
            {tracks.map(track => (
              <div key={track.id}>
                <div
                  className={`library-table__row ${expandedId === track.id ? 'library-table__row--expanded' : ''}`}
                  onClick={() => setExpandedId(expandedId === track.id ? null : track.id)}
                >
                  <span className="col-artist" title={track.artist}>{track.artist || '--'}</span>
                  <span className="col-title" title={track.title}>{track.title || track.filename}</span>
                  <span className="col-album" title={track.album}>{track.album || '--'}</span>
                  <span className="col-label" title={track.label}>{track.label || '--'}</span>
                  <span className="col-format">
                    <span className="format-badge">{track.format?.toUpperCase() || '?'}</span>
                  </span>
                  <span className="col-bitrate">{track.bitrate ? `${track.bitrate}k` : '--'}</span>
                  <span className="col-duration">{formatDuration(track.duration_seconds)}</span>
                  <span className="col-size">{formatSize(track.file_size_bytes)}</span>
                  <span className="col-actions" onClick={e => e.stopPropagation()}>
                    <button
                      className="btn btn-xs"
                      onClick={() => setEditingTrack(track)}
                    >
                      Edit
                    </button>
                    <PromoteButton track={track} />
                  </span>
                </div>
                {expandedId === track.id && (
                  <div className="library-table__detail">
                    <div className="detail-grid">
                      <div><strong>Catalog #:</strong> {track.catalog_number || '--'}</div>
                      <div><strong>Genre:</strong> {track.genre || '--'}</div>
                      <div><strong>Year:</strong> {track.year || '--'}</div>
                      <div><strong>Sample Rate:</strong> {track.sample_rate ? `${track.sample_rate} Hz` : '--'}</div>
                      <div><strong>Artwork:</strong> {track.has_artwork ? 'Yes' : 'No'}</div>
                      <div><strong>Source:</strong> {track.source || '--'}</div>
                    </div>
                    <div className="detail-path">{track.file_path}</div>
                  </div>
                )}
              </div>
            ))}
          </div>

          {totalPages > 1 && (
            <div className="library-pagination">
              <button
                className="btn btn-sm"
                disabled={page <= 1}
                onClick={() => setPage(p => p - 1)}
              >
                Previous
              </button>
              <span className="library-pagination__info">
                Page {page} of {totalPages}
              </span>
              <button
                className="btn btn-sm"
                disabled={page >= totalPages}
                onClick={() => setPage(p => p + 1)}
              >
                Next
              </button>
            </div>
          )}
        </>
      )}

      {editingTrack && (
        <EditModal track={editingTrack} onClose={() => setEditingTrack(null)} />
      )}

      {scanLibrary.data && (
        <div className="library-toast">
          Scan complete: {scanLibrary.data.created} new, {scanLibrary.data.updated} updated
        </div>
      )}
    </div>
  )
}

export default LibraryPanel
