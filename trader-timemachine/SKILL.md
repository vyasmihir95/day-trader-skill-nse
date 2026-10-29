---
name: trader-timemachine
description: >-
  Time Machine for NSE stocks. Replays every possible outcome of a trade
  before you risk a rupee: 10,000 simulated sessions plus every past time the
  setup fired. Takes the plan from trader-exit (entry, stop, target), rebuilds
  the rest of the day 10,000 times from the stock's own 5-minute bars (Yahoo
  SYMBOL.NS, the last 60 days, 75 bars a day from 09:15 to 15:30 IST), and
  re-runs the same rule on every past session. Prints the odds of target
  first, stop first, or neither by the close, where the price sits at the
  close, the average R, the history, and one honest verdict, then hands off
  to /trader-budget. Use when the user says "run the time machine", "replay
  this trade", "what are the odds", "simulate this trade", "monte carlo",
  "backtest this setup", "how often does this hit the target", "is this a coin
  flip", or after /trader-exit writes a plan, before /trader-budget.
argument-hint: "[path to plan.json, or SYMBOL]"
---

# trader-timemachine

The Time Machine sits between the plan and the money. trader-exit has set
the entry, the stop and the target. Before anything is sized or sent, this
skill asks one question: if today plays out like the stock's own last 60
days, how often does this plan hit the target first, the stop first, or
neither? It answers with 10,000 replays and with the real record of the same
rule on every past session. It is a simulation built from the stock's own
recent bars, not a prediction. Day trading loses most people money, and a
good-looking replay does not change that.

It works on NSE equities (RELIANCE, TCS, INFY, HDFCBANK, M&M ...). Tickers
are the plain NSE symbol; Yahoo gets `SYMBOL.NS`. Prices are rupees (₹), times
are IST (UTC+5:30), and the session is 09:15 to 15:30, which is 75 five-minute
bars. Brokerage, STT, GST and stamp duty are not modelled: the replay is about
price moves.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run                    # trader/plan.json, 10,000 replays, seed 19
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --write            # also save trader/timemachine.json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --until 15:15      # end each replay at your flat-by time
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --json
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" run --symbol RELIANCE --date 2026-10-14 \
    --entry 1170.70 --stop 1166.00 --target 1180.10 --entry-time "9:45 AM"
python3 "${CLAUDE_SKILL_DIR}/timemachine.py" chart --from           # ASCII fan chart of the saved run
```

Other flags: `--replays N`, `--seed N` (default 19), `--window N` (default 2
bars), `--offline FILE` (a saved Yahoo chart JSON, `.json` or `.json.gz`),
`--now "2026-10-14 09:45"` (IST) to pin the clock, `--config FILE` (for
`market_holidays`).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/plan.json` | the plan from trader-exit: symbol, date, entry, entry_time / entry_bar, stop, stop_rule, target, target_r, opening range |
| Yahoo chart API | one request: 60 days of 5-minute bars (`SYMBOL.NS`, `range=60d&interval=5m`), free and unofficial, can lag |
| `trader/config.json` | only `market_holidays` (extra closed days) |
| `trader/timemachine.json` | with `--write`: `{plan, mc, history}` plus the verdict. `mc` holds the odds, percentiles, the fan-chart bands, 160 sample paths and a 24-bin histogram of the close |

## How it works

1. **The pool.** It keeps only FULL sessions before the plan's date: all 75
   five-minute bars, 09:15 to 15:30 IST. The bar count comes from the
   session, so there is nothing to hard-code. Short days, gaps and today are
   left out. Every bar becomes four ratios to the bar before it: open, high,
   low and close over the previous close.
   **A known gap in the free data.** Yahoo's NSE 5-minute feed very often has
   no bar for the last minutes of the day (in the RELIANCE data every one of
   the 60 days lacks the 15:20 bar). A session that is complete except for
   bars among its last three slots (15:15 to 15:25) still counts: each missing
   bar is taken as unchanged from the bar before, and the report says how many
   sessions needed that. A gap anywhere else leaves the session out. The last
   few bars of the replay are therefore a little calmer than a real close.
2. **One replay.** Start at the entry price. For each bar left in the day,
   pick a past bar from the same time of day (plus or minus 2 bars) at
   random, and scale it to the current price. Bar by bar, it checks the stop
   first, then the target. A bar that touches both counts as the stop.
3. **10,000 replays** give four odds. Target first. Stop first. Neither,
   which means the trade was still open at `--until` (default 3:30 PM, the
   close; NSE has no early closes, so add `--until 15:15` to stop at the
   usual flat-by time). Green, which means target first or above the entry at
   `--until`. R is +1 at a 1R target, -1 at the stop, and otherwise the
   distance from the entry at `--until` divided by the risk. A gap through
   the stop still counts as -1R here. A real fill can be worse.
4. **The history.** The plan's own rule (the same opening range, stop rule
   and target R) runs again on every one of those past sessions. That is
   what really happened, not a simulation. Stops round down to NSE's 0.05
   tick (away from the entry); entries and targets to the nearest tick.
5. **The verdict** is one line from the numbers. It looks only at the
   replays that hit one side, and asks how often the target came first. A
   bigger target needs to win less often to break even: 1 in (1 + R). That
   is 50 % at 1R, 33 % at 2R, 25 % at 3R. Less than 5 points from break-even:
   "Close to a coin flip" at 1R, "Close to break-even" at any other R. 5 to
   15 points: "Leans toward the target" or "Leans toward the stop". 15 or
   more: "Strongly toward" one of them.
6. **Below the verdict**, when it applies: a line when more than 40 % of the
   replays hit neither (the price at `--until` decides those), and the
   average R when its sign goes against the lean. Those can only point the
   other way because of the replays that hit neither.

**The seed.** The draws come from Python's `random.Random(seed)`, seed 19 by
default. Same plan, same data, same seed: the same numbers, on any machine.
Say the seed with every result so the user can repeat it.

**5-minute bars only.** Yahoo keeps 60 days of 5-min bars but only about 7
days of 1-min bars, too few sessions for a fair pool. It refuses, in one
plain line, when there are fewer than 20 full sessions, when the entry bar is
not in the data (wrong date, not closed yet, a future day), when NSE was
closed on the plan's date (a weekend or a holiday: the 2026 list is typed from
NSE's and not verified, check nseindia.com; add days under `market_holidays`
in `trader/config.json`), or when the plan does not make sense (a stop above a
buy's entry, an entry off the 5-minute grid). A live in-progress bar is never
used.

## Steps

1. Make sure `trader/plan.json` is today's plan from `/trader-exit`. If there
   is none, run that first, or pass the plan as flags.
2. Run `timemachine.py run --write`. Add `--until 15:15` when the plan has the
   default flat-by time, so "neither" means still open at the time you would
   get out.
3. Tell the user, in this order: the four odds, the median and the 5 to 95
   range of the price at the close, the average R, the history line, then the
   verdict line and any line under it exactly as printed. Say the seed.
4. If the verdict is a coin flip or close to break-even, say so plainly. Do
   not dress it up. If the history and the replays disagree, say that too.
5. Say once that this is built from the last 60 days and is not a prediction.
   Results, news and halts can do things those 60 days never did. Check
   `/trader-news` for the stock before going on.
6. If the user still wants the trade, hand off to `/trader-budget` to size it.

## Example: RELIANCE, 14 October 2026

RELIANCE, Wednesday 2026-10-14. The 15-minute opening range is ₹1,162.70 to
₹1,169.30. The 9:40 bar closes above it: entry ₹1,170.70 at 9:45 AM (bar 5),
stop ₹1,166.00 (the range midpoint), target ₹1,180.10 (2R, risk ₹4.70 a
share). The data is RELIANCE's 60 days of 5-minute bars up to 2026-10-29; the
pool is the 49 full sessions before the 14th.

```
10,000 replays of the rest of the session, 9:45 AM to 3:30 PM (69 five-minute bars), seed 19.
  target first   23.0 %
  stop first     65.1 %
  neither        11.9 %   still open at 3:30 PM
  green          31.8 %   target first, or above the entry at 3:30 PM
  Price at 3:30 PM: median ₹1,169.27, 90 % of replays between ₹1,152.82 and ₹1,185.47.
  Average R: -0.131
History: the same rule on each of those 49 real sessions fired 23 times:
6 target, 11 stop, 6 neither. Average R +0.114.
Leans toward the stop: at 2R the target has to come first in 33 % of the replays that hit one side; it did in 26 % (target first 23.0 % vs stop first 65.1 %).
```

At 2R the target has to come first in a third of the replays that hit one
side. It did in 26 %: the replays lean toward the stop. The real history of the
same rule was better than the replays (23 trades, a small sample), so the two
disagree. That is the honest answer for this setup, and it is what the user
should hear before `/trader-budget` sizes the trade. Note that this stock fell
over the 60 days in the pool (from about ₹1,307 on 4 August to about ₹1,167 on
29 October), so its own bars lean down.

## Rules

- Every number comes from the run. Never round a coin flip up into an edge.
- The verdict line and the lines under it are printed by the tool. Repeat
  them, do not rewrite them. Never judge a 2R or 3R plan by target first vs
  stop first alone: the verdict already weighs the target's R.
- Simulated odds are not predictions and not promises. Say it once, plainly.
- Free Yahoo data is unofficial and can lag. Bars are 5-minute bars, checked
  bar by bar, never tick by tick and never real time.
- Always say the seed, so any run can be repeated.
- One plan per run. A changed entry, stop, target or date is a new run.

## What it never does

It never places an order, never sizes a position, never changes the plan,
and never calls a result a sure thing. It never prints a P&L or a profit.
It never uses a bar that has not closed yet, and never mixes today's bars
into the pool.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
