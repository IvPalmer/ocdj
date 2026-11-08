import { useLibraryMatch } from '../../api/hooks'

export default function LibraryMatch({ artist, title }) {
  const query = useLibraryMatch(artist, title)
  let label = 'Artist + title needed for library check'
  if (artist?.trim() && title?.trim()) {
    label = query.isError ? 'Library check unavailable' : 'Checking library index…'
    if (!query.isError && query.data) {
      const { status, matches } = query.data
      label = {
        no_indexed_match: 'No indexed match',
        indexed_missing: 'Indexed file marked missing',
        multiple_matches: 'Multiple indexed matches · version unverified',
      }[status] || 'Library check unavailable'
      if (status === 'matched' && matches?.length === 1) {
        const match = matches[0]
        const measured = [match.bitrate > 0 && `${match.bitrate} kbps`,
          match.sample_rate > 0 && `${match.sample_rate / 1000} kHz`].filter(Boolean)
        label = ['Indexed match', match.format?.toUpperCase() || 'Format unknown',
          ...(measured.length ? measured : ['Quality unknown'])].join(' · ')
      }
    }
  }
  return <small className="track-library-match" title="Exact artist/title in the library index only. Format is the indexed extension; bitrate/sample rate are indexed measurements. No live disk check, bit depth or lossless provenance verification. Unindexed Mac collections are unknown.">
    <span>{label}</span>
    <span className="track-library-coverage">Unindexed collections unknown</span>
  </small>
}
