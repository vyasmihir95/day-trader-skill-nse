import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

check = load("trader-risk", "check")
ticket = load("trader-broker", "ticket")

CFG_PATH = os.path.join(ROOT, "trader-budget", "config.example.json")
RELIANCE_PATH = os.path.join(FIX, "trader-risk", "orders-reliance.json")
BASKET_PATH = os.path.join(FIX, "trader-risk", "orders-basket.json")
NOW = "2026-10-27 10:01"
TODAY = "2026-10-27"
BASKET_TODAY = "2026-10-29"
BASKET_NOW = "2026-10-29 10:01"
# Yahoo closes of 2026-10-29, the basket's prices
PRICES = {"RELIANCE": 1167.4, "TCS": 2478.0, "INFY": 1146.5, "HDFCBANK": 683.3, "M&M": 2890.0}
QUOTE_TIME = datetime.datetime(2026, 10, 27, 4, 52, 13)   # a RELIANCE quote at 10:22:13 IST
# the basket rules: every day-trade check switched off, the account checks still hold
BASKET_RULES = {"budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 100000,
                "min_order_inr": 500, "max_orders_per_day": 10, "require_stop": False, "no_entries_before": None,
                "no_entries_after": None, "max_trades_per_day": 0, "max_daily_loss_inr": 0,
                "max_loss_per_trade_inr": 0, "min_reward_risk": 0}


def at(text):
    return check.parse_now(text)


def cfg(**over):
    with open(CFG_PATH) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def basket_cfg(**over):
    c = dict(BASKET_RULES)
    c.update(over)
    return c


def reliance(**over):
    with open(RELIANCE_PATH) as fh:
        o = json.load(fh)["orders"][0]
    o.update(over)
    return o


def bracket(sym, qty, entry, stop, target):
    return {"symbol": sym, "exchange": "NSE", "side": "buy", "quantity": qty, "product": "MIS",
            "order_type": "MARKET", "validity": "DAY", "stop_loss": stop, "take_profit": target,
            "plan": {"entry": entry, "stop": stop, "target": target}}


def basket_orders():
    with open(BASKET_PATH) as fh:
        return json.load(fh)["orders"]


def buy(sym, qty, entry=None):
    return {"symbol": sym, "exchange": "NSE", "side": "buy", "quantity": qty, "product": "MIS",
            "order_type": "MARKET", "validity": "DAY", "plan": {"entry": entry or PRICES[sym]}}


def sell(sym, qty, **over):
    o = {"symbol": sym, "exchange": "NSE", "side": "sell", "quantity": qty, "product": "MIS",
         "order_type": "MARKET", "validity": "DAY"}
    o.update(over)
    return o


def placed(sym, side="buy", day=TODAY, max_loss=992.0, kind=None, status="placed"):
    r = {"symbol": sym, "side": side, "status": status, "date": day, "at": day + "T10:20:00+05:30"}
    if max_loss is not None:
        r["max_loss"] = max_loss
    if kind:
        r["kind"] = kind
    return r


def verdicts(res):
    return [r["verdict"] for r in res["results"]]


def reasons(res, i=0):
    return " | ".join(res["results"][i]["reasons"])


def warnings(res, i=0):
    return " | ".join(res["results"][i]["warnings"])


class TheReliance(unittest.TestCase):
    def test_the_reliance_bracket_passes_every_check(self):
        res = check.check([reliance()], cfg(), now=at(NOW))
        r = res["results"][0]
        self.assertEqual(r["verdict"], "PASS", r["reasons"])
        self.assertEqual(r["reasons"], [])
        self.assertEqual(r["warnings"], [])
        self.assertEqual((r["kind"], r["entry_ref"], r["stop"], r["target"]), ("entry", 1197.4, 1185.0, 1222.2))
        self.assertEqual((r["risk_per_share"], r["max_loss"], r["reward_risk"]), (12.4, 992.0, 2.0))
        self.assertEqual(r["value_inr"], 95792.0)          # 80 x 1197.40
        self.assertEqual(res["cash_after"], 404208.0)       # 500,000 - 95,792
        self.assertEqual(res["date"], TODAY)
        self.assertEqual(res["now_ist"], "2026-10-27 10:01")
        self.assertIs(res["paper"], True)

    def test_81_shares_is_blocked_by_the_max_loss_only(self):
        res = check.check([reliance(quantity=81)], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["BLOCK"])
        self.assertEqual(res["results"][0]["reasons"],
                         ["loss: 81 shares x 12.40 risk a share = ₹1,004.40 at the stop, "
                          "over your ₹1,000 max loss per trade"])

    def test_fingerprint_is_shared_with_the_broker(self):
        o = reliance()
        self.assertEqual(check.fingerprint(o), ticket.fingerprint(o))
        self.assertEqual(check.fingerprint(dict(o, why="anything", plan={})), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, quantity=81)), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, stop_loss=1184.0)), check.fingerprint(o))
        self.assertNotEqual(check.fingerprint(dict(o, exchange="BSE")), check.fingerprint(o))
        self.assertEqual(len(check.fingerprint(o)), 16)
        if hasattr(ticket, "ORDER_FIELDS"):
            self.assertEqual(check.ORDER_FIELDS, ticket.ORDER_FIELDS)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "state", "risk.json")
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", RELIANCE_PATH, "--config", CFG_PATH, "--portfolio", none,
                                   "--prices", none, "--state", none, "--now", NOW, "--out", out])
            text = buf.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("PASS  #1 buy RELIANCE 80 shares, entry", text)
            self.assertIn("entry 1197.40 (the plan's entry), stop 1185.00, target 1222.20: risk 12.40 a share", text)
            self.assertIn("max loss ₹992 (limit ₹1,000), reward:risk 2.00 (min 1)", text)
            self.assertIn("1 passed, 0 blocked at 2026-10-27 10:01 IST", text)
            self.assertIn("not financial advice", text)
            self.assertNotIn("$", text)
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], TODAY)
            self.assertEqual(rec["results"][0]["kind"], "entry")
            self.assertEqual(rec["results"][0]["fingerprint"], check.fingerprint(reliance()))

    def test_no_pdt_rule_in_india(self):
        self.assertNotIn("pdt_guard", check.DEFAULTS)
        self.assertFalse(hasattr(check, "day_trades"))
        self.assertFalse(hasattr(check, "business_days"))
        # an old config key does nothing, and three day trades in a week are not a problem
        def trade(day):
            return [placed("X" + day[-2:], day=day), placed("X" + day[-2:], side="sell", day=day, kind="exit",
                                                            status="filled", max_loss=None)]
        st = {"orders": trade("2026-10-21") + trade("2026-10-22") + trade("2026-10-23")}
        res = check.check([reliance()], cfg(pdt_guard=True), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertNotIn("pdt", reasons(res))


class DayTradeChecks(unittest.TestCase):
    def one(self, order, now=NOW, **over):
        return check.check([order], cfg(**over), now=at(now))

    def test_a_stop_is_required(self):
        o = reliance()
        for k in ("take_profit", "stop_loss"):
            o.pop(k)
        o["plan"] = {"entry": 1197.4}
        res = self.one(o)
        self.assertIn("stop: no stop", reasons(res))
        res = self.one(o, require_stop=False, min_reward_risk=0)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("no stop", warnings(res))

    def test_a_plan_stop_counts_but_warns(self):
        o = reliance()
        for k in ("take_profit", "stop_loss"):
            o.pop(k)
        res = self.one(o)
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("in the plan only", warnings(res))
        self.assertEqual(res["results"][0]["max_loss"], 992.0)

    def test_stop_and_target_on_the_right_side(self):
        self.assertIn("must be below the entry 1197.40", reasons(self.one(reliance(stop_loss=1200.0))))
        res = self.one(reliance(take_profit=1197.0))
        self.assertIn("target: the target 1197.00 must be above the entry 1197.40", reasons(res))

    def test_reward_risk(self):
        # (1205.00 - 1197.40) / 12.40 = 0.61
        res = self.one(reliance(take_profit=1205.0))
        self.assertIn("reward:risk: 0.61", reasons(res))
        self.assertEqual(verdicts(self.one(reliance(take_profit=1205.0), min_reward_risk=0.5)), ["PASS"])

    def test_the_bracket_stop_and_the_plan_stop_may_differ(self):
        res = self.one(reliance(stop_loss=1186.0))
        self.assertIn("differs from the plan's stop 1185.00", warnings(res))
        self.assertEqual(res["results"][0]["stop"], 1186.0)

    def test_a_fresh_higher_price_is_the_entry(self):
        later = at("2026-10-27 10:25")
        res = check.check([reliance()], cfg(), prices={"RELIANCE": 1210.0}, now=later,
                          price_times={"RELIANCE": QUOTE_TIME})
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["risk_per_share"]), (1210.0, "price", 25.0))
        self.assertIn("loss: 80 shares x 25.00 risk a share = ₹2,000 at the stop", reasons(res))
        self.assertIn("is above the plan's entry 1197.40", " ".join(r["info"]))

    def test_a_stale_or_future_price_is_not_used(self):
        for now in ("2026-10-27 11:00", "2026-10-27 10:01"):
            res = check.check([reliance()], cfg(), prices={"RELIANCE": 1210.0}, now=at(now),
                              price_times={"RELIANCE": QUOTE_TIME})
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertEqual(res["results"][0]["entry_ref"], 1197.4)
            self.assertIn("older than 15 minutes", warnings(res))

    def test_a_lower_price_does_not_shrink_the_risk(self):
        res = check.check([reliance()], cfg(), prices={"RELIANCE": 1190.0}, now=at(NOW))
        self.assertEqual(res["results"][0]["entry_ref"], 1197.4)

    def test_a_limit_order_uses_its_limit_price(self):
        # limit 1196.00: risk 11.00, 80 x 11 = 880
        o = reliance(order_type="LIMIT", limit_price=1196.0)
        res = check.check([o], cfg(), prices={"RELIANCE": 1210.0}, now=at(NOW))
        r = res["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["max_loss"], r["verdict"]),
                         (1196.0, "limit", 880.0, "PASS"))

    def test_a_stop_market_entry_uses_its_trigger_price(self):
        o = reliance(order_type="SL-M", trigger_price=1198.0, quantity=76)   # 76 x 13.00 = 988
        r = self.one(o)["results"][0]
        self.assertEqual((r["entry_ref"], r["entry_ref_from"], r["verdict"]), (1198.0, "trigger price", "PASS"))

    def test_daily_loss_and_trades_per_day(self):
        two = {"orders": [placed("INFY"), placed("TCS")]}
        res = check.check([reliance()], cfg(), state=two, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])       # 2 x 992 + 992 = 2,976 <= 3,000
        self.assertIn("today before this: 2 of 3 trades, ₹1,984 of ₹3,000 daily loss",
                      res["results"][0]["info"][-1])
        three = {"orders": [placed("INFY"), placed("TCS"), placed("HDFCBANK")]}
        res = check.check([reliance()], cfg(), state=three, now=at(NOW))
        why = reasons(res)
        self.assertIn("trades: 3 entries placed today", why)
        self.assertIn("daily loss: ₹2,976 already at risk today plus ₹992 for this one = ₹3,968, "
                      "over your ₹3,000", why)
        yesterday = {"orders": [placed(s, day="2026-10-26") for s in ("INFY", "TCS", "HDFCBANK")]}
        self.assertEqual(verdicts(check.check([reliance()], cfg(), state=yesterday, now=at(NOW))), ["PASS"])

    def test_exits_and_declines_do_not_count_as_trades(self):
        st = {"orders": [placed("INFY"), placed("INFY", side="sell", kind="exit", max_loss=None),
                         placed("TCS", status="declined"), placed("HDFCBANK", status="failed")]}
        res = check.check([reliance()], cfg(), state=st, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertEqual(res["today"]["entries_placed"], 1)

    def test_an_entry_with_no_logged_max_loss_warns(self):
        st = {"orders": [placed("INFY", max_loss=None)]}
        res = check.check([reliance()], cfg(), state=st, now=at(NOW))
        self.assertIn("no max loss in the log", " ".join(res["warnings"]))

    def test_the_batch_counts_toward_the_daily_limits(self):
        # each: 10 shares x (500 - 400) = Rs 1,000 at the stop, reward:risk 2
        orders = [bracket(s, 10, 500.0, 400.0, 700.0) for s in ("AAA", "BBB", "CCC", "DDD")]
        res = check.check(orders, cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "PASS", "BLOCK"])
        why = reasons(res, 3)
        self.assertIn("trades: 0 entries placed today plus 3 passed earlier", why)
        self.assertIn("daily loss: ₹3,000 already at risk today plus ₹1,000 for this one = ₹4,000", why)

    def test_entry_window_ist(self):
        cases = [("2026-10-27 09:29", "no new entries before 09:30 IST (it is 09:29)"),
                 ("2026-10-27 14:31", "no new entries after 14:30 IST (it is 14:31)"),
                 ("2026-10-27 09:00", "the market is closed (Tue 2026-10-27 09:00"),
                 ("2026-10-27 15:30", "the market is closed"),
                 ("2026-10-27 18:00", "the market is closed"),
                 ("2026-10-31 10:15", "the market is closed today (weekend"),
                 ("2026-10-20 10:15", "the market is closed today (NSE holiday: Dussehra"),
                 ("2026-11-10 10:15", "the market is closed today (NSE holiday: Diwali-Balipratipada"),
                 ("2026-12-25 10:15", "the market is closed today (NSE holiday: Christmas"),
                 ("2026-10-27T03:50:00Z", "no new entries before 09:30 IST (it is 09:20)")]
        for now, want in cases:
            res = self.one(reliance(), now=now)
            self.assertIn("window: " + want, reasons(res), now)
        for now in ("2026-10-27 09:30", "2026-10-27 14:30", "2026-10-27T04:00:00Z", "2026-10-27T09:00:00Z",
                    "2026-10-27T05:30:00Z", "2026-11-27 14:30", "2026-12-24 14:30"):
            res = self.one(reliance(), now=now)
            self.assertEqual(verdicts(res), ["PASS"], now)
            self.assertNotIn("early close", " ".join(res["warnings"]))     # NSE has no early closes
        res = self.one(reliance(), now="2026-10-31 10:15", no_entries_before=None, no_entries_after=None)
        self.assertEqual(verdicts(res), ["PASS"])
        with self.assertRaises(check.RiskError):
            self.one(reliance(), no_entries_before="9.30am")

    def test_your_own_market_holidays_and_the_calendar_year(self):
        res = self.one(reliance(), market_holidays={"2026-10-27": "Muhurat test"})
        self.assertIn("window: the market is closed today (NSE holiday: Muhurat test", reasons(res))
        res = self.one(reliance(), now="2027-01-04 10:15")
        self.assertEqual(verdicts(res), ["PASS"])
        self.assertIn("covers 2026 only: check nseindia.com", " ".join(res["warnings"]))

    def test_the_window_uses_ist_not_the_machine(self):
        self.assertEqual(check.to_local(at("2026-10-27 10:01")), datetime.datetime(2026, 10, 27, 10, 1))
        self.assertEqual(at("2026-10-27 10:01"), datetime.datetime(2026, 10, 27, 4, 31))
        self.assertEqual(at("2026-12-01 10:15"), datetime.datetime(2026, 12, 1, 4, 45))   # no daylight saving
        self.assertEqual(at("2026-12-01T04:45:00Z"), at("2026-12-01 10:15"))

    def test_a_short_entry_when_allowed(self):
        short = sell("RELIANCE", 80, stop_loss=1209.8, take_profit=1172.6, plan={"entry": 1197.4})
        res = self.one(short)
        self.assertIn("short: selling more RELIANCE than you hold", reasons(res))
        res = self.one(short, allow_short=True)
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"], r["max_loss"], r["reward_risk"]), ("PASS", "entry", 992.0, 2.0))
        res = self.one(dict(short, stop_loss=1190.0), allow_short=True)
        self.assertIn("must be above the entry 1197.40 for a sell", reasons(res))

    def test_bracket_and_order_format(self):
        self.assertIn("quantity must be a positive whole number", reasons(self.one(reliance(quantity=8.5))))
        self.assertIn("quantity must be a positive whole number", reasons(self.one(reliance(quantity=0))))
        self.assertIn("quantity must be a number of shares", reasons(self.one(reliance(quantity="lots"))))
        self.assertIn("bracket order needs validity DAY", reasons(self.one(reliance(validity="IOC"))))
        o = reliance()
        o.pop("take_profit")
        self.assertIn("needs both take_profit and stop_loss", reasons(self.one(o)))
        self.assertIn("exchange must be \"NSE\"", reasons(self.one(reliance(exchange="BSE"))))
        o = reliance()
        o.pop("exchange")
        self.assertIn("exchange must be \"NSE\"", reasons(self.one(o)))
        self.assertIn("side must be buy or sell", reasons(self.one(reliance(side="hold"))))
        self.assertIn("unknown order_type", reasons(self.one(reliance(order_type="STOP"))))
        self.assertIn("unknown validity", reasons(self.one(reliance(validity="GTC"))))
        self.assertIn("unknown product", reasons(self.one(reliance(product="XYZ"))))
        self.assertIn("needs a limit_price", reasons(self.one(reliance(order_type="LIMIT"))))
        self.assertIn("needs a trigger_price", reasons(self.one(reliance(order_type="SL-M"))))
        self.assertIn("stop_loss must be a positive number", reasons(self.one(reliance(stop_loss=-1))))

    def test_prices_must_sit_on_the_tick(self):
        self.assertIn("stop_loss 1185.03 is not a multiple of the 0.05 tick", reasons(self.one(reliance(stop_loss=1185.03))))
        self.assertIn("take_profit 1222.22 is not a multiple", reasons(self.one(reliance(take_profit=1222.22))))
        res = self.one(reliance(order_type="LIMIT", limit_price=1197.42))
        self.assertIn("limit_price 1197.42 is not a multiple", reasons(res))
        self.assertEqual(verdicts(self.one(reliance(order_type="LIMIT", limit_price=1197.45))), ["PASS"])

    def test_products_and_margin(self):
        self.assertEqual(verdicts(self.one(reliance(product="CNC"))), ["PASS"])
        for prod in ("NRML", "MTF"):
            self.assertIn("margin: product %s" % prod, reasons(self.one(reliance(product=prod))))
            self.assertEqual(verdicts(self.one(reliance(product=prod), allow_margin=True)), ["PASS"])

    def test_futures_and_options_are_off(self):
        for sym in ("NIFTY26NOV24500CE", "RELIANCE26NOVFUT", "BANKNIFTY26NOV52000PE"):
            o = dict(buy("RELIANCE", 1, 600.0), symbol=sym)
            res = self.one(o, require_stop=False)
            self.assertIn("futures or options contract", reasons(res), sym)
            res = self.one(o, require_stop=False, allow_options=True)
            self.assertEqual(verdicts(res), ["PASS"], sym)
            self.assertTrue(res["results"][0]["warnings"])


class Exits(unittest.TestCase):
    PF = {"cash": 404208.0, "positions": [{"ticker": "RELIANCE", "shares": 80,
                                            "lots": [{"date": TODAY, "shares": 80, "cost": 95792.0}]}]}
    SELL = sell("RELIANCE", 80)

    def test_getting_out_is_never_blocked_by_the_day_limits(self):
        full = {"orders": [placed("RELIANCE"), placed("INFY"), placed("TCS")] +
                          [placed("T%d" % i, max_loss=1) for i in range(6)] +
                          [placed("RELIANCE", side="sell", kind="exit", max_loss=None)]}
        c = cfg(blocked=["RELIANCE"], max_order_inr=100, max_daily_loss_inr=50)
        res = check.check([self.SELL], c, portfolio=self.PF, state=full, now=at("2026-10-27 15:40"))
        r = res["results"][0]
        self.assertEqual((r["verdict"], r["kind"]), ("PASS", "exit"), r["reasons"])
        why = warnings(res)
        for word in ("blocked:", "duplicate:", "daily limit:"):
            self.assertIn("exit, not blocked: " + word, why)
        self.assertNotIn("price", why)
        res = check.check([self.SELL], cfg(), portfolio=self.PF, now=at("2026-10-31 12:00"))     # a Saturday
        self.assertEqual(verdicts(res), ["PASS"])

    def test_a_partial_exit_and_the_rest(self):
        half = sell("RELIANCE", 40)
        res = check.check([half, half], cfg(), portfolio=self.PF, prices={"RELIANCE": 1190.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual([r["kind"] for r in res["results"]], ["exit", "exit"])
        self.assertIn("exit, not blocked: duplicate", warnings(res, 1))
        res = check.check([half, half, half], cfg(), portfolio=self.PF, prices={"RELIANCE": 1190.0}, now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS", "BLOCK"])
        self.assertIn("short:", reasons(res, 2))

    def test_format_asset_and_short_still_apply_to_a_sell(self):
        res = check.check([dict(self.SELL, order_type="LIMIT")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("needs a limit_price", reasons(res))
        res = check.check([dict(self.SELL, quantity=81)], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("short: selling more RELIANCE than you hold (80 shares)", reasons(res))
        res = check.check([self.SELL], cfg(), now=at(NOW))
        self.assertIn("record the fill first", reasons(res))
        res = check.check([dict(self.SELL, exchange="BSE")], cfg(), portfolio=self.PF, now=at(NOW))
        self.assertIn("exchange must be", reasons(res))

    def test_buy_then_sell_in_one_batch(self):
        res = check.check([reliance(), self.SELL], cfg(), now=at(NOW))
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        self.assertEqual(res["results"][1]["kind"], "exit")


class AccountChecks(unittest.TestCase):
    """Plain whole-share buys with the day-trade checks off: size, cap, cash, margin, shorts, duplicates."""

    def one(self, order, **over):
        return check.check([order], basket_cfg(**over), prices=PRICES, today=BASKET_TODAY)["results"][0]

    def test_the_five_buys_pass_on_a_fresh_500000_paper_account(self):
        res = check.check(basket_orders(), basket_cfg(), today=BASKET_TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5)
        # 75x1167.40 + 35x2478 + 70x1146.50 + 120x683.30 + 30x2890 = 423,236
        self.assertEqual(res["cash_after"], 76764.0)
        self.assertEqual(res["results"][0]["value_inr"], 87555.0)
        self.assertEqual(res["results"][0]["amount"], "75 shares")
        self.assertEqual(res["results"][4]["symbol"], "M&M")

    def test_a_sixth_buy_is_blocked_three_ways(self):
        # 40 more RELIANCE = 46,696: cash left 30,068 < the 50,000 buffer, position 134,251 > 125,000
        res = check.check(basket_orders() + [buy("RELIANCE", 40)], basket_cfg(), today=BASKET_TODAY)
        self.assertEqual(verdicts(res), ["PASS"] * 5 + ["BLOCK"])
        why = reasons(res, 5)
        self.assertIn("cash:", why)
        self.assertIn("position:", why)
        self.assertIn("duplicate:", why)

    def test_the_day_trade_config_blocks_buys_without_a_stop(self):
        res = check.check(basket_orders(), cfg(), now=at(BASKET_NOW))
        self.assertEqual(verdicts(res)[0], "BLOCK")
        self.assertIn("stop: no stop", reasons(res))

    def test_max_and_min_order(self):
        big = self.one(buy("TCS", 50), max_position_pct=100)          # 50 x 2478 = 123,900
        self.assertIn("over your ₹100,000 max order", " ".join(big["reasons"]))
        small = self.one(buy("ABC", 1, 400.0))
        self.assertIn("under your ₹500 minimum", " ".join(small["reasons"]))

    def test_blocked_list(self):
        r = self.one(buy("INFY", 10), blocked=["infy"])
        self.assertEqual(r["verdict"], "BLOCK")
        self.assertIn("blocked list", r["reasons"][0])

    def test_junk_symbols_and_the_ns_suffix(self):
        for sym in ("BTC/USD", "RELI ANCE", ""):
            r = self.one(dict(buy("INFY", 1), symbol=sym))
            self.assertEqual(r["verdict"], "BLOCK", sym)
        self.assertIn("NSE equity symbol", " ".join(self.one(dict(buy("INFY", 1), symbol="BTC/USD"))["reasons"]))
        r = self.one(dict(buy("INFY", 10), symbol="infy.ns"))
        self.assertEqual((r["verdict"], r["symbol"]), ("PASS", "INFY"))
        r = self.one(buy("M&M", 10))
        self.assertEqual(r["verdict"], "PASS")

    def test_format_rules(self):
        self.assertIn("side must be buy or sell", " ".join(self.one(dict(buy("INFY", 10), side="hold"))["reasons"]))
        lim = dict(buy("INFY", 10), order_type="LIMIT")
        self.assertIn("needs a limit_price", " ".join(self.one(lim)["reasons"]))
        self.assertIn("whole number", " ".join(self.one(dict(buy("INFY", 10), quantity=2.5))["reasons"]))
        sl = dict(buy("INFY", 10), order_type="SL", limit_price=1150.0)
        self.assertIn("needs a trigger_price", " ".join(self.one(sl)["reasons"]))

    def test_share_orders_need_a_price(self):
        q = dict(buy("INFY", 2), symbol="ZZZZ", plan={})
        self.assertIn("no price for ZZZZ", " ".join(self.one(q)["reasons"]))
        q = buy("INFY", 5)
        q.pop("plan")
        r = self.one(q)                      # no plan: the price comes from prices.json
        self.assertEqual(r["verdict"], "PASS")
        self.assertEqual(r["value_inr"], 5732.5)

    def test_margin(self):
        r = check.check([buy("INFY", 100)], basket_cfg(cash_buffer_pct=0, max_position_pct=100, max_order_inr=200000),
                        portfolio={"cash": 100000, "positions": []}, prices=PRICES, today=BASKET_TODAY)["results"][0]
        self.assertIn("margin", " ".join(r["reasons"]))        # 114,650 on 100,000 cash

    PF = {"cash": 20000.0, "positions": [{"ticker": "INFY", "shares": 40,
                                           "lots": [{"date": "2026-10-28", "shares": 40, "cost": 45860.0}]}]}

    def test_short_sales_are_blocked(self):
        r = check.check([sell("INFY", 100)], basket_cfg(), portfolio=self.PF, prices=PRICES,
                        today=BASKET_TODAY)["results"][0]
        self.assertIn("short:", " ".join(r["reasons"]))
        r = check.check([sell("INFY", 30)], basket_cfg(), portfolio=self.PF, prices=PRICES,
                        today=BASKET_TODAY)["results"][0]
        self.assertEqual(r["verdict"], "PASS")

    def test_a_sell_frees_cash_for_a_later_buy(self):
        orders = [sell("INFY", 30), buy("HDFCBANK", 60)]       # +34,395 cash, then 40,998 out
        res = check.check(orders, basket_cfg(max_position_pct=100), portfolio=self.PF, prices=PRICES,
                          today=BASKET_TODAY)
        self.assertEqual(verdicts(res), ["PASS", "PASS"])
        res = check.check([buy("HDFCBANK", 60)], basket_cfg(max_position_pct=100), portfolio=self.PF,
                          prices=PRICES, today=BASKET_TODAY)
        self.assertEqual(verdicts(res), ["BLOCK"])

    def test_daily_limit_and_already_placed_today(self):
        st = {"orders": [{"symbol": "T%d" % i, "side": "buy", "status": "placed",
                          "at": BASKET_TODAY + "T10:00:00+05:30"} for i in range(10)]}
        res = check.check([buy("INFY", 10)], basket_cfg(), state=st, today=BASKET_TODAY)
        self.assertIn("daily limit", reasons(res))
        res = check.check([buy("INFY", 10)], basket_cfg(), state=st, today="2026-10-30")
        self.assertEqual(verdicts(res), ["PASS"])
        ko = {"orders": [{"symbol": "INFY", "side": "buy", "status": "placed", "at": BASKET_TODAY + "T10:00:00"}]}
        res = check.check([buy("INFY", 10)], basket_cfg(), state=ko, today=BASKET_TODAY)
        self.assertIn("already placed today", reasons(res))
        declined = {"orders": [{"symbol": "INFY", "side": "buy", "status": "declined",
                                "at": BASKET_TODAY + "T10:00:00"}]}
        self.assertEqual(verdicts(check.check([buy("INFY", 10)], basket_cfg(), state=declined,
                                              today=BASKET_TODAY)), ["PASS"])

    def test_live_account_warns(self):
        res = check.check([buy("INFY", 10)], basket_cfg(paper=False), today=BASKET_TODAY)
        self.assertIn("LIVE", res["warnings"][0])

    def test_no_portfolio_and_no_budget_is_an_error(self):
        with self.assertRaises(check.RiskError):
            check.check([buy("INFY", 10)], {"budget": None}, today=BASKET_TODAY)
        with self.assertRaises(check.RiskError):
            check.check([buy("INFY", 10)], basket_cfg(), portfolio={"positions": []}, today=BASKET_TODAY)


class Cli(unittest.TestCase):
    def test_prices_file_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump({"as_of": "2026-10-27T04:55:00Z", "prices": {"RELIANCE": 1210.0},
                           "detail": {"RELIANCE": {"time": "2026-10-27T04:52:13Z",
                                                   "fetched": "2026-10-27T04:55:00Z"}}}, fh)
            none = os.path.join(d, "none")
            base = ["--orders", RELIANCE_PATH, "--config", CFG_PATH, "--portfolio", none, "--prices", pp,
                    "--state", none, "--no-write"]
            buf = io.StringIO()
            with redirect_stdout(buf):
                self.assertEqual(check.main(base + ["--now", "2026-10-27 10:25"]), 1)
            self.assertIn("80 shares x 25.00 risk a share = ₹2,000", buf.getvalue())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(check.main(base + ["--now", "2026-10-27 11:00"]), 0)

    def test_the_basket_with_a_basket_config_file(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "config.json")
            with open(c, "w") as fh:
                json.dump(basket_cfg(), fh)
            none = os.path.join(d, "none")
            out = os.path.join(d, "state", "risk.json")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", BASKET_PATH, "--config", c, "--portfolio", none, "--prices", none,
                                   "--state", none, "--today", BASKET_TODAY, "--out", out])
            self.assertEqual(code, 0)
            self.assertIn("5 passed, 0 blocked", buf.getvalue())
            with open(out) as fh:
                rec = json.load(fh)
            self.assertEqual(rec["date"], BASKET_TODAY)
            self.assertTrue(all(len(r["fingerprint"]) == 16 for r in rec["results"]))
            bad = os.path.join(d, "orders.json")
            with open(bad, "w") as fh:
                json.dump({"orders": basket_orders() + [buy("RELIANCE", 40)]}, fh)
            with redirect_stdout(io.StringIO()):
                code = check.main(["--orders", bad, "--config", c, "--portfolio", none, "--prices", none,
                                   "--state", none, "--today", BASKET_TODAY, "--no-write"])
            self.assertEqual(code, 1)

    def test_a_portfolio_and_json_output(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w") as fh:
                json.dump({"cash": 404208.0, "positions": [{"ticker": "RELIANCE.NS", "quantity": 80}]}, fh)
            none = os.path.join(d, "none")
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = check.main(["--orders", RELIANCE_PATH, "--config", CFG_PATH, "--portfolio", pf,
                                   "--prices", none, "--state", none, "--now", NOW, "--no-write", "--json"])
            out = json.loads(buf.getvalue())
            self.assertEqual(code, 0)
            self.assertIn("no price for RELIANCE: valued at its cost basis", " ".join(out["warnings"]))
            self.assertEqual(out["results"][0]["verdict"], "PASS")

    def test_errors_are_one_line(self):
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", "/nonexistent.json", "--no-write"]), 1)
        self.assertIn("no orders file", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", RELIANCE_PATH, "--now", "noon", "--no-write"]), 1)
        self.assertIn("--now wants", err.getvalue())
        self.assertNotIn("Traceback", err.getvalue())
        err = io.StringIO()
        with redirect_stderr(err):
            self.assertEqual(check.main(["--orders", RELIANCE_PATH, "--today", "monday", "--no-write"]), 1)
        self.assertIn("--today wants", err.getvalue())


if __name__ == "__main__":
    unittest.main()
