import { useHealth, useSlskdHealth } from '../../api/hooks'
import './SettingsPanel.css'

function SettingsPanel() {
  const { data: health } = useHealth()
  const { data: slskdHealth } = useSlskdHealth()

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
