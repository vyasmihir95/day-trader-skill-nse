import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr

from _load import load, ROOT

ticket = load("trader-broker", "ticket")
check = load("trader-risk", "check")

NOW = "2026-10-26 10:15"          # a Monday, IST
TODAY = "2026-10-26"
R = "₹"

CFG = {"budget": 500000, "cash_buffer_pct": 10, "max_position_pct": 25, "max_order_inr": 150000,
       "min_order_inr": 500, "max_loss_per_trade_inr": 1000, "max_daily_loss_inr": 3000,
       "max_trades_per_day": 3, "max_orders_per_day": 10, "min_reward_risk": 1.0, "require_stop": True,
       "no_entries_before": "09:30", "no_entries_after": "14:30", "flat_by": "15:15",
       "broker": "mybroker", "paper": True}

RELIANCE = {
    "symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "quantity": 62, "product": "MIS",
    "order_type": "LIMIT", "limit_price": 1174.30, "validity": "DAY", "stop_loss": 1158.35,
    "take_profit": 1190.25, "client_tag": "tb-RELIANCE-2026-10-26",
    "why": "opening-range breakout on RELIANCE: 62 shares, bound by your Rs 1,000 max loss per trade "
           "(62 x 15.95 = Rs 988.90 at the stop)",
    "plan": {"entry": 1174.30, "stop": 1158.35, "target": 1190.25, "risk_per_share": 15.95,
             "max_loss": 988.9, "setup": "orb", "entry_time": "10:15 AM", "flat_by": "15:15"}}
MARKET_BUY = {"symbol": "TCS", "exchange": "NSE", "side": "buy", "quantity": 10, "product": "MIS",
              "order_type": "MARKET", "validity": "DAY", "stop_loss": 2440.0, "take_profit": 2520.0,
              "plan": {"entry": 2478.0, "stop": 2440.0, "target": 2520.0}}
BATCH = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "M&M"]

RELIANCE_PLAIN = [
    "BUY 62 RELIANCE on NSE at a limit of %s1,174.30, intraday MIS, good for today" % R,
    "stop-loss %s1,158.35 (a separate sell order, trigger %s1,158.35)" % (R, R),
    "target %s1,190.25 (a separate sell limit order)" % R,
    "max loss at the stop %s988.90 if it fills at %s1,158.35 (it can fill worse in a fast market)" % (R, R)]
ENTRY_LEG = {"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "quantity": 62, "product": "MIS",
             "order_type": "LIMIT", "limit_price": 1174.3, "validity": "DAY", "client_tag": "tb-RELIANCE-2026-10-26"}
STOP_LEG = {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "quantity": 62, "product": "MIS",
            "order_type": "SL-M", "trigger_price": 1158.35, "validity": "DAY",
            "client_tag": "tb-RELIANCE-2026-10-26-sl"}
TARGET_LEG = {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "quantity": 62, "product": "MIS",
              "order_type": "LIMIT", "limit_price": 1190.25, "validity": "DAY",
              "client_tag": "tb-RELIANCE-2026-10-26-tp"}


def reliance(**over):
    o = copy.deepcopy(RELIANCE)
    o.update(over)
    return o


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = ticket.main(argv)
    return code, out.getvalue(), err.getvalue()


class ExplicitYes(unittest.TestCase):
    def yes(self, reply, order=RELIANCE, batch=BATCH):
        return ticket.parse_yes(reply, order, batch)[0]

    def test_plain_yes_counts(self):
        for r in ("yes", "Yes.", "YES", "y", "yes place it", "Yes, take it", "yes RELIANCE", "yes 62 RELIANCE",
                  "yes 62", "yes reliance.ns"):
            self.assertTrue(self.yes(r), r)

    def test_anything_else_does_not(self):
        for r in ("", "sure", "ok", "okay", "go", "do it", "yeah", "yep", "absolutely", "send it",
                  "yes to all", "yes, all of them", "yes to the rest", "yes both",
                  "yes TCS", "yes M&M", "yes 60", "yes but make it 50", "yes?", "no", "not yes",
                  "yes, wait", "yes if it is under 1170"):
            self.assertFalse(self.yes(r), r)

    def test_amount_must_match_this_order(self):
        sell = {"symbol": "INFY", "exchange": "NSE", "side": "sell", "quantity": 3, "product": "MIS",
                "order_type": "MARKET", "validity": "DAY"}
        self.assertTrue(self.yes("yes sell 3 INFY", sell))
        self.assertFalse(self.yes("yes sell 4", sell))

    def test_a_bracket_style_yes(self):
        o = reliance()
        for r in (u"yes", "yes 62 RELIANCE", "yes 1,174.30", "yes %s1158.35" % R, "yes max loss 988.90",
                  "yes target 1190.25"):
            self.assertTrue(self.yes(r, o, ["RELIANCE", "TCS"]), r)
        for r in ("yes 63", "yes 100 RELIANCE", "yes but stop at 1150", "yes, stop 1158.35", "yes TCS", "sure",
                  "yes 1175"):
            self.assertFalse(self.yes(r, o, ["RELIANCE", "TCS"]), r)

    def test_symbols_with_punctuation(self):
        mm = {"symbol": "M&M", "exchange": "NSE", "side": "buy", "quantity": 5, "product": "MIS",
              "order_type": "MARKET", "validity": "DAY"}
        self.assertTrue(self.yes("yes M&M", mm))
        self.assertTrue(self.yes("yes 5 M&M", mm))
        self.assertFalse(self.yes("yes RELIANCE", mm))
        self.assertFalse(self.yes("yes BAJAJ-AUTO", mm, BATCH + ["BAJAJ-AUTO"]))


class TicketShape(unittest.TestCase):
    def test_fingerprint_is_the_risk_fingerprint(self):
        o = reliance()
        self.assertEqual(ticket.fingerprint(o), check.fingerprint(o))
        self.assertEqual(ticket.ORDER_FIELDS, check.ORDER_FIELDS)
        self.assertEqual(ticket.fingerprint(o), ticket.fingerprint({k: v for k, v in o.items()
                                                                    if k not in ("why", "plan")}))
        self.assertNotEqual(ticket.fingerprint(o), ticket.fingerprint(reliance(quantity=63)))

    def test_entry_leg_leaves_out_why_plan_and_the_protective_prices(self):
        self.assertEqual(ticket.entry_leg(reliance()), ENTRY_LEG)

    def test_legs_are_listed_separately(self):
        names = [(n, leg) for n, leg in ticket.legs(reliance())]
        self.assertEqual(names, [("entry", ENTRY_LEG), ("stop-loss", STOP_LEG), ("target", TARGET_LEG)])
        self.assertTrue(ticket.has_protective_legs(reliance()))
        mk = ticket.legs(MARKET_BUY)
        self.assertEqual([n for n, _ in mk], ["entry", "stop-loss", "target"])
        self.assertNotIn("client_tag", mk[1][1])
        self.assertNotIn("limit_price", mk[0][1])
        only_stop = ticket.legs(dict(reliance(), take_profit=None))
        self.assertEqual([n for n, _ in only_stop], ["entry", "stop-loss"])

    def test_a_sell_has_only_its_own_leg(self):
        sell = {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "quantity": 62, "product": "MIS",
                "order_type": "MARKET", "validity": "DAY"}
        self.assertEqual(ticket.legs(sell), [("entry", sell)])
        self.assertFalse(ticket.has_protective_legs(sell))

    def test_plain_english(self):
        self.assertEqual(ticket.plain_parts(reliance()), RELIANCE_PLAIN)
        self.assertEqual(ticket.plain(reliance()), " / ".join(RELIANCE_PLAIN))
        self.assertEqual(ticket.plain({"symbol": "INFY", "exchange": "NSE", "side": "sell", "quantity": 1,
                                       "product": "MIS", "order_type": "MARKET", "validity": "DAY"}),
                         "SELL 1 INFY on NSE at market, intraday MIS, good for today")
        self.assertEqual(ticket.plain({"symbol": "INFY", "exchange": "NSE", "side": "sell", "quantity": 4,
                                       "product": "MIS", "order_type": "SL-M", "trigger_price": 1100,
                                       "validity": "IOC"}),
                         "SELL 4 INFY on NSE stop-market, trigger %s1,100.00, intraday MIS, immediate or cancel" % R)
        self.assertEqual(ticket.plain({"symbol": "INFY", "exchange": "NSE", "side": "sell", "quantity": 4,
                                       "product": "CNC", "order_type": "SL", "trigger_price": 1100,
                                       "limit_price": 1098.5, "validity": "DAY"}),
                         "SELL 4 INFY on NSE stop-limit, trigger %s1,100.00, limit %s1,098.50, delivery CNC, "
                         "good for today" % (R, R))
        self.assertNotIn("$", ticket.plain(reliance()))

    def test_max_loss(self):
        self.assertEqual(ticket.max_loss(reliance()), (988.9, 1158.35))     # 62 x 15.95
        self.assertEqual(ticket.max_loss(reliance(limit_price=1170.0)), (722.3, 1158.35))
        self.assertEqual(ticket.max_loss(MARKET_BUY), (380.0, 2440.0))      # 10 x (2478 - 2440) from the plan
        no_plan = dict(MARKET_BUY, plan=None)
        self.assertEqual(ticket.max_loss(no_plan), (None, 2440.0))
        self.assertEqual(ticket.max_loss({"symbol": "X", "side": "buy", "quantity": 1}), (None, None))

    def test_ticket_json(self):
        t = ticket.ticket_json(reliance(), 1, CFG, None, {"orders": []}, TODAY)
        self.assertEqual((t["ready"], t["paper"], t["broker"], t["max_loss"], t["stop"], t["protective_legs"]),
                         (False, True, "mybroker", 988.9, 1158.35, True))
        self.assertEqual([l["leg"] for l in t["legs"]], ["entry", "stop-loss", "target"])
        self.assertEqual(t["fingerprint"], ticket.fingerprint(reliance()))

    def test_no_alpaca_left(self):
        for name in ("ALPACA_FIELDS", "tool_input", "is_bracket", "eastern_offset", "to_eastern", "ny_iso"):
            self.assertFalse(hasattr(ticket, name), name)
        with open(os.path.join(ROOT, "trader-broker", "ticket.py"), encoding="utf-8") as fh:
            text = fh.read().lower()
        for word in ("alpaca", "place_stock_order", "new york", "ibkr"):
            self.assertNotIn(word, text)


class SetupFiles(unittest.TestCase):
    def test_config_example_is_a_neutral_placeholder(self):
        path = os.path.join(ROOT, "trader-broker", "claude_desktop_config.example.json")
        with open(path) as fh:
            text = fh.read()
        self.assertNotIn("alpaca", text.lower())
        servers = json.loads(text)["mcpServers"]
        self.assertEqual(list(servers), ["my-broker"])
        for k, v in servers["my-broker"]["env"].items():
            self.assertIn("NEVER_IN_CHAT", v)
        self.assertTrue(servers["my-broker"]["command"].startswith("REPLACE_WITH"))

    def test_skill_md_is_broker_neutral(self):
        with open(os.path.join(ROOT, "trader-broker", "SKILL.md"), encoding="utf-8") as fh:
            text = fh.read()
        for word in ("Alpaca", "ALPACA", "place_stock_order", "New York", "TSLA", "IBKR", "Interactive Brokers"):
            self.assertNotIn(word, text)
        self.assertNotIn("$", text.replace("${CLAUDE_SKILL_DIR}", ""))
        for must in ("Needs approval", "Never Always allow", "verify", "separate", "15:15", "paper"):
            self.assertIn(must, text)


class Flow(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.orders = os.path.join(self.d, "orders.json")
        self.cfg = os.path.join(self.d, "config.json")
        self.risk = os.path.join(self.d, "state", "risk.json")
        self.state = os.path.join(self.d, "state", "broker.json")
        self.pf = os.path.join(self.d, "portfolio.json")
        self.plan = os.path.join(self.d, "plan.json")
        self.none = os.path.join(self.d, "none")
        self.write_orders([RELIANCE])
        self.write_cfg(CFG)

    def tearDown(self):
        shutil.rmtree(self.d)

    def write_orders(self, orders):
        with open(self.orders, "w") as fh:
            json.dump({"created_by": "trader-budget", "status": "proposed", "paper": True, "orders": orders}, fh)

    def write_cfg(self, cfg):
        with open(self.cfg, "w") as fh:
            json.dump(cfg, fh)

    def args(self, *extra, **kw):
        return list(extra) + ["--orders", self.orders, "--config", self.cfg, "--risk", self.risk,
                              "--state", self.state, "--now", kw.get("now", NOW)]

    def run_risk(self, now=NOW):
        with redirect_stdout(io.StringIO()):
            return check.main(["--orders", self.orders, "--config", self.cfg, "--portfolio", self.pf,
                               "--prices", self.none, "--state", self.state, "--now", now, "--out", self.risk])

    def state_doc(self):
        with open(self.state) as fh:
            return json.load(fh)

    def test_nothing_is_ready_before_the_risk_check(self):
        code, text, _ = run(self.args("show"))
        self.assertEqual(code, 1)
        self.assertIn("NOT READY", text)
        self.assertNotIn("Reply \"yes\"", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("not ready", err)

    def test_the_full_two_yes_flow_on_paper(self):
        self.assertEqual(self.run_risk(), 0)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 0)
        self.assertIn("ORDER 1 of 1, PAPER (nothing is sent to mybroker; you record the simulated fill by hand), NSE",
                      text)
        for part in RELIANCE_PLAIN:
            self.assertIn("  " + part + "\n", text)
        self.assertIn("READY: trader-risk PASS", text)
        self.assertIn("  Leg 1, entry, the exact fields:", text)
        self.assertIn("    " + json.dumps(ENTRY_LEG) + "\n", text)
        self.assertIn("    " + json.dumps(STOP_LEG) + "\n", text)
        self.assertIn("    " + json.dumps(TARGET_LEG) + "\n", text)
        self.assertIn("SEPARATE orders", text)
        self.assertIn("be flat by 15:15 IST yourself", text)
        self.assertNotIn('"plan"', text)
        self.assertNotIn('"why"', text)
        self.assertNotIn("Allow once", text)          # paper: no connector card
        self.assertNotIn("$", text)

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "sure"))
        self.assertEqual(code, 1)
        self.assertIn("Not recorded", text)
        self.assertFalse(os.path.exists(self.state))

        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("PAPER: nothing goes to a broker", text)
        self.assertIn("separate orders", text)
        code, _, err = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 1)
        self.assertIn("APPROVED", err)

        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "abc123",
                                      "--filled-qty", "62", "--filled-price", "1174.30", "--portfolio", self.pf,
                                      now="2026-10-26 10:16"))
        self.assertEqual(code, 0)
        st = self.state_doc()
        self.assertEqual([r["status"] for r in st["orders"]], ["approved", "placed", "filled"])
        appr, plc = st["orders"][0], st["orders"][1]
        self.assertEqual((appr["reply"], appr["kind"], appr["max_loss"], appr["date"]), ("yes", "entry", 988.9, TODAY))
        self.assertEqual(appr["at"], "2026-10-26T10:15:00+05:30")
        self.assertEqual((appr["symbol"], appr["quantity"], appr["stop"], appr["paper"]),
                         ("RELIANCE", 62, 1158.35, True))
        self.assertEqual((plc["broker_order_id"], plc["max_loss"], plc["kind"]), ("abc123", 988.9, "entry"))
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 427193.4)                 # 500000 - 62 x 1174.30
        self.assertEqual(pf["positions"][0]["shares"], 62)
        self.assertEqual(pf["positions"][0]["lots"][0]["cost"], 72806.6)

        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: placed", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn("1 placed today", text)
        self.assertIn("max loss %s988.90" % R, text)
        self.assertIn('reply "yes"', text)

        # trader-risk now counts it: a second RELIANCE buy today is a duplicate, and the max loss is used
        res = check.check([reliance()], {"budget": 500000}, state=st, now=check.parse_now("2026-10-26 10:30"))
        self.assertIn("already placed today", " ".join(res["results"][0]["reasons"]))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["loss_at_risk"]), (1, 988.9))

    def test_a_live_ticket_prints_the_fields_for_the_connector(self):
        self.write_cfg(dict(CFG, paper=False))
        self.run_risk()
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("LIVE ACCOUNT, REAL MONEY, mybroker, NSE", text)
        self.assertIn("Allow once", text)
        code, text, _ = run(self.args("approve", "--index", "1", "--reply", "yes"))
        self.assertEqual(code, 0)
        self.assertIn("verify its tool and field names yourself", text)
        self.assertEqual(json.loads(text[text.index("{"):text.rindex("}") + 1]), ENTRY_LEG)
        self.assertEqual(self.state_doc()["orders"][0]["paper"], False)

    def test_a_tag_less_market_order_still_renders(self):
        self.write_orders([MARKET_BUY])
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("BUY 10 TCS on NSE at market, intraday MIS, good for today", text)
        self.assertIn("max loss at the stop %s380.00" % R, text)

    def test_flatten_by_the_close(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "abc",
                      "--filled-qty", "62", "--filled-price", "1174.30", "--portfolio", self.pf))
        with open(self.plan, "w") as fh:
            json.dump({"symbol": "RELIANCE", "flat_by": "15:15"}, fh)
        late = "2026-10-26 15:15"
        code, text, _ = run(["flatten", "--symbol", "RELIANCE.NS", "--orders", self.orders, "--portfolio", self.pf,
                             "--plan", self.plan, "--config", self.cfg, "--now", late])
        self.assertEqual(code, 0)
        self.assertIn("SELL 62 RELIANCE on NSE at market, intraday MIS, good for today (flat by 15:15 IST)", text)
        self.assertIn("replaced 1 earlier proposal", text)
        self.assertIn("cancel them first", text)
        self.assertIn("square off", text)
        with open(self.orders) as fh:
            doc = json.load(fh)
        self.assertEqual(ticket.entry_leg(doc["orders"][0]),
                         {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "quantity": 62,
                          "product": "MIS", "order_type": "MARKET", "validity": "DAY",
                          "client_tag": "tb-RELIANCE-2026-10-26-flat"})
        self.assertEqual(doc["created_by"], "trader-broker")
        self.assertEqual(self.run_risk(now=late), 0)       # after the 14:30 entry cutoff: an exit still passes
        with open(self.risk) as fh:
            self.assertEqual(json.load(fh)["results"][0]["kind"], "exit")
        code, text, _ = run(self.args("show", now=late))
        self.assertEqual(code, 0)
        self.assertIn("READY", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes sell 62 RELIANCE", now=late))[0], 0)
        code, _, _ = run(self.args("result", "--index", "1", "--status", "placed", "--filled-qty", "62",
                                   "--filled-price", "1181.10", "--portfolio", self.pf, now="2026-10-26 15:16"))
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual((pf["cash"], pf["positions"]), (500421.6, []))     # 500000 + 62 x (1181.10 - 1174.30)
        code, _, err = run(["flatten", "--symbol", "RELIANCE", "--orders", self.orders, "--portfolio", self.pf,
                            "--now", late])
        self.assertEqual(code, 1)
        self.assertIn("nothing to flatten", err)

    def test_flatten_flat_by_falls_back_to_config_then_1515(self):
        for cfg, want in ((dict(CFG, flat_by="14:45"), "14:45"), ({}, "15:15")):
            self.write_cfg(cfg)
            code, text, _ = run(["flatten", "--symbol", "TCS", "--qty", "4", "--orders", self.orders,
                                 "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
            self.assertEqual(code, 0)
            self.assertIn("(flat by %s IST)" % want, text)

    def test_flatten_with_the_broker_qty(self):
        code, text, _ = run(["flatten", "--symbol", "reliance", "--qty", "62", "--orders", self.orders,
                             "--portfolio", self.none, "--plan", self.none, "--config", self.cfg, "--now", NOW])
        self.assertEqual(code, 0)
        self.assertNotIn("Check it against", text)
        self.assertEqual(run(["flatten", "--symbol", "RELIANCE", "--qty", "0", "--orders", self.orders])[0], 1)
        code, _, err = run(["flatten", "--symbol", "RELIANCE", "--qty", "2.5", "--orders", self.orders])
        self.assertEqual(code, 1)
        self.assertIn("whole number", err)

    def test_an_edited_order_is_not_ready(self):
        self.run_risk()
        self.write_orders([reliance(quantity=63)])
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertEqual(code, 1)
        self.assertIn("was not in the last risk check", text)

    def test_an_edited_stop_is_not_ready(self):
        self.run_risk()
        self.write_orders([reliance(stop_loss=1150.0)])
        self.assertIn("was not in the last risk check", run(self.args("show", "--index", "1"))[1])

    def test_yesterdays_check_does_not_count(self):
        self.run_risk(now="2026-10-23 10:15")
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("not today", text)

    def test_blocked_orders_are_never_ready(self):
        self.assertEqual(self.run_risk(now="2026-10-26 09:20"), 1)
        code, text, _ = run(self.args("show", "--index", "1", now="2026-10-26 09:20"))
        self.assertIn("BLOCKED by trader-risk: window: no new entries before 09:30", text)
        code, _, _ = run(self.args("approve", "--index", "1", "--reply", "yes", now="2026-10-26 09:20"))
        self.assertEqual(code, 1)

    def test_a_too_big_loss_is_blocked_by_risk(self):
        self.write_orders([reliance(quantity=70, plan=dict(RELIANCE["plan"], max_loss=1116.5))])
        self.assertEqual(self.run_risk(), 1)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("BLOCKED by trader-risk", text)
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes"))[0], 1)

    def test_result_needs_an_approval_and_decline_is_logged(self):
        self.run_risk()
        code, _, err = run(self.args("result", "--index", "1", "--status", "placed"))
        self.assertEqual(code, 1)
        self.assertIn("no yes recorded", err)
        code, text, _ = run(self.args("decline", "--index", "1", "--reply", "no thanks"))
        self.assertEqual(code, 0)
        self.assertIn("Nothing was sent", text)
        code, text, _ = run(self.args("show", "--index", "1"))
        self.assertIn("DONE: declined", text)
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertIn('reply "no thanks"', text)

    def test_denied_and_failed_results_are_logged(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        code, text, _ = run(self.args("result", "--index", "1", "--status", "failed", "--note", "rejected: RMS"))
        self.assertEqual(code, 0)
        rec = self.state_doc()["orders"][-1]
        self.assertEqual((rec["status"], rec["note"], rec["max_loss"]), ("failed", "rejected: RMS", 988.9))
        # a failed order is not retried on the old yes
        self.assertIn("DONE: failed", run(self.args("show", "--index", "1"))[1])
        self.assertEqual(run(self.args("approve", "--index", "1", "--reply", "yes"))[0], 1)

    def test_a_placed_result_without_a_fill_says_how_to_record_it(self):
        self.run_risk()
        run(self.args("approve", "--index", "1", "--reply", "yes"))
        code, text, _ = run(self.args("result", "--index", "1", "--status", "placed", "--broker-id", "77"))
        self.assertIn("ticket.py fill --symbol RELIANCE --side buy", text)
        self.assertIn("cancel the other leg", text)
        self.assertFalse(os.path.exists(self.pf))

    def test_the_daily_loss_log_feeds_trader_risk(self):
        # two entries placed today: their max losses add up against max_daily_loss_inr
        self.write_orders([reliance(), MARKET_BUY])
        self.assertEqual(self.run_risk(), 0)
        for i in (1, 2):
            reply = "yes" if i == 1 else "yes 10 TCS"
            self.assertEqual(run(self.args("approve", "--index", str(i), "--reply", reply))[0], 0)
            run(self.args("result", "--index", str(i), "--status", "placed"))
        st = self.state_doc()
        placed = [r for r in st["orders"] if r["status"] == "placed"]
        self.assertEqual([(r["symbol"], r["max_loss"]) for r in placed], [("RELIANCE", 988.9), ("TCS", 380.0)])
        res = check.check([], {"budget": 500000}, state=st, now=check.parse_now("2026-10-26 11:00"))
        self.assertEqual((res["today"]["entries_placed"], res["today"]["loss_at_risk"]), (2, 1368.9))

    def test_fill_sell_relieves_the_oldest_lot(self):
        pf = {"cash": 0, "positions": [{"ticker": "INFY", "shares": 3, "lots": [
            {"id": "INFY-1", "date": "2025-01-02", "shares": 2, "cost": 2200.0},
            {"id": "INFY-2", "date": "2026-01-02", "shares": 1, "cost": 1150.0}]}]}
        with open(self.pf, "w") as fh:
            json.dump(pf, fh)
        code, text, _ = run(["fill", "--symbol", "infy.ns", "--side", "sell", "--qty", "2", "--price", "1146.5",
                             "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        self.assertIn("Recorded sell 2 INFY at %s1,146.50 on 2026-10-26" % R, text)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual(pf["cash"], 2293.0)
        lots = pf["positions"][0]["lots"]
        self.assertEqual([l["id"] for l in lots], ["INFY-2"])
        self.assertEqual((pf["positions"][0]["shares"], lots[0]["shares"], lots[0]["cost"]), (1, 1, 1150.0))
        rec = self.state_doc()["orders"][-1]
        self.assertEqual((rec["status"], rec["symbol"], rec["quantity"], rec["price"]), ("filled", "INFY", 2, 1146.5))
        code, _, err = run(["fill", "--symbol", "INFY", "--side", "sell", "--qty", "5", "--price", "1146.5",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertIn("holds 1", err)
        self.assertNotIn("Traceback", err)
        code, _, err = run(["fill", "--symbol", "INFY", "--side", "buy", "--qty", "1.5", "--price", "1100",
                            "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertIn("whole number", err)

    def test_fill_starts_a_portfolio_from_the_budget(self):
        code, _, _ = run(["fill", "--symbol", "TCS", "--side", "buy", "--qty", "10", "--price", "2478",
                          "--date", TODAY, "--portfolio", self.pf, "--config", self.cfg, "--state", self.state])
        self.assertEqual(code, 0)
        with open(self.pf) as fh:
            pf = json.load(fh)
        self.assertEqual((pf["cash"], pf["positions"][0]["ticker"], pf["positions"][0]["shares"]),
                         (475220.0, "TCS", 10))
        code, _, err = run(["fill", "--symbol", "TCS", "--side", "buy", "--qty", "1", "--price", "2478",
                            "--portfolio", self.none, "--config", self.none, "--state", self.state])
        self.assertEqual(code, 1)
        self.assertIn("no budget", err)

    def test_log_without_decisions(self):
        code, text, _ = run(["log", "--state", self.state, "--now", NOW])
        self.assertEqual(code, 1)
        self.assertIn("No decisions recorded today", text)

    def test_show_json(self):
        self.run_risk()
        code, text, _ = run(self.args("show", "--index", "1", "--json"))
        self.assertEqual(code, 0)
        t = json.loads(text)
        self.assertTrue(t["ready"])
        self.assertEqual([l["leg"] for l in t["legs"]], ["entry", "stop-loss", "target"])
        self.assertEqual(t["legs"][1]["trigger_price"], 1158.35)


if __name__ == "__main__":
    unittest.main()
