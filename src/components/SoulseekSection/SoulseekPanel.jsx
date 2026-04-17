import { useState, useCallback, useMemo } from 'react'
import {
  useSlskdHealth, useSearch, useDownloadFile,
  useDownloadsStatus, useSearchResults,
  useSearchQueue, useAddToQueue, useRemoveFromQueue, useClearQueue,
  useCancelDownload, useClearDownloads, useDeleteDownload,
} from '../../api/hooks'
import BrowseModal from './BrowseModal'
import './SoulseekPanel.css'

const STATUS_LABELS = {
  pending: 'Pending',
  searching: 'Searching',
  found: 'Found',
  downloading: 'Downloading',
  downloaded: 'Downloaded',
  not_found: 'Not Found',
  failed: 'Failed',
}

const STATUS_ORDER = [
  'searching', 'downloading', 'found', 'pending',
  'not_found', 'failed', 'downloaded',
]

function SoulseekPanel() {
  const { data: health } = useSlskdHealth()
  const { data: downloadsData } = useDownloadsStatus()
  const { data: queueData } = useSearchQueue()
  const searchMutation = useSearch()
  const downloadMutation = useDownloadFile()
  const cancelMutation = useCancelDownload()
  const clearDlMutation = useClearDownloads()
  const deleteDlMutation = useDeleteDownload()
  const addToQueue = useAddToQueue()
  const removeFromQueue = useRemoveFromQueue()
  const clearQueue = useClearQueue()

  const [query, setQuery] = useState('')
  const [expandedItemId, setExpandedItemId] = useState(null)
  // Browse modal target: { username, filename, queueItemId, wantedItemId } or null
  const [browseTarget, setBrowseTarget] = useState(null)

  const connected = health?.status === 'connected'
  const loggedIn = health?.info?.server?.isLoggedIn
  const queueItems = queueData?.results || []
  const allDownloads = downloadsData?.downloads || []
  const downloadIndicators = downloadsData?.download_indicators || {}

  // File download status lookup
  const getFileDownloadStatus = useCallback((filename) => {
    if (!filename) return null
    return downloadIndicators[filename.toLowerCase()] || null
  }, [downloadIndicators])

  // Count items currently searching
  const searchingCount = queueItems.filter(i => i.status === 'searching').length

  // Sort: active first, then by status priority
  const sortedItems = [...queueItems].sort((a, b) => {
    const aIdx = STATUS_ORDER.indexOf(a.status)
    const bIdx = STATUS_ORDER.indexOf(b.status)
    return (aIdx === -1 ? 99 : aIdx) - (bIdx === -1 ? 99 : bIdx)
  })

  // Searchable items
  const searchableStatuses = ['pending', 'not_found', 'failed']
  const searchableItems = queueItems.filter(i => searchableStatuses.includes(i.status))

  // Download counts
  const downloadedCount = queueItems.filter(i => i.status === 'downloaded').length
  const notFoundCount = queueItems.filter(i => i.status === 'not_found').length

  // Split downloads by status
  const activeDownloads = allDownloads.filter(d => d.status === 'queued' || d.status === 'downloading')
  const completedDownloads = allDownloads.filter(d => d.status === 'completed')
  const failedDownloads = allDownloads.filter(d => d.status === 'failed' || d.status === 'cancelled')

  // Free-text search: add to queue + auto-trigger search
  const handleFreeSearch = async (e) => {
    e.preventDefault()
    if (!query.trim()) return
    try {
      const items = await addToQueue.mutateAsync({ query: query.trim() })
      const newItem = items?.[0]
      if (newItem) {
        // Auto-trigger search for the new queue item
        await searchMutation.mutateAsync({ queue_item_id: newItem.id })
        setExpandedItemId(newItem.id)
      }
      setQuery('')
    } catch (err) {
      console.error('Search failed:', err)
    }
  }

  const handleItemSearch = useCallback(async (queueItemId) => {
    try {
      await searchMutation.mutateAsync({ queue_item_id: queueItemId })
    } catch (err) {
      console.error('Search failed:', err)
    }
  }, [searchMutation])

  const handleSearchAll = useCallback(async () => {
    for (let i = 0; i < searchableItems.length; i++) {
      const item = searchableItems[i]
      try {
        await searchMutation.mutateAsync({ queue_item_id: item.id })
      } catch (err) {
        console.error(`Search failed for ${item.id}:`, err)
      }
      if (i < searchableItems.length - 1) {
        await new Promise(r => setTimeout(r, 500))
      }
    }
  }, [searchableItems, searchMutation])

  const handleDownload = (result, queueItemId) => {
    // Find the queue item to also link wanted_item if present
    const qi = queueItems.find(q => q.id === queueItemId)
    downloadMutation.mutate({
      username: result.username,
      filename: result.filename,
      size: result.file_size || result.size || 0,
      queue_item_id: queueItemId || undefined,
      wanted_item_id: qi?.wanted_item || undefined,
    })
  }

  const handleRemove = useCallback((id, e) => {
    e?.stopPropagation()
    removeFromQueue.mutate(id)
    if (expandedItemId === id) setExpandedItemId(null)
  }, [removeFromQueue, expandedItemId])

  const formatSize = (bytes) => {
    if (!bytes) return '\u2014'
    const mb = bytes / (1024 * 1024)
    return mb >= 1 ? `${mb.toFixed(1)} MB` : `${(bytes / 1024).toFixed(0)} KB`
  }

  const formatSpeed = (bytesPerSec) => {
    if (!bytesPerSec) return ''
    const kbps = bytesPerSec / 1024
    if (kbps >= 1024) return `${(kbps / 1024).toFixed(1)} MB/s`
    return `${kbps.toFixed(0)} KB/s`
  }

  const formatAgo = (dateStr) => {
    if (!dateStr) return 'never'
    const diff = Date.now() - new Date(dateStr).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    const days = Math.floor(hrs / 24)
    return `${days}d ago`
  }

  return (
    <div className="soulseek-panel">
      <div className="slsk-header">
        <h2 className="page-title">Soulseek</h2>
        <div className={`slskd-status ${connected ? (loggedIn ? 'slskd-status--ok' : 'slskd-status--warn') : 'slskd-status--error'}`}>
          <span className="status-dot" />
          {!connected ? 'Disconnected' : loggedIn ? 'Online' : 'Not logged in'}
          {connected && (
            <a href="http://localhost:5030" target="_blank" rel="noopener noreferrer" className="slskd-link">
              Web UI
            </a>
          )}
        </div>
      </div>

      {/* Search bar — adds to queue + auto-searches */}
      <form onSubmit={handleFreeSearch} className="search-form">
        <input
          type="text"
          className="search-input"
          placeholder="Search Soulseek..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          disabled={!connected}
        />
        <button
          type="submit"
          className="btn btn-primary"
          disabled={!connected || addToQueue.isPending || !query.trim()}
        >
          {addToQueue.isPending ? 'Adding...' : 'Search'}
        </button>
      </form>

      {/* ── Search Queue ── */}
      <div className="queue-section">
        <div className="queue-header">
          <h3 className="section-title">
            Search Queue
            {queueItems.length > 0 && <span className="queue-count">{queueItems.length}</span>}
          </h3>
          <div className="queue-actions">
            {searchingCount > 0 && (
              <span className="batch-progress">
                <span className="spinner-sm" /> {searchingCount} searching...
              </span>
            )}
            {searchingCount === 0 && searchableItems.length > 0 && connected && (
              <button
                className="btn btn-sm btn-primary"
                onClick={handleSearchAll}
                disabled={searchMutation.isPending}
              >
                Search All ({searchableItems.length})
              </button>
            )}
            {downloadedCount > 0 && (
              <button
                className="btn btn-xs"
                onClick={() => clearQueue.mutate('downloaded')}
                disabled={clearQueue.isPending}
              >
                Clear Downloaded
              </button>
            )}
            {notFoundCount > 0 && (
              <button
                className="btn btn-xs"
                onClick={() => clearQueue.mutate('not_found')}
                disabled={clearQueue.isPending}
              >
                Clear Not Found
              </button>
            )}
          </div>
        </div>

        {searchingCount > 0 && (
          <div className="batch-bar">
            <div
              className="batch-bar-fill"
              style={{ width: `${((queueItems.length - searchableItems.length - searchingCount) / Math.max(queueItems.length, 1)) * 100}%` }}
            />
          </div>
        )}

        {sortedItems.length > 0 ? (
          <table className="queue-table">
            <thead>
              <tr>
                <th>Item</th>
                <th>Status</th>
                <th>Score</th>
                <th>Results</th>
                <th>Last Searched</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {sortedItems.map(item => (
                <QueueItemRow
                  key={item.id}
                  item={item}
                  expanded={expandedItemId === item.id}
                  onToggle={() => setExpandedItemId(
                    expandedItemId === item.id ? null : item.id
                  )}
                  onSearch={() => handleItemSearch(item.id)}
                  onDownload={handleDownload}
                  onBrowse={(result) => setBrowseTarget({
                    username: result.username,
                    filename: result.filename,
                    queueItemId: item.id,
                    wantedItemId: item.wanted_item || null,
                  })}
                  onRemove={handleRemove}
                  downloadPending={downloadMutation.isPending}
                  getFileDownloadStatus={getFileDownloadStatus}
                  formatSize={formatSize}
                  formatAgo={formatAgo}
                  connected={connected}
                />
              ))}
            </tbody>
          </table>
        ) : (
          <div className="no-results">
            No items in queue. Search above or add from the Wanted tab.
          </div>
        )}
      </div>

      {/* ── Downloads Section ── */}
      {allDownloads.length > 0 && (
        <div className="downloads-section">
          <div className="downloads-header">
            <h3 className="section-title">Downloads</h3>
            <div className="downloads-actions">
              {completedDownloads.length > 0 && (
                <button
                  className="btn btn-xs"
                  onClick={() => clearDlMutation.mutate('completed')}
                  disabled={clearDlMutation.isPending}
                >
                  Clear Completed ({completedDownloads.length})
                </button>
              )}
              {failedDownloads.length > 0 && (
                <button
                  className="btn btn-xs"
                  onClick={() => clearDlMutation.mutate('failed')}
                  disabled={clearDlMutation.isPending}
                >
                  Clear Failed ({failedDownloads.length})
                </button>
              )}
            </div>
          </div>

          {activeDownloads.length > 0 && (
            <div className="dl-group">
              <div className="dl-group-label">Active ({activeDownloads.length})</div>
              {activeDownloads.map(dl => (
                <DownloadRow
                  key={dl.id}
                  dl={dl}
                  onCancel={() => cancelMutation.mutate(dl.id)}
                  cancelPending={cancelMutation.isPending}
                  formatSize={formatSize}
                  formatSpeed={formatSpeed}
                />
              ))}
            </div>
          )}

          {completedDownloads.length > 0 && (
            <div className="dl-group">
              <div className="dl-group-label">Completed ({completedDownloads.length})</div>
              {completedDownloads.map(dl => (
                <div key={dl.id} className="dl-row dl-row--completed">
                  <span className="dl-icon">✓</span>
                  <span className="dl-name">{dl.filename.split(/[/\\]/).pop()}</span>
                  <span className="dl-user">from {dl.username}</span>
                  <button
                    className="btn btn-xs btn-ghost dl-remove-btn"
                    onClick={() => deleteDlMutation.mutate(dl.id)}
                    disabled={deleteDlMutation.isPending}
                    title="Remove from list"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}

          {failedDownloads.length > 0 && (
            <div className="dl-group">
              <div className="dl-group-label">Failed ({failedDownloads.length})</div>
              {failedDownloads.map(dl => (
                <div key={dl.id} className="dl-row dl-row--failed">
                  <span className="dl-icon">✗</span>
                  <span className="dl-name">{dl.filename.split(/[/\\]/).pop()}</span>
                  <span className="dl-error" title={dl.error_message || dl.slskd_state}>
                    {dl.error_message || dl.slskd_state || dl.status}
                  </span>
                  <button
                    className="btn btn-xs btn-ghost dl-remove-btn"
                    onClick={() => deleteDlMutation.mutate(dl.id)}
                    disabled={deleteDlMutation.isPending}
                    title="Remove from list"
                  >
                    ✕
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {browseTarget && (
        <BrowseModal
          username={browseTarget.username}
          initialFilename={browseTarget.filename}
          queueItemId={browseTarget.queueItemId}
          wantedItemId={browseTarget.wantedItemId}
          onClose={() => setBrowseTarget(null)}
        />
      )}
    </div>
  )
}

function DownloadRow({ dl, onCancel, cancelPending, formatSize, formatSpeed }) {
  const basename = dl.filename.split(/[/\\]/).pop()
  const isDownloading = dl.status === 'downloading'
  const percent = dl.percent || dl.progress || 0
  const speed = dl.speed || 0
  const remaining = dl.remaining || ''

  const formatRemaining = (str) => {
    if (!str) return ''
    const parts = str.split(':')
    if (parts.length >= 3) {
      const h = parseInt(parts[0], 10)
      const m = parseInt(parts[1], 10)
      const s = parseInt(parts[2], 10)
      if (h > 0) return `${h}h ${m}m`
      if (m > 0) return `${m}m ${s}s`
      return `${s}s`
    }
    return str
  }

  return (
    <div className={`dl-row${isDownloading ? ' dl-row--active' : ''}`}>
      <div className="dl-row-main">
        <span className="dl-icon">
          {isDownloading ? <span className="spinner-sm" /> : '⏳'}
        </span>
        <div className="dl-info">
          <span className="dl-name">{basename}</span>
          <span className="dl-meta">
            from {dl.username}
            {dl.slskd_state && dl.slskd_state.includes('Queued') && (
              <span className="dl-queue-badge">Queued remotely</span>
            )}
          </span>
        </div>
        <div className="dl-stats">
          {isDownloading && speed > 0 && (
            <span className="dl-speed">{formatSpeed(speed)}</span>
          )}
          {isDownloading && remaining && (
            <span className="dl-eta">{formatRemaining(remaining)}</span>
          )}
          <span className="dl-percent">{Math.round(percent)}%</span>
          <button
            className="btn btn-xs btn-danger"
            onClick={onCancel}
            disabled={cancelPending}
            title="Cancel download"
          >
            ✕
          </button>
        </div>
      </div>
      <div className="dl-progress-bar">
        <div
          className={`dl-progress-fill${isDownloading ? ' dl-progress-fill--active' : ''}`}
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
    </div>
  )
}

function QueueItemRow({
  item, expanded, onToggle, onSearch,
  onDownload, onBrowse, onRemove, downloadPending, getFileDownloadStatus,
  formatSize, formatAgo, connected,
}) {
  const { data: searchResults } = useSearchResults(expanded ? item.id : null, item.status)

  const isRawQuery = !!item.raw_query
  const label = isRawQuery
    ? item.raw_query
    : ([item.artist, item.title].filter(Boolean).join(' \u2014 ') ||
       item.release_name || item.catalog_number || 'Untitled')
  const sub = isRawQuery
    ? null
    : [item.release_name, item.catalog_number].filter(Boolean).join(' \u00b7 ')

  const isSearching = item.status === 'searching'
  const isActive = isSearching || item.status === 'downloading'
  const canExpand = item.last_searched

  return (
    <>
      <tr
        className={`q-row${expanded ? ' q-row--expanded' : ''}${isActive ? ' q-row--active' : ''}`}
        onClick={canExpand ? onToggle : undefined}
        style={{ cursor: canExpand ? 'pointer' : 'default' }}
      >
        <td className="td-item">
          <span className={`item-label${isRawQuery ? ' item-label--query' : ''}`}>{label}</span>
          {sub && label !== sub && <span className="item-sub">{sub}</span>}
          {item.wanted_item && <span className="wanted-link-badge" title="From wanted list">W</span>}
        </td>
        <td>
          <QueueItemStatus status={item.status} />
        </td>
        <td className="td-score">
          {item.best_match_score ? `${item.best_match_score}%` : '\u2014'}
        </td>
        <td className="td-count">
          {item.search_count > 0 ? item.search_results_count ?? '\u2014' : '\u2014'}
        </td>
        <td className="td-ago">{formatAgo(item.last_searched)}</td>
        <td className="td-action" onClick={e => e.stopPropagation()}>
          {isSearching ? (
            <span className="searching-indicator">
              <span className="spinner" /> Searching...
            </span>
          ) : (
            <div className="action-buttons">
              <button
                className="btn btn-xs btn-primary"
                onClick={(e) => { e.stopPropagation(); onSearch() }}
                disabled={!connected || isSearching}
              >
                Search
              </button>
              <button
                className="queue-remove-btn"
                onClick={(e) => onRemove(item.id, e)}
                title="Remove from queue"
              >
                ✕
              </button>
            </div>
          )}
        </td>
      </tr>

      {/* Expanded search results */}
      {expanded && canExpand && (
        <tr className="results-expand-row">
          <td colSpan="6">
            {searchResults && searchResults.length > 0 ? (
              <div className="expand-results">
                {searchResults.slice(0, 20).map((r, i) => {
                  const dlStatus = getFileDownloadStatus(r.filename)
                  return (
                    <div
                      key={r.id || i}
                      className={`expand-result-item${dlStatus ? ' er-item--downloaded' : ''}${r.is_locked ? ' er-item--locked' : ''}`}
                    >
                      <span className="er-score">{r.match_score}%</span>
                      <span className="er-file" title={r.filename}>
                        {r.filename.split(/[/\\]/).pop()}
                      </span>
                      <span className="er-size">{formatSize(r.file_size)}</span>
                      <span className="er-format">{r.file_extension?.toUpperCase()}</span>
                      <span className={`er-bitrate${r.bitrate >= 320 ? ' hi-q' : r.bitrate && r.bitrate < 192 ? ' lo-q' : ''}`}>
                        {r.bitrate ? `${r.bitrate}k` : r.bit_depth ? `${r.bit_depth}bit` : ''}
                      </span>
                      <span className="er-user">
                        {r.is_locked && (
                          <span className="er-lock" title="Locked — peer requires privileged access">🔒</span>
                        )}
                        {r.username}
                        {r.free_upload_slots && <span className="free-slot"> free</span>}
                      </span>
                      <button
                        className="btn btn-xs btn-ghost"
                        onClick={(e) => {
                          e.stopPropagation()
                          onBrowse(r)
                        }}
                        title={`Browse ${r.username}'s shared folder`}
                      >
                        📁
                      </button>
                      {dlStatus ? (
                        <span className={`dl-badge dl-badge--${dlStatus}`}>
                          {dlStatus === 'completed' ? '✓ Done' : dlStatus === 'downloading' ? '↓ DL' : '⏳ Queue'}
                        </span>
                      ) : (
                        <button
                          className="btn btn-xs"
                          onClick={(e) => {
                            e.stopPropagation()
                            onDownload(r, item.id)
                          }}
                          disabled={downloadPending}
                        >
                          DL
                        </button>
                      )}
                    </div>
                  )
                })}
              </div>
            ) : searchResults && searchResults.length === 0 ? (
              <div className="expand-empty">
                No results found — try searching again
              </div>
            ) : (
              <div className="expand-empty">Loading results...</div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

function QueueItemStatus({ status }) {
  if (status === 'searching') {
    return (
      <span className="status-pill status-pill--searching">
        <span className="spinner-sm" /> Searching
      </span>
    )
  }

  const statusClass = `status-pill status-pill--${status}`
  return (
    <span className={statusClass}>
      {STATUS_LABELS[status] || status}
    </span>
  )
}

export default SoulseekPanel
