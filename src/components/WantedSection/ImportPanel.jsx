import { useState } from 'react'
import {
  useImportOperation, useTriggerImport, useConfirmImport,
  useImportConfigStatus, useSpotifyStatus, useConfig,
} from '../../api/hooks'
import ImportPreview from './ImportPreview'
import './ImportPanel.css'

const DEFAULT_PLAYLIST_KEYS = {
  youtube: 'YOUTUBE_DEFAULT_PLAYLIST',
  soundcloud: 'SC_DEFAULT_PLAYLIST',
  spotify: 'SPOTIFY_DEFAULT_PLAYLIST',
}

const SOURCE_CARDS = [
  { type: 'youtube', label: 'YouTube', needsUrl: true },
  { type: 'soundcloud', label: 'SoundCloud', needsUrl: true },
  { type: 'spotify', label: 'Spotify', needsUrl: true },
  { type: 'discogs', label: 'Discogs', desc: 'Import your wantlist', needsUrl: false },
]

function ImportPanel({ onClose }) {
  const [step, setStep] = useState('select') // select | url | fetching | preview
  const [selectedType, setSelectedType] = useState(null)
  const [url, setUrl] = useState('')
  const [operationId, setOperationId] = useState(null)

  const { data: configStatus } = useImportConfigStatus()
  const { data: spotifyStatus } = useSpotifyStatus()
  const { data: configData } = useConfig()
  const { data: operation } = useImportOperation(operationId)
  const triggerImport = useTriggerImport()
  const confirmImport = useConfirmImport()

  const getDefaultPlaylist = (type) => {
    const key = DEFAULT_PLAYLIST_KEYS[type]
    if (!key || !configData) return ''
    const entry = configData[key]
    return entry?.set ? (entry.value || '') : ''
  }

  // When operation status changes, advance the step
  if (operation && step === 'fetching') {
    if (operation.status === 'previewing') {
      setStep('preview')
    } else if (operation.status === 'failed') {
      setStep('error')
    }
  }

  const handleSelectSource = (type) => {
    setSelectedType(type)

    if (!SOURCE_CARDS.find(c => c.type === type).needsUrl) {
      handleTrigger(type, '')
    } else {
      // Pre-fill with default playlist if configured
      const defaultUrl = getDefaultPlaylist(type)
      setUrl(defaultUrl)
      setStep('url')
    }
  }

  const handleTrigger = (type, triggerUrl) => {
    setStep('fetching')
    triggerImport.mutate(
      { import_type: type || selectedType, url: triggerUrl ?? url },
      {
        onSuccess: (data) => setOperationId(data.id),
        onError: () => setStep('error'),
      },
    )
  }

  const handleConfirm = (selectedIndices) => {
    confirmImport.mutate(
      { id: operationId, items: selectedIndices },
      { onSuccess: () => onClose() },
    )
  }

  const getAvailability = (type) => {
    if (!configStatus) return { available: type === 'youtube' || type === 'soundcloud' }
    return configStatus[type] || { available: false }
  }

  const getCardDesc = (card) => {
    if (card.desc) return card.desc
    const defaultUrl = getDefaultPlaylist(card.type)
    if (defaultUrl) return 'Default playlist configured'
    return 'Paste a playlist URL'
  }

  const handleSpotifyConnect = async () => {
    try {
      const resp = await fetch('/api/wanted/import/spotify/auth/')
      const data = await resp.json()
      if (data.url) {
        window.open(data.url, '_blank', 'width=500,height=700')
      }
    } catch (e) {
      // ignore
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal import-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-header">
          <h3>
            {step === 'select' && 'Import Tracks'}
            {step === 'url' && `Import from ${SOURCE_CARDS.find(c => c.type === selectedType)?.label}`}
            {step === 'fetching' && (operation?.playlist_name || 'Fetching...')}
            {step === 'preview' && (operation?.playlist_name || 'Preview Import')}
            {step === 'error' && 'Import Failed'}
          </h3>
          <button className="btn-close" onClick={onClose} aria-label="Close" />
        </div>

        <div className="import-body">
          {/* Step 1: Source Selection */}
          {step === 'select' && (
            <div className="import-sources">
              {SOURCE_CARDS.map(card => {
                const avail = getAvailability(card.type)
                const isSpotifyDisconnected = card.type === 'spotify' && avail.available && !avail.connected
                return (
                  <button
                    key={card.type}
                    className={`import-source-card${!avail.available ? ' import-source-card--disabled' : ''}`}
                    onClick={() => avail.available && !isSpotifyDisconnected && handleSelectSource(card.type)}
                    disabled={!avail.available}
                  >
                    <div className="import-source-card__label">{card.label}</div>
                    <div className="import-source-card__desc">
                      {!avail.available ? 'Not configured' : getCardDesc(card)}
                    </div>
                    {isSpotifyDisconnected && (
                      <button
                        className="btn btn-xs btn-accent"
                        onClick={(e) => { e.stopPropagation(); handleSpotifyConnect() }}
                      >
                        Connect
                      </button>
                    )}
                  </button>
                )
              })}
            </div>
          )}

          {/* Step 2: URL Input */}
          {step === 'url' && (
            <div className="import-url-step">
              <div className="form-group">
                <label>Playlist URL</label>
                <input
                  type="url"
                  value={url}
                  onChange={e => setUrl(e.target.value)}
                  placeholder={`Paste ${SOURCE_CARDS.find(c => c.type === selectedType)?.label} playlist URL...`}
                  autoFocus
                  onKeyDown={e => e.key === 'Enter' && url && handleTrigger()}
                />
              </div>
              <div className="form-actions">
                <button className="btn" onClick={() => { setStep('select'); setSelectedType(null); setUrl('') }}>
                  Back
                </button>
                <button
                  className="btn btn-primary"
                  onClick={() => handleTrigger()}
                  disabled={!url}
                >
                  Go
                </button>
              </div>
            </div>
          )}

          {/* Step 3: Fetching */}
          {step === 'fetching' && (
            <div className="import-fetching">
              <div className="import-spinner" />
              <p>
                {operation?.playlist_name
                  ? `Fetching tracks from "${operation.playlist_name}"...`
                  : `Fetching tracks from ${SOURCE_CARDS.find(c => c.type === selectedType)?.label}...`
                }
              </p>
            </div>
          )}

          {/* Step 4: Preview */}
          {step === 'preview' && operation && (
            <ImportPreview
              tracks={operation.preview_data || []}
              onConfirm={handleConfirm}
              isConfirming={confirmImport.isPending}
            />
          )}

          {/* Error */}
          {step === 'error' && (
            <div className="import-error">
              <p className="import-error__msg">
                {operation?.error_message || triggerImport.error?.data?.detail || 'Something went wrong'}
              </p>
              <div className="form-actions">
                <button className="btn" onClick={() => { setStep('select'); setSelectedType(null); setUrl(''); setOperationId(null) }}>
                  Try Again
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default ImportPanel
