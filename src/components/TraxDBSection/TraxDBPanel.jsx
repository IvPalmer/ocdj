import { useState, useMemo } from 'react'
import {
  useTraxDBInventory,
  useTraxDBOperations,
  useTriggerSync,
  useTriggerDownload,
  useTriggerAudit,
  useTraxDBDownloadProgress,
  useCancelTraxDBDownload,
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
  const arr = summary[arrayKey]
  if (Array.isArray(arr)) return arr.length
  return summary[countKey] ?? summary[arrayKey] ?? '?'
}

function summarySentence(op) {
  const s = op.summary || {}
  if (op.op_type === 'sync') {
    const found = countField(s, 'links_found', 'links_found_count')
    const newLinks = countField(s, 'links_new', 'links_new_count')
    return `${newLinks} new / ${found} on page`
  }
  if (op.op_type === 'download') {
    const files = s.files_completed ?? s.files_total ?? '?'
    const bytes = s.bytes_downloaded ? formatBytes(s.bytes_downloaded) : ''
    return `${files} files${bytes ? ` (${bytes})` : ''}`
  }
  if (op.op_type === 'audit') {
    const ok = s.files_ok ?? '?'
    const missing = s.files_missing ?? 0
    return `${ok} ok, ${missing} missing`
  }
  return ''
}

/* ── detail components ── */

function LinkTable({ links, title, emptyText, isNew }) {
  const [expanded, setExpanded] = useState(false)

  if (!links || links.length === 0) {
    return emptyText ? <p className="traxdb-empty">{emptyText}</p> : null
  }

  return (
    <div className={`traxdb-detail ${isNew ? 'traxdb-detail--new' : ''}`}>
      <div className="traxdb-detail-header" onClick={() => setExpanded(e => !e)}>
        <span className="traxdb-detail-title">
          {title} <span className="traxdb-detail-count">({links.length})</span>
        </span>
        <span className={`traxdb-history-toggle ${expanded ? 'traxdb-history-toggle--open' : ''}`}>
          &#9654;
        </span>
      </div>
      {expanded && (
        <div className="traxdb-detail-body">
          <table className="traxdb-link-table">
            <thead>
              <tr>
                <th>List ID</th>
                <th>Date</th>
                <th>Files</th>
                <th>Link</th>
              </tr>
            </thead>
            <tbody>
              {links.map((link, i) => (
                <tr key={link.list_id || i}>
                  <td className="mono">{link.list_id || '\u2014'}</td>
                  <td>{link.inferred_date || '\u2014'}</td>
                  <td className="mono">
                    {link.files ? link.files.length : link.file_count ?? '\u2014'}
                  </td>
                  <td>
                    {link.pixeldrain_url ? (
                      <a
                        href={link.pixeldrain_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="traxdb-link"
                      >
                        pixeldrain &#8599;
                      </a>
                    ) : '\u2014'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function AuditListsDetail({ lists, deadLinks, errors }) {
  const [expanded, setExpanded] = useState(false)

  const hasData = (lists && lists.length > 0) || (deadLinks && deadLinks.length > 0)
  if (!hasData) return null

  return (
    <div className="traxdb-detail">
      <div className="traxdb-detail-header" onClick={() => setExpanded(e => !e)}>
        <span className="traxdb-detail-title">
          Audit Details <span className="traxdb-detail-count">({(lists || []).length} lists)</span>
        </span>
        <span className={`traxdb-history-toggle ${expanded ? 'traxdb-history-toggle--open' : ''}`}>
          &#9654;
        </span>
      </div>
      {expanded && (
        <div className="traxdb-detail-body">
          {(lists || []).map((list, i) => {
            const files = list.files || []
            const missing = files.filter(f => f.status === 'missing')
            const mismatch = files.filter(f => f.status === 'size_mismatch')
            const ok = files.filter(f => f.status === 'ok')
            const hasIssues = missing.length > 0 || mismatch.length > 0

            return (
              <div key={list.list_id || i} className={`traxdb-audit-list ${hasIssues ? 'traxdb-audit-list--issues' : ''}`}>
                <div className="traxdb-audit-list-header">
                  <span className="mono">{list.list_id || `List ${i + 1}`}</span>
                  <span className="traxdb-audit-badges">
                    {ok.length > 0 && <span className="audit-badge audit-badge--ok">{ok.length} ok</span>}
                    {missing.length > 0 && <span className="audit-badge audit-badge--missing">{missing.length} missing</span>}
                    {mismatch.length > 0 && <span className="audit-badge audit-badge--mismatch">{mismatch.length} mismatch</span>}
                  </span>
                </div>
                {hasIssues && (
                  <div className="traxdb-audit-files">
                    {[...missing, ...mismatch].map((file, fi) => (
                      <div key={fi} className={`traxdb-audit-file traxdb-audit-file--${file.status}`}>
                        <span className={`audit-file-status audit-file-status--${file.status}`}>
                          {file.status === 'missing' ? '\u2717' : '\u26A0'}
                        </span>
                        <span className="traxdb-audit-filename">{file.name || file.filename || '(unknown)'}</span>
                        {file.expected_size && (
                          <span className="traxdb-audit-size">
                            {file.status === 'size_mismatch'
                              ? `${formatBytes(file.local_size)} / ${formatBytes(file.expected_size)}`
                              : formatBytes(file.expected_size)}
                          </span>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}

          {deadLinks && deadLinks.length > 0 && (
            <div className="traxdb-dead-links">
              <div className="traxdb-dead-links-title">Dead Links ({deadLinks.length})</div>
              {deadLinks.map((dl, i) => (
                <div key={i} className="traxdb-dead-link">
                  <span className="mono">{dl.list_id || dl.url || dl}</span>
                  {dl.error && <span className="traxdb-dead-link-error">{dl.error}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function DownloadListsDetail({ lists, deadLinks, errors }) {
  const [expanded, setExpanded] = useState(false)

  const hasData = (lists && lists.length > 0) || (deadLinks && deadLinks.length > 0)
  if (!hasData) return null

  return (
    <div className="traxdb-detail">
      <div className="traxdb-detail-header" onClick={() => setExpanded(e => !e)}>
        <span className="traxdb-detail-title">
          Download Details <span className="traxdb-detail-count">({(lists || []).length} lists)</span>
        </span>
        <span className={`traxdb-history-toggle ${expanded ? 'traxdb-history-toggle--open' : ''}`}>
          &#9654;
        </span>
      </div>
      {expanded && (
        <div className="traxdb-detail-body">
          {(lists || []).map((list, i) => {
            const files = list.files || []
            return (
              <div key={list.list_id || i} className="traxdb-download-list">
                <div className="traxdb-download-list-header">
                  <span className="mono">{list.list_id || `List ${i + 1}`}</span>
                  <span className="traxdb-download-list-meta">
                    {files.length} files
                    {list.bytes_downloaded ? ` \u00B7 ${formatBytes(list.bytes_downloaded)}` : ''}
                    {list.status && list.status !== 'completed' && (
                      <span className={`audit-badge audit-badge--${list.status === 'dead' ? 'missing' : 'ok'}`}>
                        {list.status}
                      </span>
                    )}
                  </span>
                </div>
                {files.length > 0 && (
                  <div className="traxdb-download-files">
                    {files.map((file, fi) => (
                      <div key={fi} className="traxdb-download-file">
                        <span className="traxdb-download-filename">{file.name || file.filename || '(unknown)'}</span>
                        {file.size && <span className="mono traxdb-download-filesize">{formatBytes(file.size)}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )
          })}

          {deadLinks && deadLinks.length > 0 && (
            <div className="traxdb-dead-links">
              <div className="traxdb-dead-links-title">Dead Links ({deadLinks.length})</div>
              {deadLinks.map((dl, i) => (
                <div key={i} className="traxdb-dead-link">
                  <span className="mono">{dl.list_id || dl.url || dl}</span>
                  {dl.error && <span className="traxdb-dead-link-error">{dl.error}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ErrorsDetail({ errors }) {
  const [expanded, setExpanded] = useState(false)

  if (!errors || errors.length === 0) return null

  return (
    <div className="traxdb-detail traxdb-detail--errors">
      <div className="traxdb-detail-header" onClick={() => setExpanded(e => !e)}>
        <span className="traxdb-detail-title traxdb-detail-title--error">
          Errors <span className="traxdb-detail-count">({errors.length})</span>
        </span>
        <span className={`traxdb-history-toggle ${expanded ? 'traxdb-history-toggle--open' : ''}`}>
          &#9654;
        </span>
      </div>
      {expanded && (
        <div className="traxdb-detail-body">
          {errors.map((err, i) => (
            <div key={i} className="traxdb-error-item">
              {err.list && (
                <span className="traxdb-error-list-id">{err.list.list_id || err.list_id}: </span>
              )}
              {err.list_id && !err.list && (
                <span className="traxdb-error-list-id">{err.list_id}: </span>
              )}
              <span className="traxdb-error-message">
                {typeof err === 'string' ? err : (err.message || err.error || JSON.stringify(err))}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/* ── section components ── */

function InventoryOverview({ inventory }) {
  if (!inventory) return null

  return (
    <div className="traxdb-section traxdb-section--inventory">
      <div className="traxdb-section-header">
        <h3>Local Library</h3>
      </div>
      <div className="traxdb-section-body">
        <div className="traxdb-summary">
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{inventory.known_lists_count}</div>
            <div className="traxdb-stat-label">Pixeldrain Lists</div>
          </div>
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{inventory.date_dirs_count}</div>
            <div className="traxdb-stat-label">Date Folders</div>
          </div>
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{inventory.file_count?.toLocaleString()}</div>
            <div className="traxdb-stat-label">Files</div>
          </div>
          <div className="traxdb-stat">
            <div className="traxdb-stat-value">{formatBytes(inventory.total_bytes)}</div>
            <div className="traxdb-stat-label">Total Size</div>
          </div>
        </div>
        <div className="traxdb-inventory-meta">
          {inventory.oldest_date && inventory.latest_date && (
            <span>Spanning {inventory.oldest_date} to {inventory.latest_date}</span>
          )}
        </div>
      </div>
    </div>
  )
}

function SyncSection({ latestSync, isRunning, onTrigger, isPending, inventory }) {
  const summary = latestSync?.summary || {}
  const linksNew = Array.isArray(summary.links_new) ? summary.links_new : []
  const linksFound = Array.isArray(summary.links_found) ? summary.links_found : []
  const errors = Array.isArray(summary.errors) ? summary.errors : []
  const newCount = countField(summary, 'links_new', 'links_new_count')
  const foundCount = countField(summary, 'links_found', 'links_found_count')
  const alreadyInLib = typeof foundCount === 'number' && typeof newCount === 'number'
    ? foundCount - newCount
    : 0

  return (
    <div className="traxdb-section">
      <div className="traxdb-section-header">
        <h3><span className="step-badge">1</span> Check for New</h3>
        <div className="traxdb-actions">
          {isRunning && (
            <span className="traxdb-status traxdb-status--running">
              <span className="traxdb-spinner" /> Scanning blog...
            </span>
          )}
          {!isRunning && latestSync?.status === 'completed' && (
            <span className="traxdb-status traxdb-status--completed">
              {timeAgo(latestSync.updated)}
            </span>
          )}
          <button
            className="btn btn-accent btn-sm"
            onClick={() => onTrigger()}
            disabled={isRunning || isPending}
          >
            {isRunning ? 'Scanning...' : 'Check for New'}
          </button>
        </div>
      </div>
      <div className="traxdb-section-body">
        <p className="traxdb-section-desc">
          Scans the blog for new Pixeldrain lists since the last download
          {inventory?.latest_date ? ` (${inventory.latest_date})` : ''}.
          This is an incremental check — it only looks for new content, not your full history
          of {inventory?.known_lists_count ?? '?'} lists.
        </p>

        {latestSync?.status === 'failed' && (
          <div className="traxdb-error">{latestSync.error_message || 'Sync failed'}</div>
        )}
        {latestSync?.status === 'completed' && (newCount > 0 || foundCount > 0) ? (
          <>
            <div className="traxdb-summary">
              <div className="traxdb-stat traxdb-stat--accent">
                <div className="traxdb-stat-value">{newCount}</div>
                <div className="traxdb-stat-label">New to Download</div>
              </div>
              <div className="traxdb-stat">
                <div className="traxdb-stat-value">{alreadyInLib}</div>
                <div className="traxdb-stat-label">Already in Library</div>
              </div>
              <div className="traxdb-stat">
                <div className="traxdb-stat-value">{foundCount}</div>
                <div className="traxdb-stat-label">On Blog Page</div>
              </div>
            </div>

            {/* Detail sections */}
            {linksNew.length > 0 && (
              <LinkTable links={linksNew} title="New Lists (ready to download)" isNew />
            )}
            {linksFound.length > linksNew.length && (
              <LinkTable links={linksFound} title="All Lists on Blog Page" />
            )}
            <ErrorsDetail errors={errors} />
          </>
        ) : (
          !isRunning && !latestSync?.status && (
            <p className="traxdb-empty">Click "Check for New" to scan the blog for new Pixeldrain lists.</p>
          )
        )}
        {latestSync?.status === 'completed' && newCount === 0 && foundCount > 0 && (
          <p className="traxdb-empty traxdb-empty--good">
            All {foundCount} lists on the blog page are already in your library. You're up to date!
          </p>
        )}
      </div>
    </div>
  )
}

function DownloadSection({ latestDownload, latestSync, isRunning, onTrigger, onCancel, isPending }) {
  const progressId = isRunning ? latestDownload?.id : null
  const { data: progress } = useTraxDBDownloadProgress(progressId)
  const summary = latestDownload?.summary || {}
  const hasSyncReport = latestSync?.status === 'completed' && latestSync?.report_path
  const newCount = countField(latestSync?.summary || {}, 'links_new', 'links_new_count')

  // Calculate progress percentage
  const listsTotal = progress?.lists_total || 0
  const listsCompleted = progress?.lists_completed || 0
  const pct = listsTotal > 0 ? Math.round((listsCompleted / listsTotal) * 100) : 0

  const lists = Array.isArray(summary.lists) ? summary.lists : []
  const deadLinks = Array.isArray(summary.dead_links) ? summary.dead_links : []
  const errors = Array.isArray(summary.errors) ? summary.errors : []

  return (
    <div className="traxdb-section">
      <div className="traxdb-section-header">
        <h3><span className="step-badge">2</span> Download</h3>
        <div className="traxdb-actions">
          {isRunning && (
            <span className="traxdb-status traxdb-status--running">
              <span className="traxdb-spinner" /> Downloading...
            </span>
          )}
          {!isRunning && latestDownload?.status === 'completed' && (
            <span className="traxdb-status traxdb-status--completed">
              {timeAgo(latestDownload.updated)}
            </span>
          )}
          {isRunning && (
            <button
              className="btn btn-danger btn-sm"
              onClick={() => onCancel(latestDownload.id)}
              disabled={isPending}
            >
              Cancel
            </button>
          )}
          {!isRunning && (
            <button
              className="btn btn-accent btn-sm"
              onClick={() => onTrigger()}
              disabled={!hasSyncReport || newCount === 0 || isPending}
              title={!hasSyncReport ? 'Run a sync first' : newCount === 0 ? 'No new lists to download' : ''}
            >
              Download New
            </button>
          )}
        </div>
      </div>
      <div className="traxdb-section-body">
        {latestDownload?.status === 'failed' && (
          <div className="traxdb-error">{latestDownload.error_message || 'Download failed'}</div>
        )}

        {/* Live progress */}
        {isRunning && progress && listsTotal > 0 && (
          <div className="traxdb-progress">
            <div className="traxdb-progress-bar">
              <div className="traxdb-progress-fill" style={{ width: `${pct}%` }} />
            </div>
            <div className="traxdb-progress-meta">
              <span>Lists: {listsCompleted} / {listsTotal}</span>
              <span>{pct}%</span>
            </div>
            <div className="traxdb-progress-detail">
              {progress.files_completed != null && (
                <span>Files: {progress.files_completed}{progress.files_total ? ` / ${progress.files_total}` : ''}</span>
              )}
              {progress.bytes_downloaded != null && (
                <span>Downloaded: {formatBytes(progress.bytes_downloaded)}</span>
              )}
              {progress.current_list && (
                <span>Current: {progress.current_list}</span>
              )}
            </div>
          </div>
        )}

        {/* Completed summary */}
        {!isRunning && latestDownload?.status === 'completed' && Object.keys(summary).length > 0 && (
          <>
            <div className="traxdb-summary">
              <div className="traxdb-stat">
                <div className="traxdb-stat-value">{summary.lists_completed ?? 0}</div>
                <div className="traxdb-stat-label">Lists Done</div>
              </div>
              <div className="traxdb-stat">
                <div className="traxdb-stat-value">{summary.files_completed ?? 0}</div>
                <div className="traxdb-stat-label">Files</div>
              </div>
              <div className="traxdb-stat">
                <div className="traxdb-stat-value">{formatBytes(summary.bytes_downloaded)}</div>
                <div className="traxdb-stat-label">Downloaded</div>
              </div>
              {(summary.dead_links_count ?? 0) > 0 && (
                <div className="traxdb-stat">
                  <div className="traxdb-stat-value">{summary.dead_links_count}</div>
                  <div className="traxdb-stat-label">Dead Links</div>
                </div>
              )}
            </div>

            <DownloadListsDetail lists={lists} deadLinks={deadLinks} errors={errors} />
            <ErrorsDetail errors={errors} />
          </>
        )}

        {!isRunning && !latestDownload && (
          <p className="traxdb-empty">
            {hasSyncReport && newCount > 0
              ? `${newCount} new list${newCount !== 1 ? 's' : ''} ready to download from Pixeldrain.`
              : 'Run a sync first to find new content.'}
          </p>
        )}
      </div>
    </div>
  )
}

function AuditSection({ latestAudit, latestSync, isRunning, onTrigger, isPending }) {
  const summary = latestAudit?.summary || {}
  const hasSyncReport = latestSync?.status === 'completed' && latestSync?.report_path

  const lists = Array.isArray(summary.lists) ? summary.lists : []
  const deadLinks = Array.isArray(summary.dead_links) ? summary.dead_links : []
  const errors = Array.isArray(summary.errors) ? summary.errors : []

  // Count how many lists in the audit have missing files
  const auditListCount = lists.length
  const syncFoundCount = countField(latestSync?.summary || {}, 'links_found', 'links_found_count')

  return (
    <div className="traxdb-section">
      <div className="traxdb-section-header">
        <h3><span className="step-badge">3</span> Audit</h3>
        <div className="traxdb-actions">
          {isRunning && (
            <span className="traxdb-status traxdb-status--running">
              <span className="traxdb-spinner" /> Auditing files...
            </span>
          )}
          {!isRunning && latestAudit?.status === 'completed' && (
            <span className="traxdb-status traxdb-status--completed">
              {timeAgo(latestAudit.updated)}
            </span>
          )}
          <button
            className="btn btn-accent btn-sm"
            onClick={() => onTrigger()}
            disabled={isRunning || !hasSyncReport || isPending}
            title={!hasSyncReport ? 'Run a sync first' : ''}
          >
            {isRunning ? 'Auditing...' : 'Run Audit'}
          </button>
        </div>
      </div>
      <div className="traxdb-section-body">
        <p className="traxdb-section-desc">
          Verifies local files against Pixeldrain for the {syncFoundCount || '?'} lists
          found in the latest sync. Missing files = not yet downloaded or deleted.
          Run this after downloading to check integrity.
        </p>

        {latestAudit?.status === 'failed' && (
          <div className="traxdb-error">{latestAudit.error_message || 'Audit failed'}</div>
        )}
        {!isRunning && latestAudit?.status === 'completed' && Object.keys(summary).length > 0 && (
          <div className="traxdb-summary">
            <div className="traxdb-stat">
              <div className="traxdb-stat-value">{summary.files_ok ?? 0}</div>
              <div className="traxdb-stat-label">OK</div>
            </div>
            <div className="traxdb-stat">
              <div className="traxdb-stat-value">{summary.files_missing ?? 0}</div>
              <div className="traxdb-stat-label">Missing</div>
            </div>
            {(summary.files_size_mismatch ?? 0) > 0 && (
              <div className="traxdb-stat">
                <div className="traxdb-stat-value">{summary.files_size_mismatch}</div>
                <div className="traxdb-stat-label">Size Mismatch</div>
              </div>
            )}
            <div className="traxdb-stat">
              <div className="traxdb-stat-value">{summary.files_total ?? '?'}</div>
              <div className="traxdb-stat-label">Total Checked</div>
            </div>
          </div>
        )}
        {/* Show detail sections for both completed and failed (if data exists) */}
        {!isRunning && (latestAudit?.status === 'completed' || latestAudit?.status === 'failed') && (
          <>
            <AuditListsDetail lists={lists} deadLinks={deadLinks} errors={errors} />
            <ErrorsDetail errors={errors} />
          </>
        )}
        {!isRunning && !latestAudit?.status && (
          <p className="traxdb-empty">Run an audit after downloading to verify file integrity.</p>
        )}
      </div>
    </div>
  )
}

function OperationsHistory({ operations }) {
  const [expanded, setExpanded] = useState(false)

  if (!operations || operations.length === 0) return null

  return (
    <div className="traxdb-history">
      <div className="traxdb-history-header" onClick={() => setExpanded(e => !e)}>
        <h3>Operations History ({operations.length})</h3>
        <span className={`traxdb-history-toggle ${expanded ? 'traxdb-history-toggle--open' : ''}`}>
          &#9654;
        </span>
      </div>
      {expanded && (
        <table className="traxdb-history-table">
          <thead>
            <tr>
              <th>Type</th>
              <th>Status</th>
              <th>When</th>
              <th>Summary</th>
            </tr>
          </thead>
          <tbody>
            {operations.map(op => (
              <tr key={op.id}>
                <td>
                  <span className={`op-type-badge op-type-badge--${op.op_type}`}>
                    {op.op_type}
                  </span>
                </td>
                <td>
                  <span className={`status-badge status-badge--${op.status}`}>
                    {op.status === 'running' && <span className="traxdb-spinner" />}
                    {op.status}
                  </span>
                </td>
                <td>{timeAgo(op.created)}</td>
                <td className="traxdb-history-summary">
                  {op.status === 'failed'
                    ? (op.error_message || 'Failed').slice(0, 60)
                    : summarySentence(op)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
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

  // Derive latest ops per type
  const latestSync = useMemo(() => ops.find(o => o.op_type === 'sync'), [ops])
  const latestDownload = useMemo(() => ops.find(o => o.op_type === 'download'), [ops])
  const latestAudit = useMemo(() => ops.find(o => o.op_type === 'audit'), [ops])

  const syncRunning = latestSync?.status === 'running'
  const downloadRunning = latestDownload?.status === 'running'
  const auditRunning = latestAudit?.status === 'running'

  return (
    <div className="traxdb-panel">
      <div className="traxdb-header">
        <h1 className="page-title">TraxDB</h1>
      </div>

      <InventoryOverview inventory={inventory} />

      <div className="traxdb-workflow">
        <SyncSection
          latestSync={latestSync}
          isRunning={syncRunning}
          onTrigger={() => triggerSync.mutate()}
          isPending={triggerSync.isPending}
          inventory={inventory}
        />

        <DownloadSection
          latestDownload={latestDownload}
          latestSync={latestSync}
          isRunning={downloadRunning}
          onTrigger={() => triggerDownload.mutate()}
          onCancel={(id) => cancelDownload.mutate(id)}
          isPending={triggerDownload.isPending}
        />

        <AuditSection
          latestAudit={latestAudit}
          latestSync={latestSync}
          isRunning={auditRunning}
          onTrigger={() => triggerAudit.mutate()}
          isPending={triggerAudit.isPending}
        />
      </div>

      <OperationsHistory operations={ops} />
    </div>
  )
}

export default TraxDBPanel
