import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  useTraxDBInventory,
  useTraxDBOperations,
  useTraxDBOperation,
  useTriggerSync,
  useTriggerDownload,
  useTriggerAudit,
  useRetryFailedFolders,
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

function formatDuration(minutes) {
  if (minutes < 60) return `${minutes} min`
  const h = Math.floor(minutes / 60)
  const m = minutes % 60
  return m ? `${h}h ${m}m` : `${h}h`
}

/* ── library tiles + blog-vs-library freshness ── */

function LibraryOverview({ inventory, latestSync }) {
  if (!inventory) return null

  // The question this page exists to answer is "does the blog have something I
  // don't?". Both halves are known here, so state it outright instead of
  // making the operator compare a tile against a list of pending rows.
  // `latest_known_list_date` is the newest list past checks have *seen* on the
  // blog, not a live read of it — so state both dates and let them speak,
  // rather than asserting the archive is complete.
  const libraryLatest = inventory.latest_date
  const knownLatest = inventory.latest_known_list_date
  let freshness = null
  if (knownLatest && libraryLatest) {
    freshness = knownLatest > libraryLatest
      ? { tone: 'behind', text: `Newest list seen on the blog: ${knownLatest}. Newest date folder on the Mac: ${libraryLatest}.` }
      : { tone: 'level', text: `Newest list seen on the blog is ${knownLatest}, and the Mac has that date. Nothing newer has turned up.` }
  }

  return (
    <div className="traxdb-section traxdb-section--inventory">
      <div className="traxdb-section-body">
        <div className="traxdb-summary">
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{inventory.file_count?.toLocaleString()}</div>
            <div className="traxdb-stat-label">Files</div>
          </div>
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{formatBytes(inventory.total_bytes)}</div>
            <div className="traxdb-stat-label">Size</div>
          </div>
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{inventory.date_dirs_count ?? '—'}</div>
            <div className="traxdb-stat-label">Date folders</div>
          </div>
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{libraryLatest || '—'}</div>
            <div className="traxdb-stat-label">Newest in library</div>
          </div>
        </div>
        {freshness && (
          <div className={`traxdb-freshness traxdb-freshness--${freshness.tone}`}>
            {freshness.text}
          </div>
        )}
        <div className="traxdb-inventory-meta">
          <span>Blog checked {latestSync?.updated ? timeAgo(latestSync.updated) : 'never'}</span>
          {inventory.archive_location === 'mac' && (
            <>
              <span> · </span>
              <span>Mac reported {inventory.reported_at ? timeAgo(inventory.reported_at) : 'never'}</span>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

/* ── the one manual action: ask the blog what's new ── */

function CheckBar({ latestSync, syncRunning, onCheck, syncPending }) {
  const summary = latestSync?.summary || {}
  return (
    <div className="traxdb-checkbar">
      <button
        className="btn btn-accent btn-lg"
        onClick={onCheck}
        disabled={syncRunning || syncPending}
      >
        {syncRunning ? 'Checking blog…' : 'Check blog for new lists'}
      </button>
      {syncRunning && <span className="traxdb-spinner" />}
      {!syncRunning && latestSync?.status === 'completed' && (
        <span className="traxdb-checkbar-note">
          Last check found {summary.links_new_count ?? 0} new
          {' '}of {summary.links_found_count ?? 0} list
          {(summary.links_found_count ?? 0) === 1 ? '' : 's'} on the blog.
        </span>
      )}
      {!syncRunning && latestSync?.status === 'failed' && (
        <span className="traxdb-error">{latestSync.error_message || 'Check failed'}</span>
      )}
    </div>
  )
}

/* ── the queue: what is downloading, what is waiting, what broke ── */

// The Mac leases a batch and downloads it one list at a time, and a list's
// tracks only flip to downloaded when the whole folder lands — so there is no
// honest per-list progress to draw, and "claimed" is the strongest thing that
// can be said about a leased list. A percentage bar here would be decoration
// pretending to be telemetry.
const STATE_LABEL = {
  claimed: 'on the mac',
  stalled: 'stalled',
  waiting: 'waiting',
  blocked: 'blocked',
  failed: 'failed',
}

const STATE_NOTE = {
  claimed: 'the daemon has this one',
  stalled: 'claimed but abandoned — will be re-offered',
  blocked: 'that date already exists on the Mac',
}

function QueueRow({ folder }) {
  const state = folder.queue_state || folder.download_status
  const total = folder.tracks_count || 0
  const note = folder.last_error || STATE_NOTE[state] || ''

  return (
    <li className={`traxdb-queue-row traxdb-queue-row--${state}`}>
      <span className={`traxdb-dot traxdb-dot--${state}`} />
      <span className="traxdb-queue-date">{folder.inferred_date || 'no date'}</span>
      <span className="mono traxdb-queue-id">{folder.folder_id}</span>
      <span className="traxdb-queue-state">{STATE_LABEL[state] || state}</span>
      <span className="traxdb-queue-note" title={note}>{note}</span>
      <span className="mono traxdb-queue-count">{total} track{total === 1 ? '' : 's'}</span>
    </li>
  )
}

function Queue({
  inventory, latestDownload, downloadRunning,
  onDownload, downloadPending, onCancel,
  onRetryFailed, retryPending,
}) {
  const [showAll, setShowAll] = useState(false)

  // One request for the whole in-flight queue, and the chips are counted off
  // these same rows. Reading the counts from the inventory endpoint instead
  // would let a chip and the row it counts come from snapshots taken seconds
  // apart, which is how you get "0 waiting" above a list of waiting rows.
  const { data, isLoading, isError } = useTraxDBFolders(
    { download_status: 'downloading,pending,failed', limit: 200 },
    { refetchInterval: 15000 },
  )
  const rows = data?.results || []
  const truncated = (data?.total ?? rows.length) > rows.length

  const location = inventory?.archive_location
  const daemon = inventory?.daemon || {}

  const stateOf = (f) => f.queue_state || f.download_status

  const counts = useMemo(() => {
    const c = { claimed: 0, stalled: 0, waiting: 0, blocked: 0, failed: 0 }
    for (const f of rows) {
      const s = stateOf(f)
      if (s in c) c[s] += 1
    }
    return c
  }, [rows])

  // Active work first, then what needs a decision, then the backlog — reading
  // order matches "what is happening / what needs me / what is coming".
  const ordered = useMemo(() => {
    const rank = { claimed: 0, stalled: 1, failed: 2, blocked: 3, waiting: 4 }
    return [...rows].sort((a, b) =>
      (rank[stateOf(a)] ?? 9) - (rank[stateOf(b)] ?? 9) ||
      (b.inferred_date || '').localeCompare(a.inferred_date || ''))
  }, [rows])
  const visible = showAll ? ordered : ordered.slice(0, 6)

  // Only quote an ETA when the daemon has told us its own cadence. The interval
  // lives in a launchd plist and the batch size in a Mac env var; a number
  // invented here would be the one figure the operator plans around, and wrong
  // the moment either changes. Blocked lists are excluded — they are never
  // handed out, so counting them makes a queue that never appears to clear.
  const claimable = counts.claimed + counts.stalled + counts.waiting
  const intervalMin = daemon.interval_seconds ? Math.round(daemon.interval_seconds / 60) : null
  const batch = daemon.batch_limit || null
  const etaMin = (claimable > 0 && intervalMin && batch)
    ? Math.ceil(claimable / batch) * intervalMin
    : null

  const chip = (key, label) => counts[key] > 0 && (
    <span className={`traxdb-chip traxdb-chip--${key}`}>
      <span className={`traxdb-dot traxdb-dot--${key}`} />
      {counts[key]} {label}
    </span>
  )

  return (
    <div className="traxdb-section">
      <div className="traxdb-section-header">
        <h3>Queue</h3>
        <div className="traxdb-chips">
          {chip('claimed', 'on the Mac')}
          {chip('waiting', 'waiting')}
          {chip('stalled', 'stalled')}
          {chip('blocked', 'blocked')}
          {chip('failed', 'failed')}
          {!isLoading && !isError && ordered.length === 0 && (
            <span className="traxdb-chip">nothing queued</span>
          )}
        </div>
      </div>

      <div className="traxdb-section-body">
        {/* A daemon that stopped checking in is the difference between "these
            are being fetched" and "these will sit here forever" — the exact
            ambiguity this panel used to hide. */}
        {location === 'mac' && daemon.overdue && (
          <div className="traxdb-banner traxdb-banner--warn">
            {/* One element, not loose text nodes — the banner is a flex row and
                bare text between the <span className="mono"> bits would each
                become its own flex item, scattering the sentence. */}
            <span>
              <strong>The Mac daemon is overdue{daemon.last_seen ? ` — last report ${timeAgo(daemon.last_seen)}` : ''}.</strong>{' '}
              Nothing here downloads until it checks in again. Check{' '}
              <span className="mono">launchctl list | grep ocdj-traxdb</span> and{' '}
              <span className="mono">~/Library/Logs/ocdj-traxdb-local.out.log</span>.
            </span>
          </div>
        )}

        {counts.failed > 0 && (
          <div className="traxdb-banner traxdb-banner--error">
            <span>
              <strong>{counts.failed} list{counts.failed === 1 ? '' : 's'} failed.</strong>{' '}
              The reason each gave is on its row. Fix the cause first — re-queuing a
              list whose Pixeldrain link is simply dead will only fail again.
            </span>
            <button className="btn btn-sm" onClick={onRetryFailed} disabled={retryPending}>
              {retryPending ? 'Re-queuing…' : 'Retry failed'}
            </button>
          </div>
        )}

        {counts.blocked > 0 && (
          <div className="traxdb-banner traxdb-banner--warn">
            <span>
              <strong>{counts.blocked} list{counts.blocked === 1 ? '' : 's'} can't be handed out.</strong>{' '}
              Their date folders already exist on the Mac, and a date folder is never
              written into twice. They stay here until you remove the folder or the row.
            </span>
          </div>
        )}

        {isLoading ? (
          <div className="traxdb-result traxdb-result--neutral">Loading queue…</div>
        ) : isError ? (
          <div className="traxdb-error">Couldn't load the queue.</div>
        ) : ordered.length === 0 ? (
          <div className="traxdb-result traxdb-result--neutral">
            No lists are waiting, claimed, or failed.
          </div>
        ) : (
          <>
            <ul className="traxdb-queue">
              {visible.map(f => <QueueRow key={f.id} folder={f} />)}
            </ul>
            {ordered.length > 6 && (
              <button className="btn btn-sm" onClick={() => setShowAll(s => !s)}>
                {showAll ? 'Show less' : `Show all ${ordered.length}`}
              </button>
            )}
            {truncated && (
              <p className="muted small">
                Showing the first {rows.length} of {data.total}.
              </p>
            )}
          </>
        )}

        {/* Who does the work. In Mac mode nobody presses anything — saying so
            is the whole point, since the old panel showed a Download button
            the server refuses with a 409 in this mode. */}
        {location === 'mac' ? (
          <div className="traxdb-worker">
            <span className="traxdb-worker-label">Mac daemon</span>
            <span>
              fetches these from Pixeldrain on its own schedule
              {intervalMin && batch ? ` — up to ${batch} list${batch === 1 ? '' : 's'} every ${intervalMin} min` : ''}.
              {' '}Last report {daemon.last_seen ? timeAgo(daemon.last_seen) : 'never'}.
              {etaMin ? ` At that rate the queue clears in about ${formatDuration(etaMin)}.` : ''}
            </span>
          </div>
        ) : location === 'vps' ? (
          <div className="traxdb-flow-step">
            {downloadRunning ? (
              <DownloadProgress latestDownload={latestDownload} onCancel={onCancel} downloadPending={downloadPending} />
            ) : (
              <button
                className="btn btn-accent btn-lg"
                onClick={onDownload}
                disabled={downloadPending || claimable === 0}
              >
                Download {claimable} to this server
              </button>
            )}
            {latestDownload?.status === 'failed' && (
              <div className="traxdb-error">{latestDownload.error_message || 'Download failed'}</div>
            )}
          </div>
        ) : null}
      </div>
    </div>
  )
}

/* ── server-side download progress (VPS mode only) ── */

function DownloadProgress({ latestDownload, onCancel, downloadPending }) {
  const { data: progressResp } = useTraxDBDownloadProgress(latestDownload?.id)
  const progress = progressResp?.progress || progressResp || {}
  const listsTotal = progress.lists_total || 0
  const listsCompleted = progress.lists_completed || 0
  const filesTotal = progress.files_total || 0
  const filesCompleted = progress.files_completed || 0
  const bytesTotal = progress.bytes_total || 0
  const bytesDownloaded = progress.bytes_downloaded || 0
  const pct = filesTotal > 0
    ? Math.round((filesCompleted / filesTotal) * 100)
    : (listsTotal > 0 ? Math.round((listsCompleted / listsTotal) * 100) : 0)

  return (
    <>
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
    </>
  )
}

/* ── advanced (audit + archive) collapsed by default ── */

function Advanced({ latestAudit, auditRunning, onAudit, auditPending, opsCount }) {
  const [open, setOpen] = useState(false)
  const [showFolders, setShowFolders] = useState(false)
  // Collapsed by default and behind a second click — no reason to pull 100
  // folder rows before either has happened.
  const { data: foldersData } = useTraxDBFolders(
    { limit: 100 }, { enabled: open && showFolders })
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
  const retryFailed = useRetryFailedFolders()
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

  // Sync and download run in the Huey worker, so the trigger mutation resolves
  // long before any row exists. Only the operations poll knows when the job
  // actually finished — refresh the folder rows and inventory tiles off that
  // transition. Without this a completed sync leaves the panel showing its
  // mount-time snapshot: "no new lists pending" next to a DB full of them.
  const qc = useQueryClient()
  const opWatermark = `${latestSync?.id}:${latestSync?.status}|${latestDownload?.id}:${latestDownload?.status}`
  const prevWatermark = useRef(null)
  useEffect(() => {
    // Only on a real transition. Firing on mount too would just duplicate the
    // fetch those queries are already making.
    if (prevWatermark.current !== null && prevWatermark.current !== opWatermark) {
      qc.invalidateQueries({ queryKey: ['traxdb-folders'] })
      qc.invalidateQueries({ queryKey: ['traxdb-inventory'] })
    }
    prevWatermark.current = opWatermark
  }, [opWatermark, qc])

  return (
    <div className="traxdb-panel">
      <div className="traxdb-header">
        <h1 className="page-title">TraxDB</h1>
        <CheckBar
          latestSync={latestSync}
          syncRunning={syncRunning}
          onCheck={() => triggerSync.mutate({})}
          syncPending={triggerSync.isPending}
        />
      </div>

      <LibraryOverview inventory={inventory} latestSync={latestSync} />

      <Queue
        inventory={inventory}
        latestDownload={latestDownload}
        downloadRunning={downloadRunning}
        onDownload={() => triggerDownload.mutate({})}
        downloadPending={triggerDownload.isPending}
        onCancel={(id) => cancelDownload.mutate(id)}
        onRetryFailed={() => retryFailed.mutate()}
        retryPending={retryFailed.isPending}
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
