---
name: trader-budget
description: >-
  Budget Manager for NSE day trading, in rupees. Sizes every position to your
  budget and your max loss per trade. Reads the trade plan from trader-exit
  (entry, stop, target), finds the whole shares that fit inside four limits
  (max loss per trade, position cap, max order, cash after your buffer), says
  which limit decided it, and writes ONE bracket order (entry, stop-loss,
  take-profit) to trader/orders.json as a broker-neutral NSE order (symbol,
  exchange NSE, quantity, product MIS, prices on the 0.05 tick). A stop too
  far away for your max loss is a plain refusal, not a smaller order.
  Nothing is placed. Use when the user says "how many shares", "size this
  trade", "how much should I buy", "I have Rs 5 lakh", "I only want to risk
  Rs 1,000 a trade", "set my rules", "keep 10% in cash", or after
  /trader-exit writes a plan.
argument-hint: "[budget in rupees]"
---

# trader-budget

The Budget Manager turns a plan ("buy RELIANCE at 1197.40, stop 1185.00") into
an exact order ("buy 80 shares"), inside rules the user sets once. The stop
decides the size: the further away the stop, the fewer shares. It writes a
proposal only. trader-risk checks it and trader-broker asks the user yes or
no. Day trading loses most people money. Sizing to a max loss keeps each
loss small; it does not make a trade win.

Everything is NSE equities, in rupees (₹), India Standard Time. Whole shares
only (the NSE equity lot is 1 share). Prices move in 0.05 rupee ticks, so the
entry, stop and target are rounded to the nearest 0.05 before sizing.
Brokerage, STT, GST and stamp duty are NOT modelled: the real loss at the stop
is a little bigger than the number shown, so leave room in the max loss.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py"                         # trader/plan.json + trader/config.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --write                 # write trader/orders.json
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit --write         # a limit order at the plan's entry
python3 "${CLAUDE_SKILL_DIR}/size.py" --limit 1196.00 --json  # a limit order at 1196.00
```

`--now "2026-10-27 10:01"` pins the clock (IST). A plan for another day is
refused: make a new one with trader-exit.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/config.json` | the rules (start from `config.example.json` in this folder) |
| `trader/plan.json` | the trade: entry, stop, target, setup, flat-by time (trader-exit writes it) |
| `trader/prices.json` | optional: a fresh price above the plan's entry means a bigger risk (trader-alerts writes it) |
| `trader/portfolio.json` | optional: its cash caps the size (trader-broker keeps it) |
| `trader/orders.json` | the one proposed bracket order it writes with `--write` (shape below) |

## Step 1: set the rules once

If `trader/config.json` does not exist, copy `config.example.json` into
`trader/config.json` and ask the user for each value in plain words:

- **budget**: how much money this is for, in rupees. Only money they can afford to lose.
- **cash_buffer_pct**: how much always stays in cash (default 10).
- **max_loss_per_trade_inr**: the most one trade may lose if the stop fills at the stop (default ₹1,000).
- **max_position_pct**, **max_order_inr**, **min_order_inr**: the biggest position (25% of the
  budget), the biggest order (₹1,50,000, written 150000) and the smallest order (₹500).
- The day limits trader-risk enforces: **max_daily_loss_inr** (3000), **max_trades_per_day** (3),
  **min_reward_risk** (1.0), **require_stop** (true), **no_entries_before** / **no_entries_after**
  ("09:30" / "14:30") and **flat_by** ("15:15", before brokers' own intraday square-off), all IST.
- **blocked**: tickers (bare NSE symbols like RELIANCE or M&M) they never want to trade.
- Keep **paper** true until they have watched it work on a paper account.

## Step 2: size the trade

```bash
python3 "${CLAUDE_SKILL_DIR}/size.py" --write
```

Whole shares only. The size is the smallest of four limits:

| limit | shares |
| --- | --- |
| max loss | `max_loss_per_trade_inr` / risk a share (entry minus stop) |
| position cap | budget x `max_position_pct` / entry |
| max order | `max_order_inr` / entry |
| cash | (budget minus the cash buffer) / entry, never more than the cash in `trader/portfolio.json` |

The entry it sizes at: the limit price with `--limit`. For a market order, the
plan's entry, or the price in `trader/prices.json` if it is fresh (under 15
minutes old) and higher. A price that ran up since the plan means a bigger
loss at the same stop, so fewer shares. A lower price never shrinks the risk.

It refuses, and writes no order, when: the size is zero shares, the order is
under the minimum, reward:risk is under `min_reward_risk`, the stop is not
below the entry, the target is not above it (the move already happened), the
plan is for another day, the ticker is blocked, or the plan is a short. Say
the reason to the user in one line.

## Step 3: show the user

Show the four limits, which one decided the size, the shares, the rupees
and the max loss at the stop. Say plainly that a stop is an order, not a
promise: in a fast market it can fill below the stop. Then hand over to
`/trader-risk`.

## Order shape

One bracket order in a broker-neutral NSE shape. Numbers are JSON numbers, prices are
multiples of 0.05, `quantity` is a whole number. `product` is `MIS` (intraday). A
limit order (`--limit`) has `"order_type": "LIMIT"` and a `limit_price`; a market
order has `"order_type": "MARKET"` and no `limit_price`. `stop_loss` and
`take_profit` are the two legs of the bracket. `client_tag` is
`tb-<SYMBOL>-<date>`, which trader-risk and trader-broker use to spot duplicates.

## Example: RELIANCE

RELIANCE on Tuesday 2026-10-27 (5-minute bars from Yahoo). Opening range
(09:15 to 09:30) 1189.70 to 1197.00. Breakout entry 1197.40 at 10:00 AM, stop
1185.00 (a price the user chose), target 1222.20 (2R). Risk 12.40 a share. The
example config: ₹500,000 budget, 10% cash buffer, ₹1,000 max loss.

| limit | the math | shares |
| --- | --- | --- |
| max loss | ₹1,000 / 12.40 | **80 (decides it)** |
| position cap | 25% of ₹500,000 = ₹125,000 / 1197.40 | 104 |
| max order | ₹150,000 / 1197.40 | 125 |
| cash | ₹500,000 minus ₹50,000 = ₹450,000 / 1197.40 | 375 |

80 shares = ₹95,792. Max loss at the stop: 80 x 12.40 = ₹992. It writes:

```json
{"symbol": "RELIANCE", "exchange": "NSE", "side": "buy", "quantity": 80, "product": "MIS",
 "order_type": "MARKET", "validity": "DAY", "stop_loss": 1185.0, "take_profit": 1222.2,
 "client_tag": "tb-RELIANCE-2026-10-27",
 "why": "opening-range breakout on RELIANCE: 80 shares, bound by your ₹1,000 max loss per trade (80 x 12.40 = ₹992 at the stop)",
 "plan": {"entry": 1197.4, "stop": 1185.0, "target": 1222.2, "risk_per_share": 12.4, "max_loss": 992.0,
          "setup": "orb", "entry_time": "10:00 AM", "flat_by": "15:15"}}
```

`why` and `plan` are notes for trader-risk and trader-broker. They are never
sent to the broker. With the stop at 150.00 instead, one share would risk
1047.40, more than the ₹1,000 max loss: no order. If the price has run up to
1210.00 since the plan, the same stop risks 25.00 a share, so the size falls to
40 shares, and reward:risk falls to 0.49: refused, because the move it was
waiting for has mostly happened.

## Rules

- Proposals only. This skill never calls a broker tool.
- Never size money the user has not said is for this. Ask for the budget and the max loss.
- Every number comes from the config, the plan or the prices. Nothing is guessed.
- No projections: never say what a trade will make. A target is a plan, not a forecast.
- Free Yahoo prices (RELIANCE.NS style) are unofficial and can lag. The broker's price is the one that fills.

## What it never does

It never places, approves or schedules an order, never raises a limit on its
own, never shrinks a stop to fit more shares, and never sizes options,
margin or short positions.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
