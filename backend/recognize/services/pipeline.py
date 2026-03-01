import logging
import os
import shutil
import threading

from django import db
from django.conf import settings

from recognize.models import RecognizeJob
from .downloader import download_audio
from .description_parser import parse_tracklist_from_description
from .segmenter import segment_audio, segment_gaps
from .recognition import recognize_segments
from .clustering import cluster_results, find_gaps

logger = logging.getLogger(__name__)

# Pipeline configuration
SEGMENT_DURATION = 10   # seconds per segment (optimal for Shazam on mixed audio)
SEGMENT_STEP = 15       # seconds between segment starts (~66% coverage)
GAP_THRESHOLD = 30      # min gap in seconds to trigger pass 2
GAP_SEGMENT_DURATION = 12
GAP_SEGMENT_STEP = 8
MAX_GAP_SEGMENTS = 100  # cap pass 2 to avoid excessive scanning of unrecognizable audio


def run_recognize(job_id):
    """Launch the recognition pipeline in a background thread."""
    thread = threading.Thread(
        target=_recognize_worker,
        args=(job_id,),
        daemon=True,
    )
    thread.start()


def _recognize_worker(job_id):
    try:
        job = RecognizeJob.objects.get(pk=job_id)

        # Step 1: Download audio
        job.status = 'downloading'
        job.save()

        output_dir = os.path.join(settings.MEDIA_ROOT, 'recognize', str(job_id))
        audio_path, info = download_audio(job.url, output_dir)

        job.title = info.get('title', '')[:500]
        job.duration_seconds = int(info.get('duration') or 0)
        job.save()

        # Step 2: Parse description for free tracklist
        description_tracks = parse_tracklist_from_description(info)
        job.description_tracks = description_tracks
        job.save()

        # Step 3: Segment audio (pass 1: 10s segments every 15s)
        job.status = 'recognizing'
        segments = segment_audio(
            audio_path,
            segment_duration=SEGMENT_DURATION,
            step=SEGMENT_STEP,
        )
        job.segments_total = len(segments)
        job.segments_done = 0
        job.save()

        # Step 4: Recognize pass 1 (concurrent ShazamIO)
        def on_progress_pass1(done, total):
            job.segments_done = done
            job.save(update_fields=['segments_done', 'updated'])

        raw_results = recognize_segments(segments, on_progress=on_progress_pass1)

        # Step 5: Find gaps and do pass 2 with longer segments
        duration = job.duration_seconds or 0
        gaps = find_gaps(raw_results, duration, min_gap=GAP_THRESHOLD, step=SEGMENT_STEP)

        if gaps:
            gap_segs = segment_gaps(
                audio_path, gaps,
                segment_duration=GAP_SEGMENT_DURATION,
                step=GAP_SEGMENT_STEP,
            )
            # Cap gap segments to avoid spending ages on unrecognizable audio
            if gap_segs and len(gap_segs) > MAX_GAP_SEGMENTS:
                logger.info(f'Capping gap segments from {len(gap_segs)} to {MAX_GAP_SEGMENTS}')
                gap_segs = gap_segs[:MAX_GAP_SEGMENTS]

            if gap_segs:
                job.segments_total += len(gap_segs)
                job.save()

                pass1_done = job.segments_done

                def on_progress_pass2(done, total):
                    job.segments_done = pass1_done + done
                    job.save(update_fields=['segments_done', 'updated'])

                # Pass 2 uses shorter timeout and single attempt — these are
                # segments that already failed in pass 1, no point being patient
                gap_results = recognize_segments(
                    gap_segs,
                    on_progress=on_progress_pass2,
                    timeout=10,
                    retries=1,
                )
                raw_results.extend(gap_results)
                # Re-sort by start_sec
                raw_results.sort(key=lambda r: r['start_sec'])

        # Step 6: Store raw results for re-clustering
        job.raw_results = raw_results

        # Step 7: Cluster results into tracklist
        tracklist = cluster_results(raw_results, description_tracks)

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
        db.connections.close_all()
