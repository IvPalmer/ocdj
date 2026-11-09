import { useLibraryMatch } from '../../api/hooks'

const COVERAGE = 'Exact artist/title in the library index only. Unindexed collections unknown. No live disk check, bit depth or lossless provenance verification.'
const REVEAL_URL = /^ocdj-reveal:\/\/track\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/[0-9a-f]{64}$/

// Reuse the exact same cached match as the Finder indicator. No staging here.
export function useIndexedPlayback(track) {
  const archiveMatch = track.archiveLibraryMatch
  const query = useLibraryMatch(archiveMatch ? '' : track.artist, archiveMatch ? '' : track.title)
  if (track.source?.kind === 'mac') return track
  const match = archiveMatch || (query.isError ? null : query.data)
  const id = match?.status === 'matched' && match.matches?.length === 1
    ? match.matches[0].playback_id : null
  return Number.isSafeInteger(id) && id > 0 ? { ...track,
    source: { kind: 'mac', libraryId: id }, start: 0, end: undefined,
    fallback: track, description: 'Indexed Mac library · temporary MP3 playback copy',
  } : track
}

export default function LibraryMatch({ artist, title, archiveMatch }) {
  const query = useLibraryMatch(archiveMatch ? '' : artist, archiveMatch ? '' : title)
  const result = archiveMatch || (query.isError ? null : query.data)
  let label = 'Artist + title needed for library check'
  let state = 'unknown'
  let quality = ''
  let revealUrl
  if (archiveMatch || (artist?.trim() && title?.trim())) {
    label = query.isError ? 'Library check unavailable' : 'Checking library index…'
    if (result) {
      const { status, matches } = result
      label = {
        no_indexed_match: 'No indexed match',
        indexed_missing: 'Indexed file marked missing',
        multiple_matches: 'Multiple indexed matches · version unverified',
      }[status] || 'Library check unavailable'
      if (status === 'no_indexed_match') state = 'absent'
      if (status === 'indexed_missing' || status === 'multiple_matches') state = 'warning'
      if (status === 'matched' && matches?.length === 1) {
        state = 'matched'
        const match = matches[0]
        if (typeof match.reveal_url === 'string' && REVEAL_URL.exec(match.reveal_url)?.[0] === match.reveal_url) revealUrl = match.reveal_url
        const measured = [match.bitrate > 0 && `${match.bitrate} kbps`,
          match.sample_rate > 0 && `${match.sample_rate / 1000} kHz`].filter(Boolean)
        const format = match.format?.toUpperCase()
        quality = measured.length ? [format, measured[0]].filter(Boolean).join(' · ') : ''
        label = ['Indexed match', format || 'Format unknown',
          ...(measured.length ? measured : ['Quality unknown']),
          'Format is the indexed extension; bitrate/sample rate are indexed measurements'].join(' · ')
      }
    }
  }
  const coverage = archiveMatch ? COVERAGE.replace('artist/title', 'Organize archive ID') : COVERAGE
  const description = `${revealUrl ? 'Open in Finder · ' : ''}${label}. ${coverage}`
  const Indicator = revealUrl ? 'a' : 'span'
  return <Indicator className={`track-library-match track-library-match--${state}`}
    href={revealUrl} role={revealUrl ? undefined : 'img'} tabIndex={0}
    aria-label={description} title={description}>
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="8" cy="8" r="6" />
      {state === 'matched' ? <path d="m5 8 2 2 4-4" />
        : state === 'absent' ? <path d="M5.5 8h5" />
        : state === 'warning' ? <path d="M8 4.5v4M8 11h.01" />
        : <path d="M6.5 6a1.5 1.5 0 0 1 3 0c0 1-1.5 1-1.5 2.5M8 11h.01" />}
    </svg>
    {quality && <span aria-hidden="true">{quality}</span>}
  </Indicator>
}
