from typing import Optional, Dict, Any
from .youtube import is_youtube_url, is_playlist_url, get_youtube_info, get_playlist_info
from .spotify import is_spotify_url, get_spotify_info
from .apple_music import is_apple_music_url, get_apple_music_info
from .melon import is_melon_url, get_melon_info

async def resolve_music(query_or_url: str) -> Optional[Dict[str, Any]]:
    query_or_url = query_or_url.strip()
    if not query_or_url:
        return None

    if is_playlist_url(query_or_url):
        playlist_data = await get_playlist_info(query_or_url)
        if playlist_data and playlist_data.get('items'):
            return playlist_data

    meta = None
    platform = "YouTube"

    if is_melon_url(query_or_url):
        platform = "Melon"
        meta = await get_melon_info(query_or_url)
        if not meta:
            return None

    elif is_spotify_url(query_or_url):
        platform = "Spotify"
        meta = await get_spotify_info(query_or_url)
        if not meta:
            return None

    elif is_apple_music_url(query_or_url):
        platform = "Apple Music"
        meta = await get_apple_music_info(query_or_url)
        if not meta:
            return None

    if meta:
        search_query = meta['query']
        yt_info = await get_youtube_info(search_query)
        if not yt_info:
            return None

        return {
            'title': meta.get('title') or yt_info.get('title'),
            'artist': meta.get('artist') or yt_info.get('channel', ''),
            'platform': platform,
            'source_url': query_or_url,
            'stream_url': yt_info.get('stream_url'),
            'webpage_url': yt_info.get('webpage_url'),
            'duration': yt_info.get('duration', 0),
            'thumbnail': meta.get('thumbnail') or yt_info.get('thumbnail'),
            'youtube_title': yt_info.get('title')
        }
    else:
        yt_info = await get_youtube_info(query_or_url)
        if not yt_info:
            return None

        return {
            'title': yt_info.get('title'),
            'artist': yt_info.get('channel', ''),
            'platform': 'YouTube',
            'source_url': query_or_url,
            'stream_url': yt_info.get('stream_url'),
            'webpage_url': yt_info.get('webpage_url'),
            'duration': yt_info.get('duration', 0),
            'thumbnail': yt_info.get('thumbnail'),
            'youtube_title': yt_info.get('title')
        }
