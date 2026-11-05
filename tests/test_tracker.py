import contextlib
import datetime as dt
import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

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

    def test_parse_added(self):
        html = ('<h2 class="shelf">Added Nov 3, 2026 <span>2 games</span></h2>'
                '<a href="/games/loop-pop/">x</a><a href="/games/opps/">y</a>'
                '<h2 class="shelf">Added Oct 30, 2026</h2><a href="/games/opps/">z</a>'
                '<a href="/games/unknown/">u</a><h2>FAQ</h2><a href="/games/faq-game/">f</a>')
        got = collect.parse_added(html, {"loop-pop": "L", "opps": "O", "faq-game": "F"})
        self.assertEqual(got, {"L": "2026-11-03", "O": "2026-10-30"})

    def test_added_date_for_games_appearing_later(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "games.json"
            g = lambda gid: {"id": gid, "name": gid, "plays": 1, "genre": None, "icon": None,
                             "publisher": None, "removed": None, "published": "2026-10-01"}
            collect.update_games_meta(p, [g("A")], "2026-11-03", {})
            meta, _ = collect.update_games_meta(p, [g("A"), g("B")], "2026-11-04", {"A": "2026-10-20"})
            self.assertEqual(meta["A"]["added"], "2026-10-20")  # из /new/
            self.assertEqual(meta["B"]["added"], "2026-11-04")  # появилась после старта трекера

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

    def test_rate_one_step_and_zero(self):
        start = dt.date(2026, 11, 3)
        pub = start - dt.timedelta(days=100)
        out = self.run_days(start, 2, [
            # как Going Balls: 101M -> 102M, шаг 1M — прирост в 1 шаг должен учитываться
            ("BIG", "Big", pub, "S", lambda k, day: 101_000_000 + 1_000_000 * k),
            # число не изменилось: темп < шага (1K), а не «среднее за жизнь» 500K/100 = 5K
            ("FLAT", "Flat", pub, "S", lambda k, day: 500_000),
        ])
        g = {x["id"]: x for x in out["games"]}
        self.assertEqual((g["BIG"]["est"], g["BIG"]["lt"]), (0, 0))
        self.assertEqual(g["BIG"]["rate"], 1_000_000)
        self.assertEqual(g["BIG"]["prec"], 1_000_000)
        self.assertEqual((g["FLAT"]["est"], g["FLAT"]["lt"]), (0, 1))
        self.assertEqual(g["FLAT"]["prec"], 1_000)
        self.assertLessEqual(g["FLAT"]["rate"], 1_000)

    def test_flat_big_game_ranks_below_measured_growth(self):
        start = dt.date(2026, 11, 1)
        out = self.run_days(start, 5, [
            ("BIG", "Big flat", start - dt.timedelta(days=300), "S", lambda k, day: 50_000_000),  # +0, шаг 1M
            ("NEWG", "New real", start - dt.timedelta(days=1), "S", lambda k, day: 30_000 * (k + 1)),  # +30K/день
        ])
        g = {x["id"]: x for x in out["games"]}
        self.assertEqual(g["BIG"]["lt"], 1)
        self.assertLess(g["BIG"]["prRate"], g["NEWG"]["prRate"])

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


class StaleCheckTest(unittest.TestCase):
    TODAY = dt.date(2026, 11, 10)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.snaps, self.state = d / "snapshots", d / "sent.json"
        self.snaps.mkdir()
        self.state.write_text("{}")
        self.calls = []

    def tearDown(self):
        self.tmp.cleanup()

    def _open(self, req, timeout=None):
        self.calls.append(req.data.decode())
        return io.BytesIO(b'{"ok":true}')

    def check(self, days_ago, today=None, opener=None, outcome="success"):
        today = today or self.TODAY
        (self.snaps / f"{self.TODAY - dt.timedelta(days=days_ago)}.csv").write_text("id,plays\n")
        with mock.patch("urllib.request.urlopen", opener or self._open), contextlib.redirect_stdout(io.StringIO()):
            return alerts.check_stale(self.snaps, self.state, today, "T", ["111", "222"], outcome)

    def test_fresh_is_silent(self):
        self.assertEqual(self.check(2), 0)  # одно пропущенное обновление — ещё не тревога
        self.assertEqual(self.calls, [])

    def test_stale_alerts_both_once_a_day(self):
        self.assertEqual(self.check(3), 0)
        self.assertEqual(len(self.calls), 2)
        self.check(3)  # следующий запуск в тот же день
        self.assertEqual(len(self.calls), 2)
        self.check(3, today=self.TODAY + dt.timedelta(days=1))  # на следующий день — напоминание
        self.assertEqual(len(self.calls), 4)

    def test_reason_when_collect_failed(self):
        self.check(5, outcome="failure")
        self.assertIn("%D0%BF%D0%B0%D0%B4%D0%B0%D0%B5%D1%82", self.calls[0])  # «падает»

    def test_failed_send_is_retried_next_run(self):
        def down(req, timeout=None):
            raise OSError("down")
        self.assertEqual(self.check(3, opener=down), 1)
        self.assertEqual(json.loads(self.state.read_text()), {})
        self.assertEqual(self.check(3), 0)
        self.assertEqual(len(self.calls), 2)

    def test_no_snapshots_is_silent(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(alerts.check_stale(self.snaps, self.state, self.TODAY, "T", ["1"]), 0)


class TelegramDeliveryTest(unittest.TestCase):
    """Сбой доставки одному получателю не должен задевать остальных и терять состояние."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.data, self.state = d / "data.json", d / "sent.json"
        game = {"id": "NEW1", "n": "New", "p": "S", "g": "Arcade", "off": 0, "s": [5000], "q": 100,
                "age": 2, "peer": 0.99, "plays": 5000, "hot": 50.0, "rate": 2500.0, "est": 1, "lt": 0}
        self.data.write_text(json.dumps({"updated": "2026-11-05", "dates": ["2026-11-05"], "games": [game]}))
        self.state.write_text("{}")  # не первый запуск

    def tearDown(self):
        self.tmp.cleanup()

    def fake_urlopen(self, blocked=(), down=False):
        self.calls = []

        def _open(req, timeout=None):
            chat = dict(p.split("=", 1) for p in req.data.decode().split("&"))["chat_id"]
            self.calls.append(chat)
            if down:
                raise OSError("network down")
            if chat in blocked:
                raise urllib.error.HTTPError(
                    req.full_url, 403, "Forbidden", None,
                    io.BytesIO(b'{"ok":false,"description":"Forbidden: bot was blocked by the user"}'))
            return io.BytesIO(b'{"ok":true}')
        return _open

    def run_main(self, urlopen, *extra):
        env = {"TELEGRAM_BOT_TOKEN": "SECRET-TOKEN", "TELEGRAM_CHAT_ID": "111,222"}
        out = io.StringIO()
        argv = ["alerts.py", "--data", str(self.data), "--state", str(self.state), *extra]
        with mock.patch.dict(os.environ, env), mock.patch.object(sys, "argv", argv), \
                mock.patch("urllib.request.urlopen", urlopen), contextlib.redirect_stdout(out):
            code = alerts.main()
        return code, out.getvalue()

    def sent_state(self):
        return json.loads(self.state.read_text())

    def test_all_delivered(self):
        code, _ = self.run_main(self.fake_urlopen())
        self.assertEqual(code, 0)
        self.assertEqual(self.calls.count("111"), self.calls.count("222"))
        self.assertIn("breakout:NEW1", self.sent_state())

    def test_first_chat_blocked_second_still_gets_it(self):
        code, out = self.run_main(self.fake_urlopen(blocked={"111"}))
        self.assertIn("222", self.calls)  # второй получил, хотя первый упал
        self.assertEqual(code, 1)  # о сбое сообщаем
        self.assertIn("bot was blocked", out)
        self.assertIn("breakout:NEW1", self.sent_state())  # без дублей тому, кто получил

    def test_nobody_delivered_is_retried_next_run(self):
        code, _ = self.run_main(self.fake_urlopen(down=True))
        self.assertEqual(code, 1)
        self.assertEqual(self.sent_state(), {})  # сигнал не потерян

    def test_token_not_leaked_in_errors(self):
        def boom(req, timeout=None):
            raise OSError(f"failed {req.full_url}")
        _, out = self.run_main(boom)
        self.assertNotIn("SECRET-TOKEN", out)
        self.assertIn("***", out)

    def test_rate_limit_retried_once(self):
        calls = []

        def _open(req, timeout=None):
            calls.append(1)
            if len(calls) == 1:
                raise urllib.error.HTTPError(
                    req.full_url, 429, "Too Many", None,
                    io.BytesIO(b'{"ok":false,"parameters":{"retry_after":1}}'))
            return io.BytesIO(b'{"ok":true}')
        with mock.patch("urllib.request.urlopen", _open), mock.patch("time.sleep") as sl:
            failed = alerts.send_telegram("T", ["111"], "hi")
        self.assertEqual(failed, {})
        sl.assert_called_once_with(1)

    def test_test_mode_reports_failure(self):
        code, out = self.run_main(self.fake_urlopen(blocked={"222"}), "--test")
        self.assertEqual(code, 1)
        self.assertIn("1 из 2", out)


if __name__ == "__main__":
    unittest.main()
