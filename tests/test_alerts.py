import datetime
import gzip
import io
import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

alerts = load("trader-alerts", "alerts")

QDIR = os.path.join(FIX, "trader-alerts")
PLAN = os.path.join(QDIR, "plan-reliance.json")
DT = datetime.datetime
UA = "Mozilla/5.0 (compatible; day-trader-skill/2.0; +https://github.com/Jakeschincariol/day-trader-skill)"

# NSE 2026 trading holidays, typed from NSE's published list (not verified against nseindia.com)
NSE_2026 = ["2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31", "2026-04-03", "2026-04-14",
            "2026-05-01", "2026-05-28", "2026-06-26", "2026-09-14", "2026-10-02", "2026-10-20",
            "2026-11-10", "2026-11-24", "2026-12-25"]


def ist(text):
    return alerts.parse_now(text)


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = alerts.main(argv)
    return code, out.getvalue(), err.getvalue()


def plan_rules():
    with open(PLAN) as fh:
        return alerts.rules_from_plan(json.load(fh))


class Calendar(unittest.TestCase):
    def test_holidays_match_the_published_list(self):
        self.assertEqual(sorted(alerts.NSE_HOLIDAYS), NSE_2026)
        self.assertEqual(alerts.CALENDAR_YEARS, (2026,))
        for d in alerts.NSE_HOLIDAYS:
            self.assertLess(datetime.date.fromisoformat(d).weekday(), 5, d)

    def test_no_us_leftovers(self):
        for name in ("NYSE_HOLIDAYS", "NYSE_EARLY_CLOSE", "eastern_offset", "to_eastern", "stooq_url", "parse_stooq"):
            self.assertFalse(hasattr(alerts, name), name)

    def test_ist_has_no_daylight_saving(self):
        for utc in (DT(2026, 3, 8, 7, 0), DT(2026, 7, 1, 0, 0), DT(2026, 11, 1, 6, 0), DT(2027, 3, 14, 7, 0)):
            self.assertEqual(alerts.to_local(utc) - utc, datetime.timedelta(hours=5, minutes=30))
            self.assertEqual(alerts.local_to_utc(alerts.to_local(utc)), utc)

    def test_parse_now(self):
        self.assertEqual(ist("2026-10-13T09:15"), DT(2026, 10, 13, 3, 45))
        self.assertEqual(ist("2026-10-26 10:15"), DT(2026, 10, 26, 4, 45))
        self.assertEqual(ist("2026-12-01T09:30"), DT(2026, 12, 1, 4, 0))
        self.assertEqual(ist("2026-10-13T20:00:01Z"), DT(2026, 10, 13, 20, 0, 1))
        self.assertEqual(ist("2026-10-13T12:00:00+05:30"), DT(2026, 10, 13, 6, 30))
        with self.assertRaises(alerts.AlertError):
            ist("tomorrow")

    def test_market_status(self):
        st = alerts.market_status(ist("2026-10-13T10:00"))
        self.assertTrue(st["open"])
        self.assertEqual((st["opens"], st["closes"], st["now_ist"]), ("09:15", "15:30", "2026-10-13 10:00"))
        self.assertFalse(alerts.market_status(ist("2026-10-13T09:14"))["open"])
        self.assertEqual(alerts.market_status(ist("2026-10-13T09:14"))["next_open"], "2026-10-13 09:15")
        self.assertTrue(alerts.market_status(ist("2026-10-13T09:15"))["open"])
        self.assertTrue(alerts.market_status(ist("2026-10-13T15:29"))["open"])
        after = alerts.market_status(ist("2026-10-13T15:30"))
        self.assertFalse(after["open"])
        self.assertEqual((after["reason"], after["next_open"]), ("after the close", "2026-10-14 09:15"))
        dussehra = alerts.market_status(ist("2026-10-20T11:00"))
        self.assertFalse(dussehra["open"])
        self.assertEqual(dussehra["reason"], "NSE holiday: Dussehra")
        self.assertEqual(dussehra["next_open"], "2026-10-21 09:15")
        xmas = alerts.market_status(ist("2026-12-25T10:00"))
        self.assertEqual((xmas["open"], xmas["next_open"]), (False, "2026-12-28 09:15"))   # Friday -> Monday
        self.assertTrue(alerts.market_status(ist("2026-12-24T10:00"))["open"])             # no early close
        self.assertEqual(alerts.market_status(ist("2026-10-17T10:00"))["reason"], "weekend")
        self.assertFalse(alerts.market_status(ist("2027-03-01T10:00"))["calendar_known"])
        self.assertTrue(alerts.market_status(ist("2026-10-13T10:00"))["calendar_known"])

    def test_utc_instant_maps_to_the_ist_session(self):
        # 03:45 UTC is 09:15 IST, the opening bell; 10:00 UTC is 15:30 IST, the close
        self.assertTrue(alerts.market_status(DT(2026, 10, 26, 3, 45))["open"])
        self.assertFalse(alerts.market_status(DT(2026, 10, 26, 3, 44))["open"])
        self.assertTrue(alerts.market_status(DT(2026, 10, 26, 9, 59))["open"])
        self.assertFalse(alerts.market_status(DT(2026, 10, 26, 10, 0))["open"])

    def test_extra_holidays_from_config(self):
        d = tempfile.mkdtemp()
        try:
            cfg = os.path.join(d, "config.json")
            with open(cfg, "w") as fh:
                json.dump({"market_holidays": {"2026-11-09": "Diwali muhurat", "junk": "x"}}, fh)
            self.assertTrue(alerts.market_status(ist("2026-11-09T10:00"))["open"])
            code, text, _ = run(["clock", "--now", "2026-11-09 10:00", "--config", cfg])
            self.assertEqual(code, 1)
            self.assertIn("NSE holiday: Diwali muhurat", text)
            self.assertNotIn("junk", alerts.EXTRA_HOLIDAYS)
            alerts.load_holidays(os.path.join(d, "none"))          # no file: the extras are cleared
            self.assertEqual(alerts.EXTRA_HOLIDAYS, {})
        finally:
            alerts.EXTRA_HOLIDAYS.clear()
            shutil.rmtree(d)


class Quotes(unittest.TestCase):
    def test_yahoo_fixtures(self):
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "RELIANCE")
        self.assertEqual((q["symbol"], q["price"], q["prev_close"], q["open"]), ("RELIANCE", 1167.4, 1158.0, 1159.0))
        self.assertEqual((q["high"], q["low"], q["currency"], q["source"]), (1182.1, 1155.6, "INR", "yahoo"))
        self.assertEqual(q["time"], "2026-10-29T09:59:03Z")
        # that is 15:29:03 IST, the last minute of the session
        self.assertEqual(alerts.to_local(DT(2026, 10, 29, 9, 59, 3)).strftime("%H:%M:%S"), "15:29:03")
        with open(os.path.join(QDIR, "yahoo-M&M.json")) as fh:
            q = alerts.parse_yahoo(fh.read(), "M&M")
        self.assertEqual((q["symbol"], q["price"], q["prev_close"], q["open"]), ("M&M", 2890.0, 2869.9, 2869.89990234375))
        with open(os.path.join(QDIR, "yahoo-INFY.json")) as fh:
            self.assertEqual(alerts.parse_yahoo(fh.read(), "INFY.NS")["symbol"], "INFY")

    def test_a_missing_ticker_is_a_clean_error(self):
        with open(os.path.join(QDIR, "yahoo-ZZZZNOPE.json")) as fh:
            with self.assertRaises(alerts.AlertError) as cm:
                alerts.parse_yahoo(fh.read(), "ZZZZNOPE")
        self.assertIn("No data found", str(cm.exception))
        with self.assertRaises(alerts.AlertError):
            alerts.parse_yahoo("<html>", "TCS")

    def test_bare_symbols(self):
        self.assertEqual([alerts.bare(x) for x in ("reliance", "TCS.NS", " m&m ", "bajaj-auto.ns", None)],
                         ["RELIANCE", "TCS", "M&M", "BAJAJ-AUTO", ""])

    def test_urls_and_user_agent(self):
        self.assertEqual(alerts.UA, UA)
        self.assertEqual(alerts.yahoo_url("reliance"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1d")
        self.assertEqual(alerts.yahoo_url("M&M.NS"),
                         "https://query2.finance.yahoo.com/v8/finance/chart/M%26M.NS?range=1d&interval=1d")
        self.assertIn("/BAJAJ-AUTO.NS?", alerts.yahoo_url("BAJAJ-AUTO"))
        self.assertIn("//query1.finance.yahoo.com/", alerts.yahoo_url("TCS", alerts.YAHOO_HOSTS[1]))

    def test_offline_quotes_and_errors(self):
        q, errors = alerts.get_quotes(["RELIANCE", "tcs.ns", "M&M", "ZZZZNOPE", "NOPE"], QDIR)
        self.assertEqual(sorted(q), ["M&M", "RELIANCE", "TCS"])
        self.assertEqual(q["TCS"]["price"], 2478.0)
        self.assertEqual(len(errors), 2)
        self.assertIn("No data found", errors[0])
        self.assertIn("no offline quote file", errors[1])

    def test_offline_gz(self):
        d = tempfile.mkdtemp()
        try:
            with open(os.path.join(QDIR, "yahoo-RELIANCE.json"), "rb") as src:
                with gzip.open(os.path.join(d, "yahoo-RELIANCE.json.gz"), "wb") as dst:
                    dst.write(src.read())
            q, errors = alerts.get_quotes(["RELIANCE"], d)
            self.assertEqual((q["RELIANCE"]["price"], errors), (1167.4, []))
        finally:
            shutil.rmtree(d)


class FakeNet(object):
    """Stands in for urllib: answers each request with the next status code."""

    def __init__(self, codes, body=None):
        self.codes, self.calls, self.sleeps = list(codes), [], []
        with open(os.path.join(QDIR, "yahoo-RELIANCE.json")) as fh:
            self.body = body or fh.read()

    def urlopen(self, req, timeout=None):
        self.calls.append((req.full_url.split("/")[2], req.get_header("User-agent"), req.full_url))
        code = self.codes.pop(0)
        if code != 200:
            fp = io.BytesIO(b'{"chart":{"result":null,"error":{"code":"Not Found",'
                            b'"description":"No data found, symbol may be delisted"}}}' if code == 404 else b"")
            raise urllib.error.HTTPError(req.full_url, code, "x", {}, fp)
        return io.BytesIO(self.body.encode("utf-8"))


class Network(unittest.TestCase):
    def setUp(self):
        self.saved = (alerts._urlopen, alerts._sleep)

    def tearDown(self):
        alerts._urlopen, alerts._sleep = self.saved

    def net(self, codes):
        f = FakeNet(codes)
        alerts._urlopen = f.urlopen
        alerts._sleep = f.sleeps.append
        return f

    def hosts(self, f):
        return [h.split(".")[0] for h, _, _ in f.calls]

    def test_query2_first_with_the_contract_user_agent_and_ns_suffix(self):
        f = self.net([200])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual((q["RELIANCE"]["price"], errors), (1167.4, []))
        self.assertEqual(f.calls, [("query2.finance.yahoo.com", UA,
                                    "https://query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS?range=1d&interval=1d")])

    def test_an_ampersand_symbol_is_url_quoted(self):
        f = self.net([200])
        alerts.get_quotes(["M&M"])
        self.assertIn("/chart/M%26M.NS?", f.calls[0][2])

    def test_one_retry_with_a_pause_on_429(self):
        f = self.net([429, 200])
        self.assertEqual(alerts.get_quotes(["RELIANCE"])[0]["RELIANCE"]["price"], 1167.4)
        self.assertEqual(self.hosts(f), ["query2", "query2"])
        self.assertEqual(f.sleeps, [alerts.BACKOFF])

    def test_falls_back_to_query1_on_429_and_5xx(self):
        f = self.net([429, 429, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query2", "query1"])
        f = self.net([503, 200])
        self.assertIn("RELIANCE", alerts.get_quotes(["RELIANCE"])[0])
        self.assertEqual(self.hosts(f), ["query2", "query1"])
        f = self.net([500, 502])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertEqual(q, {})
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart/RELIANCE.NS answered HTTP 502", errors[0])

    def test_a_404_is_yahoos_answer_and_a_403_is_not_retried(self):
        f = self.net([404])
        q, errors = alerts.get_quotes(["NOPE"])
        self.assertIn("No data found", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])
        f = self.net([403])
        q, errors = alerts.get_quotes(["RELIANCE"])
        self.assertIn("HTTP 403", errors[0])
        self.assertEqual(self.hosts(f), ["query2"])

    def test_a_pause_between_tickers(self):
        f = self.net([200, 200])
        alerts.get_quotes(["RELIANCE", "TCS"])
        self.assertEqual(f.sleeps, [alerts.PAUSE])


class Rules(unittest.TestCase):
    def setUp(self):
        self.q, _ = alerts.get_quotes(["HDFCBANK", "INFY", "RELIANCE", "TCS"], QDIR)
        self.open = alerts.market_status(ist("2026-10-13T10:00"))
        self.now = ist("2026-10-13T10:00")

    def at(self, text, rules, state=None, quotes=None):
        now = ist(text)
        return alerts.evaluate(rules, self.q if quotes is None else quotes, alerts.market_status(now),
                               state or {}, now)

    def test_a_target_rings_once(self):
        rules = [{"id": "tcs-2450", "symbol": "TCS", "type": "above", "price": 2450}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)
        self.assertEqual(len(rings), 1)
        self.assertIn("TCS is \u20b92,478.00: at or above your alert level \u20b92,450.00", rings[0]["message"])
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("$", json.dumps(rings))

    def test_symbols_in_rules_may_carry_ns(self):
        rules = [{"id": "t", "symbol": "tcs.ns", "type": "above", "price": 2450}]
        self.assertEqual(len(alerts.evaluate(rules, self.q, self.open, {}, self.now)[0]), 1)

    def test_stop_and_rearm(self):
        rules = [{"id": "hdfc-stop", "symbol": "HDFCBANK", "type": "stop", "price": 685, "rearm": True}]
        rings, st = alerts.evaluate(rules, self.q, self.open, {}, self.now)           # 683.30 <= 685
        self.assertIn("your stop \u20b9685.00", rings[0]["message"])
        self.q["HDFCBANK"]["price"] = 686.0
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(rings, [])
        self.assertNotIn("hdfc-stop", st["rung"])
        self.q["HDFCBANK"]["price"] = 684.5
        rings, st = alerts.evaluate(rules, self.q, self.open, st, self.now)
        self.assertEqual(len(rings), 1)

    def test_pct_move_from_previous_close(self):
        big = [{"id": "hdfc-3", "symbol": "HDFCBANK", "type": "pct_move", "pct": 3}]
        self.assertEqual(alerts.evaluate(big, self.q, self.open, {}, self.now)[0], [])
        small = [{"id": "hdfc-1", "symbol": "HDFCBANK", "type": "pct_move", "pct": 0.25, "direction": "down"}]
        rings, st = alerts.evaluate(small, self.q, self.open, {}, self.now)
        self.assertIn("-0.29%", rings[0]["message"])                  # 683.30 vs 685.30
        self.assertIn("\u20b9683.30 (from \u20b9685.30)", rings[0]["message"])
        self.assertEqual(alerts.evaluate(small, self.q, self.open, st, self.now)[0], [])
        up = [{"id": "hdfc-up", "symbol": "HDFCBANK", "type": "pct_move", "pct": 0.25, "direction": "up"}]
        self.assertEqual(alerts.evaluate(up, self.q, self.open, {}, self.now)[0], [])
        infy = [{"id": "infy-2", "symbol": "INFY", "type": "pct_move", "pct": 2}]
        rings, _ = self.at("2026-10-26 10:52", infy)
        self.assertIn("INFY moved +2.08% to \u20b91,146.50 (from \u20b91,123.15)", rings[0]["message"])
        ref = [{"id": "infy-ref", "symbol": "INFY", "type": "pct_move", "pct": 5, "ref": 1000}]
        self.assertIn("+14.65%", alerts.evaluate(ref, self.q, self.open, {}, self.now)[0][0]["message"])

    def test_opening_bell_once_per_trading_day(self):
        rules = [{"id": "bell", "type": "opening_bell"}]
        closed = alerts.market_status(ist("2026-10-13T09:00"))
        self.assertEqual(alerts.evaluate(rules, {}, closed, {}, ist("2026-10-13T09:00"))[0], [])
        rings, st = alerts.evaluate(rules, {}, self.open, {}, self.now)
        self.assertEqual(rings[0]["message"], "Opening bell: the NSE is open (2026-10-13 10:00 IST).")
        self.assertEqual(alerts.evaluate(rules, {}, self.open, st, ist("2026-10-13T11:00"))[0], [])
        nxt = alerts.market_status(ist("2026-10-14T09:16"))
        self.assertEqual(len(alerts.evaluate(rules, {}, nxt, st, ist("2026-10-14T09:16"))[0]), 1)

    def test_the_plan_gives_a_stop_a_target_and_flat_by_close(self):
        rules = plan_rules()
        self.assertEqual([(r["id"], r["type"]) for r in rules],
                         [("reliance-plan-stop", "stop"), ("reliance-plan-target", "target"),
                          ("reliance-flat-by", "time")])
        self.assertEqual((rules[0]["price"], rules[1]["price"]), (1155.0, 1176.0))
        self.assertEqual((rules[2]["at"], rules[2]["label"]), ("15:15", "flat by close"))
        self.assertNotIn("before_close", rules[2])
        self.assertTrue(all(r["date"] == "2026-10-26" for r in rules))
        rings, st = self.at("2026-10-26 10:52", rules)          # the real RELIANCE quote, 1167.40
        self.assertEqual(rings, [])
        q = json.loads(json.dumps(self.q))
        q["RELIANCE"]["price"] = 1154.9
        rings, st = self.at("2026-10-26 11:30", rules, st, q)
        self.assertEqual([r["id"] for r in rings], ["reliance-plan-stop"])
        self.assertIn("RELIANCE is \u20b91,154.90: at or below your stop \u20b91,155.00 (plan stop: opening-range midpoint)",
                      rings[0]["message"])
        q["RELIANCE"]["price"] = 1176.0
        rings = self.at("2026-10-26 12:00", rules, st, q)[0]
        self.assertEqual([r["id"] for r in rings], ["reliance-plan-target"])
        self.assertIn("(plan target: 2R (two times the risk))", rings[0]["message"])

    def test_plan_falls_back_to_config_flat_by_then_1515(self):
        plan = {"symbol": "tcs.ns", "stop": 2400, "target": 2500}
        self.assertEqual(alerts.rules_from_plan(plan)[2]["at"], "15:15")
        self.assertEqual(alerts.rules_from_plan(plan, "14:45")[2]["at"], "14:45")
        self.assertEqual(alerts.rules_from_plan(dict(plan, flat_by="15:00"), "14:45")[2]["at"], "15:00")
        self.assertEqual(alerts.rules_from_plan(plan)[0]["symbol"], "TCS")
        self.assertNotIn("date", alerts.rules_from_plan(plan)[0])
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan(dict(plan, flat_by="3pm"))

    def test_flat_by_close_rings_once_per_trading_day(self):
        rules = plan_rules()[2:]
        self.assertEqual(self.at("2026-10-26 15:14", rules)[0], [])
        rings, st = self.at("2026-10-26 15:15", rules)
        self.assertEqual(len(rings), 1)
        self.assertIn("Flat by close: it is 15:15 IST (set for 15:15), the market closes at 15:30", rings[0]["message"])
        self.assertIn("trader-broker flatten --symbol RELIANCE", rings[0]["message"])
        self.assertEqual(self.at("2026-10-26 15:25", rules, st)[0], [])
        rings, _ = self.at("2026-10-26 15:40", rules)
        self.assertIn("the market has closed", rings[0]["message"])
        self.assertEqual(self.at("2026-10-27 15:16", rules, st)[0], [])     # plan rules: their day only

    def test_a_time_alert_has_no_early_close_and_skips_closed_days(self):
        rule = [{"id": "flat", "type": "time", "at": "15:15", "label": "flat by close"}]
        self.assertEqual(self.at("2026-12-24 12:55", rule)[0], [])         # no early close in India
        self.assertEqual(len(self.at("2026-12-24 15:15", rule)[0]), 1)
        self.assertEqual(self.at("2026-10-17 15:16", rule)[0], [])         # Saturday: no session
        self.assertEqual(self.at("2026-10-20 15:16", rule)[0], [])         # Dussehra
        rings, st = self.at("2026-12-01 15:15", rule)
        self.assertEqual(len(rings), 1)
        self.assertEqual(st["rung"]["flat"], "2026-12-01T09:45:00Z")       # 15:15 IST
        # rung at 23:50 IST on a day is still "that IST day", and a rule rings again the next trading day
        self.assertEqual(len(self.at("2026-12-02 15:15", rule, st)[0]), 1)

    def test_the_example_rules_are_valid(self):
        with open(os.path.join(ROOT, "trader-alerts", "alerts.example.json")) as fh:
            rules = json.load(fh)["rules"]
        for r in rules:
            alerts.validate(r)
        self.assertEqual([r for r in rules if r["id"].startswith("reliance-")],
                         plan_rules())

    def test_bad_rules(self):
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "moon"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "above", "symbol": "TCS"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.evaluate([{"id": "x", "type": "time", "at": "3pm"}], {}, self.open, {}, self.now)
        with self.assertRaises(alerts.AlertError):
            alerts.rules_from_plan({"symbol": "RELIANCE", "stop": 1155})


class Cli(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.rules = os.path.join(self.d, "alerts.json")
        self.state = os.path.join(self.d, "state", "alerts.json")
        self.prices = os.path.join(self.d, "prices.json")
        self.none = os.path.join(self.d, "none.json")

    def tearDown(self):
        shutil.rmtree(self.d)

    def check(self, now):
        return run(["check", "--rules", self.rules, "--state", self.state, "--prices-out", self.prices,
                    "--offline", QDIR, "--now", now, "--config", self.none])

    def test_the_reel_trade_from_the_plan(self):
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", self.none])
        self.assertEqual(code, 0)
        self.assertIn("Added reliance-flat-by", text)
        self.assertEqual(run(["add", "--rules", self.rules, "--opening-bell", "--config", self.none])[0], 0)

        code, text, _ = self.check("2026-10-26 10:52")
        self.assertEqual(code, 0)                                      # the opening bell rings first
        code, text, _ = self.check("2026-10-26 10:53")
        self.assertEqual(code, 1)
        self.assertIn("NSE OPEN (2026-10-26 10:53 IST, regular session).", text)
        self.assertIn("Nothing hit. 4 rule(s) watched today, RELIANCE \u20b91,167.40.", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"RELIANCE": 1167.4})
        self.assertEqual(doc["detail"]["RELIANCE"]["time"], "2026-10-29T09:59:03Z")
        self.assertEqual(doc["detail"]["RELIANCE"]["fetched"], "2026-10-26T05:23:00Z")      # 10:53 IST
        self.assertEqual(doc["detail"]["RELIANCE"]["prev_close"], 1158.0)
        self.assertEqual((doc["as_of"], doc["source"]), ("2026-10-26T05:23:00Z", "yahoo"))
        self.assertEqual(sorted(doc), ["as_of", "detail", "note", "prices", "source"])

        code, text, _ = self.check("2026-10-26 15:15")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Flat by close", text)
        self.assertIn("not an order", text)
        self.assertEqual(self.check("2026-10-26 15:17")[0], 1)
        code, text, _ = self.check("2026-10-27 10:00")
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-27 10:05")
        self.assertIn("Nothing hit. 1 rule(s) watched today.", text)

        # a new plan replaces the plan rules and re-arms them
        code, text, _ = run(["add", "--from-plan", "--plan", PLAN, "--rules", self.rules, "--state", self.state,
                             "--config", self.none])
        with open(self.rules) as fh:
            ids = [r["id"] for r in json.load(fh)["rules"]]
        self.assertEqual(sorted(ids), ["opening-bell", "reliance-flat-by", "reliance-plan-stop", "reliance-plan-target"])
        with open(self.state) as fh:
            self.assertNotIn("reliance-flat-by", json.load(fh)["rung"])

    def test_the_portfolio_evening_and_morning(self):
        for args in (["--opening-bell"], ["--symbol", "TCS", "--above", "2500"], ["--symbol", "hdfcbank.ns", "--stop", "670"]):
            code, _, _ = run(["add", "--rules", self.rules, "--config", self.none] + args)
            self.assertEqual(code, 0)
        code, _, err = run(["add", "--rules", self.rules, "--symbol", "TCS", "--above", "2500", "--config", self.none])
        self.assertEqual(code, 1)
        self.assertIn("already exists", err)

        code, text, _ = self.check("2026-10-13T15:35")                 # after the close
        self.assertEqual(code, 1)
        self.assertIn("Nothing hit", text)
        self.assertIn("NSE closed", text)
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(doc["prices"], {"HDFCBANK": 683.3, "TCS": 2478.0})

        code, text, _ = self.check("2026-10-14T09:16")
        self.assertEqual(code, 0)
        self.assertIn("ALERT Opening bell", text)
        code, text, _ = self.check("2026-10-14T09:45")
        self.assertEqual(code, 1)

        code, text, _ = run(["list", "--rules", self.rules, "--state", self.state])
        self.assertIn("rang", text)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 0)
        self.assertEqual(run(["reset", "opening-bell", "--state", self.state])[0], 1)

    def test_add_time_and_target(self):
        code, text, _ = run(["add", "--rules", self.rules, "--at", "15:15", "--label", "flat by close",
                             "--symbol", "reliance", "--config", self.none])
        self.assertEqual(code, 0)
        code, text, _ = run(["add", "--rules", self.rules, "--symbol", "RELIANCE", "--target", "1176",
                             "--date", "2026-10-26", "--config", self.none])
        self.assertEqual(code, 0)
        with open(self.rules) as fh:
            rules = json.load(fh)["rules"]
        self.assertEqual(rules[0], {"id": "time-1515", "type": "time", "at": "15:15", "label": "flat by close",
                                    "symbol": "RELIANCE"})
        self.assertEqual(rules[1], {"id": "reliance-target-1176", "symbol": "RELIANCE", "type": "target",
                                    "price": 1176.0, "date": "2026-10-26"})
        self.assertEqual(run(["add", "--rules", self.rules, "--at", "3pm", "--config", self.none])[0], 1)
        code, _, err = run(["add", "--rules", self.rules, "--from-plan", "--plan", os.path.join(self.d, "none"),
                            "--config", self.none])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)

    def test_before_close_flag_is_gone(self):
        with self.assertRaises(SystemExit):
            run(["add", "--at", "15:15", "--before-close"])

    def test_quote_write_merges(self):
        code, text, _ = run(["quote", "RELIANCE", "TCS.NS", "--offline", QDIR, "--write", self.prices,
                             "--now", "2026-10-29T09:59:03Z", "--config", self.none])
        self.assertEqual(code, 0)
        self.assertIn("RELIANCE   \u20b91,167.40 (+0.81% vs prev close \u20b91,158.00)", text)
        self.assertIn("delayed", text)
        self.assertNotIn("$", text)
        run(["quote", "M&M", "--offline", QDIR, "--write", self.prices, "--config", self.none])
        with open(self.prices) as fh:
            doc = json.load(fh)
        self.assertEqual(sorted(doc["prices"]), ["M&M", "RELIANCE", "TCS"])
        self.assertEqual(doc["detail"]["TCS"]["prev_close"], 2460.0)
        self.assertEqual(doc["detail"]["TCS"]["fetched"], "2026-10-29T09:59:03Z")
        self.assertIn("rupees", doc["note"])

    def test_quote_errors_exit_1(self):
        code, text, _ = run(["quote", "ZZZZNOPE", "--offline", QDIR, "--config", self.none])
        self.assertEqual(code, 1)
        self.assertIn("error:", text)
        self.assertEqual(run(["quote", "--config", self.none])[0], 1)

    def test_quote_holdings_from_portfolio_and_plan(self):
        pf = os.path.join(self.d, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"cash": 0, "positions": [{"ticker": "tcs.ns", "shares": 1}]}, fh)
        code, text, _ = run(["quote", "--holdings", "--portfolio", pf, "--plan", PLAN, "--offline", QDIR, "--json",
                             "--config", self.none])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(json.loads(text)["quotes"]), ["RELIANCE", "TCS"])

    def test_clock_exit_codes(self):
        self.assertEqual(run(["clock", "--now", "2026-10-26 10:15", "--config", self.none])[0], 0)
        code, text, _ = run(["clock", "--now", "2026-12-25T10:00", "--config", self.none])
        self.assertEqual(code, 1)
        self.assertIn("NSE holiday: Christmas", text)
        code, text, _ = run(["clock", "--now", "2027-02-01T10:00", "--config", self.none])
        self.assertIn("covers 2026 only: check nseindia.com", text)
        code, text, _ = run(["clock", "--now", "2026-10-26 15:30", "--config", self.none])
        self.assertIn("Next open: 2026-10-27 09:15 IST.", text)
        code, text, _ = run(["clock", "--now", "2026-10-26 10:00", "--json", "--config", self.none])
        self.assertTrue(json.loads(text)["open"])

    def test_check_without_rules(self):
        code, _, err = run(["check", "--rules", self.rules, "--offline", QDIR, "--config", self.none])
        self.assertEqual(code, 1)
        self.assertIn("no rules", err)


if __name__ == "__main__":
    unittest.main()
