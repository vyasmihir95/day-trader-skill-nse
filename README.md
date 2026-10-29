# The Day Trader skill, for the Indian market

9 Claude skills for day trading the Indian market (NSE): scan thousands of charts, track the news, time every entry and exit, and replay every outcome before you risk a rupee.

Free, MIT, no signup, no API key. The tools are plain Python that read free
public data: Yahoo Finance charts for NSE stocks, NSE's list of listed shares,
and public news feeds.

One scans every NSE share for setups. Another tracks the news on every stock
you watch. One watches a setup bar by bar and tells you the moment to get in,
and another sets your stop and your target before you do. And the craziest one
is the Time Machine: before you risk a rupee, it replays the rest of the session
10,000 times from the stock's own recent bars, plus every past day the same
setup fired, and tells you the honest odds.

And no trade goes through until you say yes. Nothing in this pack can place
an order on its own. Paper trading is the default.

> Adapted for NSE from the US day-trading pack
> [github.com/Jakeschincariol/day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
> (MIT, by Jake Schincariol). The US-only skills (Congress trades, SEC
> earnings) and all US market code were removed.

## Install

Copy the skill folders into Claude Code's skills folder:

```bash
cp -r trader-* ~/.claude/skills/
```

Project-local instead of global: copy the same folders into your repo's
`.claude/skills/`. Restart Claude Code, then run `/trader-scanner` and the rest.
No Claude Code at all? Paste any single `SKILL.md` at the top of a chat and it
runs as a mode. You lose the Python tools, but the method works.

The tools need Python 3.8 or newer. Nothing to pip install.

## The nine

| command | job | what it does |
| --- | --- | --- |
| `/trader-scanner` | Chart Scanner | Scans every NSE share for setups: opening-range breakouts, flags, oversold bounces. |
| `/trader-news` | News Monitor | Tracks the news on every stock you watch and flags what moves it. |
| `/trader-entry` | Entry Timer | Watches a setup bar by bar and tells you the moment to get in. |
| `/trader-exit` | Exit Planner | Sets your stop and your target before you enter, so you know when to get out. |
| `/trader-timemachine` | Time Machine | Replays every possible outcome of a trade before you risk a rupee: 10,000 simulated sessions plus every past time the setup fired. |
| `/trader-risk` | Risk Manager | Checks every trade against your limits and stops anything over them. |
| `/trader-budget` | Budget Manager | Sizes every position to your budget and your max loss per trade. |
| `/trader-broker` | Order Ticket | Turns an approved order into a ticket for your broker, paper trading by default, never without your yes. |
| `/trader-alerts` | Price Alerts | Rings when a price, a stop or the opening bell hits. |

## How to use it

Everything lands in a `trader/` folder in your project, and each skill reads
what the last one wrote:

```
scanner -> news -> entry -> exit -> timemachine -> budget -> risk -> broker   (one trade)
alerts: stop, target, flat by 3:15 PM                                          (while it is open)
news                                                                           (on a schedule)
```

An example, a real replay of Thursday October 29 2026 on NSE data, at 10:15 AM IST:

1. **`/trader-scanner`** scanned six names. Three had closed above their
   15-minute opening range (9:15 to 9:30): HDFCBANK, RELIANCE and ONGC. ONGC's
   range was 223.05 to 224.20, and the 10:00 bar closed at 224.23.
2. **`/trader-news`**: the morning's headlines for the stock, in IST, tagged
   (results, dividend, SEBI, order win...). Read them before you trade.
3. **`/trader-entry ONGC`** said triggered: the first 5-minute close above the
   range, 224.23 (₹0.05 tick: 224.25).
4. **`/trader-exit`** set the plan before the entry: stop at the range's
   midpoint, 223.60, so ₹0.65 a share at risk; target one times that risk,
   224.90; flat by 3:15 PM either way.
5. **`/trader-timemachine`** replayed the rest of the day 10,000 times from
   ONGC's own 5-minute bars over its last 58 sessions (seed 19, so anyone can
   repeat it): target first 38.6 %, stop first 61.3 %. The same rule on those
   58 real sessions fired 24 times: 7 target, 12 stop, 5 neither. Its verdict:
   leans toward the stop. That is the point of looking before you risk a rupee.
6. **`/trader-budget`** with a ₹5,00,000 account, a 25 % position cap and a
   ₹1,000 max loss per trade: the position cap decides, 557 shares (₹1,24,907).
   If the stop fills at 223.60, the loss is ₹362.05.
7. **`/trader-risk`** checks it against your limits: the max loss, the daily
   loss limit, trades per day, reward to risk, the entry window (9:30 to
   2:30 PM), position size and cash. PASS or BLOCK, with the reason. The same
   order at 3:00 PM is blocked.
8. **`/trader-broker`** shows you the ticket in plain words, with three exact
   legs: the entry, the stop-loss and the target. You type yes for that exact
   order. If you have set up a broker connector in Claude, point its order tool
   at the ticket; Claude's own "Allow once / Deny" card asks you one more time.
9. **`/trader-alerts`** rings on the stop, the target and at 3:15 PM to
   flatten whatever is still open.

## The tools

All standard-library Python. The data tools read public endpoints and are
polite about it.

```bash
python3 trader-scanner/scan.py universe                                # ~2,400 NSE shares (series EQ)
python3 trader-scanner/scan.py daily                                   # setups from daily closes, whole universe
python3 trader-scanner/scan.py orb --at 10:15                          # opening-range breakouts so far (IST)
python3 trader-news/news.py --tickers RELIANCE --hours 12              # headlines and today's move
python3 trader-entry/entry.py check RELIANCE --setup orb               # waiting / triggered / expired
python3 trader-entry/entry.py watch RELIANCE --every 30                # bar by bar until it triggers
python3 trader-exit/exit.py plan --stop or-mid --target 1R --write     # -> trader/plan.json
python3 trader-timemachine/timemachine.py run --write                  # 10,000 replays + the history
python3 trader-budget/size.py --write                                  # -> trader/orders.json
python3 trader-risk/check.py                                           # PASS / BLOCK per order
python3 trader-broker/ticket.py show                                   # the tickets, READY or not
python3 trader-alerts/alerts.py add --from-plan                        # stop, target, flat by 3:15 PM
python3 trader-alerts/alerts.py check                                  # rings what hit, once
```

Tickers are plain NSE symbols (`RELIANCE`, `M&M`, `BAJAJ-AUTO`); `.NS` is added
for Yahoo. Times are IST, prices are in rupees and snap to NSE's ₹0.05 tick.
Put your limits in `trader/config.json` (copy `trader-budget/config.example.json`).

```bash
python3 -m unittest discover -s tests -v
```

The tests run offline, on fixtures cut from real NSE data fetched on October 29
2026: 5-minute bars, daily closes, headlines, quotes and NSE's symbol list. The
README example above is a live replay, so only its method, not every number, is
pinned by the tests.

## Fine print

**Not financial advice.** This is software that reads public data and does
arithmetic. It does not know your situation and it cannot see the future.
Most people who day trade lose money. You can lose money. Decide for
yourself, and talk to a SEBI-registered adviser if you need advice.

**No trade goes through until you say yes.** Only `/trader-broker` can lead to
an order, and only after `/trader-risk` passes that exact order and you type
yes for it in the chat. Set any broker tool in Claude to "Needs approval"
(Customize, Connectors, tool permissions), never "Always allow", so Claude's own
"Allow once / Deny" card is a second check. No options, futures, margin or short
selling unless you turn them on.

**Paper first.** `"paper": true` is the default in `config.example.json`. Paper
here means nothing is sent anywhere: you record the simulated fill by hand. Keys
live in environment variables, never in the chat.

**The broker step is generic.** This pack does not know your broker's API. It
writes the order legs as plain fields; check your broker's own tool names and
order types. Many Indian brokers have no bracket order, so the stop-loss and the
target are separate orders that nothing links: place both right after the entry
fills, watch them, and cancel the other when one fills. Brokers also square off
intraday (MIS) positions around 3:15 to 3:20 PM on their own.

**The Time Machine is a simulation, not a prediction.** It is built from the
stock's own last 60 days of 5-minute bars. The future can do things those 60
days never did: results, a halt, a headline. Yahoo has no bar for the last few
minutes of many NSE sessions (3:15 to 3:25 PM); the Time Machine fills those as
unchanged and says how many it filled. Treat its odds as a reality check, never
as a promise.

**A stop is a plan, not a guarantee.** In a fast market or across a gap, a
stop order can fill well past its price. The max loss shown is the loss at
the stop price. Brokerage, STT, GST and stamp duty are not included.

**The data is free and unofficial.** Yahoo Finance's chart endpoint is not an
official API: it can lag, change or rate-limit you, and NSE quotes there are
delayed. The scanner and the entry timer read 1-minute or 5-minute bars, not
every tick. Check anything that matters against your broker before you act on it.

**The news is feeds, not filings.** Headlines come from Google News and the
Moneycontrol, Mint and Business Standard market feeds, filtered to the company.
They can lag and miss things, and some stories are off-topic for short tickers.
No NSE or BSE announcements are read.

**The holiday list needs your check.** The NSE 2026 trading holidays in the code
were typed from NSE's published list and are not verified. Check nseindia.com and
add or fix days under `"market_holidays"` in `trader/config.json`, for example a
Diwali muhurat session. Outside 2026 the tools tell you to check.

**Taxes.** Intraday trading profits and losses are taxed in India under rules
this pack does not model. Nothing here is tax advice.

## Files

```
trader-scanner/     SKILL.md, scan.py
trader-news/        SKILL.md, news.py
trader-entry/       SKILL.md, entry.py
trader-exit/        SKILL.md, exit.py
trader-timemachine/ SKILL.md, timemachine.py
trader-risk/        SKILL.md, check.py
trader-budget/      SKILL.md, size.py, config.example.json
trader-broker/      SKILL.md, ticket.py, claude_desktop_config.example.json
trader-alerts/      SKILL.md, alerts.py, alerts.example.json
tests/              unittest suite + offline fixtures
```

MIT. Original pack by [Jake Schincariol](https://opusjake.ai); NSE adaptation on top.
