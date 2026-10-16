import asyncio
import json
import re
import requests
from bs4 import BeautifulSoup
from typing import Optional, Dict, Any

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7'
}

def is_apple_music_url(url: str) -> bool:
    return 'music.apple.com' in url

def _extract_apple_music_sync(url: str) -> Optional[Dict[str, Any]]:
    url = url.strip()
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, 'html.parser')

        title = None
        artist = ""
        thumbnail = None

        for script in soup.find_all('script', type='application/ld+json'):
            try:
                schema = json.loads(script.string)
                if schema.get('@type') in ['MusicComposition', 'MusicRecording', 'MusicAlbum']:
                    title = schema.get('name')
                    by_artist = schema.get('byArtist')
                    if isinstance(by_artist, list) and by_artist:
                        artist = by_artist[0].get('name', '')
                    elif isinstance(by_artist, dict):
                        artist = by_artist.get('name', '')

                    image = schema.get('image')
                    if isinstance(image, str):
                        thumbnail = image
                    elif isinstance(image, dict):
                        thumbnail = image.get('url')
                    break
            except Exception:
                continue

        if not title:
            apple_title = soup.find('meta', attrs={'name': 'apple:title'})
            if apple_title and apple_title.get('content'):
                title = apple_title['content']

        if not title:
            og_title = soup.find('meta', property='og:title')
            if og_title and og_title.get('content'):
                raw_title = og_title['content']
                m = re.match(r'^(.*?)\s+by\s+(.*?)\s+on Apple', raw_title, re.IGNORECASE)
                if m:
                    title = m.group(1).strip()
                    artist = m.group(2).strip()
                else:
                    title = raw_title.split(' - ')[0].replace('â€Ž', '').strip()

        if title and not artist:
            desc_tag = soup.find('meta', attrs={'name': 'description'}) or soup.find('meta', property='og:description')
            if desc_tag and desc_tag.get('content'):
                m = re.search(r'by\s+([^.\n]+?)\s+on Apple', desc_tag['content'], re.IGNORECASE)
                if m:
                    artist = m.group(1).strip()

        if not thumbnail:
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                thumbnail = og_image['content']

        if title:
            query = f"{title} {artist}".strip()
            return {
                'title': title,
                'artist': artist,
                'query': query,
                'thumbnail': thumbnail,
                'platform': 'Apple Music'
            }

    except Exception:
        return None

    return None

async def get_apple_music_info(url: str) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(_extract_apple_music_sync, url)
