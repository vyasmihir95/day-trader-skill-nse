---
name: trader-alerts
description: >-
  Price Alerts for NSE day trades. Rings when a price, a stop or the opening
  bell hits. Turns the trade plan into three alerts in one go (the stop, the
  target and "flat by close" at your flat-by time, 15:15 IST by default), plus
  above / below / stop / target levels in rupees, percent moves from the
  previous close, any time of day, and the NSE opening bell (09:15 IST), each
  ringing once so you are not spammed. Knows NSE hours (09:15 to 15:30 IST),
  works out India time itself (no daylight saving), knows the 2026 NSE trading
  holidays (typed from NSE's list, not verified; you can add days), fetches
  free DELAYED Yahoo quotes (SYMBOL.NS) one ticker at a time, and writes
  trader/prices.json for sizing and the risk check. Use when the user says
  "alert me when", "tell me if RELIANCE hits", "watch my stop", "watch my
  trade", "remind me to close by 3:15", "is the market open", "when does the
  market open", "get me a price", or right after /trader-exit writes a plan.
argument-hint: "[ticker] [price]"
---

# trader-alerts

Price Alerts watches the numbers so the user does not have to stare at a
chart. An alert is information, never an order: when a stop alert rings,
nothing is sold by this skill. The stop-loss and the target orders the user
placed at the broker do the work (they are separate orders, and the user
watches them); the alerts tell the user what happened and when it is time to
be flat.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/alerts.py" clock                          # NSE open or closed, next open
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --from-plan                # stop, target, flat by close from trader/plan.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --opening-bell
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --stop 1155
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol RELIANCE --target 1176
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --at 15:15 --label "flat by close"
python3 "${CLAUDE_SKILL_DIR}/alerts.py" add --symbol INFY --pct 3
python3 "${CLAUDE_SKILL_DIR}/alerts.py" check                          # exit 0 = something rang
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote RELIANCE --write         # -> trader/prices.json
python3 "${CLAUDE_SKILL_DIR}/alerts.py" quote --holdings --write       # portfolio + plan tickers
python3 "${CLAUDE_SKILL_DIR}/alerts.py" list
python3 "${CLAUDE_SKILL_DIR}/alerts.py" reset reliance-plan-stop
```

Tickers are bare NSE symbols (`RELIANCE`, `M&M`, `BAJAJ-AUTO`); a typed `.NS`
is accepted and stripped. `--now "2026-10-26 15:15"` pins the clock (IST);
`--offline DIR` reads saved `yahoo-<SYMBOL>.json` quote files instead of the
network. Prices are rupees (the "₹" sign); brokerage, STT, GST and stamp duty
are not modelled.

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the trade from trader-exit, for `add --from-plan` |
| `trader/alerts.json` | the rules (`alerts.example.json` in this folder) |
| `trader/state/alerts.json` | which rules rang and when, so each rings once |
| `trader/prices.json` | the latest quotes, merged in, for trader-budget and trader-risk |
| `trader/portfolio.json` | the tickers held, for `quote --holdings` |
| `trader/config.json` | `flat_by` when the plan has none, and your own `market_holidays` |

## Rule types

| type | rings when | rings again |
| --- | --- | --- |
| `above` / `target` | price at or above `price` | after `reset`, or on its own if `"rearm": true` and the price fell back |
| `below` / `stop` | price at or below `price` | same |
| `pct_move` | moved `pct` percent from the previous close (or `ref`), optional `direction` up or down | once per day at most |
| `time` | the IST clock passed `at` ("15:15") on a trading day | once per trading day |
| `opening_bell` | the NSE is open (09:15 IST) | once per trading day |

A rule with `"date"` is watched on that IST date only; `--from-plan` rules
carry the plan's date, so yesterday's stop never rings today. NSE has no early
closes in this calendar, so a time rule rings at its own time every trading day.

## Quotes

- Yahoo only: `query2.finance.yahoo.com/v8/finance/chart/RELIANCE.NS`, an
  unofficial free JSON endpoint, one request per ticker, half a second apart
  (`M&M` is sent as `M%26M.NS`). On a 429 it waits and tries once more; on a
  429 or a 5xx after that it tries `query1`. It identifies itself as
  `day-trader-skill/2.0`. It was checked against live NSE tickers (RELIANCE,
  TCS, INFY, HDFCBANK, M&M) on 2026-10-29 and returned INR prices with a quote
  time. Stooq is gone: it has no NSE data.
- **The quotes are delayed.** Yahoo labels its NSE data delayed (commonly by
  around 15 minutes; that was not measured here, since the market was closed
  when it was checked). Each quote carries the time the price was struck:
  read it. They are fine for alerts and for checking a size, not for timing an
  order, and a price can pass a level long before the alert sees it. The
  broker's price is the one that fills. Each price in `trader/prices.json`
  keeps its quote time, and trader-risk only uses one under 15 minutes old.
- If Yahoo is down, read the price from the broker's app or terminal and write
  it into `trader/prices.json` as `{"as_of": "...Z", "prices": {"RELIANCE": 1167.4}}`.

## The NSE clock

Regular session 09:15 to 15:30 IST (UTC+5:30, no daylight saving, so no time
zone files are needed and the computer's own zone is ignored). Closed on
weekends and on these 2026 trading holidays, **typed from NSE's published list
and not verified: check nseindia.com** before relying on them:

Jan 26 (Republic Day), Mar 3 (Holi), Mar 26 (Shri Ram Navami), Mar 31 (Shri
Mahavir Jayanti), Apr 3 (Good Friday), Apr 14 (Dr. Baba Saheb Ambedkar
Jayanti), May 1 (Maharashtra Day), May 28 (Bakri Id), Jun 26 (Muharram), Sep 14
(Ganesh Chaturthi), Oct 2 (Mahatma Gandhi Jayanti), Oct 20 (Dussehra), Nov 10
(Diwali-Balipratipada), Nov 24 (Prakash Gurpurb Sri Guru Nanak Dev), Dec 25
(Christmas).

The list covers **2026 only**; for any other year the clock says so and points
to nseindia.com. NSE also holds special sessions (such as the Diwali muhurat
hour) that this calendar does not model. Add or fix days in `trader/config.json`:

```json
{"market_holidays": {"2026-11-08": "Diwali muhurat"}}
```

## Steps

1. Right after trader-exit writes the plan, run `add --from-plan`. Confirm
   the three alerts back to the user: the stop, the target and the flat-by time.
2. Run `check` while the trade is open: every few minutes from a Claude
   scheduled task or `/loop`. Each run fetches only the tickers in today's
   rules and rings each alert once. A check reads the latest delayed quote;
   it is not a live feed, and a price can pass a level between two checks.
3. Tell the user what rang in one line each. A stop or target alert means the
   stop-loss or target order may have filled: check the broker's positions and
   record the fill with trader-broker, and cancel the other leg. "Flat by
   close" means close anything still open: trader-broker `flatten`, then
   trader-risk, then the user's yes. Never act for them.

## Example: the reel

RELIANCE on Monday 2026-10-26: entry ₹1,162.00 at 10:00, stop ₹1,155.00,
target ₹1,176.00, flat by 15:15. `add --from-plan` writes
`reliance-plan-stop`, `reliance-plan-target` and `reliance-flat-by` for that
day. A `check` at 10:53 reads a real quote (₹1,167.40 from Yahoo's
RELIANCE.NS, previous close ₹1,158.00), between the stop and the target:

```
NSE OPEN (2026-10-26 10:53 IST, regular session).
Nothing hit. 4 rule(s) watched today, RELIANCE ₹1,167.40.
```

At 15:15:

```
ALERT Flat by close: it is 15:15 IST (set for 15:15), the market closes at 15:30. If RELIANCE is still
open, close it: trader-broker flatten --symbol RELIANCE builds the sell ticket, then trader-risk and your yes.
```

It rings once. The next morning the plan's three alerts are no longer
watched; with `add --opening-bell`, the bell rings at 09:15.

## Rules

- An alert is a fact about a price or the clock. Say it as a fact.
- Never present a quote as live. Yahoo's NSE quotes are delayed and unofficial.
- Confirm every new rule back to the user: ticker, type, level, day.

## What it never does

It never places, cancels or changes an order, never claims a quote is
live, and never predicts where a price is going.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
