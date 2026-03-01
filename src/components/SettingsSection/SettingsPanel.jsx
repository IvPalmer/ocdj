import { useState } from 'react'
import {
  useHealth, useSlskdHealth, useConfig, useUpdateConfig,
  useImportConfigStatus, useSpotifyStatus,
} from '../../api/hooks'
import './SettingsPanel.css'

const CONFIG_SECTIONS = [
  {
    title: 'YouTube',
    keys: [
      { key: 'YOUTUBE_API_KEY', label: 'API Key', placeholder: 'AIzaSy...' },
    ],
  },
  {
    title: 'SoundCloud',
    keys: [
      { key: 'SC_CLIENT_ID', label: 'Client ID', placeholder: 'Client ID' },
      { key: 'SC_CLIENT_SECRET', label: 'Client Secret', placeholder: 'Client Secret' },
    ],
  },
  {
    title: 'Spotify',
    keys: [
      { key: 'SPOTIFY_CLIENT_ID', label: 'Client ID', placeholder: 'Client ID' },
      { key: 'SPOTIFY_CLIENT_SECRET', label: 'Client Secret', placeholder: 'Client Secret' },
      { key: 'SPOTIFY_REDIRECT_URI', label: 'Redirect URI', placeholder: 'http://localhost:8002/api/wanted/import/spotify/callback/' },
    ],
    hasConnect: true,
  },
  {
    title: 'Discogs',
    keys: [
      { key: 'DISCOGS_PERSONAL_TOKEN', label: 'Personal Token', placeholder: 'Token' },
      { key: 'DISCOGS_USERNAME', label: 'Username', placeholder: 'your_username' },
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
