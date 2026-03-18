import logging
import os
import shutil
import threading

from django import db
from django.conf import settings

from recognize.models import RecognizeJob
from .downloader import download_audio
from .description_parser import parse_tracklist_from_description
from .segmenter import segment_audio, segment_gaps, segment_verification
from .recognition import recognize_segments as shazam_recognize_segments
from .acrcloud import recognize_segments as acrcloud_recognize_segments
from .clustering import cluster_results, find_gaps, find_single_segment_candidates, dedup_tracklist
from .trackid import lookup_by_url, submit_url

logger = logging.getLogger(__name__)

# Guard against duplicate workers for the same job
_active_jobs = set()
_active_jobs_lock = threading.Lock()

# Pipeline configuration — optimized based on testing (March 2026)
#
# Findings from max-coverage tests (10s ACR step, 5s Shazam step):
#   - ACRCloud hit rate: 7.5% (27/361). Denser scanning mostly adds false positives.
#   - Shazam hit rate: 1.7% (11/662). Even worse — 5s step not worth the time.
#   - Zero overlap between engines — they cover completely different catalogs.
#   - 10s vs 20s ACR step: more raw hits (27 vs 11) but same ~2 real unique tracks.
#   - TrackID.net found 10 tracks neither engine could detect at any density.
#   - Bottleneck is database coverage (underground music), not scan density.
#
# Optimal balance: 20s ACR step + 8s Shazam gap fill + TrackID merge.
SEGMENT_DURATION = 12       # seconds per segment (ACRCloud optimal: 10-12s)
SEGMENT_STEP = 10           # seconds between segment starts (Shazam-only fallback)
GAP_THRESHOLD = 20          # minimum unidentified gap to trigger Shazam fill
GAP_SEGMENT_DURATION = 15   # longer segments for harder-to-identify regions
GAP_SEGMENT_STEP = 8        # Shazam gap fill interval (free, but slow — 2s rate limit)
MAX_GAP_SEGMENTS = 500      # practical cap (~17 min of Shazam processing)
ACRCLOUD_SEGMENT_STEP = 20  # 20s step — denser adds noise, not real tracks


def run_recognize(job_id):
    """Launch the recognition pipeline in a background thread."""
    with _active_jobs_lock:
        if job_id in _active_jobs:
            logger.info(f'Job {job_id}: already has an active worker, skipping')
            return
        _active_jobs.add(job_id)

    thread = threading.Thread(
        target=_recognize_worker,
        args=(job_id,),
        daemon=True,
    )
    thread.start()


def resume_stale_jobs():
    """Resume jobs that were interrupted by a restart. Called on backend startup."""
    stale = RecognizeJob.objects.filter(status__in=['downloading', 'recognizing'])
    if not stale.exists():
        return

    count = stale.count()
    print(f'[recognize] Resuming {count} stale job(s)...')
    for job in stale:
        print(f'[recognize]   Job {job.id}: {job.status} — {job.title or job.url}')
        run_recognize(job.id)


def _find_existing_audio(output_dir):
    """Find an already-downloaded audio file in the output directory."""
    if not os.path.exists(output_dir):
        return None
    for f in os.listdir(output_dir):
        if f.endswith('.mp3') and not f.startswith('seg_') and not f.startswith('gap_'):
            return os.path.join(output_dir, f)
    return None


def _find_existing_segments(output_dir, step):
    """Find existing segment files that match the expected step pattern."""
    seg_dir = os.path.join(output_dir, 'segments')
    if not os.path.exists(seg_dir):
        return []
    segments = []
    for f in sorted(os.listdir(seg_dir)):
        if f.startswith('seg_') and f.endswith('.mp3'):
            start_sec = int(f.replace('seg_', '').replace('.mp3', ''))
            segments.append((os.path.join(seg_dir, f), start_sec))
    return segments


def _recognize_worker(job_id):
    try:
        job = RecognizeJob.objects.get(pk=job_id)
        output_dir = os.path.join(settings.MEDIA_ROOT, 'recognize', str(job_id))

        # Check if audio already exists (resume after restart)
        existing_audio = _find_existing_audio(output_dir)
        is_resume = existing_audio is not None

        if is_resume:
            logger.info(f'Job {job_id}: resuming — found existing audio at {existing_audio}')
            audio_path = existing_audio
            # Get duration from the audio file if not already set
            if not job.duration_seconds:
                from pydub import AudioSegment
                audio = AudioSegment.from_file(audio_path)
                job.duration_seconds = int(len(audio) / 1000)
                job.save()
        else:
            # Step 0: Check TrackID.net first (instant if mix already processed)
            job.status = 'downloading'
            job.save()

            trackid_result = lookup_by_url(job.url)
            if trackid_result and trackid_result.get('trackid_status') == 'completed' and trackid_result['tracklist']:
                # Don't short-circuit — always run full recognition so we can merge later
                logger.info(f'TrackID.net has {len(trackid_result["tracklist"])} tracks for job {job_id}, will merge after recognition')

            if not trackid_result:
                submit_url(job.url)

            # Step 1: Download audio
            audio_path, info = download_audio(job.url, output_dir)

            job.title = info.get('title', '')[:500]
            job.duration_seconds = int(info.get('duration') or 0)
            job.save()

        # Step 2: Parse description (skip on resume if already have them)
        trackid_result = None if is_resume else trackid_result
        if not is_resume:
            description_tracks = parse_tracklist_from_description(info)
            job.description_tracks = description_tracks
            job.save()
        else:
            description_tracks = job.description_tracks or []
            # Re-check TrackID on resume (pass title for keyword fallback)
            trackid_result = lookup_by_url(job.url, title=job.title)

        # Step 3: Segment + recognize
        job.status = 'recognizing'
        use_acrcloud = _has_acrcloud_credentials()
        logger.info(f'Job {job_id}: acrcloud={use_acrcloud}')

        # On resume, reuse existing primary results instead of re-running
        has_existing_results = (
            is_resume
            and job.raw_results
            and job.acrcloud_calls > 0
            and any(r.get('engine') == 'acrcloud' for r in job.raw_results)
        )

        acrcloud_calls = job.acrcloud_calls or 0

        if has_existing_results:
            # Resume: skip primary pass, reuse saved ACRCloud results
            raw_results = list(job.raw_results)
            primary_step = ACRCLOUD_SEGMENT_STEP
            job.segments_total = len(raw_results)
            job.segments_done = len(raw_results)
            job.save()
            logger.info(f'Job {job_id}: resuming with {len(raw_results)} existing results ({acrcloud_calls} ACRCloud calls)')
        elif use_acrcloud:
            # ACRCloud primary — wide step, fast (0.5s rate limit)
            primary_step = ACRCLOUD_SEGMENT_STEP
            segments = segment_audio(
                audio_path,
                segment_duration=SEGMENT_DURATION,
                step=primary_step,
            )
            job.segments_total = len(segments)
            job.segments_done = 0
            job.save()

            def on_progress_acr(done, total):
                job.segments_done = done
                job.save(update_fields=['segments_done', 'updated'])

            raw_results = acrcloud_recognize_segments(segments, on_progress=on_progress_acr)
            acrcloud_calls = len(segments)
            logger.info(f'Job {job_id}: ACRCloud primary — {acrcloud_calls} calls at {primary_step}s step')

            # Save after primary pass so results survive crashes
            job.raw_results = raw_results
            job.acrcloud_calls = acrcloud_calls
            job.save(update_fields=['raw_results', 'acrcloud_calls', 'updated'])
        else:
            # Shazam fallback when ACRCloud not configured
            primary_step = SEGMENT_STEP
            segments = segment_audio(
                audio_path,
                segment_duration=SEGMENT_DURATION,
                step=primary_step,
            )
            job.segments_total = len(segments)
            job.segments_done = 0
            job.save()

            def on_progress_shazam(done, total):
                job.segments_done = done
                job.save(update_fields=['segments_done', 'updated'])

            raw_results = shazam_recognize_segments(segments, on_progress=on_progress_shazam)

        job.acrcloud_calls = acrcloud_calls

        # Step 4: Shazam gap fill (free) — covers what ACRCloud missed at finer intervals
        duration = job.duration_seconds or 0
        gaps = find_gaps(raw_results, duration, min_gap=GAP_THRESHOLD, step=primary_step)

        if gaps:
            gap_segs = segment_gaps(
                audio_path, gaps,
                segment_duration=GAP_SEGMENT_DURATION,
                step=GAP_SEGMENT_STEP,
            )
            if gap_segs and len(gap_segs) > MAX_GAP_SEGMENTS:
                logger.info(f'Capping gap segments from {len(gap_segs)} to {MAX_GAP_SEGMENTS}')
                gap_segs = gap_segs[:MAX_GAP_SEGMENTS]

            if gap_segs:
                job.segments_total += len(gap_segs)
                job.save()

                pass2_done = job.segments_done

                def on_progress_pass2(done, total):
                    job.segments_done = pass2_done + done
                    job.save(update_fields=['segments_done', 'updated'])

                gap_results = shazam_recognize_segments(
                    gap_segs, on_progress=on_progress_pass2, timeout=10, retries=1,
                )
                raw_results.extend(gap_results)
                raw_results.sort(key=lambda r: r['start_sec'])

                # Save after gap fill so results survive restarts
                job.raw_results = raw_results
                job.save(update_fields=['raw_results', 'updated'])

        # Step 5: Verification pass — confirm single-segment results (free, Shazam)
        candidates = find_single_segment_candidates(raw_results)
        if candidates:
            verify_segs = segment_verification(
                audio_path, candidates,
                segment_duration=SEGMENT_DURATION,
                offsets=(-7, -3, 3, 7),
            )
            if verify_segs and len(verify_segs) > 50:
                logger.info(f'Capping verify segments from {len(verify_segs)} to 50')
                verify_segs = verify_segs[:50]

            if verify_segs:
                job.segments_total += len(verify_segs)
                job.save()

                verify_base = job.segments_done

                def on_progress_verify(done, total):
                    job.segments_done = verify_base + done
                    job.save(update_fields=['segments_done', 'updated'])

                verify_results = shazam_recognize_segments(
                    verify_segs, on_progress=on_progress_verify, timeout=10, retries=1,
                )
                raw_results.extend(verify_results)
                raw_results.sort(key=lambda r: r['start_sec'])

                # Save after verification so results survive restarts
                job.raw_results = raw_results
                job.save(update_fields=['raw_results', 'updated'])

        # Step 6: Store raw results for re-clustering
        job.raw_results = raw_results

        # Step 7: Cluster results into tracklist
        tracklist = cluster_results(raw_results, description_tracks)

        # Step 7b: Re-fetch TrackID.net results (may have completed during recognition)
        # and merge into tracklist to fill gaps
        try:
            trackid_result = lookup_by_url(job.url)
        except Exception as e:
            logger.warning(f'Job {job_id}: TrackID.net lookup failed: {e}')
        if trackid_result and trackid_result.get('tracklist'):
            tracklist = _merge_trackid_results(tracklist, trackid_result['tracklist'])

        # Step 7c: Final dedup — merge same-track entries that slipped through
        tracklist = dedup_tracklist(tracklist)

        # Set engine based on what was used
        engines_used = set()
        for t in tracklist:
            engines_used.update(t.get('engines', []))
        if len(engines_used) > 1:
            job.engine = 'hybrid'
        elif 'acrcloud' in engines_used:
            job.engine = 'acrcloud'
        elif 'trackid' in engines_used:
            job.engine = 'trackid'
        else:
            job.engine = 'shazam'

        job.tracklist = tracklist
        job.tracks_found = len(tracklist)
        job.segments_done = job.segments_total
        job.status = 'completed'
        job.save()

        # Step 8: Clean up temp audio files
        try:
            shutil.rmtree(output_dir, ignore_errors=True)
        except Exception:
            pass

        logger.info(f'Recognition complete for job {job_id}: {len(tracklist)} tracks found')

    except Exception as e:
        logger.exception(f'Recognition failed for job {job_id}')
        try:
            job = RecognizeJob.objects.get(pk=job_id)
            job.status = 'failed'
            job.error_message = str(e)
            job.save()
        except Exception:
            pass
    finally:
        with _active_jobs_lock:
            _active_jobs.discard(job_id)
        db.connections.close_all()


def _has_acrcloud_credentials():
    """Check if ACRCloud credentials are configured."""
    from core.views import get_config
    return bool(get_config('ACRCLOUD_ACCESS_KEY') and get_config('ACRCLOUD_ACCESS_SECRET'))


def _merge_trackid_results(shazam_tracklist, trackid_tracklist):
    """Merge TrackID.net results into tracklist.

    1. Cross-validates: if both engines found the same track, mark as verified
    2. Adds TrackID tracks that don't overlap with strong (2+ segment) existing tracks
    3. Removes weak single-hit results that fall within a TrackID track's time range
    """
    if not trackid_tracklist:
        return shazam_tracklist

    from recognize.services.clustering import _normalize_key

    merged = list(shazam_tracklist)

    # Build lookup from our results
    our_keys = set()
    for t in merged:
        our_keys.add(_normalize_key(t['artist'], t['title']))

    # Build time ranges from strong results (2+ segments)
    strong_ranges = []
    for t in merged:
        if t.get('segment_count', 0) >= 2:
            strong_ranges.append((t['timestamp_start'], t['timestamp_end']))

    # Step 1: Add TrackID tracks and cross-validate
    trackid_ranges = []  # time ranges of all TrackID tracks (for filtering our weak results)
    added = 0
    for tt in trackid_tracklist:
        tt_key = _normalize_key(tt['artist'], tt['title'])
        trackid_ranges.append((tt['timestamp_start'], tt['timestamp_end']))

        # Check if we already found this track
        if tt_key in our_keys:
            for t in merged:
                if _normalize_key(t['artist'], t['title']) == tt_key and 'trackid' not in t.get('engines', []):
                    t['engines'].append('trackid')
                    t['confidence'] = 'verified'
                    break
            continue

        # Check overlap with our strong results
        # Only skip if >50% of the TrackID track is covered (DJ mixes have natural overlaps)
        tt_duration = max(tt['timestamp_end'] - tt['timestamp_start'], 1)
        overlaps = False
        for s_start, s_end in strong_ranges:
            overlap_start = max(tt['timestamp_start'], s_start)
            overlap_end = min(tt['timestamp_end'], s_end)
            overlap = max(0, overlap_end - overlap_start)
            if overlap > tt_duration * 0.5:
                overlaps = True
                break

        if not overlaps:
            merged.append(tt)
            added += 1

    # Step 2: Remove our weak single-hit results that fall within a TrackID track's range
    # These are likely misidentifications of the track that TrackID correctly identified
    if trackid_ranges:
        filtered = []
        removed = 0
        for t in merged:
            if t.get('segment_count', 0) <= 1 and 'trackid' not in t.get('engines', []):
                # Check if this weak result falls within any TrackID track's range
                t_mid = (t['timestamp_start'] + t['timestamp_end']) / 2
                inside_trackid = any(
                    tr_start <= t_mid <= tr_end
                    for tr_start, tr_end in trackid_ranges
                )
                if inside_trackid:
                    removed += 1
                    continue
            filtered.append(t)
        merged = filtered
        if removed:
            logger.info(f'Removed {removed} weak results overlapping with TrackID tracks')

    if added:
        logger.info(f'Merged {added} TrackID.net tracks into tracklist')

    merged.sort(key=lambda t: t['timestamp_start'])
    return merged
