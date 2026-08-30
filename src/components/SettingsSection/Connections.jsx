import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useSpotifyStatus } from '../../api/hooks'
import './Connections.css'

/* Every outside account the app talks to, in one place and at the top of the
   page. Before this the only real sign-in button lived inside the Spotify
   block of the Configuration accordion, and only rendered once *every* field
   in that category was filled — including two that just remember the last
   playlist you imported. A working account could therefore show no way to
   connect at all, which is indistinguishable from the feature not existing.

   Where a provider gives us a sign-in flow, that is the whole interaction and
   no key is ever pasted. Where it does not, the honest thing is to say so and
   send you straight to the right field rather than pretend otherwise. */

const SERVICES = [
  {
    id: 'spotify',
    name: 'Spotify',
    blurb: 'Feeds the Shazam page and playlist imports.',
    auth: 'sso',
  },
  {
    id: 'discogs',
    name: 'Discogs',
    blurb: 'Release metadata and catalogue numbers.',
    auth: 'token',
    keys: ['DISCOGS_TOKEN'],
    where: 'discogs.com/settings/developers',
  },
  {
    id: 'youtube',
    name: 'YouTube',
    blurb: 'Playlist imports and track downloads.',
    auth: 'token',
    keys: ['YOUTUBE_API_KEY'],
    where: 'console.cloud.google.com',
  },
  {
    id: 'soundcloud',
    name: 'SoundCloud',
    blurb: 'Playlist imports.',
    auth: 'token',
    keys: ['SOUNDCLOUD_CLIENT_ID'],
  },
  {
    id: 'traxdb',
    name: 'TraxDB (Blogger)',
    blurb: 'Reads the private blog for new lists.',
    auth: 'bootstrap',
    keys: ['BLOGGER_CLIENT_ID', 'BLOGGER_CLIENT_SECRET', 'BLOGGER_REFRESH_TOKEN'],
    note: 'Signed in once from the Mac — tools/traxdb_sync/blogger_oauth_bootstrap.py',
  },
  {
    id: 'pixeldrain',
    name: 'Pixeldrain',
    blurb: 'Downloads the files behind each TraxDB list.',
    auth: 'token',
    keys: ['PIXELDRAIN_API_KEY'],
    where: 'pixeldrain.com/user/api_keys',
  },
  {
    id: 'slskd',
    name: 'slskd / Soulseek',
    blurb: 'Searches and downloads from the Soulseek network.',
    auth: 'token',
    keys: ['SLSKD_URL', 'SLSKD_API_KEY'],
  },
]

function statusOf(service, configData, spotifyStatus) {
  if (service.auth === 'sso') {
    if (!spotifyStatus) return { tone: 'unknown', label: 'Checking…' }
    if (!spotifyStatus.configured) return { tone: 'off', label: 'App not registered' }
    if (spotifyStatus.connected) return { tone: 'on', label: 'Signed in' }
    return { tone: 'off', label: 'Not signed in' }
  }
  const keys = service.keys || []
  const set = keys.filter(k => configData?.[k]?.set).length
  if (set === 0) return { tone: 'off', label: 'Not set up' }
  if (set < keys.length) return { tone: 'partial', label: `${set} of ${keys.length} set` }
  return { tone: 'on', label: 'Configured' }
}

export default function Connections({ configData, onJumpTo }) {
  const qc = useQueryClient()
  const { data: spotifyStatus } = useSpotifyStatus()
  const [busy, setBusy] = useState(false)

  const connectSpotify = async () => {
    setBusy(true)
    try {
      const resp = await fetch('/api/wanted/import/spotify/auth/')
      const data = await resp.json()
      if (!data.url) return
      const win = window.open(data.url, 'spotify-auth', 'width=520,height=720')
      // The callback lands in the popup, not here, so there is no event to
      // wait on — poll for the window closing and then re-ask the server.
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

  return (
    <div className="settings-section">
      <h3 className="section-title">Connections</h3>
      <p className="connections-intro">
        Accounts this app signs into on your behalf. Sign-in buttons are used wherever the
        provider offers one; the rest still need a key pasted below, which is a limitation of
        those services, not a preference.
      </p>

      <div className="connections-grid">
        {SERVICES.map(svc => {
          const st = statusOf(svc, configData, spotifyStatus)
          return (
            <div key={svc.id} className={`connection-card connection-card--${st.tone}`}>
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
                {svc.auth === 'sso' ? (
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
                  <button className="btn btn-sm" onClick={() => onJumpTo(svc.id)}>
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
