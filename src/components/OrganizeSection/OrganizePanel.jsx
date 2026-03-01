import { useState } from 'react'
import {
  usePipelineStats, usePipelineItems, useProcessPipeline,
  useProcessSingle, useRetryItem, useSkipStage, useScanDownloads,
} from '../../api/hooks'
import './OrganizePanel.css'

const STAGES = [
  { key: 'downloaded', label: 'Downloaded', color: 'var(--accent-blue)' },
  { key: 'tagging', label: 'Tagging', color: 'var(--accent-amber)' },
  { key: 'tagged', label: 'Tagged', color: 'var(--accent-green)' },
  { key: 'renaming', label: 'Renaming', color: 'var(--accent-amber)' },
  { key: 'renamed', label: 'Renamed', color: 'var(--accent-green)' },
  { key: 'ready', label: 'Ready', color: 'var(--accent-green)' },
  { key: 'failed', label: 'Failed', color: 'var(--accent-red)' },
]

function StageCard({ stage, count, isActive, onClick }) {
  const isProcessing = stage.key === 'tagging' || stage.key === 'renaming'
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

function OrganizePanel() {
  const [stageFilter, setStageFilter] = useState(null)

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
              <span className="col-file">File</span>
              <span className="col-meta">Artist / Title</span>
              <span className="col-stage">Stage</span>
              <span className="col-source">Source</span>
              <span className="col-actions">Actions</span>
            </div>
            {items.map(item => (
              <div key={item.id} className="pipeline-table__row">
                <span className="col-file" title={item.original_filename}>
                  {item.original_filename}
                </span>
                <span className="col-meta">
                  {item.artist && item.title
                    ? `${item.artist} - ${item.title}`
                    : item.artist || item.title || '—'}
                </span>
                <span className="col-stage">
                  <StagePill stage={item.stage} />
                </span>
                <span className="col-source">
                  {item.metadata_source || '—'}
                </span>
                <span className="col-actions">
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
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

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
