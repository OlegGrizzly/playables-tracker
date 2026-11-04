#!/usr/bin/env python3
"""Разовая проверка: отдаёт ли YouTube точные числа плеев Playables.

Делает несколько запросов (как обычный браузер):
  1. страница youtube.com/playables -> ytInitialData + параметры клиента
  2. youtubei/v1/browse FEmini_app_destination (каталог) + до N продолжений
  3. страница одной игры (Going Balls) -> ytInitialData

Ищет в ответах всё, что похоже на число плеев, и сохраняет сырые ответы
в probe_out/ (артефакт workflow) для ручного разбора. Ничего не коммитит.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

OUT = Path("probe_out")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Cookie": "SOCS=CAI; CONSENT=YES+"}
GAME_ID = "UgkxUP2ow18nGLszSTR08Kg8z1RU1cGNUY_5"  # Going Balls (~102M по playables.games)
MAX_CONT = 3

PLAYS_RE = re.compile(r"[\d][\d,.  ]*\s*[KMB]?\s*(?:plays?|played|players?)\b", re.I)
LONG_NUM_RE = re.compile(r"\b\d{1,3}(?:,\d{3}){2,}\b|\b\d{7,}\b")


def get(url: str) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def post(url: str, body: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={**HEADERS, "Content-Type": "application/json",
                                          "Origin": "https://www.youtube.com"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def extract_json(html: str, var: str):
    m = re.search(r"(?:var\s+)?" + re.escape(var) + r"\s*=\s*", html)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(html, m.end())[0]
    except json.JSONDecodeError:
        return None


def ytcfg(html: str) -> dict:
    cfg = {}
    for k in ("INNERTUBE_API_KEY", "INNERTUBE_CLIENT_VERSION", "VISITOR_DATA", "INNERTUBE_CONTEXT_GL"):
        m = re.search(r'"' + k + r'"\s*:\s*"([^"]+)"', html)
        if m:
            cfg[k] = m.group(1)
    return cfg


def walk(o, path=""):
    if isinstance(o, dict):
        for k, v in o.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f"{path}[{i}]")
    else:
        yield path, o


def scan(name: str, data) -> list[str]:
    hits = []
    if data is None:
        return [f"[{name}] пусто"]
    for path, v in walk(data):
        if isinstance(v, str) and (PLAYS_RE.search(v) or LONG_NUM_RE.search(v)):
            if "clickTrackingParams" in path or "trackingParams" in path or path.endswith(".url"):
                continue
            hits.append(f"[{name}] {path[-120:]} = {v[:160]!r}")
        elif isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 1_000_000 and re.search(r"count|play|view", path, re.I):
            hits.append(f"[{name}] {path[-120:]} = {v}")
    return hits


def continuations(data) -> list[str]:
    toks = []
    for path, v in walk(data):
        if path.endswith(".continuationCommand.token") or path.endswith(".nextContinuationData.continuation"):
            toks.append(v)
    return toks


def main() -> int:
    OUT.mkdir(exist_ok=True)
    report: list[str] = []

    html = get("https://www.youtube.com/playables?gl=US&hl=en")
    (OUT / "playables.html").write_text(html, encoding="utf-8")
    cfg = ytcfg(html)
    report.append(f"ytcfg: GL={cfg.get('INNERTUBE_CONTEXT_GL')} client={cfg.get('INNERTUBE_CLIENT_VERSION')}")
    init = extract_json(html, "ytInitialData")
    (OUT / "playables_initial.json").write_text(json.dumps(init, ensure_ascii=False, indent=1), encoding="utf-8")
    report += scan("playables:init", init)

    if cfg.get("INNERTUBE_API_KEY"):
        ctx = {"client": {"clientName": "WEB", "clientVersion": cfg.get("INNERTUBE_CLIENT_VERSION", "2.20261030.02.00"),
                          "hl": "en", "gl": "US", "visitorData": cfg.get("VISITOR_DATA", "")}}
        url = f"https://www.youtube.com/youtubei/v1/browse?key={cfg['INNERTUBE_API_KEY']}&prettyPrint=false"
        try:
            br = post(url, {"context": ctx, "browseId": "FEmini_app_destination"})
            (OUT / "browse_0.json").write_text(json.dumps(br, ensure_ascii=False, indent=1), encoding="utf-8")
            report += scan("browse:0", br)
            seen = 0
            for tok in continuations(br)[:MAX_CONT]:
                seen += 1
                c = post(url, {"context": ctx, "continuation": tok})
                (OUT / f"browse_{seen}.json").write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
                report += scan(f"browse:{seen}", c)
        except Exception as exc:  # noqa: BLE001
            report.append(f"browse ошибка: {exc}")
    else:
        report.append("INNERTUBE_API_KEY не найден на странице")

    try:
        gh = get(f"https://www.youtube.com/playables/{GAME_ID}?gl=US&hl=en")
        (OUT / "game.html").write_text(gh, encoding="utf-8")
        gi = extract_json(gh, "ytInitialData")
        (OUT / "game_initial.json").write_text(json.dumps(gi, ensure_ascii=False, indent=1), encoding="utf-8")
        report += scan("game:init", gi)
        report.append(f"game page: 'Going Balls' в ответе: {'Going Balls' in gh}; 'not available' в ответе: {'not available' in gh.lower()}")
    except Exception as exc:  # noqa: BLE001
        report.append(f"game ошибка: {exc}")

    # уникальные находки, чтобы лог был читаемым
    seen_vals, uniq = set(), []
    for line in report:
        key = line.split(" = ", 1)[-1]
        if key in seen_vals:
            continue
        seen_vals.add(key)
        uniq.append(line)
    text = "\n".join(uniq[:400])
    print(text)
    (OUT / "report.txt").write_text(text, encoding="utf-8")
    if s := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(s, "a", encoding="utf-8") as f:
            f.write("### Проверка YouTube\n\n```\n" + "\n".join(uniq[:120]) + "\n```\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
