import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tracker"))

import alerts  # noqa: E402
import build  # noqa: E402
import collect  # noqa: E402

GENRES = ["Simulation", "Arcade"]


def page(date: str, games: list[tuple]) -> str:
    """games: (id, name, plays, published YYYYMMDD, publisher)"""
    rows = [[n, p, i % 2, gid, f"https://img/{gid}", 0, 0, pub, 0, pubd]
            for i, (gid, n, p, pubd, pub) in enumerate(games)]
    return (f'<span class="tag">updated {date}</span><script>const DATA={json.dumps(rows)};\n'
            f"const GENRES={json.dumps(GENRES)};</script>")


def peers(n=60, day0=dt.date(2026, 11, 1)):
    # фон: n игр, опубликованных каждый день, с плеями ~ 1000/день жизни
    out = []
    for i in range(n):
        pub = day0 - dt.timedelta(days=i)
        out.append([f"P{i:03d}", f"Peer {i}", pub, f"Pub{i % 5}"])
    return out


class ParseTest(unittest.TestCase):
    def test_parse_catalog(self):
        html = page("2026-11-03", [("A", "Alpha", 1200, 20261101, "Studio")] * 600)
        date, games = collect.parse_catalog(html)
        self.assertEqual(date, "2026-11-03")
        self.assertEqual(games[0]["published"], "2026-11-01")
        self.assertEqual(games[0]["genre"], "Simulation")
        self.assertEqual(games[0]["publisher"], "Studio")

    def test_too_few_games_raises(self):
        with self.assertRaises(collect.ParseError):
            collect.parse_catalog(page("2026-11-03", [("A", "Alpha", 1, 0, "S")]))

    def test_missing_data_raises(self):
        with self.assertRaises(collect.ParseError):
            collect.parse_catalog("<html>nothing</html>")

    def test_quantum(self):
        self.assertEqual(build.quantum(999), 1)
        self.assertEqual(build.quantum(6100), 100)
        self.assertEqual(build.quantum(398000), 1000)
        self.assertEqual(build.quantum(6_100_000), 100_000)
        self.assertEqual(build.quantum(15_000_000), 1_000_000)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        collect.MIN_GAMES = 1

    def tearDown(self):
        self.tmp.cleanup()

    def run_days(self, start: dt.date, days: int, plays_fn):
        bg = peers()
        for k in range(days):
            day = start + dt.timedelta(days=k)
            games = []
            for gid, name, pub, publ in bg:
                age = (day - pub).days
                games.append((gid, name, 1000 * max(age, 1), int(pub.strftime("%Y%m%d")), publ))
            for gid, name, pub, publ, fn in plays_fn:
                games.append((gid, name, fn(k, day), int(pub.strftime("%Y%m%d")), publ))
            collect.collect(self.dir, html=page(day.isoformat(), games))
        return build.build(self.dir)

    def test_metrics_and_spike(self):
        start = dt.date(2026, 11, 1)
        star_pub = start - dt.timedelta(days=1)

        def star(k, day):  # новинка, растущая в 20 раз быстрее фона
            return 20_000 * (k + 1)

        def spiky(k, day):  # ровно 10K/день, на последний день +100K
            return 500_000 + 10_000 * k + (90_000 if k == 13 else 0)

        out = self.run_days(start, 14, [
            ("STAR", "Star", star_pub, "Studio", star),
            ("SPIKE", "Spiky", start - dt.timedelta(days=100), "Studio", spiky),
        ])
        g = {x["id"]: x for x in out["games"]}
        self.assertEqual(len(out["dates"]), 14)

        s = g["STAR"]
        self.assertEqual(s["est"], 0)
        self.assertAlmostEqual(s["rate"], 20_000, delta=1)
        self.assertEqual(s["d1"], 20_000)
        self.assertGreaterEqual(s["peer"], 0.95)
        self.assertEqual(s["at"]["1"], 20_000)
        self.assertEqual(s["at"]["7"], 140_000)
        self.assertAlmostEqual(s["mom"], 1.0, places=2)

        sp = g["SPIKE"]
        self.assertGreater(sp["mom"], 1.5)

        sig = alerts.detect(out)
        spikes = [x for x in sig if x["type"] == "spike"]
        self.assertEqual([x["g"]["id"] for x in spikes], ["SPIKE"])
        self.assertAlmostEqual(spikes[0]["today"], 100_000)

        # дедупликация: повторный прогон в тот же день ничего не шлёт
        sent = {f"{x['type']}:{x['g']['id']}": out["updated"] for x in sig}
        self.assertEqual(alerts.filter_new(sig, sent, out["updated"]), [])

        msgs = alerts.render(sig, out, "https://example.org/")
        self.assertTrue(any("Резкие пики" in m for m in msgs))

    def test_old_snapshot_not_overwritten(self):
        bg = [(f"G{i}", "g", 100 + i, 20260101, "S") for i in range(3)]
        collect.collect(self.dir, html=page("2026-11-05", bg))
        res = collect.collect(self.dir, html=page("2026-11-04", bg))
        self.assertFalse(res["changed"])
        self.assertEqual(sorted(p.stem for p in (self.dir / "snapshots").glob("*.csv")), ["2026-11-05"])

    def test_single_snapshot_uses_estimate(self):
        out = self.run_days(dt.date(2026, 11, 1), 1, [])
        g = out["games"][0]
        self.assertEqual(g["est"], 1)
        self.assertIsNone(g["mom"])
        self.assertIsNone(g["d1"])


if __name__ == "__main__":
    unittest.main()
