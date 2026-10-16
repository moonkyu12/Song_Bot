import asyncio
import re
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any
from urllib.parse import urlparse, parse_qs

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
}

def is_melon_url(url: str) -> bool:
    return 'melon.com' in url

def _extract_melon_sync(url: str) -> Optional[Dict[str, Any]]:
    url = url.strip()
    try:
        song_id = None
        parsed = urlparse(url)
        qs = parse_qs(parsed.query)
        if 'songId' in qs:
            song_id = qs['songId'][0]
        else:
            m = re.search(r'songId=(\d+)', url)
            if m:
                song_id = m.group(1)

        target_url = f"https://www.melon.com/song/detail.htm?songId={song_id}" if song_id else url

        r = requests.get(target_url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, 'html.parser')

        title = None
        song_div = soup.select_one('div.song_name')
        if song_div:
            for tag in song_div.find_all(['strong', 'span']):
                tag.decompose()
            title = song_div.get_text(strip=True)

        artist = ""
        artist_div = soup.select_one('div.artist')
        if artist_div:
            artist = artist_div.get_text(strip=True)

        thumbnail = None
        thumb_img = soup.select_one('div.thumb a img')
        if thumb_img and thumb_img.get('src'):
            thumbnail = thumb_img['src']

        if title:
            query = f"{title} {artist}".strip()
            return {
                'title': title,
                'artist': artist,
                'query': query,
                'thumbnail': thumbnail,
                'platform': 'Melon'
            }

    except Exception:
        return None

    return None

async def get_melon_info(url: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_extract_melon_sync, url)
