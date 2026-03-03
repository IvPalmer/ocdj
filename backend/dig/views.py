import json
import logging
import re
import subprocess
from collections import OrderedDict
from urllib.parse import urlparse

import requests
from django.http import HttpResponse
from django.utils.html import escape
from django.views.decorators.clickjacking import xframe_options_exempt
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework import status as http_status

from core.views import get_config
from wanted.models import WantedSource, WantedItem
from wanted.serializers import WantedItemSerializer
from wanted.services.dedup import check_duplicates
from .serializers import DigAddSerializer, DigBatchSerializer, DigCheckSerializer

logger = logging.getLogger(__name__)


def _get_or_create_source(source_site, source_url=''):
    """Get or create a WantedSource for the dig extension."""
    source_type = source_site if source_site != 'dig' else 'manual'
    source, _ = WantedSource.objects.get_or_create(
        source_type='dig',
        defaults={'name': 'Dig (Browser Extension)', 'url': ''},
    )
    return source


@api_view(['POST'])
def add_item(request):
    """Add a single item from the browser extension with dedup check."""
    ser = DigAddSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    # Check for duplicates
    tracks = [{'artist': data.get('artist', ''), 'title': data.get('title', '')}]
    check_duplicates(tracks)
    track = tracks[0]

    if track.get('is_duplicate'):
        duplicate_item = WantedItem.objects.filter(id=track['duplicate_of_id']).first()
        return Response({
            'created': False,
            'duplicate': True,
            'duplicate_of': WantedItemSerializer(duplicate_item).data if duplicate_item else None,
            'fuzzy_score': track.get('fuzzy_score'),
        })

    source = _get_or_create_source(data['source_site'], data.get('source_url', ''))

    notes_parts = []
    if data.get('notes'):
        notes_parts.append(data['notes'])
    if data.get('source_url'):
        notes_parts.append(data['source_url'])

    item = WantedItem.objects.create(
        artist=data.get('artist', ''),
        title=data.get('title', ''),
        release_name=data.get('release_name', ''),
        catalog_number=data.get('catalog_number', ''),
        label=data.get('label', ''),
        notes='\n'.join(notes_parts),
        source=source,
    )

    return Response({
        'created': True,
        'duplicate': False,
        'item': WantedItemSerializer(item).data,
    }, status=http_status.HTTP_201_CREATED)


@api_view(['POST'])
def batch_add(request):
    """Batch add items from label/listing pages."""
    ser = DigBatchSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    items_data = data['items']
    skip_dupes = data.get('skip_duplicates', True)

    # Run dedup check on all items
    check_duplicates(items_data)

    source = _get_or_create_source(data['source_site'], data.get('source_url', ''))

    created_items = []
    duplicate_items = []

    for item_data in items_data:
        if item_data.get('is_duplicate'):
            if skip_dupes:
                duplicate_items.append({
                    'artist': item_data.get('artist', ''),
                    'title': item_data.get('title', ''),
                    'duplicate_of_id': item_data.get('duplicate_of_id'),
                    'fuzzy_score': item_data.get('fuzzy_score'),
                })
                continue

        notes_parts = []
        if item_data.get('notes'):
            notes_parts.append(item_data['notes'])
        if data.get('source_url'):
            notes_parts.append(data['source_url'])

        item = WantedItem.objects.create(
            artist=item_data.get('artist', ''),
            title=item_data.get('title', ''),
            release_name=item_data.get('release_name', ''),
            catalog_number=item_data.get('catalog_number', ''),
            label=item_data.get('label', ''),
            notes='\n'.join(notes_parts),
            source=source,
        )
        created_items.append(item)

    return Response({
        'created': len(created_items),
        'skipped_duplicates': len(duplicate_items),
        'items': WantedItemSerializer(created_items, many=True).data,
        'duplicates': duplicate_items,
    }, status=http_status.HTTP_201_CREATED)


@api_view(['POST'])
def check_items(request):
    """Check items for duplicates without adding them."""
    ser = DigCheckSerializer(data=request.data)
    ser.is_valid(raise_exception=True)

    items = ser.validated_data['items']
    check_duplicates(items)

    results = []
    for item in items:
        results.append({
            'artist': item.get('artist', ''),
            'title': item.get('title', ''),
            'is_duplicate': item.get('is_duplicate', False),
            'duplicate_of_id': item.get('duplicate_of_id'),
            'fuzzy_score': item.get('fuzzy_score'),
        })

    return Response({'results': results})


_video_cache = OrderedDict()
_VIDEO_CACHE_MAX = 200


@api_view(['GET'])
def release_videos(request, release_id):
    """Fetch YouTube videos for a Discogs release."""
    if release_id in _video_cache:
        _video_cache.move_to_end(release_id)
        return Response(_video_cache[release_id])

    token = get_config('DISCOGS_PERSONAL_TOKEN')
    if not token:
        return Response(
            {'error': 'Discogs token not configured'},
            status=http_status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    try:
        resp = requests.get(
            f'https://api.discogs.com/releases/{release_id}',
            headers={
                'Authorization': f'Discogs token={token}',
                'User-Agent': 'ocdj/1.0',
            },
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error('Discogs API error for release %s: %s', release_id, e)
        return Response(
            {'error': f'Discogs API error: {e}'},
            status=http_status.HTTP_502_BAD_GATEWAY,
        )

    data = resp.json()

    artists_list = data.get('artists', [])
    # Deduplicate artist names (Discogs sometimes lists same artist twice)
    seen_names = []
    for a in artists_list:
        name = a.get('name', '').strip()
        if name and name not in seen_names:
            seen_names.append(name)
    artist = ', '.join(seen_names)

    videos = []
    seen_ids = set()
    for v in data.get('videos', []):
        uri = v.get('uri', '')
        m = re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([\w-]+)', uri)
        if m:
            vid = m.group(1)
            if vid in seen_ids:
                continue
            seen_ids.add(vid)
            videos.append({
                'videoId': vid,
                'title': v.get('title', ''),
                'duration': v.get('duration', 0),
            })

    result = {
        'releaseId': release_id,
        'artist': artist,
        'title': data.get('title', ''),
        'thumb': data.get('thumb', ''),
        'year': data.get('year', ''),
        'videos': videos,
    }

    _video_cache[release_id] = result
    if len(_video_cache) > _VIDEO_CACHE_MAX:
        _video_cache.popitem(last=False)

    return Response(result)


@xframe_options_exempt
def embed_proxy(request):
    """Generic embed proxy for third-party players.

    Chrome extension pages have chrome-extension:// origin which some embeds
    reject (e.g. SoundCloud verification). This wraps the embed in a localhost
    page so the widget sees an http:// embedder.
    """
    url = request.GET.get('url', '')
    if not url:
        return HttpResponse('Missing URL parameter', status=400)

    parsed = urlparse(url)
    allowed_hosts = {
        'w.soundcloud.com',
        'bandcamp.com',
        'open.spotify.com',
        'www.youtube-nocookie.com',
    }
    if parsed.hostname not in allowed_hosts:
        return HttpResponse('Invalid embed domain', status=400)

    safe_url = escape(url)

    html = f'''<!DOCTYPE html>
<html><head><style>*{{margin:0;padding:0}}html,body{{width:100%;height:100%;overflow:hidden;background:#111}}</style></head>
<body>
<iframe id="embed" src="{safe_url}"
  style="width:100%;height:100%;border:0"
  allow="autoplay; encrypted-media"
  allowfullscreen></iframe>
<script>
var embed = document.getElementById('embed');

// Relay play/pause commands from parent (side panel) to inner embed
window.addEventListener('message', function(e) {{
  if (!embed || !embed.contentWindow) return;
  var data = e.data;
  if (!data || !data.action) return;

  if (data.action === 'toggle' || data.action === 'play' || data.action === 'pause') {{
    // SoundCloud Widget API (JSON string format)
    var method = data.action === 'toggle' ? 'toggle' : data.action;
    embed.contentWindow.postMessage(JSON.stringify({{method: method}}), '*');
    // Spotify IFrame API
    embed.contentWindow.postMessage({{command: data.action}}, '*');
  }}
}});

// Auto-play: after embed loads, try triggering playback
embed.addEventListener('load', function() {{
  setTimeout(function() {{
    // SoundCloud Widget API
    embed.contentWindow.postMessage(JSON.stringify({{method: 'play'}}), '*');
    // Spotify
    embed.contentWindow.postMessage({{command: 'resume'}}, '*');
  }}, 800);
}});
</script>
</body></html>'''

    resp = HttpResponse(html, content_type='text/html')
    resp['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return resp


@xframe_options_exempt
def player_page(request):
    """Minimal YouTube embed page — loaded by extension side panel.

    Chrome extension pages have chrome-extension:// origin which YouTube
    rejects (Error 153). This page has http://localhost origin which YouTube
    accepts, acting as a proxy for the embed.
    """
    video_id = request.GET.get('v', '')
    if not video_id or not re.match(r'^[a-zA-Z0-9_-]{6,20}$', video_id):
        return HttpResponse('Invalid video ID', status=400)

    html = f'''<!DOCTYPE html>
<html><head><style>*{{margin:0;padding:0}}html,body{{width:100%;height:100%;overflow:hidden;background:#000}}</style></head>
<body>
<iframe id="yt"
  src="https://www.youtube-nocookie.com/embed/{video_id}?autoplay=1&enablejsapi=1&origin=http%3A%2F%2Flocalhost%3A8002&rel=0&modestbranding=1"
  style="width:100%;height:100%;border:0"
  allow="autoplay; encrypted-media"
  allowfullscreen></iframe>
<script>
const yt = document.getElementById('yt');
window.addEventListener('message', function(e) {{
  if (e.source === window.parent && e.data && e.data.action) {{
    if (e.data.action === 'command') {{
      yt.contentWindow.postMessage(JSON.stringify({{
        event: 'command', func: e.data.func, args: ''
      }}), '*');
    }}
  }} else if (e.origin && e.origin.includes('youtube')) {{
    window.parent.postMessage(e.data, '*');
  }}
}});
</script>
</body></html>'''

    resp = HttpResponse(html, content_type='text/html')
    resp['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return resp


@api_view(['GET'])
def yt_search(request):
    """Search YouTube for a track and return the first video result."""
    query = request.GET.get('q', '').strip()
    if not query:
        return Response({'error': 'No query'}, status=http_status.HTTP_400_BAD_REQUEST)

    try:
        result = subprocess.run(
            ['yt-dlp', '--no-download', '--print-json', '--no-playlist',
             '--no-warnings', f'ytsearch1:{query}'],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return Response({'error': 'Search timed out'}, status=http_status.HTTP_504_GATEWAY_TIMEOUT)

    if result.returncode != 0 or not result.stdout.strip():
        return Response({'error': 'No results'}, status=http_status.HTTP_404_NOT_FOUND)

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return Response({'error': 'Parse error'}, status=http_status.HTTP_502_BAD_GATEWAY)

    return Response({
        'videoId': data.get('id', ''),
        'title': data.get('title', ''),
        'duration': data.get('duration', 0),
    })


@api_view(['GET'])
def dig_status(request):
    """Health check and status for the extension."""
    wanted_count = WantedItem.objects.filter(
        status__in=['pending', 'identified', 'searching']
    ).count()

    # Check which services are configured
    services = {
        'discogs': bool(get_config('DISCOGS_PERSONAL_TOKEN')),
        'youtube': bool(get_config('YOUTUBE_API_KEY')),
        'soundcloud': bool(get_config('SC_CLIENT_ID')),
        'spotify': bool(get_config('SPOTIFY_CLIENT_ID')),
        'soulseek': True,  # Always available via slskd container
    }

    return Response({
        'healthy': True,
        'wanted_count': wanted_count,
        'services': services,
    })


@api_view(['GET'])
def bandcamp_streams(request):
    """Extract Bandcamp audio streams via yt-dlp."""
    url = request.GET.get('url', '').strip()
    if not url:
        return Response({'error': 'Missing URL'}, status=http_status.HTTP_400_BAD_REQUEST)

    parsed = urlparse(url)
    hostname = parsed.hostname or ''
    if not hostname.endswith('bandcamp.com') and 'bandcamp' not in hostname:
        return Response({'error': 'Not a Bandcamp URL'}, status=http_status.HTTP_400_BAD_REQUEST)

    try:
        result = subprocess.run(
            ['yt-dlp', '--no-download', '-j', '--no-warnings', url],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return Response({'error': 'Extraction timed out'}, status=http_status.HTTP_504_GATEWAY_TIMEOUT)

    tracks = []
    for line in result.stdout.strip().split('\n'):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            audio_url = data.get('url', '')
            if not audio_url:
                for fmt in reversed(data.get('formats', [])):
                    if fmt.get('acodec', 'none') != 'none':
                        audio_url = fmt.get('url', '')
                        break
            if audio_url:
                tracks.append({
                    'title': data.get('track', data.get('title', '')),
                    'url': audio_url,
                    'duration': data.get('duration', 0),
                    'artist': data.get('artist', data.get('uploader', '')),
                    'track_num': data.get('track_number', len(tracks) + 1),
                })
        except json.JSONDecodeError:
            continue

    if not tracks:
        return Response({'error': 'No playable tracks found'}, status=http_status.HTTP_404_NOT_FOUND)

    return Response({'tracks': tracks})


@xframe_options_exempt
def bandcamp_player(request):
    """Custom Bandcamp audio player with HTML5 <audio>.

    Loaded in the side panel iframe. Fetches stream URLs from the
    bandcamp_streams endpoint and builds a fully controllable player
    with autoplay, transport controls via postMessage, and state relay.
    """
    url = request.GET.get('url', '')
    thumb = request.GET.get('thumb', '')
    title = request.GET.get('title', '')
    artist = request.GET.get('artist', '')

    if not url:
        return HttpResponse('Missing URL', status=400)

    parsed = urlparse(url)
    hostname = parsed.hostname or ''
    if not hostname.endswith('bandcamp.com') and 'bandcamp' not in hostname:
        return HttpResponse('Not a Bandcamp URL', status=400)

    safe_url = escape(url)
    safe_thumb = escape(thumb)
    safe_title = escape(title)
    safe_artist = escape(artist)

    html = f'''<!DOCTYPE html>
<html><head><style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{background:#1a1a2e;color:#e0e0e0;font-family:-apple-system,BlinkMacSystemFont,sans-serif;height:100vh;overflow:hidden}}
.container{{display:flex;flex-direction:column;height:100%;padding:12px}}
.artwork{{width:100%;max-height:180px;object-fit:cover;border-radius:6px;margin-bottom:8px}}
.info{{margin-bottom:8px}}
.info .title{{font-size:14px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.info .artist{{font-size:12px;color:#aaa}}
.loading{{text-align:center;padding:20px;color:#888}}
.spinner{{display:inline-block;width:24px;height:24px;border:2px solid #444;border-top-color:#0d9488;border-radius:50%;animation:spin .8s linear infinite}}
@keyframes spin{{to{{transform:rotate(360deg)}}}}
.tracklist{{flex:1;overflow-y:auto;scrollbar-width:thin;scrollbar-color:#444 transparent}}
.track{{display:flex;align-items:center;padding:5px 8px;border-radius:4px;cursor:pointer;font-size:12px;gap:6px}}
.track:hover{{background:rgba(255,255,255,.05)}}
.track.active{{background:rgba(13,148,136,.2);color:#0d9488}}
.track-num{{color:#666;min-width:18px;text-align:right;font-size:11px}}
.track-title{{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
.track-dur{{color:#666;font-size:11px}}
.controls{{display:flex;align-items:center;gap:6px;padding:8px 0;border-top:1px solid #333;margin-top:auto}}
.controls button{{background:none;border:none;color:#e0e0e0;cursor:pointer;padding:4px}}
.controls button:hover{{color:#0d9488}}
.progress{{flex:1;height:4px;background:#333;border-radius:2px;cursor:pointer}}
.progress-fill{{height:100%;background:#0d9488;border-radius:2px;width:0%;transition:width .3s}}
.time{{font-size:10px;color:#888;min-width:32px}}
.error{{text-align:center;padding:20px;color:#ef4444}}
</style></head>
<body>
<div class="container">
  <img class="artwork" id="artwork" src="{safe_thumb}" style="display:{'block' if thumb else 'none'}">
  <div class="info"><div class="title" id="title">{safe_title}</div><div class="artist" id="artist">{safe_artist}</div></div>
  <div class="loading" id="loading"><div class="spinner"></div><div style="margin-top:8px">Loading tracks...</div></div>
  <div class="tracklist" id="tracklist" style="display:none"></div>
  <div class="controls" id="controls" style="display:none">
    <button id="btn-prev" title="Previous"><svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor"><rect x="1" y="2" width="2" height="10"/><polygon points="13,2 5,7 13,12"/></svg></button>
    <button id="btn-play" title="Play/Pause">
      <svg id="icon-play" width="16" height="16" viewBox="0 0 16 16" fill="currentColor"><polygon points="4,1 14,8 4,15"/></svg>
      <svg id="icon-pause" width="16" height="16" viewBox="0 0 16 16" fill="currentColor" style="display:none"><rect x="2" y="1" width="4" height="14"/><rect x="10" y="1" width="4" height="14"/></svg>
    </button>
    <button id="btn-next" title="Next"><svg width="14" height="14" viewBox="0 0 14 14" fill="currentColor"><polygon points="1,2 9,7 1,12"/><rect x="11" y="2" width="2" height="10"/></svg></button>
    <span class="time" id="time-current">0:00</span>
    <div class="progress" id="progress"><div class="progress-fill" id="progress-fill"></div></div>
    <span class="time" id="time-total">0:00</span>
  </div>
</div>
<audio id="audio" preload="auto"></audio>
<script>
(function(){{
  var audio=document.getElementById('audio'),tracks=[],cur=0,src="{safe_url}";

  fetch('/api/dig/bandcamp-streams/?url='+encodeURIComponent(src))
    .then(function(r){{return r.json()}})
    .then(function(d){{
      document.getElementById('loading').style.display='none';
      if(!d.tracks||!d.tracks.length){{
        document.getElementById('loading').innerHTML='<div class="error">No playable tracks</div>';
        document.getElementById('loading').style.display='block';
        return;
      }}
      tracks=d.tracks;
      render();
      document.getElementById('tracklist').style.display='block';
      document.getElementById('controls').style.display='flex';
      play(0);
    }})
    .catch(function(e){{
      document.getElementById('loading').innerHTML='<div class="error">Failed: '+e.message+'</div>';
    }});

  function render(){{
    var list=document.getElementById('tracklist');list.innerHTML='';
    tracks.forEach(function(t,i){{
      var div=document.createElement('div');
      div.className='track'+(i===cur?' active':'');
      div.innerHTML='<span class="track-num">'+(i+1)+'</span><span class="track-title">'+esc(t.title)+'</span><span class="track-dur">'+fmt(t.duration)+'</span>';
      div.addEventListener('click',function(){{play(i)}});
      list.appendChild(div);
    }});
  }}

  function play(i){{
    if(i<0||i>=tracks.length)return;
    cur=i;audio.src=tracks[i].url;
    audio.play().catch(function(){{}});
    render();notify('playing');
  }}

  function esc(s){{var d=document.createElement('div');d.textContent=s;return d.innerHTML}}
  function fmt(s){{if(!s)return'0:00';var m=Math.floor(s/60),sec=Math.floor(s%60);return m+':'+(sec<10?'0':'')+sec}}

  audio.addEventListener('play',function(){{
    document.getElementById('icon-play').style.display='none';
    document.getElementById('icon-pause').style.display='block';
    notify('playing');
  }});
  audio.addEventListener('pause',function(){{
    document.getElementById('icon-play').style.display='block';
    document.getElementById('icon-pause').style.display='none';
    notify('paused');
  }});
  audio.addEventListener('ended',function(){{
    if(cur<tracks.length-1)play(cur+1);else notify('ended');
  }});
  audio.addEventListener('timeupdate',function(){{
    var c=audio.currentTime,d=audio.duration||0;
    document.getElementById('time-current').textContent=fmt(c);
    document.getElementById('time-total').textContent=fmt(d);
    if(d>0)document.getElementById('progress-fill').style.width=(c/d*100)+'%';
  }});

  document.getElementById('btn-play').addEventListener('click',function(){{
    if(audio.paused)audio.play();else audio.pause();
  }});
  document.getElementById('btn-prev').addEventListener('click',function(){{
    if(audio.currentTime>3)audio.currentTime=0;
    else if(cur>0)play(cur-1);
  }});
  document.getElementById('btn-next').addEventListener('click',function(){{
    if(cur<tracks.length-1)play(cur+1);
  }});
  document.getElementById('progress').addEventListener('click',function(e){{
    var r=this.getBoundingClientRect(),p=(e.clientX-r.left)/r.width;
    if(audio.duration)audio.currentTime=p*audio.duration;
  }});

  window.addEventListener('message',function(e){{
    var d=e.data;if(!d||!d.action)return;
    switch(d.action){{
      case'play':audio.play().catch(function(){{}});break;
      case'pause':audio.pause();break;
      case'toggle':if(audio.paused)audio.play().catch(function(){{}});else audio.pause();break;
      case'next':if(cur<tracks.length-1)play(cur+1);else notify('ended');break;
      case'prev':if(audio.currentTime>3)audio.currentTime=0;else if(cur>0)play(cur-1);break;
    }}
  }});

  function notify(state){{
    window.parent.postMessage({{event:'bandcamp-state',state:state,track:cur,total:tracks.length}},'*');
  }}
}})();
</script>
</body></html>'''

    resp = HttpResponse(html, content_type='text/html')
    resp['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    return resp
