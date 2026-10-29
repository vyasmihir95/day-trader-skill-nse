"""trader-scanner tests (NSE). Fixtures in tests/fixtures/trader-scanner/, all real Yahoo / NSE data
fetched on Thu Oct 29 2026 after the close, except where a test builds a synthetic series:

wall5m-2026-10-29.json.gz       54 NSE names (53 real 5-minute v8 charts, 9:15 to 15:25 IST, and Yahoo's own
                                "No data found" answer for NOSUCHCO), as a bundle {SYMBOL: v8 json}
daily-spark-2026-10-29.json     the same 53 names, 3 months of daily closes, as one spark answer
spark-5m-2026-10-29.json        a real 20-symbol spark answer for 1 day of 5-minute bars (closes only)
symdir/EQUITY_L.csv             21 real rows of NSE's EQUITY_L.csv: series EQ, BE and BZ, with M&M and BAJAJ-AUTO
"""

import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, FIX

scan = load("trader-scanner", "scan")

SDIR = os.path.join(FIX, "trader-scanner")
WALL = os.path.join(SDIR, "wall5m-2026-10-29.json.gz")
DAILY = os.path.join(SDIR, "daily-spark-2026-10-29.json")
SPARK5 = os.path.join(SDIR, "spark-5m-2026-10-29.json")
SYMDIR = os.path.join(SDIR, "symdir")
DT = datetime.datetime
D = datetime.date
DAY = D(2026, 10, 29)
OPEN_TS = 1793245500       # 2026-10-29 09:15 IST


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = scan.main(argv)
    return code, out.getvalue(), err.getvalue()


def ts(ist_text):
    """IST wall-clock text -> unix seconds (through the tool's own clock)."""
    utc = scan.local_to_utc(DT.strptime(ist_text, "%Y-%m-%d %H:%M:%S" if ist_text.count(":") == 2
                                        else "%Y-%m-%d %H:%M"))
    return int((utc - DT(1970, 1, 1)).total_seconds())


def bars_series(day, closes, highs=None, lows=None, start="09:15", extra=None):
    """A v8-like series of 5-min bars from `start` on `day` (YYYY-MM-DD)."""
    t0 = DT.strptime(day + " " + start, "%Y-%m-%d %H:%M")
    t = [ts((t0 + datetime.timedelta(minutes=5 * i)).strftime("%Y-%m-%d %H:%M")) for i in range(len(closes))]
    s = {"t": t, "c": list(closes), "h": list(highs or [c + 0.1 if c is not None else None for c in closes]),
         "l": list(lows or [c - 0.1 if c is not None else None for c in closes]), "o": None,
         "v": [1000] * len(closes), "meta": {}}
    for when, c in (extra or []):
        s["t"].append(ts(when))
        for k in ("c", "h", "l"):
            s[k].append(c)
        s["v"].append(10)
    return s


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class Universe(unittest.TestCase):
    def setUp(self):
        self.text = read(os.path.join(SYMDIR, "EQUITY_L.csv"))
        self.kept, self.counts = scan.build_universe(self.text)

    def test_the_kept_list_is_series_eq_only(self):
        self.assertEqual([s for s, _ in self.kept],
                         ["BAJAJ-AUTO", "HDFCBANK", "ICICIBANK", "IDEA", "INFY", "ITC", "LT", "M&M", "NTPC",
                          "ONGC", "RELIANCE", "SBIN", "TCS", "YESBANK"])
        self.assertEqual(self.counts, {"rows": 21, "keep": 14, "series BE": 4, "series BZ": 3})
        names = dict(self.kept)
        self.assertEqual(names["M&M"], "Mahindra & Mahindra Limited")
        self.assertEqual(names["RELIANCE"], "Reliance Industries Limited")
        self.assertNotIn("3IINFOLTD", names)       # series BE: trade for trade, no intraday
        self.assertNotIn("AGSTRA", names)          # series BZ

    def test_crlf_files_and_the_header_check(self):
        kept, counts = scan.build_universe(self.text.replace("\n", "\r\n"))
        self.assertEqual(len(kept), 14)
        with self.assertRaises(scan.ScanError):
            scan.build_universe("Symbol|Security Name\nAAPL|Apple")
        with self.assertRaises(scan.ScanError):
            scan.build_universe("<html>maintenance</html>")
        with self.assertRaises(scan.ScanError):
            scan.build_universe("")

    def test_not_a_plain_ticker_and_other_series(self):
        head = "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
        text = (head + "GOOD,Good Limited,EQ,01-JAN-2000,10,1,INE000000001,10\n"
                "BAD SYM,Bad Limited,EQ,01-JAN-2000,10,1,INE000000002,10\n"
                "GOLDBEES,A Fund,EQ ,01-JAN-2000,10,1,INE000000003,10\n"
                "SMEOD,An SME,SM,01-JAN-2000,10,1,INE000000004,10\n"
                "BLANK,Blank Series,,01-JAN-2000,10,1,INE000000005,10\n"
                "SHORT,only\n")
        kept, counts = scan.build_universe(text)
        self.assertEqual([s for s, _ in kept], ["GOLDBEES", "GOOD"])
        self.assertEqual(counts["not a plain ticker"], 1)
        self.assertEqual(counts["series SM"], 1)
        self.assertEqual(counts["series blank"], 1)


class Time(unittest.TestCase):
    def test_the_clock_is_ist_with_no_daylight_saving(self):
        utc = DT(2026, 10, 29, 4, 45)
        self.assertEqual(scan.to_local(utc), DT(2026, 10, 29, 10, 15))
        self.assertEqual(scan.local_to_utc(DT(2026, 7, 1, 9, 15)), DT(2026, 7, 1, 3, 45))
        self.assertEqual(scan.ts_to_local(OPEN_TS), DT(2026, 10, 29, 9, 15))        # RELIANCE's first bar
        self.assertEqual(scan.ts_to_local(ts("2026-12-01 09:15")), DT(2026, 12, 1, 9, 15))
        self.assertEqual(scan.parse_now("2026-10-29 10:15"), utc)
        self.assertEqual(scan.parse_now("2026-12-01 10:15"), DT(2026, 12, 1, 4, 45))   # same offset all year
        self.assertEqual(scan.parse_now("2026-10-29T04:45:00Z"), utc)
        self.assertEqual(scan.parse_now("2026-10-29T10:15:00+05:30"), utc)
        self.assertEqual(scan.clock_label(DT(2026, 10, 29, 10, 15)), "10:15 AM")
        self.assertEqual(scan.clock_label(datetime.time(12, 5)), "12:05 PM")
        self.assertEqual(scan.clock_label(datetime.time(15, 30)), "3:30 PM")
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_now("tomorrow")
        self.assertIn("IST", str(cm.exception))
        with self.assertRaises(scan.ScanError):
            scan.parse_hhmm("25:00")

    def test_session_is_915_to_1530_and_the_calendar_is_nses(self):
        self.assertEqual(scan.session(DAY), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertIsNone(scan.session(D(2026, 10, 31)))      # Saturday
        ok, why = scan.trading_day(D(2026, 10, 20))
        self.assertFalse(ok)
        self.assertEqual(why, "NSE holiday: Dussehra")
        self.assertEqual(scan.trading_day(D(2026, 10, 31)), (False, "weekend"))
        self.assertTrue(scan.trading_day(D(2026, 11, 26))[0])  # Thanksgiving is just a Thursday here
        for day in (D(2026, 11, 27), D(2026, 12, 24)):          # no early closes on the NSE
            self.assertEqual(scan.session(day), (datetime.time(9, 15), datetime.time(15, 30)))
        self.assertEqual(len(scan.NSE_HOLIDAYS), 15)

    def test_user_holidays_from_config_close_the_market(self):
        scan.EXTRA_HOLIDAYS["2026-11-12"] = "Diwali muhurat"
        try:
            self.assertEqual(scan.trading_day(D(2026, 11, 12)), (False, "NSE holiday: Diwali muhurat"))
            self.assertIsNone(scan.session(D(2026, 11, 12)))
        finally:
            scan.EXTRA_HOLIDAYS.clear()

    def test_there_is_no_market_switch(self):
        for gone in ("set_market", "MARKETS", "MARKET", "NYSE_HOLIDAYS", "eastern_offset", "to_eastern"):
            self.assertFalse(hasattr(scan, gone), gone)
        with self.assertRaises(SystemExit):
            with redirect_stderr(io.StringIO()):
                scan.main(["--market", "us", "daily"])


class Tickers(unittest.TestCase):
    def test_yahoo_tickers_get_ns_and_ampersands_are_encoded(self):
        self.assertEqual(scan.to_yahoo("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(scan.to_yahoo("M&M"), "M%26M.NS")
        self.assertEqual(scan.to_yahoo("BAJAJ-AUTO"), "BAJAJ-AUTO.NS")
        self.assertEqual(scan.to_yahoo("tcs"), "TCS.NS")
        self.assertEqual(scan.bare("reliance.ns"), "RELIANCE")
        self.assertEqual(scan.bare("M&M.NS"), "M&M")

    def test_typed_symbols_may_carry_ns_and_money_is_rupees(self):
        class A:
            symbols, watchlist, universe, limit, offline = "reliance.ns, tcs m&m", None, None, None, None
        self.assertEqual(scan.read_symbols(A), ["RELIANCE", "TCS", "M&M"])
        self.assertEqual(scan.money(1234.5), "₹1,234.50")
        self.assertEqual(scan.money(5), "₹5.00")


class Spark(unittest.TestCase):
    def test_a_real_spark_answer(self):
        text = read(SPARK5)
        names = ["RELIANCE", "TCS", "INFY", "M&M", "BAJAJ-AUTO", "NOSUCHCO"]
        wanted = {scan.urllib.parse.unquote(scan.to_yahoo(s)): s for s in names}
        data = scan.parse_spark(text, wanted)
        self.assertEqual(len(data), 20)            # Yahoo answered for 20 names; NOSUCHCO is simply not there
        self.assertNotIn("NOSUCHCO", data)
        self.assertIn("M&M", data)                 # M&M.NS comes back as ours
        self.assertIn("BAJAJ-AUTO", data)
        self.assertNotIn("M&M.NS", data)
        self.assertEqual(len(scan.parse_spark(text)), 20)   # no map: .NS is stripped just the same
        r = data["RELIANCE"]
        self.assertEqual(len(r["t"]), 74)          # 75 slots, one null close at the end is dropped later
        self.assertIsNone(r["h"])                  # spark is closes only
        self.assertEqual(r["t"][0], OPEN_TS)
        self.assertEqual(r["meta"]["regularMarketPrice"], 1167.4)
        self.assertEqual(r["meta"]["currency"], "INR")
        bars = scan.session_bars(r, DAY, DT(2026, 10, 29, 10, 1))
        self.assertEqual([b["start"].strftime("%H:%M") for b in bars],
                         ["09:15", "09:20", "09:25", "09:30", "09:35", "09:40", "09:45", "09:50", "09:55"])

    def test_a_real_daily_answer_and_the_session_close(self):
        data = scan.parse_spark(read(DAILY))
        self.assertEqual(len(data), 53)
        tcs = data["TCS"]
        self.assertEqual(tcs["meta"]["regularMarketVolume"], 3427409)
        self.assertEqual(scan.daily_closes(tcs, scan.parse_now("2026-10-29 18:00"))[-1], (DAY, 2478.0))
        # the same answer read during the session: today's bar is still in progress
        self.assertEqual(scan.daily_closes(tcs, scan.parse_now("2026-10-29 12:00"))[-1], (D(2026, 10, 28), 2460.0))
        self.assertEqual(scan.daily_closes(tcs, scan.parse_now("2026-10-29 15:29"))[-1][0], D(2026, 10, 28))
        self.assertEqual(scan.daily_closes(tcs, scan.parse_now("2026-10-29 15:30"))[-1][0], DAY)
        # and before the open
        self.assertEqual(scan.daily_closes(tcs, scan.parse_now("2026-10-29 09:00"))[-1][0], D(2026, 10, 28))

    def test_spark_errors(self):
        too_many = '{"spark":{"result":null,"error":{"code":"Bad Request",' \
                   '"description":"Number of symbols needs to be less than or equal to 20"}}}'
        with self.assertRaises(scan.ScanError) as cm:
            scan.parse_spark(too_many)
        self.assertIn("less than or equal to 20", str(cm.exception))
        self.assertEqual(scan.parse_spark('{"spark":{"result":[],"error":null}}'), {})
        with self.assertRaises(scan.ScanError):
            scan.parse_spark('{"chart":{}}')

    def test_urls(self):
        self.assertEqual(scan.spark_path(["M&M", "tcs"], "3mo", "1d"),
                         "/v7/finance/spark?symbols=M%26M.NS,TCS.NS&range=3mo&interval=1d")
        self.assertEqual(scan.chart_path("RELIANCE", "1d", "5m"),
                         "/v8/finance/chart/RELIANCE.NS?range=1d&interval=5m")
        self.assertEqual(scan.chart_path("BAJAJ-AUTO", "5d", "5m"),
                         "/v8/finance/chart/BAJAJ-AUTO.NS?range=5d&interval=5m")


class Network(unittest.TestCase):
    """429s and 5xx without the network."""

    def setUp(self):
        self.calls, self.sleeps = [], []
        self._get, self._sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = self.sleeps.append
        spark = json.loads(read(DAILY))
        spark["spark"]["result"] = spark["spark"]["result"][:20]          # one spark request's worth
        self.body = json.dumps(spark)
        self.names = [r["symbol"][:-3] for r in spark["spark"]["result"]]

    def tearDown(self):
        scan.http_get, scan.SLEEP = self._get, self._sleep

    def answers(self, codes):
        codes = list(codes)

        def fake(url, timeout=20, ua=None):
            self.calls.append(url.split("/v")[0])
            code = codes.pop(0) if codes else 200
            if code != 200:
                raise urllib.error.HTTPError(url, code, "x", {}, io.BytesIO(b""))
            return self.body
        scan.http_get = fake

    def test_429_falls_back_then_waits_once(self):
        self.answers([429, 429, 200])
        text = scan.yahoo_get("/v7/finance/spark?symbols=TCS.NS&range=3mo&interval=1d")
        self.assertEqual(text, self.body)
        self.assertEqual(self.calls, ["https://query2.finance.yahoo.com", "https://query1.finance.yahoo.com",
                                      "https://query2.finance.yahoo.com"])
        self.assertEqual(self.sleeps, [scan.BACKOFF])

    def test_5xx_goes_to_query1_without_waiting(self):
        self.answers([503, 200])
        scan.yahoo_get("/v8/finance/chart/TCS.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.sleeps, [])

    def test_429_for_good_is_reported_as_partial(self):
        self.answers([200] + [429] * 10)
        syms = self.names[:19] + ["NOSUCHCO"] + ["X%02d" % i for i in range(20)]
        data, failed = scan.fetch_spark(syms, "3mo", "1d", 0.3)
        self.assertEqual(len(data), 19)
        self.assertEqual(failed["NOSUCHCO"], "no data from Yahoo")
        self.assertEqual(len(failed), 21)
        self.assertIn("HTTP 429", failed["X00"])
        self.assertEqual(self.sleeps, [0.3, scan.BACKOFF])  # a pause between batches, one backoff
        hits, counts, _ = scan.scan_daily(data, syms, scan.parse_now("2026-10-29 18:00"), 20, 500000)
        self.assertEqual(counts["requested"], 40)
        self.assertEqual(counts["fetched"], 19)

    def test_a_404_is_final(self):
        self.answers([404])
        with self.assertRaises(scan.ScanError):
            scan.yahoo_get("/v8/finance/chart/NOSUCHCO.NS?range=1d&interval=5m")
        self.assertEqual(len(self.calls), 1)


class Daily(unittest.TestCase):
    # Hand-checked from the closes (the numbers in the comments), through Thu Oct 29 2026. Every NSE price is in rupees.
    EXPECT = {
        "KOTAKBANK": ["breakout_20d"],      # 459.00 > 447.95, the best close of the 20 before it
        "ADANIPORTS": ["breakout_20d"],
        "ETERNAL": ["breakout_20d"],
        "LT": ["breakout_20d"],
        "PNB": ["breakout_20d"],
        "DRREDDY": ["breakout_20d"],
        "DLF": ["breakout_20d"],
        "NHPC": ["breakout_20d"],
        "BANKBARODA": ["breakout_20d", "reclaim_ma20"],   # 241.31 over 238.43 and over its 20-day average 233.72
        "INFY": ["bull_flag"],              # up 16.3 % over the 10 sessions, last 5 closes in a 3.7 % range
        "TCS": ["bull_flag"],
        "TECHM": ["bull_flag"],
        "HCLTECH": ["bull_flag"],
        "WIPRO": ["bull_flag"],
        "SBIN": ["reclaim_ma20"],           # 956.80 under 957.58, then 971.90 over 957.94
        "ITC": ["reclaim_ma20"],
        "HINDUNILVR": ["reclaim_ma20"],
        "TITAN": ["reclaim_ma20"],
        "POWERGRID": ["reclaim_ma20"],
        "GRASIM": ["reclaim_ma20"],
        "YESBANK": ["oversold"],            # RSI 17.3, a Rs 15 stock
    }

    def setUp(self):
        self.data, self.errors = scan.load_offline(DAILY)
        self.syms = sorted(self.data)

    def test_setups_after_the_close(self):
        self.assertEqual(len(self.syms), 53)
        hits, counts, last = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-29 18:00"), 0, 0)
        self.assertEqual({h["symbol"]: h["setups"] for h in hits}, self.EXPECT)
        self.assertEqual(last, DAY)
        self.assertEqual(counts, {"requested": 53, "fetched": 53, "illiquid": 0, "short": 0, "scanned": 53,
                                  "hits": 21})
        kb = [h for h in hits if h["symbol"] == "KOTAKBANK"][0]
        self.assertEqual(kb, {"symbol": "KOTAKBANK", "setups": ["breakout_20d"], "price": 459.0,
                              "close_date": "2026-10-29", "rsi": 68.1, "volume": 46297204})
        self.assertEqual(hits[0]["symbol"], "KOTAKBANK")      # the most rupees traded (price x volume) first
        keys = [h["price"] * h["volume"] for h in hits]
        self.assertEqual(keys, sorted(keys, reverse=True))

    def test_the_numbers_behind_a_few_hits(self):
        now = scan.parse_now("2026-10-29 18:00")
        closes = [c for _, c in scan.daily_closes(self.data["KOTAKBANK"], now)]
        self.assertEqual(closes[-8:], [442.3, 437.8, 437.0, 430.5, 440.75, 442.5, 440.0, 459.0])
        tags, nums = scan.find_setups(closes)
        self.assertEqual(tags, ["breakout_20d"])
        self.assertEqual(nums["high20"], 447.95)
        closes = [c for _, c in scan.daily_closes(self.data["INFY"], now)]
        tags, nums = scan.find_setups(closes)
        self.assertEqual(tags, ["bull_flag"])
        self.assertAlmostEqual(nums["run_pct"], 16.3, places=1)
        self.assertAlmostEqual(nums["range5_pct"], 3.7, places=1)

    def test_the_in_progress_bar_is_dropped(self):
        during = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-29 10:50"), 0, 0)
        before = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-29 09:00"), 0, 0)
        self.assertEqual(before, during)
        self.assertEqual(during[2], D(2026, 10, 28))           # Wednesday's close is the last complete bar
        self.assertEqual(during[1]["scanned"], 53)
        after = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-29 15:30"), 0, 0)
        self.assertEqual(after[2], DAY)
        self.assertNotEqual(during[0], after[0])

    def test_a_bar_dated_after_the_clock_is_dropped(self):
        bars = scan.daily_closes(self.data["TCS"], scan.parse_now("2026-10-27 18:00"))
        self.assertEqual(bars[-1][0], D(2026, 10, 27))

    def test_liquidity_filter(self):
        hits, counts, _ = scan.scan_daily(self.data, self.syms, scan.parse_now("2026-10-29 18:00"), 20, 500000)
        self.assertEqual(counts["illiquid"], 8)
        self.assertEqual(counts["scanned"], 45)
        names = sorted(h["symbol"] for h in hits)
        self.assertNotIn("YESBANK", names)                     # Rs 15.43 is under the Rs 20 floor
        self.assertNotIn("GRASIM", names)                      # 241,493 shares is under 500,000
        self.assertEqual(len(hits), 19)
        # what was left out, by the meta the filter reads
        for sym in ("IDEA", "YESBANK"):
            self.assertLess(self.data[sym]["meta"]["regularMarketPrice"], 20)
        for sym in ("GRASIM", "BAJAJ-AUTO"):
            self.assertLess(self.data[sym]["meta"]["regularMarketVolume"], 500000)

    def test_the_defaults_are_nse_floors(self):
        self.assertEqual(scan.MIN_PRICE, 20.0)
        self.assertEqual(scan.DAILY_VOLUME, 500000)

    def test_short_and_empty_charts(self):
        s = {"t": [ts("2026-10-%02d 09:15" % d) for d in range(1, 11)], "c": [10.0] * 10,
             "meta": {"regularMarketPrice": 100, "regularMarketVolume": 5000000}}
        hits, counts, last = scan.scan_daily({"X": s, "E": {"t": [], "c": [], "meta": {}}}, ["X", "E", "GONE"],
                                             scan.parse_now("2026-10-29 09:00"), 20, 500000)
        self.assertEqual(hits, [])
        self.assertEqual((counts["fetched"], counts["short"], counts["illiquid"], counts["scanned"]), (2, 1, 1, 0))
        self.assertIsNone(last)

    def test_rsi_matches_the_reel_math(self):
        self.assertIsNone(scan.rsi([1.0] * 10))
        self.assertEqual(scan.rsi([float(x) for x in range(1, 40)]), 100.0)
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89, 46.03, 45.61,
                  46.28, 46.28]
        self.assertAlmostEqual(scan.rsi(closes), 70.46, places=1)  # Wilder's textbook example


class Orb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data, cls.errors = scan.load_offline(WALL)
        cls.syms = sorted(set(cls.data) | set(cls.errors))

    def scan_at(self, now, min_price=0, syms=None, or_minutes=15):
        day, cutoff = scan.orb_window(scan.parse_now(now), None, or_minutes, datetime.time(14, 30))
        return scan.scan_orb(self.data, syms or self.syms, day, cutoff, or_minutes, min_price, 0)

    def test_the_scan_at_1015(self):
        hits, counts, failed = self.scan_at("2026-10-29 10:15")
        self.assertEqual(len(self.syms), 54)
        self.assertEqual(counts, {"requested": 54, "fetched": 53, "unusable": 0, "illiquid": 0, "scanned": 53,
                                  "candidates": 38, "confirmed": 38, "unconfirmed": 0, "hits": 28})
        self.assertEqual(len(hits), 28)
        self.assertEqual(self.errors, {"NOSUCHCO": "no data for NOSUCHCO: No data found, symbol may be delisted"})
        ongc = [h for h in hits if h["symbol"] == "ONGC"][0]
        self.assertEqual(ongc, {"symbol": "ONGC", "setups": ["orb"], "price": 224.23, "at": "10:05 AM",
                                "bar_start": "10:00", "bar_index": 9, "or_high": 224.2, "or_low": 223.05,
                                "pct_over": 0.01})
        self.assertEqual(hits[0]["symbol"], "ADANIENT")    # sorted by time, then symbol
        mins = [h["bar_index"] for h in hits]
        self.assertEqual(mins, sorted(mins))
        self.assertEqual([h["symbol"] for h in hits if h["at"] == "9:35 AM"],
                         ["ADANIENT", "CIPLA", "COALINDIA", "DRREDDY", "HDFCBANK", "IOC", "KOTAKBANK", "PNB",
                          "TITAN", "VEDL"])
        grasim = [h for h in hits if h["symbol"] == "GRASIM"][0]
        self.assertEqual(grasim, {"symbol": "GRASIM", "setups": ["orb"], "price": 2901.2, "at": "10:15 AM",
                                  "bar_start": "10:10", "bar_index": 11, "or_high": 2900.0, "or_low": 2883.2,
                                  "pct_over": 0.04})
        self.assertEqual(sum(1 for h in hits if h["at"] == "10:15 AM"), 1)
        hd = [h for h in hits if h["symbol"] == "HDFCBANK"][0]
        self.assertEqual((hd["price"], hd["or_high"], hd["pct_over"]), (682.5, 682.45, 0.01))   # a close 5 paise over

    def test_later_scans_only_add_hits(self):
        at10 = {h["symbol"] for h in self.scan_at("2026-10-29 10:10")[0]}
        at1015 = {h["symbol"] for h in self.scan_at("2026-10-29 10:15")[0]}
        at11 = {h["symbol"] for h in self.scan_at("2026-10-29 11:00")[0]}
        self.assertEqual(len(at10), 27)
        self.assertEqual(at1015 - at10, {"GRASIM"})
        self.assertTrue(at10 <= at1015 <= at11)
        self.assertEqual(len(at11), 33)

    def test_price_floor(self):
        hits, counts, _ = self.scan_at("2026-10-29 10:15", min_price=20)
        self.assertEqual(counts["illiquid"], 2)            # IDEA at Rs 12 and YESBANK at Rs 15
        self.assertEqual(counts["scanned"], 51)
        self.assertEqual(len(hits), 28)
        # the floor reads the last close at the cutoff: IDEA 11.88, YESBANK 15.54 at 10:15
        _, counts, _ = self.scan_at("2026-10-29 10:15", min_price=15.6)
        self.assertEqual(counts["illiquid"], 2)
        _, counts, _ = self.scan_at("2026-10-29 10:15", min_price=15.5)
        self.assertEqual(counts["illiquid"], 1)            # only IDEA now
        self.assertEqual(self.data["IDEA"]["meta"]["regularMarketPrice"], 12.12)
        self.assertEqual(self.data["YESBANK"]["meta"]["regularMarketPrice"], 15.43)

    def test_in_progress_bar_and_no_breakout_yet(self):
        hits, _, _ = self.scan_at("2026-10-29 10:04", syms=["ONGC"])
        self.assertEqual(hits, [])                         # the 10:00 bar is not done until 10:05
        hits, _, _ = self.scan_at("2026-10-29 10:05", syms=["ONGC"])
        self.assertEqual(hits[0]["at"], "10:05 AM")
        # the 9:30 bar wicks to 224.74 but closes AT the range high 224.20: not a hit at 9:40
        hits, _, _ = self.scan_at("2026-10-29 09:40", syms=["ONGC"])
        self.assertEqual(hits, [])

    def test_the_live_tick_is_dropped(self):
        s = dict(self.data["ONGC"])
        s = {k: (list(v) if isinstance(v, list) else v) for k, v in s.items()}
        cut = [i for i, t in enumerate(s["t"]) if t <= OPEN_TS + 3000]         # up to the 10:05 bar
        for k in ("t", "c", "h", "l", "o", "v"):
            s[k] = [s[k][i] for i in cut]
        for k, v in (("t", OPEN_TS + 3247), ("c", 224.3), ("h", 224.3), ("l", 224.3), ("o", 224.3), ("v", 0)):
            s[k].append(v)                                                    # a tick at 10:09:07, off the grid
        self.assertEqual(scan.ts_to_local(s["t"][-1]), DT(2026, 10, 29, 10, 9, 7))
        bars = scan.session_bars(s, DAY, DT(2026, 10, 29, 10, 9, 30))
        self.assertEqual(len(bars), 10)                    # 9:15 to 10:00; the 10:05 bar is in progress, the tick dropped
        self.assertTrue(scan.usable(bars, DAY, DT(2026, 10, 29, 10, 9, 30), 3))
        hits, _, _ = scan.scan_orb({"ONGC": s}, ["ONGC"], DAY, DT(2026, 10, 29, 10, 9, 30), 15, 0, 0)
        self.assertEqual(hits[0]["price"], 224.23)         # still the first close above the range

    def test_stage_one_is_a_necessary_condition(self):
        for cut in (DT(2026, 10, 29, 10, 15), DT(2026, 10, 29, 12, 0), DT(2026, 10, 29, 14, 30)):
            for sym, s in self.data.items():
                bars = scan.session_bars(s, DAY, cut)
                if scan.usable(bars, DAY, cut, 3) and scan.orb_hit(bars, 3):
                    self.assertTrue(scan.orb_candidate(bars, 3), sym)

    def test_candidates_are_more_than_hits(self):
        # a chart can close at or above the highest range CLOSE without closing above the range HIGH
        _, counts, _ = self.scan_at("2026-10-29 10:15")
        self.assertGreater(counts["candidates"], counts["hits"])

    def test_confirm_uses_the_v8_highs(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}  # closes only, like spark
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.data[sym]
        hits, counts, failed = scan.scan_orb(spark_like, self.syms, DAY, DT(2026, 10, 29, 10, 15), 15, 0, 0, confirm)
        self.assertEqual(len(hits), 28)
        self.assertEqual(counts["candidates"], len(asked))
        self.assertLess(len(asked), 53)                    # stage 1 skipped the charts that never got that high

        def broken(sym):
            raise scan.ScanError("Yahoo answered HTTP 429 for /v8/finance/chart/%s" % sym)
        hits, counts, failed = scan.scan_orb(spark_like, ["ONGC", "RELIANCE"], DAY,
                                             DT(2026, 10, 29, 10, 15), 15, 0, 0, broken)
        self.assertEqual(hits, [])
        self.assertIn("429", failed["ONGC"])

    def test_a_confirm_chart_with_a_missing_bar_fails_that_symbol_only(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.data.items()}
        holed = dict(self.data["ONGC"], t=[t for i, t in enumerate(self.data["ONGC"]["t"]) if i != 4],
                     c=[c for i, c in enumerate(self.data["ONGC"]["c"]) if i != 4],
                     h=[c for i, c in enumerate(self.data["ONGC"]["h"]) if i != 4],
                     l=[c for i, c in enumerate(self.data["ONGC"]["l"]) if i != 4],
                     o=[c for i, c in enumerate(self.data["ONGC"]["o"]) if i != 4],
                     v=[c for i, c in enumerate(self.data["ONGC"]["v"]) if i != 4])
        hits, counts, failed = scan.scan_orb(spark_like, ["ONGC", "RELIANCE"], DAY, DT(2026, 10, 29, 10, 15), 15,
                                             0, 0, lambda s: holed if s == "ONGC" else self.data[s])
        self.assertEqual(failed, {"ONGC": "the v8 chart has a missing bar"})
        self.assertEqual([h["symbol"] for h in hits], ["RELIANCE"])

    def test_a_missing_bar_makes_a_chart_unusable(self):
        s = bars_series("2026-10-29", [10, 10.2, 10.1, None, 10.5, 10.6, 10.7, 10.8, 10.9])
        cut = DT(2026, 10, 29, 10, 0)
        bars = scan.session_bars(s, DAY, cut)
        self.assertFalse(scan.usable(bars, DAY, cut, 3))
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], DAY, cut, 15, 0, 0)
        self.assertEqual((counts["unusable"], counts["scanned"], hits), (1, 0, []))

    def test_a_30_minute_range_and_no_early_close(self):
        # Fri Nov 27 2026 is an ordinary full NSE day (the NYSE closed early; the NSE does not)
        closes = [100, 101, 100.5, 100.8, 101.2, 100.9, 101.5] + [101.0] * 40
        s = bars_series("2026-11-27", closes, highs=[c + 0.2 for c in closes])
        day = D(2026, 11, 27)
        hits, counts, _ = scan.scan_orb({"X": s}, ["X"], day, DT(2026, 11, 27, 12, 0), 30, 0, 0)
        self.assertEqual(counts["scanned"], 1)
        self.assertEqual(hits[0]["at"], "9:50 AM")          # range 9:15 to 9:45 (high 101.4), the 9:45 bar 101.5
        self.assertEqual(hits[0]["or_high"], 101.4)
        self.assertEqual(hits[0]["bar_index"], 6)
        self.assertEqual(scan.ts_to_local(s["t"][0]), DT(2026, 11, 27, 9, 15))
        bars = scan.session_bars(s, day, DT(2026, 11, 27, 12, 59))
        self.assertEqual(len(bars), 44)                    # 9:15 to 12:50 done at 12:59
        bars = scan.session_bars(s, day, DT(2026, 11, 27, 14, 0))
        self.assertEqual(len(bars), 47)                    # bars after 1:00 PM count: no early close
        self.assertEqual(scan.orb_window(scan.parse_now("2026-11-27 13:10"), None, 15, datetime.time(14, 30))[1],
                         DT(2026, 11, 27, 13, 10))

    def test_window_messages(self):
        until = datetime.time(14, 30)

        def msg(now, at=None, until=until):
            with self.assertRaises(scan.ScanError) as cm:
                scan.orb_window(scan.parse_now(now), at, 15, until)
            return str(cm.exception)
        self.assertIn("weekend", msg("2026-10-31 10:15"))
        self.assertIn("NSE holiday: Dussehra", msg("2026-10-20 10:15"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-29 09:20"))
        self.assertIn("Run orb again after 9:35 AM", msg("2026-10-29 09:33"))
        self.assertIn("9:15 AM to 9:30 AM", msg("2026-10-29 09:20"))
        self.assertIn("IST", msg("2026-10-29 09:20"))
        self.assertIn("expired", msg("2026-10-29 14:45"))
        self.assertIn("expired", msg("2026-10-29 11:30", until=datetime.time(11, 0)))
        self.assertIn("closed at 3:30 PM IST", msg("2026-10-29 16:30"))
        self.assertIn("later than now", msg("2026-10-29 10:00", at=datetime.time(10, 15)))
        self.assertIn("after the 3:30 PM close", msg("2026-10-29 17:00", at=datetime.time(16, 30)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-29 16:30"), datetime.time(10, 15), 15, until),
                         (DAY, DT(2026, 10, 29, 10, 15)))
        self.assertEqual(scan.orb_window(scan.parse_now("2026-10-29 09:35"), None, 15, until)[1],
                         DT(2026, 10, 29, 9, 35))
        self.assertEqual(scan.slot(DAY, 0), DT(2026, 10, 29, 9, 15))
        with self.assertRaises(scan.ScanError):
            scan.orb_window(scan.parse_now("2026-10-29 10:15"), None, 7, until)


class LiveFiveMinute(unittest.TestCase):
    """A real spark 5-minute answer (stage 1) with each name's v8 chart (stage 2)."""

    def setUp(self):
        self.spark = scan.parse_spark(read(SPARK5))
        self.v8, _ = scan.load_offline(WALL)
        self.now = scan.parse_now("2026-10-29 10:01")

    def test_in_progress_bar_is_dropped_at_1001(self):
        self.assertEqual(len(self.spark), 20)
        day, cut = DAY, DT(2026, 10, 29, 10, 1)
        bars = scan.session_bars(self.spark["RELIANCE"], day, cut)
        self.assertEqual(len(bars), 9)                     # 9:15 ... 9:55 complete, 10:00 still in progress
        self.assertEqual(bars[-1]["start"], DT(2026, 10, 29, 9, 55))
        self.assertIsNone(bars[-1]["h"])                   # spark: closes only

    def test_stage_two_confirms_what_stage_one_found(self):
        day, cut = DAY, DT(2026, 10, 29, 10, 15)
        names = sorted(self.spark)
        hits, counts, failed = scan.scan_orb(self.spark, names, day, cut, 15, 20, 0, lambda s: self.v8[s])
        full, _, _ = scan.scan_orb(self.v8, names, day, cut, 15, 20, 0)
        self.assertEqual(hits, full)                       # spark for stage 1, v8 for stage 2: the same answer
        self.assertEqual(counts["fetched"], 20)
        self.assertEqual(failed, {})
        self.assertIn("RELIANCE", [h["symbol"] for h in hits])
        rel = [h for h in hits if h["symbol"] == "RELIANCE"][0]
        self.assertEqual((rel["or_high"], rel["or_low"], rel["price"], rel["at"]), (1170.5, 1156.2, 1174.3, "9:40 AM"))

    def test_stage_two_cap_takes_the_most_traded(self):
        spark_like = {k: dict(v, h=None, l=None) for k, v in self.v8.items()}
        asked = []

        def confirm(sym):
            asked.append(sym)
            return self.v8[sym]
        day, cut = DAY, DT(2026, 10, 29, 10, 15)
        hits, counts, _ = scan.scan_orb(spark_like, sorted(self.v8), day, cut, 15, 0, 0, confirm, max_confirm=10)
        self.assertEqual((counts["confirmed"], counts["unconfirmed"]), (10, counts["candidates"] - 10))
        self.assertEqual(counts["candidates"], 38)
        # the ten with the most rupees traded by 10:15 (last close x volume so far), among the candidates
        traded = {}
        for sym, s in self.v8.items():
            bars = scan.session_bars(s, day, cut)
            traded[sym] = bars[-1]["c"] * sum(b["v"] for b in bars)
        cands = [sym for sym in self.v8 if scan.orb_candidate(scan.session_bars(self.v8[sym], day, cut), 3)]
        self.assertEqual(sorted(asked), sorted(sorted(cands, key=lambda s: -traded[s])[:10]))
        self.assertLessEqual(len(hits), 10)
        hits_all, _, _ = scan.scan_orb(spark_like, sorted(self.v8), day, cut, 15, 0, 0, confirm, max_confirm=0)
        self.assertEqual(len(hits_all), 28)
        for h in hits:
            self.assertIn(h, hits_all)


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.t = os.path.join(self.d, "trader")
        self.wl = os.path.join(self.t, "watchlist.json")

    def tearDown(self):
        shutil.rmtree(self.d)
        scan.EXTRA_HOLIDAYS.clear()

    def read(self, path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_universe_daily_orb_end_to_end(self):
        uni = os.path.join(self.t, "universe.txt")
        code, text, _ = run(["universe", "--offline", SYMDIR, "--out", uni])
        self.assertEqual(code, 0)
        self.assertIn("NSE equity list: 21 rows.", text)
        self.assertIn("Kept 14 NSE-listed shares (series EQ", text)
        self.assertIn("Dropped: 4 series BE, 3 series BZ.", text)
        lines = read(uni).splitlines()
        self.assertEqual(len(lines), 14)
        self.assertIn("M&M|Mahindra & Mahindra Limited", lines)
        self.assertIn("BAJAJ-AUTO|Bajaj Auto Limited", lines)
        # the same with the CSV file itself, as JSON
        code, text, _ = run(["universe", "--offline", os.path.join(SYMDIR, "EQUITY_L.csv"), "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["count"], out["rows"], out["out"]), (14, 21, None))
        self.assertEqual(out["dropped"], {"series BE": 4, "series BZ": 3})

        # daily over that universe: all 14 names have a chart; IDEA and YESBANK are under the Rs 20 floor
        code, text, _ = run(["daily", "--universe", uni, "--offline", DAILY, "--now", "2026-10-29 18:00",
                             "--out", self.wl, "--min-volume", "0"])
        self.assertEqual(code, 0)
        self.assertIn("Fetched 14 of 14 charts. 12 passed the liquidity filter", text)
        doc = self.read(self.wl)
        self.assertEqual((doc["mode"], doc["universe"], doc["scanned"], doc["failed"]), ("daily", 14, 12, 0))
        syms = {h["symbol"]: h["setups"] for h in doc["hits"]}
        self.assertEqual(syms, {k: v for k, v in Daily.EXPECT.items() if k in ("INFY", "ITC", "LT", "SBIN", "TCS")})
        self.assertEqual(doc["as_of"], "2026-10-29 close")
        self.assertEqual(doc["filters"], {"min_price": 20.0, "min_volume": 0})
        # a name Yahoo has no chart for makes the scan partial, and it says so
        code, text, _ = run(["daily", "--symbols", "TCS,NOSUCHCO", "--offline", DAILY, "--now", "2026-10-29 18:00",
                             "--no-write"])
        self.assertIn("Fetched 1 of 2 charts (1 failed or had no data)", text)

        # the 10:15 scan
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:15", "--out", self.wl])
        self.assertEqual(code, 0)
        self.assertIn("28 of 51 charts closed above their opening-range high by 10:15 AM", text)
        self.assertRegex(text, r"10:15 AM  GRASIM +₹2,901\.20  range 2,883\.20 to 2,900\.00  \+0\.04% over")
        self.assertIn("10:05 AM  ONGC", text)
        self.assertIn("(2 under the price / volume floor)", text)
        self.assertIn("not a list of stocks to buy", text)
        self.assertNotIn("$", text)
        doc = self.read(self.wl)
        self.assertEqual({k: doc[k] for k in ("created_by", "created_at", "mode", "as_of", "universe", "scanned",
                                              "source")},
                         {"created_by": "trader-scanner", "created_at": "2026-10-29T04:45:00Z", "mode": "orb",
                          "as_of": "2026-10-29 10:15 IST", "universe": 54, "scanned": 51, "source": "yahoo"})
        self.assertEqual(doc["filters"], {"min_price": 20.0, "min_volume": 0})
        self.assertEqual(len(doc["hits"]), 28)
        self.assertIn({"symbol": "ONGC", "setups": ["orb"], "price": 224.23, "at": "10:05 AM", "bar_start": "10:00",
                       "bar_index": 9, "or_high": 224.2, "or_low": 223.05, "pct_over": 0.01}, doc["hits"])

        # the hits read back as a watchlist, with --at instead of --now
        code, text, _ = run(["orb", "--offline", WALL, "--at", "10:15", "--watchlist", self.wl, "--min-price", "0",
                             "--json", "--no-write"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], len(out["hits"])), (28, 28, 28))

    def test_partial_stage_two_is_said(self):
        """The networked path with a fake Yahoo that answers from the Oct 29 bundle."""
        bundle = json.loads(scan._read_maybe_gz(WALL))
        calls = []

        def bare_of(path_part):
            return scan.urllib.parse.unquote(path_part)[:-3]

        def get(path):
            calls.append(path)
            if path.startswith("/v7/finance/spark"):
                res = []
                for y in path.split("symbols=")[1].split("&")[0].split(","):
                    r = ((bundle.get(bare_of(y)) or {}).get("chart") or {}).get("result")
                    if r:
                        closes = r[0]["indicators"]["quote"][0].get("close", [])
                        res.append({"symbol": scan.urllib.parse.unquote(y),
                                    "response": [dict(r[0], indicators={"quote": [{"close": closes}]})]})
                return json.dumps({"spark": {"result": res, "error": None}})
            return json.dumps(bundle[bare_of(path.split("/chart/")[1].split("?")[0])])
        old_get, old_sleep, real_now = scan.yahoo_get, scan.SLEEP, scan.parse_now
        scan.yahoo_get, scan.SLEEP = get, (lambda s: None)
        scan.parse_now = lambda text: real_now(text or "2026-10-29 10:20")  # the machine clock, pinned
        uni = os.path.join(self.d, "u.txt")
        with open(uni, "w") as fh:
            fh.write("\n".join(sorted(bundle)) + "\n")
        try:
            code, text, _ = run(["orb", "--universe", uni, "--now", "2026-10-29 10:15", "--min-price", "0",
                                 "--max-confirm", "10", "--no-write"])
        finally:
            scan.yahoo_get, scan.SLEEP, scan.parse_now = old_get, old_sleep, real_now
        self.assertEqual(code, 0)
        self.assertIn("53 usable; 38 candidates, 10 confirmed", text)
        self.assertIn("PARTIAL: 28 more candidates were not checked", text)
        self.assertRegex(text, r"\d+ of the 10 checked charts closed above their opening-range high by 10:15 AM")
        self.assertEqual(sum(1 for c in calls if c.startswith("/v8/")), 10)
        self.assertEqual(sum(1 for c in calls if c.startswith("/v7/")), 3)    # 54 symbols, 20 a request
        self.assertTrue(all("range=1d&interval=5m" in c for c in calls))
        self.assertTrue(any("M%26M.NS" in c for c in calls if c.startswith("/v7/")))

    def test_universe_download_uses_nses_url_user_agent_and_one_retry(self):
        body = read(os.path.join(SYMDIR, "EQUITY_L.csv"))
        seen, sleeps = [], []
        old_get, old_sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = sleeps.append

        def flaky(url, timeout=20, ua=None):
            seen.append((url, ua))
            if len(seen) == 1:
                raise urllib.error.URLError("timed out")
            return body
        scan.http_get = flaky
        try:
            code, text, _ = run(["universe", "--out", os.path.join(self.t, "u.txt")])
        finally:
            scan.http_get, scan.SLEEP = old_get, old_sleep
        self.assertEqual(code, 0)
        self.assertEqual(seen, [("https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv", "Mozilla/5.0")] * 2)
        self.assertEqual(sleeps, [scan.BACKOFF])
        self.assertIn("Kept 14 NSE-listed shares", text)
        self.assertEqual(len(read(os.path.join(self.t, "u.txt")).splitlines()), 14)

    def test_universe_download_failures_are_plain_messages(self):
        old_get, old_sleep = scan.http_get, scan.SLEEP
        scan.SLEEP = lambda s: None

        def down(url, timeout=20, ua=None):
            raise urllib.error.URLError("no route")

        def forbidden(url, timeout=20, ua=None):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, io.BytesIO(b""))
        try:
            scan.http_get = down
            code, _, err = run(["universe", "--no-write"])
            self.assertEqual(code, 1)
            self.assertIn("could not reach nsearchives.nseindia.com", err)
            scan.http_get = forbidden
            code, _, err = run(["universe", "--no-write"])
            self.assertIn("HTTP 403", err)
            self.assertNotIn("Traceback", err)
        finally:
            scan.http_get, scan.SLEEP = old_get, old_sleep
        code, _, err = run(["universe", "--offline", self.d])
        self.assertIn("no EQUITY_L.csv", err)

    def test_plain_messages_and_exit_codes(self):
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-31 10:15", "--out", self.wl])
        self.assertEqual(code, 1)
        self.assertIn("No session on Sat 2026-10-31 (weekend)", err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(os.path.exists(self.wl))
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-29 09:24", "--out", self.wl])
        self.assertIn("after 9:35 AM", err)
        cfg = os.path.join(self.t, "config.json")
        os.makedirs(self.t)
        with open(cfg, "w") as fh:
            json.dump({"no_entries_after": "10:00"}, fh)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:15", "--config", cfg])
        self.assertEqual(code, 1)
        self.assertIn("expired", err)
        code, _, err = run(["orb", "--offline", WALL])
        self.assertIn("--at HH:MM", err)
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:00", "--symbols", "ONGC",
                             "--out", self.wl])
        self.assertEqual(code, 1)                          # no breakout yet, but the empty list is written
        self.assertIn("No chart closed above its opening-range high by 10:00 AM", text)
        self.assertEqual(self.read(self.wl)["hits"], [])
        code, _, err = run(["daily", "--universe", os.path.join(self.d, "nope.txt")])
        self.assertEqual(code, 1)
        self.assertIn("scan.py universe", err)
        code, _, err = run(["daily", "--offline", os.path.join(self.d, "nothing-here")])
        self.assertIn("no offline file", err)
        empty = os.path.join(self.d, "empty.json")
        with open(empty, "w") as fh:
            fh.write("{}")
        code, _, err = run(["daily", "--offline", empty])
        self.assertEqual(code, 1)
        self.assertIn("no symbols to scan", err)

    def test_config_market_holidays_close_the_day(self):
        os.makedirs(self.t)
        cfg = os.path.join(self.t, "config.json")
        with open(cfg, "w") as fh:
            json.dump({"market_holidays": {"2026-10-29": "Test holiday"}}, fh)
        code, _, err = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:15", "--config", cfg, "--no-write"])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Test holiday", err)

    def test_offline_folder_of_v8_files(self):
        folder = os.path.join(self.d, "charts")
        os.makedirs(folder)
        bundle = json.loads(scan._read_maybe_gz(WALL))
        for sym in ("ONGC", "RELIANCE", "M&M", "NOSUCHCO"):
            with open(os.path.join(folder, sym + ".json"), "w") as fh:
                json.dump(bundle[sym], fh)
        code, text, _ = run(["orb", "--offline", folder, "--now", "2026-10-29 10:15", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"], out["failed"]), (4, 3, 1))
        self.assertEqual(sorted(h["symbol"] for h in out["hits"]), ["ONGC", "RELIANCE"])
        self.assertEqual(code, 0)
        # M&M had no breakout by 10:15; the symbol (with its ampersand) was still read from the file name
        data, errors = scan.load_offline(folder)
        self.assertEqual(sorted(data), ["M&M", "ONGC", "RELIANCE"])
        self.assertEqual(list(errors), ["NOSUCHCO"])

    def test_symbol_lists(self):
        txt = os.path.join(self.d, "list.txt")
        with open(txt, "w") as fh:
            fh.write("# my list\nONGC|Oil & Natural Gas Corporation Limited\nreliance.ns\n\nONGC\nM&M\n")
        self.assertEqual(scan.symbols_from_file(txt), ["ONGC", "RELIANCE", "ONGC", "M&M"])
        js = os.path.join(self.d, "wl.json")
        with open(js, "w") as fh:
            json.dump({"hits": [{"symbol": "ongc"}, {"symbol": "RELIANCE"}, {"symbol": "m&m.ns"}]}, fh)
        self.assertEqual(scan.symbols_from_file(js), ["ONGC", "RELIANCE", "M&M"])
        code, text, _ = run(["daily", "--offline", DAILY, "--watchlist", txt, "--now", "2026-10-29 18:00",
                             "--json", "--no-write", "--min-volume", "0"])
        out = json.loads(text)
        self.assertEqual((out["universe"], out["scanned"]), (3, 3))     # ONGC twice counts once
        code, text, _ = run(["daily", "--offline", DAILY, "--limit", "3", "--now", "2026-10-29 18:00", "--json",
                             "--no-write", "--min-volume", "0"])
        self.assertEqual(json.loads(text)["universe"], 3)
        code, text, _ = run(["daily", "--offline", DAILY, "--symbols", "m&m.ns,TCS", "--now", "2026-10-29 18:00",
                             "--json", "--no-write"])
        self.assertEqual(json.loads(text)["universe"], 2)

    def test_daily_json_and_text(self):
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-29 18:00", "--json", "--no-write",
                             "--min-volume", "0", "--min-price", "0"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(len(out["hits"]), 21)
        self.assertEqual(out["note"], "A scan is a list of charts that match a rule, not a list of stocks to buy.")
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-29 18:00", "--no-write"])
        self.assertEqual(code, 0)
        self.assertIn("Daily scan, Thu 2026-10-29 18:00 IST, closes through 2026-10-29", text)
        self.assertIn("passed the liquidity filter (₹20+ and 500,000+ shares)", text)
        self.assertIn("19 charts matched a setup", text)
        self.assertRegex(text, r"KOTAKBANK +₹459\.00  RSI  68\.1  20-day closing-high breakout")
        self.assertNotIn("$", text)

    def test_defaults_are_the_nse_ones(self):
        code, text, _ = run(["orb", "--offline", WALL, "--now", "2026-10-29 10:15", "--no-write", "--json"])
        out = json.loads(text)
        self.assertEqual(out["filters"], {"min_price": 20.0, "min_volume": 0})
        self.assertEqual(out["or_minutes"], 15)
        code, text, _ = run(["daily", "--offline", DAILY, "--now", "2026-10-29 18:00", "--no-write", "--json"])
        self.assertEqual(json.loads(text)["filters"], {"min_price": 20.0, "min_volume": 500000})
        self.assertEqual(scan.UNTIL, "14:30")


class NoUsLeftovers(unittest.TestCase):
    def test_script_and_skill_are_india_only(self):
        from _load import ROOT
        with open(os.path.join(ROOT, "trader-scanner", "scan.py"), encoding="utf-8") as fh:
            src = fh.read()
        for never in ("NYSE", "New York", "Nasdaq", "nasdaqtrader", "TRADER_MARKET", "set_market", "eastern",
                      "America/", "BRK"):
            self.assertNotIn(never, src)
        with open(os.path.join(ROOT, "trader-scanner", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith("---\nname: trader-scanner\n"))
        self.assertIn("## Safety", text)
        low = " ".join(text.lower().split())
        for never in ("nyse", "nasdaq", "new york", "--market", "\u2014", " et "):
            self.assertNotIn(never, low)
        for must in ("nse", "ist", "nseindia.com", "equity_l.csv", "series eq", "a scan is a list of charts that match "
                     "a rule, not a list of stocks to buy", "/trader-entry", "/trader-news", "10:05 am", "₹224.23",
                     "bar by bar", "partial"):
            self.assertIn(must, low)


if __name__ == "__main__":
    unittest.main()
