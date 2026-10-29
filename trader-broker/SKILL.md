---
name: trader-broker
description: >-
  Order Executor for NSE day trades. Takes approved orders to your broker,
  paper trading by default, never without your yes. Shows each order as a
  plain ticket (buy N RELIANCE at a limit of Rs x on NSE, intraday MIS, with
  the stop-loss, the target and the max loss at the stop) plus the exact
  fields of each leg: the entry, and the stop-loss and target as separate
  protective orders, because many Indian brokers have no bracket orders.
  Records your yes word for word, then Claude's own Allow once / Deny card
  asks again if a connector is used. Builds the sell ticket that closes a day
  trade by your flat-by time (15:15 IST), records fills, and logs each
  trade's max loss so trader-risk can hold the daily limit. Broker-neutral:
  paper needs no connector, and for live use you point whichever broker
  connector you have set up (for example a Zerodha Kite or Upstox one) at the
  ticket and verify its tool names yourself; order tools stay on "Needs
  approval". Use when the user says "place the order", "buy it", "execute",
  "send it to my broker", "close my position", "flatten", "get me out",
  "set up paper trading", or after /trader-risk passes.
argument-hint: "[order number]"
---

# trader-broker

The Order Executor is the only skill in this pack that can lead to a real
order, so it is the strictest. An order reaches the market only after three
separate gates, every time, for every order:

1. **trader-risk passed this exact order today** (matched by fingerprint).
2. **The user typed an explicit yes for this one order** in the chat.
3. **The user clicked Allow once** on Claude's own permission card, which
   appears because the order tool is set to "Needs approval".

Two yeses from the user, one PASS from the rules. No yes, no order. In paper
mode (the default) nothing is sent anywhere, so gate 3 does not arise: the
user's yes is recorded and they record the simulated fill by hand. There is no
paper-trading API assumed.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/ticket.py" show                     # every ticket, READY or not
python3 "${CLAUDE_SKILL_DIR}/ticket.py" approve --index 1 --reply "yes"
python3 "${CLAUDE_SKILL_DIR}/ticket.py" result --index 1 --status placed --broker-id ID --filled-qty 62 --filled-price 1174.30
python3 "${CLAUDE_SKILL_DIR}/ticket.py" fill --symbol RELIANCE --side sell --qty 62 --price 1190.25
python3 "${CLAUDE_SKILL_DIR}/ticket.py" flatten --symbol RELIANCE --qty 62
python3 "${CLAUDE_SKILL_DIR}/ticket.py" log
```

`ticket.py` never talks to a broker. It renders tickets, checks that a reply
is a real yes for that order, builds the flatten ticket, and keeps the log in
`trader/state/broker.json`. `--now "2026-10-26 10:15"` pins the clock (IST). Whole shares only; brokerage,
STT, GST and stamp duty are not modelled.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/orders.json` | the proposed NSE orders (trader-budget writes it; `flatten` replaces it) |
| `trader/state/risk.json` | trader-risk's verdicts; only a PASS from today is READY |
| `trader/state/broker.json` | every yes, no, placed, failed and filled, with each entry's max loss |
| `trader/portfolio.json` | cash and shares, updated by `fill` and by `result --filled-qty` |
| `trader/plan.json` | the flat-by time for `flatten` |

## Connecting a broker (optional: paper needs none)

This pack does not ship a broker integration, and nothing here has been
checked against any Indian broker's API. Paper trading needs no connector:
after the user's yes, they record the simulated fill by hand (`result
--filled-qty --filled-price`, or `fill`).

For live trading, only if the user wants it: if they have set up a broker
connector in Claude (for example a Zerodha Kite or an Upstox one), point its
order tool at the ticket's legs. Claude must **verify that connector's tool
names and field names itself** (read its tool list) and map the ticket's
fields to them: the JSON on the ticket is broker-neutral (`symbol`,
`exchange`, `side`, `quantity`, `product`, `order_type`, `limit_price`,
`trigger_price`, `validity`), not any broker's own. Do not guess a tool name.

1. The user sets up the connector or server their broker documents, signs in
   to the broker there, and types any API key into that tool's own config
   (`claude_desktop_config.example.json` here is an empty placeholder for that
   file's shape) or an environment variable. **Keys never go in the chat.**
   Never ask for them, never read the config back into the conversation, and
   if the user pastes a key anyway, tell them to delete that message and
   regenerate the key at the broker.
2. **Set the tool permissions.** Customize > Connectors (Settings >
   Connectors in older versions), open the connector, and for each tool pick
   Always allow, Needs approval or Blocked:
   - every tool that places an order: **Needs approval**. Never Always allow.
   - every tool that cancels, modifies or closes anything: Needs approval.
   - anything for options, futures, margin or "square off everything" or
     "cancel everything": **Blocked** unless the user has a reason and says so.
   - read-only tools (positions, orders, quotes, funds): Always allow is fine.
3. Check it with a read-only call (the funds or profile tool) and have the
   user confirm in the broker's own app that it is the account they meant.

In Claude Code, keep the order tool on ask in `.claude/settings.json`:
`"permissions": {"ask": ["mcp__<server>__<order tool>"]}`, and never put an
order tool in `allow`.

**The stop-loss and the target are separate orders.** Many Indian brokers do
not offer bracket orders for equity, and nothing here assumes one. The ticket
lists the legs one by one with the exact fields: leg 1 the entry, leg 2 the
stop-loss (a sell, stop-loss market at the trigger; if the broker only offers
stop-loss limit, use a limit a little under the trigger and say so), leg 3
the target (a sell limit). Place legs 2 and 3 once the entry has filled (a sell
before that is a short sale), watch them, and cancel the other when one
fills: nothing links them. A stop is a plan, not a guarantee: in a fast
market it fills worse, or a stop-market can be rejected. Brokers also square
off intraday MIS positions on their own around 15:15 to 15:20 and charge for
it, so be flat by `flat_by` yourself.

## Steps: the entry

1. `ticket.py show`. Only READY tickets go to the user. A ticket that is NOT
   READY or BLOCKED goes back to `/trader-risk`.
2. Show ONE ticket and ask: "Take this order? Reply yes or no." One order
   per question. Never offer "yes to all".
3. Run `ticket.py approve --index N --reply "<the user's reply, word for word>"`.
   If it prints "Not recorded", the reply was not an explicit yes for this
   order (for example "sure", "yes to all", or a yes that names a different
   stock or amount). Ask again. Never turn a reply into a yes yourself.
4. Only after "Recorded your yes":
   - **Paper** (`"paper": true`): nothing is sent. Ask the user for the
     simulated fill price and quantity, then record it with step 5.
   - **Live** (`"paper": false`, the user's own decision): place leg 1, the
     entry, with the broker connector's order tool, using the exact fields
     the ticket printed mapped to that tool's own parameters (verify them).
     Claude's card appears: Allow once or Deny.
5. Record what happened: `ticket.py result --index N --status placed
   --broker-id <id> --filled-qty Q --filled-price P` (the fill, from the
   broker or typed by the user for paper), or `--status denied`, or
   `--status failed` with `--note`. A failed order is not retried without a
   new check and a new yes. The log keeps the entry's max loss for
   trader-risk's daily limit.
6. After the entry has filled, the stop-loss leg and the target leg: the user
   places them (paper: they note them down and watch the price with
   trader-alerts). Live, each is its own order tool call with its own Allow
   once. They are separate orders: when one fills, cancel the other.

## Steps: while it is open, and out by the close

- trader-alerts watches the stop, the target and the flat-by time
  (`alerts.py add --from-plan`). Its alerts use delayed quotes, and nothing
  links the stop and the target orders at the broker: the user watches them.
- When the stop-loss or the target fills, record it:
  `ticket.py fill --symbol RELIANCE --side sell --qty 62 --price 1190.25`.
  Then cancel the leg that did not fill.
- By `flat_by` (15:15 IST), anything still open gets closed:
  1. Read how many shares are held from the broker's positions (or, paper,
     from `trader/portfolio.json`).
  2. `ticket.py flatten --symbol RELIANCE --qty 62` writes a market sell for
     those shares (product MIS, validity DAY) to `trader/orders.json`.
  3. `/trader-risk`. A sell that only closes what the user holds is never
     blocked by the daily limits or the entry window.
  4. `ticket.py show`, then the user's yes for that one order, as above.
  5. Cancel the open stop-loss and target orders at the broker first (some
     brokers reject a sell while the shares are held for them), then place
     the sell, then `result`. Brokers square off intraday positions
     themselves around 15:15 to 15:20 and charge for it: do not rely on it.

## Example: the reel

```
ORDER 1 of 1, PAPER (nothing is sent to your broker; you record the simulated fill by hand), NSE
  BUY 62 RELIANCE on NSE at a limit of ₹1,174.30, intraday MIS, good for today
  stop-loss ₹1,158.35 (a separate sell order, trigger ₹1,158.35)
  target ₹1,190.25 (a separate sell limit order)
  max loss at the stop ₹988.90 if it fills at ₹1,158.35 (it can fill worse in a fast market)
  why: opening-range breakout on RELIANCE: 62 shares, bound by your ₹1,000 max loss per trade (62 x 15.95 = ₹988.90 at the stop)
  READY: trader-risk PASS, waiting for your yes
  Leg 1, entry, the exact fields:
    {"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "quantity": 62, "product": "MIS", "order_type": "LIMIT", "limit_price": 1174.3, "validity": "DAY", "client_tag": "tb-RELIANCE-2026-10-19"}
  Leg 2, stop-loss, the exact fields:
    {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "quantity": 62, "product": "MIS", "order_type": "SL-M", "trigger_price": 1158.35, "validity": "DAY", "client_tag": "tb-RELIANCE-2026-10-19-sl"}
  Leg 3, target, the exact fields:
    {"symbol": "RELIANCE", "exchange": "NSE", "side": "sell", "quantity": 62, "product": "MIS", "order_type": "LIMIT", "limit_price": 1190.25, "validity": "DAY", "client_tag": "tb-RELIANCE-2026-10-19-tp"}
```

The user replies "yes". Claude records it. On paper, the user gives the
simulated fill and Claude records it. Live, Claude places leg 1 with the
connector and the card reads "Claude wants to use <the order tool>" with two
buttons: Allow once, Deny. The `why` and the plan stay in the file; they are
never sent.

## What it never does

- Never places an order without all the gates, and never two orders on one yes.
- Never places orders from a scheduled or background task: nobody is there to say yes.
- Never asks for, reads, stores or repeats API keys or passwords.
- Never switches `"paper"` to live. The user does that themselves.
- Never calls a "cancel all", "square off all", option, future or margin tool on its own.
- Never invents a broker connector's tool names: it reads them from the connector.
- Never promises a fill price: a market order and a stop fill at whatever the market gives.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config. A stop is a plan, not a guarantee.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, futures, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the connector's own config, never in the chat.
