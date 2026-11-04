#!/usr/bin/env python3
"""Уведомления о резких пиках (Telegram).

Запускается после build.py, читает docs/data.json. Типы сигналов:

  spike    — дневной прирост резко выше обычного: прирост за последние сутки
             >= SPIKE_X × медиана дневных приростов за предыдущие 7 дней,
             и при этом >= SPIKE_MIN плеев/день и >= 3 шагов округления.
             (Нужно минимум 4 снимка истории игры.)
  breakout — новинка (<= 7 дней) обгоняет ровесников: перцентиль >= 0.97
             и >= BREAKOUT_MIN плеев. Приходит один раз на игру.
  hot      — игра впервые вошла в топ-HOT_TOP по 🔥 Hot (один раз на игру
             за HOT_COOLDOWN дней).

Повторы гасятся через data/alerts_sent.json (spike — не чаще раза в
SPIKE_COOLDOWN дней на игру).

Переменные окружения (секреты GitHub):
  TELEGRAM_BOT_TOKEN — токен бота от @BotFather
  TELEGRAM_CHAT_ID   — id чата/канала (можно несколько через запятую)
  DASHBOARD_URL      — ссылка на дашборд (для кнопок в сообщениях), опционально
Без токена — только печатает сигналы в лог/summary (dry run).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import statistics
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SPIKE_X = 3.0
SPIKE_MIN = 5_000
SPIKE_COOLDOWN = 3
BREAKOUT_PEER = 0.97
BREAKOUT_MIN = 1_000
BREAKOUT_MAX_AGE = 7
HOT_TOP = 10
HOT_COOLDOWN = 14
MAX_PER_MESSAGE = 15


def fmt(n) -> str:
    if n is None:
        return "—"
    a = abs(n)
    s = "-" if n < 0 else ""
    if a < 1e3:
        return s + str(round(a))
    if a < 1e4:
        return s + f"{a / 1e3:.1f}".rstrip("0").rstrip(".") + "K"
    if a < 1e6:
        return s + f"{round(a / 1e3)}K"
    if a < 1e7:
        return s + f"{a / 1e6:.1f}".rstrip("0").rstrip(".") + "M"
    if a < 1e9:
        return s + f"{round(a / 1e6)}M"
    return s + f"{a / 1e9:.1f}B"


def top_pct(p) -> str:
    import math
    return "—" if p is None else f"топ {max(1, math.ceil((1 - p) * 100))}%"


def daily_deltas(g: dict, dates: list[str]) -> list[tuple[str, float]]:
    """[(дата, прирост в день)] по соседним наблюдениям (с нормировкой на разрыв)."""
    pts = [(dates[g["off"] + i], v) for i, v in enumerate(g["s"]) if v is not None]
    out = []
    for (d0, v0), (d1, v1) in zip(pts, pts[1:]):
        gap = (dt.date.fromisoformat(d1) - dt.date.fromisoformat(d0)).days
        if gap > 0:
            out.append((d1, (v1 - v0) / gap))
    return out


def detect(data: dict) -> list[dict]:
    dates = data["dates"]
    last = dates[-1]
    games = [g for g in data["games"] if not g.get("rm")]
    sig = []

    for g in games:
        dd = daily_deltas(g, dates)
        if len(dd) >= 4 and dd[-1][0] == last:
            today = dd[-1][1]
            base = [v for _, v in dd[-8:-1]]
            med = statistics.median(base)
            ref = max(med, g["q"] / 2)  # медиана 0 у больших игр из-за округления
            if today >= SPIKE_MIN and today >= 3 * g["q"] and today >= SPIKE_X * ref:
                sig.append({"type": "spike", "g": g, "today": today, "base": med, "x": today / ref if ref else None})

        if (g.get("age") is not None and g["age"] <= BREAKOUT_MAX_AGE and g.get("peer") is not None
                and g["peer"] >= BREAKOUT_PEER and g["plays"] >= BREAKOUT_MIN):
            sig.append({"type": "breakout", "g": g})

    ranked = sorted((g for g in games if g.get("hot") is not None), key=lambda g: -g["hot"])
    for i, g in enumerate(ranked[:HOT_TOP]):
        sig.append({"type": "hot", "g": g, "rank": i + 1})
    return sig


def filter_new(signals: list[dict], sent: dict, today: str) -> list[dict]:
    t = dt.date.fromisoformat(today)
    out = []
    for s in signals:
        key = f"{s['type']}:{s['g']['id']}"
        prev = sent.get(key)
        if s["type"] == "breakout" and prev:
            continue
        if prev:
            days = (t - dt.date.fromisoformat(prev)).days
            if s["type"] == "spike" and days < SPIKE_COOLDOWN:
                continue
            if s["type"] == "hot" and days < HOT_COOLDOWN:
                continue
        out.append(s)
    return out


def render(signals: list[dict], data: dict, dash: str | None) -> list[str]:
    def link(g):
        name = html.escape(g["n"] or "?")
        if dash:
            return f'<a href="{html.escape(dash)}#g={urllib.parse.quote(g["id"])}">{name}</a>'
        return f'<a href="https://www.youtube.com/playables/{urllib.parse.quote(g["id"])}">{name}</a>'

    def meta(g):
        age = f"{g['age']} дн" if g.get("age") is not None else "возраст ?"
        return f"{html.escape(g.get('p') or '—')} · {html.escape(g.get('g') or '—')} · {age}"

    blocks = {"spike": [], "breakout": [], "hot": []}
    for s in signals:
        g = s["g"]
        if s["type"] == "spike":
            x = f"×{s['x']:.1f}" if s.get("x") else ""
            blocks["spike"].append(
                f"📈 {link(g)} — <b>+{fmt(s['today'])}</b> за сутки {x} к обычному (~{fmt(s['base'])}/д)\n"
                f"    {meta(g)} · всего {fmt(g['plays'])}")
        elif s["type"] == "breakout":
            blocks["breakout"].append(
                f"🚀 {link(g)} — <b>{fmt(g['plays'])}</b> плеев, ровесники {top_pct(g['peer'])}\n"
                f"    {meta(g)}")
        else:
            blocks["hot"].append(
                f"🔥 #{s['rank']} {link(g)} — Hot {g['hot']:.0f}, {'≈' if g.get('est') else ''}{fmt(g.get('rate'))}/день\n"
                f"    {meta(g)}")

    titles = {"spike": "Резкие пики", "breakout": "Новинки залетают", "hot": "Новые в топ-10 Hot"}
    msgs = []
    for k in ("spike", "breakout", "hot"):
        items = blocks[k]
        if not items:
            continue
        for i in range(0, len(items), MAX_PER_MESSAGE):
            chunk = items[i:i + MAX_PER_MESSAGE]
            head = f"<b>{titles[k]}</b> · данные на {data['updated']}"
            if len(items) > MAX_PER_MESSAGE:
                head += f" ({i // MAX_PER_MESSAGE + 1}/{(len(items) - 1) // MAX_PER_MESSAGE + 1})"
            msgs.append(head + "\n\n" + "\n".join(chunk))
    return msgs


def send_telegram(token: str, chat_ids: list[str], text: str) -> None:
    for chat in chat_ids:
        body = urllib.parse.urlencode({
            "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": "true",
        }).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
        with urllib.request.urlopen(req, timeout=30) as r:
            res = json.load(r)
            if not res.get("ok"):
                raise RuntimeError(f"Telegram: {res}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Уведомления о пиках")
    ap.add_argument("--data", type=Path, default=ROOT / "docs" / "data.json")
    ap.add_argument("--state", type=Path, default=ROOT / "data" / "alerts_sent.json")
    ap.add_argument("--dry-run", action="store_true", help="не отправлять и не запоминать")
    ap.add_argument("--test", action="store_true", help="отправить тестовое сообщение и выйти")
    args = ap.parse_args()

    if args.test:
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        chats = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
        if not token or not chats:
            print(f"Не заданы: {'TELEGRAM_BOT_TOKEN ' if not token else ''}{'TELEGRAM_CHAT_ID' if not chats else ''}")
            return 1
        data = json.loads(args.data.read_text(encoding="utf-8"))
        dash = os.environ.get("DASHBOARD_URL") or ""
        active = sum(1 for g in data["games"] if not g.get("rm"))
        send_telegram(token, chats, (
            "✅ <b>Playables Tracker подключён</b>\n\n"
            f"Данные на {data['updated']}, снимков: {len(data['dates'])}, игр: {active}.\n"
            "Сюда будут приходить резкие пики, залетающие новинки и новые игры в топ-10 Hot."
            + (f'\n\n<a href="{html.escape(dash)}">Открыть дашборд</a>' if dash else "")))
        print(f"Тестовое сообщение отправлено в {len(chats)} чат(а)")
        return 0

    data = json.loads(args.data.read_text(encoding="utf-8"))
    sent = json.loads(args.state.read_text(encoding="utf-8")) if args.state.exists() else {}
    today = data["updated"]

    first_run = not args.state.exists()
    signals = filter_new(detect(data), sent, today)
    if first_run:
        # Первый запуск: запоминаем текущий топ и новинки без отправки, чтобы не заспамить.
        print(f"Первый запуск: {len(signals)} сигналов запомнено без отправки.")
        msgs = []
    else:
        msgs = render(signals, data, os.environ.get("DASHBOARD_URL") or None)

    for m in msgs:
        print(m, end="\n\n")
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"\n### Сигналы: {len(signals)}\n\n" + "\n\n".join(msgs).replace("\n", "  \n") + "\n")

    if args.dry_run:
        return 0
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chats = [c.strip() for c in os.environ.get("TELEGRAM_CHAT_ID", "").split(",") if c.strip()]
    if msgs and token and chats:
        for m in msgs:
            send_telegram(token, chats, m)
        print(f"Отправлено сообщений: {len(msgs)}")
    elif msgs:
        print("TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не заданы — только лог.")

    # Запоминаем только то, что реально ушло (или первый запуск / нет токена — тоже, чтобы не копить).
    for s in signals:
        sent[f"{s['type']}:{s['g']['id']}"] = today
    # чистим старое
    cutoff = dt.date.fromisoformat(today) - dt.timedelta(days=60)
    sent = {k: v for k, v in sent.items() if k.startswith("breakout:") or dt.date.fromisoformat(v) >= cutoff}
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(sent, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
