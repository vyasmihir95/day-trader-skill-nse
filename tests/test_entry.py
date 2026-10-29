"""trader-entry tests (NSE). Fixtures in tests/fixtures/trader-entry/:

ONGC-2026-10-29-5m.json      ONGC's Thu Oct 29 2026 session, five-minute bars, real Yahoo chart JSON
                             (v8, range=1d, ONGC.NS), 9:15 to 15:25 IST. A natural test: the 9:30 bar
                             wicks to 224.74 and closes AT the range high 224.20 (no trigger), the
                             10:00 bar is the first close above it.
RELIANCE-2026-10-29-5m.json  the same day for RELIANCE (an early trigger)
RELIANCE-2026-10-29-1m.json  RELIANCE one-minute bars of the same day, real
M&M-2026-10-29-5m.json       M&M, for the ampersand in the ticker
DEMO-1m-synthetic.json       SYNTHETIC one-minute bars (not market data): a wick above the
                             range high, a close AT the high, a null bar, a live tick.

Yahoo's live tick and in-progress bar are made from the real session by live_cut(): the bars stop at
the tick time and a tick is appended off the grid, the way a live answer looks.
"""

import copy
import datetime
import gzip
import io
import json
import os
import re
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX, ROOT

entry = load("trader-entry", "entry")

EDIR = os.path.join(FIX, "trader-entry")
ONGC = os.path.join(EDIR, "ONGC-2026-10-29-5m.json")
RELI = os.path.join(EDIR, "RELIANCE-2026-10-29-5m.json")
RELI1M = os.path.join(EDIR, "RELIANCE-2026-10-29-1m.json")
MM = os.path.join(EDIR, "M&M-2026-10-29-5m.json")
DEMO = os.path.join(EDIR, "DEMO-1m-synthetic.json")
OPEN_TS = 1793245500       # 2026-10-29 09:15 IST
DT = datetime.datetime
DAY = "2026-10-29"


def ist(text):
    return entry.parse_now(text)


def doc(path=ONGC):
    with open(path) as fh:
        return json.load(fh)


def chart(d=None, sym="ONGC"):
    return entry.parse_chart(json.dumps(d if d is not None else doc()), sym)


def at(text, d=None, **kw):
    return entry.evaluate(chart(d), ist(text), **kw)


def shift_to(d, day):
    """The same session moved to another date. IST has no daylight saving: whole days only."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    delta = (DT.combine(day, datetime.time(0, 0)) - DT.combine(datetime.date(2026, 10, 29), datetime.time(0, 0))
             ).days * 86400
    r["timestamp"] = [t + delta for t in r["timestamp"]]
    r["meta"]["regularMarketTime"] += delta
    return d


def no_breakout(d=None):
    """Every bar after the range stays under the 224.20 range high."""
    d = copy.deepcopy(d if d is not None else doc())
    q = d["chart"]["result"][0]["indicators"]["quote"][0]
    for i in range(3, len(q["close"])):
        if q["close"][i] is None:
            continue
        q["close"][i] = min(q["close"][i], 224.15)
        q["high"][i] = min(q["high"][i], 224.19)
        q["open"][i] = min(q["open"][i], 224.15)
    return d


def live_cut(d, tick_hhmmss, price):
    """What Yahoo serves while the market is open: bars up to the tick, the bar in progress showing the
    tick price, and the tick itself off the grid. meta.regularMarketTime is the tick."""
    d = copy.deepcopy(d)
    r = d["chart"]["result"][0]
    hh, mm, ss = [int(x) for x in tick_hhmmss.split(":")]
    tick = OPEN_TS + ((hh * 3600 + mm * 60 + ss) - (9 * 3600 + 15 * 60))
    q = r["indicators"]["quote"][0]
    keep = [i for i, t in enumerate(r["timestamp"]) if t <= tick]
    r["timestamp"] = [r["timestamp"][i] for i in keep]
    for k in q:
        q[k] = [q[k][i] for i in keep]
    last = len(keep) - 1
    if r["timestamp"][last] + 300 > tick:           # the bar in progress shows the tick price
        q["close"][last] = price
        q["high"][last] = max(q["high"][last], price)
        q["low"][last] = min(q["low"][last], price)
    r["timestamp"].append(tick)
    for k in q:
        q[k].append(price if k != "volume" else None)
    r["meta"]["regularMarketTime"] = tick
    return d


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = entry.main(argv)
    return code, out.getvalue(), err.getvalue()


class TheSession(unittest.TestCase):
    def test_triggers_at_1005_on_the_1000_bar(self):
        r = at(DAY + " 10:05")
        self.assertEqual(r["status"], "triggered")
        self.assertEqual(r["opening_range"]["high"], 224.2)        # the 9:25 bar's high
        self.assertEqual(r["opening_range"]["low"], 223.05)        # the 9:15 bar's low
        self.assertEqual(r["opening_range"]["bars"], 3)
        t = r["trigger"]
        self.assertEqual(t["time"], "10:05 AM")
        self.assertEqual(t["close_time"], "10:05")
        self.assertEqual(t["bar_start"], "10:00")
        self.assertEqual(t["bar_index"], 9)
        self.assertEqual(t["price"], 224.23)
        self.assertEqual(t["close"], 224.22999572753906)
        self.assertEqual(t["pct_over"], 0.01)
        self.assertEqual(t["volume"], 31108)
        self.assertEqual(r["level"], 224.2)
        self.assertEqual(r["prev_close"], 224.89)
        self.assertEqual(r["opens"], "09:15")
        self.assertEqual(r["closes"], "15:30")
        self.assertEqual((r["no_entries_before"], r["no_entries_after"]), ("09:30", "14:30"))

    def test_at_1003_the_1000_bar_is_still_forming(self):
        r = at(DAY + " 10:03")
        self.assertEqual(r["status"], "waiting")
        self.assertIsNone(r["trigger"])
        self.assertEqual(r["last"]["bar_start"], "09:55")
        self.assertEqual(r["last"]["close"], 224.08)
        self.assertEqual(r["forming"]["bar_start"], "10:00")
        self.assertIn("₹0.12 under the range high ₹224.20", r["reason"])
        self.assertEqual(at(DAY + " 10:04:59")["status"], "waiting")

    def test_a_wick_and_a_close_at_the_high_are_not_a_trigger(self):
        # the 9:30 bar's high is 224.74, far over the range high, and it closes AT 224.20
        c = chart()
        b930 = [b for b in c["bars"] if b["t"] == OPEN_TS + 3 * 300][0]
        self.assertEqual((b930["h"], b930["c"]), (224.74000549316406, 224.1999969482422))
        r = at(DAY + " 09:36")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["last"]["bar_start"], "09:30")
        self.assertEqual(r["last"]["vs_level_pct"], 0.0)
        self.assertEqual(r["bars_closed"], 4)

    def test_before_the_open_and_while_the_range_forms(self):
        r = at(DAY + " 09:00")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "pre-open"))
        self.assertIn("the NSE opens at 9:15", r["reason"])
        self.assertIn("range is set at 9:30", r["reason"])
        r = at(DAY + " 09:28")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "forming"))
        self.assertEqual(r["opening_range"]["bars"], 2)
        self.assertFalse(r["opening_range"]["complete"])
        r = at(DAY + " 09:31")
        self.assertEqual((r["status"], r["phase"]), ("waiting", "open"))
        self.assertEqual(r["opening_range"]["bars"], 3)
        self.assertTrue(r["opening_range"]["complete"])

    def test_the_live_tick_and_the_in_progress_bar_are_dropped(self):
        d = live_cut(doc(), "10:19:45", 224.45)
        c = chart(d)
        self.assertEqual(c["bars"][-1]["t"], OPEN_TS + 3600 + 4 * 60 + 45)    # 10:19:45 is in the file
        r = entry.evaluate(c, entry.utc_of(c["data_time"]))      # read as of the file's own time
        self.assertEqual(r["now"], "2026-10-29 10:19:45")
        self.assertEqual(r["_bars"][-1]["slot"], 11)               # the 10:10 bar
        self.assertEqual(r["forming"]["bar_start"], "10:15")
        self.assertEqual(r["bars_closed"], 12)
        # 10:20: closed by the clock, but Yahoo's last trade (10:19:45) is before its close
        r = entry.evaluate(c, ist(DAY + " 10:20"))
        self.assertEqual(r["_bars"][-1]["slot"], 11)
        self.assertIn("Yahoo's last trade is 10:19:45", r["lag_note"])
        # a minute later with no newer trade it counts; the 10:19:45 tick never becomes a bar
        r = entry.evaluate(c, ist(DAY + " 10:21"))
        self.assertEqual(r["_bars"][-1]["slot"], 12)
        self.assertEqual(r["bars_closed"], 13)
        self.assertTrue(all(b["t"] % 300 == 0 for b in r["_bars"]))

    def test_after_the_close_it_stays_triggered_and_says_it_is_late(self):
        r = at(DAY + " 15:45")
        self.assertEqual((r["status"], r["phase"]), ("triggered", "after-close"))
        self.assertEqual(r["trigger"]["time"], "10:05 AM")
        text = "\n".join(entry.describe(r))
        self.assertIn("minutes ago", text)
        self.assertNotIn("This is the moment", text)
        self.assertIn("₹", text)
        self.assertNotIn("$", text)

    def test_describe_is_in_rupees_and_ist(self):
        text = "\n".join(entry.describe(at(DAY + " 10:05")))
        self.assertIn("ONGC  Thu 2026-10-29 10:05 IST  opening-range breakout (15-minute range)", text)
        self.assertIn("Opening range 9:15-9:30: high ₹224.20, low ₹223.05 (3 of 3 bars).", text)
        self.assertIn("TRIGGERED 10:05 AM at ₹224.23: the 10:00 bar (bar 9) closed above the range high "
                      "₹224.20 (+0.01%).", text)
        self.assertIn("Nothing was placed.", text)
        self.assertNotIn("$", text)

    def test_a_real_second_chart(self):
        # RELIANCE: range 9:15 to 9:30, high 1170.50; the 9:35 bar is the first close over it (1174.30)
        r = entry.evaluate(chart(doc(RELI), "RELIANCE"), ist(DAY + " 10:15"))
        self.assertEqual(r["opening_range"]["high"], 1170.5)
        self.assertEqual(r["opening_range"]["low"], 1156.2)
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["price"]), ("9:40 AM", "09:35", 1174.3))
        self.assertEqual(r["symbol"], "RELIANCE")


class Rules(unittest.TestCase):
    def test_volume_confirmation_moves_the_trigger(self):
        r = at(DAY + " 11:00", min_volume_x=1)
        self.assertEqual(r["opening_range"]["avg_volume"], 82985)   # (104405 + 83949 + 60600) / 3
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_index"], t["price"]), ("10:35 AM", 15, 225.12))
        self.assertEqual(t["volume_x"], 1.18)
        r = at(DAY + " 11:00", min_volume_x=0.5)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["volume_x"]), ("10:30 AM", 0.52))
        r = at(DAY + " 10:07", min_volume_x=1)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("0.37x the range's volume, under your 1.00x", r["reason"])

    def test_level_setup(self):
        r = at(DAY + " 10:30", setup="level", level=224.5)
        self.assertEqual(r["trigger"]["time"], "10:30 AM")
        self.assertEqual(r["trigger"]["bar_index"], 14)
        self.assertEqual(r["trigger"]["price"], 224.76)
        self.assertEqual(r["level"], 224.5)
        self.assertEqual(r["opening_range"]["high"], 224.2)        # still reported, not the level
        # a level under the open: the first eligible close is the 9:25 bar's, at 9:30 (no_entries_before)
        r = at(DAY + " 10:30", setup="level", level=220)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:30 AM", 2))
        with self.assertRaises(entry.EntryError):
            at(DAY + " 10:30", setup="level")
        r = at(DAY + " 10:10", setup="level", level=230)
        self.assertEqual(r["status"], "waiting")
        self.assertIn("the level ₹230.00", r["reason"])
        self.assertEqual(entry.setup_name(r), "close above ₹230.00")

    def test_no_breakout_waits_then_expires_at_the_cutoff(self):
        d = no_breakout()
        r = at(DAY + " 10:49", d)
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(at(DAY + " 14:29", d)["status"], "waiting")
        r = at(DAY + " 14:30", d)
        self.assertEqual(r["status"], "expired")
        self.assertIn("by 14:30", r["reason"])
        self.assertIn("close above the range high ₹224.20", r["reason"])
        self.assertEqual(at(DAY + " 15:45", d)["phase"], "after-close")
        self.assertIn("No entry on this setup today", "\n".join(entry.describe(r)))

    def test_the_bar_closing_at_the_cutoff_still_counts_when_yahoo_lags(self):
        d = live_cut(no_breakout(), "14:29:40", 224.1)              # the last bar in the file is the 14:25 one
        cfg = {"no_entries_before": "09:30", "no_entries_after": "14:30"}
        r = at(DAY + " 14:30:10", d, cfg=cfg)                       # the 14:25 bar closed by the clock only
        self.assertEqual(r["status"], "waiting")
        self.assertIn("Yahoo's last trade is 14:29:40", r["lag_note"])
        self.assertEqual(at(DAY + " 14:31", d, cfg=cfg)["status"], "expired")

    def test_config_window(self):
        cfg = {"no_entries_before": "09:30", "no_entries_after": "10:00"}
        r = at(DAY + " 10:15", cfg=cfg)
        self.assertEqual(r["status"], "expired")                    # the trigger bar closes at 10:05
        self.assertEqual(r["no_entries_after"], "10:00")
        cfg = {"no_entries_before": "10:10", "no_entries_after": "14:30"}
        r = at(DAY + " 10:30", cfg=cfg)
        self.assertEqual(r["trigger"]["time"], "10:10 AM")        # the 10:05 bar, first to close at or after 10:10
        self.assertEqual(r["trigger"]["price"], 224.22)
        r = at(DAY + " 10:07", cfg=cfg)                            # the 10:00 bar closed above, but before 10:10
        self.assertEqual(r["status"], "waiting")
        self.assertIn("but no entries before 10:10 (config)", r["reason"])

    def test_a_cutoff_past_the_close_is_the_close(self):
        r = at(DAY + " 10:15", cfg={"no_entries_before": "09:30", "no_entries_after": "16:00"})
        self.assertEqual(r["no_entries_after"], "15:30")

    def test_defaults_are_the_nse_window(self):
        self.assertEqual(entry.DEFAULTS, {"no_entries_before": "09:30", "no_entries_after": "14:30"})
        self.assertEqual((entry.OPEN, entry.CLOSE), (datetime.time(9, 15), datetime.time(15, 30)))

    def test_bad_inputs(self):
        with self.assertRaises(entry.EntryError):
            at(DAY + " 10:15", or_minutes=7)
        with self.assertRaises(entry.EntryError):
            at(DAY + " 10:15", interval="15m")
        with self.assertRaises(entry.EntryError):
            entry.parse_now("tomorrow")
        with self.assertRaises(entry.EntryError):
            entry.parse_hhmm("25:00", "x")
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_now("tomorrow")
        self.assertIn("IST", str(cm.exception))

    def test_the_clock_is_ist(self):
        utc = DT(2026, 10, 29, 4, 45)
        self.assertEqual(entry.to_local(utc), DT(2026, 10, 29, 10, 15))
        self.assertEqual(entry.local_to_utc(DT(2026, 7, 1, 9, 15)), DT(2026, 7, 1, 3, 45))   # no daylight saving
        self.assertEqual(entry.parse_now(DAY + " 10:15"), utc)
        self.assertEqual(entry.parse_now(DAY + " 10:15 IST"), utc)
        self.assertEqual(entry.parse_now("2026-10-29T04:45:00Z"), utc)
        self.assertEqual(entry.parse_now("2026-10-29T10:15:00+05:30"), utc)
        self.assertEqual(entry.ts_of(utc) - entry.ts_of(DT(2026, 10, 29, 3, 45)), 3600)
        self.assertEqual(entry.utc_of(OPEN_TS), DT(2026, 10, 29, 3, 45))                    # 9:15 IST

    def test_or_minutes_30(self):
        r = at(DAY + " 11:00", or_minutes=30)
        self.assertEqual(r["opening_range"]["bars"], 6)
        self.assertEqual(r["opening_range"]["high"], 224.74)        # the 9:30 bar's wick is now in the range
        self.assertEqual(r["trigger"]["time"], "10:30 AM")
        self.assertEqual(r["trigger"]["price"], 224.76)


class OneMinute(unittest.TestCase):
    def c(self):
        return entry.parse_chart(json.dumps(doc(DEMO)), "DEMO")

    def test_wick_and_close_at_the_high_do_not_trigger(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m")
        self.assertEqual(r["now"], "2026-10-19 09:51:30")
        self.assertEqual(r["opening_range"], {"high": 100.5, "low": 99.0, "minutes": 15, "bars": 15, "of": 15,
                                              "complete": True, "avg_volume": 10667})
        t = r["trigger"]
        self.assertEqual((t["time"], t["bar_start"], t["bar_index"], t["price"]), ("9:44 AM", "09:43", 28, 100.62))
        r = entry.evaluate(c, ist("2026-10-19 09:43:59"), interval="1m")
        self.assertEqual(r["status"], "waiting")
        self.assertEqual(r["forming"]["bar_start"], "09:43")
        self.assertEqual(r["last"]["close"], 100.5)             # the 9:42 bar closed AT the high

    def test_null_bar_tick_and_volume(self):
        c = self.c()
        r = entry.evaluate(c, entry.utc_of(c["data_time"]), interval="1m", min_volume_x=2)
        self.assertEqual((r["trigger"]["time"], r["trigger"]["bar_index"]), ("9:47 AM", 31))
        slots = [b["slot"] for b in r["_bars"]]
        self.assertNotIn(19, slots)                               # 9:34 came as nulls
        self.assertEqual(slots[-1], 35)                           # 9:51 still forming, the 9:51:30 tick dropped
        self.assertEqual(r["forming"]["bar_start"], "09:51")

    def test_the_file_and_the_interval_must_agree(self):
        code, _, err = run(["check", "DEMO", "--offline", DEMO])
        self.assertEqual(code, 1)
        self.assertIn("1m bars, not 5m", err)

    def test_a_real_one_minute_chart(self):
        c = entry.parse_chart(json.dumps(doc(RELI1M)), "RELIANCE")
        self.assertEqual(c["bars"][0]["t"], OPEN_TS)
        r = entry.evaluate(c, ist(DAY + " 10:15"), interval="1m")
        self.assertEqual(r["opening_range"]["bars"], 15)
        self.assertEqual(r["opening_range"]["of"], 15)
        self.assertEqual(r["status"], "triggered")
        t = r["trigger"]
        self.assertGreaterEqual(t["bar_index"], 15)
        # the same rule by hand: the first one-minute close over the 15-minute high
        bars = {b["t"]: b for b in c["bars"]}
        hi = max(bars[OPEN_TS + 60 * k]["h"] for k in range(15) if OPEN_TS + 60 * k in bars)
        self.assertEqual(r["level"], round(hi, 2))
        first = [k for k in range(15, 60) if OPEN_TS + 60 * k in bars and bars[OPEN_TS + 60 * k]["c"] > hi
                 and k + 1 >= 15][0]
        self.assertEqual(t["bar_index"], first)


class Calendar(unittest.TestCase):
    def tearDown(self):
        entry.EXTRA_HOLIDAYS.clear()

    def test_no_daylight_saving_any_month(self):
        for day in (datetime.date(2026, 7, 1), datetime.date(2026, 12, 7), datetime.date(2026, 3, 9),
                    datetime.date(2026, 11, 2)):
            d = shift_to(doc(), day)
            r = entry.evaluate(chart(d), ist("%s 10:05" % day))
            self.assertEqual(r["status"], "triggered", day)
            self.assertEqual(r["trigger"]["time"], "10:05 AM", day)
            self.assertEqual(r["trigger"]["bar_index"], 9, day)
            self.assertEqual(entry.evaluate(chart(d), ist("%s 10:03" % day))["status"], "waiting", day)
        self.assertEqual(ist("2026-12-07 10:05"), DT(2026, 12, 7, 4, 35))

    def test_there_are_no_early_closes(self):
        for day in (datetime.date(2026, 11, 27), datetime.date(2026, 12, 24)):
            self.assertEqual(entry.session(day), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertNotIn("early_close", at(DAY + " 10:05"))

    def test_weekend_holiday_and_empty_data(self):
        r = at("2026-10-31 10:30")                                # a Saturday
        self.assertEqual((r["status"], r["phase"]), ("no-data", "closed"))
        self.assertEqual(r["next_open"], "2026-11-02 09:15")
        self.assertIn("weekend", r["reason"])
        self.assertIn("Next open: 2026-11-02 09:15 IST", "\n".join(entry.describe(r)))
        r = at("2026-10-20 10:30")                                # Dussehra
        self.assertIn("the NSE is closed today (NSE holiday: Dussehra)", r["reason"])
        self.assertEqual(r["next_open"], "2026-10-21 09:15")
        self.assertEqual(entry.trading_day(datetime.date(2026, 11, 10)), (False, "NSE holiday: Diwali-Balipratipada"))
        self.assertEqual(entry.next_open(datetime.date(2026, 11, 9)), "2026-11-11 09:15")
        self.assertTrue(entry.trading_day(datetime.date(2026, 11, 26))[0])    # Thanksgiving is just a Thursday
        empty = copy.deepcopy(doc())
        res = empty["chart"]["result"][0]
        del res["timestamp"]
        res["indicators"] = {"quote": [{}]}
        r = at(DAY + " 10:15", empty)
        self.assertEqual((r["status"], r["phase"]), ("no-data", "open"))
        r = at("2026-10-30 10:15")                                # Yahoo sent yesterday's bars only
        self.assertEqual(r["status"], "no-data")

    def test_calendar_years_known(self):
        self.assertTrue(at(DAY + " 10:30")["calendar_known"])
        r = at("2027-03-01 10:30")
        self.assertFalse(r["calendar_known"])
        self.assertIn("Holiday list covers 2026 only: check nseindia.com", "\n".join(entry.describe(r)))
        self.assertNotIn("nyse", "\n".join(entry.describe(r)).lower())

    def test_user_holidays_close_the_market(self):
        entry.EXTRA_HOLIDAYS["2026-11-12"] = "Diwali muhurat"
        self.assertEqual(entry.trading_day(datetime.date(2026, 11, 12)), (False, "NSE holiday: Diwali muhurat"))
        r = at("2026-11-12 10:30")
        self.assertIn("NSE holiday: Diwali muhurat", r["reason"])

    def test_yahoo_errors(self):
        bad = json.dumps({"chart": {"result": None, "error": {"code": "Not Found",
                                                              "description": "No data found, symbol may be delisted"}}})
        with self.assertRaises(entry.EntryError) as cm:
            entry.parse_chart(bad, "ZZZZ")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(entry.EntryError):
            entry.parse_chart("<html>", "ONGC")


class Tickers(unittest.TestCase):
    def test_yahoo_symbols_and_bare_names(self):
        self.assertEqual(entry.yahoo_symbol("tcs"), "TCS.NS")
        self.assertEqual(entry.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(entry.yahoo_symbol("m&m.ns"), "M%26M.NS")
        self.assertEqual(entry.yahoo_symbol("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(entry.bare("RELIANCE.NS"), "RELIANCE")
        self.assertEqual(entry.bare(" m&m.NS "), "M&M")
        self.assertEqual(entry.chart_path("m&m", "1d", "5m"), "/v8/finance/chart/M%26M.NS?range=1d&interval=5m")

    def test_the_symbol_comes_back_bare(self):
        c = chart(doc(MM), "M&M")
        self.assertEqual(c["symbol"], "M&M")                     # Yahoo says M&M.NS
        self.assertEqual(c["meta"]["symbol"], "M&M.NS")


class Network(unittest.TestCase):
    """Yahoo is never called: _open_url and _sleep are swapped for fakes."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._open, self._sleep = entry._open_url, entry._sleep
        entry._sleep = lambda s: self.sleeps.append(s)
        with open(ONGC) as fh:
            self.body = fh.read()

    def tearDown(self):
        entry._open_url, entry._sleep = self._open, self._sleep

    def answers(self, *codes):
        seq = list(codes)

        def fake(url, timeout=20):
            self.calls.append(url)
            code = seq.pop(0)
            if code == 200:
                return self.body
            raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b'{"chart":{"result":null,'
                                                                           b'"error":{"description":"No data found"}}}'))
        entry._open_url = fake

    def test_url_and_429_retry_once_after_a_pause(self):
        self.answers(429, 200)
        c = entry.get_chart("ONGC", "5m", datetime.date(2026, 10, 29), today=datetime.date(2026, 10, 29))
        self.assertEqual(len(c["bars"]), 74)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com/v8/finance/chart/ONGC.NS?range=1d&interval=5m"] * 2)
        self.assertEqual(self.sleeps, [entry.BACKOFF])

    def test_429_twice_falls_back_to_query1(self):
        self.answers(429, 429, 200)
        entry.get_chart("ONGC", "5m", datetime.date(2026, 10, 29), today=datetime.date(2026, 10, 29))
        self.assertEqual([u.split("/")[2] for u in self.calls],
                         ["query2.finance.yahoo.com", "query2.finance.yahoo.com", "query1.finance.yahoo.com"])

    def test_5xx_falls_back_without_waiting(self):
        self.answers(503, 200)
        entry.get_chart("ONGC", "5m", datetime.date(2026, 10, 29), today=datetime.date(2026, 10, 29))
        self.assertEqual(self.sleeps, [])
        self.assertIn("query1", self.calls[1])

    def test_still_busy_is_one_plain_error(self):
        self.answers(429, 429, 429, 429)
        code, out, err = run(["check", "ONGC", "--now", DAY + " 10:05"])
        self.assertEqual(code, 1)
        self.assertIn("HTTP 429", err)
        self.assertNotIn("Traceback", err)

    def test_unknown_ticker_404(self):
        self.answers(404)
        with self.assertRaises(entry.EntryError) as cm:
            entry.get_chart("NOSUCHCO", "5m", datetime.date(2026, 10, 29), today=datetime.date(2026, 10, 29))
        self.assertIn("No data found", str(cm.exception))

    def test_a_typed_ns_suffix_is_one_request_for_the_bare_name(self):
        self.answers(200)
        c = entry.get_chart("ongc.ns", "5m", datetime.date(2026, 10, 29), today=datetime.date(2026, 10, 29))
        self.assertEqual(c["symbol"], "ONGC")
        self.assertIn("/chart/ONGC.NS?", self.calls[0])

    def test_ranges_for_past_sessions(self):
        today = datetime.date(2026, 10, 29)
        self.assertEqual(entry.chart_range(today, today, "5m"), "1d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 27), today, "5m"), "5d")
        self.assertEqual(entry.chart_range(datetime.date(2026, 10, 10), today, "5m"), "1mo")
        self.assertEqual(entry.chart_range(datetime.date(2026, 9, 10), today, "5m"), "60d")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 10, 10), today, "1m")
        with self.assertRaises(entry.EntryError):
            entry.chart_range(datetime.date(2026, 7, 1), today, "5m")


def live_provider(d, lag_until=None, calls=None):
    """Bars the way Yahoo serves them at `now`: closed bars final, the in-progress bar
    showing a spike to 224.50 (above the high, then it closes under), plus a live tick.
    lag_until: before this UTC time Yahoo's last trade lags 30 seconds behind."""
    base = chart(d)

    def provider(now):
        if calls is not None:
            calls.append(now)
        nts = entry.ts_of(now)
        lagging = lag_until is not None and now < lag_until
        data_ts = nts - 30 if lagging else nts
        bars = []
        for b in base["bars"]:
            if b["t"] % 300 or b["t"] > data_ts:
                continue
            b = dict(b)
            if b["t"] + 300 > data_ts:
                b["c"], b["h"] = 224.50, max(b["h"], 224.50)
            bars.append(b)
        if data_ts % 300:
            bars.append({"t": data_ts, "o": 224.5, "h": 224.5, "l": 224.5, "c": 224.5, "v": 0})
        return {"symbol": "ONGC", "meta": {}, "bars": bars, "prev_close": 224.89, "data_time": data_ts}
    return provider


class Watch(unittest.TestCase):
    OPTS = {"setup": "orb", "or_minutes": 15, "interval": "5m", "level": None, "min_volume_x": None, "cfg": None}

    def loop(self, provider, start, **kw):
        clock = entry.SimClock(ist(start))
        lines, fired = [], []
        code, res = entry.watch_loop(provider, clock.now, clock.sleep, lines.append, dict(self.OPTS),
                                     on_trigger=lambda r, now: fired.append((r, now)), **kw)
        return code, res, lines, fired, clock

    def test_fires_on_the_first_closed_bar_not_the_spike_in_progress(self):
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), calls=calls), DAY + " 09:57", every=60)
        self.assertEqual(code, 0)
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:05 AM")
        self.assertEqual(fired[0][0]["trigger"]["price"], 224.23)
        # it looked at 9:57 (the 9:55 bar spiking to 224.50 in progress), at 10:00:02 (that bar closed 224.08)
        # and at 10:05:02 (the 10:00 bar closed 224.23)
        self.assertEqual([entry.to_local(c).strftime("%H:%M:%S") for c in calls], ["09:57:00", "10:00:02", "10:05:02"])
        text = "\n".join(lines)
        self.assertIn("9:55 bar  closed ₹224.08  ₹0.12 under ₹224.20", text)
        self.assertIn("10:00 bar  closed ₹224.23  TRIGGERED", text)
        self.assertEqual(text.count("TRIGGERED"), 1)         # one line, not one per poll

    def test_waits_for_yahoo_to_catch_up(self):
        lag = ist(DAY + " 10:05:30")
        calls = []
        code, res, lines, fired, clock = self.loop(live_provider(doc(), lag_until=lag, calls=calls),
                                                   DAY + " 10:03", every=30)
        self.assertEqual(code, 0)
        times = [entry.to_local(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times, ["10:03:00", "10:05:02", "10:05:32"])   # 10:05:02: Yahoo still at 10:04:32
        self.assertEqual(fired[0][0]["trigger"]["time"], "10:05 AM")

    def test_from_before_the_open_prints_the_range_bar_by_bar(self):
        code, res, lines, fired, clock = self.loop(live_provider(doc()), DAY + " 09:00", every=30)
        self.assertEqual(code, 0)
        text = "\n".join(lines)
        self.assertIn("not open yet", lines[0])
        self.assertIn("watching from the first bar's close at 9:20", lines[0])
        self.assertIn(" 9:25 bar  closed ₹224.07  opening range set: high ₹224.20, low ₹223.05", text)
        self.assertEqual(sum(1 for x in lines if " bar  closed " in x), 10)    # 9:15 ... 10:00, one line each

    def test_a_computer_that_dozes_off_catches_up_when_it_wakes(self):
        class Dozing(entry.SimClock):
            naps = 0

            def sleep(self, seconds):
                entry.SimClock.sleep(self, seconds)
                self.naps += 1
                if self.naps == 3:                      # the laptop sleeps for 17 minutes mid-wait
                    self.t += datetime.timedelta(minutes=17)
        clock = Dozing(ist(DAY + " 09:31"))
        calls, lines = [], []
        code, res = entry.watch_loop(live_provider(doc(), calls=calls), clock.now, clock.sleep, lines.append,
                                     dict(self.OPTS), every=30)
        self.assertEqual(code, 0)
        times = [entry.to_local(c).strftime("%H:%M:%S") for c in calls]
        self.assertEqual(times[0], "09:31:00")
        gaps = [(b - a).total_seconds() for a, b in zip(calls, calls[1:])]
        self.assertTrue(any(g > 17 * 60 for g in gaps))           # the nap shows
        self.assertLess(min(g for g in gaps if g > 17 * 60), 17 * 60 + 5 * 60)   # ... and it reads at once after
        text = "\n".join(lines)
        for start in (" 9:30", " 9:35", " 9:40", " 9:45", " 9:50", " 9:55", "10:00"):
            self.assertIn(start + " bar  closed", text)          # the bars it slept through, one line each
        self.assertEqual(res["trigger"]["time"], "10:05 AM")

    def test_expires_and_until(self):
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), DAY + " 14:00", every=300)
        self.assertEqual(code, 1)
        self.assertEqual(res["status"], "expired")
        self.assertEqual(fired, [])
        self.assertEqual(entry.to_local(clock.now()).strftime("%H:%M"), "14:30")
        self.assertIn("EXPIRED", lines[-1])
        until = DT(2026, 10, 29, 11, 0)
        code, res, lines, fired, clock = self.loop(live_provider(no_breakout()), DAY + " 10:30", every=300,
                                                   until=until)
        self.assertEqual(code, 1)
        self.assertIn("--until 11:00", lines[-1])

    def test_closed_day_and_repeated_errors(self):
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-31 10:00")
        self.assertEqual(code, 1)
        self.assertIn("weekend", lines[0])
        code, _, lines, _, _ = self.loop(live_provider(doc()), "2026-10-20 10:00")
        self.assertIn("NSE holiday: Dussehra", lines[0])

        def broken(now):
            raise entry.EntryError("Yahoo did not answer")
        code, _, lines, _, _ = self.loop(broken, DAY + " 10:00", every=30)
        self.assertEqual(code, 1)
        self.assertIn("Stopping after 5 failed reads", lines[-1])
        self.assertIn("IST  could not read bars", lines[0])


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cwd = os.getcwd()
        os.chdir(self.d)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.d)
        entry.EXTRA_HOLIDAYS.clear()

    def test_check_write_is_the_contract_shape(self):
        code, text, _ = run(["check", "ONGC", "--offline", ONGC, "--now", DAY + " 10:05", "--write"])
        self.assertEqual(code, 0)
        self.assertIn("TRIGGERED 10:05 AM at ₹224.23", text)
        self.assertIn("/trader-exit", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            e = json.load(fh)
        want = {"created_by": "trader-entry", "symbol": "ONGC", "setup": "orb", "side": "buy", "date": DAY,
                "status": "triggered", "time": "10:05 AM", "bar_start": "10:00", "bar_index": 9, "price": 224.23,
                "level": 224.2, "interval": "5m", "opening_range": {"high": 224.2, "low": 223.05, "minutes": 15},
                "source": "yahoo", "checked_at": "2026-10-29T04:35:00Z"}
        for k, v in want.items():
            self.assertEqual(e[k], v, k)

    def test_check_waiting_json_and_exit_codes(self):
        code, text, _ = run(["check", "ONGC", "--offline", ONGC, "--now", DAY + " 10:03", "--json"])
        self.assertEqual(code, 1)
        r = json.loads(text)
        self.assertEqual(r["status"], "waiting")
        self.assertNotIn("_bars", r)
        code, text, _ = run(["check", "ONGC", "--offline", ONGC, "--now", DAY + " 10:03", "--write", "e.json"])
        with open("e.json") as fh:
            self.assertEqual(json.load(fh)["status"], "waiting")
        self.assertIn("still forming", text)

    def test_offline_gz_folder_and_mismatch(self):
        os.mkdir("charts")
        with open(ONGC, "rb") as src, gzip.open(os.path.join("charts", "ONGC.json.gz"), "wb") as dst:
            dst.write(src.read())
        code, text, _ = run(["check", "ongc", "--offline", "charts", "--now", DAY + " 10:05"])
        self.assertEqual(code, 0)
        code, text, _ = run(["check", "ONGC.NS", "--offline", "charts", "--now", DAY + " 10:05"])
        self.assertEqual(code, 0)
        code, _, err = run(["check", "TCS", "--offline", ONGC])
        self.assertEqual(code, 1)
        self.assertIn("holds ONGC, not TCS", err)

    def test_an_ampersand_ticker_end_to_end(self):
        os.mkdir("charts")
        shutil.copy(MM, os.path.join("charts", "M&M.json"))
        code, text, _ = run(["check", "M&M.NS", "--offline", "charts", "--now", DAY + " 10:15", "--write"])
        self.assertIn("M&M  Thu 2026-10-29 10:15 IST", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["symbol"], "M&M")

    def test_longs_only_and_flag_errors(self):
        code, _, err = run(["check", "ONGC", "--offline", ONGC, "--side", "short"])
        self.assertEqual(code, 1)
        self.assertIn("v2 plans longs only", err)
        code, _, err = run(["check", "ONGC", "--offline", ONGC, "--level", "200"])
        self.assertIn("--level is for --setup level", err)
        code, _, err = run(["check", "ONGC", "--offline", ONGC, "--setup", "level"])
        self.assertIn("needs --level", err)

    def test_there_is_no_market_switch(self):
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                entry.main(["--market", "us", "check", "ONGC"])
        self.assertFalse(hasattr(entry, "set_market"))
        self.assertFalse(hasattr(entry, "MARKETS"))

    def test_config_is_read(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"no_entries_after": "10:00"}, fh)
        code, text, _ = run(["check", "ONGC", "--offline", ONGC, "--now", DAY + " 10:15"])
        self.assertEqual(code, 1)
        self.assertIn("EXPIRED", text)
        with open(os.path.join("trader", "config.json"), "w") as fh:
            fh.write("{nope")
        code, _, err = run(["check", "ONGC", "--offline", ONGC])
        self.assertIn("not valid JSON", err)

    def test_config_market_holidays(self):
        os.mkdir("trader")
        with open(os.path.join("trader", "config.json"), "w") as fh:
            json.dump({"market_holidays": {DAY: "Test holiday"}}, fh)
        code, text, _ = run(["check", "ONGC", "--offline", ONGC, "--now", DAY + " 10:05"])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Test holiday", text)

    def test_watch_replays_the_file_and_writes_the_entry(self):
        code, text, _ = run(["watch", "ONGC", "--offline", ONGC, "--now", DAY + " 09:50", "--every", "30"])
        self.assertEqual(code, 0)
        self.assertIn("10:00 bar  closed ₹224.23  TRIGGERED", text)
        with open(os.path.join("trader", "entry.json")) as fh:
            self.assertEqual(json.load(fh)["bar_index"], 9)
        code, text, _ = run(["watch", "ONGC", "--offline", ONGC, "--now", DAY + " 09:50", "--until", "09:58",
                             "--out", "x.json"])
        self.assertEqual(code, 1)
        self.assertFalse(os.path.exists("x.json"))

    def test_watchlist(self):
        os.mkdir("charts")
        shutil.copy(RELI, os.path.join("charts", "RELIANCE.json"))
        shutil.copy(ONGC, os.path.join("charts", "ONGC.json"))
        shutil.copy(DEMO, os.path.join("charts", "DEMO.json"))
        os.mkdir("trader")
        with open(os.path.join("trader", "watchlist.json"), "w") as fh:
            json.dump({"created_by": "trader-scanner", "hits": [
                {"symbol": "RELIANCE", "setups": ["orb"], "or_high": 1170.5, "or_low": 1156.2},
                {"symbol": "ONGC", "setups": ["orb"]},
                {"symbol": "NOSUCHCO", "setups": ["orb"]}, {"symbol": "DEMO", "setups": ["orb"]}]}, fh)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", DAY + " 10:03"])
        self.assertEqual(code, 0)
        rows = text.splitlines()
        self.assertEqual(rows[1].split(), ["RELIANCE", "triggered", "9:40", "AM", "₹1174.30", "₹1170.50",
                                           "₹1173.80", "+0.28%"])
        self.assertTrue(rows[2].startswith("ONGC        waiting"), rows[2])
        self.assertIn("NOSUCHCO    error: no offline file", rows[3])
        self.assertIn("DEMO        error: DEMO has 1m bars", rows[4])
        self.assertIn("1 of 4 triggered", text)
        code, text, _ = run(["watchlist", "--offline", "charts", "--now", DAY + " 09:00", "--json"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(text)[0]["result"]["status"], "waiting")
        code, _, err = run(["watchlist", "--watchlist", "missing.json"])
        self.assertIn("run /trader-scanner first", err)


class SkillDoc(unittest.TestCase):
    def test_skill_md(self):
        with open(os.path.join(ROOT, "trader-entry", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-entry\n"))
        self.assertGreater(len(text.splitlines()), 60)
        self.assertIn("## Safety", text)
        low = " ".join(text.lower().split())
        for must in ("bar by bar", "can lag", "day trading loses most people money", "/trader-exit", "longs only",
                     "in the background", "/loop", "nothing was placed", "10:05 am", "₹224.23", "bar 9", "nse",
                     "ist", "nseindia.com"):
            self.assertIn(must, low)
        for never in ("\u2014", "tick " + "by tick", "real " + "time", "pro" + "fit", "nyse", "new york",
                      "--market", "nasdaq"):
            self.assertNotIn(never, low)


class NoUsLeftovers(unittest.TestCase):
    def test_script_is_india_only(self):
        with open(os.path.join(ROOT, "trader-entry", "entry.py"), encoding="utf-8") as fh:
            src = fh.read()
        for never in ("NYSE", "New York", "TRADER_MARKET", "set_market", "eastern", "America/"):
            self.assertNotIn(never, src)


if __name__ == "__main__":
    unittest.main()
