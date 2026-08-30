import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSpotifyStatus, useSlskdHealth } from '../../api/hooks'
import './Connections.css'

/* Every outside account the app talks to, in one place and at the top of the
   page. Before this the only sign-in button lived inside the Spotify block of
   the Configuration accordion and only rendered once *every* field in that
   category was filled — including two that just remember the last playlist
   imported. A working account could therefore show no way to connect at all.

   Two rules keep this honest, both learned by getting it wrong first:

   1. Never hand-write key names. The first version listed SLSKD_URL,
      DISCOGS_TOKEN and SOUNDCLOUD_CLIENT_ID; the real keys are
      SLSKD_BASE_URL, DISCOGS_PERSONAL_TOKEN and SC_CLIENT_ID, so three
      configured services reported themselves unconfigured. Credentials are
      read from the schema as the fields marked `is_secret`, which also
      excludes the cosmetic *_DEFAULT_PLAYLIST fields for free.

   2. Prefer a live check to stored config. slskd has no API key in the config
      store and is nonetheless connected and logged in, because the key comes
      from the environment. Config presence is a guess about reachability;
      a health endpoint is the answer. */

const SERVICES = [
  { category: 'spotify', name: 'Spotify', check: 'spotify',
    blurb: 'Feeds the Shazam page and playlist imports.' },
  { category: 'slskd', name: 'slskd / Soulseek', check: 'slskd',
    blurb: 'Searches and downloads from the Soulseek network.' },
  { category: 'discogs', name: 'Discogs',
    blurb: 'Release metadata and catalogue numbers.',
    where: 'discogs.com/settings/developers' },
  { category: 'youtube', name: 'YouTube',
    blurb: 'Playlist imports and track downloads.',
    where: 'console.cloud.google.com' },
  { category: 'soundcloud', name: 'SoundCloud',
    blurb: 'Playlist imports.' },
  { category: 'traxdb', name: 'TraxDB — blog + Pixeldrain',
    blurb: 'Reads the private blog and downloads each list.',
    note: 'Blogger was signed in once from the Mac — blogger_oauth_bootstrap.py' },
]

function credentialKeys(schema, category) {
  // The credentials are exactly the secrets. Everything else in a category is
  // configuration, and holding a connection hostage to it is what broke this
  // page the first time.
  return (schema?.[category] || []).filter(f => f.is_secret).map(f => f.key)
}

export default function Connections({ schema, configData, onJumpTo }) {
  const qc = useQueryClient()
  const { data: spotifyStatus } = useSpotifyStatus()
  const { data: slskdHealth } = useSlskdHealth()
  const [busy, setBusy] = useState(false)

  const connectSpotify = async () => {
    setBusy(true)
    try {
      const resp = await fetch('/api/wanted/import/spotify/auth/')
      const data = await resp.json()
      if (!data.url) return
      const win = window.open(data.url, 'spotify-auth', 'width=520,height=720')
      // The callback lands in the popup, not here, so there is no event to
      // wait on — poll for the window closing, then re-ask the server.
      const timer = setInterval(() => {
        if (win && !win.closed) return
        clearInterval(timer)
        qc.invalidateQueries({ queryKey: ['spotify-status'] })
        qc.invalidateQueries({ queryKey: ['shazam-status'] })
      }, 800)
    } finally {
      setBusy(false)
    }
  }

  const statusOf = (svc) => {
    if (svc.check === 'spotify') {
      if (!spotifyStatus) return { tone: 'unknown', label: 'Checking…' }
      if (!spotifyStatus.configured) return { tone: 'off', label: 'App not registered' }
      if (spotifyStatus.connected) return { tone: 'on', label: 'Signed in' }
      return { tone: 'off', label: 'Not signed in' }
    }
    if (svc.check === 'slskd') {
      if (!slskdHealth) return { tone: 'unknown', label: 'Checking…' }
      return slskdHealth.status === 'connected'
        ? { tone: 'on', label: 'Connected' }
        : { tone: 'off', label: 'Disconnected' }
    }
    const keys = credentialKeys(schema, svc.category)
    if (keys.length === 0) return { tone: 'unknown', label: '—' }
    const set = keys.filter(k => configData?.[k]?.set).length
    if (set === 0) return { tone: 'off', label: 'Not set up' }
    if (set < keys.length) return { tone: 'partial', label: `${set} of ${keys.length} set` }
    return { tone: 'on', label: 'Configured' }
  }

  return (
    <div className="settings-section">
      <h3 className="section-title">Connections</h3>
      <p className="connections-intro">
        Accounts this app signs into on your behalf. A sign-in button is used wherever the
        provider offers a flow — today that is Spotify only; the rest still need a key pasted,
        which is a limitation of those services rather than a preference.
      </p>

      <div className="connections-grid">
        {SERVICES.map(svc => {
          const st = statusOf(svc)
          return (
            <div key={svc.category} className={`connection-card connection-card--${st.tone}`}>
              <div className="connection-main">
                <div className="connection-head">
                  <span className={`connection-dot connection-dot--${st.tone}`} />
                  <span className="connection-name">{svc.name}</span>
                  <span className="connection-status">{st.label}</span>
                </div>
                <p className="connection-blurb">{svc.blurb}</p>
                {svc.note && <p className="connection-note">{svc.note}</p>}
                {svc.where && st.tone !== 'on' && (
                  <p className="connection-note">Key from <span className="mono">{svc.where}</span></p>
                )}
              </div>
              <div className="connection-action">
                {svc.check === 'spotify' ? (
                  <button
                    className={`btn btn-sm${st.tone === 'on' ? '' : ' btn-accent'}`}
                    onClick={connectSpotify}
                    disabled={busy || !spotifyStatus?.configured}
                    title={!spotifyStatus?.configured
                      ? 'Add the Spotify client id and secret below first'
                      : 'Sign in with Spotify'}
                  >
                    {st.tone === 'on' ? 'Reconnect' : 'Sign in'}
                  </button>
                ) : (
                  <button className="btn btn-sm" onClick={() => onJumpTo(svc.category)}>
                    {st.tone === 'on' ? 'Change' : 'Set up'}
                  </button>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
