import json
import collect

request = collect.youtube_request
options = {}
sets = {}
observed = {}
meta = json.loads((collect.ROOT / "data/games.json").read_text())
active = {gid for gid,v in meta.items() if not v.get("removed")}
for mode in ("A to Z", "Z to A", "Latest", "Oldest"):
    pages = []
    def traced(url, body=None):
        if body and "browseId" in body and mode != "A to Z":
            body = dict(body, params=options[mode])
        raw = request(url, body)
        if body:
            nodes = list(collect.objects(json.loads(raw)))
            cards = [v["miniGameCardViewModel"]["title"] for v in nodes if "miniGameCardViewModel" in v]
            for v in nodes:
                item = v.get("listItemViewModel", {})
                for cmd in collect.objects(item):
                    ep = cmd.get("browseEndpoint", {})
                    if ep.get("params") and ep.get("browseId") == "FEmini_app_destination":
                        options[item["title"]["content"]] = ep["params"]
            pages.append((len(cards), cards[:1], cards[-1:], sum("continuationCommand" in v for v in nodes)))
        return raw
    collect.youtube_request = traced
    games = collect.fetch_youtube_catalog()
    observed.update((game["id"], game) for game in games)
    ids = {g["id"] for g in games}
    sets[mode] = ids
    print("RESULT", mode, len(ids), "pages",len(pages),"first",pages[0],"last",pages[-1],"new_vs_AZ",len(ids-sets["A to Z"]),flush=True)
union = set.union(*sets.values())
print("RESULT union",len(union),"active matched",len(active & union),"of",len(active))

import concurrent.futures
import csv
import gzip
import re
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from threading import Event

blocked = Event()
def inspect_game(gid):
    if blocked.is_set():
        return gid, "rate_limited"
    req = urllib.request.Request(f"{collect.YOUTUBE_ORIGIN}/playables/{gid}?gl=US&hl=en",
        headers={**collect.YOUTUBE_HEADERS, "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
        html = body.decode("utf8")
        start = re.search(r"(?:var )?ytInitialData\s*=\s*", html)
        if not start:
            return gid, "unverified"
        data, _ = json.JSONDecoder().raw_decode(html, start.end())
        for node in collect.objects(data):
            game = node.get("miniAppContainerViewModel", {})
            if game.get("playableId") == gid:
                return gid, "game_page"
        return gid, "no_game_card"
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            blocked.set()
        return gid, f"http_{exc.code}"
    except (ValueError, OSError):
        return gid, "request_error"

latest = sorted((collect.ROOT/"data/snapshots").glob("*.csv"))[-1]
with latest.open() as handle:
    previous = {row["id"]:row["plays"] for row in csv.DictReader(handle)}
status = dict.fromkeys(observed, "catalogue")
with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
    status.update(pool.map(inspect_game, sorted(meta.keys() - observed.keys())))
print("RESULT status",Counter(status.values()),flush=True)
with Path("playables.csv").open("w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.writer(handle)
    writer.writerow(["id","name","youtube_url","youtube_check","youtube_plays_2026-11-23","snapshot_"+latest.stem,"tracker_removed_on"])
    for gid in sorted(meta.keys() | observed.keys()):
        g = observed.get(gid,{})
        m = meta.get(gid,{})
        writer.writerow([gid,g.get("name") or m.get("name"),f"https://www.youtube.com/playables/{gid}",
                         status[gid],g.get("plays"),previous.get(gid),m.get("removed")])
