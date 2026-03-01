import { useState } from 'react'

function ImportPreview({ tracks, onConfirm, isConfirming }) {
  const [selected, setSelected] = useState(() => {
    // Auto-select non-duplicates
    const initial = new Set()
    tracks.forEach((t, i) => {
      if (!t.is_duplicate) initial.add(i)
    })
    return initial
  })

  const toggle = (idx) => {
    setSelected(prev => {
      const next = new Set(prev)
      if (next.has(idx)) next.delete(idx)
      else next.add(idx)
      return next
    })
  }

  const selectAll = () => {
    setSelected(new Set(tracks.map((_, i) => i)))
  }

  const skipDuplicates = () => {
    setSelected(new Set(tracks.filter(t => !t.is_duplicate).map((_, i) => {
      // Need to find original index
      let count = 0
      for (let j = 0; j < tracks.length; j++) {
        if (!tracks[j].is_duplicate) {
          if (count === i) return j
          count++
        }
      }
      return i
    })))
  }

  const selectNone = () => setSelected(new Set())

  const handleConfirm = () => {
    onConfirm(Array.from(selected))
  }

  const dupCount = tracks.filter(t => t.is_duplicate).length
  const newCount = tracks.length - dupCount

  return (
    <div className="import-preview">
      <div className="import-preview__summary">
        <span>{tracks.length} tracks found</span>
        {dupCount > 0 && (
          <span className="import-preview__dup-count">{dupCount} duplicates</span>
        )}
        <span className="import-preview__selected">{selected.size} selected</span>
      </div>

      <div className="import-preview__actions">
        <button className="btn btn-xs" onClick={selectAll}>All</button>
        <button className="btn btn-xs" onClick={skipDuplicates}>Skip Dupes</button>
        <button className="btn btn-xs" onClick={selectNone}>None</button>
      </div>

      <div className="import-preview__list">
        {tracks.map((track, idx) => (
          <label
            key={idx}
            className={`import-preview__item${track.is_duplicate ? ' import-preview__item--dup' : ''}${selected.has(idx) ? ' import-preview__item--selected' : ''}`}
          >
            <input
              type="checkbox"
              checked={selected.has(idx)}
              onChange={() => toggle(idx)}
            />
            <div className="import-preview__track-info">
              <div className="import-preview__track-main">
                {track.artist && (
                  <span className="import-preview__artist">{track.artist}</span>
                )}
                {track.artist && track.title && <span className="import-preview__sep"> - </span>}
                <span className="import-preview__title">{track.title || track.raw_title}</span>
              </div>
              {track.release_name && (
                <div className="import-preview__release">{track.release_name}</div>
              )}
            </div>
            {track.is_duplicate && (
              <span className="import-preview__badge import-preview__badge--dup">
                DUP {track.fuzzy_score ? `${track.fuzzy_score}%` : ''}
              </span>
            )}
          </label>
        ))}
      </div>

      <div className="form-actions">
        <button
          className="btn btn-primary"
          onClick={handleConfirm}
          disabled={selected.size === 0 || isConfirming}
        >
          {isConfirming ? 'Importing...' : `Import ${selected.size} Track${selected.size !== 1 ? 's' : ''}`}
        </button>
      </div>
    </div>
  )
}

export default ImportPreview
