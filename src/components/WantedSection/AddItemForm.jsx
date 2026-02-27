import { useState } from 'react'

function AddItemForm({ sources, onSubmit, onClose }) {
  const [artist, setArtist] = useState('')
  const [title, setTitle] = useState('')
  const [releaseName, setReleaseName] = useState('')
  const [catalogNumber, setCatalogNumber] = useState('')
  const [label, setLabel] = useState('')
  const [sourceId, setSourceId] = useState('')
  const [notes, setNotes] = useState('')

  const handleSubmit = (e) => {
    e.preventDefault()
    if (!artist && !title && !releaseName && !catalogNumber) return
    onSubmit({
      artist,
      title,
      release_name: releaseName,
      catalog_number: catalogNumber,
      label,
      source: sourceId || null,
      notes,
    })
  }

  const hasAny = artist || title || releaseName || catalogNumber

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Add to Wanted</h3>
          <button className="btn-close" onClick={onClose} aria-label="Close" />
        </div>
        <form onSubmit={handleSubmit} className="add-form">
          <div className="form-row">
            <div className="form-group form-group--flex">
              <label>Artist</label>
              <input
                type="text"
                value={artist}
                onChange={(e) => setArtist(e.target.value)}
                placeholder="Artist name"
                autoFocus
              />
            </div>
            <div className="form-group form-group--flex">
              <label>Title</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Track title"
              />
            </div>
          </div>

          <div className="form-group">
            <label>Release</label>
            <input
              type="text"
              value={releaseName}
              onChange={(e) => setReleaseName(e.target.value)}
              placeholder="Album or EP name"
            />
          </div>

          <div className="form-row">
            <div className="form-group form-group--flex">
              <label>Catalog #</label>
              <input
                type="text"
                value={catalogNumber}
                onChange={(e) => setCatalogNumber(e.target.value)}
                placeholder="e.g. WARP123"
              />
            </div>
            <div className="form-group form-group--flex">
              <label>Label</label>
              <input
                type="text"
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Label name"
              />
            </div>
          </div>

          <div className="form-row">
            <div className="form-group form-group--flex">
              <label>Source</label>
              <select value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                <option value="">None</option>
                {Array.isArray(sources) && sources.map(s => (
                  <option key={s.id} value={s.id}>{s.name}</option>
                ))}
              </select>
            </div>
            <div className="form-group form-group--flex">
              <label>Notes</label>
              <input
                type="text"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="Optional notes"
              />
            </div>
          </div>

          <div className="form-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={!hasAny}>
              Add
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

export default AddItemForm
