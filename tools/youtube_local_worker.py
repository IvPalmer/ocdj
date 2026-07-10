#!/usr/bin/env python3
"""Download public YouTube jobs from the Mac and return audio to OCDJ.

This worker deliberately never reads browser cookies. It exists because the
VPS network is bot-blocked while the operator's normal Mac egress can fetch
public videos without authentication.
"""
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import requests


BASE_URL = os.environ.get('OCDJ_YOUTUBE_WORKER_URL', '').rstrip('/')
TOKEN = os.environ.get('OCDJ_YOUTUBE_WORKER_TOKEN', '').strip()
POLL_SECONDS = float(os.environ.get('OCDJ_YOUTUBE_WORKER_POLL_SECONDS', '8'))
FETCH_TIMEOUT = int(os.environ.get('OCDJ_YOUTUBE_WORKER_FETCH_TIMEOUT', '900'))
YT_DLP = os.environ.get('OCDJ_YT_DLP', shutil.which('yt-dlp') or 'yt-dlp')


def _headers():
    return {'Authorization': f'Bearer {TOKEN}'}


def _claim(session):
    response = session.get(
        f'{BASE_URL}/api/ytfetch/worker/claim/',
        headers=_headers(),
        timeout=30,
    )
    response.raise_for_status()
    return response.json().get('job')


def _post_failure(session, job_id, message):
    try:
        session.post(
            f'{BASE_URL}/api/ytfetch/worker/jobs/{job_id}/fail/',
            headers=_headers(),
            data={'error': message[-1000:]},
            timeout=30,
        ).raise_for_status()
    except requests.RequestException as exc:
        print(f'Could not report YouTube job {job_id} failure: {exc}', flush=True)


def _metadata(url):
    result = subprocess.run(
        [
            YT_DLP, '--no-cookies', '--js-runtimes', 'node',
            '--remote-components', 'ejs:github', '--no-playlist',
            '--skip-download', '--print', '%(id)s\t%(uploader)s\t%(title)s',
            '--', url,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        return {}
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return {}
    parts = lines[-1].split('\t', 2)
    if len(parts) != 3:
        return {}
    return {'video_id': parts[0], 'uploader': parts[1], 'title': parts[2]}


def _download(url, destination):
    template = str(destination / '%(artist,creator,uploader|YouTube)s - %(title)s [%(id)s].%(ext)s')
    result = subprocess.run(
        [
            YT_DLP, '--no-cookies', '--js-runtimes', 'node',
            '--remote-components', 'ejs:github', '--no-playlist',
            '-f', 'bestaudio/best', '--extract-audio', '--audio-format', 'wav',
            '--audio-quality', '0', '--output', template,
            '--print', 'after_move:filepath', '--no-progress', '--', url,
        ],
        capture_output=True,
        text=True,
        timeout=FETCH_TIMEOUT,
    )
    if result.returncode != 0:
        raise RuntimeError((result.stderr or 'yt-dlp failed').strip()[-1000:])
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    filepath = Path(lines[-1]) if lines else None
    if filepath is None or not filepath.is_file():
        raise RuntimeError('yt-dlp completed without an output file')
    return filepath


def _process_one(session, job):
    job_id = job['id']
    metadata = _metadata(job['url'])
    with tempfile.TemporaryDirectory(prefix='ocdj-youtube-') as tmp:
        filepath = _download(job['url'], Path(tmp))
        with filepath.open('rb') as audio:
            response = session.post(
                f'{BASE_URL}/api/ytfetch/worker/jobs/{job_id}/complete/',
                headers=_headers(),
                data=metadata,
                files={'file': (filepath.name, audio, 'audio/wav')},
                timeout=300,
            )
        response.raise_for_status()
    print(f'YouTube job {job_id} completed: {metadata.get("title") or job["url"]}', flush=True)


def main():
    if not BASE_URL or not TOKEN:
        raise SystemExit('Set OCDJ_YOUTUBE_WORKER_URL and OCDJ_YOUTUBE_WORKER_TOKEN')
    session = requests.Session()
    print(f'YouTube local worker listening for {BASE_URL}', flush=True)
    while True:
        try:
            job = _claim(session)
            if job:
                try:
                    _process_one(session, job)
                except Exception as exc:
                    print(f'YouTube job {job["id"]} failed: {exc}', flush=True)
                    _post_failure(session, job['id'], str(exc))
            else:
                time.sleep(POLL_SECONDS)
        except requests.RequestException as exc:
            print(f'YouTube worker connection error: {exc}', flush=True)
            time.sleep(max(POLL_SECONDS, 15))


if __name__ == '__main__':
    main()
