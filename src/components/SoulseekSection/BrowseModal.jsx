import { useEffect, useMemo, useState } from 'react'
import { useBrowseUser, useDownloadFile } from '../../api/hooks'
import './BrowseModal.css'

function formatSize(bytes) {
  if (!bytes) return '\u2014'
  const mb = bytes / (1024 * 1024)
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`
}

function basename(path) {
  return (path || '').split(/[/\\]/).pop()
}

function parentDir(path) {
  const norm = (path || '').replace(/\\/g, '/')
  const idx = norm.lastIndexOf('/')
  return idx >= 0 ? norm.slice(0, idx) : ''
}

export default function BrowseModal({ username, initialFilename = '', queueItemId, wantedItemId, onClose }) {
  // Default scope = the folder containing the file the user clicked Browse on.
  // They can clear/edit the prefix to widen.
  const initialPrefix = useMemo(() => parentDir(initialFilename), [initialFilename])
  const [dirPrefix, setDirPrefix] = useState(initialPrefix)
  const [audioOnly, setAudioOnly] = useState(true)
  const [expandedDirs, setExpandedDirs] = useState(() => new Set([initialPrefix.replace(/\\/g, '/').toLowerCase()]))
  const [limit, setLimit] = useState(200)

  const { data, isLoading, error, isFetching, refetch } = useBrowseUser({
    username,
    dirPrefix,
    audioOnly,
    limit,
  })

  const downloadMutation = useDownloadFile()

  // Close on Escape
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const dirs = data?.directories || []
  const isInitialFolder = (name) => name && initialFilename
    && name.replace(/\\/g, '/').toLowerCase() === parentDir(initialFilename).replace(/\\/g, '/').toLowerCase()

  const toggle = (name) => {
    setExpandedDirs(prev => {
      const k = name.replace(/\\/g, '/').toLowerCase()
      const next = new Set(prev)
      if (next.has(k)) next.delete(k); else next.add(k)
      return next
    })
  }

  const handleDownload = (file) => {
    downloadMutation.mutate({
      username,
      filename: file.filename,
      size: file.size || 0,
      queue_item_id: queueItemId || undefined,
      wanted_item_id: wantedItemId || undefined,
    })
  }

  return (
    <div className="browse-modal-backdrop" onClick={onClose}>
      <div className="browse-modal" onClick={e => e.stopPropagation()}>
        <header className="browse-modal-header">
          <div>
            <h3 className="browse-modal-title">Browse <span className="mono">{username}</span></h3>
            <div className="browse-modal-stats">
              {isLoading ? 'Loading…' : data && (
                <>
                  {data.matched_dirs} folder{data.matched_dirs === 1 ? '' : 's'}
                  {data.matched_dirs !== (data.total_dirs + (data.locked_dirs || 0)) && (
                    <> (of {data.total_dirs}{data.locked_dirs ? ` + ${data.locked_dirs} locked` : ''})</>
                  )}
                  {' · '}{data.total_files} file{data.total_files === 1 ? '' : 's'}
                  {data.truncated && <span className="browse-modal-warn"> · showing first {data.returned_dirs}</span>}
                </>
              )}
            </div>
          </div>
          <button className="browse-modal-close" onClick={onClose} title="Close (Esc)">✕</button>
        </header>

        <div className="browse-modal-controls">
          <input
            type="text"
            className="browse-modal-prefix"
            value={dirPrefix}
            placeholder="Filter by folder path (clear to show everything)"
            onChange={e => setDirPrefix(e.target.value)}
          />
          <label className="browse-modal-checkbox">
            <input type="checkbox" checked={audioOnly} onChange={e => setAudioOnly(e.target.checked)} />
            Audio only
          </label>
          {dirPrefix && (
            <button className="btn btn-xs" onClick={() => setDirPrefix('')}>Clear filter</button>
          )}
          <button className="btn btn-xs" onClick={() => refetch()}>Refresh</button>
        </div>

        <div className="browse-modal-body">
          {error && (
            <div className="browse-modal-error">
              Browse failed: {error?.data?.error || error?.message}
            </div>
          )}
          {!error && !isLoading && dirs.length === 0 && (
            <div className="browse-modal-empty">
              No folders match. Clear the filter or try a shorter prefix.
            </div>
          )}
          {dirs.map(dir => {
            const k = (dir.name || '').replace(/\\/g, '/').toLowerCase()
            const open = expandedDirs.has(k)
            const highlighted = isInitialFolder(dir.name)
            return (
              <div key={dir.name} className={`browse-dir${highlighted ? ' browse-dir--match' : ''}${dir.locked ? ' browse-dir--locked' : ''}`}>
                <div className="browse-dir-header" onClick={() => toggle(dir.name)}>
                  <span className={`browse-dir-toggle${open ? ' browse-dir-toggle--open' : ''}`}>▶</span>
                  {dir.locked && (
                    <span className="browse-dir-lock" title="Locked — peer requires privileged access">🔒</span>
                  )}
                  <span className="browse-dir-name mono">{dir.name || '(root)'}</span>
                  <span className="browse-dir-count">{dir.file_count} file{dir.file_count === 1 ? '' : 's'}</span>
                </div>
                {open && dir.files.length > 0 && (
                  <div className="browse-dir-files">
                    {dir.files.map((f, i) => {
                      const isMatched = initialFilename
                        && f.filename.replace(/\\/g, '/').toLowerCase() === initialFilename.replace(/\\/g, '/').toLowerCase()
                      return (
                        <div key={i} className={`browse-file${isMatched ? ' browse-file--match' : ''}${f.locked ? ' browse-file--locked' : ''}`}>
                          <span className="browse-file-lock" title={f.locked ? 'Locked file' : ''}>{f.locked ? '🔒' : ''}</span>
                          <span className="browse-file-name" title={f.filename}>{basename(f.filename)}</span>
                          <span className="browse-file-meta">
                            {f.extension && <span className="browse-file-ext">{f.extension.toUpperCase()}</span>}
                            {f.bitrate ? <span>{f.bitrate}k</span> : null}
                            <span>{formatSize(f.size)}</span>
                          </span>
                          <button
                            className="btn btn-xs"
                            onClick={() => handleDownload(f)}
                            disabled={downloadMutation.isPending}
                          >
                            DL
                          </button>
                        </div>
                      )
                    })}
                  </div>
                )}
              </div>
            )
          })}

          {data?.truncated && (
            <button
              className="btn btn-sm browse-modal-more"
              onClick={() => setLimit(l => l + 200)}
            >
              Show more folders
            </button>
          )}

          {isFetching && !isLoading && (
            <div className="browse-modal-loading">Refreshing…</div>
          )}
        </div>
      </div>
    </div>
  )
}
