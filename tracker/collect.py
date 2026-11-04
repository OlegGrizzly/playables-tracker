#!/usr/bin/env python3
"""Сборщик снимков каталога YouTube Playables.

Источник — https://playables.games/app/: весь каталог встроен в HTML как
`const DATA=[...]`. Публичного API нет, поэтому раз в день (или чаще) скачиваем
страницу, достаём массив и сохраняем снимок:

  data/snapshots/YYYY-MM-DD.csv  — id,plays на дату данных источника
  data/games.json                — метаданные игр (название, издатель, жанр,
                                   дата публикации, иконка, first/last seen)

Дата снимка берётся из метки сайта «updated YYYY-MM-DD», а не из времени
запуска: если сайт ещё не обновился, повторный запуск ничего не меняет.

Формат строки DATA (по состоянию на 2026-11):
  [name, plays, genreIdx, playableId, icon, isNew, removedDate|0,
   publisher, trendScore, publishedYYYYMMDD|0]

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

SOURCE_URL = "https://playables.games/app/"
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


def latest_snapshot_date(snap_dir: Path) -> str | None:
    dates = sorted(p.stem for p in snap_dir.glob("*.csv"))
    return dates[-1] if dates else None


def write_snapshot(path: Path, games: list[dict]) -> bool:
    """Пишет снимок; возвращает True, если содержимое изменилось."""
    lines = ["id,plays"] + [f"{g['id']},{g['plays']}" for g in sorted(games, key=lambda g: g["id"])]
    content = "\n".join(lines) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False
    path.write_text(content, encoding="utf-8")
    return True


def update_games_meta(path: Path, games: list[dict], date: str) -> tuple[dict, list[str]]:
    meta = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    new_ids = []
    for g in games:
        e = meta.get(g["id"])
        if e is None:
            e = meta[g["id"]] = {"first_seen": date}
            new_ids.append(g["id"])
        e.update(
            name=g["name"],
            publisher=g["publisher"],
            genre=g["genre"],
            published=g["published"],
            icon=g["icon"],
            removed=g["removed"],
            last_seen=date,
        )
        if e.get("first_seen", date) > date:
            e["first_seen"] = date
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return meta, new_ids


def collect(data_dir: Path, html: str | None = None, today: str | None = None) -> dict:
    html = html if html is not None else fetch(SOURCE_URL)
    data_date, games = parse_catalog(html)
    if not data_date:
        data_date = today or dt.datetime.now(dt.timezone.utc).date().isoformat()
        print(f"⚠️  Метка «updated» не найдена, использую дату запуска {data_date}", file=sys.stderr)

    snap_dir = data_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    latest = latest_snapshot_date(snap_dir)
    if latest and data_date < latest:
        print(f"Источник отдаёт данные за {data_date}, а у нас уже есть {latest} — пропускаю.")
        return {"date": data_date, "changed": False, "games": len(games), "new": []}

    changed = write_snapshot(snap_dir / f"{data_date}.csv", games)
    meta, new_ids = update_games_meta(data_dir / "games.json", games, data_date)
    active = sum(1 for g in games if not g["removed"])
    print(
        f"Снимок {data_date}: игр {len(games)} (активных {active}), "
        f"новых в базе {len(new_ids)}, файл {'обновлён' if changed else 'без изменений'}"
    )
    return {"date": data_date, "changed": changed, "games": len(games), "new": new_ids, "meta": meta}


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
