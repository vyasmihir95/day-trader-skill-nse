#!/usr/bin/env python3
"""Order tickets and the decision log for trader-broker (NSE, rupees). Standard library only.

This tool never talks to a broker and never places anything. It does its
work around the one moment that matters, your yes. Orders are broker-neutral
NSE orders (the shape trader-budget writes): symbol, exchange "NSE", side,
quantity (whole shares), product (MIS intraday), order_type, limit_price,
validity, and for a day trade a stop_loss and a take_profit.

  show      renders each proposed order as a plain-English ticket plus the
            exact JSON of each leg: the entry, then the stop-loss and the
            target as SEPARATE protective orders (many Indian brokers have no
            bracket orders, so you place and watch those two yourself). It
            shows the max loss at the stop. An order is READY only if
            trader-risk passed THIS exact order today (matched by fingerprint)
            and nobody has decided on it yet.
  approve   records your explicit yes for ONE order, with your reply word for
            word. It refuses "yes to all", "sure", "ok", a yes that names a
            different stock or amount, and any order that is not READY.
  decline   records a no.
  result    records what happened: placed (with the broker's order id, or a
            paper fill you give by hand), denied, or failed.
  fill      records a fill in trader/portfolio.json (cash and shares).
  flatten   builds a sell ticket that closes a symbol's open position by
            flat_by (15:15 IST). It still goes through trader-risk and your yes.
  log       prints today's decisions.

    python3 ticket.py show                    # every order, READY or not
    python3 ticket.py show --index 1 --json   # one ticket as JSON
    python3 ticket.py approve --index 1 --reply "yes"
    python3 ticket.py result --index 1 --status placed --broker-id 2310260001 --filled-qty 62 --filled-price 1174.30
    python3 ticket.py fill --symbol RELIANCE --side sell --qty 62 --price 1190.25
    python3 ticket.py flatten --symbol RELIANCE --qty 62
    python3 ticket.py log

Paper first ("paper": true, the default): there is no paper API. Nothing is
sent anywhere; after your yes you record the simulated fill by hand with
`result --filled-qty --filled-price` or `fill`. Brokerage, STT, GST and stamp
duty are not modelled.

Everything is written to trader/state/broker.json (append only). Dates are
IST dates; --now "2026-10-26 10:15" pins the clock (IST).
"""

import argparse
import datetime
import hashlib
import json
import os
import re
import sys

# the fields that make an order what it is: the fingerprint hashes these (why / plan are commentary).
# Same list and same function as trader-risk/check.py.
ORDER_FIELDS = ("symbol", "exchange", "side", "quantity", "product", "order_type", "limit_price",
                "trigger_price", "validity", "stop_loss", "take_profit", "client_tag")
ENTRY_FIELDS = ("symbol", "exchange", "side", "quantity", "product", "order_type", "limit_price",
                "trigger_price", "validity", "client_tag")
VALIDITY_WORDS = {"DAY": "good for today", "IOC": "immediate or cancel"}
PRODUCT_WORDS = {"MIS": "intraday MIS", "CNC": "delivery CNC", "NRML": "NRML", "MTF": "MTF"}
RUPEE = "\u20b9"
YES_WORDS = ("yes", "y")
BATCH_WORDS = {"all", "every", "everything", "rest", "remaining", "them", "both", "batch", "each"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later",
               "hold", "unless", "if"}
IST = datetime.timedelta(hours=5, minutes=30)   # UTC+5:30, no daylight saving


class TicketError(Exception):
    pass


# ---------------------------------------------------------------- time (IST, a constant offset)

def to_local(utc):
    return utc.replace(tzinfo=None) + IST


def local_to_utc(local):
    return local - IST


def parse_now(text):
    """ISO time -> naive UTC. A time without an offset is taken as IST."""
    if not text:
        return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0, tzinfo=None)
    t = text.strip().replace("Z", "+00:00")
    try:
        dt = datetime.datetime.fromisoformat(t)
    except ValueError:
        raise TicketError("--now wants a time like \"2026-10-26 10:15\" (IST) or ...Z (UTC)")
    if dt.tzinfo is None:
        return local_to_utc(dt)
    return (dt - dt.utcoffset()).replace(tzinfo=None)


def ist_iso(now_utc):
    """naive UTC -> "2026-10-26T10:15:00+05:30" (IST wall clock with its offset)."""
    return "%s+05:30" % to_local(now_utc).replace(microsecond=0).isoformat()


def bare(sym):
    """"reliance.ns" -> "RELIANCE": orders and files carry the bare NSE symbol."""
    sym = str(sym or "").strip().upper()
    return sym[:-3] if sym.endswith(".NS") else sym


# ---------------------------------------------------------------- files

def read_json(path, what, required=True):
    if not path or not os.path.exists(path):
        if required:
            raise TicketError("no %s at %s" % (what, path))
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as e:
        raise TicketError("%s is not valid JSON: %s" % (path, e))


def write_json(path, data):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def rec_day(rec):
    """The trading day a decision belongs to: its "date" (the day it was made under), else the day of "at"."""
    return str(rec.get("date") or str(rec.get("at", ""))[:10])


# ---------------------------------------------------------------- the order

def fingerprint(order):
    """Same function as trader-risk/check.py: identifies the exact order."""
    core = {k: order[k] for k in ORDER_FIELDS if k in order and order[k] is not None}
    blob = json.dumps(core, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        f = float(v)
        return str(int(f)) if f == int(f) else repr(f)
    return str(v)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def money(v):
    return RUPEE + "{:,.2f}".format(float(v))


def entry_leg(order):
    """The entry as one broker order: the order's own fields (NSE names, plain numbers).
    "why", "plan", stop_loss and take_profit are not part of it: the protective
    legs are separate orders, and why / plan are ours and are never sent."""
    return {k: order[k] for k in ENTRY_FIELDS if k in order and order[k] is not None}


def legs(order):
    """[(name, order dict)]: the entry, then for a buy its stop-loss leg (a sell, stop-loss market at the
    trigger) and its target leg (a sell limit). They are separate orders, not linked by the broker."""
    out = [("entry", entry_leg(order))]
    if str(order.get("side", "")).lower() != "buy":
        return out
    base = {"symbol": order.get("symbol"), "exchange": order.get("exchange", "NSE"), "side": "sell",
            "quantity": order.get("quantity"), "product": order.get("product", "MIS")}
    tag = order.get("client_tag")
    if order.get("stop_loss") is not None:
        leg = dict(base, order_type="SL-M", trigger_price=order["stop_loss"], validity="DAY")
        if tag:
            leg["client_tag"] = tag + "-sl"
        out.append(("stop-loss", leg))
    if order.get("take_profit") is not None:
        leg = dict(base, order_type="LIMIT", limit_price=order["take_profit"], validity="DAY")
        if tag:
            leg["client_tag"] = tag + "-tp"
        out.append(("target", leg))
    return out


def has_protective_legs(order):
    return len(legs(order)) > 1


def max_loss(order):
    """(rupees, stop) lost if the stop fills exactly at the stop, or (None, stop)."""
    plan = order.get("plan") or {}
    stop = _f(order.get("stop_loss")) or _f(plan.get("stop"))
    q = _f(order.get("quantity"))
    if not stop or not q:
        return None, stop
    if str(order.get("order_type", "MARKET")).upper() in ("LIMIT", "SL") and _f(order.get("limit_price")):
        entry = _f(order["limit_price"])
    elif _f(plan.get("max_loss")) is not None:
        return round(_f(plan["max_loss"]), 2), stop
    else:
        entry = _f(plan.get("entry"))
    if not entry:
        return None, stop
    risk = abs(entry - stop)
    return round(q * int(round(risk * 100)) / 100.0, 2), stop


def plain_parts(order):
    """The ticket in plain words, one part per line."""
    side = str(order.get("side", "")).upper()
    sym = order.get("symbol", "?")
    q = _num(order.get("quantity"))
    typ = str(order.get("order_type", "MARKET")).upper()
    if typ == "LIMIT":
        how = "at a limit of %s" % money(order.get("limit_price"))
    elif typ == "SL":
        how = "stop-limit, trigger %s, limit %s" % (money(order.get("trigger_price")), money(order.get("limit_price")))
    elif typ == "SL-M":
        how = "stop-market, trigger %s" % money(order.get("trigger_price"))
    else:
        how = "at market"
    prod = PRODUCT_WORDS.get(str(order.get("product", "MIS")).upper(), order.get("product"))
    val = VALIDITY_WORDS.get(str(order.get("validity", "DAY")).upper(), order.get("validity"))
    parts = ["%s %s %s on %s %s, %s, %s" % (side, q, sym, order.get("exchange", "NSE"), how, prod, val)]
    if str(order.get("side", "")).lower() == "buy":
        stop = order.get("stop_loss")
        if stop is not None:
            parts.append("stop-loss %s (a separate sell order, trigger %s)" % (money(stop), money(stop)))
        if order.get("take_profit") is not None:
            parts.append("target %s (a separate sell limit order)" % money(order["take_profit"]))
        loss, stop = max_loss(order)
        if loss is not None:
            parts.append("max loss at the stop %s if it fills at %s (it can fill worse in a fast market)"
                         % (money(loss), money(stop)))
    return parts


def plain(order):
    """BUY 62 RELIANCE on NSE at a limit of Rs 1,174.30, intraday MIS, good for today / stop-loss ..."""
    return " / ".join(plain_parts(order))


def amount_numbers(order):
    nums = []
    for k in ("quantity", "limit_price", "trigger_price", "stop_loss", "take_profit"):
        if order.get(k) is not None:
            v = _f(order[k])
            if v is not None:
                nums.append(v)
    loss, _ = max_loss(order)
    if loss is not None:
        nums.append(loss)
    return nums


def parse_yes(reply, order, batch_symbols=()):
    """Is this reply an explicit yes for THIS one order? Returns (ok, why)."""
    raw = (reply or "").strip()
    if not raw:
        return False, "the reply is empty"
    if "?" in raw:
        return False, "the reply is a question, not a yes"
    words = re.findall(r"[a-z']+", raw.lower())
    if not words or words[0] not in YES_WORDS:
        return False, "only a reply that starts with \"yes\" counts (not ok, sure, go, do it)"
    if BATCH_WORDS & set(words):
        return False, "a yes covers one order at a time, not a batch"
    if DOUBT_WORDS & set(words[1:]):
        return False, "the reply has a condition or a doubt in it; ask again"
    sym = bare(order.get("symbol"))
    tokens = {bare(t) for t in re.findall(r"[A-Za-z][A-Za-z0-9&\-]{0,19}(?:\.NS)?", raw)}
    others = {bare(s) for s in batch_symbols} - {sym}
    named = tokens & others
    if named:
        return False, "the reply names %s, but this order is for %s" % (", ".join(sorted(named)), sym)
    nums = [float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", raw)]
    if nums:
        want = amount_numbers(order)
        if not any(abs(n - w) < 0.005 for n in nums for w in want):
            return False, "the reply says %s, but this order is %s" % (
                ", ".join(_num(n) for n in nums), plain(order))
    return True, "explicit yes for %s" % plain(order)


# ---------------------------------------------------------------- state

def load_state(path):
    return read_json(path, "broker state", required=False) or {"orders": []}


def latest(state, fp, today):
    """The latest decision recorded today for this fingerprint, or None."""
    found = None
    for rec in state.get("orders", []):
        if rec.get("fingerprint") == fp and rec_day(rec) == today:
            found = rec
    return found


def risk_verdict(risk, fp, today):
    if not risk:
        return None, "trader-risk has not checked these orders (run check.py)"
    if risk.get("date") != today:
        return None, "the risk check is from %s, not today; run check.py again" % risk.get("date")
    for r in risk.get("results", []):
        if r.get("fingerprint") == fp:
            return r, None
    return None, "this exact order was not in the last risk check (it changed or is new); run check.py again"


def status_of(order, risk, state, today):
    """(ready, label, risk_result)"""
    fp = fingerprint(order)
    rv, why = risk_verdict(risk, fp, today)
    if rv is None:
        return False, "NOT READY: " + why, None
    if rv.get("verdict") != "PASS":
        return False, "BLOCKED by trader-risk: " + "; ".join(rv.get("reasons", [])), rv
    rec = latest(state, fp, today)
    if rec:
        st = rec.get("status")
        if st == "approved":
            return False, "APPROVED by you at %s, waiting for you to place it" % rec.get("at"), rv
        return False, "DONE: %s at %s" % (st, rec.get("at")), rv
    return True, "READY: trader-risk PASS, waiting for your yes", rv


def kind_of(order, rv):
    """entry or exit, as trader-risk saw it (older checks: a buy is an entry)."""
    if rv and rv.get("kind"):
        return rv["kind"]
    return "entry" if str(order.get("side", "")).lower() == "buy" else "exit"


# ---------------------------------------------------------------- rendering

def account_line(cfg):
    broker = str(cfg.get("broker") or "your broker")
    if cfg.get("paper", True):
        return "PAPER (nothing is sent to %s; you record the simulated fill by hand), NSE" % broker
    return "LIVE ACCOUNT, REAL MONEY, %s, NSE" % broker


def render(order, idx, total, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    lines = ["ORDER %d of %d, %s" % (idx, total, account_line(cfg))]
    for part in plain_parts(order):
        lines.append("  " + part)
    if order.get("why"):
        lines.append("  why: " + order["why"])
    lines.append("  " + label)
    for w in (rv or {}).get("warnings", []):
        lines.append("  note: " + w)
    for n, (name, leg) in enumerate(legs(order), 1):
        lines.append("  Leg %d, %s, the exact fields:" % (n, name))
        lines.append("    " + json.dumps(leg))
    if has_protective_legs(order):
        lines.append("  The stop-loss and the target are SEPARATE orders. Many Indian brokers have no bracket")
        lines.append("  order, so nothing links them: place both right after the entry fills, watch them, and")
        lines.append("  cancel the other one when one fills. Brokers also square off intraday positions around")
        lines.append("  15:15 to 15:20 on their own: be flat by %s IST yourself." % (cfg.get("flat_by") or "15:15"))
    if ready:
        lines.append("  Reply \"yes\" to take this one order, or \"no\" to skip it.")
        if not cfg.get("paper", True):
            lines.append("  Claude's own card then asks again for each order tool call: Allow once places it, Deny stops it.")
    return "\n".join(lines), ready


def ticket_json(order, idx, cfg, risk, state, today):
    ready, label, rv = status_of(order, risk, state, today)
    loss, stop = max_loss(order)
    return {"index": idx, "ready": ready, "status": label, "plain": plain(order),
            "fingerprint": fingerprint(order), "paper": bool(cfg.get("paper", True)),
            "broker": cfg.get("broker"), "legs": [dict(leg, leg=name) for name, leg in legs(order)],
            "why": order.get("why", ""), "protective_legs": has_protective_legs(order),
            "max_loss": loss, "stop": stop}


# ---------------------------------------------------------------- fills -> portfolio

def whole(x, what):
    f = _f(x)
    if f is None or f <= 0 or abs(f - round(f)) > 1e-9:
        raise TicketError("%s must be a positive whole number of shares, got %r" % (what, x))
    return int(round(f))


def apply_fill(portfolio_path, cfg, symbol, side, qty, price, date):
    """Record a filled order in trader/portfolio.json: cash and shares, with a
    cost-basis lot per buy (oldest lots are relieved first on a sell)."""
    pf = read_json(portfolio_path, "portfolio", required=False)
    if pf is None:
        budget = cfg.get("budget")
        if not budget:
            raise TicketError("no %s yet and no budget in the config to start it from" % portfolio_path)
        pf = {"cash": float(budget), "positions": []}
    symbol = bare(symbol)
    qty, price = whole(qty, "a fill's quantity"), _f(price)
    if price is None or price <= 0:
        raise TicketError("a fill needs a positive price")
    cost = round(qty * price, 2)
    pos = None
    for p in pf.setdefault("positions", []):
        if bare(p.get("ticker") or p.get("symbol")) == symbol:
            pos = p
    if side == "buy":
        if pos is None:
            pos = {"ticker": symbol, "shares": 0, "lots": []}
            pf["positions"].append(pos)
        lots = pos.setdefault("lots", [])
        lots.append({"id": "%s-%d" % (symbol, len(lots) + 1), "date": date, "shares": qty, "cost": cost})
        pos["shares"] = int(round(float(pos.get("shares", 0)) + qty))
        pf["cash"] = round(float(pf.get("cash", 0)) - cost, 2)
    else:
        if pos is None or float(pos.get("shares", 0)) + 1e-9 < qty:
            raise TicketError("cannot record selling %s %s: the portfolio holds %s"
                              % (qty, symbol, _num(pos.get("shares", 0)) if pos else 0))
        left = qty
        for lot in sorted(pos.get("lots", []), key=lambda l: l.get("date", "")):
            if left <= 0:
                break
            take = min(left, int(round(float(lot["shares"]))))
            per = float(lot["cost"]) / float(lot["shares"]) if float(lot["shares"]) else 0
            lot["cost"] = round(float(lot["cost"]) - per * take, 2)
            lot["shares"] = int(round(float(lot["shares"]))) - take
            left -= take
        pos["lots"] = [l for l in pos.get("lots", []) if float(l["shares"]) > 0]
        pos["shares"] = int(round(float(pos["shares"]))) - qty
        if pos["shares"] <= 0:
            pf["positions"].remove(pos)
        pf["cash"] = round(float(pf.get("cash", 0)) + cost, 2)
    write_json(portfolio_path, pf)
    return pf


def log_fill(state_path, now, today, symbol, side, qty, price):
    """A fill in the log: what was bought and sold, and at what price."""
    state = load_state(state_path)
    state.setdefault("orders", []).append({
        "at": ist_iso(now), "date": today, "status": "filled", "symbol": bare(symbol), "side": side,
        "quantity": whole(qty, "a fill's quantity"), "price": float(price),
        "plain": "%s %s %s at %s" % (side.upper(), whole(qty, "qty"), bare(symbol), money(price))})
    write_json(state_path, state)


# ---------------------------------------------------------------- commands

def _clock(a):
    now = parse_now(getattr(a, "now", None))
    today = getattr(a, "today", None) or to_local(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", today):
        raise TicketError("--today wants a date like 2026-10-26")
    return now, today


def _load(a):
    doc = read_json(a.orders, "orders file (run trader-budget, or ticket.py flatten, first)")
    orders = doc.get("orders", doc) if isinstance(doc, dict) else doc
    if not isinstance(orders, list) or not orders:
        raise TicketError("no orders in %s" % a.orders)
    cfg = read_json(a.config, "config", required=False) or {}
    risk = read_json(a.risk, "risk check", required=False)
    state = load_state(a.state)
    now, today = _clock(a)
    return orders, cfg, risk, state, today, now


def _pick(orders, index):
    if index is None:
        raise TicketError("say which order: --index N")
    if not 1 <= index <= len(orders):
        raise TicketError("there is no order %d (there are %d)" % (index, len(orders)))
    return orders[index - 1]


def cmd_show(a):
    orders, cfg, risk, state, today, now = _load(a)
    picks = [(a.index, _pick(orders, a.index))] if a.index else list(enumerate(orders, 1))
    if a.json:
        out = [ticket_json(o, i, cfg, risk, state, today) for i, o in picks]
        print(json.dumps(out[0] if a.index else out, indent=2))
        return 0 if any(t["ready"] for t in out) else 1
    blocks, any_ready = [], False
    for i, o in picks:
        text, ready = render(o, i, len(orders), cfg, risk, state, today)
        blocks.append(text)
        any_ready = any_ready or ready
    print("\n\n".join(blocks))
    print("\nOne order at a time: a yes covers one ticket, never the batch. Education and tooling, "
          "not financial advice.")
    return 0 if any_ready else 1


def cmd_approve(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    ready, label, rv = status_of(order, risk, state, today)
    if not ready:
        raise TicketError("order %d is not ready: %s" % (a.index, label))
    ok, why = parse_yes(a.reply, order, [o.get("symbol", "") for o in orders])
    if not ok:
        print("Not recorded: %s. Ask the user again with the ticket for order %d." % (why, a.index))
        return 1
    loss, stop = max_loss(order)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"),
           "quantity": order.get("quantity"), "kind": kind_of(order, rv), "max_loss": loss, "stop": stop,
           "plain": plain(order), "status": "approved", "reply": a.reply,
           "paper": bool(cfg.get("paper", True)), "broker": cfg.get("broker")}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded your yes for order %d: %s." % (a.index, plain(order)))
    if cfg.get("paper", True):
        print("PAPER: nothing goes to a broker. Record the simulated fill: ticket.py result --index %d --status "
              "placed --filled-qty Q --filled-price P" % a.index)
    else:
        print("Now place the entry with your broker connector's order tool (verify its tool and field names "
              "yourself; this JSON is broker-neutral). The exact fields:")
        print(json.dumps(entry_leg(order), indent=2))
        print("Claude's card shows Allow once / Deny. Then: ticket.py result --index %d --status "
              "placed|denied|failed" % a.index)
    if has_protective_legs(order):
        print("Once the entry has filled, the stop-loss and target legs are two separate orders (see show). "
              "Place them with the same care: each order tool call still gets its own Allow once.")
    return 0


def cmd_decline(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    rec = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fingerprint(order),
           "symbol": str(order.get("symbol", "")).upper(), "side": order.get("side"),
           "plain": plain(order), "status": "declined", "reply": a.reply or "no"}
    state.setdefault("orders", []).append(rec)
    write_json(a.state, state)
    print("Recorded: you said no to order %d (%s). Nothing was sent." % (a.index, plain(order)))
    return 0


def cmd_result(a):
    orders, cfg, risk, state, today, now = _load(a)
    order = _pick(orders, a.index)
    fp = fingerprint(order)
    rec = latest(state, fp, today)
    if not rec or rec.get("status") != "approved":
        raise TicketError("order %d has no yes recorded today; a result can only follow an approval" % a.index)
    new = {"at": ist_iso(now), "date": today, "index": a.index, "fingerprint": fp, "symbol": rec["symbol"],
           "side": rec["side"], "plain": rec["plain"], "status": a.status}
    for k in ("quantity", "kind", "max_loss", "stop"):
        if rec.get(k) is not None:
            new[k] = rec[k]
    if a.broker_id:
        new["broker_order_id"] = a.broker_id
    if a.note:
        new["note"] = a.note
    state["orders"].append(new)
    write_json(a.state, state)
    print("Recorded order %d as %s." % (a.index, a.status))
    if a.status == "placed" and a.filled_qty and a.filled_price:
        apply_fill(a.portfolio, cfg, rec["symbol"], rec["side"], a.filled_qty, a.filled_price, today)
        log_fill(a.state, now, today, rec["symbol"], rec["side"], a.filled_qty, a.filled_price)
        print("Added the fill to %s." % a.portfolio)
    elif a.status == "placed":
        print("When it fills, record it: ticket.py fill --symbol %s --side %s --qty Q --price P"
              % (rec["symbol"], rec["side"]))
        if rec.get("kind") == "entry" and has_protective_legs(order):
            print("When the stop-loss or the target fills later, record that sell the same way, and cancel the "
                  "other leg.")
    return 0


def cmd_fill(a):
    cfg = read_json(a.config, "config", required=False) or {}
    now = parse_now(a.now)
    date = a.date or to_local(now).date().isoformat()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        raise TicketError("--date wants a date like 2026-10-26")
    pf = apply_fill(a.portfolio, cfg, a.symbol, a.side, a.qty, a.price, date)
    log_fill(a.state, now, date, a.symbol, a.side, a.qty, a.price)
    print("Recorded %s %s %s at %s on %s in %s. Cash now %s." % (a.side, a.qty, bare(a.symbol),
                                                                money(a.price), date, a.portfolio,
                                                                money(pf["cash"])))
    return 0


def cmd_flatten(a):
    cfg = read_json(a.config, "config", required=False) or {}
    sym = bare(a.symbol)
    now, today = _clock(a)
    source = "your broker's positions"
    if a.qty is not None:
        qty = whole(a.qty, "--qty (the shares you hold, like --qty 62)")
    else:
        pf = read_json(a.portfolio, "portfolio", required=False) or {}
        qty = sum(_f(p.get("shares", p.get("quantity", p.get("qty")))) or 0 for p in pf.get("positions", [])
                  if bare(p.get("ticker") or p.get("symbol")) == sym)
        source = a.portfolio
        if qty <= 1e-9:
            raise TicketError("nothing to flatten: no %s shares in %s. Check your broker's positions and "
                              "pass --qty if you do hold some" % (sym, a.portfolio))
        qty = whole(qty, "the shares held")
    plan = read_json(a.plan, "plan", required=False) or {}
    flat_by = (plan.get("flat_by") if bare(plan.get("symbol")) == sym else None) \
        or cfg.get("flat_by") or "15:15"
    order = {"symbol": sym, "exchange": "NSE", "side": "sell", "quantity": qty, "product": "MIS",
             "order_type": "MARKET", "validity": "DAY", "client_tag": "tb-%s-%s-flat" % (sym, today),
             "why": "flat by %s IST: closes the %s day trade before the close (quantity from %s)"
             % (flat_by, sym, source)}
    old = read_json(a.orders, "orders", required=False)
    replaced = len(old.get("orders", [])) if isinstance(old, dict) else 0
    doc = {"created_by": "trader-broker", "created_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "status": "proposed",
           "paper": bool(cfg.get("paper", True)),
           "note": "Flatten ticket. Nothing is placed until trader-risk passes it and you say yes in the chat.",
           "source": "ticket.py flatten %s on %s" % (sym, today), "orders": [order]}
    write_json(a.orders, doc)
    print("Flatten %s: %s (flat by %s IST)." % (sym, plain(order), flat_by))
    print("Wrote it to %s%s." % (a.orders, (" (it replaced %d earlier proposal%s)"
                                             % (replaced, "" if replaced == 1 else "s")) if replaced else ""))
    if a.qty is None:
        print("The quantity comes from %s. Check it against your broker's positions before you say yes." % source)
    print("Next:")
    print("  1. trader-risk: check.py. A sell that only closes what you hold is never blocked by the daily")
    print("     limits or the entry window.")
    print("  2. ticket.py show, then the user's yes for this one order.")
    print("  3. If the stop-loss and target orders are still open at your broker, cancel them first: they")
    print("     are separate orders that would still be waiting, and some brokers reject a sell while")
    print("     those shares are held for them. Then place the sell, then ticket.py result (or fill).")
    print("  Brokers auto square off intraday positions around 15:15 to 15:20 (and charge for it): do not rely on it.")
    return 0


def cmd_log(a):
    state = load_state(a.state)
    now, today = _clock(a)
    rows = [r for r in state.get("orders", []) if a.all or rec_day(r) == today]
    if not rows:
        print("No decisions recorded%s." % ("" if a.all else " today"))
        return 1
    for r in rows:
        extra = (" id " + r["broker_order_id"]) if r.get("broker_order_id") else ""
        reply = (' reply "%s"' % r["reply"]) if r.get("reply") and r["status"] in ("approved", "declined") else ""
        loss = (" max loss %s" % money(r["max_loss"])) if r.get("max_loss") is not None and r["status"] == "placed" else ""
        idx = ("#%s" % r["index"]) if r.get("index") is not None else "  "
        print("%s  %s %-9s %s%s%s%s" % (r.get("at"), idx, r.get("status"), r.get("plain"), reply, loss, extra))
    placed = sum(1 for r in rows if r.get("status") == "placed")
    print("%d placed%s." % (placed, "" if a.all else " today"))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Order tickets and your yes (trader-broker). Never places orders.")
    sub = ap.add_subparsers(dest="cmd")

    def clock(p):
        p.add_argument("--now", help="pretend it is this IST time, e.g. \"2026-10-26 10:15\"")
        p.add_argument("--today", help="YYYY-MM-DD (default: today in IST)")

    def common(p):
        p.add_argument("--orders", default="trader/orders.json")
        p.add_argument("--config", default="trader/config.json")
        p.add_argument("--risk", default="trader/state/risk.json")
        p.add_argument("--state", default="trader/state/broker.json")
        clock(p)

    p = sub.add_parser("show", help="render the tickets")
    common(p)
    p.add_argument("--index", type=int)
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve", help="record the user's explicit yes for one order")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply", required=True, help="the user's reply, word for word")
    p = sub.add_parser("decline", help="record a no")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--reply")
    p = sub.add_parser("result", help="record what happened after the Allow once / Deny card")
    common(p)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--status", required=True, choices=["placed", "denied", "failed"])
    p.add_argument("--broker-id")
    p.add_argument("--note")
    p.add_argument("--filled-qty")
    p.add_argument("--filled-price")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p = sub.add_parser("fill", help="record a fill in trader/portfolio.json")
    p.add_argument("--symbol", required=True)
    p.add_argument("--side", required=True, choices=["buy", "sell"])
    p.add_argument("--qty", required=True)
    p.add_argument("--price", required=True)
    p.add_argument("--date", help="YYYY-MM-DD (default: today in IST)")
    p.add_argument("--now")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--config", default="trader/config.json")
    p.add_argument("--state", default="trader/state/broker.json")
    p = sub.add_parser("flatten", help="a sell ticket that closes a symbol's position by flat_by")
    p.add_argument("--symbol", required=True)
    p.add_argument("--qty", help="shares you hold, from your broker (default: trader/portfolio.json)")
    p.add_argument("--orders", default="trader/orders.json", help="where the ticket is written")
    p.add_argument("--portfolio", default="trader/portfolio.json")
    p.add_argument("--plan", default="trader/plan.json")
    p.add_argument("--config", default="trader/config.json")
    clock(p)
    p = sub.add_parser("log", help="today's decisions")
    p.add_argument("--state", default="trader/state/broker.json")
    clock(p)
    p.add_argument("--all", action="store_true")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 1
    try:
        return {"show": cmd_show, "approve": cmd_approve, "decline": cmd_decline, "result": cmd_result,
                "fill": cmd_fill, "flatten": cmd_flatten, "log": cmd_log}[a.cmd](a)
    except TicketError as e:
        print("ticket.py: %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
