---
name: trader-news
description: >-
  News Monitor for NSE stocks. Tracks breaking news on every stock you watch
  and flags what moves it: Google News headlines per company, Moneycontrol, Mint
  and Business Standard market feeds filtered to your stocks, and how far each
  stock is from the previous close. Shows only what is new since the last check,
  newest first, in IST, tags each item (results, board meeting, dividend, order
  win, SEBI, RBI, block deal, promoter, analyst, deal, halt, regulatory,
  macro...) and never repeats one. Free, no keys. Reads no exchange filings.
  Use when the user says "any news on RELIANCE", "why is TCS moving", "what's
  the news on my watchlist", "morning briefing", "pre-market news", "monitor
  the news", "did anything break", or after /trader-scanner finds setups.
argument-hint: "[symbols]"
---

# trader-news

A chart can look perfect and still be about to gap on a results date, a
circuit limit or a SEBI order. Before a setup becomes a trade, this skill reads
the public news for every NSE stock on the watchlist and tells you only what is
new. It also prints how far each stock is from the previous close, so a headline
and a move sit side by side.

Tool in this folder (standard library Python, no keys):

```bash
python3 "${CLAUDE_SKILL_DIR}/news.py"                         # watchlist + plan + portfolio
python3 "${CLAUDE_SKILL_DIR}/news.py" --tickers RELIANCE TCS --hours 12
python3 "${CLAUDE_SKILL_DIR}/news.py" --no-feeds --json
```

Symbols are bare NSE symbols (RELIANCE, M&M, BAJAJ-AUTO); a typed `.NS` suffix is
accepted and dropped. Times are IST (UTC+5:30), prices are in rupees.

## What it reads and writes

| file / feed | what |
| --- | --- |
| `--tickers`, else `trader/watchlist.json` | the scanner's hits (trader-scanner) |
| `trader/plan.json` | the trade you are planning (trader-exit) |
| `trader/portfolio.json` | what you hold (trader-broker records fills there) |
| `trader/universe.txt` | `SYMBOL\|Name` lines from trader-scanner: the company name for the news search (without it the symbol itself is searched) |
| Google News RSS | one search per symbol: `"Reliance Industries" OR "RELIANCE"`, English India edition |
| Moneycontrol, Mint, Business Standard RSS | market-wide feeds, one request each per run, kept only where a headline names one of your companies or symbols (`--no-feeds` skips them) |
| Yahoo Finance chart endpoint | the last price against the previous close, from `SYMBOL.NS` (unofficial, can lag) |
| `trader/state/news.json` | what has been shown (kept 30 days) |

Sources checked and rejected: Yahoo Finance's headline RSS and search-news
endpoints return no stories for NSE stocks, and NSE and BSE's announcement APIs
block plain requests, so none of them is used.

## The rules

- **Headlines are leads, not facts.** Before telling the user something
  happened, open the story and read it. Quote the source and link it. Never
  summarise a paywalled story you could not read: say so.
- **No exchange filings are read.** Results, board-meeting outcomes, bulk and
  block deals, shareholding patterns and corporate announcements reach this
  tool only if a news outlet wrote about them. For the filing itself, point the
  user to the company's announcements on nseindia.com or bseindia.com.
- **The feeds lag and miss things.** Google News can be minutes to hours behind
  and is not complete; a quiet feed does not mean nothing happened. Matching is
  by company name and symbol, so a stray story can slip in (a symbol that is
  also an ordinary word) and a story that says only "Reliance Group" is missed.
- **Say what it touches.** Every item lists the symbols it touches and its
  tags: results, board, dividend, order, sebi, rbi, block deal, promoter,
  analyst, deal, capital, legal, people, risk, halt, regulatory, macro. Treat
  results, board, halt, sebi, legal, regulatory and risk first.
- **MOVING** marks a stock 3 % or more from the previous close (`--move-pct`).
  A move with no headline is worth saying out loud too.
- **No predictions.** Report what was published and how far the price is from
  the previous close. Never say what the stock will do next.

## Steps

1. Run the check: `python3 "${CLAUDE_SKILL_DIR}/news.py"`. Exit code 1 means
   nothing new. `--all` shows items already seen, `--json` gives everything
   machine readable, `--dry-run` leaves the state alone.
2. Read the items that matter: anything tagged results, board, halt, sebi, legal,
   regulatory or risk, and every MOVING symbol first.
3. Write a short brief, per symbol: what happened, the source and link, the
   move, and one line on why it matters to the setup (for example "Q2 results
   are tomorrow: an overnight gap can jump past any stop").
4. If a story changes the picture, say so before `/trader-entry` and
   `/trader-exit`: a results date inside the trade, a circuit limit, a fund
   raise. Never act on a headline alone.

During the session: `/loop 15m /trader-news`, or a scheduled task each
morning before the 09:15 open.

## Example

Thursday 2026-10-29, checked at 21:10 IST after the close, for RELIANCE. The
price line reads `RELIANCE ₹1,167.40 +0.81% vs previous close ₹1,158.00 at 15:29
IST`. The Google News search for the last two days leads with "Reliance
Industries to dissolve Hamleys Franchising subsidiary in UK" (16:10 IST) and
"WABAG Secures Mega Order from Reliance Industries for Jamnagar Plants"
(12:45 IST, tagged order); one day earlier "Reliance Industries board to
consider fund-raise on Oct 30" (board, capital) and "Reliance Industries Shares
Fall 2% After Jio Platforms Listing; Q2 Results on October 30" (results). The
brief: "Results and a fund-raise board meeting are due on 30 Oct, so an
overnight gap can jump past any stop; confirm the date on nseindia.com." When
a symbol has no news, say so rather than padding the brief.

## What it never does

- Never invents or embellishes a story, a quote or a number.
- Never trades on news, and never tells the user to buy or sell because of a
  headline.
- Never hammers a server: one request per feed per symbol, a pause between
  calls.

## Safety

Education and tooling, not financial advice. This skill only reads and
reports. Nothing places an order except `/trader-broker`, after
`/trader-risk` passes and the user types an explicit yes for that order.
