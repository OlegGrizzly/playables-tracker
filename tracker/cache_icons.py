"""Cache small game thumbnails for the dashboard. Retry missing files on the next run."""
import io
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "docs" / "icons"


def cache(item):
    gid, game = item
    url = game.get("icon")
    if not url or not re.fullmatch(r"[\w-]+", gid):
        return False
    target = DEST / (gid + ".webp")
    if target.exists():
        return True
    host = urlsplit(url).hostname or ""
    if host == "yt3.ggpht.com":
        urls = [re.sub(r"=s\d+[\w-]*$", "", url) + "=s144"]
    elif host.endswith(".h5games.usercontent.goog"):
        urls = [re.sub(r"=s\d+[\w-]*$", "", url), url]
    else:
        return False
    for src in dict.fromkeys(urls):
        try:
            with urlopen(Request(src, headers={"User-Agent": "Mozilla/5.0"}), timeout=10) as response:
                raw = response.read(8 * 1024 * 1024)
            with Image.open(io.BytesIO(raw)) as image:
                image.thumbnail((144, 144), Image.Resampling.LANCZOS)
                data = io.BytesIO()
                ImageOps.exif_transpose(image).save(data, "WEBP", quality=82)
            target.write_bytes(data.getvalue())
            return True
        except (OSError, ValueError):
            continue
    return False


if __name__ == "__main__":
    DEST.mkdir(exist_ok=True)
    games = json.loads((ROOT / "data/games.json").read_text())
    with ThreadPoolExecutor(max_workers=12) as pool:
        count = sum(pool.map(cache, games.items()))
    print(f"Cached {count}/{len(games)} icons, {sum(p.stat().st_size for p in DEST.glob('*.webp'))} bytes")
