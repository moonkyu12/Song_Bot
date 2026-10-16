import asyncio
import urllib.parse
import urllib.request
import re
from typing import Optional, Dict, Any, List
import yt_dlp

YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'extractaudio': True,
    'audioformat': 'mp3',
    'outtmpl': '%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'ytsearch1',
    'source_address': '0.0.0.0',
}

YOUTUBE_REGEX = re.compile(
    r'^(https?://)?(www\.)?(youtube\.com|youtu\.be)/(watch\?v=|shorts/|embed/)?([a-zA-Z0-9_-]+)'
)

def is_youtube_url(url: str) -> bool:
    return bool(YOUTUBE_REGEX.match(url.strip()))

def is_playlist_url(url: str) -> bool:
    u = url.strip()
    return is_youtube_url(u) and ('list=' in u or '/playlist' in u)

def get_query_candidates(query: str) -> List[str]:
    query = query.strip()
    candidates = [query]

    no_brackets = re.sub(r'\[.*?\]|\(.*?\)', ' ', query)
    no_brackets = re.sub(r'[/|\\#\-–~]', ' ', no_brackets)
    no_brackets = re.sub(r'\s+', ' ', no_brackets).strip()
    if no_brackets and no_brackets not in candidates:
        candidates.append(no_brackets)

    parts = re.split(r'[/|\-–]', query)
    if len(parts) > 1:
        first_part = re.sub(r'\[.*?\]|\(.*?\)', ' ', parts[0]).strip()
        first_part = re.sub(r'\s+', ' ', first_part).strip()
        if first_part and first_part not in candidates:
            candidates.append(first_part)

    cleaned_specials = re.sub(r'[{}\[\]()\/|\\#_~]', ' ', query)
    cleaned_specials = re.sub(r'\s+', ' ', cleaned_specials).strip()
    if cleaned_specials and cleaned_specials not in candidates:
        candidates.append(cleaned_specials)

    return candidates

def search_youtube_first_video(query: str) -> Optional[str]:
    try:
        encoded = urllib.parse.quote(query.strip())
        search_url = f"https://www.youtube.com/results?search_query={encoded}"
        req = urllib.request.Request(
            search_url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
            }
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8', errors='ignore')

        video_ids = re.findall(r'watch\?v=([a-zA-Z0-9_-]{11})', html)
        seen = set()
        unique_ids = [vid for vid in video_ids if not (vid in seen or seen.add(vid))]

        if unique_ids:
            return f"https://www.youtube.com/watch?v={unique_ids[0]}"
    except Exception:
        pass
    return None

def _extract_playlist_sync(url: str) -> Optional[Dict[str, Any]]:
    opts = {
        'extract_flat': 'in_playlist',
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(url.strip(), download=False)
            if not info or 'entries' not in info:
                return None

            items = []
            for entry in info['entries']:
                if not entry:
                    continue
                v_url = entry.get('url')
                if not v_url or not v_url.startswith('http'):
                    v_id = entry.get('id')
                    if v_id:
                        v_url = f"https://www.youtube.com/watch?v={v_id}"
                if not v_url:
                    continue

                thumb = None
                if entry.get('thumbnails'):
                    thumb = entry['thumbnails'][-1].get('url')
                elif entry.get('thumbnail'):
                    thumb = entry['thumbnail']

                items.append({
                    'title': entry.get('title', 'Unknown Title'),
                    'artist': entry.get('uploader') or entry.get('channel', 'YouTube'),
                    'platform': 'YouTube',
                    'source_url': v_url,
                    'stream_url': None,
                    'webpage_url': v_url,
                    'duration': entry.get('duration', 0),
                    'thumbnail': thumb,
                })

            if not items:
                return None

            return {
                'is_playlist': True,
                'playlist_title': info.get('title', 'YouTube 재생목록'),
                'items': items
            }
        except Exception:
            return None

async def get_playlist_info(url: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_extract_playlist_sync, url)

def _extract_sync(query_or_url: str) -> Optional[Dict[str, Any]]:
    query_or_url = query_or_url.strip()

    if is_youtube_url(query_or_url):
        with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
            try:
                info = ydl.extract_info(query_or_url, download=False)
                if info:
                    if 'entries' in info and info['entries']:
                        info = info['entries'][0]
                    return {
                        'title': info.get('title', 'Unknown Title'),
                        'stream_url': info.get('url'),
                        'webpage_url': info.get('webpage_url') or query_or_url,
                        'duration': info.get('duration', 0),
                        'thumbnail': info.get('thumbnail'),
                        'channel': info.get('uploader') or info.get('channel', 'Unknown Artist'),
                        'platform': 'YouTube'
                    }
            except Exception:
                return None

    candidates = get_query_candidates(query_or_url)

    with yt_dlp.YoutubeDL(YTDL_OPTIONS) as ydl:
        for candidate in candidates:
            quick_url = search_youtube_first_video(candidate)
            target = quick_url if quick_url else f"ytsearch1:{candidate}"

            try:
                info = ydl.extract_info(target, download=False)
                if not info:
                    continue

                if 'entries' in info and info['entries']:
                    info = info['entries'][0]

                if info and info.get('url'):
                    return {
                        'title': info.get('title', 'Unknown Title'),
                        'stream_url': info.get('url'),
                        'webpage_url': info.get('webpage_url') or f"https://www.youtube.com/watch?v={info.get('id')}",
                        'duration': info.get('duration', 0),
                        'thumbnail': info.get('thumbnail'),
                        'channel': info.get('uploader') or info.get('channel', 'Unknown Artist'),
                        'platform': 'YouTube'
                    }
            except Exception:
                continue

    return None

async def get_youtube_info(query_or_url: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_extract_sync, query_or_url)

def _extract_comments_sync(webpage_url: str, max_comments: int = 5) -> List[Dict[str, Any]]:
    opts = {
        'getcomments': True,
        'extractor_args': {
            'youtube': {
                'max_comments': [str(max_comments)],
                'comment_sort': ['top']
            }
        },
        'skip_download': True,
        'quiet': True,
        'no_warnings': True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(webpage_url, download=False)
            raw_comments = info.get('comments', []) or []
            results = []
            for c in raw_comments[:max_comments]:
                results.append({
                    'author': c.get('author', '익명'),
                    'text': c.get('text', '').replace('\n', ' ').strip()[:100],
                    'like_count': c.get('like_count', 0),
                })
            return results
        except Exception:
            return []

async def get_video_comments(webpage_url: str, max_comments: int = 5) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(_extract_comments_sync, webpage_url, max_comments)
