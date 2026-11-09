import { useLibraryMatch } from '../../api/hooks'

const COVERAGE = 'Exact artist/title in the library index only. Unindexed collections unknown. No live disk check, bit depth or lossless provenance verification.'

export default function LibraryMatch({ artist, title }) {
  const query = useLibraryMatch(artist, title)
  let label = 'Artist + title needed for library check'
  let state = 'unknown'
  let quality = ''
  if (artist?.trim() && title?.trim()) {
    label = query.isError ? 'Library check unavailable' : 'Checking library index…'
    if (!query.isError && query.data) {
      const { status, matches } = query.data
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
  const description = `${label}. ${COVERAGE}`
  return <span className={`track-library-match track-library-match--${state}`}
    role="img" tabIndex={0} aria-label={description} title={description}>
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="8" cy="8" r="6" />
      {state === 'matched' ? <path d="m5 8 2 2 4-4" />
        : state === 'absent' ? <path d="M5.5 8h5" />
        : state === 'warning' ? <path d="M8 4.5v4M8 11h.01" />
        : <path d="M6.5 6a1.5 1.5 0 0 1 3 0c0 1-1.5 1-1.5 2.5M8 11h.01" />}
    </svg>
    {quality && <span aria-hidden="true">{quality}</span>}
  </span>
}
