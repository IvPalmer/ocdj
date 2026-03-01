import logging
import os

logger = logging.getLogger(__name__)


def download_audio(url, output_dir):
    """Download audio from a URL using yt-dlp. Returns (audio_path, info_dict)."""
    import yt_dlp

    os.makedirs(output_dir, exist_ok=True)

    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': os.path.join(output_dir, '%(id)s.%(ext)s'),
        'quiet': True,
        'no_warnings': True,
        'socket_timeout': 30,
        'retries': 3,
        'fragment_retries': 5,
        'noprogress': True,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)

    # Find the downloaded mp3
    video_id = info.get('id', 'audio')
    audio_path = os.path.join(output_dir, f'{video_id}.mp3')

    # yt-dlp may use a different extension, search for it
    if not os.path.exists(audio_path):
        for f in os.listdir(output_dir):
            if f.endswith('.mp3'):
                audio_path = os.path.join(output_dir, f)
                break

    if not os.path.exists(audio_path):
        raise FileNotFoundError(f'Downloaded audio not found in {output_dir}')

    logger.info(f'Downloaded audio: {audio_path} ({info.get("duration", "?")}s)')
    return audio_path, info
