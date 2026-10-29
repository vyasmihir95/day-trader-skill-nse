import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from _load import FIX, load

news = load("trader-news", "news")
DIR = os.path.join(FIX, "trader-news")
UNI = os.path.join(DIR, "universe.txt")
NOW = "2026-10-29T15:40:00+00:00"  # 21:10 IST on Thursday 29 Oct 2026, after the close
WATCH = {"RELIANCE": "Reliance Industries Limited", "TCS": "Tata Consultancy Services Limited",
         "M&M": "Mahindra & Mahindra Limited"}


def raw(name):
    with open(os.path.join(DIR, name), "rb") as fh:
        return fh.read()


def run(args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = news.main(args)
    return code, out.getvalue(), err.getvalue()


def item(title, ticker="RELIANCE", summary="", when=None, id_="x", link=""):
    return {"id": id_, "source": "Test", "kind": "headline", "ticker": ticker, "title": title,
            "summary": summary, "link": link, "time": when}


class Feeds(unittest.TestCase):
    def test_google_news_rss(self):
        items = news.parse_google(raw("google-RELIANCE.xml"), "RELIANCE")
        self.assertEqual(len(items), 100)
        first = items[0]
        # the ' - CNBC' publisher tail is split off the title
        self.assertEqual(first["title"],
                         "Billionaire Ambani cries tears of joy as Jio Platforms becomes the second most valued Indian firm")
        self.assertEqual(first["source"], "CNBC")
        self.assertEqual(first["time"], datetime.datetime(2026, 10, 29, 4, 55, 17,
                                                          tzinfo=datetime.timezone.utc))
        self.assertTrue(first["link"].startswith("https://news.google.com/rss/articles/"))
        self.assertTrue(first["id"].startswith("gn:"))
        self.assertEqual(first["ticker"], "RELIANCE")

    def test_market_feeds(self):
        mint = news.parse_feed(raw("feed-mint.xml"), "Mint")
        self.assertEqual(len(mint), 35)
        self.assertEqual(mint[0]["source"], "Mint")
        self.assertEqual(mint[0]["ticker"], "")
        # Mint prints +0530: 20:56:57 IST is 15:26:57 UTC
        self.assertEqual(mint[0]["time"].isoformat(), "2026-10-29T15:26:57+00:00")
        self.assertTrue(mint[0]["title"].startswith("Alphabet shares drop 13.5%"))
        mc = news.parse_feed(raw("feed-moneycontrol.xml"), "Moneycontrol")
        self.assertEqual(len(mc), 15)
        self.assertNotIn("<img", mc[0]["summary"])  # HTML is stripped from descriptions
        self.assertIn("VNB fell 19.45 percent", mc[0]["summary"])

    def test_a_bare_ampersand_does_not_kill_a_feed(self):
        # Business Standard's real feed has "Bombay Coated & Special Steels" unescaped
        self.assertIn(b"Bombay Coated & Special Steels", raw("feed-business-standard.xml"))
        bs = news.parse_feed(raw("feed-business-standard.xml"), "Business Std")
        self.assertEqual(len(bs), 35)
        self.assertTrue(any("Bombay Coated & Special Steels" in i["summary"] + i["title"] for i in bs)
                        or bs[0]["title"].startswith("Sensex, Nifty rise over 1%"))
        with self.assertRaises(news.NewsError):
            news.parse_feed(b"<rss><channel><item>", "Mint")

    def test_times(self):
        self.assertEqual(news.parse_time("Thu, 29 Oct 2026 20:56:57 +0530").hour, 15)
        self.assertEqual(news.parse_time("Thu, 29 Oct 2026 04:55:17 GMT").hour, 4)
        self.assertEqual(news.parse_time("2026-10-29T20:56:57+05:30").hour, 15)
        self.assertIsNone(news.parse_time("soon"))

    def test_symbols(self):
        self.assertEqual(news.bare("reliance.ns"), "RELIANCE")
        self.assertEqual(news.bare("M&M"), "M&M")
        self.assertEqual(news.yahoo_symbol("RELIANCE"), "RELIANCE.NS")
        self.assertEqual(news.yahoo_symbol("M&M"), "M%26M.NS")
        self.assertEqual(news.yahoo_symbol("BAJAJ-AUTO.NS"), "BAJAJ-AUTO.NS")

    def test_google_query(self):
        self.assertEqual(news.short_name("Reliance Industries Limited"), "Reliance Industries")
        self.assertEqual(news.short_name("Mahindra & Mahindra Ltd."), "Mahindra & Mahindra")
        url = news.google_url("M&M", "Mahindra & Mahindra Limited", 48)
        self.assertIn("q=%22Mahindra+%26+Mahindra%22+OR+%22M%26M%22+when%3A48h", url)
        self.assertIn("hl=en-IN&gl=IN&ceid=IN:en", url)
        self.assertIn("when%3A5d", news.google_url("TCS", None, 100))
        # no company name: the symbol itself is searched
        self.assertIn("q=%22TCS%22+NSE+when%3A", news.google_url("TCS", None, 24))


class Universe(unittest.TestCase):
    def test_names_from_the_universe_file(self):
        uni = news.load_universe(UNI)
        self.assertEqual(uni["M&M"], "Mahindra & Mahindra Limited")
        self.assertEqual(uni["RELIANCE"], "Reliance Industries Limited")
        self.assertEqual(news.load_universe("/nonexistent.txt"), {})


class Tagging(unittest.TestCase):
    def test_touches_and_tags(self):
        items = news.parse_google(raw("google-RELIANCE.xml"), "RELIANCE")
        items += news.parse_google(raw("google-TCS.xml"), "TCS")
        news.tag(items, WATCH)
        by = {i["title"][:30]: i for i in items}
        rank = by["Jio changes market rankings: T"]
        self.assertEqual(rank["touches"], ["TCS"])  # "Reliance Group" is not "Reliance Industries"
        both = news.tag([item("Reliance Industries and TCS lead the Nifty higher", "TCS")], WATCH)[0]
        self.assertEqual(both["touches"], ["RELIANCE", "TCS"])
        board = by["Reliance Industries board to c"]
        self.assertEqual(board["tags"], ["board", "capital"])
        self.assertEqual(by["Reliance Industries seen posti"]["tags"], ["results"])

    def test_symbol_matching_is_strict(self):
        pats = news.mention_patterns({"TCS": None, "M&M": "Mahindra & Mahindra Limited"})
        sym = lambda t, s: bool(pats[t][1].search(s))
        self.assertTrue(sym("TCS", "Mitessh Thakkar recommends TCS, L&T and KPIT Tech"))
        self.assertFalse(sym("TCS", "tcs lowercase is not the symbol"))
        self.assertFalse(sym("TCS", "ETCS and TCSX"))
        self.assertTrue(sym("M&M", "M&M rallies on SUV sales"))
        self.assertFalse(sym("M&M", "M&M's chocolate recall"))
        self.assertFalse(sym("M&M", "ABM&M rallies"))
        name = lambda s: bool(pats["M&M"][0].search(s))
        self.assertTrue(name("Mahindra & Mahindra rises Wednesday"))
        self.assertFalse(name("Kotak Mahindra Bank shares gain"))

    def test_india_tags(self):
        cases = {
            "results": "Infosys Q2FY27 net profit rises 8%",
            "board": "Board meeting on Nov 5 to consider results",
            "dividend": "TCS declares interim dividend of Rs 10, record date Nov 7",
            "order": "L&T bags mega order worth Rs 5,000 crore",
            "sebi": "SEBI issues show-cause notice to promoter",
            "rbi": "RBI keeps repo rate unchanged",
            "block deal": "Promoter sells 2% via block deal",
            "promoter": "Promoter pledge falls to 5%",
            "analyst": "Jefferies upgrades HDFC Bank, target price Rs 2,100",
            "deal": "Tata Motors completes acquisition of Iveco",
            "capital": "Company announces QIP to raise Rs 3,000 crore",
            "legal": "NCLT admits insolvency petition",
            "people": "CEO resigns with immediate effect",
            "risk": "Plant fire; company reports a data breach",
            "halt": "Stock locked in upper circuit; added to F&O ban",
            "regulatory": "USFDA approval for generic drug",
            "macro": "Sensex, Nifty fall as crude rises; FII selling",
        }
        for label, title in cases.items():
            tags = news.tag([item(title, "X")], {"X": None})[0]["tags"]
            self.assertIn(label, tags, "%s: %s" % (label, title))
        wabag = news.tag([item("WABAG Secures Mega Order from Reliance Industries for Jamnagar Plants", "X")],
                         {"X": None})[0]
        self.assertIn("order", wabag["tags"])
        self.assertEqual(news.tag([item("Company opens a new office", "X")], {"X": None})[0]["tags"], [])

    def test_dedupe_merges_the_same_story(self):
        a = news.parse_google(raw("google-TCS.xml"), "TCS")
        b = news.parse_google(raw("google-TCS.xml"), "RELIANCE")
        items = news.dedupe(news.tag(a + b, {"TCS": None, "RELIANCE": None}))
        self.assertEqual(len(items), 78)  # 81 results, 3 repeat a title on the same day
        self.assertEqual(items[0]["touches"], ["RELIANCE", "TCS"])

    def test_dedupe_same_title_same_day_different_outlet(self):
        day = datetime.datetime(2026, 10, 29, 5, 0, tzinfo=datetime.timezone.utc)
        a = item("TCS wins order!", "TCS", when=day, id_="a", link="http://a")
        b = item("TCS wins   order", "INFY", when=day, id_="b", link="http://b")
        c = item("TCS wins order", "TCS", when=day + datetime.timedelta(days=1), id_="c", link="http://c")
        out = news.dedupe(news.tag([a, b, c], {"TCS": None, "INFY": None}))
        self.assertEqual([i["id"] for i in out], ["a", "c"])
        self.assertEqual(out[0]["touches"], ["INFY", "TCS"])


class Check(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state", "news.json")
        self.base = ["--offline", DIR, "--universe", UNI, "--state", self.state, "--now", NOW,
                     "--portfolio", os.path.join(self.tmp.name, "none.json"),
                     "--watchlist", os.path.join(self.tmp.name, "none.json"),
                     "--plan", os.path.join(self.tmp.name, "none.json")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_items_once_newest_first(self):
        args = ["--tickers", "RELIANCE", "TCS", "M&M", "--hours", "24", "--json"] + self.base
        code, out, err = run(args)
        self.assertEqual(code, 0, err)
        data = json.loads(out)
        times = [i["time"] for i in data["items"]]
        self.assertEqual(times, sorted(times, reverse=True))
        self.assertEqual(len(data["items"]), 38)
        self.assertTrue(all(times[k] >= "2026-10-28T15:40:00" for k in range(len(times))))
        self.assertEqual(data["notes"], [])
        code, out, _ = run(["--tickers", "RELIANCE", "TCS", "M&M", "--hours", "24"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("Nothing new.", out)

    def test_a_wider_window_and_all(self):
        args = ["--tickers", "RELIANCE", "TCS", "M&M"]
        code, out, err = run(args + ["--hours", "48", "--json", "--dry-run"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads(out)["items"]), 52)  # 38 in the last 24 hours, 14 before
        run(args + ["--hours", "24"] + self.base)  # a check remembers every item it saw, in the window or not
        code, out, _ = run(args + ["--hours", "48", "--json", "--dry-run"] + self.base)
        self.assertEqual((code, json.loads(out)["items"]), (1, []))
        code, out, _ = run(args + ["--hours", "48", "--all", "--json", "--dry-run"] + self.base)
        data = json.loads(out)
        self.assertEqual(len(data["items"]), 52)
        self.assertEqual(code, 1)  # --all shows seen items, but none is new
        self.assertEqual(sum(1 for i in data["items"] if i["new"]), 0)

    def test_output_in_ist_with_rupees(self):
        code, out, err = run(["--tickers", "RELIANCE", "--hours", "48"] + self.base)
        self.assertEqual(code, 0, err)
        # 15:23:01 UTC is 20:53 IST
        self.assertIn("2026-10-28 20:53 IST  RELIANCE", out)
        self.assertIn("Checked 2026-10-29 21:10 IST", out)
        self.assertIn("₹", out)
        self.assertNotIn("$", out.split("Checked")[0].split("\n")[0])  # the price line has no dollar sign
        self.assertIn("no NSE or BSE filings are read", out)
        self.assertIn("Not financial advice", out)
        self.assertNotIn(chr(0x2014), out)
        line = [l for l in out.splitlines() if "double-digit Q2FY27 EBITDA" in l]
        self.assertEqual(len(line), 1)
        self.assertIn("BusinessLine", line[0])
        tags = out.splitlines()[out.splitlines().index(line[0]) + 1]
        self.assertIn("tags: results", tags)
        board = out.splitlines()[[i for i, l in enumerate(out.splitlines()) if "board to consider fund-raise" in l][0] + 1]
        self.assertIn("tags: board, capital", board)

    def test_the_pinned_clock_hides_later_items(self):
        args = list(self.base)
        args[args.index("--now") + 1] = "2026-10-29T05:00:00+00:00"  # 10:30 IST
        code, out, _ = run(["--tickers", "RELIANCE", "--hours", "48", "--json", "--dry-run", "--no-moves"] + args)
        data = json.loads(out)
        self.assertEqual(data["checked"], "2026-10-29T05:00:00+00:00")
        self.assertTrue(all(i["time"] <= "2026-10-29T05:10:00+00:00" for i in data["items"]))
        self.assertEqual(len(data["items"]), 12)  # RELIANCE stories from 10:30 IST on 27 Oct to the pinned 10:30 IST

    def test_feed_stories_naming_your_stocks_are_merged_and_deduped(self):
        # TCS: the Mint feed story "TCS, Infosys, HCL Tech: Anthropic revenue soars..." is also in the
        # Google results (as Livemint): one row, not two
        code, out, _ = run(["--tickers", "TCS", "--hours", "48", "--json", "--dry-run"] + self.base)
        rows = [i for i in json.loads(out)["items"] if i["title"].startswith("TCS, Infosys, HCL Tech: Anthropic")]
        self.assertEqual(len(rows), 1)
        # without the Google file it comes from the Mint feed alone (and only stories that name TCS)
        d = os.path.join(self.tmp.name, "only-feeds")
        os.makedirs(d)
        for f in ("feed-mint.xml", "feed-business-standard.xml", "feed-moneycontrol.xml", "chart-TCS.json"):
            shutil.copy(os.path.join(DIR, f), d)
        args = list(self.base)
        args[args.index("--offline") + 1] = d
        code, out, _ = run(["--tickers", "TCS", "--hours", "48", "--json", "--dry-run"] + args)
        data = json.loads(out)
        titles = [i["title"] for i in data["items"]]
        self.assertEqual(len(titles), 1)
        self.assertTrue(titles[0].startswith("TCS, Infosys, HCL Tech: Anthropic"))
        self.assertEqual(data["items"][0]["source"], "Mint")
        self.assertEqual(data["items"][0]["touches"], ["TCS"])
        self.assertTrue(any("Google" in n or "google-TCS" in n for n in data["notes"]))
        # --no-feeds drops them
        code, out, _ = run(["--tickers", "TCS", "--hours", "48", "--json", "--dry-run", "--no-feeds"] + args)
        self.assertEqual(json.loads(out)["items"], [])

    def test_tickers_from_watchlist_plan_and_universe(self):
        wl = os.path.join(self.tmp.name, "watchlist.json")
        with open(wl, "w") as fh:
            json.dump({"created_by": "trader-scanner",
                       "hits": [{"symbol": "TCS", "setups": ["orb"]}, {"symbol": "RELIANCE.NS"}]}, fh)
        plan = os.path.join(self.tmp.name, "plan.json")
        with open(plan, "w") as fh:
            json.dump({"created_by": "trader-exit", "symbol": "M&M"}, fh)
        got = news.watch_from_files("/nonexistent.json", wl, plan, UNI)
        self.assertEqual(sorted(got), ["M&M", "RELIANCE", "TCS"])
        self.assertEqual(got["TCS"], "Tata Consultancy Services Limited")  # name from universe.txt
        args = list(self.base)
        args[args.index("--watchlist") + 1] = wl
        args[args.index("--plan") + 1] = plan
        code, out, err = run(["--json", "--dry-run", "--hours", "48"] + args)
        self.assertEqual(code, 0, err)
        tickers = {t for i in json.loads(out)["items"] for t in i["touches"]}
        self.assertEqual(tickers, {"M&M", "RELIANCE", "TCS"})
        self.assertFalse(os.path.exists(self.state))  # --dry-run leaves no state

    def test_portfolio_positions_are_watched(self):
        pf = os.path.join(self.tmp.name, "portfolio.json")
        with open(pf, "w") as fh:
            json.dump({"positions": [{"symbol": "INFY", "name": "Infosys Limited"}, {"ticker": "TCS"}]}, fh)
        got = news.watch_from_files(pf, "/none.json", "/none.json", UNI)
        self.assertEqual(got, {"INFY": "Infosys Limited", "TCS": "Tata Consultancy Services Limited"})

    def test_no_tickers_is_an_error(self):
        code, _, err = run(self.base)
        self.assertEqual(code, 2)
        self.assertIn("no tickers", err)

    def test_typed_ns_suffix_is_accepted(self):
        code, out, err = run(["--tickers", "reliance.ns", "--hours", "24", "--json", "--dry-run"] + self.base)
        self.assertEqual(code, 0, err)
        self.assertEqual({i["ticker"] for i in json.loads(out)["items"]}, {"RELIANCE"})

    def test_a_failing_source_degrades_to_a_note(self):
        d = os.path.join(self.tmp.name, "broken")
        os.makedirs(d)
        with open(os.path.join(d, "google-TCS.xml"), "w") as fh:
            fh.write("<html>blocked</html")
        shutil.copy(os.path.join(DIR, "google-RELIANCE.xml"), d)
        with open(os.path.join(d, "chart-TCS.json"), "w") as fh:
            fh.write("not json")
        args = list(self.base)
        args[args.index("--offline") + 1] = d
        code, out, err = run(["--tickers", "RELIANCE", "TCS", "--hours", "24", "--dry-run"] + args)
        self.assertEqual(code, 0, err)
        self.assertIn("note: headlines for TCS:", out)
        self.assertIn("note: price for TCS: chart for TCS is not usable", out)
        self.assertIn("RELIANCE", out)

    def test_offline_missing_files(self):
        # no google file for a symbol: a note; no chart file offline: no move line, no note
        code, out, _ = run(["--tickers", "INFY", "--hours", "24", "--dry-run"] + self.base)
        self.assertEqual(code, 1)
        self.assertIn("note: headlines for INFY: offline: no file", out)
        self.assertNotIn("price for INFY", out)
        self.assertNotIn("vs previous close", out)
        self.assertIn("Nothing new.", out)
        with self.assertRaises(news.NewsError):
            news.Fetcher(DIR).get("http://example.invalid", "missing.xml")


class Moves(unittest.TestCase):
    def test_chart_parse(self):
        m = news.parse_chart(raw("chart-RELIANCE.json"), "RELIANCE")
        self.assertEqual(m["price"], 1167.4)
        self.assertEqual(m["prev_close"], 1158.0)
        self.assertEqual(m["pct"], 0.81)  # 1167.40 / 1158.00 - 1
        # regularMarketTime 1793267943 = 09:59:03 UTC = 15:29 IST, the last 5-minute bar of the session
        self.assertEqual(news.to_local(m["time"]).strftime("%H:%M"), "15:29")
        self.assertEqual(news.parse_chart(raw("chart-TCS.json"), "TCS")["pct"], 0.73)  # 2478 / 2460
        mm = news.parse_chart(raw("chart-M&M.json"), "M&M")
        self.assertEqual((mm["price"], mm["prev_close"], mm["pct"]), (2890.0, 2869.9, 0.7))
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": null}}', "ZZZZ")
        with self.assertRaises(news.NewsError):
            news.parse_chart(b'{"chart": {"result": [{"meta": {}}]}}', "ZZZZ")

    def test_render_and_flag(self):
        m = news.parse_chart(raw("chart-RELIANCE.json"), "RELIANCE")
        line = news.render_moves([m])[0]
        self.assertIn("RELIANCE", line)
        self.assertIn("₹1,167.40 +0.81% vs previous close ₹1,158.00 at 15:29 IST", line)
        self.assertNotIn("MOVING", line)
        self.assertIn("MOVING", news.render_moves([m], move_pct=0.5)[0])
        down = dict(m, pct=-3.2)
        self.assertIn("MOVING", news.render_moves([down])[0])  # either direction

    def test_the_price_lines_in_a_check(self):
        base = ["--offline", DIR, "--universe", UNI, "--now", NOW, "--dry-run", "--no-feeds",
                "--portfolio", "/n", "--watchlist", "/n", "--plan", "/n"]
        code, out, _ = run(["--tickers", "RELIANCE", "TCS", "M&M", "--hours", "1"] + base)
        lines = out.splitlines()[:3]
        self.assertTrue(lines[0].startswith("M&M"))
        self.assertTrue(lines[1].startswith("RELIANCE"))
        self.assertIn("₹2,478.00 +0.73% vs previous close ₹2,460.00 at 15:29 IST", lines[2])
        code, out, _ = run(["--tickers", "TCS", "--hours", "1", "--move-pct", "0.5"] + base)
        self.assertIn("MOVING", out.splitlines()[0])
        code, out, _ = run(["--tickers", "TCS", "--hours", "1", "--no-moves"] + base)
        self.assertNotIn("vs previous close", out)
        code, out, _ = run(["--tickers", "TCS", "--hours", "1", "--json"] + base)
        mv = json.loads(out)["moves"][0]
        self.assertEqual((mv["ticker"], mv["pct"], mv["moving"]), ("TCS", 0.73, False))


class TimeAndState(unittest.TestCase):
    def test_ist_is_a_fixed_offset(self):
        for month in (1, 7, 10):  # no daylight saving, ever
            utc = datetime.datetime(2026, month, 15, 3, 45, tzinfo=datetime.timezone.utc)
            self.assertEqual(news.to_local(utc).strftime("%H:%M"), "09:15")
        naive = datetime.datetime(2026, 10, 29, 10, 0)  # naive UTC is accepted too
        self.assertEqual(news.to_local(naive), datetime.datetime(2026, 10, 29, 15, 30))
        local = datetime.datetime(2026, 10, 29, 9, 15)
        self.assertEqual(news.local_to_utc(local).isoformat(), "2026-10-29T03:45:00+00:00")
        self.assertEqual(news.ts_to_local(1793267943).strftime("%H:%M:%S"), "15:29:03")
        # midnight rollover: 19:00 UTC is 00:30 IST the next day
        late = datetime.datetime(2026, 10, 28, 19, 0, tzinfo=datetime.timezone.utc)
        self.assertEqual(news.to_local(late).strftime("%Y-%m-%d %H:%M"), "2026-10-29 00:30")

    def test_state_forgets_after_30_days(self):
        state = {"seen": {"old": "2026-08-01T00:00:00+00:00", "recent": "2026-10-20T00:00:00+00:00"}}
        now = news.parse_time(NOW)
        news.remember([item("fresh", id_="f")], state, now)
        self.assertNotIn("old", state["seen"])
        self.assertIn("recent", state["seen"])
        self.assertEqual(len(state["seen"]), 2)
        self.assertEqual(state["last_check"], now.isoformat())

    def test_select_marks_new_and_respects_the_window(self):
        now = news.parse_time(NOW)
        old = item("old", id_="o", when=now - datetime.timedelta(hours=60))
        fresh = item("fresh", id_="f", when=now - datetime.timedelta(hours=1))
        future = item("future", id_="u", when=now + datetime.timedelta(hours=1))
        undated = item("undated", id_="n")
        state = {}
        shown = news.select([old, fresh, future, undated], state, now, hours=48)
        self.assertEqual([i["title"] for i in shown], ["undated", "fresh"])  # an undated item counts as now
        news.remember(shown, state, now)
        self.assertEqual(news.select([fresh, undated], state, now, hours=48), [])
        self.assertEqual(len(news.select([fresh, undated], state, now, hours=48, show_all=True)), 2)


if __name__ == "__main__":
    unittest.main()
