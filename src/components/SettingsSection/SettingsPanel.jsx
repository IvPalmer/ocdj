import { useState } from 'react'
import {
  useHealth, useSlskdHealth, useConfig, useUpdateConfig,
  useImportConfigStatus, useSpotifyStatus,
  useAutomationConfig, useUpdateAutomationConfig, useRunAutomation,
  useAutomationStatus,
} from '../../api/hooks'
import './SettingsPanel.css'

const CONFIG_SECTIONS = [
  {
    title: 'YouTube',
    keys: [
      { key: 'YOUTUBE_API_KEY', label: 'API Key', placeholder: 'AIzaSy...', secret: true },
      { key: 'YOUTUBE_DEFAULT_PLAYLIST', label: 'Default Playlist', placeholder: 'https://www.youtube.com/playlist?list=...' },
    ],
  },
  {
    title: 'SoundCloud',
    keys: [
      { key: 'SC_CLIENT_ID', label: 'Client ID', placeholder: 'Client ID', secret: true },
      { key: 'SC_CLIENT_SECRET', label: 'Client Secret', placeholder: 'Client Secret', secret: true },
      { key: 'SC_DEFAULT_PLAYLIST', label: 'Default Playlist', placeholder: 'https://soundcloud.com/user/sets/playlist' },
    ],
  },
  {
    title: 'Spotify',
    keys: [
      { key: 'SPOTIFY_CLIENT_ID', label: 'Client ID', placeholder: 'Client ID', secret: true },
      { key: 'SPOTIFY_CLIENT_SECRET', label: 'Client Secret', placeholder: 'Client Secret', secret: true },
      { key: 'SPOTIFY_REDIRECT_URI', label: 'Redirect URI', placeholder: 'http://127.0.0.1:8002/api/wanted/import/spotify/callback', secret: true },
      { key: 'SPOTIFY_DEFAULT_PLAYLIST', label: 'Default Playlist', placeholder: 'https://open.spotify.com/playlist/...' },
    ],
    hasConnect: true,
  },
  {
    title: 'Discogs',
    keys: [
      { key: 'DISCOGS_PERSONAL_TOKEN', label: 'Personal Token', placeholder: 'Token', secret: true },
      { key: 'DISCOGS_USERNAME', label: 'Username', placeholder: 'your_username' },
    ],
  },
  {
    title: 'Organize',
    keys: [
      { key: 'ORGANIZE_RENAME_TEMPLATE', label: 'Rename Template', placeholder: '{artist} - {title} [{label} {catalog}]' },
    ],
  },
]

function ConfigSection({ section, configData, onSave, importStatus, spotifyStatus }) {
  const [editing, setEditing] = useState(false)
  const [values, setValues] = useState({})

  const startEdit = () => {
    const initial = {}
    section.keys.forEach(k => {
      initial[k.key] = ''
    })
    setValues(initial)
    setEditing(true)
  }

  const handleSave = () => {
    // Only send non-empty values
    const toSave = {}
    for (const [key, val] of Object.entries(values)) {
      if (val.trim()) toSave[key] = val.trim()
    }
    if (Object.keys(toSave).length > 0) {
      onSave(toSave)
    }
    setEditing(false)
    setValues({})
  }

  const allSet = section.keys.every(k => configData?.[k.key]?.set)

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
    <div className="config-section">
      <div className="config-section__header">
        <h4 className="config-section__title">{section.title}</h4>
        <div className="config-section__status-row">
          {allSet ? (
            <span className="config-badge config-badge--set">Configured</span>
          ) : (
            <span className="config-badge config-badge--unset">Not configured</span>
          )}
          {section.hasConnect && spotifyStatus?.connected && (
            <span className="config-badge config-badge--connected">Connected</span>
          )}
        </div>
      </div>

      {!editing ? (
        <div className="config-fields-preview">
          {section.keys.map(k => {
            const info = configData?.[k.key]
            return (
              <div key={k.key} className="config-field-row">
                <span className="config-field-label">{k.label}</span>
                <span className={`config-field-value ${info?.set ? '' : 'config-field-value--empty'}`}>
                  {info?.set ? info.value : 'Not set'}
                </span>
              </div>
            )
          })}
          <div className="config-actions">
            <button className="btn btn-sm" onClick={startEdit}>
              {allSet ? 'Update' : 'Configure'}
            </button>
            {section.hasConnect && allSet && !spotifyStatus?.connected && (
              <button className="btn btn-sm btn-accent" onClick={handleSpotifyConnect}>
                Connect Spotify
              </button>
            )}
          </div>
        </div>
      ) : (
        <div className="config-fields-edit">
          {section.keys.map(k => (
            <div key={k.key} className="form-group">
              <label>{k.label}</label>
              <input
                type="text"
                value={values[k.key] || ''}
                onChange={e => setValues(v => ({ ...v, [k.key]: e.target.value }))}
                placeholder={k.placeholder}
              />
            </div>
          ))}
          <div className="config-actions">
            <button className="btn btn-sm" onClick={() => setEditing(false)}>Cancel</button>
            <button className="btn btn-sm btn-primary" onClick={handleSave}>Save</button>
          </div>
        </div>
      )}
    </div>
  )
}

function AutomationSection() {
  const { data: config, isLoading } = useAutomationConfig()
  const { data: status } = useAutomationStatus()
  const updateConfig = useUpdateAutomationConfig()
  const runAutomation = useRunAutomation()

  if (isLoading) return null

  const toggle = (key) => {
    updateConfig.mutate({ [key]: !config?.[key] })
  }

  const setThreshold = (value) => {
    updateConfig.mutate({ AUTOMATION_CONFIDENCE_THRESHOLD: parseInt(value) })
  }

  const pipeline = status?.pipeline || {}
  const preview = status?.preview?.steps || {}

  return (
    <div className="automation-section">
      <div className="automation-toggle-row">
        <div className="automation-toggle-info">
          <span className="automation-toggle-label">Enable Automation</span>
          <span className="automation-toggle-desc">Master switch for all auto-advance steps</span>
        </div>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={!!config?.AUTOMATION_ENABLED}
            onChange={() => toggle('AUTOMATION_ENABLED')}
          />
          <span className="toggle-slider" />
        </label>
      </div>

      <div className={`automation-steps ${!config?.AUTOMATION_ENABLED ? 'automation-steps--disabled' : ''}`}>
        <div className="automation-toggle-row">
          <div className="automation-toggle-info">
            <span className="automation-toggle-label">Auto Search</span>
            <span className="automation-toggle-desc">Queue Soulseek searches for pending wanted items</span>
          </div>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={!!config?.AUTOMATION_AUTO_SEARCH}
              onChange={() => toggle('AUTOMATION_AUTO_SEARCH')}
              disabled={!config?.AUTOMATION_ENABLED}
            />
            <span className="toggle-slider" />
          </label>
        </div>

        <div className="automation-toggle-row">
          <div className="automation-toggle-info">
            <span className="automation-toggle-label">Auto Download</span>
            <span className="automation-toggle-desc">Download best match above confidence threshold</span>
          </div>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={!!config?.AUTOMATION_AUTO_DOWNLOAD}
              onChange={() => toggle('AUTOMATION_AUTO_DOWNLOAD')}
              disabled={!config?.AUTOMATION_ENABLED}
            />
            <span className="toggle-slider" />
          </label>
        </div>

        <div className="automation-threshold-row">
          <span className="automation-toggle-label">Confidence Threshold</span>
          <div className="automation-threshold-control">
            <input
              type="range"
              min="50"
              max="100"
              value={config?.AUTOMATION_CONFIDENCE_THRESHOLD || 85}
              onChange={e => setThreshold(e.target.value)}
              disabled={!config?.AUTOMATION_ENABLED}
            />
            <span className="automation-threshold-value">
              {config?.AUTOMATION_CONFIDENCE_THRESHOLD || 85}%
            </span>
          </div>
        </div>

        <div className="automation-toggle-row">
          <div className="automation-toggle-info">
            <span className="automation-toggle-label">Auto Organize</span>
            <span className="automation-toggle-desc">Process downloads through tag/rename pipeline</span>
          </div>
          <label className="toggle-switch">
            <input
              type="checkbox"
              checked={!!config?.AUTOMATION_AUTO_ORGANIZE}
              onChange={() => toggle('AUTOMATION_AUTO_ORGANIZE')}
              disabled={!config?.AUTOMATION_ENABLED}
            />
            <span className="toggle-slider" />
          </label>
        </div>
      </div>

      <div className="automation-actions">
        <button
          className="btn btn-sm btn-primary"
          onClick={() => runAutomation.mutate({})}
          disabled={runAutomation.isPending || !config?.AUTOMATION_ENABLED}
        >
          {runAutomation.isPending ? 'Running...' : 'Run Now'}
        </button>
        <button
          className="btn btn-sm"
          onClick={() => runAutomation.mutate({ dry_run: true })}
          disabled={runAutomation.isPending}
        >
          Dry Run
        </button>
      </div>

      {runAutomation.data && (
        <div className="automation-results">
          <div className="automation-result-title">
            {runAutomation.data.dry_run ? 'Dry Run Results' : 'Last Run Results'}
          </div>
          {Object.entries(runAutomation.data.steps || {}).map(([step, data]) => (
            <div key={step} className="automation-result-step">
              <span className="automation-result-step-name">{step}</span>
              {data.skipped ? (
                <span className="automation-result-skipped">{data.reason}</span>
              ) : (
                <span className="automation-result-counts">
                  {step === 'search' && `${data.queued} queued, ${data.already_queued} skipped`}
                  {step === 'download' && `${data.downloaded} started, ${data.below_threshold} below ${data.threshold}%`}
                  {step === 'organize' && `${data.ingested} ingested, ${data.failed} failed`}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SettingsPanel() {
  const { data: health } = useHealth()
  const { data: slskdHealth } = useSlskdHealth()
  const { data: configData } = useConfig()
  const { data: importStatus } = useImportConfigStatus()
  const { data: spotifyStatus } = useSpotifyStatus()
  const updateConfig = useUpdateConfig()

  const handleSave = (values) => {
    updateConfig.mutate(values)
  }

  return (
    <div className="settings-panel">
      <h2 className="page-title">Settings</h2>

      <div className="settings-section">
        <h3 className="section-title">Service Status</h3>
        <div className="settings-grid">
          <div className="setting-item">
            <span className="setting-label">Backend</span>
            <span className={`setting-value ${health ? 'text-green' : 'text-red'}`}>
              {health ? 'Running' : 'Disconnected'}
            </span>
          </div>
          <div className="setting-item">
            <span className="setting-label">slskd</span>
            <span className={`setting-value ${slskdHealth?.status === 'connected' ? 'text-green' : 'text-red'}`}>
              {slskdHealth?.status === 'connected' ? 'Connected' : 'Disconnected'}
            </span>
          </div>
          {health && (
            <>
              <div className="setting-item">
                <span className="setting-label">slskd URL</span>
                <span className="setting-value">{health.slskd_url}</span>
              </div>
              <div className="setting-item">
                <span className="setting-label">Music Root</span>
                <span className="setting-value">{health.music_root}</span>
              </div>
            </>
          )}
        </div>
      </div>

      <div className="settings-section">
        <h3 className="section-title">Automation</h3>
        <AutomationSection />
      </div>

      <div className="settings-section">
        <h3 className="section-title">Import Sources</h3>
        <div className="config-sections">
          {CONFIG_SECTIONS.map(section => (
            <ConfigSection
              key={section.title}
              section={section}
              configData={configData}
              importStatus={importStatus}
              spotifyStatus={spotifyStatus}
              onSave={handleSave}
            />
          ))}
        </div>
      </div>

      <div className="settings-section">
        <h3 className="section-title">Quick Links</h3>
        <div className="links-grid">
          <a href="http://localhost:5030" target="_blank" rel="noopener noreferrer" className="quick-link">
            slskd Web UI
          </a>
          <a href="http://localhost:8002/admin/" target="_blank" rel="noopener noreferrer" className="quick-link">
            Django Admin
          </a>
          <a href="http://localhost:8002/api/wanted/items/" target="_blank" rel="noopener noreferrer" className="quick-link">
            API Browser
          </a>
        </div>
      </div>

      <div className="settings-section">
        <h3 className="section-title">About</h3>
        <p className="settings-about">
          OCDJ v2.0 — Django + React + Docker rebuild.
          Manages your wanted list, searches Soulseek, and organizes your music library.
        </p>
      </div>
    </div>
  )
}

export default SettingsPanel
