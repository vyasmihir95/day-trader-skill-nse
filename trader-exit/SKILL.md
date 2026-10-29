---
name: trader-exit
description: >-
  Exit Planner for NSE stocks. Sets your stop and your target before you
  enter, so you know when to get out. Reads the entry from trader-entry (or a
  price you give), puts the stop at the opening-range midpoint by default (or
  the range low, your price, a percent, or a multiple of the ATR), rounds it
  down to NSE's 0.05 tick, and sets the target at 2R (or 1R, or your price).
  Refuses a stop at or above the entry, a target at or below it, or a stop
  too tight to survive the spread, and says why. Writes trader/plan.json for
  the Time Machine, the Budget Manager and Price Alerts. Prices in rupees,
  times in IST. Long side only. Use when the user says "where's my stop",
  "set my stop and target", "plan the exit", "when do I get out", "stop loss",
  "what's my R", "risk reward", or right after /trader-entry triggers, before
  /trader-timemachine.
argument-hint: "[--stop or-mid|or-low|price:X|pct:X|atr:K] [--target 2R|1R|price:X]"
---

# trader-exit

The Exit Planner decides how a trade ends before it starts. The stop is where
the setup has failed. The target is where you take what you came for. Both
are set BEFORE the entry, written down and handed on, so nobody has to decide
anything in the middle of a fast move. A stop is a plan, not a guarantee: a
gap or a fast market can fill past it. Day trading loses most people money,
and a neat plan does not change that.

It works on NSE equities (RELIANCE, TCS, INFY, HDFCBANK, M&M ...). Tickers are
the plain NSE symbol; Yahoo gets `SYMBOL.NS` for you. Prices are rupees (₹) and
every time is IST (UTC+5:30). Brokerage, STT, GST and stamp duty are not
modelled: the numbers here are price moves, not what lands in your account.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan                          # trader/entry.json: midpoint stop, 2R target
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop or-mid --target 1R --write
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --stop atr:1.5           # needs the session's bars (Yahoo or --offline)
python3 "${CLAUDE_SKILL_DIR}/exit.py" plan --symbol RELIANCE --entry 1170.70 --or-high 1169.30 --or-low 1162.70
python3 "${CLAUDE_SKILL_DIR}/exit.py" show                          # the current trader/plan.json
```

Other flags: `--flat-by 15:15`, `--json`, `--now "2026-10-14 09:45"` (IST),
`--entry PRICE` with a waiting entry.json to plan ahead, `--setup level`,
`--offline FILE` (a saved Yahoo chart JSON for an ATR stop).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/entry.json` | the trigger from trader-entry: symbol, entry price, time, bar, opening range |
| `trader/config.json` | `flat_by` (15:15), `min_reward_risk` (1.0: a note here, trader-risk enforces it) and `market_holidays` (extra closed days) |
| Yahoo chart API | only for an `atr:K` stop: the session's bars up to the entry bar, free and unofficial |
| `trader/plan.json` | with `--write`: entry, stop and its rule, target and its R, risk a share, reward:risk, flat by. trader-timemachine, trader-budget and trader-alerts read it |

## Stops and targets

| stop | where it goes |
| --- | --- |
| `or-mid` | the opening-range midpoint (the default for an opening-range breakout) |
| `or-low` | the opening-range low: wider, so fewer shares for the same max loss |
| `price:X` | your price |
| `pct:X` | X percent under the entry |
| `atr:K` | K times the 14-bar ATR under the entry, on the session's bars up to the entry bar. With fewer than 14 bars by then it averages all of them and says so |

A level setup has no opening range to lean on, so it needs a stop you name.

- **The tick.** NSE equities trade in steps of ₹0.05. The stop rounds DOWN to
  that 0.05 tick, away from the entry. The target and the entry round half up
  to the tick. So every price in the plan is one the exchange can fill at.
  Risk = entry minus stop. Target = entry + R x risk. `2R` by default; `1R`,
  `1.5R` or `price:X` also work.
- **Refused, with the reason.** A stop at or above the entry. A target at or
  below it. A risk under 0.05% of the price: too tight to survive the
  spread, normal wiggle would stop you out. An entry.json that has not
  triggered (unless you give `--entry`). A short: plans are longs only.
- **Flat by.** 15:15 IST by default (or `flat_by` in the config). Out by then,
  win or lose, no overnight hold. Brokers square off intraday (MIS)
  positions themselves around 15:15 to 15:20, so the plan is flat before they
  do it for you. NSE has no early closes. An entry at or after the flat-by
  time is refused; a flat-by at or after the 15:30 close is refused.
- **Holidays.** The 2026 NSE holiday list in the script was typed from NSE's
  published list and is NOT verified: check nseindia.com. A plan for a closed
  day carries a note. Add days in `trader/config.json` under
  `"market_holidays": {"2026-11-09": "extra closure"}`. Outside 2026 it says
  to check nseindia.com.

## Steps

1. Run it right after `/trader-entry` triggers. Before that, `--entry PRICE`
   plans ahead at the level you expect.
2. Use the default stop for an opening-range breakout unless the user names
   another. A level setup needs one: ask.
3. Run `exit.py plan --write`. Read back the entry, the stop and its rule,
   the target and its rule, the risk a share, reward:risk and flat by.
4. If it refuses, say the reason and the fix (a wider stop, a farther
   target). Never loosen anything on your own.
5. Say once that the stop is a plan, not a guarantee.
6. Hand off to `/trader-timemachine` to replay the plan, then `/trader-budget`
   to size it to the max loss per trade. Offer `/trader-alerts` for the stop,
   the target and the flat-by time. Claude can keep `entry.py watch` running
   in the background or on a `/loop` until a setup triggers, then come here.

## Example: RELIANCE, 14 October 2026

RELIANCE, Wednesday 2026-10-14. The 15-minute opening range (9:15 to 9:30) is
₹1,162.70 to ₹1,169.30. The 9:40 bar closes at ₹1,170.70, the first close over
the range high: the entry is ₹1,170.70 at 9:45 AM (bar 5). `exit.py plan
--write` with the defaults:

```
Exit plan: RELIANCE long, Wed 2026-10-14, opening-range breakout on 5-minute bars
  Entry         ₹1,170.70 at 9:45 AM (bar 5)
  Stop          ₹1,166.00  opening-range midpoint: (₹1,169.30 + ₹1,162.70) / 2 = ₹1,166.00
  Target        ₹1,180.10  2R (two times the risk)
  Risk a share  ₹4.70 (0.40% of the entry)
  Reward:risk   2.0
  Flat by       15:15 IST, win or lose: no overnight hold
```

With `--target 1R` the target is ₹1,175.40 (1170.70 + 4.70). A midpoint that
falls between ticks rounds down: a range of 1162.70 to 1169.35 has the
midpoint 1166.025, so the stop is ₹1,166.00. trader-budget then sizes it: a
₹1,000 max loss per trade allows floor(1000 / 4.70) = 212 shares by the loss
limit alone, before its other limits.

## Rules

- The stop and the target are set before the entry, never after.
- Never move a stop further from the entry once the trade is on.
- Plain numbers only: entry, stop, target, risk a share, reward:risk. It
  never prints a P&L or a return.
- Nothing here promises winning trades or money. A 1R target is not a
  prediction that the price gets there.
- Long side only. Plans are longs only.

## What it never does

It never places an order, never sizes a position, never widens a stop to
make a plan pass, and never calls a stop a guarantee.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
