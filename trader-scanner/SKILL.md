---
name: trader-scanner
description: >-
  Chart Scanner for NSE stocks. Scans thousands of charts for setups:
  opening-range breakouts, flags, oversold bounces. Builds the list of
  NSE-listed equities (series EQ) from NSE's own EQUITY_L.csv, runs a
  pre-market scan of daily closes (20-day closing-high breakout, bull flag,
  reclaimed the 20-day average, oversold, oversold bounce) and an intraday
  opening-range breakout scan on 5-minute bars from the 9:15 AM IST open,
  then writes trader/watchlist.json for trader-news and trader-entry. Free
  Yahoo data, bar by bar. Use when the user says "scan the market", "find me
  setups", "what is breaking out", "opening range breakouts", "scan for bull
  flags", "oversold stocks", "pre-market scan", "build my watchlist", "what
  should I look at today", or at the start of a trading day before
  /trader-news.
argument-hint: "[universe | daily | orb] [--at HH:MM]"
---

# trader-scanner

The Chart Scanner is the first step of a day trade. It reads thousands of
free charts and lists the ones that match a rule. That is all it does. A scan
is a list of charts that match a rule, not a list of stocks to buy, and a
setup on a chart is not a prediction. Most people who day trade lose money;
the scanner does not change that. It only saves you from flipping through
2,000 charts by hand.

It is built for the National Stock Exchange of India (NSE): cash-market
equities, the 9:15 AM to 3:30 PM IST session, tickers as NSE writes them
(RELIANCE, TCS, M&M, BAJAJ-AUTO). Brokerage, STT, GST and stamp duty are not
modelled.

`${CLAUDE_SKILL_DIR}` below is this skill's folder (where this SKILL.md lives).

```bash
python3 "${CLAUDE_SKILL_DIR}/scan.py" universe                       # NSE's EQUITY_L.csv -> trader/universe.txt
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily                          # before the open -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" daily --symbols RELIANCE,TCS,M&M   # or --watchlist FILE, --limit 200
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb                            # after 9:35 AM IST -> trader/watchlist.json
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --at 10:15                 # cut the clock at 10:15 AM IST
python3 "${CLAUDE_SKILL_DIR}/scan.py" orb --watchlist trader/watchlist.json --json
```

Quote a ticker with an `&` in it in the shell (`'M&M'`). A typed `.NS` suffix
is accepted and dropped: files and output carry the bare symbol, and Yahoo is
asked for `SYMBOL.NS`.

Flags: `--min-price 20` (rupees) and `--min-volume` (daily 500,000 shares, orb
0), `--or-minutes 15`, `--max-confirm 200` (orb stage 2),
`--now "2026-10-29 10:15"` (IST) to pin the clock, `--offline PATH` to read
saved Yahoo files, `--no-write`, `--show N`, `--json`. Exit code 0 when the
scan found something, 1 when it found nothing or could not run (the reason
is one plain line).

## What it reads and writes

| file | what |
| --- | --- |
| `trader/universe.txt` | written by `universe`: one `SYMBOL\|Name` a line, read by `daily` and `orb` |
| `trader/watchlist.json` | written by `daily` and `orb`: `mode`, `as_of`, `universe`, `scanned`, `failed`, `hits[]`. trader-news and trader-entry read `hits[].symbol` (bare symbols) |
| `trader/config.json` | read by `orb`: `no_entries_after` (default 14:30); `"market_holidays"` for extra closed days |

Times are IST (UTC+5:30, no daylight saving). The NSE holiday list in the
script is typed from NSE's published 2026 list and has NOT been verified:
check nseindia.com, and add anything missing (a Diwali muhurat session, a new
holiday) in `trader/config.json`:
`"market_holidays": {"2026-11-08": "Diwali muhurat"}`. A day with no bars is
reported as unusable rather than guessed.

## The universe

`universe` downloads NSE's list of listed equities,
`nsearchives.nseindia.com/content/equities/EQUITY_L.csv` (sent with a plain
`Mozilla/5.0` user agent, one retry if the download stalls), and keeps series
EQ: the normal rolling-settlement shares you can trade intraday. Series BE and
BZ (trade for trade, no intraday) are left out, and so is any symbol that is
not a plain ticker (letters, digits, `&` and `-`).

On Thu Oct 29 2026 the file had 2,620 rows: 2,368 kept (EQ), 219 BE and 33 BZ
dropped. `--offline PATH` takes a folder with `EQUITY_L.csv`, or that file.

## The daily scan (before the open)

Yahoo's spark endpoint returns closes for 20 symbols a request, so 2,368
symbols take 119 requests. On Oct 29 2026 that took 73 seconds with the polite
pause (0.3 s): 2,366 charts came back, 562 passed the liquidity filter, 540
had enough history, 200 matched a setup. Liquidity comes from the spark
meta: last price at least `--min-price` (20 rupees) and last session's volume
at least `--min-volume` (500,000 shares). Run it before the open or after the
close: during the session the volume is the day's so far, and today's daily
bar is left out until 3:30 PM.

The setups are computed from daily closes only (spark has no highs or lows):

| setup | the rule on closes |
| --- | --- |
| `breakout_20d` | the last close is above every close of the 20 sessions before it |
| `bull_flag` | up more than 10 % over the 10 sessions before the last 5, then the last 5 closes inside a 5 % range |
| `reclaim_ma20` | the close before was under its 20-day average, the last close is above it |
| `oversold` | RSI 14 (Wilder) under 30 |
| `oversold_bounce` | RSI 14 was under 30 yesterday and today closed up |

A closing-high breakout fires more often than an intraday-high one. Hits are
listed most traded first (price times volume).

## The opening-range breakout scan (during the session)

The opening range is the first `--or-minutes` (15) of the session: the 9:15,
9:20 and 9:25 five-minute bars. A hit is the first completed 5-minute bar
to CLOSE above the range high, at that bar's close time.

1. Stage 1, every symbol: spark closes of today's 5-minute bars, 20 a
   request. A chart goes on only if a completed bar after the range closed at
   or above the highest close of the range. That is a necessary condition
   (the range high is at least every range close), so no breakout is lost.
2. Stage 2, each candidate: the v8 chart with real highs and lows, one
   request each. Range high = the highest high of the range bars, range low =
   the lowest low. On a strong morning most charts are candidates, so stage 2
   checks at most `--max-confirm` (200) of them, the most traded first (price
   times volume so far), and prints PARTIAL with the count it skipped.
   `--max-confirm 0` checks them all.

For a fast answer, scan the daily watchlist or a list of names you trade
(`--watchlist`, `--symbols`): 50 names are 3 spark requests plus one chart
request per candidate.

A chart is usable only when every 5-minute bar from 9:15 to the cutoff is
there; a missing bar means a halt or a thin stock. Null closes are dropped,
a bar off the 5-minute grid (the live tick, like 10:58:07) is dropped, and a
bar counts only once it is complete (start + 5 minutes). Before 9:35 AM, on
a weekend or an NSE holiday, after the close, or after `no_entries_after` (an
expired setup) it prints one plain line and writes nothing. The NSE has no
early closes.

## Data and limits

- Yahoo's chart and spark endpoints are free, unofficial and can lag. Bars are
  5-minute bars, bar by bar, never tick by tick. query2 first, query1 on a 429
  or 5xx, one retry after a 5-second wait on a 429.
- Partial results are said as partial: "Fetched 2,366 of 2,368 charts (2
  failed or had no data)". Never round a partial scan up to "the whole market".
- Yahoo keeps about 60 days of 5-minute bars, so an old session can only be
  replayed from saved files (`--offline`).
- The holiday list is NSE's 2026 list, typed in and unverified (see above).

## Steps

1. Once a week or so: `scan.py universe`.
2. Before the open: `scan.py daily`. Tell the user the counts (fetched,
   liquid, scanned, hits) and the top few by setup.
3. After 9:35 AM IST (10:15 is the classic): `scan.py orb`. Tell the user
   how many usable charts broke their range and when, and that a later hit is
   closer to the price now.
4. The user picks one chart. Hand it on: `/trader-news TICKER` (what is moving
   it), then `/trader-entry TICKER` (the entry bar by bar). Never pick for them.

## Example: Thursday 29 October 2026

Scan at 10:15 AM IST over a wall of 54 liquid NSE names
(`tests/fixtures/trader-scanner/wall5m-2026-10-29.json.gz`, real Yahoo charts,
one of them a ticker Yahoo does not know):

```
python3 scan.py orb --offline wall5m-2026-10-29.json.gz --now "2026-10-29 10:15"
Opening-range breakout scan, Thu 2026-10-29 10:15 AM IST: 15-minute range (9:15 AM to 9:30 AM), 5-minute bars, Yahoo.
Fetched 53 of 54 charts (1 failed or had no data); 51 usable (2 under the price / volume floor); 36 candidates, 36 confirmed with real highs and lows.
28 of 51 charts closed above their opening-range high by 10:15 AM:
   9:35 AM  ADANIENT    ₹2,505.70  range 2,474.80 to 2,505.20  +0.02% over
  ...
  10:05 AM  ONGC          ₹224.23  range 223.05 to 224.20  +0.01% over
  ...
  10:15 AM  GRASIM      ₹2,901.20  range 2,883.20 to 2,900.00  +0.04% over
```

The two under the floor are IDEA (about Rs 12) and YESBANK (about Rs 15.50).
ONGC's range is the 9:15 to 9:25 bars, high 224.20, low 223.05; the 10:00 bar
closed at 224.23, 0.01 % over the high, at 10:05 AM. The user picks ONGC and
Claude hands it to `/trader-news ONGC`, then `/trader-entry ONGC`.

The pre-market style scan on the daily closes of the same 53 names (through
Oct 29) tags KOTAKBANK, ADANIPORTS, ETERNAL, LT, PNB, DRREDDY, DLF, NHPC and
BANKBARODA a 20-day breakout, INFY, TCS, TECHM, HCLTECH and WIPRO a bull flag,
and SBIN, ITC, HINDUNILVR, TITAN, POWERGRID, GRASIM and BANKBARODA a reclaim
of the 20-day average.

## Rules

- Say what was scanned: how many symbols, how many charts came back, how many
  were usable, and the time of the data.
- Say "matched a rule", never "a buy", "a winner" or "a good trade".
- Never print a P&L, a return or a profit figure.
- A hit is stale once the price has moved on. Re-run before acting on it.

## What it never does

It never places, sizes or checks an order, never says which stock to buy,
never calls data real time, and never promises that a setup works.

## Safety

- Education and tooling, not financial advice. A PASS is a rules check, not a recommendation.
- Paper trading by default (`"paper": true`). Going live is the user's own decision, in their own config.
- Nothing in this pack places an order except trader-broker, and only when all three hold: trader-risk
  passed that exact order today, the user typed an explicit yes for that one order in the chat, and the
  user clicked Allow once on Claude's own card because the order tool is set to "Needs approval"
  (Customize > Connectors > tool permissions). Never set an order tool to "Always allow".
- No options, margin, short selling or leverage unless the config allows it and the user confirms.
- Broker keys live in environment variables or the Claude Desktop config, never in the chat.
