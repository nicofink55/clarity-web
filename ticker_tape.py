"""
Ticker Tape module for Clarity
Scrolling tape with live quotes via Yahoo Finance JSON APIs
(same approach as engine.py - stdlib only, no yfinance needed).
"""

import os, json, sqlite3, streamlit as st
from datetime import datetime, timedelta
from urllib.request import Request, urlopen

_TICKER_DB_PATH = os.path.join(os.path.dirname(__file__), "ticker_analytics.db")
STATIC_ETFS = ["SPY", "QQQ", "DIA", "IWM"]
BASELINE_TICKERS = ["NVDA", "GOOGL", "MSFT", "META", "AVGO", "V"]
TAPE_ROLLING_SLOTS = 8
YF_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _init_db():
    conn = sqlite3.connect(_TICKER_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ticker_searches ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticker TEXT NOT NULL, "
        "searched_at TEXT DEFAULT (datetime('now')))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON ticker_searches(searched_at)")
    conn.commit()
    conn.close()


def log_ticker_search(ticker):
    try:
        _init_db()
        conn = sqlite3.connect(_TICKER_DB_PATH)
        conn.execute(
            "INSERT INTO ticker_searches (ticker, searched_at) VALUES (?, ?)",
            (ticker.upper(), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_trending_tickers(window_hours=24, limit=8):
    try:
        _init_db()
        conn = sqlite3.connect(_TICKER_DB_PATH)
        cutoff = (datetime.utcnow() - timedelta(hours=window_hours)).strftime("%Y-%m-%d %H:%M:%S")
        rows = conn.execute(
            "SELECT ticker, COUNT(*) as cnt FROM ticker_searches "
            "WHERE searched_at >= ? GROUP BY ticker ORDER BY cnt DESC LIMIT ?",
            (cutoff, limit),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def _build_tape_list():
    trending = get_trending_tickers(window_hours=24, limit=TAPE_ROLLING_SLOTS)
    trending = [t for t in trending if t not in STATIC_ETFS]
    seen = set(STATIC_ETFS + trending)
    for bt in BASELINE_TICKERS:
        if len(trending) >= TAPE_ROLLING_SLOTS:
            break
        if bt not in seen:
            trending.append(bt)
            seen.add(bt)
    return STATIC_ETFS + trending


def _yf_get(url):
    req = Request(url, headers=YF_HEADERS)
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


@st.cache_data(ttl=90, show_spinner=False)
def _fetch_quotes(tickers_tuple):
    quotes = {}
    try:
        symbols = ",".join(tickers_tuple)
        data = _yf_get("https://query1.finance.yahoo.com/v7/finance/quote?symbols=" + symbols)
        results = data.get("quoteResponse", {}).get("result", [])
        for q in results:
            t = q.get("symbol", "")
            price = q.get("regularMarketPrice", 0)
            chg = q.get("regularMarketChange", 0)
            pct = q.get("regularMarketChangePercent", 0)
            if t and price:
                quotes[t] = {"price": float(price), "chg": float(chg), "pct": float(pct)}
    except Exception:
        for t in tickers_tuple:
            try:
                data = _yf_get(
                    "https://query1.finance.yahoo.com/v8/finance/chart/" + t + "?range=2d&interval=1d"
                )
                meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
                price = meta.get("regularMarketPrice", 0)
                prev = meta.get("chartPreviousClose", 0) or meta.get("previousClose", 0)
                if price and prev:
                    chg = price - prev
                    pct = (chg / prev) * 100
                    quotes[t] = {"price": float(price), "chg": float(chg), "pct": float(pct)}
                elif price:
                    quotes[t] = {"price": float(price), "chg": 0.0, "pct": 0.0}
            except Exception:
                continue
    return quotes


def _tape_css(dur):
    return (
        "<style>"
        "@keyframes tickerScroll{0%{transform:translateX(0)}100%{transform:translateX(-50%)}}"
        ".ticker-tape-wrap{"
        "position:fixed;top:0;left:0;right:0;height:36px;"
        "background:linear-gradient(180deg,rgba(8,12,20,0.97),rgba(8,12,20,0.90));"
        "border-bottom:1px solid rgba(62,207,142,0.06);"
        "z-index:999;overflow:hidden;backdrop-filter:blur(12px);"
        "display:flex;align-items:center}"
        ".ticker-tape-wrap::before,.ticker-tape-wrap::after{"
        "content:'';position:absolute;top:0;bottom:0;width:60px;z-index:2;pointer-events:none}"
        ".ticker-tape-wrap::before{"
        "left:0;background:linear-gradient(90deg,rgba(8,12,20,0.97),transparent)}"
        ".ticker-tape-wrap::after{"
        "right:0;background:linear-gradient(-90deg,rgba(8,12,20,0.97),transparent)}"
        ".ticker-tape-track{"
        "display:flex;align-items:center;white-space:nowrap;"
        "animation:tickerScroll " + str(dur) + "s linear infinite;"
        "will-change:transform}"
        ".ticker-tape-track:hover{animation-play-state:paused}"
        ".tape-item{"
        "display:inline-flex;align-items:center;gap:6px;"
        "padding:0 20px;border-right:1px solid rgba(62,207,142,0.04);cursor:default}"
        ".tape-sym{font-family:JetBrains Mono,monospace;font-weight:700;font-size:.7rem;"
        "color:#e2e8f0;letter-spacing:.02em}"
        ".tape-px{font-family:JetBrains Mono,monospace;font-weight:500;font-size:.68rem;color:#8b95a8}"
        ".tape-chg{font-family:JetBrains Mono,monospace;font-weight:600;font-size:.65rem}"
        "header[data-testid=stHeader]{"
        "background:transparent!important;height:36px!important;z-index:998!important}"
        ".block-container{padding-top:3.5rem!important}"
        "section[data-testid=stSidebar]{top:36px!important}"
        "@media(max-width:768px){"
        ".ticker-tape-wrap{height:30px}"
        ".tape-item{padding:0 12px;gap:4px}"
        ".tape-sym{font-size:.6rem}.tape-px{font-size:.58rem}.tape-chg{font-size:.55rem}"
        "header[data-testid=stHeader]{height:30px!important}"
        "section[data-testid=stSidebar]{top:30px!important}"
        ".block-container{padding-top:3rem!important}}"
        "</style>"
    )


def render_ticker_tape():
    all_tickers = _build_tape_list()
    quotes = _fetch_quotes(tuple(all_tickers))

    items = []
    for t in all_tickers:
        q = quotes.get(t)
        if q:
            color = "#3ecf8e" if q["chg"] >= 0 else "#f85149"
            arrow = "&#9650;" if q["chg"] >= 0 else "&#9660;"
            glow = "rgba(62,207,142,0.3)" if q["chg"] >= 0 else "rgba(248,81,73,0.3)"
            items.append(
                '<div class="tape-item">'
                + '<span class="tape-sym">' + t + "</span>"
                + '<span class="tape-px">' + "{:,.2f}".format(q["price"]) + "</span>"
                + '<span class="tape-chg" style="color:' + color
                + ";text-shadow:0 0 8px " + glow + '">'
                + arrow + " " + "{:+.2f}".format(q["chg"])
                + " (" + "{:+.2f}%".format(q["pct"]) + ")</span>"
                + "</div>"
            )
        else:
            items.append(
                '<div class="tape-item">'
                + '<span class="tape-sym">' + t + "</span>"
                + '<span class="tape-px" style="color:#3d4655">&mdash;</span>'
                + "</div>"
            )

    strip = "".join(items)
    track = strip + strip
    dur = max(30, len(all_tickers) * 4)

    return (
        _tape_css(dur)
        + '<div class="ticker-tape-wrap">'
        + '<div class="ticker-tape-track">'
        + track
        + "</div></div>"
    )
