import asyncio
import re
import json
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any

SPOTIFY_REGEX = re.compile(
    r'^(https?://)?(open\.spotify\.com|spotify\.link)/track/([a-zA-Z0-9]+)'
)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
}

def is_spotify_url(url: str) -> bool:
    return 'spotify.com' in url or 'spotify.link' in url

def _extract_spotify_sync(url: str) -> Optional[Dict[str, Any]]:
    url = url.strip()

    track_match = re.search(r'/track/([a-zA-Z0-9]+)', url)
    track_id = track_match.group(1) if track_match else None

    if track_id:
        embed_url = f"https://open.spotify.com/embed/track/{track_id}"
        try:
            r = requests.get(embed_url, headers=HEADERS, timeout=8)
            if r.status_code == 200:
                soup = BeautifulSoup(r.text, 'html.parser')
                script_tag = soup.find('script', id='__NEXT_DATA__')
                if script_tag and script_tag.string:
                    data = json.loads(script_tag.string)
                    entity = data.get('props', {}).get('pageProps', {}).get('state', {}).get('data', {}).get('entity', {})
                    title = entity.get('name')
                    artists = [a.get('name') for a in entity.get('artists', []) if a.get('name')]
                    artist_str = ", ".join(artists) if artists else ""
                    images = entity.get('visualIdentity', {}).get('image', []) or []
                    thumbnail = images[0].get('url') if images else None

                    if title:
                        query = f"{title} {artist_str}".strip()
                        return {
                            'title': title,
                            'artist': artist_str,
                            'query': query,
                            'thumbnail': thumbnail,
                            'platform': 'Spotify'
                        }
        except Exception:
            pass

    try:
        oembed_url = f"https://open.spotify.com/oembed?url={url}"
        r = requests.get(oembed_url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            data = r.json()
            title = data.get('title')
            thumbnail = data.get('thumbnail_url')
            if title:
                return {
                    'title': title,
                    'artist': '',
                    'query': title,
                    'thumbnail': thumbnail,
                    'platform': 'Spotify'
                }
    except Exception:
        pass

    return None

async def get_spotify_info(url: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_extract_spotify_sync, url)
