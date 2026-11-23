#!/usr/bin/env python3
"""Сборщик снимков каталога YouTube Playables.

Основной источник — каталог YouTube Playables. Снимок датируется днём
сбора (UTC). Если прямой каталог недоступен или неполон, используем
снимок playables.games с датой, указанной самим источником.
История хранится в data/snapshots, метаданные — в data/games.json.

Только стандартная библиотека Python — без зависимостей.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

YOUTUBE_ORIGIN = "https://www.youtube.com"
YOUTUBE_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "Accept-Language": "en-US,en;q=0.9",
    "Cookie": "SOCS=CAI; CONSENT=YES+",
}
MAX_YOUTUBE_PAGES = 100
MIN_PREVIOUS_COVERAGE = 0.75
SOURCE_URL = "https://playables.games/app/"
NEW_URL = "https://playables.games/new/"  # «Added <дата>» — когда игра появилась в каталоге
USER_AGENT = "Mozilla/5.0 (compatible; PlayablesTracker/1.0; daily snapshot)"
MIN_GAMES = 500  # защита от записи «пустого» снимка, если вёрстка сломалась
ROOT = Path(__file__).resolve().parent.parent


class ParseError(RuntimeError):
    pass


def fetch(url: str, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en"}
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read().decode("utf-8")
        except Exception as exc:  # noqa: BLE001 — сеть, повторяем
            last = exc
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Не удалось скачать {url}: {last}")


def extract_js_const(html: str, name: str):
    """Достаёт JSON-литерал из `const NAME=<json>;`."""
    m = re.search(r"\bconst\s+" + re.escape(name) + r"\s*=\s*", html)
    if not m:
        raise ParseError(f"На странице нет `const {name}=` — формат сайта изменился")
    try:
        value, _ = json.JSONDecoder().raw_decode(html, m.end())
    except json.JSONDecodeError as exc:
        raise ParseError(f"`const {name}` не парсится как JSON: {exc}") from exc
    return value


def yyyymmdd_to_iso(v) -> str | None:
    if not v:
        return None
    s = str(v)
    if re.fullmatch(r"\d{8}", s):
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    return None


def parse_catalog(html: str) -> tuple[str | None, list[dict]]:
    """Возвращает (дата данных источника, список игр)."""
    rows = extract_js_const(html, "DATA")
    genres = extract_js_const(html, "GENRES")
    if not isinstance(rows, list) or not isinstance(genres, list):
        raise ParseError("DATA/GENRES имеют неожиданный тип")

    m = re.search(r"updated\s+(\d{4}-\d{2}-\d{2})", html)
    data_date = m.group(1) if m else None

    games: list[dict] = []
    bad = 0
    for r in rows:
        try:
            name, plays, gidx, gid, icon = r[0], r[1], r[2], r[3], r[4]
            if not isinstance(gid, str) or not gid or not isinstance(plays, (int, float)):
                raise ValueError
            games.append(
                {
                    "id": gid,
                    "name": str(name),
                    "plays": int(plays),
                    "genre": genres[gidx] if isinstance(gidx, int) and 0 <= gidx < len(genres) else None,
                    "icon": icon or None,
                    "publisher": (r[7] or None) if len(r) > 7 else None,
                    "removed": yyyymmdd_to_iso(r[6]) if len(r) > 6 else None,
                    "published": yyyymmdd_to_iso(r[9]) if len(r) > 9 else None,
                }
            )
        except (IndexError, TypeError, ValueError):
            bad += 1
    if bad:
        print(f"⚠️  Пропущено строк с неожиданным форматом: {bad}", file=sys.stderr)
    if len(games) < MIN_GAMES:
        raise ParseError(f"Слишком мало игр ({len(games)} < {MIN_GAMES}) — формат сайта изменился?")
    return data_date, games


MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def youtube_request(url: str, body: dict | None = None) -> str:
    headers = dict(YOUTUBE_HEADERS)
    payload = None
    if body is not None:
        headers.update({"Content-Type": "application/json", "Origin": YOUTUBE_ORIGIN})
        payload = json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=payload, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read().decode("utf-8")
        except OSError as exc:
            if attempt == 2:
                raise RuntimeError(f"Запрос к YouTube не удался: {type(exc).__name__}") from None
            time.sleep(2 ** attempt)


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)


def parse_youtube_card(card: dict) -> dict:
    url = card["onTap"]["innertubeCommand"]["commandMetadata"]["webCommandMetadata"]["url"]
    game_id = re.fullmatch(r"/playables/([A-Za-z0-9_-]+)(?:\?.*)?", url)
    count = re.fullmatch(r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*([KMB]?)\s+plays?",
                         card.get("metadataText", ""), re.I)
    if not game_id or not count or not card.get("title"):
        raise ParseError("Карточка YouTube без ID, названия или счётчика")
    number, suffix = count.groups()
    whole, _, fraction = number.replace(",", "").partition(".")
    multiplier = {"": 1, "K": 1000, "M": 1000000, "B": 1000000000}[suffix.upper()]
    plays = int(whole + fraction) * multiplier // (10 ** len(fraction))

    fields = {node["label"]: node.get("value") for node in objects(card.get("overflowButton"))
              if node.get("label") in ("Publisher", "Publish date", "Genre")}
    publication = re.fullmatch(r"([A-Za-z]{3}) (\d{1,2}), (\d{4})",
                               fields.get("Publish date") or "")
    published = None
    if publication and publication[1] in MONTHS:
        published = dt.date(int(publication[3]), MONTHS[publication[1]],
                            int(publication[2])).isoformat()
    images = card.get("image", {}).get("sources", [])
    return {
        "id": game_id[1],
        "name": card["title"],
        "plays": plays,
        "genre": card.get("genre") or fields.get("Genre"),
        "icon": images[0].get("url") if images else None,
        "publisher": fields.get("Publisher"),
        "published": published,
        "removed": None,
    }


def fetch_youtube_catalog() -> list[dict]:
    html = youtube_request(f"{YOUTUBE_ORIGIN}/playables?gl=US&hl=en")
    config = {}
    for key in ("INNERTUBE_API_KEY", "INNERTUBE_CLIENT_VERSION", "VISITOR_DATA"):
        match = re.search(r'"' + key + r'"\s*:\s*"([^"]+)"', html)
        if match:
            config[key] = match[1]
    if not all(key in config for key in ("INNERTUBE_API_KEY", "INNERTUBE_CLIENT_VERSION")):
        raise ParseError("YouTube не вернул настройки клиента Playables")

    endpoint = (f"{YOUTUBE_ORIGIN}/youtubei/v1/browse?key="
                f"{config['INNERTUBE_API_KEY']}&prettyPrint=false")
    client = {"clientName": "WEB", "clientVersion": config["INNERTUBE_CLIENT_VERSION"],
              "hl": "en", "gl": "US", "visitorData": config.get("VISITOR_DATA", "")}
    query = {"browseId": "FEmini_app_destination"}
    games = {}
    seen_tokens = set()
    for page in range(MAX_YOUTUBE_PAGES):
        response = json.loads(youtube_request(endpoint, {"context": {"client": client}, **query}))
        if page == 0:
            grids = [node["richGridRenderer"]["contents"] for node in objects(response)
                     if "richGridRenderer" in node]
        else:
            grids = [node["appendContinuationItemsAction"]["continuationItems"]
                     for node in objects(response) if "appendContinuationItemsAction" in node]
        if len(grids) != 1:
            raise ParseError(f"Неожиданный ответ каталога YouTube на странице {page + 1}")

        nodes = list(objects(grids[0]))
        cards = [parse_youtube_card(node["miniGameCardViewModel"]) for node in nodes
                 if "miniGameCardViewModel" in node]
        tokens = {node["continuationCommand"]["token"]
                  for node in objects([item["continuationItemRenderer"] for item in grids[0]
                                       if "continuationItemRenderer" in item])
                  if "continuationCommand" in node}
        if not cards and (page == 0 or tokens):
            raise ParseError(f"Пустая страница каталога YouTube: {page + 1}")
        if any(card["id"] in games for card in cards) or len({card["id"] for card in cards}) != len(cards):
            raise ParseError(f"Повтор карточек на странице каталога YouTube: {page + 1}")
        games.update((card["id"], card) for card in cards)
        if not tokens:
            return list(games.values())
        if len(tokens) != 1 or tokens & seen_tokens:
            raise ParseError(f"Некорректное продолжение каталога YouTube: страница {page + 1}")
        token = tokens.pop()
        seen_tokens.add(token)
        query = {"continuation": token}
    raise ParseError(f"Каталог YouTube не завершился за {MAX_YOUTUBE_PAGES} страниц")


def parse_added(new_html: str, slug_to_id: dict[str, str]) -> dict[str, str]:
    """Страница /new/: секции «Added Nov 3, 2026» со ссылками /games/<slug>/ -> {id: дата}."""
    out: dict[str, str] = {}
    parts = re.split(r"<h2[^>]*>\s*Added\s+", new_html)
    for part in parts[1:]:
        m = re.match(r"([A-Z][a-z]{2})[a-z]*\.?\s+(\d{1,2}),\s+(\d{4})", part)
        if not m or m.group(1) not in MONTHS:
            continue
        date = f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
        section = part.split("<h2", 1)[0]
        for slug in re.findall(r'href="/games/([^/"]+)/"', section):
            gid = slug_to_id.get(slug)
            if gid and (gid not in out or date < out[gid]):
                out[gid] = date
    return out


def latest_snapshot_date(snap_dir: Path) -> str | None:
    dates = sorted(p.stem for p in snap_dir.glob("*.csv"))
    return dates[-1] if dates else None


def atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def write_snapshot(path: Path, games: list[dict]) -> bool:
    """Пишет снимок; возвращает True, если содержимое изменилось."""
    lines = ["id,plays"] + [f"{g['id']},{g['plays']}" for g in sorted(games, key=lambda g: g["id"])]
    content = "\n".join(lines) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    atomic_write(path, content)
    return True


def update_games_meta(path: Path, games: list[dict], date: str,
                      added: dict[str, str] | None = None, *, preserve_missing: bool = False) -> tuple[dict, list[str]]:
    """added — даты появления в каталоге с /new/. Для игр, которых там нет, но которые
    появились у нас уже после старта трекера, дата появления = первый снимок с ними."""
    first_run = not path.exists()
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    added = added or {}
    new_ids = []
    for g in games:
        e = meta.get(g["id"])
        if e is None:
            e = meta[g["id"]] = {"first_seen": date}
            new_ids.append(g["id"])
            if not first_run and g["id"] not in added:
                e["added"] = date
        if g["id"] in added and (not e.get("added") or added[g["id"]] < e["added"]):
            e["added"] = added[g["id"]]
        if e.get("last_seen", date) > date:
            e["first_seen"] = min(e.get("first_seen", date), date)
            continue
        for field in ("name", "publisher", "genre", "published", "icon"):
            if g[field] or not preserve_missing or field not in e:
                e[field] = g[field]
        e["removed"] = g["removed"]
        e["last_seen"] = date
        if e.get("first_seen", date) > date:
            e["first_seen"] = date
    atomic_write(path, json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
    return meta, new_ids


def load_added(html: str, new_html: str | None) -> dict[str, str]:
    """Даты появления в каталоге. Некритично: при любой ошибке — пустой словарь."""
    try:
        pages = extract_js_const(html, "PAGES")
        if new_html is None:
            new_html = fetch(NEW_URL)
        added = parse_added(new_html, {slug: gid for gid, slug in pages.items()})
        print(f"Даты появления в каталоге: {len(added)} игр")
        return added
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  Не удалось получить даты появления в каталоге: {exc}", file=sys.stderr)
        return {}


def merge_observations(data_dir, date, games, source, added=None):
    directory = data_dir / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{date}.csv"
    rows = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as f:
            rows = {r["id"]: (int(r["plays"]), r.get("source") or "playables.games")
                    for r in csv.DictReader(f)}
    original = rows.copy()
    for g in games:
        gid = g["id"]
        if source == "youtube" or rows.get(gid, (None, None))[1] != "youtube":
            rows[gid] = (g["plays"], source)
    changed = original != rows
    meta, new = update_games_meta(data_dir / "games.json", games, date, added,
                                  preserve_missing=True)
    if changed:
        lines = ["id,plays,source"]
        lines.extend(f"{gid},{plays},{src}" for gid, (plays, src) in sorted(rows.items()))
        atomic_write(path, "\n".join(lines) + "\n")
    return {"date": date, "changed": changed, "games": len(rows), "new": new, "meta": meta}


def collect(data_dir: Path, html: str | None = None, today: str | None = None,
            new_html: str | None = None) -> dict:
    date = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
    results = []
    latest = latest_snapshot_date(data_dir / "snapshots")
    if html is None:
        try:
            games = fetch_youtube_catalog()
            if not games:
                raise ParseError("Пустой ответ YouTube")
        except (RuntimeError, ValueError, KeyError, TypeError, AttributeError, OSError) as exc:
            print(f"YouTube: {exc}", file=sys.stderr)
        else:
            results.append(merge_observations(data_dir, date, games, "youtube"))
            print(f"YouTube: {len(games)} замеров за {date}")
    try:
        page = html if html is not None else fetch(SOURCE_URL)
        source_date, games = parse_catalog(page)
        if html is not None and not source_date:
            source_date = date
        if not source_date or source_date > date:
            raise ParseError("Некорректная дата playables.games")
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        if not results:
            raise
        print(f"playables.games: {exc}", file=sys.stderr)
    else:
        if html is not None and latest and source_date < latest:
            return {"date": latest, "changed": False, "games": len(games), "new": []}
        path = data_dir / "snapshots" / f"{source_date}.csv"
        # Старые двухколоночные снимки уже содержат этот каталог целиком.
        legacy = False
        if path.exists() and source_date < date:
            with path.open() as f:
                legacy = f.readline().strip() == "id,plays"
        if legacy:
            results.append({"date": source_date, "changed": False, "games": len(games), "new": []})
        else:
            added = load_added(page, new_html) if (html is None or new_html is not None) else {}
            results.append(merge_observations(data_dir, source_date, games, "playables.games", added))
        print(f"playables.games: {len(games)} строк за {source_date}")
    result = max(results, key=lambda r: r["date"]).copy()
    result["date"] = max(result["date"], latest or "")
    result["changed"] = any(r["changed"] for r in results)
    result["new"] = sorted({gid for r in results for gid in r["new"]})
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--html-file", type=Path, help="взять HTML из файла вместо сети (для отладки)")
    args = ap.parse_args()

    html = args.html_file.read_text(encoding="utf-8") if args.html_file else None
    res = collect(args.data_dir, html=html)

    if gh_out := os.environ.get("GITHUB_OUTPUT"):
        with open(gh_out, "a", encoding="utf-8") as f:
            f.write(f"date={res['date']}\nnew={len(res['new'])}\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        meta = res.get("meta") or {}
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Снимок {res['date']}\n\nИгр в каталоге: {res['games']}. Новых в базе: {len(res['new'])}.\n\n")
            for gid in res["new"][:50]:
                g = meta.get(gid, {})
                f.write(f"- {g.get('name')} — {g.get('publisher')} ({g.get('genre')})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
