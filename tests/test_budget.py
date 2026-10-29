import datetime
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT, FIX

size = load("trader-budget", "size")

CFG = os.path.join(ROOT, "trader-budget", "config.example.json")
PLAN = os.path.join(FIX, "trader-budget", "plan-reliance.json")
ORDERS_FIXTURE = os.path.join(FIX, "trader-risk", "orders-reliance.json")
NOW = "2026-10-27 10:01"
TODAY = "2026-10-27"
# the contract's config, key for key (rupees)
CONTRACT_CFG = {
    "budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 150000, "min_order_inr": 500,
    "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000, "max_trades_per_day": 3, "max_orders_per_day": 10,
    "min_reward_risk": 1.0, "require_stop": True,
    "no_entries_before": "09:30", "no_entries_after": "14:30", "flat_by": "15:15",
    "broker": "your broker", "paper": True, "blocked": [],
    "allow_options": False, "allow_margin": False, "allow_short": False, "market_holidays": {},
}


def cfg(**over):
    with open(CFG) as fh:
        c = json.load(fh)
    c.update(over)
    return c


def plan(**over):
    with open(PLAN) as fh:
        p = json.load(fh)
    p.update(over)
    return p


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = size.main(argv)
    return code, out.getvalue(), err.getvalue()


def prices_doc(price, quote_time="2026-10-27T04:52:13Z"):
    # 04:52:13 UTC = 10:22:13 IST
    return {"as_of": quote_time, "source": "yahoo", "prices": {"RELIANCE": price},
            "detail": {"RELIANCE": {"prev_close": 1190.0, "time": quote_time, "source": "yahoo"}}}


class TheReliance(unittest.TestCase):
    """Rs 500,000, 10% buffer, Rs 1,000 max loss, RELIANCE entry 1197.40 stop 1185.00 (risk 12.40)
    -> 1000 / 12.40 = 80.6 -> 80 shares, Rs 95,792, max loss 80 x 12.40 = Rs 992."""

    def test_config_example_is_the_contract(self):
        with open(CFG) as fh:
            self.assertEqual(json.load(fh), CONTRACT_CFG)

    def test_reliance_is_80_shares_bound_by_the_max_loss(self):
        s = size.size_trade(plan(), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 80)
        self.assertEqual(s["bound_by"], "max loss")
        self.assertEqual(s["risk_per_share"], 12.4)
        self.assertEqual(s["cost"], 95792.0)          # 80 x 1197.40
        self.assertEqual(s["max_loss"], 992.0)        # 80 x 12.40
        self.assertEqual(s["reward_risk"], 2.0)       # (1222.20 - 1197.40) / 12.40
        self.assertEqual(s["cash_buffer"], 50000)
        # 1000/12.4 = 80.6, 125000/1197.4 = 104.4, 150000/1197.4 = 125.3, 450000/1197.4 = 375.8
        self.assertEqual([(l["name"], l["shares"]) for l in s["limits"]],
                         [("max loss", 80), ("position cap", 104), ("max order", 125), ("cash", 375)])
        self.assertEqual([l["name"] for l in s["limits"] if l["binds"]], ["max loss"])
        self.assertIsNone(s["refusal"])

    def test_the_bracket_order_is_the_broker_neutral_nse_shape(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY))
        why = o.pop("why")
        self.assertEqual(o, {
            "symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "quantity": 80, "product": "MIS",
            "order_type": "MARKET", "validity": "DAY", "stop_loss": 1185.0, "take_profit": 1222.2,
            "client_tag": "tb-RELIANCE-2026-10-27",
            "plan": {"entry": 1197.4, "stop": 1185.0, "target": 1222.2, "risk_per_share": 12.4,
                     "max_loss": 992.0, "setup": "orb", "entry_time": "10:00 AM", "flat_by": "15:15"}})
        self.assertNotIn("limit_price", o)
        self.assertIsInstance(o["quantity"], int)
        for k in ("stop_loss", "take_profit"):
            self.assertIsInstance(o[k], float)
        self.assertIn("bound by your ₹1,000 max loss", why)
        self.assertIn("₹992", why)

    def test_the_fixture_orders_file_is_what_it_writes(self):
        with open(ORDERS_FIXTURE) as fh:
            fixture = json.load(fh)["orders"][0]
        self.assertEqual(size.bracket_order(size.size_trade(plan(), cfg(), today=TODAY)), fixture)

    def test_a_limit_order(self):
        o = size.bracket_order(size.size_trade(plan(), cfg(), limit=1197.40, today=TODAY))
        self.assertEqual((o["order_type"], o["limit_price"], o["quantity"]), ("LIMIT", 1197.4, 80))
        # a limit below the entry: risk 1196.00 - 1185.00 = 11.00 -> 1000/11 = 90.9 -> 90 shares, loss 990
        s = size.size_trade(plan(), cfg(), limit=1196.00, today=TODAY)
        self.assertEqual((s["risk_per_share"], s["qty"], s["max_loss"]), (11.0, 90, 990.0))
        self.assertEqual(size.bracket_order(s)["limit_price"], 1196.0)

    def test_cli_writes_one_bracket_order(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "trader", "orders.json")
            code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 0)
            self.assertIn("Size: 80 shares of RELIANCE = ₹95,792 at 1197.40", text)
            self.assertIn("80 x 12.40 = ₹992", text)
            self.assertIn("<- binds", text)
            self.assertIn("buy 80 RELIANCE on NSE, market, day, take-profit 1222.20, stop-loss 1185.00", text)
            self.assertIn("Flat by 15:15 IST", text)
            self.assertIn("Brokerage, STT, GST and stamp duty are not included", text)
            self.assertIn("not financial advice", text)
            self.assertNotIn("$", text)
            with open(out) as fh:
                doc = json.load(fh)
            self.assertEqual(doc["status"], "proposed")
            self.assertIs(doc["paper"], True)
            self.assertEqual(doc["created_at"], "2026-10-27T04:31:00Z")      # 10:01 IST
            self.assertEqual(len(doc["orders"]), 1)
            self.assertEqual(doc["orders"][0]["plan"]["max_loss"], 992.0)
            self.assertEqual(doc["orders"][0]["exchange"], "NSE")

    def test_cli_json_and_limit(self):
        code, text, _ = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent", "--now", NOW,
                             "--limit", "--json"])
        self.assertEqual(code, 0)
        out = json.loads(text)
        self.assertEqual(out["sizing"]["qty"], 80)
        self.assertEqual(out["orders"][0]["limit_price"], 1197.4)
        self.assertEqual(out["orders"][0]["order_type"], "LIMIT")

    def test_the_clock_is_india_standard_time(self):
        self.assertEqual(size.parse_now("2026-10-27 10:01"), datetime.datetime(2026, 10, 27, 4, 31))
        self.assertEqual(size.parse_now("2026-10-27T04:31:00Z"), datetime.datetime(2026, 10, 27, 4, 31))
        self.assertEqual(size.to_local(datetime.datetime(2026, 10, 27, 4, 31)), datetime.datetime(2026, 10, 27, 10, 1))
        # 22:00 UTC on the 26th is already the 27th in India: the plan's date is the IST date
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--portfolio", "/nonexistent",
                            "--now", "2026-10-26T22:00:00Z", "--json"])
        self.assertEqual(code, 0, err)

    def test_the_swing_leftovers_are_gone(self):
        for name in ("parse_weights", "orders_from", "normalise", "allocate", "eastern_offset", "pdt_guard"):
            self.assertFalse(hasattr(size, name), name)
        self.assertNotIn("pdt_guard", size.DEFAULTS)
        self.assertNotIn("fractional", size.DEFAULTS)


class PlanRules(unittest.TestCase):
    def test_zero_shares_is_a_plain_refusal(self):
        # risk 1197.40 - 150 = 1047.40 a share, more than the whole Rs 1,000 max loss
        s = size.size_trade(plan(stop=150.0), cfg(), today=TODAY)
        self.assertEqual(s["qty"], 0)
        self.assertIn("more than your ₹1,000 max loss per trade", s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "plan.json")
            with open(p, "w") as fh:
                json.dump(plan(stop=150.0), fh)
            out = os.path.join(d, "orders.json")
            code, text, _ = run(["--plan", p, "--config", CFG, "--portfolio", os.path.join(d, "none"),
                                 "--now", NOW, "--write", out])
            self.assertEqual(code, 1)
            self.assertIn("No order:", text)
            self.assertFalse(os.path.exists(out))

    def test_a_stop_too_wide_gives_fewer_shares_not_a_refusal(self):
        # risk 97.40 -> 1000/97.40 = 10.3 -> 10 shares, loss 974
        s = size.size_trade(plan(stop=1100.0), cfg(), today=TODAY)
        self.assertEqual((s["qty"], s["max_loss"]), (10, 974.0))

    def test_each_limit_can_bind(self):
        # max order 90,000 / 1197.40 = 75.2
        s = size.size_trade(plan(), cfg(max_order_inr=90000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (75, "max order"))
        # position cap 15% of 500,000 = 75,000 / 1197.40 = 62.6
        s = size.size_trade(plan(), cfg(max_position_pct=15), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (62, "position cap"))
        # cash 60,000 minus the 50,000 buffer = 10,000 / 1197.40 = 8.4
        s = size.size_trade(plan(), cfg(), cash=60000, today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"], s["cash_available"]), (8, "cash", 10000))
        # max loss 2,000 -> 161 by loss, but the position cap (104) is smaller
        s = size.size_trade(plan(), cfg(max_loss_per_trade_inr=2000), today=TODAY)
        self.assertEqual((s["qty"], s["bound_by"]), (104, "position cap"))

    def test_prices_snap_to_the_nse_tick(self):
        # 1197.43 -> 1197.45, 1184.98 -> 1185.00, 1222.22 -> 1222.20
        s = size.size_trade(plan(entry=1197.43, stop=1184.98, target=1222.22), cfg(), today=TODAY)
        self.assertEqual((s["entry"], s["stop"], s["target"]), (1197.45, 1185.0, 1222.2))
        self.assertEqual((s["risk_per_share"], s["qty"], s["max_loss"]), (12.45, 80, 996.0))
        o = size.bracket_order(s)
        for v in (o["stop_loss"], o["take_profit"], o["plan"]["entry"]):
            self.assertAlmostEqual(v * 20, round(v * 20), places=6)
        s = size.size_trade(plan(), cfg(), limit=1196.03, today=TODAY)
        self.assertEqual(size.bracket_order(s)["limit_price"], 1196.05)
        self.assertEqual([size.round_tick(x) for x in (0.02, 0.025, 0.07, 1174.3, 1174.33, 1174.325)],
                         [0.0, 0.05, 0.05, 1174.3, 1174.35, 1174.35])

    def test_bare_symbols(self):
        self.assertEqual(size.size_trade(plan(symbol="reliance.ns"), cfg(), today=TODAY)["symbol"], "RELIANCE")
        s = size.size_trade(plan(symbol="m&m"), cfg(), today=TODAY)
        self.assertEqual(size.bracket_order(s)["client_tag"], "tb-M&M-2026-10-27")
        self.assertEqual(size.bracket_order(s)["symbol"], "M&M")

    def test_a_fresh_higher_price_means_a_bigger_risk(self):
        # last 1203.47 -> tick 1203.45: risk 18.45, 1000/18.45 = 54.2 -> 54 shares, loss 996.30
        s = size.size_trade(plan(), cfg(), price=1203.47, today=TODAY)
        self.assertEqual((s["entry"], s["entry_from"], s["risk_per_share"]), (1203.45, "price", 18.45))
        self.assertEqual((s["qty"], s["max_loss"], s["reward_risk"]), (54, 996.3, 1.02))
        self.assertIsNone(s["refusal"])
        # last 1210: risk 25.00, reward 12.20 -> reward:risk 0.49
        s = size.size_trade(plan(), cfg(min_reward_risk=0), price=1210.00, today=TODAY)
        self.assertEqual((s["entry"], s["risk_per_share"], s["qty"], s["max_loss"], s["reward_risk"]),
                         (1210.0, 25.0, 40, 1000.0, 0.49))
        self.assertIsNone(s["refusal"])
        s = size.size_trade(plan(), cfg(), price=1210.00, today=TODAY)
        self.assertIn("reward:risk 0.49 (target 1222.20, entry 1210.00, stop 1185.00) is under your minimum 1",
                      s["refusal"])
        self.assertIsNone(size.bracket_order(s))
        lower = size.size_trade(plan(), cfg(), price=1190.00, today=TODAY)
        self.assertEqual((lower["entry"], lower["qty"]), (1197.4, 80))

    def test_prices_file_is_used_only_when_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            pp = os.path.join(d, "prices.json")
            with open(pp, "w") as fh:
                json.dump(prices_doc(1210.0), fh)
            base = ["--plan", PLAN, "--config", CFG, "--portfolio", os.path.join(d, "none"), "--prices", pp, "--json"]
            code, text, _ = run(base + ["--now", "2026-10-27 10:25"])
            out = json.loads(text)
            self.assertEqual((code, out["sizing"]["qty"], out["orders"]), (1, 40, []))
            self.assertIn("above the plan's entry", out["notes"][0])
            self.assertIn("10:22 IST", out["notes"][0])
            out = json.loads(run(base + ["--now", "2026-10-27 11:00"])[1])
            self.assertEqual(out["sizing"]["qty"], 80)
            self.assertIn("not used", out["notes"][0])

    def test_the_portfolio_cash_caps_the_size(self):
        with tempfile.TemporaryDirectory() as d:
            pf = os.path.join(d, "portfolio.json")
            with open(pf, "w") as fh:
                json.dump({"cash": 60000, "positions": []}, fh)
            out = json.loads(run(["--plan", PLAN, "--config", CFG, "--portfolio", pf, "--now", NOW, "--json"])[1])
            self.assertEqual((out["sizing"]["qty"], out["sizing"]["bound_by"]), (8, "cash"))

    def test_bad_plans(self):
        for p, word in ((plan(stop=1200.0), "not below the entry"),
                        (plan(target=1190.0), "already happened"),
                        (plan(target=None), "positive target"),
                        (plan(side="sell"), "long (buy) day trades only"),
                        (plan(symbol=""), "no symbol")):
            with self.assertRaises(size.SizeError) as e:
                size.size_trade(p, cfg(), today=TODAY)
            self.assertIn(word, str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), price=1230.00, today=TODAY)
        self.assertIn("already happened", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(blocked=["reliance"]), today=TODAY)
        self.assertIn("blocked list", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(), today="2026-10-28")
        self.assertIn("make a new plan", str(e.exception))
        with self.assertRaises(size.SizeError) as e:
            size.size_trade(plan(), cfg(budget=None), today=TODAY)
        self.assertIn("no budget", str(e.exception))

    def test_under_the_minimum_order(self):
        cheap = plan(symbol="ABC", entry=20.0, stop=19.0, target=21.0)
        s = size.size_trade(cheap, cfg(max_loss_per_trade_inr=10), today=TODAY)
        self.assertEqual(s["qty"], 10)
        self.assertIn("under your ₹500 minimum order", s["refusal"])
        self.assertIsNone(size.bracket_order(s))

    def test_missing_plan_and_bad_clock(self):
        code, _, err = run(["--plan", "/nonexistent/plan.json", "--config", CFG, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("no plan", err)
        code, _, err = run(["--plan", PLAN, "--config", CFG, "--now", "tomorrow"])
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)


if __name__ == "__main__":
    unittest.main()
