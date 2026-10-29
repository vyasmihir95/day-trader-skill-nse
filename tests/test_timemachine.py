"""trader-timemachine tests, on real NSE data: RELIANCE's 60 days of 5-minute bars
(Yahoo chart, range=60d interval=5m, fetched 2026-10-29 after the close) and the
exit plan trader-exit writes for the 2026-10-14 opening-range breakout (9:45 AM,
tests/fixtures/trader-exit/). The numbers below were derived by running the code
and spot-checked by hand against the raw bars (see the Spot checks class)."""

import copy
import datetime
import gzip
import hashlib
import io
import json
import os
import random
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

tm = load("trader-timemachine", "timemachine")

DIR = os.path.join(FIX, "trader-timemachine")
RELIANCE = os.path.join(DIR, "RELIANCE-60d5m.json.gz")   # Yahoo v8 chart for RELIANCE.NS, fetched 2026-10-29 after the close
PLAN_FILE = os.path.join(FIX, "trader-exit", "plan-RELIANCE-2026-10-14.json")
with gzip.open(RELIANCE, "rt", encoding="utf-8") as _fh:
    TEXT = _fh.read()
with open(PLAN_FILE, encoding="utf-8") as _fh:
    PLAN = json.load(_fh)
NOW = "2026-10-29 16:00"
NOW_UTC = tm.parse_now(NOW)

# the plan as trader-exit writes it for the 2026-10-14 breakout: opening range 1162.70 to 1169.30,
# the 9:40 bar (bar 5) closes at 1170.70, stop at the range midpoint 1166.00, 2R target 1180.10
assert PLAN["entry"] == 1170.7 and PLAN["stop"] == 1166.0 and PLAN["target"] == 1180.1 and PLAN["entry_bar"] == 5

# fingerprints of this run (seed 19, 10,000 replays): the same numbers on any machine
SIM_BANDS = "b6d9a1cb6d8fd9b6"
SIM_PATHS = "da352736576fa856"
SIM_TRADES = "c3f699a6fdba0ec1"
SIM_HIST = {"lo": 1145.15, "hi": 1192.52,
            "bins": [95, 82, 117, 163, 213, 311, 397, 455, 623, 696, 761, 841, 805, 827, 698, 664, 528, 436, 343,
                     279, 212, 133, 94, 63]}
ONE_R_TARGET = 1175.4        # 1170.70 + 4.70


def sha(x):
    return hashlib.sha256(json.dumps(x).encode("utf-8")).hexdigest()[:16]


def plan(**kw):
    raw = dict(PLAN)
    raw.update(kw)
    return tm.normalize_plan(raw)


def machine(p=None, text=TEXT, now=NOW_UTC, **kw):
    return tm.time_machine(text, p or plan(), now, **kw)


def edited(fn):
    """The fixture's chart JSON, changed by fn(result), back as text."""
    doc = json.loads(TEXT)
    fn(doc["chart"]["result"][0])
    return json.dumps(doc)


def keep_bars(result, keep):
    """Keep only the bars where keep(ts) is true."""
    q = result["indicators"]["quote"][0]
    idx = [i for i, ts in enumerate(result["timestamp"]) if keep(ts)]
    result["timestamp"] = [result["timestamp"][i] for i in idx]
    for k in q:
        q[k] = [q[k][i] for i in idx]


def lt(ts):
    return tm.ts_to_local(ts)


def stamp(day, hhmm):
    """Unix seconds of an IST wall-clock time."""
    h, m = hhmm.split(":")
    local = datetime.datetime.combine(datetime.date.fromisoformat(day), datetime.time(int(h), int(m)))
    return int((tm.local_to_utc(local) - tm.EPOCH).total_seconds())


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = tm.main(argv)
    return code, out.getvalue(), err.getvalue()


class Session(unittest.TestCase):
    """The default plan: entry 1170.70 at 9:45 AM, stop 1166.00, 2R target 1180.10."""

    @classmethod
    def setUpClass(cls):
        cls.doc = machine()

    def test_the_odds(self):
        m = self.doc["mc"]
        self.assertEqual((m["replays"], m["seed"], m["window"], m["until"]), (10000, 19, 2, "15:30"))
        self.assertEqual(m["pool_sessions"], 49)
        self.assertEqual(m["bars_left"], 69)             # bars 6..74 of a 75-bar day
        self.assertEqual((m["pool_from"], m["pool_to"]), ("2026-08-04", "2026-10-13"))
        self.assertEqual(m["p_target"], 0.2296)
        self.assertEqual(m["p_stop"], 0.6512)
        self.assertEqual(m["p_neither"], 0.1192)
        self.assertEqual(m["p_green"], 0.3175)
        self.assertEqual(m["median_close"], 1169.27)
        self.assertEqual(m["p5_close"], 1152.82)
        self.assertEqual(m["p95_close"], 1185.47)
        self.assertEqual(m["avg_R"], -0.131)
        self.assertEqual(m["r_at_target"], 2.0)

    def test_percentages_add_up_to_100(self):
        m = self.doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(sum(m["hist"]["bins"]), sum(SIM_HIST["bins"]))

    def test_numbers_are_pinned(self):
        m = self.doc["mc"]
        self.assertEqual(sha(m["bands"]), SIM_BANDS)
        self.assertEqual(len(m["sample_paths"]), 160)
        self.assertEqual(sha(m["sample_paths"]), SIM_PATHS)
        self.assertEqual(m["hist"], SIM_HIST)
        self.assertEqual(m["bands"][-1], [1152.77, 1163.15, 1169.25, 1176.21, 1185.86])

    def test_the_history_replay(self):
        h = self.doc["history"]
        self.assertEqual((h["sessions"], h["triggered"]), (49, 23))
        self.assertEqual((h["target"], h["stop"], h["close"], h["green"]), (6, 11, 6, 9))
        self.assertEqual(h["target"] + h["stop"] + h["close"], h["triggered"])
        self.assertEqual(h["avg_R"], 0.114)
        self.assertEqual(h["rule"], {"setup": "orb", "or_minutes": 15, "stop_rule": "or_mid", "target_r": 2.0})
        slim = [{"date": t["date"], "entry": t["entry"], "outcome": t["outcome"], "R": t["R"]} for t in h["trades"]]
        self.assertEqual(sha(slim), SIM_TRADES)
        first = h["trades"][0]
        self.assertEqual((first["date"], first["time"], first["entry"], first["stop"], first["target"], first["outcome"]),
                         ("2026-08-06", "9:45 AM", 1300.9, 1292.05, 1318.6, "target"))
        # every entry, stop and target sits on NSE's 0.05 tick, every stop under its entry
        for t in h["trades"]:
            for k in ("entry", "stop", "target"):
                self.assertAlmostEqual(t[k] / 0.05, round(t[k] / 0.05), places=6, msg=t)
            self.assertTrue(t["stop"] < t["entry"] < t["target"], t)

    def test_the_verdict_leans_toward_the_stop(self):
        self.assertEqual(self.doc["verdict"],
                         "Leans toward the stop: at 2R the target has to come first in 33 % of the replays that hit "
                         "one side; it did in 26 % (target first 23.0 % vs stop first 65.1 %).")
        gap, share, even = tm.lean(self.doc["mc"])
        self.assertEqual((round(share, 3), round(even, 3)), (0.261, 0.333))
        self.assertEqual(self.doc["verdict_notes"], [])

    def test_one_r_plan(self):
        doc = machine(plan(target=ONE_R_TARGET))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (1.0, 0.4416, 0.5444, 0.014, -0.103))
        self.assertEqual(doc["verdict"], "Leans toward the stop: target first 44.2 % vs stop first 54.4 %.")
        h = doc["history"]
        self.assertEqual((h["triggered"], h["target"], h["stop"], h["close"], h["avg_R"]), (23, 10, 10, 3, 0.003))
        self.assertEqual(h["rule"]["target_r"], 1.0)

    def test_neither_note_when_most_replays_hit_no_side(self):
        # a wide stop and a far target: most replays are still open at the close
        doc = machine(plan(stop=1160.0, target=1190.0, stop_rule="pct"))
        m = doc["mc"]
        self.assertEqual((m["r_at_target"], m["p_target"], m["p_stop"], m["p_neither"], m["avg_R"]),
                         (1.8037, 0.0323, 0.295, 0.6727, -0.105))
        self.assertEqual(doc["verdict"], "Strongly toward the stop: at 1.8R the target has to come first in 36 % of "
                                         "the replays that hit one side; it did in 10 % (target first 3.2 % vs stop "
                                         "first 29.5 %).")
        self.assertEqual(doc["verdict_notes"],
                         ["67.3 % of the replays hit neither by 3:30 PM; the price at 3:30 PM decides those."])
        text = "\n".join(tm.report(doc))
        self.assertIn(doc["verdict"] + "\n" + doc["verdict_notes"][0], text)

    def test_bands_start_at_the_entry(self):
        m = self.doc["mc"]
        self.assertEqual(m["bands"][0], [1170.7] * 5)
        self.assertEqual(len(m["bands"]), 70)
        self.assertEqual((m["band_times"][0], m["band_times"][-1], len(m["band_times"])), ("09:45", "15:30", 70))
        self.assertTrue(all(p[0] == 1170.7 and len(p) == 70 for p in m["sample_paths"]))

    def test_the_plan_is_carried_through(self):
        p = self.doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["entry_bar"], p["entry_time"]),
                         ("RELIANCE", 1170.7, 1166.0, 1180.1, 5, "9:45 AM"))
        self.assertEqual((p["risk_per_share"], p["target_r"], p["flat_by"]), (4.7, 2.0, "15:15"))
        self.assertEqual(self.doc["warnings"], [])
        self.assertEqual(self.doc["data"]["symbol"], "RELIANCE")
        self.assertEqual(self.doc["data"]["last_bar"], "2026-10-29 15:15 IST")

    def test_the_text_report(self):
        text = "\n".join(tm.report(self.doc))
        for line in ("RELIANCE buy, Wednesday 2026-10-14: entry ₹1,170.70 at 9:45 AM (bar 5), stop ₹1,166.00, "
                     "target ₹1,180.10 (2R, risk ₹4.70 a share)",
                     "10,000 replays", "9:45 AM to 3:30 PM (69 five-minute bars)", "seed 19", "49 full sessions",
                     "(2026-08-04 to 2026-10-13)",
                     "target first   23.0 %", "stop first     65.1 %", "neither        11.9 %   still open at 3:30 PM",
                     "green          31.8 %",
                     "median ₹1,169.27, 90 % of replays between ₹1,152.82 and ₹1,185.47",
                     "Average R: -0.131", "fired 23 times: 6 target, 11 stop, 6 neither. Average R +0.114",
                     "Leans toward the stop: at 2R", "not a prediction", "--seed 19"):
            self.assertIn(line, text)
        self.assertNotIn("$", text)
        for word in ("profit", "P&L", "guarantee", "real time", "tick by tick"):
            self.assertNotIn(word, text)

    def test_the_missing_last_bars_are_said_out_loud(self):
        # Yahoo's NSE feed has no 15:20 bar on any of the 60 days: those bars are taken as unchanged, and the report says so
        self.assertEqual(self.doc["data"]["tail_bars_filled"], {"sessions": 49, "bars": 101})
        text = "\n".join(tm.report(self.doc))
        self.assertIn("Yahoo had no bar for the last minutes of 49 session(s) (101 bars among the final 3 slots, "
                      "15:15 to 15:25): each was taken as unchanged from the bar before.", text)

    def test_another_day_another_breakout(self):
        # 2026-10-19: the 9:45 bar closes at 1227.80 over the range 1207.30 to 1223.40 (bar 6, entry 9:50 AM);
        # midpoint 1215.35, risk 12.45, 2R target 1252.70
        p = plan(date="2026-10-19", entry=1227.8, entry_time="9:50 AM", entry_bar=6, stop=1215.35, target=1252.7,
                 opening_range={"high": 1223.4, "low": 1207.3, "minutes": 15})
        doc = machine(p, replays=2000)
        self.assertEqual((doc["plan"]["risk_per_share"], doc["plan"]["target_r"], doc["plan"]["entry_bar"]), (12.45, 2.0, 6))
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_mid")
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-16")
        self.assertEqual(doc["mc"]["bars_left"], 68)
        self.assertEqual(doc["warnings"], [])


class SpotChecks(unittest.TestCase):
    """Numbers re-derived by hand from the raw bars, not from the code under test."""

    @classmethod
    def setUpClass(cls):
        res = json.loads(TEXT)["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        cls.raw = {}
        for i, ts in enumerate(res["timestamp"]):
            if q["close"][i] is not None:
                l = tm.ts_to_local(ts)
                cls.raw[(l.date().isoformat(), l.strftime("%H:%M"))] = tuple(q[k][i] for k in ("open", "high", "low", "close"))

    def test_the_opening_range_and_the_breakout_bar(self):
        day = "2026-10-14"
        first3 = [self.raw[(day, t)] for t in ("09:15", "09:20", "09:25")]
        self.assertEqual((max(b[1] for b in first3), min(b[2] for b in first3)), (1169.300048828125, 1162.699951171875))
        # no close over the range high until the 9:40 bar, the sixth bar of the day (slot 5), done at 9:45
        for t in ("09:30", "09:35"):
            self.assertLess(self.raw[(day, t)][3], 1169.3)
        self.assertEqual(self.raw[(day, "09:40")][3], 1170.699951171875)
        self.assertEqual(tm.clock12(tm.OPEN_MIN + 5 * (5 + 1)), "9:45 AM")
        self.assertEqual(PLAN["opening_range"], {"high": 1169.3, "low": 1162.7, "minutes": 15})
        self.assertEqual(PLAN["stop"], 1166.0)     # floor((1169.30 + 1162.70) / 2) on the 0.05 tick

    def test_the_first_history_trade_by_hand(self):
        # 2026-08-06: range 1283.60 to 1300.50, the 9:40 bar closes 1300.90 > 1300.50: entry 1300.90,
        # stop = (1300.5 + 1283.6) / 2 = 1292.05 (already on the tick), risk 8.85, 2R target 1300.90 + 17.70 = 1318.60
        day = "2026-08-06"
        bars = [(t, self.raw[(day, t)]) for t in sorted(k[1] for k in self.raw if k[0] == day)]
        oh = max(b[1] for t, b in bars[:3])
        ol = min(b[2] for t, b in bars[:3])
        self.assertEqual((round(oh, 2), round(ol, 2)), (1300.5, 1283.6))
        self.assertEqual(bars[5][0], "09:40")
        self.assertGreater(bars[5][1][3], oh)
        self.assertTrue(all(b[3] <= oh for t, b in bars[3:5]))
        # later bars: no low at 1292.05 or under before the first high at 1318.60 or over
        hit = next(((t, "stop" if b[2] <= 1292.05 else "target") for t, b in bars[6:] if b[2] <= 1292.05 or b[1] >= 1318.6), None)
        self.assertEqual(hit[1], "target")
        first = machine(replays=10)["history"]["trades"][0]
        self.assertEqual((first["date"], first["entry"], first["stop"], first["target"], first["outcome"], first["R"]),
                         (day, 1300.9, 1292.05, 1318.6, "target", 2.0))

    def test_a_pool_ratio_by_hand(self):
        # pool[1] is each full session's second bar over the first bar's close, oldest session first
        pool = tm.build_pool(tm.full_sessions(tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "RELIANCE"), NOW_UTC)[1]),
                                              "2026-10-14")[0])
        o, h, l, c = self.raw[("2026-08-04", "09:20")]
        pc = self.raw[("2026-08-04", "09:15")][3]
        self.assertEqual(len(pool), 75)
        self.assertEqual(len(pool[1]), 49)
        self.assertEqual(pool[1][0], (o / pc, h / pc, l / pc, c / pc))
        self.assertEqual(pool[0], [])
        # slot 73 (the 15:20 bar) is never in Yahoo's data: every ratio is a flat bar
        self.assertEqual(set(pool[73]), {(1.0, 1.0, 1.0, 1.0)})

    def test_the_session_is_75_bars(self):
        self.assertEqual((tm.OPEN_MIN, tm.CLOSE_MIN, tm.SESSION_BARS), (9 * 60 + 15, 15 * 60 + 30, 75))
        self.assertEqual(tm.clock12(tm.OPEN_MIN + 5 * tm.SESSION_BARS), "3:30 PM")


class Seeds(unittest.TestCase):
    def test_same_seed_same_numbers(self):
        a = machine(replays=2000, seed=5)["mc"]
        b = machine(replays=2000, seed=5)["mc"]
        self.assertEqual(a, b)

    def test_another_seed_gives_other_numbers(self):
        m = machine(seed=7)["mc"]
        self.assertNotEqual((m["p_target"], m["p_stop"], m["median_close"]), (0.2296, 0.6512, 1169.27))
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertNotEqual(sha(m["bands"]), SIM_BANDS)

    def test_the_draws_are_stable_across_pythons(self):
        # random.Random(seed), randint and choice are stable since Python 3.2; the numbers above
        # are the real proof, this pins the first draws so a failure points at the RNG
        r = random.Random(19)
        self.assertEqual([r.randint(-2, 2) for _ in range(8)], [-2, 2, -2, 2, -1, 1, 0, 2])
        self.assertEqual(r.choice(range(49)), 18)

    def test_window_and_replays(self):
        a = machine(replays=500, window=0)["mc"]
        self.assertEqual((a["replays"], a["window"]), (500, 0))
        self.assertNotEqual(a["bands"], machine(replays=500)["mc"]["bands"])
        with self.assertRaises(tm.TMError):
            machine(replays=0)
        with self.assertRaises(tm.TMError):
            machine(window=11)


class Bars(unittest.TestCase):
    def setUp(self):
        self.result = tm.chart_result(TEXT, "RELIANCE")

    def test_the_bar_in_progress_is_dropped(self):
        meta, bars, dropped = tm.parse_bars(self.result, NOW_UTC)
        # 115 empty rows (no 15:20 bar on any day, 15:25 and others on some); the data's own clock
        # (15:29:03 IST) says the 2026-10-29 15:25 bar was not closed yet
        self.assertEqual(dropped, {"no_price": 115, "off_grid": 0, "not_complete": 1, "outside_hours": 0})
        last = bars[-1]
        self.assertEqual((last["date"], last["start"], last["slot"]), ("2026-10-29", "15:15", 72))
        self.assertEqual(len(bars), 4500 - 115 - 1)
        # with the real clock (any time later) the data's own time still holds the 15:25 bar back
        self.assertEqual(tm.parse_bars(self.result, tm.parse_now(None))[1][-1]["start"], "15:15")
        entry = [b for b in bars if b["date"] == "2026-10-14" and b["slot"] == 5][0]
        self.assertEqual((entry["start"], entry["c"]), ("09:40", 1170.699951171875))

    def test_now_pins_the_clock(self):
        bars = tm.parse_bars(self.result, tm.parse_now("2026-10-14 09:42"))[1]
        today = [b for b in bars if b["date"] == "2026-10-14"]
        self.assertEqual(len(today), 5)                         # 9:15 to 9:35: the 9:40 bar is still open
        with self.assertRaises(tm.TMError) as cm:
            machine(now=tm.parse_now("2026-10-14 09:42"))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("09:35 bar", str(cm.exception))
        self.assertEqual(machine(now=tm.parse_now("2026-10-14 09:45"), replays=200)["mc"]["pool_sessions"], 49)
        # "IST" after the time is allowed, ...Z is absolute
        self.assertEqual(tm.parse_now("2026-10-14 09:45 IST"), tm.parse_now("2026-10-14T04:15Z"))

    def test_ist_has_no_daylight_saving(self):
        # 09:15 IST is 03:45 UTC all year: the offset never changes
        self.assertEqual(stamp("2026-10-30", "09:15"), 1793331900)
        self.assertEqual(stamp("2026-11-02", "09:15") - stamp("2026-10-30", "09:15"), 3 * 86400)
        self.assertEqual(tm.to_local(tm.utc_of(1793331900)), datetime.datetime(2026, 10, 30, 9, 15))
        stamps = [stamp("2026-10-30", "09:15"), stamp("2026-11-02", "09:15"), stamp("2026-11-02", "09:10"),
                  stamp("2026-11-02", "15:25"), stamp("2026-11-02", "15:30")]
        res = {"meta": {"symbol": "X.NS"}, "timestamp": stamps,
               "indicators": {"quote": [{"open": [1] * 5, "high": [1] * 5, "low": [1] * 5, "close": [1] * 5,
                                         "volume": [0] * 5}]}}
        meta, bars, dropped = tm.parse_bars(res, tm.parse_now("2026-11-03T00:00Z"))
        self.assertEqual([(b["date"], b["start"], b["slot"]) for b in bars],
                         [("2026-10-30", "09:15", 0), ("2026-11-02", "09:15", 0), ("2026-11-02", "15:25", 74)])
        self.assertEqual(dropped["outside_hours"], 2)           # 09:10 before the open, 15:30 at the close

    def test_the_live_tick_is_off_grid(self):
        def tick(res):
            ts = stamp("2026-10-29", "15:00") + 45             # 15:00:45, Yahoo's live price
            res["timestamp"].append(ts)
            for k, arr in res["indicators"]["quote"][0].items():
                arr.append(1100.0)
        meta, bars, dropped = tm.parse_bars(tm.chart_result(edited(tick), "RELIANCE"), NOW_UTC)
        self.assertEqual(dropped["off_grid"], 1)
        self.assertEqual(len(bars), 4500 - 115 - 1)

    def test_a_short_session_is_left_out(self):
        # the real data has one: 2026-10-15 has no 11:45 and 11:50 bars
        doc = machine(plan(date="2026-10-16", entry=1214.8, entry_time="1:15 PM", entry_bar=None, stop=1206.0,
                           target=1232.4, opening_range={"high": 1214.4, "low": 1197.6, "minutes": 15}), replays=200)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-10-15", "bars": 72}])
        self.assertEqual(doc["mc"]["pool_sessions"], 50)      # 10-14 and the 49 before it; 10-15 is out
        self.assertNotIn("2026-10-15", [t["date"] for t in doc["history"]["trades"]])
        self.assertIn("Left out 1 short session(s) (half days or gaps): 2026-10-15.", "\n".join(tm.report(doc)))

    def test_a_half_day_is_left_out(self):
        def cut(res):  # a Muhurat-style short session: 2026-09-04 ends at 11:15, 24 bars
            keep_bars(res, lambda ts: not (lt(ts).date().isoformat() == "2026-09-04" and lt(ts).hour * 60 + lt(ts).minute >= 11 * 60 + 15))
        doc = machine(text=edited(cut), replays=300)
        self.assertEqual(doc["mc"]["pool_sessions"], 48)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-09-04", "bars": 24}])
        self.assertEqual(doc["history"]["sessions"], 48)
        self.assertNotIn("2026-09-04", [t["date"] for t in doc["history"]["trades"]])

    def test_a_gap_inside_the_day_leaves_the_session_out(self):
        def hole(res):
            keep_bars(res, lambda ts: not (lt(ts).date().isoformat() == "2026-09-22" and lt(ts).strftime("%H:%M") == "10:30"))
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["mc"]["pool_sessions"], 48)
        self.assertEqual(doc["data"]["short_sessions_left_out"], [{"date": "2026-09-22", "bars": 72}])

    def test_the_last_bars_missing_are_filled_but_a_session_without_the_open_is_not(self):
        days = tm.split_sessions(tm.parse_bars(tm.chart_result(TEXT, "RELIANCE"), NOW_UTC)[1])
        full, short, filled = tm.full_sessions(days, "2026-10-14")
        self.assertEqual((len(full), short, len(filled)), (49, [], 49))
        self.assertTrue(all(v == [73] or v == [73, 74] or v == [72, 73, 74] or v == [72, 73] for v in filled.values()), filled)
        d, rows = full[0]
        self.assertEqual((d, len(rows), rows[73]["filled"]), ("2026-08-04", 75, True))
        self.assertEqual((rows[73]["o"], rows[73]["h"], rows[73]["l"], rows[73]["c"]), (rows[72]["c"],) * 4)
        # a gap at the very start or the day before the last three slots is not filled
        for gone in (0, 71):
            sess = {i: b for i, b in days["2026-08-05"].items() if i != gone}
            f2, s2, _ = tm.full_sessions({"2026-08-05": sess}, "2026-09-01")
            self.assertEqual((len(f2), len(s2)), (0, 1), gone)

    def test_today_is_not_in_the_pool(self):
        doc = machine(replays=200)
        self.assertEqual(doc["mc"]["pool_to"], "2026-10-13")
        self.assertNotIn("2026-10-14", [t["date"] for t in doc["history"]["trades"]])

    def test_a_missing_price_is_dropped(self):
        def hole(res):
            i = [j for j, ts in enumerate(res["timestamp"]) if lt(ts).strftime("%Y-%m-%d %H:%M") == "2026-08-05 10:00"][0]
            res["indicators"]["quote"][0]["close"][i] = None
        doc = machine(text=edited(hole), replays=200)
        self.assertEqual(doc["data"]["dropped"]["no_price"], 116)
        self.assertEqual(doc["mc"]["pool_sessions"], 48)   # 2026-08-05 now has a gap

    def test_empty_and_broken_data(self):
        empty = json.dumps({"chart": {"result": [{"meta": {"symbol": "RELIANCE.NS"}, "timestamp": [],
                                                  "indicators": {"quote": [{}]}}], "error": None}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=empty)
        self.assertIn("no complete 5-minute bars", str(cm.exception))
        gone = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                               "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(tm.TMError) as cm:
            machine(text=gone)
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(tm.TMError):
            machine(text="<html>rate limited</html>")

    def test_fewer_than_20_sessions_is_refused(self):
        def trim(res):
            keep_bars(res, lambda ts: lt(ts).date().isoformat() >= "2026-09-16")
        with self.assertRaises(tm.TMError) as cm:
            machine(text=edited(trim))
        self.assertIn("only 19 full session(s)", str(cm.exception))
        self.assertIn("at least 20", str(cm.exception))

        def twenty(res):
            keep_bars(res, lambda ts: lt(ts).date().isoformat() >= "2026-09-15")
        self.assertEqual(machine(text=edited(twenty), replays=200)["mc"]["pool_sessions"], 20)

    def test_there_is_no_early_close_on_nse(self):
        # NSE has no early closes: the replay always runs to the 15:30 close
        doc = machine(plan(date="2026-10-14"), replays=100)
        self.assertEqual(doc["mc"]["until"], "15:30")
        with self.assertRaises(tm.TMError) as cm:
            machine(until="15:35", replays=10)
        self.assertIn("no later than 3:30 PM (the NSE close)", str(cm.exception))

    def test_the_data_must_be_the_plans_ticker(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(symbol="INFY"))
        self.assertIn("data is for RELIANCE but the plan is for INFY", str(cm.exception))
        # a typed .NS suffix is the same ticker
        self.assertEqual(machine(plan(symbol="reliance.ns"), replays=50)["plan"]["symbol"], "RELIANCE")


class ClosedDays(unittest.TestCase):
    def test_holiday_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-20"))
        self.assertIn("NSE is closed on 2026-10-20 (NSE holiday: Dussehra)", str(cm.exception))
        self.assertIn("nseindia.com", str(cm.exception))

    def test_weekend_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-17"))
        self.assertIn("NSE is closed on 2026-10-17 (weekend)", str(cm.exception))

    def test_the_holiday_list(self):
        self.assertEqual(len(tm.NSE_HOLIDAYS), 15)
        self.assertEqual(tm.NSE_HOLIDAYS["2026-11-10"], "Diwali-Balipratipada")
        self.assertEqual(tm.NSE_HOLIDAYS["2026-12-25"], "Christmas")
        self.assertEqual(tm.CALENDAR_YEARS, (2026,))
        for d in tm.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        self.assertIsNone(tm.closed_reason(datetime.date(2026, 10, 14)))
        self.assertEqual(tm.closed_reason(datetime.date(2026, 11, 24)), "NSE holiday: Prakash Gurpurb Sri Guru Nanak Dev")

    def test_the_config_adds_a_day(self):
        hol = tm.holidays_of({"market_holidays": {"2026-10-14": "Test closure"}})
        self.assertEqual(hol["2026-10-14"], "Test closure")
        self.assertEqual(hol["2026-10-20"], "Dussehra")
        with self.assertRaises(tm.TMError) as cm:
            machine(holidays=hol)
        self.assertIn("(NSE holiday: Test closure)", str(cm.exception))
        self.assertEqual(tm.holidays_of(None), tm.NSE_HOLIDAYS)

    def test_a_day_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:
            machine(plan(date="2026-10-30"))          # a trading Friday after the data ends
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("no bars for that day", str(cm.exception))


class Plans(unittest.TestCase):
    def test_an_entry_not_in_the_data_is_refused(self):
        with self.assertRaises(tm.TMError) as cm:      # the 15:20 bar is empty on 2026-10-29
            machine(plan(date="2026-10-29", entry_time="3:25 PM", entry_bar=None, entry=1165.0, stop=1160.0,
                         target=1175.0))
        self.assertIn("not in the data", str(cm.exception))
        self.assertIn("the data stops at the 15:15 bar", str(cm.exception))

    def test_plan_checks(self):
        cases = [
            (dict(entry_time="9:47 AM", entry_bar=None), "5-minute grid"),
            (dict(entry_time="3:30 PM", entry_bar=None), "between 9:20 AM and 3:25 PM"),
            (dict(entry_time="9:15 AM", entry_bar=None), "between 9:20 AM and 3:25 PM"),
            (dict(entry_bar=9), "disagrees with itself"),
            (dict(entry_time=None, entry_bar=None), "entry_time"),
            (dict(interval="1m"), "7 days of 1-min bars"),
            (dict(stop=1171.0), "stop < entry < target"),
            (dict(side="short"), "buy or sell"),
            (dict(symbol=""), "ticker"),
            (dict(symbol="BAD SYMBOL"), "ticker"),
            (dict(entry="abc"), "number for entry"),
            (dict(date="Oct 14"), "date like"),
            (dict(opening_range={"minutes": 7}), "multiple of 5"),
            (dict(opening_range={"minutes": 375}), "multiple of 5"),
        ]
        for change, words in cases:
            with self.assertRaises(tm.TMError, msg=change) as cm:
                plan(**change)
            self.assertIn(words, str(cm.exception), change)

    def test_nse_symbols(self):
        for raw, want in (("reliance", "RELIANCE"), ("RELIANCE.NS", "RELIANCE"), ("m&m", "M&M"), ("bajaj-auto.ns", "BAJAJ-AUTO")):
            self.assertEqual(plan(symbol=raw)["symbol"], want)

    def test_entry_bar_or_entry_time_alone(self):
        self.assertEqual(plan(entry_time=None)["entry_time"], "9:45 AM")
        self.assertEqual(plan(entry_bar=None)["entry_bar"], 5)
        self.assertEqual(plan(entry_time="09:45", entry_bar=None)["entry_bar"], 5)
        self.assertEqual(plan(entry_time="9:45am", entry_bar=None)["entry_bar"], 5)
        self.assertEqual(plan(entry_time="3:15", entry_bar=None)["entry_bar"], 71)    # a bare 3:15 is p.m.
        self.assertEqual(plan(entry_time="15:25", entry_bar=None)["entry_bar"], 73)   # the last bar that leaves one to replay

    def test_until_cuts_the_replay(self):
        doc = machine(until="15:15", replays=500)
        m = doc["mc"]
        self.assertEqual((m["until"], m["bars_left"], m["band_times"][-1]), ("15:15", 66, "15:15"))
        self.assertIn("still open at 3:15 PM", "\n".join(tm.report(doc)))
        self.assertEqual(machine(until="3:15 PM", replays=500)["mc"], m)
        for bad, words in (("9:45 AM", "nothing to replay"), ("15:35", "no later than 3:30 PM"),
                           ("15:17", "5-minute grid"), ("noon", "IST time")):
            with self.assertRaises(tm.TMError) as cm:
                machine(until=bad, replays=10)
            self.assertIn(words, str(cm.exception), bad)

    def test_an_entry_outside_its_bar_is_flagged(self):
        doc = machine(plan(entry=1180.0, stop=1175.0, target=1190.0), replays=200)
        self.assertIn("outside that bar's range", doc["warnings"][0])
        # one tick of slack: 1170.65 is within 0.05 of the bar's range? the bar is 1167.50 to 1171.40, so yes
        self.assertEqual(machine(plan(entry=1171.45, stop=1166.0, target=1182.35), replays=100)["warnings"], [])

    def test_stops_round_down_to_the_tick_and_entries_to_the_nearest(self):
        self.assertEqual(tm.tick_away(1166.025, True), 1166.0)       # a buy's stop goes down
        self.assertEqual(tm.tick_away(1166.025, False), 1166.05)     # a sell's stop goes up
        self.assertEqual(tm.tick_away(1166.0, True), 1166.0)
        self.assertEqual(tm.tick_away(1162.699951171875, True), 1162.7)      # Yahoo's float noise
        self.assertEqual(tm.tick_away(1162.7000000000003, True), 1162.7)
        self.assertEqual(tm.tick_away(1162.6999999999998, True), 1162.7)
        self.assertEqual(tm.tick_near(1170.699951171875), 1170.7)
        self.assertEqual(tm.tick_near(1170.725), 1170.75)           # halves go up
        self.assertEqual(tm.tick_near(1170.72), 1170.7)
        self.assertEqual(tm.tick_near(1318.6999), 1318.7)

    def test_the_stop_rule_is_worked_out_when_not_given(self):
        rule = machine(plan(stop_rule=None), replays=100)["history"]["rule"]
        self.assertEqual(rule["stop_rule"], "or_mid")
        rule = machine(plan(stop_rule=None, stop=1162.7, target=1186.7), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["target_r"]), ("or_low", 2.0))
        rule = machine(plan(stop_rule="atr", stop=1165.0, target=1182.1), replays=100)["history"]["rule"]
        self.assertEqual((rule["stop_rule"], rule["stop_pct"], rule["target_r"]), ("pct", 0.4869, 2.0))
        self.assertEqual(tm.normalize_plan(dict(PLAN, stop_rule="or_low"))["stop_rule"], "or_low")

    def test_a_setup_other_than_orb_skips_the_history(self):
        doc = machine(plan(setup="level"), replays=200)
        self.assertIsNone(doc["history"]["rule"])
        self.assertIn("orb", doc["history"]["note"])
        self.assertEqual(doc["mc"]["pool_sessions"], 49)
        self.assertIn("History: skipped", "\n".join(tm.report(doc)))

    def test_a_sell_plan_mirrors_the_rules(self):
        doc = machine(plan(side="sell", stop=1175.4, target=1166.0, stop_rule="or_high"), replays=2000)
        m = doc["mc"]
        self.assertAlmostEqual(m["p_target"] + m["p_stop"] + m["p_neither"], 1.0, places=6)
        self.assertEqual(m["r_at_target"], 1.0)
        self.assertEqual(doc["history"]["rule"]["stop_rule"], "or_high")
        for t in doc["history"]["trades"]:
            self.assertTrue(t["target"] < t["entry"] < t["stop"], t)
        self.assertIn("below the entry", "\n".join(tm.report(doc)))

    def test_the_verdict_thresholds_at_1r(self):
        # 1R: the target's share of the replays that hit one side against 50 %
        self.assertTrue(tm.verdict({"p_target": 0.30, "p_stop": 0.26}).startswith("Close to a coin flip"))
        self.assertTrue(tm.verdict({"p_target": 0.40, "p_stop": 0.30}).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict({"p_target": 0.20, "p_stop": 0.30}).startswith("Leans toward the stop"))
        self.assertTrue(tm.verdict({"p_target": 0.60, "p_stop": 0.30}).startswith("Strongly toward the target"))
        self.assertEqual(tm.verdict({"p_target": 0.10, "p_stop": 0.60, "r_at_target": 1.0}),
                         "Strongly toward the stop: target first 10.0 % vs stop first 60.0 %.")
        self.assertEqual(tm.verdict({"p_target": 0.0, "p_stop": 0.0, "until": "15:15"}),
                         "No replay reached the target or the stop by 3:15 PM: the price at 3:15 PM decides every one.")
        self.assertIn("by 3:30 PM", tm.verdict({"p_target": 0.0, "p_stop": 0.0}))      # the default is the NSE close

    def test_a_2r_target_is_judged_against_its_break_even(self):
        # a 2R plan needs the target first in 33 % of the replays that hit one side: it got 32 %
        mc = {"p_target": 0.241, "p_stop": 0.505, "p_neither": 0.254, "r_at_target": 2.0, "avg_R": 0.095}
        self.assertEqual(tm.verdict(mc), "Close to break-even: at 2R the target has to come first in 33 % of the "
                                         "replays that hit one side; it did in 32 % (target first 24.1 % vs stop "
                                         "first 50.5 %).")
        self.assertEqual(tm.verdict_notes(mc), [])
        lean = dict(mc, p_target=0.15, p_stop=0.55)
        self.assertTrue(tm.verdict(lean).startswith("Leans toward the stop: at 2R the target has to come first in "
                                                    "33 % of the replays that hit one side; it did in 21 %"))
        self.assertEqual(tm.verdict_notes(lean), ["Average R is +0.095 all the same: the replays that hit neither "
                                                  "pull it the other way."])
        self.assertTrue(tm.verdict(dict(mc, p_target=0.30, p_stop=0.40)).startswith("Leans toward the target"))
        self.assertTrue(tm.verdict(dict(mc, p_target=0.40, p_stop=0.40)).startswith("Strongly toward the target"))
        self.assertIn("at 1.5R the target has to come first in 40 %", tm.verdict(dict(mc, r_at_target=1.5)))

    def test_2r_on_the_data_is_weighed_against_a_third(self):
        # the exit plan's 2R target: 23.0 % vs 65.1 % looks lopsided, and against the 33 % a 2R plan needs it is
        self.assertEqual(self.verdict_of(machine()), "Leans toward the stop")
        # the same entry and stop with the 1R target: 44.2 % vs 54.4 % against 50 %
        self.assertEqual(self.verdict_of(machine(plan(target=ONE_R_TARGET))), "Leans toward the stop")

    @staticmethod
    def verdict_of(doc):
        return doc["verdict"].split(":")[0]


class Network(unittest.TestCase):
    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = tm._get, tm._sleep
        tm._sleep = self.sleeps.append

    def tearDown(self):
        tm._get, tm._sleep = self._get, self._sleep

    def answer(self, codes):
        codes = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url.split(".finance")[0].split("//")[1])
            code = codes.pop(0)
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return TEXT
        tm._get = fake

    def test_url_and_user_agent(self):
        self.assertEqual(tm.chart_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=60d&interval=5m")
        self.assertEqual(tm.chart_url("RELIANCE.NS"), tm.chart_url("reliance"))
        self.assertIn("/chart/M%26M.NS?", tm.chart_url("m&m", "query1"))
        self.assertIn("/chart/BAJAJ-AUTO.NS?", tm.chart_url("bajaj-auto"))
        self.assertTrue(tm.UA.startswith("Mozilla/5.0"))

    def test_429_waits_retries_then_falls_back_to_query1(self):
        self.answer([429, 429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2", "query1"])
        self.assertEqual(self.sleeps, [tm.BACKOFF, tm.PAUSE])

    def test_one_429_then_ok(self):
        self.answer([429, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query2"])

    def test_5xx_falls_back(self):
        self.answer([503, 200])
        self.assertEqual(tm.fetch_chart("RELIANCE"), TEXT)
        self.assertEqual(self.calls, ["query2", "query1"])

    def test_rate_limited_everywhere_is_one_plain_line(self):
        self.answer([429, 429, 429, 429])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("RELIANCE")
        self.assertIn("rate limiting", str(cm.exception))

    def test_404(self):
        self.answer([404])
        with self.assertRaises(tm.TMError) as cm:
            tm.fetch_chart("ZZZZ")
        self.assertIn("check the ticker", str(cm.exception))

    def test_run_over_the_network_path(self):
        self.answer([200])
        d = tempfile.mkdtemp()
        try:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(PLAN, fh)
            code, text, _ = run(["run", "--plan", p, "--now", NOW, "--replays", "300"])
            self.assertEqual(code, 0)
            self.assertEqual(self.calls, ["query2"])
        finally:
            shutil.rmtree(d)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.plan = os.path.join(self.d, "trader", "plan.json")
        os.makedirs(os.path.dirname(self.plan))
        shutil.copy(PLAN_FILE, self.plan)
        self.out = os.path.join(self.d, "trader", "timemachine.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def test_run_with_the_plan_end_to_end(self):
        code, text, err = run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--write", self.out])
        self.assertEqual(code, 0, err)
        self.assertIn("target first   23.0 %", text)
        self.assertIn("Leans toward the stop: at 2R the target has to come first in 33 %", text)
        self.assertIn("Wrote %s." % self.out, text)
        self.assertIn("/trader-budget", text)               # the hand-off
        with open(self.out) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(k for k in ("plan", "mc", "history") if k in doc), ["history", "mc", "plan"])
        self.assertEqual(doc["created_by"], "trader-timemachine")
        self.assertEqual(doc["source"], "offline")
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["avg_R"]), (0.2296, -0.131))
        self.assertEqual(sha(doc["mc"]["sample_paths"]), SIM_PATHS)
        self.assertEqual((doc["history"]["triggered"], doc["history"]["avg_R"]), (23, 0.114))
        self.assertEqual(doc["plan"]["from"], self.plan)
        # the plan block keeps what trader-budget reads: entry, stop, target, risk a share, flat by
        p = doc["plan"]
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"], p["risk_per_share"], p["flat_by"]),
                         ("RELIANCE", 1170.7, 1166.0, 1180.1, 4.7, "15:15"))
        self.assertEqual(doc["data"]["interval"], "5m")
        self.assertEqual(doc["data"]["range"], "60d")

    def test_json_output(self):
        code, text, _ = run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--json"])
        self.assertEqual(code, 0)
        doc = json.loads(text)
        self.assertEqual(doc["mc"]["p_stop"], 0.6512)
        self.assertNotIn("sample_paths", doc["mc"])
        self.assertEqual(doc["verdict"][:23], "Leans toward the stop: ")
        self.assertFalse(os.path.exists(self.out))

    def test_the_plan_as_flags_matches_the_file(self):
        code, text, err = run(["run", "--symbol", "reliance", "--date", "2026-10-14", "--entry", "1170.7",
                               "--stop", "1166", "--target", "1180.1", "--entry-time", "9:45 AM",
                               "--offline", RELIANCE, "--now", NOW, "--json"])
        self.assertEqual(code, 0, err)
        doc = json.loads(text)
        self.assertEqual((doc["mc"]["p_target"], doc["mc"]["p_green"]), (0.2296, 0.3175))
        self.assertEqual(doc["plan"]["stop_rule"], "or_mid")
        self.assertEqual((doc["history"]["target"], doc["history"]["stop"], doc["history"]["avg_R"]), (6, 11, 0.114))
        code, _, err = run(["run", "--symbol", "RELIANCE", "--entry", "1170.7", "--offline", RELIANCE])
        self.assertEqual(code, 1)
        self.assertIn("--stop", err)

    def test_offline_folder_and_default_paths(self):
        shutil.copy(RELIANCE, self.d)
        cwd = os.getcwd()
        os.chdir(self.d)
        try:
            code, text, err = run(["run", "--offline", ".", "--now", NOW, "--replays", "500", "--write"])
            self.assertEqual(code, 0, err)
            self.assertTrue(os.path.exists(os.path.join("trader", "timemachine.json")))
            code, text, err = run(["chart", "--from"])
            self.assertEqual(code, 0, err)
            self.assertIn("* median", text)
            with open(os.path.join("trader", "timemachine.json")) as fh:
                self.assertEqual(json.load(fh)["mc"]["replays"], 500)
        finally:
            os.chdir(cwd)

    def test_the_config_holiday_reaches_the_run(self):
        cfg = os.path.join(self.d, "trader", "config.json")
        with open(cfg, "w") as fh:
            json.dump({"market_holidays": {"2026-10-14": "Test closure"}}, fh)
        code, _, err = run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Test closure", err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        with open(cfg, "w") as fh:
            fh.write("{not json")
        code, _, err = run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("not valid JSON", err)

    def test_closed_day_is_one_plain_line(self):
        shutil.copy(PLAN_FILE, self.plan)
        with open(self.plan) as fh:
            doc = json.load(fh)
        doc["date"] = "2026-10-20"
        with open(self.plan, "w") as fh:
            json.dump(doc, fh)
        code, _, err = run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("timemachine.py: NSE is closed on 2026-10-20 (NSE holiday: Dussehra)"), err)

    def test_errors_are_one_plain_line(self):
        for argv in (["run", "--plan", os.path.join(self.d, "nope.json")],
                     ["run", "--plan", self.plan, "--offline", os.path.join(self.d, "missing.json.gz")],
                     ["run", "--plan", self.plan, "--offline", RELIANCE, "--until", "9:00"],
                     ["chart", "--from", os.path.join(self.d, "nope.json")]):
            code, text, err = run(argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
            self.assertTrue(err.startswith("timemachine.py: "), err)
            self.assertNotIn("Traceback", err)
        self.assertEqual(run([])[0], 1)

    def test_chart(self):
        run(["run", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW, "--write", self.out])
        code, text, _ = run(["chart", "--from", self.out])
        self.assertEqual(code, 0)
        lines = text.splitlines()
        self.assertIn("2,000 replay paths from the 9:45 AM entry (seed 19)", lines[0])
        self.assertTrue(any(l.startswith("  1180.10 |") and l.endswith("  target") for l in lines))
        self.assertTrue(any(l.startswith("  1166.00 |") and l.endswith("  stop") for l in lines))
        self.assertTrue(any(l.startswith("  1170.70 |*") and l.endswith("  entry") for l in lines))
        self.assertIn("9:45", text)
        self.assertIn("3:30", text)
        self.assertEqual(lines[-1], self.expected_verdict_line())
        code, again, _ = run(["chart", "--plan", self.plan, "--offline", RELIANCE, "--now", NOW])
        self.assertEqual(again, text)

    @staticmethod
    def expected_verdict_line():
        return ("Leans toward the stop: at 2R the target has to come first in 33 % of the replays that hit one side; "
                "it did in 26 % (target first 23.0 % vs stop first 65.1 %).")


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        root = os.path.dirname(DIR.rstrip(os.sep))
        root = os.path.dirname(os.path.dirname(root))
        with open(os.path.join(root, "trader-timemachine", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-timemachine\n"))
        low = " ".join(text.lower().split())
        self.assertNotIn("$", text.replace("${CLAUDE_SKILL_DIR}", ""))
        for must in ("75 bars", "09:15 to 15:30", "symbol.ns", "\u20b91,170.70", "\u20b91,180.10", "23.0 %", "65.1 %", "-0.131",
                     "+0.114", "seed 19", "0.05", "nseindia.com", "/trader-budget", "not a prediction", "15:15",
                     "market_holidays", "taken as unchanged", "49 full sessions"):
            self.assertIn(must, low)
        for never in ("\u2014", "nyse", "new york", "tsla", "1:00 p.m.", "78 "):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
