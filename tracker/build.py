#!/usr/bin/env python3
"""Собирает docs/data.json для дашборда из накопленных снимков.

Метрики на игру (все — по последнему снимку T):
  plays    — всего плеев (округлено так, как показывает YouTube)
  age      — дней с публикации
  d1       — прирост за последние сутки (если есть снимок за T-1)
  rate     — плеев в день за окно до 7 дней по реальному приросту между снимками.
             Прирост 0 -> известна только граница «< шаг округления / окно» (lt=1).
             Нет двух снимков -> оценка plays/age (est=1)
  growth   — рост за неделю, % (rate*7 / plays неделю назад)
  mom      — ускорение: темп за 3 последних дня / темп за 7 дней до этого
  peer     — перцентиль среди «ровесников» (0..1): сравниваем плеи-в-день-жизни
             с играми того же возраста (по всем снимкам). 0.95 = лучше 95%.
  hot      — сводный скор 0..100 «залетает»:
             45% перцентиль rate + 35% peer + 20% перцентиль ускорения
             (неизвестные компоненты = 0.5, нейтрально).
             Игры, у которых прирост не виден из-за округления (lt), в перцентиле
             темпа стоят ниже любой игры с измеренным приростом — иначе стоящая
             игра с шагом 1M обгоняла бы новинку с реальными +30K/день.
  at       — плеи на 1/3/7/14/30-й день жизни (если снимок попал на этот день)
"""
from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Корзины возраста (дни, левая граница включительно) для сравнения с ровесниками.
AGE_EDGES = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 14, 17, 21, 25, 30, 35, 42, 50, 60,
             75, 90, 120, 150, 180, 240, 300, 365, 500, 730, 100000]
MIN_PEERS = 10
AT_AGES = (1, 3, 7, 14, 30)
W_RATE, W_PEER, W_MOM = 0.45, 0.35, 0.20


def d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def quantum(plays: int) -> int:
    """Шаг округления YouTube для данного числа (1.2K, 12K, 123K, 1.2M, 12M…)."""
    if plays < 1_000:
        return 1
    if plays < 10_000:
        return 100
    if plays < 1_000_000:
        return 1_000
    if plays < 10_000_000:
        return 100_000
    return 1_000_000


def age_bucket(age: int) -> int:
    return max(0, bisect.bisect_right(AGE_EDGES, age) - 1)


def per_day(plays: int, age: int) -> float:
    return plays / max(age, 1)


def load(data_dir: Path):
    meta = json.loads((data_dir / "games.json").read_text(encoding="utf-8"))
    snaps = sorted((data_dir / "snapshots").glob("*.csv"))
    if not snaps:
        raise SystemExit("Нет снимков в data/snapshots — сначала запустите collect.py")
    dates = [p.stem for p in snaps]
    obs: dict[str, list[tuple[dt.date, int]]] = {}
    for p in snaps:
        day = d(p.stem)
        with p.open(encoding="utf-8") as f:
            for row in csv.DictReader(f):
                obs.setdefault(row["id"], []).append((day, int(row["plays"])))
    return meta, dates, obs


def value_at_or_before(series: list[tuple[dt.date, int]], target: dt.date):
    """Последнее наблюдение с датой <= target (series отсортирован по дате)."""
    i = bisect.bisect_right(series, (target, math.inf)) - 1
    return series[i] if i >= 0 else None


def window_rate(series, t_date, t_val, days):
    """Темп (плеев/день) за окно до `days` дней назад. -> (rate, delta, span, base) | None"""
    base = value_at_or_before(series, t_date - dt.timedelta(days=days))
    if base is None:
        base = series[0]
    span = (t_date - base[0]).days
    if span <= 0:
        return None
    delta = t_val - base[1]
    return delta / span, delta, span, base[1]


def percentile_ranks(values: dict[str, float]) -> dict[str, float]:
    """Перцентильный ранг 0..1 (средний ранг для равных)."""
    if not values:
        return {}
    srt = sorted(values.values())
    n = len(srt)
    out = {}
    for k, v in values.items():
        lo = bisect.bisect_left(srt, v)
        hi = bisect.bisect_right(srt, v)
        out[k] = ((lo + hi - 1) / 2) / (n - 1) if n > 1 else 0.5
    return out


def build(data_dir: Path) -> dict:
    meta, dates, obs = load(data_dir)
    last = d(dates[-1])
    date_idx = {s: i for i, s in enumerate(dates)}

    # --- Наблюдения «возраст -> плеи/день» для сравнения с ровесниками ---
    # По каждой игре и корзине берём одно (самое позднее) наблюдение,
    # чтобы игры с длинной историей не перевешивали.
    bucket_obs: dict[int, dict[str, float]] = {}
    bucket_age: dict[int, dict[str, int]] = {}  # возраст того же наблюдения — для кривой
    for gid, series in obs.items():
        pub = meta.get(gid, {}).get("published")
        if not pub:
            continue
        pub_d = d(pub)
        for day, plays in series:
            age = (day - pub_d).days
            if age < 0:
                continue
            b = age_bucket(age)
            bucket_obs.setdefault(b, {})[gid] = per_day(plays, age)
            bucket_age.setdefault(b, {})[gid] = age
    bucket_sorted = {b: sorted(v.values()) for b, v in bucket_obs.items()}

    def peer_pct(gid: str, age: int, plays: int):
        vals = bucket_sorted.get(age_bucket(age))
        if not vals or len(vals) < MIN_PEERS:
            return None, len(vals or [])
        x = per_day(plays, age)
        lo = bisect.bisect_left(vals, x)
        hi = bisect.bisect_right(vals, x)
        n = len(vals) - 1  # без самой игры
        if n <= 0:
            return None, 0
        # Сама игра входит в равные (hi-lo>=1), исключаем её.
        return max(0.0, min(1.0, (lo + (hi - lo - 1) / 2) / n)), n

    # Кривая ровесников: p25/p50/p75/p90 накопленных плеев на возрасте a.
    def q(vals, p):
        if not vals:
            return None
        k = (len(vals) - 1) * p
        f, c = math.floor(k), math.ceil(k)
        return vals[f] + (vals[c] - vals[f]) * (k - f)

    # Одна точка на корзину возраста (в медианном возрасте её игр): без «пилы»
    # внутри корзин и для всех возрастов, включая самые старые игры.
    curve = {"age": [], "p25": [], "p50": [], "p75": [], "p90": [], "n": []}
    for b in sorted(bucket_sorted):
        vals = bucket_sorted[b]
        if len(vals) < MIN_PEERS:
            continue
        ages = sorted(bucket_age[b].values())
        a = ages[len(ages) // 2]
        mult = max(a, 1)
        curve["age"].append(a)
        curve["n"].append(len(vals))
        for key, p in (("p25", 0.25), ("p50", 0.5), ("p75", 0.75), ("p90", 0.9)):
            curve[key].append(round(q(vals, p) * mult))

    # --- Метрики по играм ---
    games_out = []
    rates: dict[str, float] = {}
    moms: dict[str, float] = {}
    for gid, m in meta.items():
        series = obs.get(gid)
        if not series:
            continue
        t_date, t_val = series[-1]
        pub = m.get("published")
        age = (last - d(pub)).days if pub else None
        if age is not None and age < 0:
            age = 0
        qn = quantum(t_val)
        removed = bool(m.get("removed"))

        # d1
        d1 = None
        prev = value_at_or_before(series, t_date - dt.timedelta(days=1))
        if prev and prev[0] == t_date - dt.timedelta(days=1):
            d1 = t_val - prev[1]

        # rate / growth
        # Любое наблюдение лучше среднего за жизнь:
        #  прирост > 0  -> реальный темп (точность ± шаг округления / окно);
        #  прирост = 0  -> известна только верхняя граница: темп < шаг / окно (lt=1),
        #                  для ранжирования берём середину [0, граница], но не выше среднего за жизнь;
        #  нет 2 снимков -> оценка «плеи / возраст» (est=1).
        rate = growth = None
        est = lt = 0
        span = 0
        prec = None
        wr = window_rate(series, t_date, t_val, 7)
        if wr:
            r, delta, span, base_val = wr
            prec = qn / span
            if delta > 0:
                rate = r
                if base_val > 0:
                    growth = r * 7 / base_val * 100
            else:
                lt = 1
                rate = prec / 2
                if age is not None:
                    rate = min(rate, per_day(t_val, age))
                growth = 0.0 if span >= 7 else None
        if rate is None and age is not None:
            rate = per_day(t_val, age)
            est = 1

        # momentum: темп за 3 дня против темпа за 7 дней до них
        mom = None
        p3 = value_at_or_before(series, t_date - dt.timedelta(days=3))
        p10 = value_at_or_before(series, t_date - dt.timedelta(days=10))
        if p3 and p10 and p3[0] < t_date and p10[0] < p3[0]:
            r_recent = (t_val - p3[1]) / (t_date - p3[0]).days
            r_prev = (p3[1] - p10[1]) / (p3[0] - p10[0]).days
            if r_prev > 0 and (t_val - p10[1]) >= 3 * qn:
                mom = r_recent / r_prev

        peer, peer_n = (None, 0)
        if pub:
            # возраст на дату последнего наблюдения — именно оно лежит в корзине
            peer, peer_n = peer_pct(gid, max(0, (t_date - d(pub)).days), t_val)

        at = {}
        if pub:
            pub_d = d(pub)
            for day, plays in series:
                a = (day - pub_d).days
                if a in AT_AGES:
                    at[str(a)] = plays

        # ряд, выровненный по датам
        s = [None] * len(dates)
        for day, plays in series:
            s[date_idx[day.isoformat()]] = plays
        # обрезаем ведущие null — клиент восстановит по offset
        off = next((i for i, v in enumerate(s) if v is not None), 0)

        g = {
            "id": gid,
            "n": m.get("name"),
            "p": m.get("publisher"),
            "g": m.get("genre"),
            "pub": pub,
            "ic": m.get("icon"),
            "fs": m.get("first_seen"),
            "rm": m.get("removed"),
            "off": off,
            "s": s[off:],
            "plays": t_val,
            "q": qn,
            "age": age,
            "d1": d1,
            "rate": round(rate, 2) if rate is not None else None,
            "est": est,
            "lt": lt,
            "prec": round(prec, 2) if prec is not None else None,
            "span": span,
            "growth": round(growth, 2) if growth is not None else None,
            "mom": round(mom, 3) if mom is not None else None,
            "peer": round(peer, 4) if peer is not None else None,
            "peerN": peer_n,
            "at": at,
        }
        games_out.append(g)
        if not removed:
            if rate is not None:
                # lt: ранжируем по верхней границе, но ниже всех измеренных
                rates[gid] = rate if not lt else -1e12 + (prec or 0)
            if mom is not None:
                moms[gid] = mom

    pr_rate = percentile_ranks(rates)
    pr_mom = percentile_ranks(moms)
    for g in games_out:
        if g["rm"]:
            g["hot"] = None
            continue
        a = pr_rate.get(g["id"], 0.5)
        b = g["peer"] if g["peer"] is not None else 0.5
        c = pr_mom.get(g["id"], 0.5)
        g["hot"] = round(100 * (W_RATE * a + W_PEER * b + W_MOM * c), 1)
        g["prRate"] = round(a, 4)
        g["prMom"] = round(pr_mom[g["id"]], 4) if g["id"] in pr_mom else None

    games_out.sort(key=lambda g: (g["hot"] is None, -(g["hot"] or 0)))
    return {
        "updated": dates[-1],
        "dates": dates,
        "weights": {"rate": W_RATE, "peer": W_PEER, "mom": W_MOM},
        "curve": curve,
        "games": games_out,
    }


def stamp_assets(docs: Path) -> None:
    """Проставляет ?v=<хэш содержимого> у style.css/app.js в index.html, чтобы браузер
    не держал старые версии из кэша. Хэш детерминирован — без лишних коммитов."""
    import hashlib
    import re
    index = docs / "index.html"
    if not index.exists():
        return
    html = index.read_text(encoding="utf-8")
    for name in ("style.css", "app.js"):
        f = docs / name
        if f.exists():
            h = hashlib.sha1(f.read_bytes()).hexdigest()[:10]
            html = re.sub(r'(["\'])' + re.escape(name) + r'(\?v=[0-9a-f]*)?\1', rf'\g<1>{name}?v={h}\g<1>', html)
    if html != index.read_text(encoding="utf-8"):
        index.write_text(html, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Собрать docs/data.json")
    ap.add_argument("--data-dir", type=Path, default=ROOT / "data")
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "data.json")
    args = ap.parse_args()
    out = build(args.data_dir)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    stamp_assets(args.out.parent)
    print(f"{args.out}: игр {len(out['games'])}, снимков {len(out['dates'])}, "
          f"{args.out.stat().st_size / 1024:.0f} КБ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
