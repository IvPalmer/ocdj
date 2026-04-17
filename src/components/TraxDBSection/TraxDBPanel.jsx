import { useMemo, useState } from 'react'
import {
  useTraxDBInventory,
  useTraxDBOperations,
  useTraxDBOperation,
  useTriggerSync,
  useTriggerDownload,
  useTriggerAudit,
  useTraxDBDownloadProgress,
  useCancelTraxDBDownload,
  useTraxDBFolders,
} from '../../api/hooks'
import './TraxDBPanel.css'

/* ── helpers ── */

function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`
}

function timeAgo(isoStr) {
  if (!isoStr) return ''
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000
  if (diff < 60) return 'just now'
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return `${Math.floor(diff / 86400)}d ago`
}

function countField(summary, arrayKey, countKey) {
  const arr = summary?.[arrayKey]
  if (Array.isArray(arr)) return arr.length
  return summary?.[countKey] ?? 0
}

/* ── compact library overview ── */

function LibraryOverview({ inventory, latestSync, latestDownload }) {
  if (!inventory) return null
  return (
    <div className="traxdb-section traxdb-section--inventory">
      <div className="traxdb-section-body">
        <div className="traxdb-summary">
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{inventory.known_lists_count}</div>
            <div className="traxdb-stat-label">Lists</div>
          </div>
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{inventory.file_count?.toLocaleString()}</div>
            <div className="traxdb-stat-label">Files</div>
          </div>
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{formatBytes(inventory.total_bytes)}</div>
            <div className="traxdb-stat-label">Size</div>
          </div>
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{inventory.latest_date || '—'}</div>
            <div className="traxdb-stat-label">Newest in library</div>
          </div>
        </div>
        <div className="traxdb-inventory-meta">
          <span>Last check: {latestSync?.updated ? timeAgo(latestSync.updated) : 'never'}</span>
          <span> · </span>
          <span>Last download: {latestDownload?.updated ? timeAgo(latestDownload.updated) : 'never'}</span>
        </div>
      </div>
    </div>
  )
}

/* ── main flow: check → download ── */

function CheckAndDownload({
  latestSync, syncRunning, onCheck, syncPending,
  latestDownload, downloadRunning, onDownload, onCancel, downloadPending,
  inventory,
}) {
  // What "new" means here is: scraped lists in the DB that aren't downloaded
  // yet. The sync's `links_new` only counts deltas since the last sync run —
  // re-running sync on already-discovered lists would (correctly) report 0
  // new even when 17 are still pending download. Pull pending list metadata
  // straight from the folders endpoint so the UI matches reality.
  const { data: pendingData } = useTraxDBFolders({
    download_status: 'pending',
    limit: 100,
  })
  const pendingFolders = pendingData?.results || []
  const newCount = pendingData?.total ?? pendingFolders.length

  // Live download progress. The endpoint returns { status, progress: {...} }
  // so the actual counters live one level down.
  const progressId = downloadRunning ? latestDownload?.id : null
  const { data: progressResp } = useTraxDBDownloadProgress(progressId)
  const progress = progressResp?.progress || progressResp || {}
  const listsTotal = progress.lists_total || 0
  const listsCompleted = progress.lists_completed || 0
  const filesTotal = progress.files_total || 0
  const filesCompleted = progress.files_completed || 0
  const bytesTotal = progress.bytes_total || 0
  const bytesDownloaded = progress.bytes_downloaded || 0
  // Prefer file-level progress when we know it — gives smoother bar movement
  // since one list can take minutes and visual progress would otherwise stall.
  const pct = filesTotal > 0
    ? Math.round((filesCompleted / filesTotal) * 100)
    : (listsTotal > 0 ? Math.round((listsCompleted / listsTotal) * 100) : 0)

  const dlSummary = latestDownload?.summary || {}
  const dlCompleted = !downloadRunning && latestDownload?.status === 'completed'

  // Has the latest download already consumed the latest sync? Avoid showing
  // stale "X new" after a successful download.
  const downloadIsAfterSync = latestDownload?.created && latestSync?.created
    && new Date(latestDownload.created) >= new Date(latestSync.created)

  return (
    <div className="traxdb-section">
      <div className="traxdb-section-body traxdb-flow">
        {/* Stage 1: Check */}
        {!syncRunning && !downloadRunning && (
          <div className="traxdb-flow-step">
            <button
              className="btn btn-accent btn-lg"
              onClick={onCheck}
              disabled={syncPending}
            >
              {latestSync ? 'Check Again for New Lists' : 'Check for New Lists'}
            </button>
            {latestSync?.status === 'failed' && (
              <div className="traxdb-error">{latestSync.error_message || 'Check failed'}</div>
            )}
          </div>
        )}

        {syncRunning && (
          <div className="traxdb-flow-step">
            <span className="traxdb-status traxdb-status--running">
              <span className="traxdb-spinner" /> Scanning blog…
            </span>
          </div>
        )}

        {/* Stage 2: Pending lists ready to download */}
        {!syncRunning && !downloadRunning && newCount > 0 && (
          <div className="traxdb-flow-step">
            <div className="traxdb-result traxdb-result--good">
              <strong>{newCount}</strong> list{newCount === 1 ? '' : 's'} ready to download.
            </div>
            <ul className="traxdb-newlist">
              {pendingFolders.slice(0, 8).map(f => (
                <li key={f.id}>
                  <span className="mono">{f.folder_id}</span>
                  <span className="traxdb-newlist-date">{f.inferred_date || ''}</span>
                </li>
              ))}
              {pendingFolders.length > 8 && <li className="muted">…and {pendingFolders.length - 8} more</li>}
            </ul>
            <button
              className="btn btn-accent btn-lg"
              onClick={onDownload}
              disabled={downloadPending}
            >
              Download {newCount} New
            </button>
          </div>
        )}

        {/* Stage 2 (alt): Up to date */}
        {!syncRunning && !downloadRunning && newCount === 0 && latestSync?.status === 'completed' && (
          <div className="traxdb-flow-step">
            <div className="traxdb-result traxdb-result--neutral">
              You're up to date. No new lists pending download.
            </div>
          </div>
        )}

        {/* Stage 3: Downloading */}
        {downloadRunning && (
          <div className="traxdb-flow-step">
            <div className="traxdb-status traxdb-status--running">
              <span className="traxdb-spinner" /> Downloading…
            </div>
            <div className="traxdb-progress">
              <div className="traxdb-progress-bar">
                <div className="traxdb-progress-fill" style={{ width: `${pct}%` }} />
              </div>
              <div className="traxdb-progress-meta">
                <span>
                  {listsCompleted} / {listsTotal || '?'} lists
                  {filesTotal > 0 && ` · ${filesCompleted} / ${filesTotal} files`}
                </span>
                <span>{pct}%</span>
              </div>
              {(bytesDownloaded > 0 || progress.current_list) && (
                <div className="traxdb-progress-detail">
                  {bytesTotal > 0 && (
                    <span>{formatBytes(bytesDownloaded)} / {formatBytes(bytesTotal)}</span>
                  )}
                  {progress.current_list && (
                    <span> · current: <span className="mono">{progress.current_list}</span></span>
                  )}
                </div>
              )}
            </div>
            <button
              className="btn btn-danger btn-sm"
              onClick={() => onCancel(latestDownload.id)}
              disabled={downloadPending}
            >
              Cancel
            </button>
          </div>
        )}

        {/* Stage 4: Just-finished download summary (only fresh ones) */}
        {!downloadRunning && dlCompleted && downloadIsAfterSync && newCount === 0 && (
          <div className="traxdb-flow-step">
            <div className="traxdb-result traxdb-result--good">
              Downloaded <strong>{dlSummary.lists_completed ?? 0}</strong> list
              {(dlSummary.lists_completed ?? 0) === 1 ? '' : 's'},{' '}
              <strong>{dlSummary.files_completed ?? 0}</strong> file
              {(dlSummary.files_completed ?? 0) === 1 ? '' : 's'}{' '}
              ({formatBytes(dlSummary.bytes_downloaded)})
              {(dlSummary.dead_links_count ?? 0) > 0 && (
                <span className="muted"> · {dlSummary.dead_links_count} dead link{dlSummary.dead_links_count === 1 ? '' : 's'} skipped</span>
              )}
            </div>
          </div>
        )}

        {!downloadRunning && latestDownload?.status === 'failed' && (
          <div className="traxdb-error">{latestDownload.error_message || 'Download failed'}</div>
        )}
      </div>
    </div>
  )
}

/* ── advanced (audit + archive) collapsed by default ── */

function Advanced({ latestAudit, auditRunning, onAudit, auditPending, opsCount }) {
  const [open, setOpen] = useState(false)
  const [showFolders, setShowFolders] = useState(false)
  const { data: foldersData } = useTraxDBFolders({ limit: 100 })
  const folders = foldersData?.results || []
  const total = foldersData?.total || 0
  const summary = latestAudit?.summary || {}

  return (
    <div className="traxdb-section traxdb-section--advanced">
      <div
        className="traxdb-section-header traxdb-section-header--clickable"
        onClick={() => setOpen(o => !o)}
      >
        <h3>
          <span className={`traxdb-history-toggle ${open ? 'traxdb-history-toggle--open' : ''}`}>
            &#9654;
          </span>
          {' '}Advanced
        </h3>
      </div>
      {open && (
        <div className="traxdb-section-body">
          {/* Audit */}
          <div className="traxdb-flow-step">
            <div className="traxdb-row-between">
              <div>
                <strong>Verify integrity</strong>
                <p className="muted small">
                  Check local files match Pixeldrain. Run after download to confirm everything saved.
                  {summary.files_total != null && (
                    <> Last run: {summary.files_ok ?? 0} ok, {summary.files_missing ?? 0} missing.</>
                  )}
                </p>
              </div>
              <button
                className="btn btn-sm"
                onClick={onAudit}
                disabled={auditRunning || auditPending}
              >
                {auditRunning ? 'Auditing…' : 'Run Audit'}
              </button>
            </div>
            {latestAudit?.status === 'failed' && (
              <div className="traxdb-error">{latestAudit.error_message}</div>
            )}
          </div>

          {/* Folder browser */}
          <div className="traxdb-flow-step">
            <div className="traxdb-row-between">
              <div>
                <strong>Scraped Archive</strong>
                <p className="muted small">{total} lists in DB.</p>
              </div>
              <button className="btn btn-sm" onClick={() => setShowFolders(s => !s)}>
                {showFolders ? 'Hide' : 'Browse'}
              </button>
            </div>
            {showFolders && (
              <table className="traxdb-link-table">
                <thead>
                  <tr>
                    <th>List</th>
                    <th>Date</th>
                    <th>Tracks</th>
                    <th>Status</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {folders.map(f => (
                    <tr key={f.id}>
                      <td className="mono">{f.folder_id}</td>
                      <td>{f.inferred_date || '—'}</td>
                      <td className="mono">{f.tracks_downloaded}/{f.tracks_count}</td>
                      <td>
                        <span className={`status-badge status-badge--${f.download_status}`}>
                          {f.download_status}
                        </span>
                      </td>
                      <td>
                        {f.pixeldrain_url && (
                          <a href={f.pixeldrain_url} target="_blank" rel="noopener noreferrer" className="traxdb-link">
                            ↗
                          </a>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          <p className="muted small">{opsCount} operations recorded.</p>
        </div>
      )}
    </div>
  )
}

/* ── Main panel ── */

function TraxDBPanel() {
  const { data: inventory } = useTraxDBInventory()
  const { data: opsData } = useTraxDBOperations()
  const triggerSync = useTriggerSync()
  const triggerDownload = useTriggerDownload()
  const triggerAudit = useTriggerAudit()
  const cancelDownload = useCancelTraxDBDownload()

  const ops = opsData?.results || []
  const latestSyncStub = useMemo(() => ops.find(o => o.op_type === 'sync'), [ops])
  const latestDownloadStub = useMemo(() => ops.find(o => o.op_type === 'download'), [ops])
  const latestAuditStub = useMemo(() => ops.find(o => o.op_type === 'audit'), [ops])

  // Fetch full detail (with summary JSON) for the latest of each kind. The
  // operations LIST endpoint omits `summary` to keep the payload small.
  const { data: latestSyncDetail } = useTraxDBOperation(latestSyncStub?.id)
  const { data: latestDownloadDetail } = useTraxDBOperation(latestDownloadStub?.id)
  const { data: latestAuditDetail } = useTraxDBOperation(latestAuditStub?.id)

  const latestSync = latestSyncDetail || latestSyncStub
  const latestDownload = latestDownloadDetail || latestDownloadStub
  const latestAudit = latestAuditDetail || latestAuditStub

  const syncRunning = latestSync?.status === 'running' || latestSync?.status === 'pending'
  const downloadRunning = latestDownload?.status === 'running' || latestDownload?.status === 'pending'
  const auditRunning = latestAudit?.status === 'running' || latestAudit?.status === 'pending'

  return (
    <div className="traxdb-panel">
      <div className="traxdb-header">
        <h1 className="page-title">TraxDB</h1>
      </div>

      <LibraryOverview
        inventory={inventory}
        latestSync={latestSync}
        latestDownload={latestDownload}
      />

      <CheckAndDownload
        latestSync={latestSync}
        syncRunning={syncRunning}
        onCheck={() => triggerSync.mutate({})}
        syncPending={triggerSync.isPending}
        latestDownload={latestDownload}
        downloadRunning={downloadRunning}
        onDownload={() => triggerDownload.mutate({})}
        onCancel={(id) => cancelDownload.mutate(id)}
        downloadPending={triggerDownload.isPending}
        inventory={inventory}
      />

      <Advanced
        latestAudit={latestAudit}
        auditRunning={auditRunning}
        onAudit={() => triggerAudit.mutate({})}
        auditPending={triggerAudit.isPending}
        opsCount={ops.length}
      />
    </div>
  )
}

export default TraxDBPanel
