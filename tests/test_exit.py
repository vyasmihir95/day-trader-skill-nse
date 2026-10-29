"""trader-exit tests, on a real NSE session: RELIANCE, Wednesday 2026-10-14. The opening range
(9:15 to 9:30 IST) is 1162.70 to 1169.30; the 9:40 bar closes at 1170.70, the first close over the
range high, so trader-entry would call the entry at 9:45 AM (bar 5). The entry file and the plan it
produces are in tests/fixtures/trader-exit/; bars for the ATR stop are RELIANCE's 60 days of
5-minute data in tests/fixtures/trader-timemachine/ (see test_timemachine.py)."""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from decimal import Decimal

from _load import load, FIX, ROOT

exitp = load("trader-exit", "exit")

ENTRY_FILE = os.path.join(FIX, "trader-exit", "entry-RELIANCE-2026-10-14.json")
PLAN_FILE = os.path.join(FIX, "trader-exit", "plan-RELIANCE-2026-10-14.json")
BARS = os.path.join(FIX, "trader-timemachine", "RELIANCE-60d5m.json.gz")
with open(ENTRY_FILE, encoding="utf-8") as _fh:
    ENTRY = json.load(_fh)
with open(PLAN_FILE, encoding="utf-8") as _fh:
    FIXTURE_PLAN = json.load(_fh)

# trader/plan.json for the breakout with the default stop (or-mid) and the default 2R target
PLAN = {"created_by": "trader-exit", "symbol": "RELIANCE", "side": "buy", "date": "2026-10-14", "setup": "orb",
        "entry": 1170.7, "entry_time": "9:45 AM", "entry_bar": 5, "interval": "5m",
        "opening_range": {"high": 1169.3, "low": 1162.7, "minutes": 15},
        "stop": 1166.0, "stop_rule": "or_mid", "stop_label": "opening-range midpoint",
        "target": 1180.1, "target_r": 2.0, "target_label": "2R (two times the risk)",
        "risk_per_share": 4.7, "reward_risk": 2.0, "flat_by": "15:15"}
ONE_R = dict(PLAN, target=1175.4, target_r=1.0, target_label="1R (one times the risk)", reward_risk=1.0)


def run(mod, argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = mod.main(argv)
    return code, out.getvalue(), err.getvalue()


def c(entry_px, stop, target="2R", oh=1169.3, ol=1162.7, atr=None, n=None):
    sr, sp = exitp.parse_stop(stop)
    tr, tp = exitp.parse_target(target)
    return exitp.compute(entry_px, sr, sp, tr, tp, oh, ol, atr, n)


def on_tick(x):
    return Decimal(str(x)) % Decimal("0.05") == 0


class Fixture(unittest.TestCase):
    def test_the_fixture_is_the_real_session(self):
        # the plan file is what exit.py writes for the entry file; both match the facts above
        self.assertEqual(FIXTURE_PLAN, PLAN | {k: FIXTURE_PLAN[k] for k in ("stop_detail", "created_at", "note")})
        self.assertEqual((ENTRY["symbol"], ENTRY["status"], ENTRY["time"], ENTRY["bar_index"], ENTRY["price"]),
                         ("RELIANCE", "triggered", "9:45 AM", 5, 1170.7))
        self.assertEqual(FIXTURE_PLAN["created_at"], "2026-10-14T04:16:00Z")       # 09:46 IST


class Math(unittest.TestCase):
    def test_the_default_or_mid_2r(self):
        p = c(1170.7, "or-mid")
        self.assertEqual(p["stop"], Decimal("1166.00"))              # (1169.30 + 1162.70) / 2 = 1166.00, on the tick
        self.assertEqual(p["risk"], Decimal("4.70"))
        self.assertEqual(p["target"], Decimal("1180.10"))
        self.assertEqual(p["reward_risk"], Decimal("2.00"))
        self.assertEqual(p["target_label"], "2R (two times the risk)")
        self.assertEqual(p["stop_label"], "opening-range midpoint")
        self.assertEqual(p["stop_detail"], "(₹1,169.30 + ₹1,162.70) / 2 = ₹1,166.00")
        self.assertNotIn("rounded down", p["stop_detail"])

    def test_1r(self):
        p = c(1170.7, "or-mid", "1R")
        self.assertEqual((p["stop"], p["risk"], p["target"], p["reward_risk"]),
                         (Decimal("1166.00"), Decimal("4.70"), Decimal("1175.40"), Decimal("1.00")))
        self.assertEqual(p["target_label"], "1R (one times the risk)")

    def test_the_stop_rounds_down_to_the_tick(self):
        # a midpoint between two ticks: (1169.35 + 1162.70) / 2 = 1166.025 goes DOWN to 1166.00, not to the cent
        p = c(1170.7, "or-mid", oh=1169.35)
        self.assertEqual(p["stop"], Decimal("1166.00"))
        self.assertIn("= ₹1,166.025, rounded down", p["stop_detail"])
        self.assertEqual(p["risk"], Decimal("4.70"))
        # 1166.075 would be 1166.05
        self.assertEqual(c(1170.7, "or-mid", oh=1169.45)["stop"], Decimal("1166.05"))
        # your own price rounds away from the entry too: 1165.97 -> 1165.95, 1165.99 -> 1165.95, 1166.00 stays
        self.assertEqual(c(1170.7, "price:1165.97")["stop"], Decimal("1165.95"))
        self.assertEqual(c(1170.7, "price:1165.99")["stop"], Decimal("1165.95"))
        self.assertEqual(c(1170.7, "price:1166")["stop"], Decimal("1166.00"))
        self.assertEqual(exitp.floor_ticks("1166.0499"), Decimal("1166.00"))
        self.assertEqual(exitp.floor_ticks(1166.05), Decimal("1166.05"))

    def test_entry_and_target_round_to_the_tick(self):
        self.assertEqual(exitp.ticks(1170.72), Decimal("1170.70"))
        self.assertEqual(exitp.ticks(1170.725), Decimal("1170.75"))     # half up
        self.assertEqual(exitp.ticks(1170.74), Decimal("1170.75"))
        p = c(1170.72, "price:1166")
        self.assertEqual(p["entry"], Decimal("1170.70"))
        # 1000 with a 9.95 stop and a 1.5R target: 1000 + 14.925 = 1014.925 -> half up to 1014.95
        p = c(1000, "price:990.05", "1.5R", None, None)
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("990.05"), Decimal("9.95"), Decimal("1014.95")))
        self.assertEqual(p["target_label"], "1.5R (1.5 times the risk)")
        self.assertTrue(all(on_tick(x) for x in (p["entry"], p["stop"], p["target"], p["risk"])))

    def test_raw_yahoo_floats_give_the_same_ticks(self):
        p = c(1170.699951171875, "or-mid", "1R", 1169.300048828125, 1162.699951171875)
        self.assertEqual((p["entry"], p["stop"], p["target"]), (Decimal("1170.70"), Decimal("1166.00"), Decimal("1175.40")))

    def test_other_stops(self):
        p = c(1170.7, "or-low")
        self.assertEqual((p["stop"], p["risk"], p["target"]), (Decimal("1162.70"), Decimal("8.00"), Decimal("1186.70")))
        p = c(1170.7, "pct:1")
        self.assertEqual(p["stop"], Decimal("1158.95"))                 # 1170.7 x 0.99 = 1158.993
        self.assertEqual((p["risk"], p["target"]), (Decimal("11.75"), Decimal("1194.20")))
        self.assertEqual(p["stop_label"], "1% under the entry")
        self.assertIn("rounded down", p["stop_detail"])
        p = c(1170.7, "atr:1.5", atr=3.7667, n=6)
        self.assertEqual(p["stop"], Decimal("1165.00"))                 # 1170.70 - 5.65005 = 1165.04995
        self.assertEqual(p["stop_label"], "1.5 x ATR(6) under the entry")
        self.assertIn("ATR ₹3.7667 over the 6 bars to the entry (fewer than 14 so far)", p["stop_detail"])
        p = c(1170.7, "atr:1", atr=3.7667, n=14)
        self.assertNotIn("fewer than 14", p["stop_detail"])

    def test_every_number_sits_on_the_tick(self):
        for stop in ("or-mid", "or-low", "pct:0.7", "pct:1.3", "price:1161.17", "atr:1.5", "atr:2.2"):
            p = c(1170.7, stop, "1.7R", atr=3.7667, n=14)
            for k in ("entry", "stop", "target", "risk"):
                self.assertTrue(on_tick(p[k]), (stop, k, p[k]))

    def test_price_targets(self):
        p = c(1170.7, "or-mid", "price:1178")
        self.assertEqual(p["target"], Decimal("1178.00"))
        self.assertEqual(p["reward_risk"], Decimal("1.55"))              # 7.30 / 4.70
        self.assertEqual(p["target_label"], "your price (1.55R)")
        self.assertEqual(p["target_r"], Decimal("1.55"))
        self.assertEqual(c(1170.7, "or-mid", "price:1178.03")["target"], Decimal("1178.05"))

    def test_refusals_say_why(self):
        cases = [("price:1170.7", "2R", "at or above the entry"),
                 ("price:1171", "2R", "at or above the entry"),
                 ("or-mid", "price:1170", "at or below the entry"),
                 ("or-mid", "price:1170.7", "at or below the entry"),
                 ("price:1170.15", "2R", "too tight to survive the spread"),
                 ("pct:0.04", "2R", "too tight to survive the spread")]
        for stop, target, why in cases:
            with self.assertRaises(exitp.PlanError) as cm:
                c(1170.7, stop, target)
            self.assertIn(why, str(cm.exception), stop)
        # 0.05% of 1170.70 is 0.585: a 0.60 risk is allowed, 0.55 is not
        self.assertEqual(c(1170.7, "price:1170.10")["risk"], Decimal("0.60"))
        with self.assertRaises(exitp.PlanError) as cm:
            c(1170.7, "price:1170.15")
        self.assertIn("less than 0.05% of the price (₹0.59 on ₹1,170.70)", str(cm.exception))
        with self.assertRaises(exitp.PlanError):
            c(1160.00, "or-mid")                                         # the midpoint is above this entry
        for bad in ("mid-ish", "pct:100", "price:0", "atr:-1"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_stop(bad)
        for bad in ("0R", "R", "lots", "price:x"):
            with self.assertRaises(exitp.PlanError):
                exitp.parse_target(bad)
        with self.assertRaises(exitp.PlanError):
            c(1170.7, "or-mid", oh=None, ol=None)
        with self.assertRaises(exitp.PlanError):
            c(1170.7, "or-mid", oh=1162.7, ol=1169.3)
        with self.assertRaises(exitp.PlanError) as cm:
            c(0, "pct:1")
        self.assertIn("above ₹0", str(cm.exception))
        with self.assertRaises(exitp.PlanError) as cm:
            c(0.3, "price:0.04")                                           # below one tick: the stop floors to zero
        self.assertIn("pick a stop above ₹0", str(cm.exception))

    def test_prices_are_rupees(self):
        self.assertEqual(exitp.money(1170.7), "₹1,170.70")
        self.assertEqual(exitp.money("1166"), "₹1,166.00")
        self.assertEqual(exitp.D("₹1,170.70"), Decimal("1170.70"))
        self.assertEqual(exitp.parse_stop("price:₹1,165.95".replace(",", "")), ("price", Decimal("1165.95")))
        self.assertEqual(exitp.raw_money(Decimal("1166.025")), "₹1,166.025")
        self.assertEqual(exitp.raw_money(Decimal("1166")), "₹1,166.00")
        self.assertEqual(exitp.TICK, Decimal("0.05"))

    def test_atr_on_the_session_bars(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, BARS)
        day = datetime.date(2026, 10, 14)
        bars = exitp.session_bars(ch, day, "5m", upto_slot=5)
        self.assertEqual(len(bars), 6)                                   # 9:15 ... the 9:40 entry bar
        self.assertEqual([exitp.ts_to_local(b["t"]).strftime("%H:%M") for b in bars],
                         ["09:15", "09:20", "09:25", "09:30", "09:35", "09:40"])
        # by hand: the true range of each bar, the first one without the overnight gap
        trs, prev = [], None
        for b in bars:
            r = b["h"] - b["l"]
            if prev is not None:
                r = max(r, abs(b["h"] - prev), abs(b["l"] - prev))
            trs.append(r)
            prev = b["c"]
        self.assertAlmostEqual(trs[0], 1169.3 - 1163.7, places=3)        # the 9:15 bar's own high - low
        atr, n = exitp.atr_of(bars)
        self.assertEqual(n, 6)
        self.assertAlmostEqual(atr, sum(trs) / 6, places=9)
        self.assertAlmostEqual(atr, 3.7667, places=4)
        # later in the day it uses the last 14 bars; Yahoo has no 15:20 bar, so 74 closed bars
        all_bars = exitp.session_bars(ch, day, "5m")
        self.assertEqual(len(all_bars), 74)
        self.assertEqual(exitp.atr_of(all_bars)[1], 14)

    def test_bars_still_forming_never_count(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, BARS)
        day = datetime.date(2026, 10, 14)
        now = exitp.parse_now("2026-10-14 09:42")                       # the 9:40 bar closes at 9:45
        bars = exitp.session_bars(ch, day, "5m", now_utc=now)
        self.assertEqual(len(bars), 5)
        self.assertEqual(exitp.ts_to_local(bars[-1]["t"]).strftime("%H:%M"), "09:35")
        # the chart's own clock (15:29:03 IST on 2026-10-29) holds that day's 15:25 bar back
        last = exitp.session_bars(ch, datetime.date(2026, 10, 29), "5m")
        self.assertEqual(exitp.ts_to_local(last[-1]["t"]).strftime("%H:%M"), "15:15")

    def test_closed_days_give_no_bars(self):
        ch = exitp.load_chart("RELIANCE", "5m", None, BARS)
        self.assertEqual(exitp.session_bars(ch, datetime.date(2026, 10, 20), "5m"), [])    # Dussehra
        self.assertEqual(exitp.session_bars(ch, datetime.date(2026, 10, 17), "5m"), [])    # a Saturday


class Calendar(unittest.TestCase):
    def test_session_and_holidays(self):
        self.assertEqual(exitp.session(datetime.date(2026, 10, 14)), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(exitp.session(datetime.date(2026, 10, 20)))
        self.assertIsNone(exitp.session(datetime.date(2026, 10, 17)))
        self.assertEqual(exitp.closed_reason(datetime.date(2026, 10, 20)), "NSE holiday: Dussehra")
        self.assertEqual(exitp.closed_reason(datetime.date(2026, 10, 18)), "weekend")
        self.assertIsNone(exitp.closed_reason(datetime.date(2026, 11, 27)))          # no early closes on NSE
        self.assertEqual(len(exitp.NSE_HOLIDAYS), 15)
        self.assertEqual(exitp.CALENDAR_YEARS, (2026,))
        for d in exitp.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)
        hol = exitp.holidays_of({"market_holidays": {"2026-11-09": "Diwali muhurat"}})
        self.assertEqual(exitp.closed_reason(datetime.date(2026, 11, 9), hol), "NSE holiday: Diwali muhurat")

    def test_ist_clock(self):
        utc = datetime.datetime(2026, 10, 14, 3, 45)
        self.assertEqual(exitp.to_local(utc), datetime.datetime(2026, 10, 14, 9, 15))
        self.assertEqual(exitp.local_to_utc(datetime.datetime(2026, 10, 14, 9, 15)), utc)
        for t in ("2026-01-15 12:00", "2026-07-15 12:00"):                # no daylight saving: the same offset all year
            self.assertEqual(exitp.to_local(exitp.parse_now(t)) - exitp.parse_now(t), datetime.timedelta(hours=5, minutes=30))
        self.assertEqual(exitp.parse_now("2026-10-14 09:45 IST"), datetime.datetime(2026, 10, 14, 4, 15))
        self.assertEqual(exitp.parse_now("2026-10-14T04:15Z"), datetime.datetime(2026, 10, 14, 4, 15))
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.parse_now("tomorrow")
        self.assertIn("IST", str(cm.exception))
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.parse_hhmm("noon", "--flat-by")
        self.assertIn("IST time like 15:15", str(cm.exception))

    def test_symbols(self):
        self.assertEqual(exitp.bare("reliance.ns"), "RELIANCE")
        self.assertEqual(exitp.bare(" m&m "), "M&M")
        self.assertEqual(exitp.yahoo_symbol("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(exitp.yahoo_symbol("m&m"), "M%26M.NS")
        self.assertEqual(exitp.yahoo_symbol("BAJAJ-AUTO.NS"), "BAJAJ-AUTO.NS")


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)
        os.mkdir("trader")

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)

    def write(self, path, doc):
        with open(path, "w") as fh:
            json.dump(doc, fh)

    def entry(self, **kw):
        self.write(os.path.join("trader", "entry.json"), dict(ENTRY, **kw))

    def plan(self, *args):
        return run(exitp, ["plan", "--now", "2026-10-14 09:46"] + list(args))

    def read_plan(self):
        with open(os.path.join("trader", "plan.json")) as fh:
            return json.load(fh)

    def test_entry_to_plan_end_to_end(self):
        shutil.copy(ENTRY_FILE, os.path.join("trader", "entry.json"))
        code, text, err = self.plan("--write")                          # the defaults: or-mid, 2R
        self.assertEqual(code, 0, err)
        self.assertEqual(self.read_plan(), FIXTURE_PLAN)
        p = self.read_plan()
        for k, v in PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Exit plan: RELIANCE long, Wed 2026-10-14, opening-range breakout on 5-minute bars", text)
        self.assertIn("Entry         ₹1,170.70 at 9:45 AM (bar 5)", text)
        self.assertIn("Stop          ₹1,166.00  opening-range midpoint: (₹1,169.30 + ₹1,162.70) / 2 = ₹1,166.00", text)
        self.assertIn("Target        ₹1,180.10  2R (two times the risk)", text)
        self.assertIn("Risk a share  ₹4.70 (0.40% of the entry)", text)
        self.assertIn("Reward:risk   2.0", text)
        self.assertIn("Flat by       15:15 IST", text)
        self.assertIn("not a guarantee", text)
        self.assertNotIn("$", text)
        self.assertNotIn("pro" + "fit", text.lower())   # never on screen
        # the hand-off: the next skills, and nothing placed
        self.assertIn("/trader-timemachine", text)
        self.assertIn("/trader-budget", text)
        self.assertIn("Nothing was placed", text)
        self.assertIn("Wrote trader/plan.json.", text)
        code, text, _ = self.plan("--stop", "or-mid", "--target", "1R", "--write")
        p = self.read_plan()
        for k, v in ONE_R.items():
            self.assertEqual(p[k], v, k)
        self.assertIn("Target        ₹1,175.40  1R (one times the risk)", text)
        self.assertIn("Reward:risk   1.0", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-14 10:20"])
        self.assertEqual(code, 0)
        self.assertIn("₹1,175.40", text)
        self.assertNotIn("not today", text)
        code, text, _ = run(exitp, ["show", "--now", "2026-10-15 10:20"])
        self.assertIn("not today", text)
        code, text, _ = run(exitp, ["show", "--json"])
        self.assertEqual(json.loads(text)["stop"], 1166.0)

    def test_the_plan_json_shape(self):
        # what trader-timemachine, trader-budget and trader-alerts read
        self.assertEqual(list(FIXTURE_PLAN), ["created_by", "symbol", "side", "date", "setup", "entry", "entry_time",
                                              "entry_bar", "interval", "opening_range", "stop", "stop_rule",
                                              "stop_label", "target", "target_r", "target_label", "risk_per_share",
                                              "reward_risk", "flat_by", "stop_detail", "created_at", "note"])
        self.assertEqual(FIXTURE_PLAN["symbol"], "RELIANCE")                # bare, never RELIANCE.NS
        for k in ("entry", "stop", "target", "risk_per_share"):
            self.assertIsInstance(FIXTURE_PLAN[k], float)
            self.assertTrue(on_tick(FIXTURE_PLAN[k]), k)
        self.assertEqual(round(FIXTURE_PLAN["entry"] - FIXTURE_PLAN["stop"], 2), FIXTURE_PLAN["risk_per_share"])

    def test_manual_flags_and_json(self):
        code, text, _ = self.plan("--symbol", "reliance.ns", "--entry", "1170.7", "--or-high", "1169.3", "--or-low",
                                  "1162.7", "--time", "9:45", "--entry-bar", "5", "--date", "2026-10-14", "--json")
        self.assertEqual(code, 0)
        p = json.loads(text)
        for k, v in PLAN.items():
            self.assertEqual(p[k], v, k)
        self.assertEqual(p["warnings"], [])
        code, text, _ = self.plan("--symbol", "m&m", "--entry", "3150.55", "--setup", "level", "--stop", "pct:1",
                                  "--date", "2026-10-14", "--json")
        p = json.loads(text)
        self.assertEqual((p["symbol"], p["entry"], p["stop"], p["target"]), ("M&M", 3150.55, 3119.0, 3213.65))

    def test_atr_stop_reads_bars(self):
        self.entry()
        code, text, err = self.plan("--stop", "atr:1.5", "--offline", BARS, "--json")
        self.assertEqual(code, 0, err)
        p = json.loads(text)
        self.assertEqual((p["stop"], p["risk_per_share"], p["target"]), (1165.0, 5.7, 1182.1))
        self.assertEqual((p["stop_rule"], p["atr_bars"]), ("atr", 6))
        self.assertAlmostEqual(p["atr"], 3.7667, places=4)
        self.assertEqual(p["stop_label"], "1.5 x ATR(6) under the entry")
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", BARS, "--entry-bar", "1")
        self.assertEqual(code, 1)
        self.assertIn("at least 3 closed", err)
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", os.path.join(self.d, "nope.json"))
        self.assertEqual(code, 1)
        self.assertIn("no offline file", err)
        self.entry(symbol="INFY")
        code, _, err = self.plan("--stop", "atr:1.5", "--offline", BARS)
        self.assertEqual(code, 1)
        self.assertIn("the chart file holds RELIANCE.NS, not INFY", err)

    def test_refusals_from_the_cli(self):
        self.entry(status="waiting", time=None, price=None, bar_index=None, bar_start=None)
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("RELIANCE is waiting, not triggered", err)
        code, text, _ = self.plan("--entry", "1169.35", "--json")       # plan ahead at the level
        self.assertEqual(code, 0)
        p = json.loads(text)
        self.assertEqual((p["entry"], p["stop"], p["entry_time"]), (1169.35, 1166.0, None))

        self.entry(setup="level", level=1169.0)
        code, _, err = self.plan()
        self.assertIn("level setup has no opening-range stop", err)
        self.assertEqual(self.plan("--stop", "pct:1")[0], 0)

        code, _, err = self.plan("--side", "sell")
        self.assertIn("plans are longs only", err)
        self.entry(side="sell")
        code, _, err = self.plan()
        self.assertIn("longs only", err)
        code, _, err = self.plan("--stop", "price:1170.10")
        self.assertIn("longs only", err)
        os.remove(os.path.join("trader", "entry.json"))
        code, _, err = self.plan()
        self.assertIn("no entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE")
        self.assertIn("needs --entry", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--from", "x.json", "--entry", "1")
        self.assertIn("not both", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1170.7", "--or-high", "1169.3")
        self.assertIn("both --or-high and --or-low", err)
        code, _, err = self.plan("--symbol", "RELIANCE", "--entry", "1170.7", "--stop", "price:1170.7")
        self.assertIn("at or above the entry", err)
        self.assertNotIn("Traceback", err)

    def test_flat_by(self):
        self.entry()
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:10", "min_reward_risk": 1.5})
        code, text, _ = self.plan("--target", "1R", "--json")
        p = json.loads(text)
        self.assertEqual(p["flat_by"], "15:10")
        self.assertIn("under your min_reward_risk 1.5", p["warnings"][0])
        os.remove(os.path.join("trader", "config.json"))
        self.assertEqual(json.loads(self.plan("--json")[1])["flat_by"], "15:15")            # the default
        self.assertEqual(json.loads(self.plan("--flat-by", "15:00", "--json")[1])["flat_by"], "15:00")

        # NSE has no early closes: a Friday late in November is a normal session with the same flat-by
        self.entry(date="2026-11-27")
        code, text, _ = run(exitp, ["plan", "--now", "2026-11-27 09:46"])
        self.assertEqual(code, 0)
        self.assertIn("Flat by       15:15 IST", text)
        self.assertNotIn("early close", text)
        self.assertNotIn("Note:", text)

        self.entry()
        for flag, words in (("15:30", "is at or after the 3:30 PM close"), ("16:30", "is at or after the 3:30 PM close"),
                            ("09:15", "at or before the 9:15 AM open"), ("noon", "IST time like 15:15")):
            code, _, err = self.plan("--flat-by", flag)
            self.assertEqual(code, 1, flag)
            self.assertIn(words, err, flag)
        self.write(os.path.join("trader", "config.json"), {"flat_by": "15:45"})
        code, _, err = self.plan()
        self.assertIn("flat_by 15:45 in the config is at or after the 3:30 PM close", err)
        os.remove(os.path.join("trader", "config.json"))
        self.entry(time="3:20 PM")
        code, _, err = self.plan()
        self.assertEqual(code, 1)
        self.assertIn("the entry at 3:20 PM is at or after your flat-by 15:15: there is no time left to hold it", err)

    def test_closed_days_and_the_calendar(self):
        self.entry(date="2026-10-20")
        code, text, _ = run(exitp, ["plan", "--now", "2026-10-20 09:46"])
        self.assertEqual(code, 0)
        self.assertIn("Note: NSE is closed on 2026-10-20 (NSE holiday: Dussehra): there is no session to trade.", text)
        self.entry(date="2026-10-17")
        code, text, _ = run(exitp, ["plan", "--now", "2026-10-17 09:46"])
        self.assertIn("NSE is closed on 2026-10-17 (weekend)", text)
        self.entry()
        self.write(os.path.join("trader", "config.json"), {"market_holidays": {"2026-10-14": "Test closure"}})
        p = json.loads(self.plan("--json")[1])
        self.assertIn("(NSE holiday: Test closure)", p["warnings"][0])
        os.remove(os.path.join("trader", "config.json"))
        code, text, _ = run(exitp, ["plan", "--now", "2027-01-05 09:46", "--date", "2027-01-05"])
        self.assertIn("The holiday list here covers 2026 only: check nseindia.com that NSE trades on 2027-01-05.", text)
        # an open day gives no calendar note, a day other than today a plain one
        self.assertEqual(json.loads(self.plan("--json")[1])["warnings"], [])
        code, text, _ = run(exitp, ["plan", "--now", "2026-10-15 09:46"])
        self.assertIn("This plan is for 2026-10-14, not today (2026-10-15).", text)

    def test_show_without_a_plan(self):
        code, text, _ = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("No plan", text)

    def test_show_a_broken_plan(self):
        self.write(os.path.join("trader", "plan.json"), {"symbol": "RELIANCE"})
        code, _, err = run(exitp, ["show"])
        self.assertEqual(code, 1)
        self.assertIn("is missing", err)

    def test_no_command_prints_help(self):
        self.assertEqual(run(exitp, [])[0], 1)


class Network(unittest.TestCase):
    """ATR bars from Yahoo: the SYMBOL.NS chart URL and the range for the plan's date."""

    def setUp(self):
        self.urls = []
        self._open = exitp._open_url
        with open(BARS, "rb") as fh:
            import gzip
            self.text = gzip.decompress(fh.read()).decode("utf-8")

        def fake(url, timeout=20):
            self.urls.append(url)
            return self.text
        exitp._open_url = fake

    def tearDown(self):
        exitp._open_url = self._open

    def test_urls(self):
        today = exitp.to_local(exitp.utc_now()).date()
        exitp.load_chart("RELIANCE", "5m", today)
        exitp.load_chart("reliance.ns", "5m", today - datetime.timedelta(days=3))
        exitp.load_chart("RELIANCE", "5m", today - datetime.timedelta(days=20))
        exitp.load_chart("RELIANCE", "5m", today - datetime.timedelta(days=40))
        base = "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=%s&interval=5m"
        self.assertEqual(self.urls, [base % r for r in ("1d", "5d", "1mo", "60d")])
        with self.assertRaises(exitp.PlanError) as cm:
            exitp.load_chart("RELIANCE", "1m", today - datetime.timedelta(days=10))
        self.assertIn("1-minute bars for about a week", str(cm.exception))

    def test_m_and_m_is_quoted(self):
        # a Yahoo answer for another symbol is refused, and the URL carries M%26M.NS
        today = exitp.to_local(exitp.utc_now()).date()
        with self.assertRaises(exitp.PlanError):
            exitp.load_chart("M&M", "5m", today)
        self.assertIn("/chart/M%26M.NS?", self.urls[0])


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-exit", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        with open(os.path.join(ROOT, "trader-risk", "SKILL.md"), encoding="utf-8") as fh:
            risk = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-exit\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertEqual(text[text.index("## Safety"):], risk[risk.index("## Safety"):])
        low = " ".join(text.lower().split())
        self.assertNotIn("$", text.replace("${CLAUDE_SKILL_DIR}", ""))
        for must in ("before the entry", "a stop is a plan, not a guarantee", "day trading loses most people money",
                     "/trader-timemachine", "/trader-budget", "longs only", "₹1,166.00", "₹1,180.10", "₹4.70",
                     "\u20b91,175.40", "too tight to survive the spread", "0.05 tick", "nseindia.com",
                     "15:15", "ist", "reliance"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "nyse", "new york", "tsla",
                      "1:00 p.m."):
            self.assertNotIn(never, low)


if __name__ == "__main__":
    unittest.main()
