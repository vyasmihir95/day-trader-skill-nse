#!/usr/bin/env python3
"""News monitor for trader-news (NSE). Standard library only.

Watches headlines for the NSE stocks you watch or hold and prints only what
is new since the last check, newest first, with how far each stock has moved
today and a flag on anything that touches your symbols.

    python3 news.py                                  # watchlist + plan + portfolio symbols
    python3 news.py --tickers RELIANCE TCS INFY
    python3 news.py --tickers RELIANCE --all --hours 72   # everything in the last 3 days, seen or not
    python3 news.py --no-feeds                       # skip the market-wide feeds
    python3 news.py --no-moves                       # skip the price line per symbol

Your symbols (bare NSE symbols: RELIANCE, M&M, BAJAJ-AUTO), when --tickers is
not given: every hit in trader/watchlist.json (trader-scanner), the symbol in
trader/plan.json (trader-exit) and the positions in trader/portfolio.json.
A typed .NS suffix is accepted and dropped. Company names for the news query
come from trader/universe.txt (SYMBOL|Name lines, written by trader-scanner);
without one the symbol itself is searched.

Sources (checked from India, no key, no login):
  - Google News RSS, one request per symbol, searching the company name or the symbol:
    https://news.google.com/rss/search?q="<Company>" OR "<SYMBOL>" when:48h&hl=en-IN&gl=IN&ceid=IN:en
  - Market-wide RSS feeds from Moneycontrol, Mint and Business Standard, one
    request each per run, kept only where a headline names one of your
    companies or symbols.
  - The move: Yahoo Finance's chart endpoint (unofficial, can lag), the last
    price against the previous close:
    https://query2.finance.yahoo.com/v8/finance/chart/<SYMBOL>.NS?range=1d&interval=5m
  Yahoo's headline RSS and search-news endpoints return no NSE stories, so they
  are not used. Nothing from NSE or BSE is read: their announcement APIs block
  plain requests, so exchange filings (results, board meetings, shareholding)
  are NOT covered here. News feeds lag and miss things.

Times print in IST (UTC+5:30, no daylight saving). State: trader/state/news.json
remembers what you have seen (30 days), so an item shows once. Offline:
--offline DIR reads google-<SYMBOL>.xml, chart-<SYMBOL>.json and
feed-moneycontrol.xml / feed-mint.xml / feed-business-standard.xml from DIR.
The tests use it.

Headlines are leads, not facts: open the story before acting, and treat
nothing here as advice.
"""

import argparse
import datetime as _dt
import email.utils
import gzip
import hashlib
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

GOOGLE = "https://news.google.com/rss/search?q=%s&hl=en-IN&gl=IN&ceid=IN:en"
CHART = "https://%s.finance.yahoo.com/v8/finance/chart/%s?range=1d&interval=5m"
FEEDS = [  # market-wide RSS: (file name offline, label, url)
    ("feed-moneycontrol.xml", "Moneycontrol", "https://www.moneycontrol.com/rss/business.xml"),
    ("feed-mint.xml", "Mint", "https://www.livemint.com/rss/markets"),
    ("feed-business-standard.xml", "Business Std", "https://www.business-standard.com/rss/markets-106.rss"),
]
BROWSER_UA = "Mozilla/5.0"
KEEP_DAYS = 30
PER_SYMBOL = 25   # newest stories kept per symbol from its Google News search
MOVE_PCT = 3.0    # a stock this far from the previous close is flagged as moving
IST = _dt.timedelta(hours=5, minutes=30)  # fixed: India has no daylight saving

KEYWORDS = [
    ("results", r"\bresults?\b|\bearnings\b|\bQ[1-4]\s?FY\d+|\bnet profit\b|\bnet loss\b|\bPAT\b|\bEBITDA\b|\bguidance\b"),
    ("board", r"\bboard meeting\b|\bboard (?:meets|approves|clears|to consider)\b"),
    ("dividend", r"\bdividend\b|\bbonus (?:issue|shares)\b|\brecord date\b|\bex-date\b|\bstock split\b"),
    ("order", r"\border (?:win|book|worth|inflow)\b|\b(?:bags?|bagged|wins?|won|secures?|secured)\b.{0,25}\b(?:orders?|contracts?)\b|\bletter of (?:intent|award)\b|\bLoI\b|\bLoA\b"),
    ("sebi", r"\bSEBI\b"),
    ("rbi", r"\bRBI\b|\bReserve Bank\b|\brepo rate\b|\bmonetary policy\b"),
    ("block deal", r"\bblock deals?\b|\bbulk deals?\b"),
    ("promoter", r"\bpromoters?\b|\bpledg(?:e|ed|es)\b|\binsider\b|\bstake (?:sale|buy|hike|cut)\b|\bsells? stake\b|\bbuys? stake\b"),
    ("analyst", r"\bupgrade[sd]?\b|\bdowngrade[sd]?\b|target price|\btarget\b.{0,20}\bRs\b|\bbuy rating\b|\bsell rating\b|\binitiates coverage\b|\bbrokerage\b|\boverweight\b|\bunderweight\b|\boutperform\b|\bunderperform\b"),
    ("deal", r"\bacqui(?:re|res|red|sition)\b|\bmerger\b|\bdemerger\b|\bamalgamation\b|\bbuyout\b|\btakeover\b|\bjoint venture\b|\bJV\b|\bdeal\b"),
    ("capital", r"\bbuyback\b|\bQIP\b|\brights issue\b|\bpreferential (?:issue|allotment)\b|\bIPO\b|\bOFS\b|\boffer for sale\b|\bfund[- ]?rais|\braises? Rs\b"),
    ("legal", r"\blawsuit\b|\bsued\b|\bprobe\b|\binvestigation\b|\bcourt\b|\bNCLT\b|\bNCLAT\b|\bED raid|\bfraud\b|\bpenalty\b|\bshow[- ]cause\b"),
    ("people", r"\bCEO\b|\bCFO\b|\bMD\b|\bchairman\b|\bresign|\bsteps down\b|\bappoint"),
    ("risk", r"\brecall\b|\bbankrupt|\binsolvency\b|\bdefault\b|\bdelist|\bhack|\bbreach\b|\boutage\b"),
    ("halt", r"\bhalt(?:s|ed)?\b|\btrading (?:pause|suspen)|\bupper circuit\b|\blower circuit\b|\bcircuit\b|\bprice band\b|\bF&O ban\b|\bASM\b|\bGSM\b"),
    ("regulatory", r"\bUSFDA\b|\bFDA\b|\bapproval\b|\bapproves\b|\bCCI\b|\bDGCA\b|\bTRAI\b|\bIRDAI\b|\btax notice\b|\bGST\b"),
    ("macro", r"\bSensex\b|\bNifty\b|\bFed\b|\bFOMC\b|\bCPI\b|\binflation\b|\bGDP\b|\bcrude\b|\btariffs?\b|\bFII\b|\bFPI\b|\bGIFT Nifty\b"),
]


class NewsError(Exception):
    pass


# ---------------------------------------------------------------- time

def to_local(utc):
    """Aware or naive UTC -> naive IST wall-clock time (a constant +5:30)."""
    if utc.tzinfo is not None:
        utc = utc.astimezone(_dt.timezone.utc)
    return utc.replace(tzinfo=None) + IST


def local_to_utc(local):
    """Naive IST wall-clock time -> aware UTC."""
    return (local - IST).replace(tzinfo=_dt.timezone.utc)


def ts_to_local(ts):
    return to_local(_dt.datetime.fromtimestamp(ts, _dt.timezone.utc))


# ---------------------------------------------------------------- inputs

def load_json(path):
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def bare(sym):
    """The symbol as the rest of the pack carries it: RELIANCE, not RELIANCE.NS."""
    sym = sym.strip().upper()
    return sym[:-3] if sym.endswith(".NS") else sym


def yahoo_symbol(sym):
    """RELIANCE -> RELIANCE.NS, URL-quoted (M&M -> M%26M.NS)."""
    return urllib.parse.quote(bare(sym) + ".NS", safe=".-")


def load_universe(path="trader/universe.txt"):
    """{SYMBOL: Name} from the scanner's SYMBOL|Name lines; {} when there is no file."""
    out = {}
    if path and os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                sym, sep, name = line.strip().partition("|")
                if sep and sym.strip():
                    out[bare(sym)] = name.strip()
    return out


def watch_from_files(portfolio="trader/portfolio.json", watchlist="trader/watchlist.json",
                     plan="trader/plan.json", universe="trader/universe.txt"):
    """{symbol: name or None}: the scanner's hits, the planned trade and your holdings."""
    out = {}
    wl = load_json(watchlist) or {}
    for h in wl.get("hits", []):
        sym = h.get("symbol") or h.get("ticker")
        if sym:
            out[bare(sym)] = h.get("name")
    pl = load_json(plan) or {}
    if pl.get("symbol"):
        out.setdefault(bare(pl["symbol"]), pl.get("name"))
    pf = load_json(portfolio) or {}
    for p in pf.get("positions", []):
        sym = p.get("ticker") or p.get("symbol")
        if sym:
            out[bare(sym)] = p.get("name") or out.get(bare(sym))
    names = load_universe(universe)
    return {s: (n or names.get(s)) for s, n in out.items()}


# ---------------------------------------------------------------- fetching

class Fetcher(object):
    def __init__(self, offline=None, pause=0.15):
        self.offline = offline
        self.pause = pause
        self._last = 0.0

    def get(self, url, name):
        if self.offline:
            path = os.path.join(self.offline, name)
            if not os.path.isfile(path):
                raise NewsError("offline: no file %s" % path)
            with open(path, "rb") as fh:
                return fh.read()
        gap = time.time() - self._last
        if gap < self.pause:
            time.sleep(self.pause - gap)
        self._last = time.time()
        req = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA, "Accept-Encoding": "gzip"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as exc:
            raise NewsError("HTTP %s for %s" % (exc.code, url))
        except urllib.error.URLError as exc:
            raise NewsError("could not reach %s: %s" % (url, exc.reason))


def short_name(name):
    """'Reliance Industries Limited' -> 'Reliance Industries' (for the search and for matching)."""
    name = re.sub(r"\([^)]*\)", " ", name or "")
    name = re.sub(r"[\s,]+(?:limited|ltd\.?)\s*$", "", name.strip(), flags=re.I)
    return " ".join(name.split())


def google_url(sym, name, hours=48):
    """The news search for one symbol: the quoted company name or the symbol, newest window only."""
    short = short_name(name)
    q = '"%s" OR "%s"' % (short, bare(sym)) if len(short) >= 3 else '"%s" NSE' % bare(sym)
    hours = max(1, int(math.ceil(hours)))
    q += " when:%dh" % hours if hours <= 72 else " when:%dd" % int(math.ceil(hours / 24.0))
    return GOOGLE % urllib.parse.quote_plus(q)


# ---------------------------------------------------------------- parsing

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _text(el, name):
    for c in el.iter():
        if _local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def _plain(markup):
    """Feed descriptions carry HTML: keep only the words."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(markup or ""))).split())


def parse_time(value):
    """RFC 822 (RSS) or ISO 8601 -> aware UTC datetime, or None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        d = None
    if d is None:
        try:
            d = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=_dt.timezone.utc)
    return d.astimezone(_dt.timezone.utc)


def _items(data, what):
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # real feeds ship a bare "&" now and then: escape those and try once more
        fixed = re.sub(rb"&(?!(?:[A-Za-z]+|#\d+|#x[0-9A-Fa-f]+);)", b"&amp;", data)
        try:
            root = ET.fromstring(fixed)
        except ET.ParseError as exc:
            raise NewsError("%s is not valid XML: %s" % (what, exc))
    return list(root.iter("item"))


def parse_google(data, sym):
    """Google News RSS -> items. The publisher is the ' - Name' tail of each title."""
    out = []
    for it in _items(data, "Google News for %s" % sym):
        title = _text(it, "title")
        publisher = _text(it, "source")
        if publisher and title.endswith(" - " + publisher):
            title = title[:-len(publisher) - 3]
        elif " - " in title:
            title, publisher = title.rsplit(" - ", 1)
        guid = _text(it, "guid") or _text(it, "link")
        out.append({"id": "gn:" + guid, "source": publisher or "Google News", "kind": "headline",
                    "ticker": sym, "title": title.strip(), "summary": "",
                    "link": _text(it, "link"), "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_feed(data, label):
    """A market-wide RSS feed (Moneycontrol, Mint, Business Standard) -> items with no symbol yet."""
    out = []
    for it in _items(data, "%s feed" % label):
        link = _text(it, "link")
        out.append({"id": "rss:" + (_text(it, "guid") or link), "source": label, "kind": "headline",
                    "ticker": "", "title": _plain(_text(it, "title")),
                    "summary": _plain(_text(it, "description")), "link": link,
                    "time": parse_time(_text(it, "pubDate"))})
    return out


def parse_chart(data, sym):
    """Yahoo chart JSON -> {"ticker", "price", "prev_close", "pct", "time"} or raise."""
    try:
        doc = json.loads(data.decode("utf-8") if isinstance(data, bytes) else data)
        meta = doc["chart"]["result"][0]["meta"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise NewsError("chart for %s is not usable" % sym)
    price = meta.get("regularMarketPrice")
    prev = meta.get("chartPreviousClose") or meta.get("previousClose")
    if not price or not prev:
        raise NewsError("chart for %s has no price" % sym)
    when = meta.get("regularMarketTime")
    return {"ticker": sym, "price": round(float(price), 2), "prev_close": round(float(prev), 2),
            "pct": round((float(price) / float(prev) - 1) * 100, 2),
            "time": _dt.datetime.fromtimestamp(when, _dt.timezone.utc) if when else None}


def collect_moves(fetcher, watch, errors=None):
    errors = [] if errors is None else errors
    out = []
    for t in sorted(watch):
        name = "chart-%s.json" % t
        try:
            try:
                data = fetcher.get(CHART % ("query2", yahoo_symbol(t)), name)
            except NewsError as exc:
                if fetcher.offline or not re.search(r"HTTP (429|5\d\d)", str(exc)):
                    raise
                data = fetcher.get(CHART % ("query1", yahoo_symbol(t)), name)
            out.append(parse_chart(data, t))
        except NewsError as exc:
            if not (fetcher.offline and "no file" in str(exc)):
                errors.append("price for %s: %s" % (t, exc))
    return out


def mention_patterns(watch):
    """Per symbol: (company-name regex or None, symbol regex). The name matches any case,
    the symbol only as written in capitals (TCS, not 'tcs'), and only if 3+ characters."""
    pats = {}
    for t, name in watch.items():
        short = short_name(name)
        by_name = None
        if len(short) >= 4:
            by_name = re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(short).replace(r"\ ", r"[\s-]"),
                                 re.I)
        by_sym = re.compile(r"(?<![A-Za-z0-9&-])%s(?![A-Za-z0-9&'\u2019-])" % re.escape(t)) if len(t) >= 3 else None
        pats[t] = (by_name, by_sym)
    return pats


def names_it(it, pats):
    """Does the headline (or its summary) name this company or symbol?"""
    by_name, by_sym = pats
    hay = "%s %s" % (it["title"], it.get("summary") or "")
    return bool((by_name and by_name.search(hay)) or (by_sym and by_sym.search(hay)))


def tag(items, watch):
    """Which of your symbols each item touches, and why it matters."""
    pats = mention_patterns(watch)
    for it in items:
        hay = "%s %s" % (it["title"], it.get("summary") or "")
        touches = {it["ticker"]} if it["ticker"] in watch else set()
        for t, p in pats.items():
            if names_it(it, p):
                touches.add(t)
        it["touches"] = sorted(touches)
        it["tags"] = [label for label, rx in KEYWORDS if re.search(rx, hay, re.I)]
    return items


def dedupe(items):
    """One item per story: same id, or same link, or same title the same day."""
    seen, out = {}, []
    for it in items:
        keys = [it["id"], it["link"],
                "%s|%s" % (re.sub(r"\W+", " ", it["title"].lower()).strip(),
                           it["time"].date() if it["time"] else "")]
        hit = next((seen[k] for k in keys if k and k in seen), None)
        if hit is not None:
            for t in it.get("touches", []):
                if t not in hit["touches"]:
                    hit["touches"].append(t)
            hit["touches"].sort()
            continue
        for k in keys:
            if k:
                seen[k] = it
        out.append(it)
    return out


def fingerprint(it):
    return hashlib.sha1(it["id"].encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- the check

def collect(fetcher, watch, feeds=True, hours=48, errors=None):
    errors = [] if errors is None else errors
    items = []
    pats = mention_patterns(watch)
    for t in sorted(watch):
        try:
            got = parse_google(fetcher.get(google_url(t, watch[t], hours), "google-%s.xml" % t), t)
            got = [i for i in got if names_it(i, pats[t])]  # Google pads a search with loose matches
            got.sort(key=lambda i: i["time"] or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc), reverse=True)
            items += got[:PER_SYMBOL]
        except NewsError as exc:
            errors.append("headlines for %s: %s" % (t, exc))
    if feeds:
        for name, label, url in FEEDS:
            try:
                got = parse_feed(fetcher.get(url, name), label)
            except NewsError as exc:
                if not (fetcher.offline and "no file" in str(exc)):
                    errors.append("%s feed: %s" % (label, exc))
                continue
            items += [i for i in tag(got, watch) if i["touches"]]  # only stories that name your stocks
    return items


def select(items, state, now, hours=48, show_all=False):
    """Items within the window; only unseen ones unless show_all."""
    seen = state.setdefault("seen", {})
    cutoff = now - _dt.timedelta(hours=hours)
    ahead = now + _dt.timedelta(minutes=10)  # a pinned --now: nothing from after it
    out = []
    for it in items:
        if it["time"] is not None and (it["time"] < cutoff or it["time"] > ahead):
            continue
        fp = fingerprint(it)
        it["new"] = fp not in seen
        if it["new"] or show_all:
            out.append(it)
    out.sort(key=lambda i: (i["time"] or now), reverse=True)
    return out


def remember(items, state, now):
    seen = state.setdefault("seen", {})
    stamp = now.isoformat()
    for it in items:
        seen.setdefault(fingerprint(it), stamp)
    cutoff = (now - _dt.timedelta(days=KEEP_DAYS)).isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v >= cutoff}
    state["last_check"] = stamp


def render_moves(moves, move_pct=MOVE_PCT):
    out = []
    for m in moves:
        flag = "MOVING" if abs(m["pct"]) >= move_pct else ""
        when = (" at %s IST" % to_local(m["time"]).strftime("%H:%M")) if m.get("time") else ""
        out.append("%-12s ₹%s %+.2f%% vs previous close ₹%s%s  %s" % (
            m["ticker"], "{:,.2f}".format(m["price"]), m["pct"], "{:,.2f}".format(m["prev_close"]),
            when, flag))
    return out


def render(items, errors, now, moves=None, move_pct=MOVE_PCT):
    out = render_moves(moves or [], move_pct)
    if out:
        out.append("")
    if not items:
        out.append("Nothing new.")
    for it in items:
        when = to_local(it["time"]).strftime("%Y-%m-%d %H:%M IST") if it["time"] else "time unknown"
        who = ",".join(it["touches"]) or it["ticker"] or "market"
        out.append("  %s  %-11s %-13s %s" % (when, who, it["source"][:13], it["title"]))
        extra = []
        if it["tags"]:
            extra.append("tags: " + ", ".join(it["tags"]))
        extra.append(it["link"])
        out.append("      " + "  ".join(extra))
    for e in errors:
        out.append("note: %s" % e)
    out.append("Checked %s. Headlines are leads, not facts; open the story before acting. "
               "News feeds lag and miss things; no NSE or BSE filings are read. "
               "Not financial advice." % to_local(now).strftime("%Y-%m-%d %H:%M IST"))
    return "\n".join(out) + "\n"


def _json_ready(items):
    out = []
    for it in items:
        d = dict(it)
        d["time"] = it["time"].isoformat() if it["time"] else None
        out.append(d)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tickers", nargs="*", help="NSE symbols; default: watchlist + plan + portfolio")
    ap.add_argument("--portfolio", default="trader/portfolio.json")
    ap.add_argument("--watchlist", default="trader/watchlist.json")
    ap.add_argument("--plan", default="trader/plan.json")
    ap.add_argument("--universe", default="trader/universe.txt", help="SYMBOL|Name lines, for company names")
    ap.add_argument("--state", default="trader/state/news.json")
    ap.add_argument("--hours", type=float, default=48, help="ignore items older than this")
    ap.add_argument("--all", action="store_true", help="show seen items too")
    ap.add_argument("--no-feeds", action="store_true", help="skip the market-wide Moneycontrol/Mint/Business Standard feeds")
    ap.add_argument("--no-moves", action="store_true", help="skip the price line per symbol")
    ap.add_argument("--move-pct", type=float, default=MOVE_PCT, help="flag a move this big (percent)")
    ap.add_argument("--offline", metavar="DIR")
    ap.add_argument("--now", help="ISO time, for reproducible runs")
    ap.add_argument("--dry-run", action="store_true", help="do not update the state file")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    known = watch_from_files(args.portfolio, args.watchlist, args.plan, args.universe)
    if args.tickers:
        watch = {bare(t): None for t in args.tickers}
        watch.update({t: known[t] for t in watch if known.get(t)})
        names = load_universe(args.universe)
        watch.update({t: names[t] for t in watch if not watch[t] and names.get(t)})
    else:
        watch = known
    if not watch:
        print("news: no tickers. Pass --tickers, or run /trader-scanner first (it writes "
              "trader/watchlist.json).", file=sys.stderr)
        return 2
    now = parse_time(args.now) if args.now else _dt.datetime.now(_dt.timezone.utc)
    state = load_json(args.state) or {}
    errors = []
    fetcher = Fetcher(offline=args.offline)
    items = collect(fetcher, watch, feeds=not args.no_feeds, hours=args.hours, errors=errors)
    items = dedupe(tag(items, watch))
    shown = select(items, state, now, hours=args.hours, show_all=args.all)
    moves = [] if args.no_moves else collect_moves(fetcher, watch, errors)
    if args.json:
        mv = [dict(m, time=m["time"].isoformat() if m.get("time") else None,
                   moving=abs(m["pct"]) >= args.move_pct) for m in moves]
        print(json.dumps({"checked": now.isoformat(), "moves": mv, "items": _json_ready(shown),
                          "notes": errors}, indent=2))
    else:
        sys.stdout.write(render(shown, errors, now, moves, args.move_pct))
    if not args.dry_run:
        remember(items, state, now)
        folder = os.path.dirname(args.state)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(args.state, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=2)
            fh.write("\n")
    return 0 if any(i["new"] for i in shown) else 1


if __name__ == "__main__":
    sys.exit(main())
