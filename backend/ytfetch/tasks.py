"""Huey task for fetching YouTube audio into the organize pipeline.

Enqueuing `task_fetch` writes a row into the shared Huey sqlite DB; the worker
container claims it and runs yt-dlp. Routing through Huey (not a thread) means a
backend redeploy can't lose an in-flight fetch. Per-job `lock_task` keeps a
duplicate enqueue from spawning two concurrent yt-dlp runs for the same job.
"""
import logging
import os
import shutil
import subprocess
import tempfile

from huey.contrib.djhuey import db_task, lock_task

from core.services.config import get_config

logger = logging.getLogger(__name__)

# yt-dlp gets 15 minutes — enough for a long DJ set but bounded so a hung
# process (e.g. a network stall) eventually fails the job instead of pinning a
# worker forever.
FETCH_TIMEOUT = 900
# Metadata pre-pass is a quick network round-trip; keep it short so a slow
# probe never eats into the real download budget.
META_TIMEOUT = 120


# Writable directory holding the live, self-refreshing cookie jar. yt-dlp
# rewrites the cookie file whenever YouTube rotates a session cookie
# mid-request; letting it persist that write means the session maintains
# itself for as long as the account stays valid (months) instead of the
# static export going stale in days. A directory (not a single-file) mount is
# required — yt-dlp saves via a temp-file + atomic rename, which breaks a
# single-file bind mount (new inode) but works fine within a mounted dir.
COOKIE_STATE_DIR = os.environ.get(
    'YOUTUBE_COOKIE_STATE_DIR', '/srv/ocdj/ytcookies'
)


def _prepare_auth():
    """Return yt-dlp auth flags, using a self-refreshing live cookie copy.

    The configured YOUTUBE_COOKIES path is the read-only *seed*. On first use
    we copy it into COOKIE_STATE_DIR (writable) and thereafter point yt-dlp at
    that live copy, which it keeps fresh via YouTube's own rotation. Falls back
    to --cookies-from-browser, or to no auth at all — anonymous yt-dlp still
    succeeds most of the time, so a missing/expired jar degrades rather than
    breaks.
    """
    seed = str(get_config('YOUTUBE_COOKIES') or '').strip()
    if seed and os.path.exists(seed):
        try:
            os.makedirs(COOKIE_STATE_DIR, exist_ok=True)
            live = os.path.join(COOKIE_STATE_DIR, 'youtube_cookies.txt')
            if not os.path.exists(live):
                shutil.copyfile(seed, live)  # seed once; yt-dlp maintains it after
            return ['--cookies', live]
        except OSError as e:
            # State dir not writable/mountable — fall back to a throwaway copy
            # so a rotation write can't crash the run on the read-only seed.
            logger.warning(f'ytfetch: cookie state dir unusable ({e}); using temp copy')
            fd, tmp = tempfile.mkstemp(prefix='ytdlp-cookies-', suffix='.txt')
            os.close(fd)
            shutil.copyfile(seed, tmp)
            return ['--cookies', tmp]

    browser = str(get_config('YOUTUBE_COOKIES_FROM_BROWSER') or '').strip()
    if browser:
        return ['--cookies-from-browser', browser]

    return []


def _yt_dlp_network_args():
    """Return optional VPS egress arguments for YouTube requests."""
    proxy = str(os.environ.get('YOUTUBE_PROXY') or '').strip()
    return ['--proxy', proxy] if proxy else []


@db_task(retries=0)
def task_fetch(job_id: int):
    with lock_task(f'ytfetch-job-{job_id}'):
        run_fetch_job(job_id)


def _is_bot_check(text):
    lowered = (text or '').lower()
    return (
        'sign in to confirm' in lowered
        or "confirm you're not a bot" in lowered
        or 'confirm you’re not a bot' in lowered
    )


def _fail(job, stderr, bot_check=False, auth_configured=False):
    msg = (stderr or '').strip()[-500:]
    if bot_check:
        if auth_configured:
            hint = (
                "YouTube blocked the production server with a bot-check even "
                "though cookies are configured. This video may require a "
                "different session or a non-server network; retrying unchanged "
                "will likely fail. "
            )
        else:
            hint = (
                "YouTube bot-check blocked the production server's network "
                "egress. "
                "Configure an accepted server-side YouTube proxy or network "
                "route; cookies are not a durable fix. "
            )
        msg = hint + msg
    job.status = 'failed'
    job.error_message = msg
    job.save(update_fields=['status', 'error_message'])


def run_fetch_job(job_id):
    from .models import FetchJob

    job = FetchJob.objects.get(id=job_id)
    # Idempotency guard: only a queued job may start fetching. A duplicate
    # enqueue (or a stale Huey row re-claimed after the job already ran) must
    # not re-download or clobber a finished/failed status. The retry endpoint
    # resets status to 'queued' before re-enqueuing, so retries pass this.
    if job.status != 'queued':
        logger.info(
            f'ytfetch job {job_id}: skipping fetch, status is '
            f'{job.status!r} (expected queued)'
        )
        return
    job.status = 'fetching'
    job.save(update_fields=['status'])

    download_dir = os.path.join(
        get_config('SOULSEEK_DOWNLOAD_ROOT'), '01_downloaded', 'YouTube'
    )
    os.makedirs(download_dir, exist_ok=True)

    auth_args = _prepare_auth()
    network_args = _yt_dlp_network_args()
    # Metadata pre-pass (best effort). Populates title/uploader/id so the job
    # row reads nicely while the download runs. If it fails we still download.
    try:
        meta = subprocess.run(
            [
                'yt-dlp', '--js-runtimes', 'node',
                '--remote-components', 'ejs:github',
                *network_args,
                *auth_args, '--no-playlist',
                '--skip-download', '--print', '%(id)s\t%(uploader)s\t%(title)s',
                '--', job.url,
            ],
            capture_output=True, text=True, timeout=META_TIMEOUT,
        )
        if meta.returncode == 0 and meta.stdout.strip():
            parts = meta.stdout.strip().splitlines()[-1].split('\t')
            if len(parts) == 3:
                job.video_id = parts[0][:32]
                job.uploader = parts[1][:500]
                job.title = parts[2][:500]
                job.save(update_fields=['video_id', 'uploader', 'title'])
    except Exception as e:
        logger.warning(f'ytfetch metadata pre-pass failed for job {job_id}: {e}')

    # The [%(id)s] suffix makes filenames unique per video so same-titled
    # fetches can't collide; `|YouTube` provides a default when none of
    # artist/creator/uploader is populated.
    output_tmpl = os.path.join(
        download_dir,
        '%(artist,creator,uploader|YouTube)s - %(title)s [%(id)s].%(ext)s',
    )
    # `bestaudio/best` grabs the highest-bitrate audio stream YouTube serves
    # (typically opus ~160k; falls back to a muxed stream if no audio-only
    # exists). Extraction to WAV is a lossless PCM decode — zero extra quality
    # loss on top of YouTube's own encoding — and the pipeline's wav->aiff rule
    # is also lossless, so the delivered aiff preserves everything YouTube
    # gave us. `--audio-quality 0` is a no-op for wav but guards if the target
    # format is ever changed to a lossy one.
    cmd = [
        'yt-dlp', '--js-runtimes', 'node',
        '--remote-components', 'ejs:github',
        *network_args,
        *auth_args, '--no-playlist',
        '-f', 'bestaudio/best',
        '--extract-audio', '--audio-format', 'wav', '--audio-quality', '0',
        '--output', output_tmpl,
        '--print', 'after_move:filepath',
        '--no-progress', '--', job.url,
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FETCH_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        _fail(job, f'yt-dlp timed out after {FETCH_TIMEOUT}s')
        return

    if proc.returncode != 0:
        stderr = proc.stderr or ''
        _fail(
            job,
            stderr,
            bot_check=_is_bot_check(stderr),
            auth_configured=bool(auth_args),
        )
        return

    # `--print after_move:filepath` emits the final path on stdout; take the
    # last non-empty line to be robust against any leading yt-dlp chatter.
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    filepath = lines[-1].strip() if lines else ''
    if not filepath or not os.path.exists(filepath):
        stderr = proc.stderr or 'yt-dlp produced no output file'
        _fail(
            job,
            stderr,
            bot_check=_is_bot_check(stderr),
            auth_configured=bool(auth_args),
        )
        return

    job.downloaded_path = filepath
    job.status = 'downloaded'
    job.save(update_fields=['downloaded_path', 'status'])

    # Ingest into the organize pipeline and process. scan_completed_downloads()
    # sweeps 01_downloaded/ (incl. our YouTube/ subdir) and creates a
    # PipelineItem for the untracked file; we then link it and run it through.
    try:
        from organize.services.pipeline import (
            scan_completed_downloads, process_pipeline_item,
        )
        from organize.models import PipelineItem

        scan_completed_downloads()

        # Prefer the exact current_path match — unambiguous by construction.
        # The basename fallback only fires if the scan normalized the path
        # somehow; it must match a single stage='downloaded' item or we risk
        # linking (and processing) somebody else's file, so more than one
        # candidate is treated as ambiguous and left unlinked.
        item = PipelineItem.objects.filter(current_path=filepath).first()
        if item is None:
            basename = os.path.basename(filepath)
            candidates = list(
                PipelineItem.objects.filter(
                    stage='downloaded', original_filename=basename,
                )[:2]
            )
            if len(candidates) == 1:
                item = candidates[0]
            elif len(candidates) > 1:
                logger.warning(
                    f'ytfetch job {job_id}: multiple downloaded items match '
                    f'basename {basename!r}, leaving job unlinked'
                )
        if item is not None:
            job.pipeline_item = item
            job.save(update_fields=['pipeline_item'])
            process_pipeline_item(item.id)
        else:
            logger.warning(
                f'ytfetch job {job_id}: could not locate PipelineItem for '
                f'{filepath} after scan'
            )
    except Exception as e:
        # The bytes are safely downloaded and the job is marked downloaded;
        # a pipeline hiccup shouldn't flip the fetch to failed. The operator
        # can re-scan / process manually from the Organize tab.
        logger.error(f'ytfetch job {job_id}: pipeline ingest failed: {e}')
