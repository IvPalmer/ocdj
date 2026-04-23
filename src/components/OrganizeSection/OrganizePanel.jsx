import { useRef, useState } from 'react'
import {
  usePipelineStats, usePipelineItems, useProcessPipeline,
  useProcessSingle, useRetryItem, useSkipStage, useScanDownloads,
  useUpdatePipelineItem, useRetagItem,
  useConversionRules, useUpdateConversionRules,
} from '../../api/hooks'
import './OrganizePanel.css'

const STAGES = [
  { key: 'downloaded', label: 'Downloaded', color: 'var(--accent-blue)' },
  { key: 'tagging', label: 'Tagging', color: 'var(--accent-amber)' },
  { key: 'tagged', label: 'Tagged', color: 'var(--accent-green)' },
  { key: 'renaming', label: 'Renaming', color: 'var(--accent-amber)' },
  { key: 'renamed', label: 'Renamed', color: 'var(--accent-green)' },
  { key: 'converting', label: 'Converting', color: 'var(--accent-amber)' },
  { key: 'converted', label: 'Converted', color: 'var(--accent-green)' },
  { key: 'ready', label: 'Ready', color: 'var(--accent-green)' },
  { key: 'failed', label: 'Failed', color: 'var(--accent-red)' },
]

const EDITABLE_FIELDS = [
  { key: 'artist', label: 'Artist' },
  { key: 'title', label: 'Title' },
  { key: 'album', label: 'Album' },
  { key: 'label', label: 'Label' },
  { key: 'catalog_number', label: 'Catalog #' },
  { key: 'genre', label: 'Genre' },
  { key: 'year', label: 'Year' },
  { key: 'track_number', label: 'Track #' },
]

const DOWNLOADABLE_STATES = new Set(['on_workbench', 'publishable', 'draining'])

const AUDIO_EXTS = new Set(['mp3', 'flac', 'wav', 'aiff', 'aif', 'm4a', 'ogg'])


function UploadButton({ onDone }) {
  const [working, setWorking] = useState(false)
  const [status, setStatus] = useState(null)
  const inputRef = useRef(null)

  const upload = async (fileList) => {
    const files = Array.from(fileList || [])
      .filter(f => AUDIO_EXTS.has(f.name.split('.').pop().toLowerCase()))
    if (files.length === 0) {
      setStatus('no audio files')
      return
    }
    setWorking(true)
    setStatus(`uploading ${files.length}…`)
    const form = new FormData()
    files.forEach(f => form.append('files', f))
    try {
      const resp = await fetch('/api/organize/pipeline/upload/?autoprocess=1', {
        method: 'POST',
        body: form,
      })
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        setStatus(`failed: ${body.error || resp.status}`)
        return
      }
      const data = await resp.json()
      setStatus(`created ${data.created.length}, skipped ${data.skipped.length}`)
      if (onDone) onDone(data)
    } catch (e) {
      setStatus(`error: ${e}`)
    } finally {
      setWorking(false)
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".mp3,.flac,.wav,.aiff,.aif,.m4a,.ogg,audio/*"
        style={{ display: 'none' }}
        onChange={(e) => upload(e.target.files)}
      />
      <button
        className="btn btn-sm"
        onClick={() => inputRef.current?.click()}
        disabled={working}
        title={status || 'Upload audio files into the pipeline'}
      >
        {working ? 'Uploading…' : 'Upload'}
      </button>
      {status && !working && <span style={{ fontSize: 11, opacity: 0.7, marginLeft: 8 }}>{status}</span>}
    </>
  )
}


function DownloadButton({ item }) {
  const [working, setWorking] = useState(false)
  const [err, setErr] = useState(null)

  const archived = item.archive_state === 'archived'
  const show = item.stage === 'ready' || item.stage === 'published' || archived
  if (!show) return null

  if (archived) {
    return (
      <span
        className="btn btn-xs"
        title="This track is on your home Mac; download no longer available"
        style={{ opacity: 0.6, cursor: 'default' }}
      >
        at home
      </span>
    )
  }

  if (!DOWNLOADABLE_STATES.has(item.archive_state)) return null

  const onClick = async () => {
    setErr(null)
    setWorking(true)
    try {
      const resp = await fetch(`/api/organize/pipeline/${item.id}/download-url/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      })
      if (resp.status === 410) {
        setErr('already on your home Mac')
        return
      }
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}))
        setErr(body.error || `HTTP ${resp.status}`)
        return
      }
      const data = await resp.json()
      // Use a temporary anchor click instead of window.location.assign —
      // doesn't pollute SPA history, plays nicer on iOS Safari, lets the
      // `download` attribute hint the filename to the browser.
      const a = document.createElement('a')
      a.href = data.url
      a.download = data.filename || ''
      a.rel = 'noreferrer'
      document.body.appendChild(a)
      a.click()
      a.remove()
    } catch (e) {
      setErr(String(e))
    } finally {
      setWorking(false)
    }
  }

  return (
    <button
      className="btn btn-xs"
      onClick={onClick}
      disabled={working}
      title={err || 'Stream file to this device'}
    >
      {working ? '…' : err ? 'retry' : 'Download'}
    </button>
  )
}


function StageCard({ stage, count, isActive, onClick }) {
  const isProcessing = stage.key === 'tagging' || stage.key === 'renaming' || stage.key === 'converting'
  return (
    <button
      className={`stage-card ${isActive ? 'stage-card--active' : ''} ${isProcessing && count > 0 ? 'stage-card--processing' : ''}`}
      onClick={onClick}
      style={{ '--stage-color': stage.color }}
    >
      <span className="stage-card__count">{count}</span>
      <span className="stage-card__label">{stage.label}</span>
    </button>
  )
}

function PipelineFlow({ stats, activeStage, onStageClick }) {
  return (
    <div className="pipeline-flow">
      {STAGES.map((stage, i) => (
        <div key={stage.key} className="pipeline-flow__step">
          <StageCard
            stage={stage}
            count={stats?.[stage.key] || 0}
            isActive={activeStage === stage.key}
            onClick={() => onStageClick(activeStage === stage.key ? null : stage.key)}
          />
          {i < STAGES.length - 1 && <span className="pipeline-flow__arrow">&rarr;</span>}
        </div>
      ))}
    </div>
  )
}

function StagePill({ stage }) {
  const stageInfo = STAGES.find(s => s.key === stage) || STAGES[0]
  return (
    <span className="stage-pill" style={{ '--pill-color': stageInfo.color }}>
      {stageInfo.label}
    </span>
  )
}

function EditModal({ item, onClose }) {
  const [form, setForm] = useState(() => {
    const init = {}
    EDITABLE_FIELDS.forEach(f => { init[f.key] = item[f.key] || '' })
    return init
  })
  const updateItem = useUpdatePipelineItem()
  const retagItem = useRetagItem()

  const handleSave = () => {
    updateItem.mutate({ id: item.id, ...form }, {
      onSuccess: () => onClose(),
    })
  }

  const handleSaveAndRetag = () => {
    updateItem.mutate({ id: item.id, ...form }, {
      onSuccess: () => {
        retagItem.mutate(item.id, {
          onSuccess: () => onClose(),
        })
      },
    })
  }

  const set = (key, val) => setForm(prev => ({ ...prev, [key]: val }))

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal edit-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>Edit Metadata</h3>
          <button className="btn-close" onClick={onClose} />
        </div>
        <div className="edit-modal__body">
          <div className="edit-modal__filename">{item.original_filename}</div>
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
            className="btn btn-sm"
            onClick={handleSave}
            disabled={updateItem.isPending}
          >
            Save
          </button>
          <button
            className="btn btn-sm btn-accent"
            onClick={handleSaveAndRetag}
            disabled={updateItem.isPending || retagItem.isPending}
          >
            {retagItem.isPending ? 'Applying...' : 'Save & Apply Tags'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ConversionRules() {
  const { data } = useConversionRules()
  const updateRules = useUpdateConversionRules()
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')

  const startEdit = () => {
    setDraft(data?.rules || '')
    setEditing(true)
  }

  const save = () => {
    updateRules.mutate(draft, { onSuccess: () => setEditing(false) })
  }

  return (
    <div className="organize-section">
      <div className="section-header">
        <h3 className="section-title">Conversion Rules</h3>
        {!editing && (
          <button className="btn btn-sm" onClick={startEdit}>Edit</button>
        )}
      </div>
      {editing ? (
        <div className="conversion-rules-editor">
          <textarea
            className="conversion-rules-textarea"
            value={draft}
            onChange={e => setDraft(e.target.value)}
            rows={8}
            placeholder="wav -> aiff&#10;flac -> aiff&#10;mp3>=320k -> keep&#10;mp3<320k -> skip"
          />
          <div className="conversion-rules-actions">
            <button className="btn btn-sm" onClick={() => setEditing(false)}>Cancel</button>
            <button
              className="btn btn-sm btn-accent"
              onClick={save}
              disabled={updateRules.isPending}
            >
              {updateRules.isPending ? 'Saving...' : 'Save'}
            </button>
          </div>
        </div>
      ) : (
        <pre className="conversion-rules-display">{data?.rules || 'Loading...'}</pre>
      )}
    </div>
  )
}

function OrganizePanel() {
  const [stageFilter, setStageFilter] = useState(null)
  const [editingItem, setEditingItem] = useState(null)

  const { data: stats } = usePipelineStats()
  const { data: itemsData } = usePipelineItems({ stage: stageFilter })
  const processPipeline = useProcessPipeline()
  const processSingle = useProcessSingle()
  const retryItem = useRetryItem()
  const skipStage = useSkipStage()
  const scanDownloads = useScanDownloads()

  const items = itemsData?.results || []

  return (
    <div className="organize-panel">
      <div className="organize-header">
        <h2 className="page-title">Organize</h2>
        <div className="organize-header__actions">
          <UploadButton onDone={() => scanDownloads.mutate()} />
          <button
            className="btn btn-sm"
            onClick={() => scanDownloads.mutate()}
            disabled={scanDownloads.isPending}
          >
            {scanDownloads.isPending ? 'Scanning...' : 'Scan Downloads'}
          </button>
          <button
            className="btn btn-sm btn-primary"
            onClick={() => processPipeline.mutate()}
            disabled={processPipeline.isPending || !stats?.downloaded}
          >
            {processPipeline.isPending ? 'Processing...' : 'Process All'}
          </button>
        </div>
      </div>

      <div className="organize-section">
        <h3 className="section-title">Pipeline</h3>
        <PipelineFlow
          stats={stats}
          activeStage={stageFilter}
          onStageClick={setStageFilter}
        />
        {stats?.total > 0 && (
          <div className="pipeline-summary">
            {stats.total} total items
          </div>
        )}
      </div>

      <ConversionRules />

      <div className="organize-section">
        <div className="section-header">
          <h3 className="section-title">
            {stageFilter ? `Items — ${STAGES.find(s => s.key === stageFilter)?.label}` : 'All Items'}
          </h3>
          {stageFilter && (
            <button className="btn btn-sm" onClick={() => setStageFilter(null)}>
              Show All
            </button>
          )}
        </div>

        {items.length === 0 ? (
          <div className="empty-state">
            {stageFilter
              ? `No items in "${STAGES.find(s => s.key === stageFilter)?.label}" stage`
              : 'No items in the pipeline. Use "Scan Downloads" to import completed downloads.'}
          </div>
        ) : (
          <div className="pipeline-table">
            <div className="pipeline-table__header">
              <span className="col-file">Current filename</span>
              <span className="col-meta">Tags</span>
              <span className="col-stage">Stage</span>
              <span className="col-source">Source</span>
              <span className="col-actions">Actions</span>
            </div>
            {items.map(item => {
              const currentName = item.final_filename
                || (item.current_path ? item.current_path.split('/').pop() : null)
                || item.original_filename
              const renamed = item.final_filename && item.original_filename
                && item.final_filename !== item.original_filename
              return (
              <div key={item.id} className="pipeline-table__row">
                <span className="col-file" title={item.current_path || currentName}>
                  <span className="col-file__name">{currentName}</span>
                  {renamed && (
                    <span className="col-file__original" title={`Originally: ${item.original_filename}`}>
                      was: {item.original_filename}
                    </span>
                  )}
                </span>
                <span className="col-meta">
                  {item.artist || item.title ? (
                    <>
                      <span className="col-meta__main">
                        {[item.artist, item.title].filter(Boolean).join(' — ')}
                      </span>
                      {(item.album || item.label) && (
                        <span className="col-meta__sub">
                          {[item.album, item.label].filter(Boolean).join(' · ')}
                        </span>
                      )}
                    </>
                  ) : '—'}
                </span>
                <span className="col-stage">
                  <StagePill stage={item.stage} />
                </span>
                <span className="col-source">
                  {item.metadata_source || '—'}
                </span>
                <span className="col-actions">
                  <button
                    className="btn btn-xs"
                    onClick={() => setEditingItem(item)}
                  >
                    Edit
                  </button>
                  {item.stage === 'downloaded' && (
                    <button
                      className="btn btn-xs"
                      onClick={() => processSingle.mutate(item.id)}
                      disabled={processSingle.isPending}
                    >
                      Process
                    </button>
                  )}
                  {item.stage === 'failed' && (
                    <button
                      className="btn btn-xs"
                      onClick={() => retryItem.mutate(item.id)}
                      disabled={retryItem.isPending}
                    >
                      Retry
                    </button>
                  )}
                  {!['ready', 'failed'].includes(item.stage) && !item.stage.endsWith('ing') && (
                    <button
                      className="btn btn-xs"
                      onClick={() => skipStage.mutate(item.id)}
                      disabled={skipStage.isPending}
                    >
                      Skip
                    </button>
                  )}
                  <DownloadButton item={item} />
                </span>
              </div>
              )
            })}
          </div>
        )}
      </div>

      {editingItem && (
        <EditModal item={editingItem} onClose={() => setEditingItem(null)} />
      )}

      {processPipeline.data?.message && (
        <div className="organize-toast">{processPipeline.data.message}</div>
      )}
      {scanDownloads.data?.message && (
        <div className="organize-toast">{scanDownloads.data.message}</div>
      )}
    </div>
  )
}

export default OrganizePanel
