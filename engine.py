"""
C L A R I T Y
═══════════════
SEC Filing Downloader + DCF Analyzer — Unified Desktop App
Download filings from EDGAR → auto-parse → instant DCF valuation.

INSTALL:  pip install beautifulsoup4 lxml
          pip install pdfplumber  (optional, PDF filings)
          pip install tkinterdnd2 (optional, drag-and-drop)

USAGE:    python clarity.py
"""

import os, re, sys, math, json, time
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from pathlib import Path

# ─── Optional imports ───
try:
    from bs4 import BeautifulSoup; HAS_BS4 = True
except ImportError:
    HAS_BS4 = False
try:
    import pdfplumber; HAS_PDF = True
except ImportError:
    HAS_PDF = False


# ════════════════════════════════════════════════════════
#  FRED: Live 10-Year Treasury (Risk-Free Rate)
# ════════════════════════════════════════════════════════

_rf_cache = {'rate': None, 'ts': 0}

def fetch_risk_free_rate(fallback=0.035):
    """Fetch the latest 10-Year Treasury yield from FRED.
    Uses the public CSV endpoint (no API key needed).
    Caches for 6 hours to avoid hammering FRED."""
    if _rf_cache['rate'] and (time.time() - _rf_cache['ts']) < 21600:  # 6hr cache
        return _rf_cache['rate']
    try:
        url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DGS10&cosd=2024-01-01"
        req = Request(url, headers={'User-Agent': 'Clarity/1.0'})
        raw = urlopen(req, timeout=8).read().decode('utf-8')
        # CSV: DATE,DGS10 — last non-empty row is most recent
        lines = [l.strip() for l in raw.strip().split('\n') if l.strip() and not l.startswith('DATE')]
        for line in reversed(lines):
            parts = line.split(',')
            if len(parts) == 2 and parts[1] not in ('.', ''):
                rate = float(parts[1]) / 100.0  # Convert from percentage
                if 0.005 < rate < 0.15:  # Sanity check: 0.5% to 15%
                    _rf_cache['rate'] = rate
                    _rf_cache['ts'] = time.time()
                    return rate
    except Exception:
        pass
    return _rf_cache['rate'] if _rf_cache['rate'] else fallback


# ════════════════════════════════════════════════════════
#  SEC EDGAR DOWNLOADER ENGINE
# ════════════════════════════════════════════════════════

SEC_HEADERS = {"User-Agent": "ClarityApp/1.0 (nico@example.com)"}

def sec_fetch(url, retries=5, timeout=45):
    for attempt in range(retries):
        delay = 0.2 + attempt * 0.5  # 0.2s, 0.7s, 1.2s, 1.7s, 2.2s
        time.sleep(delay)
        try:
            req = Request(url, headers=SEC_HEADERS)
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            err_str = str(e)
            is_rate_limit = '503' in err_str or '429' in err_str or 'Service Unavailable' in err_str
            is_timeout = 'timed out' in err_str.lower() or 'timeout' in err_str.lower()
            if (is_rate_limit or is_timeout) and attempt < retries - 1:
                backoff = 2.0 * (attempt + 1)  # 2s, 4s, 6s, 8s
                time.sleep(backoff)
                continue
            if attempt == retries - 1:
                raise
    raise Exception(f"SEC fetch failed after {retries} retries: {url}")

def sec_json(url):
    return json.loads(sec_fetch(url))

def lookup_cik(ticker):
    data = sec_json("https://www.sec.gov/files/company_tickers.json")
    t = ticker.upper()
    for entry in data.values():
        if entry["ticker"].upper() == t:
            return str(entry["cik_str"]).zfill(10), entry["title"]
    raise ValueError(f"Ticker '{ticker}' not found in SEC database")

def find_filing(cik, form_type):
    data = sec_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    company = data.get("name", "Unknown")
    recent = data.get("filings", {}).get("recent", {})
    forms, dates = recent.get("form", []), recent.get("filingDate", [])
    accessions, docs = recent.get("accessionNumber", []), recent.get("primaryDocument", [])
    for target in [form_type, form_type + "/A"]:
        for i, f in enumerate(forms):
            if f == target:
                return {"company": company, "form": f, "date": dates[i],
                        "accession": accessions[i], "doc": docs[i], "cik": cik}
    alt = "10-K" if form_type == "10-Q" else "10-Q"
    for target in [alt, alt + "/A"]:
        for i, f in enumerate(forms):
            if f == target:
                return {"company": company, "form": f, "date": dates[i],
                        "accession": accessions[i], "doc": docs[i], "cik": cik, "_fallback": True}
    raise ValueError(f"No {form_type} or {alt} found for {company}")

def download_filing(info, save_dir):
    acc_flat = info["accession"].replace("-", "")
    cik_clean = info["cik"].lstrip("0")
    # Primary URL: standard Archives path
    url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{acc_flat}/{info['doc']}"
    try:
        content = sec_fetch(url, retries=5, timeout=60)
    except Exception:
        # Fallback: try without /Archives/ prefix (alternate EDGAR CDN path)
        alt_url = f"https://www.sec.gov/Archives/edgar/data/{cik_clean}/{info['accession']}/{info['doc']}"
        try:
            content = sec_fetch(alt_url, retries=3, timeout=60)
        except Exception:
            raise Exception(f"Could not download filing after retries.\nURL: {url}\nTip: Try again in 30 seconds — EDGAR may be rate-limiting.")
    ext = os.path.splitext(info["doc"])[1] or ".htm"
    safe_form = info["form"].replace("/", "-")
    filename = f"{safe_form}_{info['date']}{ext}"
    os.makedirs(save_dir, exist_ok=True)
    path = os.path.join(save_dir, filename)
    with open(path, "wb") as f:
        f.write(content)
    return path, len(content)


# ════════════════════════════════════════════════════════
#  MULTI-FILING ENGINE (Pull N quarters/annuals for trends)
# ════════════════════════════════════════════════════════

def find_filings(cik, form_type, count=4):
    """Return the most recent `count` filings of the given type."""
    data = sec_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    company = data.get("name", "Unknown")
    recent = data.get("filings", {}).get("recent", {})
    forms, dates = recent.get("form", []), recent.get("filingDate", [])
    accessions, docs = recent.get("accessionNumber", []), recent.get("primaryDocument", [])

    results = []
    for target in [form_type, form_type + "/A"]:
        for i, f in enumerate(forms):
            if f == target:
                # Skip amendments if we already have a filing for the same period
                results.append({
                    "company": company, "form": f, "date": dates[i],
                    "accession": accessions[i], "doc": docs[i], "cik": cik
                })
                if len(results) >= count:
                    return results
    return results


def download_and_parse_filings(ticker, form_type="10-Q", count=4, save_dir=None, log_fn=None):
    """Download and parse multiple filings, return list of parsed financials.
    
    Returns:
        quarterly_data: list of parsed fins dicts, most recent first
        trailing: dict with TTM aggregates and trend data
    """
    import tempfile
    if save_dir is None:
        save_dir = tempfile.mkdtemp(prefix="clarity_")

    cik, name = lookup_cik(ticker)
    filings = find_filings(cik, form_type, count=count)

    if not filings:
        return [], {}

    quarterly_data = []
    for info in filings:
        try:
            path, _ = download_filing(info, save_dir)
            if path.lower().endswith('.pdf'):
                fins = parse_pdf(path, ticker=ticker)
            else:
                fins = parse_html(path, ticker=ticker)
            fins['_filing_date'] = info['date']
            fins['_filing_form'] = info['form']
            quarterly_data.append(fins)
            if log_fn:
                log_fn(f"  Parsed {info['form']} {info['date']}: {len([k for k in fins if not k.startswith('_')])} fields", "ok")
        except Exception as e:
            if log_fn:
                log_fn(f"  Failed {info['form']} {info['date']}: {e}", "warn")

    if not quarterly_data:
        return [], {}

    # ── Build trailing aggregates ──
    trailing = _compute_trailing_aggregates(quarterly_data, form_type)
    return quarterly_data, trailing


def _compute_trailing_aggregates(quarterly_data, form_type):
    """Compute TTM / trailing aggregates from multiple parsed filings."""
    trailing = {
        'quarters_available': len(quarterly_data),
        'form_type': form_type,
    }

    if not quarterly_data:
        return trailing

    # Determine annualization factor per filing
    # 10-Q: each quarter is ~1/4 of annual; 10-K: each is already annual
    is_quarterly = form_type in ('10-Q', '10-Q/A')

    # For 10-Q: Sum raw quarterly figures (divide annualized values back to quarterly)
    # For 10-K: Use most recent, but track growth trend over multiple years
    if is_quarterly and len(quarterly_data) >= 2:
        # The parse engine annualizes quarterly data. We need to de-annualize.
        # Detect the annualization factor from the filing
        # Most 10-Qs are 3-month (ann=4) or 9-month (ann=4/3) or 6-month (ann=2)
        # We'll extract the raw quarterly contribution by dividing by ann factor.
        # Since we don't store ann_factor, we approximate:
        # - If filing has 'nine months' in form context → value = 9M annualized to 12M
        # - Default: assume 3-month quarterly, annualized ×4
        
        flow_fields = ['revenue', 'net_income', 'operating_income', 'operating_cf',
                        'capex', 'depreciation', 'fcf', 'gross_profit', 'sbc']
        
        # Strategy: Use the most recent filing's annualized figures as primary
        # Then compute growth trend from the series
        most_recent = quarterly_data[0]
        
        # Revenue trend across quarters (for CAGR computation)
        rev_series = []
        ni_series = []
        fcf_series = []
        for q in quarterly_data:
            if q.get('revenue', 0) > 0:
                rev_series.append(q['revenue'])
            if q.get('net_income', 0) > 0:
                ni_series.append(q['net_income'])
            if q.get('fcf', 0) > 0:
                fcf_series.append(q['fcf'])
        
        trailing['rev_series'] = rev_series
        trailing['ni_series'] = ni_series
        trailing['fcf_series'] = fcf_series
        
        # Compute trailing revenue growth (most recent vs oldest)
        if len(rev_series) >= 2 and rev_series[-1] > 0:
            trailing['rev_cagr'] = (rev_series[0] / rev_series[-1]) ** (1 / len(rev_series)) - 1
        
        # Compute FCF stability (coefficient of variation)
        if len(fcf_series) >= 3:
            mean_fcf = sum(fcf_series) / len(fcf_series)
            if mean_fcf > 0:
                variance = sum((f - mean_fcf) ** 2 for f in fcf_series) / len(fcf_series)
                trailing['fcf_cv'] = (variance ** 0.5) / mean_fcf  # lower = more stable
        
        # Margin trend
        if len(rev_series) >= 2 and len(ni_series) >= 2:
            recent_margin = ni_series[0] / rev_series[0] if rev_series[0] > 0 else 0
            oldest_margin = ni_series[-1] / rev_series[-1] if rev_series[-1] > 0 else 0
            trailing['margin_trend'] = recent_margin - oldest_margin  # positive = expanding

    elif not is_quarterly and len(quarterly_data) >= 2:
        # 10-K: Multi-year analysis
        rev_series = [q.get('revenue', 0) for q in quarterly_data if q.get('revenue', 0) > 0]
        ni_series = [q.get('net_income', 0) for q in quarterly_data if q.get('net_income', 0) > 0]
        fcf_series = [q.get('fcf', 0) for q in quarterly_data if q.get('fcf', 0) > 0]
        
        trailing['rev_series'] = rev_series
        trailing['ni_series'] = ni_series
        trailing['fcf_series'] = fcf_series
        
        # Annual CAGR over the available years
        if len(rev_series) >= 2 and rev_series[-1] > 0:
            years = len(rev_series) - 1
            trailing['rev_cagr'] = (rev_series[0] / rev_series[-1]) ** (1 / years) - 1

        if len(fcf_series) >= 2 and fcf_series[-1] > 0:
            years = len(fcf_series) - 1
            trailing['fcf_cagr'] = (fcf_series[0] / fcf_series[-1]) ** (1 / years) - 1
        
        # FCF stability
        if len(fcf_series) >= 3:
            mean_fcf = sum(fcf_series) / len(fcf_series)
            if mean_fcf > 0:
                variance = sum((f - mean_fcf) ** 2 for f in fcf_series) / len(fcf_series)
                trailing['fcf_cv'] = (variance ** 0.5) / mean_fcf
        
        # Margin trend  
        if len(rev_series) >= 2 and len(ni_series) >= 2:
            trailing['margin_trend'] = (ni_series[0] / rev_series[0]) - (ni_series[-1] / rev_series[-1])

    return trailing


# ─── SEC EDGAR: Shares Outstanding from XBRL companyfacts ───

def fetch_shares_from_edgar(ticker):
    """Get diluted shares outstanding from SEC EDGAR XBRL data. Returns share count (raw, not millions) or None."""
    try:
        cik, company = lookup_cik(ticker)
        data = sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
        gaap = data.get("facts", {}).get("us-gaap", {})

        # Try diluted shares first, then common shares outstanding
        for concept_name in [
            "WeightedAverageNumberOfDilutedSharesOutstanding",
            "CommonStockSharesOutstanding",
            "EntityCommonStockSharesOutstanding",
            "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
        ]:
            concept = gaap.get(concept_name)
            if not concept:
                # Also check dei namespace for EntityCommonStockSharesOutstanding
                if concept_name.startswith("Entity"):
                    concept = data.get("facts", {}).get("dei", {}).get(concept_name)
                if not concept:
                    continue

            values = (concept.get("units") or {}).get("shares", [])
            if not values:
                continue

            # Find the most recent annual (10-K) value, then fall back to quarterly.
            # IMPORTANT: Multi-class stocks (Visa, Google, Meta) report per-class
            # share counts with dimensional qualifiers. Multiple entries can share
            # the same end date. We group by (form, end) and take the LARGEST
            # value per date, which is the primary/total class.
            from collections import defaultdict
            date_groups = defaultdict(list)
            for v in values:
                end = v.get("end", "")
                form = v.get("form", "")
                val = v.get("val", 0)
                if val <= 0 or not end:
                    continue
                date_groups[(form, end)].append(val)

            best_val = 0
            best_date = ""
            best_form_rank = 99  # lower = better (10-K=0, 10-Q=1)
            for (form, end), vals in date_groups.items():
                largest = max(vals)  # Take largest value for this date (primary class)
                form_rank = 0 if form in ("10-K", "10-K/A") else 1 if form in ("10-Q", "10-Q/A") else 99
                # Prefer 10-K over 10-Q, then most recent date
                is_better = (form_rank < best_form_rank or 
                            (form_rank == best_form_rank and end > best_date) or
                            (form_rank <= 1 and best_form_rank > 1))
                if is_better and largest > 0:
                    best_val = largest
                    best_date = end
                    best_form_rank = form_rank

            if best_val > 0:
                return best_val

        return None
    except Exception as e:
        return None


# ─── Yahoo Finance (stdlib only, no yfinance needed) ───

YF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

def _yf_get(url):
    """Fetch JSON from Yahoo Finance with proper headers."""
    req = Request(url, headers=YF_HEADERS)
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())

def fetch_market_data(ticker):
    """Fetch price + shares. Price from Yahoo, shares from SEC EDGAR XBRL (primary) or Yahoo (fallback)."""
    price = 0; shares = 0; name = ticker; beta = None

    # ── Shares: SEC EDGAR companyfacts (most reliable) ──
    edgar_shares = fetch_shares_from_edgar(ticker)
    if edgar_shares and edgar_shares > 0:
        shares = edgar_shares

    # ── Attempt 1: v7 quote (lightweight, usually has price) ──
    try:
        data = _yf_get(f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={ticker}")
        q = data.get("quoteResponse", {}).get("result", [{}])[0]
        price = q.get("regularMarketPrice", 0)
        name = q.get("shortName") or q.get("longName") or ticker
        if not shares:
            shares = q.get("sharesOutstanding", 0)
            if not shares:
                mcap = q.get("marketCap", 0)
                if mcap and price: shares = mcap / price
    except: pass

    # ── Attempt 2: v10 quoteSummary (has beta, more details) ──
    try:
        data = _yf_get(
            f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}"
            f"?modules=price,defaultKeyStatistics,summaryDetail")
        mods = data.get("quoteSummary", {}).get("result", [{}])[0]
        pm = mods.get("price", {})
        ks = mods.get("defaultKeyStatistics", {})
        sd = mods.get("summaryDetail", {})
        if not price:
            price = pm.get("regularMarketPrice", {}).get("raw", 0)
        if not shares:
            shares = (ks.get("sharesOutstanding", {}).get("raw", 0)
                      or pm.get("sharesOutstanding", {}).get("raw", 0)
                      or ks.get("floatShares", {}).get("raw", 0))
        if not shares and price:
            mcap = pm.get("marketCap", {}).get("raw", 0) or sd.get("marketCap", {}).get("raw", 0)
            if mcap: shares = mcap / price
        if not name or name == ticker:
            name = pm.get("shortName", ticker)
        beta = ks.get("beta", {}).get("raw") or beta
    except: pass

    # ── Attempt 3: v8 chart API (always works, price only) ──
    if not price:
        try:
            data = _yf_get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d")
            meta = data.get("chart", {}).get("result", [{}])[0].get("meta", {})
            price = meta.get("regularMarketPrice", 0)
        except: pass

    if not price:
        raise ValueError(f"No data found for {ticker}")

    return {
        "price": float(price),
        "shares_mil": shares / 1e6 if shares else 0,
        "shares_source": "SEC EDGAR" if edgar_shares else "Yahoo Finance",
        "name": name or ticker,
        "beta": beta,
    }


# ════════════════════════════════════════════════════════
#  LIVE PEER COMPS — EDGAR XBRL + Yahoo v8 Prices
# ════════════════════════════════════════════════════════

# Curated peer groups per sector (liquid, well-known companies)
PEER_GROUPS = {
    'hyperscaler':      ['MSFT', 'GOOGL', 'AMZN', 'META', 'ORCL', 'CRM', 'ADBE', 'NOW'],
    'saas_tech':        ['CRM', 'NOW', 'ADBE', 'WDAY', 'SNOW', 'DDOG', 'ZS', 'PANW'],
    'pharma':           ['LLY', 'JNJ', 'ABBV', 'MRK', 'PFE', 'TMO', 'ABT', 'BMY'],
    'fintech':          ['PYPL', 'SQ', 'FISV', 'FIS', 'GPN', 'ADYEY', 'AFRM', 'SOFI'],
    'payment_network':  ['V', 'MA', 'AXP', 'PYPL', 'GPN', 'FISV', 'FIS'],
    'consumer':         ['PG', 'KO', 'PEP', 'COST', 'WMT', 'CL', 'MCD', 'NKE'],
    'industrial':       ['HON', 'CAT', 'DE', 'MMM', 'GE', 'EMR', 'ETN', 'ITW'],
    'ep':               ['XOM', 'CVX', 'COP', 'EOG', 'MPC', 'VLO', 'PSX', 'SLB'],
    'midstream':        ['WMB', 'EPD', 'ET', 'KMI', 'OKE', 'MPLX', 'TRGP', 'LNG'],
    'utility':          ['NEE', 'DUK', 'SO', 'D', 'AEP', 'SRE', 'EXC', 'XEL'],
    'telecom':          ['T', 'VZ', 'TMUS', 'CMCSA', 'CHTR'],
    'insurance':        ['BRK-B', 'PGR', 'TRV', 'ALL', 'MET', 'AIG', 'CB', 'AFL'],
    'bank':             ['JPM', 'BAC', 'WFC', 'GS', 'MS', 'C', 'USB', 'PNC'],
    'specialty_lender': ['STWD', 'BXMT', 'LADR', 'ABR', 'TPVG', 'KREF', 'RC', 'ARI'],
    'aero_defense':     ['LMT', 'RTX', 'BA', 'NOC', 'GD', 'LHX', 'HII', 'TDG'],
    'data_analytics':   ['PLTR', 'SNOW', 'DDOG', 'MDB', 'SPLK', 'ESTC', 'CFLT'],
}

# Module-level cache: {sector: {pe: X, ev_ebitda: Y, ev_rev: Z, timestamp: T}}
_live_comps_cache = {}
_LIVE_COMPS_TTL = 3600  # 1 hour cache

def _get_xbrl_annual_value(gaap, concepts, target_end):
    """Get XBRL value matching target end date, with nearby-date fallback."""
    for concept in concepts:
        c = gaap.get(concept)
        if not c: continue
        for unit_key in c.get("units", {}):
            entries = c["units"][unit_key]
            matches = [e for e in entries
                      if e.get("end") == target_end
                      and e.get("form") in ("10-K", "10-K/A", "10-Q", "10-Q/A")]
            if matches:
                return max(m.get("val", 0) for m in matches)
    # Nearby fallback (±60 days)
    from datetime import datetime
    try:
        target_dt = datetime.strptime(target_end, "%Y-%m-%d")
    except:
        return 0
    best, best_gap = 0, 999
    for concept in concepts:
        c = gaap.get(concept)
        if not c: continue
        for unit_key in c.get("units", {}):
            for e in c["units"][unit_key]:
                if e.get("form") not in ("10-K", "10-K/A"): continue
                try:
                    gap = abs((datetime.strptime(e.get("end", ""), "%Y-%m-%d") - target_dt).days)
                    if gap < 60 and gap < best_gap and e.get("val", 0) != 0:
                        best, best_gap = e["val"], gap
                except: continue
    return best

def _fetch_peer_financials_xbrl(cik):
    """Fetch period-aligned annual financials from EDGAR XBRL for one peer."""
    data = sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json")
    gaap = data.get("facts", {}).get("us-gaap", {})
    dei = data.get("facts", {}).get("dei", {})

    REV_CONCEPTS = [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues", "SalesRevenueNet", "InterestAndDividendIncomeOperating",
    ]
    # Find most recent 10-K end date across all revenue concepts
    best_end, best_rev = "", 0
    for rc in REV_CONCEPTS:
        c = gaap.get(rc)
        if not c: continue
        for uk in c.get("units", {}):
            annuals = [e for e in c["units"][uk]
                      if e.get("form") in ("10-K", "10-K/A") and e.get("end", "") >= "2023-01-01"]
            if annuals:
                annuals.sort(key=lambda x: x.get("end", ""), reverse=True)
                if annuals[0]["end"] > best_end:
                    best_end, best_rev = annuals[0]["end"], annuals[0].get("val", 0)
    if not best_end:
        return None

    gv = lambda concepts: _get_xbrl_annual_value(gaap, concepts, best_end)

    ni = gv(["NetIncomeLoss", "NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss"])
    oi = gv(["OperatingIncomeLoss"])
    da = gv(["DepreciationDepletionAndAmortization", "DepreciationAndAmortization"])
    equity = gv(["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"])
    debt = gv(["LongTermDebt", "LongTermDebtNoncurrent"])
    cash = gv(["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsAndShortTermInvestments"])
    shares = gv(["WeightedAverageNumberOfDilutedSharesOutstanding",
                  "WeightedAverageNumberOfShareOutstandingBasicAndDiluted"])
    if not shares:
        shares = gv(["CommonStockSharesOutstanding"])
    # EPS-implied fallback for multi-class stocks
    eps = gv(["EarningsPerShareDiluted"])
    if eps and eps > 0 and ni and ni > 0:
        eps_implied = ni / eps
        if shares <= 0:
            shares = eps_implied
        elif shares > 0:
            ratio = shares / eps_implied
            if ratio < 0.4 or ratio > 2.5:
                shares = eps_implied

    return {"revenue": best_rev, "net_income": ni, "operating_income": oi,
            "da": da, "shares": shares, "equity": equity, "debt": debt, "cash": cash}


def fetch_live_comps(sector, log_fn=None):
    """Fetch live sector comps from EDGAR XBRL + Yahoo prices.

    Returns dict with median PE, EV/EBITDA, EV/Revenue, peer_count, or None on failure.
    Results are cached for 1 hour.
    """
    # Check cache
    cached = _live_comps_cache.get(sector)
    if cached and (time.time() - cached.get('_ts', 0)) < _LIVE_COMPS_TTL:
        return cached

    peers = PEER_GROUPS.get(sector)
    if not peers:
        return None

    # Fetch ticker CIK map
    try:
        ticker_data = sec_json("https://www.sec.gov/files/company_tickers.json")
    except:
        return None

    def get_cik(t):
        for entry in ticker_data.values():
            if entry["ticker"].upper() == t.upper():
                return str(entry["cik_str"]).zfill(10)
        return None

    pe_list, ev_ebitda_list, ev_rev_list = [], [], []

    for peer in peers:
        try:
            cik = get_cik(peer)
            if not cik: continue

            fins = _fetch_peer_financials_xbrl(cik)
            if not fins or not fins.get("shares") or fins["shares"] <= 0:
                continue

            # Fetch price
            price_data = _yf_get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{peer}?range=1d&interval=1d")
            price = price_data.get("chart", {}).get("result", [{}])[0].get("meta", {}).get("regularMarketPrice", 0)
            if not price or price <= 0:
                continue

            rev, ni, oi, da = fins["revenue"], fins["net_income"], fins["operating_income"], fins["da"]
            shares, debt, cash = fins["shares"], fins.get("debt", 0) or 0, fins.get("cash", 0) or 0

            mcap = price * shares
            ev = mcap + debt - cash
            ebitda = (oi or 0) + (da or 0)

            # Margin sanity check
            if ni and rev and rev > 0 and abs(ni / rev) > 0.80:
                continue

            if ni and ni > 0:
                pe = mcap / ni
                if 0 < pe < 120: pe_list.append(pe)
            if ebitda and ebitda > 0:
                eve = ev / ebitda
                if 0 < eve < 60: ev_ebitda_list.append(eve)
            if rev and rev > 0:
                evr = ev / rev
                if 0 < evr < 30: ev_rev_list.append(evr)

        except:
            continue

    from statistics import median as _median

    if len(pe_list) < 3:
        return None  # Not enough data, fall back to hardcoded

    result = {
        'pe': round(_median(pe_list), 1),
        'ev_ebitda': round(_median(ev_ebitda_list), 1) if len(ev_ebitda_list) >= 3 else None,
        'ev_revenue': round(_median(ev_rev_list), 1) if len(ev_rev_list) >= 3 else None,
        'peer_count': len(pe_list),
        'source': 'live_edgar',
        '_ts': time.time(),
    }
    _live_comps_cache[sector] = result

    if log_fn:
        log_fn(f"Live comps: PE={result['pe']}x EV/EBITDA={result.get('ev_ebitda','—')}x ({result['peer_count']} peers)", "info")

    return result


# ════════════════════════════════════════════════════════
#  CORE ENGINE (all parsing + DCF logic)
# ════════════════════════════════════════════════════════

def clean_num(text):
    if not text or not isinstance(text, str): return None
    text = text.strip()
    if text in ('\u2014','\u2013','-','','N/A','*','$','N/M',')','('): return None
    is_neg = '(' in text
    cleaned = re.sub(r'[$,\s\(\)]', '', text)
    cleaned = re.sub(r'%.*$', '', cleaned)
    cleaned = re.sub(r'[A-Za-z]+$', '', cleaned)
    if not cleaned or cleaned in ('-','\u2014','\u2013'): return None
    try:
        val = float(cleaned)
        return -val if is_neg else val
    except ValueError:
        return None

_SCALE_DEFAULT = 'default'  # Sentinel: no explicit scale detected (must not collide with 1e3)

def detect_scale(text):
    t = text.lower()
    # Also match (000 without $ sign — e.g. "(000, except per share data)"
    thou_count = len(re.findall(r'in\s+thousands|in\s+\$\s*000|\(\s*thousands|\(0{3}[,\s)]', t))
    mill_count = len(re.findall(r'in\s+millions|\(\s*millions', t))
    bill_count = len(re.findall(r'in\s+billions|\(\s*billions', t))

    # CRITICAL FIX: "(in millions, except shares in thousands)" is very common.
    # The "thousands" only applies to share counts, not financial data.
    if thou_count > 0 and mill_count > 0:
        thou_in_shares_context = bool(re.search(
            r'(?:shares?|number\s+of\s+shares?|share\s+data|per\s+share)'
            r'.*?(?:in\s+thousands|thousands)', t))
        if thou_in_shares_context:
            return 1e6  # Financial data is in millions

    if bill_count > 0 and bill_count >= thou_count and bill_count >= mill_count:
        return 1e9
    if thou_count > 0 and thou_count >= mill_count:
        return 1e3
    if mill_count > 0:
        return 1e6
    patterns = [
        # Thousands patterns — check these BEFORE "except per share"
        (r'\(\s*\$\s*0{3}\s*[s,)]', 1e3), (r'\(\s*\$\s*0{3}\b', 1e3),
        (r'\(\s*0{3}s?\s*\)', 1e3), (r'in\s+\$\s*0{3}', 1e3),
        (r'\(0{3}[,\s]', 1e3),  # Matches "(000," or "(000 " without $ or closing paren
        # Millions patterns
        (r'except\s+per[\s-]+share', 1e6), (r'except\s+share\s+data', 1e6),
    ]
    for pat, sc in patterns:
        if re.search(pat, t): return sc
    return _SCALE_DEFAULT

def _scale_is_explicit(text):
    """Return True if text contains an explicit scale indicator (not just the default 1000)."""
    t = text.lower()
    return bool(re.search(
        r'in\s+thousands|in\s+millions|in\s+billions|\(\s*thousands|\(\s*millions|\(\s*billions'
        r'|in\s+\$\s*000|\(\s*\$\s*0{3}|\(0{3}[,\s)]'
        r'|except\s+per[\s-]+share|except\s+share\s+data', t))

def detect_form(full_text, filename=None):
    ft = full_text.lower()
    if filename:
        fn = filename.lower()
        if '10-q' in fn or '10q' in fn: return '10-Q', True
        if '20-f' in fn or '20f' in fn: return '20-F', False
        if '10-k' in fn or '10k' in fn: return '10-K', False
    if re.search(r'\bq[1-3]\b', ft[:500]): return '10-Q', True
    area = ft[:30000]
    if re.search(r'\b10-q\b', area) or re.search(r'quarterly\s+report', area): return '10-Q', True
    if re.search(r'\b20-f\b', area): return '20-F', False
    if re.search(r'three\s+months?\s+ended', ft): return '10-Q', True
    return '10-K', False

# ── Ticker-based sector overrides (authoritative — skips text detection) ──
# Use this for companies whose filings contain misleading cross-sector keywords.
TICKER_SECTOR_OVERRIDE = {
    # Payment Networks — filings mention "net interest income" etc. but they're not banks
    'V': 'payment_network', 'MA': 'payment_network', 'AXP': 'payment_network',
    # Mega-Cap Tech / Hyperscalers — ad revenue + data centers, not banks or SaaS
    'META': 'hyperscaler', 'GOOGL': 'hyperscaler', 'GOOG': 'hyperscaler',
    'AMZN': 'hyperscaler', 'MSFT': 'hyperscaler', 'AAPL': 'hyperscaler',
    'NVDA': 'hyperscaler', 'ORCL': 'hyperscaler', 'NFLX': 'hyperscaler',
    # Fintech — often mention banking terms but aren't banks
    'PYPL': 'fintech', 'SQ': 'fintech', 'SOFI': 'fintech', 'AFRM': 'fintech',
    'COIN': 'fintech',
    # Banks (explicit)
    'JPM': 'bank', 'BAC': 'bank', 'WFC': 'bank', 'GS': 'bank', 'MS': 'bank',
    'C': 'bank', 'USB': 'bank', 'PNC': 'bank', 'SCHW': 'bank', 'TFC': 'bank',
    # Insurance
    'BRK-B': 'insurance', 'BRK-A': 'insurance',
}

SECTOR_RULES = [
    (['net asset value per share','business development company','regulated investment company',
      'investment income','net investment income','total investment income','net increase in net assets',
      'net realized gain','unrealized appreciation','portfolio company','subchapter m',
      'distributable earnings','incentive fees'], 'bdc', 4),
    (['defense contract','defense system','defense segment','military aircraft','missile system',
      'fighter aircraft','aircraft carrier','naval vessel','munitions','weapons system','warfighter',
      'department of defense','dod contract','pentagon','classified program',
      'rotary wing','combat system','radar system','sonar','electronic warfare',
      'f-35','f35','patriot missile','guided missile','hypersonic weapon',
      'defense electronics','defense revenue','defense backlog','funded backlog','unfunded backlog',
      'cost-plus contract','cost reimbursement contract',
      'lockheed','raytheon','northrop','general dynamics','l3harris','bae systems'], 'aero_defense', 3),
    (['net interest income','provision for credit loss','loan loss','nonperforming',
      'tier 1 capital','net interest margin','deposits','allowance for loan'], 'bank', 3),
    (['mortgage reit','bridge loan','investor loan','fix and flip','fix-and-flip','dscr loan',
      'commercial mortgage','mortgage-backed','warehouse line','loan origination','real estate debt',
      'non-qm','mezzanine loan','securitization trust'], 'specialty_lender', 3),
    (['medical loss ratio','premiums earned','claims and benefits','underwriting income',
      'policy liabilities','loss ratio','benefit ratio','medical costs'], 'insurance', 3),
    (['regulated utility','rate base','public utility','kilowatt','megawatt',
      'rate case','electric utility','generation capacity','ratepayer'], 'utility', 3),
    (['barrels of oil','crude oil production','natural gas production','proved reserves',
      'exploration and production','boe/d','drilling'], 'ep', 3),
    (['pipeline','gathering and processing','natural gas liquids','throughput',
      'tariff rate','master limited','midstream'], 'midstream', 3),
    (['clinical trial','fda approval','drug candidate','therapeutic area',
      'phase 1','phase 2','phase 3','investigational new drug','biologics',
      'new drug application'], 'pharma', 2),
    (['wireless subscribers','postpaid','prepaid subscribers','spectrum','cell sites',
      'arpu','average revenue per user','mobile subscribers'], 'telecom', 3),
    (['credit rating','credit score','fico score','credit bureau','ratings revenue',
      'index revenue','index licensing','analytics revenue',
      'market intelligence','commodity insights','capital iq','platts',
      'debt rating','bond rating','issuer credit',
      'structured finance rating','financial data subscription',
      'data analytics subscription','decision analytics',
      'scoring solutions','esg rating','credit decisioning',
      'fair isaac','moodys','s&p global','verisk','msci','factset','morningstar',
      'rating agency','credit assessment','origination score'], 'data_analytics', 3),
    (['subscription revenue','annual recurring revenue','saas','cloud-based platform',
      'software-as-a-service','recurring revenue','platform revenue',
      'monthly active user','net retention rate','subscription services',
      'human capital management','enterprise resource planning',
      'marketing platform','marketing automation','demand-side platform','data-driven marketing',
      'programmatic advertising','omnichannel marketing','customer data platform','marketing cloud',
      'adtech','ad tech','advertising platform','campaign management',
      'revenue cycle management','healthcare software','claims management',
      'workflow automation','cloud-based software','platform-as-a-service',
      'net revenue retention','dollar-based net retention','arr','annual contract value',
      'customer acquisition cost','lifetime value','land and expand'], 'saas_tech', 2),
    (['payment network','card network','network transaction','credential',
      'acceptance locations','acceptance network','scheme fee','scheme volume',
      'data processing revenue','service revenue','international transaction revenue',
      'client incentive','visa inc','mastercard','payment scheme',
      'cardholder','issuer','acquirer','four-party model',
      'visa u.s.a.','visa international','payments volume','processed transactions',
      'cross-border volume'], 'payment_network', 3),
    (['payment processing','transaction volume','gross payment volume',
      'payment facilitator','interchange','total payment volume',
      'take rate','merchant','remittance','cross-border','money transfer',
      'send volume','disbursement','foreign exchange spread','fx spread',
      'transaction fee','payment volume','digital wallet','payment network',
      'buy now, pay later','bnpl','installment','pay-in-four'], 'fintech', 2),
    (['same-store sales','comparable store sales','retail stores','e-commerce',
      'consumer products','brand portfolio','store count','comp sales'], 'consumer', 2),
    (['data center','cloud computing','hyperscal','cloud services','cloud revenue',
      'ai infrastructure','gpu','ai accelerat','optical transceiver',
      'datacenter','networking segment',
      'advertising revenue','ad impressions','daily active','monthly active people',
      'family of apps','reality labs','metaverse','instagram','whatsapp',
      'ad targeting','average price per ad','ad revenue',
      'youtube','google cloud','search revenue','generative ai',
      'azure','aws','amazon web services'], 'hyperscaler', 1.5),
    (['semiconductor','wafer','fabrication','backlog','defense contract',
      'industrial equipment','manufacturing capacity','silicon carbide',
      'optoelectronic','photonics','laser'], 'industrial', 1.5),
]

SECTOR_NAMES = {
    'bdc':'BDC / CEF','aero_defense':'Aerospace & Defense','data_analytics':'Data / Analytics / Ratings',
    'hyperscaler':'Mega-Cap / Hyperscaler','saas_tech':'SaaS / Tech','pharma':'Pharma / Biotech',
    'bank':'Bank','specialty_lender':'Specialty Lender / Mortgage REIT','ep':'E&P (Oil & Gas)','midstream':'Midstream','utility':'Utility',
    'consumer':'Consumer / Retail','industrial':'Industrial / Semis','fintech':'Fintech / Payments','payment_network':'Payment Network',
    'insurance':'Insurance / Managed Care','telecom':'Telecom / Media','general':'General',
}

def detect_sector(full_text, ticker=None):
    # ── Ticker override: authoritative, skip text detection entirely ──
    if ticker:
        t = ticker.upper().strip()
        if t in TICKER_SECTOR_OVERRIDE:
            return TICKER_SECTOR_OVERRIDE[t], 'high'

    ft = full_text.lower()
    scores = {}
    for keywords, sector, weight in SECTOR_RULES:
        hits = 0
        for kw in keywords:
            count = min(len(re.findall(re.escape(kw), ft, re.IGNORECASE)), 10)
            if count: hits += count
        if hits: scores[sector] = scores.get(sector, 0) + hits * weight

    # Bank boost: only if genuinely a bank (not a payment network, fintech, or ad-tech company)
    if 'net interest income' in ft:
        # Disqualifiers: terms that indicate the company is NOT a bank
        non_bank_signals = [
            'payment network', 'card network', 'visa inc', 'visa u.s.a.', 'mastercard',
            'credential', 'acceptance network', 'scheme fee', 'four-party model',
            'payment volume', 'payments volume', 'processed transactions',
            'advertising revenue', 'ad impressions', 'daily active', 'monthly active',
            'family of apps', 'reality labs', 'instagram', 'whatsapp', 'youtube',
            'subscription revenue', 'annual recurring revenue', 'saas',
            'gross payment volume', 'payment facilitator', 'digital wallet',
            'buy now, pay later', 'bnpl',
        ]
        is_non_bank = any(kw in ft for kw in non_bank_signals)
        if not is_non_bank:
            # Additional check: if bank keywords are sparse relative to other sectors, skip boost
            bank_core_hits = sum(1 for kw in ['tier 1 capital', 'net interest margin',
                                                'provision for credit loss', 'loan loss',
                                                'nonperforming', 'allowance for loan',
                                                'deposits'] if kw in ft)
            if bank_core_hits >= 2:
                scores['bank'] = scores.get('bank', 0) + 50
    best, best_score = 'general', 0
    for s, sc in scores.items():
        if sc > best_score: best, best_score = s, sc
    if best_score < 5: return 'general', 'low'
    return best, 'high' if best_score > 20 else 'medium'

LABELS = {
    'rev': ['total revenue','total revenues','net revenue','total net revenue','net sales',
            'total net sales','revenues','total net revenues','net interest income','revenue'],
    'ni': ['net income','net earnings','net income (loss)','net income attributable','net loss','net loss attributable'],
    'oi': ['operating income','income from operations','operating profit','income / (loss) from operations','income (loss) from operations','loss from operations'],
    'gp': ['gross profit'],
    'ta': ['total assets'],
    'cash': ['cash and cash equivalents','cash and equivalents','cash, cash equivalents'],
    'debt': ['long-term debt','long term debt','total long-term debt','senior notes','long-term borrowings','total debt','debt, noncurrent','debt noncurrent','long-term notes','notes payable, noncurrent','line of credit'],
    'convertible_debt': ['convertible notes','convertible debt','convertible senior notes'],
    'tl': ['total liabilities'],
    'eq': ['total stockholders','total shareholders','total equity',"total stockholders' equity","total shareholders' equity","total shareowners' equity"],
    'ocf': ['net cash provided by operating','net cash provided from operating','net cash from operating','cash provided by operating activities','net cash used in operating','net cash provided by (used in) operating','net cash provided from (used for) operating','cash flows from operating','net cash generated from operating','cash generated from operating','cash from operating activities','net cash flows provided by','net cash flows provided by (used in) operating','net cash flows from operating'],
    'capex': ['capital expenditure','purchases of property','purchase of property','additions to property','expenditures for property','purchases of property and equipment','acquisition of property','property and equipment additions','capital spending','purchases of intangible assets','capitalized internal-use software','capitalized software','purchases related to property','property and equipment and intangible'],
    'da': ['depreciation and amortization','depreciation'],
    'amort_intangible': ['amortization of intangible','amortization of acquired','amortization of other intangible','amortization of purchased'],
    'amortization': ['amortization'],
    'sbc': ['share-based compensation','stock-based compensation','share based compensation','stock based compensation','equity based compensation','equity-based compensation'],
    'inventory_change': ['inventories','increase in inventories','decrease in inventories','change in inventories'],
    'provision_credit_losses': ['provision for credit losses','provision for loan losses','credit loss provision','allowance for credit losses','provision for credit loss'],
    'ar_change': ['accounts receivable','trade receivables'],
    'shares_dil': ['diluted weighted average','weighted average common shares','shares used in computation of earnings per share','weighted-average shares used','diluted weighted average commo','weighted average number of shares','weighted-average diluted shares','weighted average diluted shares','total weighted-average diluted','average diluted shares','diluted shares outstanding','diluted average common','diluted shares'],
    'loan_originations': ['purchases and originations of notes receivable','originations of notes receivable','purchases of notes receivable','originations of loans','loans originated','net loan originations','consumer loans originated','notes receivable originated','purchases of loans','loans receivable originated','originations of finance receivables','purchases and originations of finance receivables','increase in notes receivable','net change in notes receivable'],
}

def find_value(rows, labels, col_idx=0, exclude=None):
    for row in rows:
        if not row: continue
        row_text = ' '.join(str(c or '') for c in row).lower()
        if exclude and any(x in row_text for x in exclude): continue
        for label in labels:
            if label.lower() in row_text:
                merged = []; i = 0
                while i < len(row):
                    s = str(row[i] or '').strip()
                    if s == '$' and i+1 < len(row):
                        nxt = str(row[i+1] or '').strip()
                        if nxt and nxt not in ('','$'): merged.append('$'+nxt); i += 2; continue
                    elif s == '(' and i+1 < len(row):
                        merged.append('('+str(row[i+1] or '').strip()); i += 2; continue
                    merged.append(s); i += 1
                # Identify which merged cell is the label (first cell with the matching label text)
                # and strip embedded dollar amounts from it to avoid VIE/parenthetical contamination
                # e.g. 'Cash and cash equivalents, including amounts held by VIE of $ 25,921 and $ 30,899'
                label_idx = -1
                for mi, mc in enumerate(merged):
                    if label.lower() in mc.lower():
                        label_idx = mi; break
                if label_idx >= 0:
                    # Remove embedded "$X,XXX" patterns and standalone numbers from the label cell
                    cleaned_label = re.sub(r'\$\s*[\d,]+', '', merged[label_idx])
                    cleaned_label = re.sub(r'\b\d{1,3}(?:,\d{3})+\b', '', cleaned_label)
                    merged[label_idx] = cleaned_label
                nums = [n for n in (clean_num(c) for c in merged) if n is not None]
                if len(nums) > col_idx: return nums[col_idx]
    return None

def detect_period(table, full_text, is_10q):
    """Returns (col_index, annualization_factor) for the best column to extract.
    
    Handles: 10-K multi-year tables (pick most recent year),
             10-Q multi-period tables (prefer longest period, current year),
             10-Q CF tables with Twelve Months Ended (use TTM, ann=1).
    """
    # Scan headers for period and year info
    has_three = has_nine = has_six = has_twelve = False
    years_found = []
    for row in table[:5]:
        rt = ' '.join(str(c or '') for c in row).lower()
        if 'three month' in rt: has_three = True
        if 'nine month' in rt: has_nine = True
        if 'six month' in rt: has_six = True
        if 'twelve month' in rt or 'year ended' in rt: has_twelve = True
        for c in row:
            cs = str(c or '')
            cl = cs.lower()
            # Skip cells that are part of "% Change" or "vs." comparison headers
            if any(x in cl for x in ['change', 'vs.', 'vs ', 'variance', 'growth']): continue
            m = re.search(r'(20[1-3]\d)', cs)
            if m: years_found.append(int(m.group(1)))

    # Count numeric columns in data rows to understand table layout
    num_cols = 0
    for row in table[2:8]:
        merged = []; i = 0
        while i < len(row):
            s = str(row[i] or '').strip()
            if s == '$' and i+1 < len(row): merged.append('$'+str(row[i+1] or '').strip()); i += 2
            else: merged.append(s); i += 1
        nc = sum(1 for c in merged if clean_num(c) is not None)
        if nc > num_cols: num_cols = nc

    # ── 10-K: always ann=1, pick most recent year ──
    if not is_10q:
        if num_cols >= 3 and len(years_found) >= 2:
            # Multi-year table: [old, ..., newest]. Find col with max year.
            max_year = max(years_found)
            # The last occurrence of max_year tells us which column group it's in
            # For 3-year table [2023, 2024, 2025]: years_found=[2023,2024,2025], max=2025, index=2
            last_idx = len(years_found) - 1 - years_found[::-1].index(max_year)
            return last_idx, 1
        if num_cols >= 2 and len(years_found) >= 2:
            max_year = max(years_found)
            last_idx = len(years_found) - 1 - years_found[::-1].index(max_year)
            return last_idx, 1
        return 0, 1

    # ── 10-Q: find the best period column ──
    
    # Special case: table has "Twelve Months Ended" (AMZN CF-style TTM)
    # Prefer this over 9M annualized since it's actual trailing-twelve-month data
    if has_twelve and num_cols >= 6:
        # Layout likely: [3M old, 3M new, 9M old, 9M new, 12M old, 12M new]
        # Find the 12M current-year column
        if len(years_found) >= 6:
            # Last two years are the 12M pair
            if years_found[-1] >= years_found[-2]:
                return len(years_found) - 1, 1  # Last col, no annualization
            else:
                return len(years_found) - 2, 1
        # Fallback: last column
        return num_cols - 1, 1

    # Multi-period table (three+nine or three+six)
    if has_three and (has_nine or has_six):
        ann = 4/3 if has_nine else 2
        if len(years_found) >= 4:
            # Determine which YTD column has the current year
            # YTD columns start at index 2 (after the two Q columns)
            if years_found[2] >= years_found[3]:
                return 2, ann
            else:
                return 3, ann
        return 2, ann

    # Table with 4+ numeric columns but no clear period headers
    if num_cols >= 4:
        ann = 4/3 if has_nine else 2 if has_six else 4/3
        if len(years_found) >= 4:
            if years_found[2] >= years_found[3]: return 2, ann
            else: return 3, ann
        if not (has_nine or has_six):
            ft = full_text.lower()
            if any(x in ft for x in ['nine months','nine-month','9 months ended']): ann = 4/3
            elif any(x in ft for x in ['six months','six-month','6 months ended']): ann = 2
        return 2, ann

    # 2-column table: single period
    if num_cols >= 2:
        if has_nine: return 0, 4/3
        if has_six: return 0, 2
        if has_three: return 0, 4
        ft = full_text.lower()
        if any(x in ft for x in ['nine months','nine-month','9 months ended']): return 0, 4/3
        if any(x in ft for x in ['six months','six-month','6 months ended']): return 0, 2
        return 0, 4

    # Fallback
    if has_nine: return 0, 4/3
    if has_six: return 0, 2
    if has_three: return 0, 4
    return 0, 4

def extract_financials(all_tables, scale, is_10q, full_text, table_scales=None, table_contexts=None):
    r = {}

    # ── BDC / Investment Company Detection & Parsing ──
    # BDCs have completely different financial statements:
    # - "Total investment income" instead of "Revenue"
    # - "Net investment income" instead of "Net income"
    # - "Net assets" / "NAV" instead of "Stockholders' equity"
    # - No cash flow statement (OCF/CapEx don't apply)
    #
    # CRITICAL: "net asset value per share" also appears in pension/fair-value
    # disclosures of normal companies (Level 3 hierarchy). Require multiple
    # BDC-specific signals to avoid false positives.
    ft = full_text.lower()
    bdc_signals = 0
    if 'business development company' in ft: bdc_signals += 3
    if 'total investment income' in ft and 'net investment income' in ft: bdc_signals += 2
    if 'net increase in net assets' in ft and 'net investment income' in ft: bdc_signals += 2
    if 'regulated investment company' in ft: bdc_signals += 2
    if 'subchapter m' in ft: bdc_signals += 2
    # "net asset value per share" alone is NOT sufficient — normal companies
    # mention it in pension/fair-value disclosures. Only count if other signals present.
    if 'net asset value per share' in ft and bdc_signals >= 1: bdc_signals += 1
    is_bdc = bdc_signals >= 3

    if is_bdc:
        for table in all_tables:
            if not table or len(table) < 3: continue
            tt = ' '.join(str(c or '') for row in table for c in row).lower()
            ts = detect_scale(tt)
            # BDC filings often report in raw dollars (no scale indicator)
            eff = ts if ts != _SCALE_DEFAULT else (scale if scale != _SCALE_DEFAULT else 1e3)
            # Check if values look like raw dollars — ONLY if no explicit scale in table
            if not _scale_is_explicit(tt) and eff >= 1000:
                big_count = 0
                for row in table[1:10]:
                    for c in row:
                        digits = re.sub(r'[^0-9]', '', str(c or ''))
                        if len(digits) >= 7: big_count += 1
                if big_count >= 2: eff = 1

            # Statement of Operations
            if 'total investment income' in tt and 'net investment income' in tt:
                col, ann = detect_period(table, full_text, is_10q)
                # Total investment income → maps to "revenue"
                v = find_value(table, ['total investment income'], col)
                if v is not None and 'revenue' not in r:
                    r['revenue'] = v * eff * ann
                    # Prior period
                    vp = find_value(table, ['total investment income'], col + 1)
                    if vp and vp > 0:
                        r['revenue_prior'] = vp * eff * ann
                # Net investment income → maps to "operating_income" and "net_income"
                v = find_value(table, ['net investment income'], col)
                if v is not None:
                    if 'operating_income' not in r: r['operating_income'] = v * eff * ann
                    if 'net_income' not in r: r['net_income'] = v * eff * ann
                # Net increase in net assets (true bottom line)
                v = find_value(table, ['net increase in net assets resulting from operations',
                                       'net increase (decrease) in net assets resulting from operations',
                                       'net increase in net assets',
                                       'net decrease in net assets'], col)
                if v is not None:
                    r['net_income'] = v * eff * ann  # override with true bottom line
                # EPS equivalent
                v = find_value(table, ['net increase in net assets per share',
                                       'net increase (decrease) in net assets resulting from operations',
                                       'net investment income per share'], col)
                if v is not None and abs(v) < 500:
                    r['eps_diluted'] = v * ann if is_10q else v
                # Shares
                v = find_value(table, ['weighted average shares outstanding',
                                       'weighted average shares'], col)
                if v is not None and v > 0:
                    r['shares_diluted'] = v * eff  # BDCs report actual share count

            # Balance Sheet (Statement of Assets & Liabilities)
            if 'total assets' in tt and ('net assets' in tt or 'total net assets' in tt):
                bs_col, _ = detect_period(table, full_text, is_10q)
                v = find_value(table, ['total assets'], bs_col)
                if v is not None: r['total_assets'] = v * eff
                v = find_value(table, ['total liabilities'], bs_col)
                if v is not None: r['total_liabilities'] = v * eff
                v = find_value(table, ['total net assets','net assets'], bs_col)
                if v is not None: r['stockholders_equity'] = v * eff
                v = find_value(table, ['cash and cash equivalents'], bs_col)
                if v is not None: r['cash'] = v * eff
                v = find_value(table, ['net asset value per share'], bs_col)
                if v is not None: r['nav_per_share'] = v
                # Debt: revolving credit, borrowings, notes payable
                for debt_label in ['revolving line of credit','borrowings','credit facility',
                                   'notes payable','total borrowings']:
                    v = find_value(table, [debt_label], bs_col)
                    if v is not None and v > 0:
                        r['long_term_debt'] = r.get('long_term_debt', 0) + v * eff

        # BDC-specific: derive FCF-equivalent (NII is distributable cash)
        if 'net_income' in r:
            r['operating_cf'] = r.get('operating_income', r['net_income'])
            r['capex'] = 0
            r['fcf'] = r['operating_cf']
            r['depreciation'] = 0

        # Mark as BDC
        r['_is_bdc'] = True
        if r.get('revenue'):
            # Don't fall through to standard parsing if BDC parse succeeded
            return r

    # Compute a consensus "filing-level" scale from tables that have explicit context scales
    # This handles cases where the global detect_scale picks up "in millions" from segment notes
    # but the actual financial statements are in thousands ($000)
    # If global scale is the default sentinel, use 1e3 as a safe numeric fallback
    numeric_scale = scale if scale != _SCALE_DEFAULT else 1e3
    consensus_scale = numeric_scale
    if table_scales:
        explicit_scales = [s for s in table_scales if s is not None]
        if explicit_scales:
            # Use the most common explicit scale as the filing's true scale
            from collections import Counter
            sc_counts = Counter(explicit_scales)
            consensus_scale = sc_counts.most_common(1)[0][0]

    def is_likely_IS(table, tt):
        """Check if table is a real income statement, not a CF table with 'unearned revenue'."""
        # Exclude CF tables: they have "operating activities" + "depreciation"/"investing"
        is_cf = (any(x in tt for x in ['operating activities','cash flows from'])
                 and any(x in tt for x in ['depreciation','investing activities','financing activities']))
        if is_cf: return False
        if any(x in tt for x in ['percentage of revenue','% of revenue','period-to-period change',
                                   'period-to-period percentage','segment revenue']): return False
        # Exclude common-size (percentage-of-revenue) tables:
        # These show Revenue=100.0% and all other lines as percentages.
        # Detection: revenue row value is exactly 100.0 and most numeric values are < 200
        # (real IS tables have values in thousands/millions)
        pct_signals = 0
        for row in table:
            if not row: continue
            rt = ' '.join(str(c or '') for c in row).lower()
            if any(x in rt for x in ['revenue','revenues','net sales']) and \
               not any(x in rt for x in ['cost of revenue','unearned','deferred']):
                # Check if the revenue value is ~100 (common-size indicator)
                for c in row:
                    s = str(c or '').strip().replace('%','').replace(',','').strip()
                    try:
                        v = float(s)
                        if 99.5 <= v <= 100.5:
                            pct_signals += 2  # Revenue = 100% is strong signal
                    except (ValueError, TypeError):
                        pass
            # Check for % symbols in data cells (not header)
            for c in row:
                s = str(c or '').strip()
                if s.endswith('%') and any(ch.isdigit() for ch in s):
                    pct_signals += 1
        # If we see Revenue=100% and multiple % signs, it's a common-size table
        if pct_signals >= 3:
            return False
        # Require a standalone revenue-like row label (not just 'unearned revenue' or 'deferred revenue')
        for row in table:
            if not row: continue
            # Get the label cell (first non-empty, non-dollar cell)
            label = ''
            for c in row:
                s = str(c or '').strip()
                if s and s != '$' and not s.replace(',','').replace('.','').replace('-','').replace('(','').replace(')','').isdigit():
                    label = s.lower(); break
            # Match against actual revenue labels
            if any(x in label for x in ['total revenue','total net revenue','net sales','total net sales',
                                         'revenues','net interest income','total revenues','revenue',
                                         'earned premiums']):
                if any(x in label for x in ['unearned','deferred','recognition','contract',
                                             'cost of revenue','other revenue','other revenues']): continue
                return True
        return False

    best_is, best_ann = None, 99
    best_rev = 0  # Track largest revenue to prefer consolidated over segment tables
    best_rows = 0  # Track row count as quality signal
    best_quality = -1  # Quality score for table selection
    for table in all_tables:
        if not table or len(table) < 3: continue
        tt = ' '.join(str(c or '') for row in table for c in row).lower()
        if is_likely_IS(table, tt) and ('net income' in tt or 'net earnings' in tt or 'net loss' in tt):
            _, ann = detect_period(table, full_text, is_10q)
            # Get the largest revenue-like value in this table as a quality signal
            max_rev = 0
            for row in table:
                rt = ' '.join(str(c or '') for c in row).lower()
                if any(x in rt for x in ['total revenue','revenues','earned premium','net sales','net interest income','revenue']):
                    for c in row:
                        v = clean_num(str(c or ''))
                        if v is not None and v > max_rev:
                            max_rev = v
            nrows = len(table)
            
            # ── Quality score: prefer formal consolidated IS over summary/segment ──
            # Formal consolidated IS has: title row, clean column layout, many line items
            # Summary tables have: "Summary", "Highlights", Change/Variance columns
            quality = 0
            header_text = ' '.join(str(c or '') for c in table[0]).lower() if table else ''
            header2_text = ' '.join(str(c or '') for c in table[1]).lower() if len(table) > 1 else ''
            header3_text = ' '.join(str(c or '') for c in table[2]).lower() if len(table) > 2 else ''
            early_text = header_text + ' ' + header2_text + ' ' + header3_text
            # Bonus for formal statement titles (check first 3 rows)
            if any(x in early_text for x in ['consolidated statement of income',
                                               'consolidated statements of income',
                                               'consolidated statement of operations',
                                               'consolidated statements of operations',
                                               'consolidated statement of earnings']):
                quality += 50
            # Penalty for summary/highlight/selected data tables
            if any(x in early_text for x in ['summary','highlight','selected','overview',
                                               'financial performance','key metrics',
                                               'financial highlights']):
                quality -= 30
            # Penalty for tables with Change/Variance columns (messy layouts)
            # Check header rows specifically for Change indicators
            if any(x in early_text for x in ['$ change','% change','variance',
                                               'period-over-period']):
                quality -= 25
            # Bare 'change' in header row (JPM-style: just "Change" as column header)
            # Only penalize if it appears as a standalone column header, not in a phrase
            for hrow in [table[0], table[1] if len(table) > 1 else [], table[2] if len(table) > 2 else []]:
                for cell in hrow:
                    cs = str(cell or '').strip().lower()
                    if cs in ('change', 'changes', 'chg', 'chg.', 'yoy', 'y/y', 'qoq', 'q/q'):
                        quality -= 25
                        break
            # Penalty for segment tables (segment-level, not consolidated)
            # Check both early text and full table text for segment markers
            if any(x in tt[:300] for x in ['consumer banking','commercial banking',
                                             'corporate and investment','wealth and investment',
                                             'segment results','reportable segment',
                                             'consumer & community','asset & wealth',
                                             'line of business','managed basis',
                                             'fully taxable-equivalent']):
                quality -= 40
            # Additional segment detection: "Segment Net income" or "Segment revenue" anywhere
            if re.search(r'segment\s+(?:net\s+)?(?:income|loss|revenue|earnings|profit)', tt):
                quality -= 40
            # Bonus for EPS presence (strong signal this is the real consolidated IS)
            has_eps_data = False
            for row in table:
                rt = ' '.join(str(c or '') for c in row).lower()
                if any(x in rt for x in ['per share', 'earnings per', 'income per', 'loss per']):
                    has_eps_data = True
                    break
            if has_eps_data:
                quality += 30
            # Bonus for diluted shares (another strong signal for real IS)
            if any('diluted' in ' '.join(str(c or '') for c in row).lower() 
                   and any(str(c or '').replace(',','').replace(' ','').isdigit() and len(str(c or '').replace(',','').replace(' ','')) >= 6 
                           for c in row) 
                   for row in table):
                quality += 10
            # Bonus for more rows (comprehensive IS)
            quality += min(nrows, 50)
            # Bonus for larger revenue (consolidated > segment)
            if max_rev > 0:
                quality += min(max_rev / 1000, 30)  # up to 30 pts for large revenue
            
            # Selection: prefer quality, then annualization, then revenue
            is_better = False
            if quality > best_quality + 10:  # clearly better quality
                is_better = True
            elif quality >= best_quality - 5:  # similar quality
                if is_10q:
                    if ann < best_ann or (ann == best_ann and max_rev > best_rev * 1.5):
                        is_better = True
                else:
                    if nrows > best_rows * 1.5:
                        is_better = True
                    elif nrows > best_rows and max_rev > best_rev:
                        is_better = True
                    elif max_rev > best_rev * 2 and nrows >= best_rows * 0.7:
                        is_better = True
            if best_is is None:
                is_better = True
            if is_better:
                best_is, best_ann, best_rev, best_rows, best_quality = table, ann, max_rev, nrows, quality

    for tidx, table in enumerate(all_tables):
        if not table or len(table) < 3: continue
        tt = ' '.join(str(c or '') for row in table for c in row).lower()
        ts = detect_scale(tt)
        eff = ts if ts != _SCALE_DEFAULT else consensus_scale

        # If the table itself has no explicit scale (ts==1000 default),
        # check the per-table context scale from surrounding HTML elements
        has_explicit_context = False
        if ts == _SCALE_DEFAULT and table_scales and tidx < len(table_scales):
            ctx_s = table_scales[tidx]
            if ctx_s is not None:
                eff = ctx_s
                has_explicit_context = True

        # Heuristic: if table has no explicit scale indicator (ts==1000 default)
        # and NO explicit context scale from surrounding HTML,
        # and multiple data values have 7+ digits, they're likely raw dollars (scale=1)
        if ts == _SCALE_DEFAULT and eff >= 1000 and not has_explicit_context:
            big_count = 0
            for row in table[1:10]:
                for c in row:
                    digits = re.sub(r'[^0-9]', '', str(c or ''))
                    if len(digits) >= 7: big_count += 1
            if big_count >= 2: eff = 1

        has_rev = is_likely_IS(table, tt)
        has_ni = 'net income' in tt or 'net earnings' in tt or 'net loss' in tt

        if has_rev and has_ni:
            if best_is is not None and table is not best_is: continue
            col, ann = detect_period(table, full_text, is_10q)

            for fld, lbl in [('revenue',LABELS['rev']),('operating_income',LABELS['oi']),('net_income',LABELS['ni']),('gross_profit',LABELS['gp']),('provision_credit_losses',LABELS['provision_credit_losses'])]:
                if fld not in r:
                    # Revenue: exclude 'other revenue' rows that match the broad 'revenues' label
                    excl = ['other revenue','other revenues','non-operating','unearned','deferred'] if fld == 'revenue' else None
                    v = find_value(table, lbl, col, exclude=excl)
                    if v is None and col > 0:
                        v = find_value(table, lbl, 0, exclude=excl)
                        if v is not None: r[fld] = v * eff * (4 if is_10q else 1); continue
                    if v is not None: r[fld] = v * eff * ann
            if 'revenue_prior' not in r and 'revenue' in r:
                rev_excl = ['other revenue','other revenues','non-operating','unearned','deferred']
                # 10-K: prior year is to the LEFT (col-1); 10-Q: prior period to the RIGHT (col+1)
                if not is_10q and col > 0:
                    vp = find_value(table, LABELS['rev'], col - 1, exclude=rev_excl)
                else:
                    vp = find_value(table, LABELS['rev'], col + 1, exclude=rev_excl)
                # Sanity: prior should be same order of magnitude as current
                if vp is not None and vp > 0:
                    cur = r['revenue'] / eff / ann if ann else r['revenue']
                    if 0.1 < (vp / cur) < 10:
                        r['revenue_prior'] = vp * eff * ann
            if 'eps_diluted' not in r:
                v = find_value(table, ['diluted earnings per','diluted net income per','earnings per common share','diluted loss per','basic and diluted loss per','net loss per'], col)
                if v is None:
                    # Fallback: search for 'diluted' but verify it's EPS not share count
                    # Look for rows with '$' sign near the 'diluted' label
                    for ridx, row in enumerate(table):
                        if not row: continue
                        rt = ' '.join(str(c or '') for c in row).lower()
                        if 'diluted' not in rt: continue
                        # Skip if this is a share count row (no $ sign, or "shares" in label)
                        if 'shares' in rt or 'weighted' in rt: continue
                        raw = ' '.join(str(c or '') for c in row)
                        if '$' not in raw:
                            # Multi-class header: "Diluted Earnings Per Share" with no numbers
                            # Scan NEXT rows for per-class EPS values (e.g. "Class A common stock $3.03")
                            if any(x in rt for x in ['earnings per', 'income per', 'loss per']):
                                for nrow in table[ridx+1:ridx+6]:
                                    if not nrow: continue
                                    nrt = ' '.join(str(c or '') for c in nrow).lower()
                                    nraw = ' '.join(str(c or '') for c in nrow)
                                    if '$' not in nraw: continue
                                    # Skip rows about shares
                                    if 'shares' in nrt or 'weighted' in nrt: continue
                                    # Found a per-class EPS row
                                    merged = []; i = 0
                                    while i < len(nrow):
                                        s = str(nrow[i] or '').strip()
                                        if s == '$' and i+1 < len(nrow): merged.append('$'+str(nrow[i+1] or '').strip()); i += 2
                                        else: merged.append(s); i += 1
                                    nums = [n for n in (clean_num(c) for c in merged) if n is not None]
                                    if len(nums) > col and abs(nums[col]) < 500:
                                        v = nums[col]; break
                                    elif nums and abs(nums[0]) < 500:
                                        v = nums[0]; break
                            if v is not None: break
                            continue
                        # This row has 'diluted' + '$' → likely EPS
                        merged = []; i = 0
                        while i < len(row):
                            s = str(row[i] or '').strip()
                            if s == '$' and i+1 < len(row): merged.append('$'+str(row[i+1] or '').strip()); i += 2
                            else: merged.append(s); i += 1
                        nums = [n for n in (clean_num(c) for c in merged) if n is not None]
                        if len(nums) > col and abs(nums[col]) < 500:
                            v = nums[col]; break
                        elif nums and abs(nums[0]) < 500:
                            v = nums[0]; break
                if v is not None and abs(v) < 500: r['eps_diluted'] = v * ann if is_10q else v

            # Extract prior-period EPS for earnings growth divergence detection
            if 'eps_diluted_prior' not in r and 'eps_diluted' in r:
                # Prior EPS is in the next column (10-Q: col+1, 10-K: col-1)
                prior_col = col - 1 if not is_10q and col > 0 else col + 1
                vp = find_value(table, ['diluted earnings per','diluted net income per',
                                         'earnings per common share','diluted loss per'], prior_col)
                if vp is not None and abs(vp) < 500 and abs(vp) > 0.01:
                    r['eps_diluted_prior'] = vp * ann if is_10q else vp

            # Extract diluted shares (in the scale of the table, typically millions)
            if 'shares_diluted' not in r:
                v = find_value(table, LABELS['shares_dil'], col)
                if v is None and col > 0:
                    v = find_value(table, LABELS['shares_dil'], 0)
                # Multi-class fallback: if header matched but no numbers,
                # scan next rows for per-class share counts (take LARGEST = primary class)
                if v is None:
                    for ridx, row in enumerate(table):
                        if not row: continue
                        rt = ' '.join(str(c or '') for c in row).lower()
                        if any(lbl in rt for lbl in ['diluted weighted average','weighted average common shares',
                                                      'diluted shares outstanding','diluted shares']):
                            # Check if this header row has numbers itself
                            merged = []; i = 0
                            while i < len(row):
                                s = str(row[i] or '').strip()
                                if s == '$' and i+1 < len(row): merged.append('$'+str(row[i+1] or '').strip()); i += 2
                                else: merged.append(s); i += 1
                            nums = [n for n in (clean_num(c) for c in merged) if n is not None]
                            if nums and nums[0] > 0:
                                break  # Header has numbers, normal extraction should work
                            # Header has no numbers — scan next rows for per-class values
                            best_shares_val = 0
                            for nrow in table[ridx+1:ridx+8]:
                                if not nrow: continue
                                nrt = ' '.join(str(c or '') for c in nrow).lower()
                                # Stop at next section header
                                if any(x in nrt for x in ['basic','earnings per','income per','loss per','total']): break
                                nmerged = []; ni = 0
                                while ni < len(nrow):
                                    s = str(nrow[ni] or '').strip()
                                    if s == '(' and ni+1 < len(nrow): nmerged.append('('+str(nrow[ni+1] or '').strip()); ni += 2
                                    else: nmerged.append(s); ni += 1
                                nnums = [n for n in (clean_num(c) for c in nmerged) if n is not None and n > 0]
                                if nnums:
                                    candidate = nnums[col] if len(nnums) > col else nnums[0]
                                    if candidate > best_shares_val:
                                        best_shares_val = candidate
                            if best_shares_val > 0:
                                v = best_shares_val
                            break
                if v is not None and v > 0:
                    # Shares may have a DIFFERENT scale than financials.
                    # Use centralized resolver that checks table headers, context, and full text
                    header_text = ''
                    for hrow_idx in range(min(3, len(table))):
                        header_text += ' ' + ' '.join(str(c or '') for c in table[hrow_idx]).lower()
                    ctx_text = table_contexts[tidx] if table_contexts and tidx < len(table_contexts) else ''
                    eff_shares = _resolve_eff_shares(v, eff, header_text, ctx_text, full_text)
                    r['shares_diluted'] = v * eff_shares

        # Balance sheet — handle both combined and split BS tables
        # Combined: single table has both 'total assets' and equity markers
        # Split: one table for assets, another for liabilities+equity (CEG, some utilities)
        is_bs_combined = 'total assets' in tt and any(x in tt for x in ['stockholders','equity','shareholders','shareowners'])
        is_bs_assets = 'total assets' in tt and ('cash and cash' in tt or 'current assets' in tt)
        is_bs_liab_eq = (not ('total assets' in tt)) and any(x in tt for x in ['total equity','total stockholders','total shareholders']) and any(x in tt for x in ['total liabilities','long-term debt'])

        if is_bs_combined or is_bs_assets or is_bs_liab_eq:
            # Skip tables with Change/Variance columns — their column indexing is unreliable
            has_change_cols = False
            for hrow in table[:3]:
                for cell in hrow:
                    cs = str(cell or '').strip().lower()
                    if cs in ('change', 'changes', 'chg', '$ change', '% change', 'yoy', 'y/y', 'qoq'):
                        has_change_cols = True
                        break
            # Also skip "Selected metrics" type tables
            header_lower = ' '.join(str(c or '') for c in table[0]).lower() if table else ''
            is_summary_bs = any(x in header_lower for x in ['selected metrics', 'selected balance', 'key metrics',
                                                              'financial performance', 'financial highlights'])
            
            if has_change_cols or is_summary_bs:
                # Only use this table if it has more BS fields than what we already have
                # AND the values are sensible (total_assets > equity)
                bs_col, _ = detect_period(table, full_text, is_10q)
                ta_v = find_value(table, LABELS['ta'], bs_col)
                eq_v = find_value(table, LABELS['eq'], bs_col)
                if ta_v and eq_v and ta_v * eff > eq_v * eff > 0:
                    pass  # Values look sane, continue normally
                else:
                    # Skip this unreliable table
                    is_bs_combined = is_bs_assets = is_bs_liab_eq = False

        if is_bs_combined or is_bs_assets or is_bs_liab_eq:
            # Use detect_period for column selection (handles 10-K multi-year)
            bs_col, _ = detect_period(table, full_text, is_10q)
            for fld, lbl in [('total_assets',LABELS['ta']),('cash',LABELS['cash']),('long_term_debt',LABELS['debt']),('total_liabilities',LABELS['tl']),('stockholders_equity',LABELS['eq'])]:
                if fld not in r:
                    excl = ['due within','current portion','current maturities'] if fld == 'long_term_debt' else None
                    v = find_value(table, lbl, bs_col, exclude=excl)
                    if v is None and bs_col > 0:
                        v = find_value(table, lbl, 0, exclude=excl)  # fallback
                    if v is not None: r[fld] = v * eff
            # Convertible debt fallback: if no traditional LT debt found, check for convertible
            if 'convertible_debt' not in r:
                v = find_value(table, LABELS['convertible_debt'], bs_col)
                if v is None and bs_col > 0:
                    v = find_value(table, LABELS['convertible_debt'], 0)
                if v is not None and v > 0:
                    r['convertible_debt'] = v * eff

        is_cf = (any(x in tt for x in ['operating activities','cash flows from','cash provided by'])
                 and any(x in tt for x in ['depreciation','investing','capital','financing activities','cash and cash equivalents','property and equipment','net cash']))
        if is_cf:
            # Use detect_period for both column selection and annualization
            cf_col, cf_ann = detect_period(table, full_text, is_10q)
            for fld, lbl in [('operating_cf',LABELS['ocf']),('depreciation',LABELS['da'])]:
                if fld not in r:
                    v = find_value(table, lbl, cf_col)
                    if v is None and cf_col > 0:
                        v = find_value(table, lbl, 0)  # fallback to col 0
                    if v is not None: r[fld] = v * eff * cf_ann

            # Capex: accumulate ALL capex-like investing lines (PP&E + capitalized software)
            # because tech companies often split these into separate CF lines
            if 'capex' not in r:
                capex_labels_primary = ['capital expenditure','purchases of property','purchase of property',
                                        'additions to property','expenditures for property',
                                        'purchases of property and equipment','acquisition of property',
                                        'property and equipment additions','capital spending',
                                        'purchases of intangible assets',
                                        'purchases related to property','property and equipment and intangible']
                capex_labels_software = ['capitalized internal-use software','capitalized software',
                                         'capitalized software development']
                capex_total = 0
                capex_found = False
                for capex_labels in [capex_labels_primary, capex_labels_software]:
                    v = find_value(table, capex_labels, cf_col)
                    if v is None and cf_col > 0:
                        v = find_value(table, capex_labels, 0)
                    if v is not None:
                        capex_total += abs(v)
                        capex_found = True
                if capex_found:
                    r['capex'] = capex_total * eff * cf_ann

            # Extract intangible amortization (separate from D&A combined line)
            # Try specific "amortization of intangible" first, then generic "amortization"
            if 'amort_intangible' not in r:
                v = find_value(table, LABELS['amort_intangible'], cf_col)
                if v is None and cf_col > 0:
                    v = find_value(table, LABELS['amort_intangible'], 0)
                if v is not None and v > 0:
                    r['amort_intangible'] = v * eff * cf_ann
                elif 'amort_standalone' not in r:
                    # Look for standalone "amortization" line (not "depreciation and amortization")
                    for row in table:
                        if not row: continue
                        rt = ' '.join(str(c or '') for c in row).lower()
                        # Match "amortization" but NOT "depreciation and amortization" or "amortization of debt"
                        # Also exclude insurance-specific DAC/deferred cost amortization (real operating expenses)
                        if ('amortization' in rt and 'depreciation' not in rt and 'debt' not in rt 
                            and 'lease' not in rt and 'deferred' not in rt and 'dac' not in rt
                            and 'policy' not in rt and 'prior service' not in rt):
                            merged = []; i = 0
                            while i < len(row):
                                s = str(row[i] or '').strip()
                                if s == '$' and i+1 < len(row): merged.append('$'+str(row[i+1] or '').strip()); i += 2
                                elif s == '(' and i+1 < len(row): merged.append('('+str(row[i+1] or '').strip()); i += 2
                                else: merged.append(s); i += 1
                            nums = [n for n in (clean_num(c) for c in merged) if n is not None]
                            if len(nums) > cf_col and nums[cf_col] > 0:
                                r['amort_intangible'] = nums[cf_col] * eff * cf_ann
                                break
                            elif nums and nums[0] > 0:
                                r['amort_intangible'] = nums[0] * eff * cf_ann
                                break

            # Extract SBC from CF statement
            if 'sbc' not in r:
                v = find_value(table, LABELS['sbc'], cf_col)
                if v is None and cf_col > 0:
                    v = find_value(table, LABELS['sbc'], 0)
                if v is not None and v > 0:
                    r['sbc'] = v * eff * cf_ann

            # Extract inventory change for WC normalization
            if 'inventory_change' not in r:
                v = find_value(table, LABELS['inventory_change'], cf_col)
                if v is None and cf_col > 0:
                    v = find_value(table, LABELS['inventory_change'], 0)
                if v is not None:
                    r['inventory_change'] = v * eff * cf_ann  # negative = build

            # Extract net loan originations from investing section (BNPL/lending companies)
            # These represent the capital deployed to originate consumer loans — functionally
            # equivalent to capex for a lending business.
            if 'loan_originations' not in r:
                v = find_value(table, LABELS['loan_originations'], cf_col)
                if v is None and cf_col > 0:
                    v = find_value(table, LABELS['loan_originations'], 0)
                if v is not None:
                    r['loan_originations'] = abs(v) * eff * cf_ann  # stored as positive

    if 'operating_cf' in r and 'capex' in r: r['fcf'] = r['operating_cf'] - r['capex']
    elif 'operating_cf' in r and 'capex' not in r: r['capex'] = 0; r['fcf'] = r['operating_cf']

    # Merge convertible debt into long_term_debt if traditional LT debt is zero
    if (not r.get('long_term_debt') or r['long_term_debt'] == 0) and r.get('convertible_debt', 0) > 0:
        r['long_term_debt'] = (r.get('long_term_debt') or 0) + r['convertible_debt']

    # Search all tables for diluted shares if not yet found (some filings have a separate EPS table)
    if 'shares_diluted' not in r:
        for tidx2, table in enumerate(all_tables):
            if not table or len(table) < 2: continue
            tt = ' '.join(str(c or '') for row in table for c in row).lower()
            if 'diluted' not in tt or 'share' not in tt: continue
            ts = detect_scale(tt)
            eff = ts if ts != _SCALE_DEFAULT else consensus_scale
            if ts == _SCALE_DEFAULT and table_scales and tidx2 < len(table_scales):
                ctx_s = table_scales[tidx2]
                if ctx_s is not None:
                    eff = ctx_s
                    has_explicit_ctx = True
                else:
                    has_explicit_ctx = False
            else:
                has_explicit_ctx = False
            if ts == _SCALE_DEFAULT and eff >= 1000 and not has_explicit_ctx:
                big_count = 0
                for row in table[1:10]:
                    for c in row:
                        digits = re.sub(r'[^0-9]', '', str(c or ''))
                        if len(digits) >= 7: big_count += 1
                if big_count >= 2: eff = 1
            col, _ = detect_period(table, full_text, is_10q)
            v = find_value(table, LABELS['shares_dil'], col)
            if v is None and col > 0:
                v = find_value(table, LABELS['shares_dil'], 0)
            if v is not None and v > 0:
                # Use centralized resolver that checks table headers, context, and full text
                hdr = ''
                for hrow_idx2 in range(min(3, len(table))):
                    hdr += ' ' + ' '.join(str(c or '') for c in table[hrow_idx2]).lower()
                ctx_text2 = table_contexts[tidx2] if table_contexts and tidx2 < len(table_contexts) else ''
                eff_shares = _resolve_eff_shares(v, eff, hdr, ctx_text2, full_text)
                r['shares_diluted'] = v * eff_shares
                break

    # Fallback: derive shares from net_income / eps
    if 'shares_diluted' not in r and r.get('net_income') and r.get('eps_diluted') and abs(r['eps_diluted']) > 0.01:
        r['shares_diluted'] = abs(r['net_income'] / r['eps_diluted'])

    # Fallback: extract shares from cover page text
    if 'shares_diluted' not in r:
        for m in re.finditer(r'(?:shares?\s+)?outstanding', full_text.lower()):
            start = max(0, m.start() - 250)
            end = min(len(full_text), m.end() + 250)
            window = full_text[start:end].lower()
            if any(x in window for x in ['loan','debt','principal','option','warrant','rsu','preferred','authorized','authorize']): continue
            if not any(x in window for x in ['common','share','class a','class b','class c','registrant']): continue
            for n in re.findall(r'[\d,]{7,}', window):
                val = float(n.replace(',', ''))
                if 1e6 < val < 1e11:
                    r['shares_diluted'] = val
                    break
            if 'shares_diluted' in r: break

    # ── GLOBAL SANITY CHECK: Share count reasonableness ──
    # No public company has more than ~25B shares outstanding (even BRK-B has ~2.1B class B equiv)
    # If shares look impossibly high, likely a scale error — try to correct
    if r.get('shares_diluted', 0) > 50e9:
        # Check if EPS can give us a better number
        if r.get('net_income') and r.get('eps_diluted') and abs(r['eps_diluted']) > 0.01:
            eps_implied = abs(r['net_income'] / r['eps_diluted'])
            if 1e5 < eps_implied < 50e9:
                r['shares_diluted'] = eps_implied
                r['_shares_corrected'] = 'eps_implied'
        else:
            # Divide by the filing scale as a last resort
            corrected = r['shares_diluted']
            for divisor in [1e3, 1e6]:
                candidate = r['shares_diluted'] / divisor
                if 1e5 < candidate < 50e9:
                    corrected = candidate
                    break
            if corrected != r['shares_diluted']:
                r['shares_diluted'] = corrected
                r['_shares_corrected'] = f'divided_by_{int(r["shares_diluted"])}'

    # Also sanity-check: if shares are implausibly LOW (e.g., < 10,000), 
    # they may have been under-scaled
    if 0 < r.get('shares_diluted', 0) < 1e4 and r.get('revenue', 0) > 1e6:
        if r.get('net_income') and r.get('eps_diluted') and abs(r['eps_diluted']) > 0.01:
            eps_implied = abs(r['net_income'] / r['eps_diluted'])
            if eps_implied > 1e5:
                r['shares_diluted'] = eps_implied
                r['_shares_corrected'] = 'eps_implied_low'

    return r

def _get_table_context(table_el):
    """Get text surrounding a <table> element to find scale indicators like ($000) or ($ in millions)."""
    context_parts = []
    # Check preceding siblings of the table and its parent
    for el in [table_el, table_el.parent]:
        if not el: continue
        for sib in list(el.previous_siblings)[:5]:
            t = sib.get_text(strip=True) if hasattr(sib, 'get_text') else str(sib).strip()
            if t:
                context_parts.append(t)
                if len(' '.join(context_parts)) > 300: break
        if len(' '.join(context_parts)) > 300: break
    return ' '.join(context_parts)

def detect_scale_explicit(text):
    """Like detect_scale, but returns None if no explicit indicator found (instead of 1000 default).
    This is used for per-table context detection where we need to distinguish
    'found thousands' from 'found nothing'."""
    t = text.lower()
    # Also match (000 without $ sign — e.g. "(000, except per share data)"
    thou_count = len(re.findall(r'in\s+thousands|in\s+\$\s*000|\(\s*thousands|\(0{3}[,\s)]', t))
    mill_count = len(re.findall(r'in\s+millions|\(\s*millions', t))
    bill_count = len(re.findall(r'in\s+billions|\(\s*billions', t))

    # CRITICAL FIX: Many filings say "(in millions, except shares which are reflected in thousands)"
    # The "thousands" here only applies to share counts, NOT to financial data.
    # If "thousands" appears only in a share-exception context AND "millions" is also present,
    # the financial data scale is millions, not thousands.
    if thou_count > 0 and mill_count > 0:
        # Check if "thousands" only appears near share-related words
        thou_in_shares_context = bool(re.search(
            r'(?:shares?|number\s+of\s+shares?|share\s+data|per\s+share)'
            r'.*?(?:in\s+thousands|thousands)', t))
        if thou_in_shares_context:
            # The thousands reference is about shares, not financials — treat as millions
            return 1e6

    if bill_count > 0 and bill_count >= thou_count and bill_count >= mill_count:
        return 1e9
    if thou_count > 0 and thou_count >= mill_count:
        return 1e3
    if mill_count > 0:
        return 1e6
    patterns = [
        # Thousands patterns — check these BEFORE "except per share"
        (r'\(\s*\$\s*0{3}\s*[s,)]', 1e3), (r'\(\s*\$\s*0{3}\b', 1e3),
        (r'\(\s*0{3}s?\s*\)', 1e3), (r'in\s+\$\s*0{3}', 1e3),
        (r'\(0{3}[,\s]', 1e3),  # Matches "(000," or "(000 " without $ or closing paren
        # Millions patterns
        (r'except\s+per[\s-]+share', 1e6), (r'except\s+share\s+data', 1e6),
    ]
    for pat, sc in patterns:
        if re.search(pat, t): return sc
    return None  # No explicit indicator found

def _shares_are_exempt_from_scale(text):
    """Detect whether share/per-share data is explicitly exempted from the stated scale.
    
    Common patterns in SEC filings:
      - "(in thousands, except for share and per share data)"
      - "(in millions, except share data)"
      - "(in thousands, except per share amounts)"
      - "($ in thousands, except share and per share amounts)"
      - "(in thousands, except for per share information)"
      - "(in millions, except share data in thousands)"  ← shares ARE scaled, but differently
    
    Returns:
      'exempt'  — shares are in raw counts (no scale)
      'thousands' — shares are explicitly in thousands
      'millions' — shares are explicitly in millions  
      None — no explicit share-scale info found
    """
    t = text.lower()
    
    # Pattern: "except share data in thousands" / "shares in thousands" → shares × 1e3
    if re.search(r'(?:shares?|share\s+data)\s+(?:are\s+)?(?:in|reported\s+in)\s+thousands', t):
        return 'thousands'
    if re.search(r'except\s+(?:for\s+)?share\s+(?:data|amounts?)\s+(?:which\s+(?:are|is)\s+)?in\s+thousands', t):
        return 'thousands'
    
    # Pattern: "shares in millions" → shares × 1e6
    if re.search(r'(?:shares?|share\s+data)\s+(?:are\s+)?(?:in|reported\s+in)\s+millions', t):
        return 'millions'
    
    # Pattern: "except for share and per share data" / "except share data" / "except per share"
    # These mean shares are RAW counts (not scaled)
    if re.search(r'except\s+(?:for\s+)?share\s+(?:and\s+per[\s-]*share\s+)?(?:data|amounts?|information)', t):
        return 'exempt'
    if re.search(r'except\s+(?:for\s+)?per[\s-]*share\s+(?:and\s+share\s+)?(?:data|amounts?|information)', t):
        return 'exempt'
    if re.search(r'except\s+(?:for\s+)?(?:share\s+)?(?:and\s+)?per[\s-]*share', t):
        return 'exempt'
    if re.search(r'except\s+share\s+data', t):
        return 'exempt'
    
    return None


def _resolve_eff_shares(raw_value, eff, header_text, context_text, full_text):
    """Determine the correct scale to apply to a raw share count value.
    
    This centralizes all share-scaling logic to avoid duplication and ensure
    consistent handling across all extraction paths.
    
    Args:
        raw_value: The raw numeric value extracted from the table
        eff: The effective financial scale for the table (e.g., 1e3, 1e6)
        header_text: Concatenated text from the table's first 3 rows (lowercase)
        context_text: Text surrounding the table element (e.g., title, preceding divs)
        full_text: Full filing text (for global fallback patterns)
    
    Returns:
        The correct multiplier to apply to raw_value to get actual share count
    """
    # Check all available text sources for share-scale info
    for text_source in [header_text, context_text, full_text[:5000]]:
        if not text_source:
            continue
        exempt = _shares_are_exempt_from_scale(text_source)
        if exempt == 'exempt':
            return 1  # Shares are raw counts
        elif exempt == 'thousands':
            return 1e3
        elif exempt == 'millions':
            return 1e6
    
    # Explicit share scale patterns in header (existing logic, kept as fallback)
    if re.search(r'shares?\s+in\s+thousands|except\s+share.*thousands', header_text):
        return 1e3
    if re.search(r'shares?\s+in\s+millions', header_text):
        return 1e6
    
    # Heuristic: if share value looks like a raw count already
    # (large number with many digits), don't scale it further
    if raw_value > 1e6 and eff >= 1e3:
        # 181,165,738 × 1e3 = 181B shares → clearly wrong
        # 181,165,738 × 1 = 181M shares → correct
        # Rule: if raw value > 1M AND scaling would give > 50B shares, it's raw
        scaled = raw_value * eff
        if scaled > 50e9:  # No public company has 50B+ shares
            return 1
    
    if eff >= 1e6 and raw_value > 50000 and raw_value < 1e7:
        # Values like 269,700 are likely shares in thousands (269.7M)
        return 1e3
    
    if eff >= 1e6 and raw_value > 0 and raw_value < 100:
        # Values like 0.27 in billions context → 270M shares
        return eff
    
    return eff


def parse_html(filepath, ticker=None):
    with open(filepath, 'r', encoding='utf-8', errors='replace') as f: content = f.read()
    soup = BeautifulSoup(content, 'html.parser')
    full_text = soup.get_text(separator=' ', strip=True)
    scale = detect_scale(full_text)
    form, is_10q = detect_form(full_text, os.path.basename(filepath))
    tables = []
    table_scales = []  # per-table scale from surrounding context (None if no explicit indicator)
    table_contexts = []  # per-table raw context text for share-exemption detection
    for te in soup.find_all('table'):
        rows = []
        for tr in te.find_all('tr'):
            cells = [td.get_text(separator=' ', strip=True) for td in tr.find_all(['td','th'])]
            if any(c for c in cells): rows.append(cells)
        if rows:
            tables.append(rows)
            # Detect scale from context around the table (preceding text like "$000")
            ctx = _get_table_context(te)
            ctx_scale = detect_scale_explicit(ctx) if ctx else None
            table_scales.append(ctx_scale)
            table_contexts.append(ctx or '')
    r = extract_financials(tables, scale, is_10q, full_text, table_scales=table_scales, table_contexts=table_contexts)
    r['_form'] = form; r['_scale'] = scale; r['_tables'] = len(tables)
    sec, conf = detect_sector(full_text, ticker=ticker)
    r['_sector'] = sec; r['_sector_conf'] = conf
    return r

def parse_pdf(filepath, ticker=None):
    tables = []; full_text = ''
    with pdfplumber.open(filepath) as pdf:
        for page in pdf.pages:
            full_text += (page.extract_text() or '') + '\n'
            for t in (page.extract_tables() or []):
                rows = [row for row in t if any(c for c in row)]
                if rows: tables.append(rows)
    scale = detect_scale(full_text)
    form, is_10q = detect_form(full_text, os.path.basename(filepath))
    r = extract_financials(tables, scale, is_10q, full_text)
    r['_form'] = form; r['_scale'] = scale; r['_tables'] = len(tables)
    sec, conf = detect_sector(full_text, ticker=ticker)
    r['_sector'] = sec; r['_sector_conf'] = conf
    return r

# ── DCF Engine ──

BETAS = {
    'hyperscaler': 1.15, 'bank': .90, 'specialty_lender': 1.10, 'ep': 1.20, 'midstream': .85,
    'saas_tech': 1.15, 'pharma': .80, 'fintech': 1.10, 'payment_network': 0.95, 'consumer': .80,
    'industrial': 1.00, 'utility': .55, 'telecom': .80, 'insurance': .95,
    'aero_defense': .85, 'data_analytics': 1.00, 'general': 1.00
}

# (CAPEX_FACTORS defined below, near compute_capex_normalization)

# Target FCF margins for mature companies by sector (used in rev-margin DCF)
TARGET_MARGINS = {
    'hyperscaler':.20,'saas_tech':.22,'pharma':.18,'fintech':.18,'payment_network':.50,'consumer':.10,
    'industrial':.12,'ep':.10,'midstream':.18,'bank':.15,'specialty_lender':.10,'utility':.12,
    'telecom':.14,'insurance':.10,'aero_defense':.09,'data_analytics':.30,'general':.12
}

SCENARIOS = {
    'hyperscaler': [
        ('\U0001f525 Dominance',.10,[.25,.22,.19,.16,.14,.12,.10,.09,.08,.07],30,1.10),
        ('\U0001f4c8 Strong',.25,[.18,.16,.14,.12,.10,.09,.08,.07,.06,.05],24,1.05),
        ('\U0001f4ca Base',.35,[.13,.12,.10,.09,.08,.07,.06,.05,.05,.04],20,1.00),
        ('\U0001f4c9 Maturation',.20,[.06,.05,.05,.04,.04,.03,.03,.03,.03,.03],14,0.90),
        ('\U0001f480 Disruption',.10,[.01,-.02,0,.02,.02,.02,.02,.02,.02,.02],9,0.75)],
    'saas_tech': [
        ('\U0001f525 Hypergrowth',.10,[.28,.24,.20,.17,.14,.11,.09,.08,.07,.06],25,1.15),
        ('\U0001f4c8 Strong',.25,[.18,.16,.14,.12,.10,.08,.07,.06,.05,.05],20,1.05),
        ('\U0001f4ca Base',.35,[.11,.10,.09,.08,.07,.06,.05,.05,.04,.04],15,1.00),
        ('\U0001f4c9 Decel',.20,[.03,.03,.03,.02,.02,.02,.02,.02,.02,.02],10,0.85),
        ('\U0001f480 Disrupt',.10,[-.03,-.05,-.02,.01,.02,.02,.01,.01,.01,.01],6,0.60)],
    'pharma': [
        ('\U0001f525 Pipeline',.10,[.16,.14,.12,.10,.08,.07,.06,.05,.04,.04],16,1.10),
        ('\U0001f4c8 Growth',.25,[.09,.08,.07,.06,.06,.05,.05,.04,.04,.03],13,1.00),
        ('\U0001f4ca Base',.35,[.04,.04,.04,.03,.03,.03,.02,.02,.02,.02],10,1.00),
        ('\U0001f4c9 Patent',.20,[-.01,-.05,-.08,-.03,.01,.02,.02,.01,.01,.01],7,0.80),
        ('\U0001f480 Fail',.10,[-.08,-.15,-.10,-.04,.01,.01,.01,.01,.01,.01],4,0.50)],
    'ep': [
        ('\U0001f525 $85 Oil',.10,[.22,.18,.14,.10,.07,.05,.04,.03,.03,.02],5.0,1.10),
        ('\U0001f4c8 $75 Oil',.25,[.10,.08,.06,.05,.04,.03,.03,.02,.02,.02],4.5,1.00),
        ('\U0001f4ca $65 Oil',.35,[.03,.02,.02,.02,.01,.01,.01,.01,.01,.01],4.0,1.00),
        ('\U0001f4c9 $50 Oil',.20,[-.08,-.12,-.05,.01,.02,.01,.01,.01,.01,.01],3.0,0.70),
        ('\U0001f480 $40 Crash',.10,[-.18,-.22,-.08,-.02,.01,.01,.01,.01,.01,.01],2.5,0.45)],
    'midstream': [
        ('\U0001f525 Boom',.10,[.10,.09,.07,.06,.05,.05,.04,.03,.03,.03],9,1.05),
        ('\U0001f4c8 Steady',.25,[.06,.05,.05,.04,.04,.03,.03,.03,.02,.02],8,1.00),
        ('\U0001f4ca Base',.35,[.03,.03,.02,.02,.02,.02,.02,.02,.02,.02],7,1.00),
        ('\U0001f4c9 Decline',.20,[-.01,-.02,-.01,.01,.01,.01,.01,.01,.01,.01],5,0.85),
        ('\U0001f480 Transition',.10,[-.05,-.07,-.04,-.02,0,0,0,0,0,0],3,0.65)],
    'utility': [
        ('\U0001f525 Rate Growth',.10,[.08,.07,.06,.06,.05,.05,.04,.04,.04,.03],16,1.05),
        ('\U0001f4c8 Constructive',.25,[.05,.05,.05,.04,.04,.04,.03,.03,.03,.03],14,1.00),
        ('\U0001f4ca Base',.35,[.03,.03,.03,.03,.03,.02,.02,.02,.02,.02],12,1.00),
        ('\U0001f4c9 Headwinds',.20,[.01,.01,.02,.02,.02,.02,.02,.02,.02,.02],10,0.90),
        ('\U0001f480 Adverse',.10,[-.01,-.02,0,.01,.01,.01,.01,.01,.01,.01],7,0.75)],
    'specialty_lender': [
        ('\U0001f525 Rate Tailwind',.10,[.16,.17,.18,.18,.18,.17,.16,.16,.15,.15],.16,1.0),
        ('\U0001f4c8 Strong Cycle',.20,[.14,.15,.16,.16,.16,.15,.15,.14,.14,.14],.14,1.0),
        ('\U0001f4ca Base',.35,[.12,.12,.13,.13,.13,.12,.12,.12,.12,.12],.12,1.0),
        ('\U0001f4c9 NIM Compress',.20,[.09,.08,.09,.09,.10,.10,.10,.10,.10,.10],.09,1.0),
        ('\U0001f480 Credit Crisis',.15,[.04,.00,.02,.05,.07,.08,.09,.09,.09,.09],.07,1.0)],
    'bank': [
        ('\U0001f525 ROE Up',.10,[.18,.20,.22,.23,.24,.24,.23,.22,.22,.22],.20,1.0),
        ('\U0001f4c8 Strong',.25,[.15,.16,.17,.18,.18,.18,.17,.17,.17,.17],.16,1.0),
        ('\U0001f4ca Base',.35,[.13,.13,.14,.14,.14,.14,.13,.13,.13,.13],.13,1.0),
        ('\U0001f4c9 NIM Down',.20,[.10,.09,.10,.10,.11,.11,.11,.11,.11,.11],.10,1.0),
        ('\U0001f480 Crisis',.10,[.06,.02,.04,.06,.08,.09,.10,.10,.10,.10],.08,1.0)],
    'insurance': [
        ('\U0001f525 Hard Market',.10,[.06,.04,.02,-.01,-.03,-.02,.01,.02,.03,.03],10,1.05),
        ('\U0001f4c8 Moderate Cycle',.25,[.04,.03,.01,0,-.02,-.01,.01,.02,.02,.02],9,1.00),
        ('\U0001f4ca Base',.35,[.02,.01,0,-.02,-.03,-.01,.01,.02,.02,.02],8,0.95),
        ('\U0001f4c9 Soft Market',.20,[0,-.02,-.04,-.05,-.03,-.01,.01,.01,.01,.01],6,0.80),
        ('\U0001f480 Cat Losses',.10,[-.04,-.08,-.10,-.05,-.02,.01,.02,.02,.01,.01],4,0.55)],
    'fintech': [
        ('\U0001f525 Network Expansion',.10,[.18,.16,.14,.12,.10,.09,.08,.07,.06,.06],22,1.10),
        ('\U0001f4c8 Steady Growth',.25,[.12,.11,.10,.09,.08,.07,.07,.06,.06,.05],18,1.00),
        ('\U0001f4ca Base',.35,[.08,.07,.07,.06,.06,.05,.05,.05,.04,.04],15,1.00),
        ('\U0001f4c9 Compression',.20,[.03,.03,.03,.03,.03,.03,.03,.02,.02,.02],10,0.80),
        ('\U0001f480 Disruption',.10,[-.02,-.04,-.02,.01,.02,.02,.02,.02,.01,.01],6,0.55)],
    'payment_network': [
        ('\U0001f525 Digital Acceleration',.10,[.14,.13,.12,.11,.10,.09,.08,.07,.06,.06],32,1.05),
        ('\U0001f4c8 Secular Growth',.25,[.12,.11,.10,.09,.08,.07,.07,.06,.06,.05],28,1.00),
        ('\U0001f4ca Base',.35,[.10,.09,.08,.07,.07,.06,.06,.05,.05,.05],25,1.00),
        ('\U0001f4c9 Maturation',.20,[.06,.06,.05,.05,.05,.04,.04,.04,.04,.04],18,0.90),
        ('\U0001f480 Disruption',.10,[.00,-.02,.00,.02,.03,.03,.03,.03,.02,.02],10,0.65)],
    'consumer': [
        ('\U0001f525 Share Gains',.10,[.10,.09,.08,.07,.06,.05,.05,.04,.04,.04],16,1.10),
        ('\U0001f4c8 Steady',.25,[.07,.06,.06,.05,.05,.05,.04,.04,.04,.03],14,1.00),
        ('\U0001f4ca Base',.35,[.05,.05,.04,.04,.04,.03,.03,.03,.03,.03],12,1.00),
        ('\U0001f4c9 Recession',.20,[-.01,-.03,0,.02,.03,.03,.03,.02,.02,.02],8,0.80),
        ('\U0001f480 Secular Decline',.10,[-.04,-.06,-.04,-.02,0,.01,.01,.01,.01,.01],5,0.55)],
    'industrial': [
        ('\U0001f525 Cycle Peak',.10,[.20,.15,.10,.08,.06,.05,.04,.04,.03,.03],16,1.10),
        ('\U0001f4c8 Expansion',.25,[.12,.10,.08,.06,.05,.05,.04,.04,.03,.03],13,1.00),
        ('\U0001f4ca Base',.35,[.06,.05,.05,.04,.04,.03,.03,.03,.03,.02],11,1.00),
        ('\U0001f4c9 Downcycle',.20,[-.05,-.10,-.03,.04,.05,.04,.03,.03,.03,.02],8,0.75),
        ('\U0001f480 Deep Recession',.10,[-.12,-.18,-.08,.02,.04,.04,.03,.02,.02,.02],5,0.50)],
    'telecom': [
        ('\U0001f525 5G/Fiber Cycle',.10,[.08,.07,.06,.05,.04,.04,.03,.03,.03,.03],12,1.05),
        ('\U0001f4c8 Steady',.25,[.04,.04,.04,.03,.03,.03,.03,.02,.02,.02],10,1.00),
        ('\U0001f4ca Base',.35,[.02,.02,.02,.02,.02,.02,.02,.02,.02,.02],8,1.00),
        ('\U0001f4c9 Cord-Cutting',.20,[-.01,-.02,-.01,.01,.01,.01,.01,.01,.01,.01],6,0.85),
        ('\U0001f480 Legacy Decline',.10,[-.04,-.06,-.04,-.02,-.01,0,0,0,0,0],4,0.65)],
    'aero_defense': [
        ('\U0001f525 Rearm Supercycle',.10,[.12,.10,.09,.08,.07,.06,.05,.05,.04,.04],18,1.10),
        ('\U0001f4c8 Budget Growth',.25,[.07,.07,.06,.06,.05,.05,.04,.04,.04,.03],15,1.00),
        ('\U0001f4ca Base',.35,[.04,.04,.04,.04,.03,.03,.03,.03,.03,.03],13,1.00),
        ('\U0001f4c9 Budget Flat',.20,[.01,.01,.02,.02,.02,.02,.02,.02,.02,.02],10,0.85),
        ('\U0001f480 Sequestration',.10,[-.03,-.05,-.03,.01,.02,.02,.02,.02,.01,.01],7,0.65)],
    'data_analytics': [
        ('\U0001f525 Pricing Power',.10,[.14,.13,.12,.11,.10,.09,.08,.07,.06,.06],28,1.10),
        ('\U0001f4c8 Steady Growth',.25,[.10,.10,.09,.08,.08,.07,.06,.06,.05,.05],24,1.05),
        ('\U0001f4ca Base',.35,[.07,.07,.06,.06,.06,.05,.05,.05,.04,.04],20,1.00),
        ('\U0001f4c9 Decel',.20,[.04,.04,.04,.03,.03,.03,.03,.03,.03,.03],15,0.90),
        ('\U0001f480 Disruption',.10,[.01,-.01,.01,.02,.02,.02,.02,.02,.02,.02],10,0.70)],
    'general': [
        ('\U0001f525 Bull Extreme',.10,[.18,.15,.13,.11,.09,.08,.07,.06,.05,.05],18,1.10),
        ('\U0001f4c8 Bull Base',.25,[.10,.09,.08,.07,.06,.06,.05,.04,.04,.04],14,1.00),
        ('\U0001f4ca Base',.35,[.05,.05,.05,.04,.04,.03,.03,.03,.03,.03],12,1.00),
        ('\U0001f4c9 Bear Base',.20,[.01,.01,.02,.02,.02,.02,.02,.02,.02,.02],8,0.80),
        ('\U0001f480 Bear Extreme',.10,[-.04,-.06,-.03,.01,.01,.01,.01,.01,.01,.01],5,0.55)],
}

def calc_wacc(sector, beta=None, dr=0.0):
    b = beta if beta else BETAS.get(sector, 1.0)
    # Risk-free: live 10yr Treasury from FRED (falls back to 3.5%)
    # ERP: Damodaran implied ERP (~4.6% as of early 2025)
    rf = fetch_risk_free_rate(fallback=0.035)
    erp = 0.046
    coe = rf + b * erp
    if sector in ('bank','insurance'): return coe
    # Cost of debt: ~5.2% pre-tax, tax-shield at 21%
    cod = 0.052 * 0.79
    return coe * (1 - dr) + cod * dr

# Terminal growth rates by sector (used in Gordon Growth terminal value)
TERMINAL_GROWTH = {
    'hyperscaler': 0.035, 'saas_tech': 0.035, 'pharma': 0.025, 'fintech': 0.03, 'payment_network': 0.035,
    'consumer': 0.03, 'industrial': 0.025, 'ep': 0.015, 'midstream': 0.02,
    'utility': 0.025, 'telecom': 0.02, 'insurance': 0.015, 'bank': 0.025, 'specialty_lender': 0.02,
    'data_analytics': 0.035, 'aero_defense': 0.025, 'general': 0.025
}

# Sector-specific terminal multiple caps (max, based on historical P/E ranges)
TERMINAL_CAPS = {
    'hyperscaler': 30, 'saas_tech': 28, 'pharma': 18, 'fintech': 22, 'payment_network': 35,
    'consumer': 25, 'industrial': 18, 'ep': 12, 'midstream': 12,
    'utility': 22, 'telecom': 11, 'insurance': 10, 'bank': 14, 'specialty_lender': 10,
    'data_analytics': 30, 'aero_defense': 20, 'general': 20
}

def compute_terminal_multiple(wacc, sector, scenario_hint=1.0):
    """Compute terminal multiple using Gordon Growth Model.
    
    terminal = (1 + g) / (WACC - g)
    
    scenario_hint scales the terminal growth rate:
    - Bull scenarios: hint > 1.0 → slightly higher terminal growth
    - Bear scenarios: hint < 1.0 → lower terminal growth
    
    Sector-specific caps reflect historical valuation ranges.
    """
    base_g = TERMINAL_GROWTH.get(sector, 0.025)
    # Scale terminal growth by scenario hint, but cap near WACC
    g = base_g * scenario_hint
    g = min(g, wacc - 0.01)  # g must be < WACC, with 1% buffer
    g = max(g, 0.005)         # floor at 0.5%
    
    tm = (1 + g) / (wacc - g)
    cap = TERMINAL_CAPS.get(sector, 20)
    return max(6, min(tm, cap))

def dcf_fcf(fcf, growth, tm, wacc, shares, nc=0):
    if fcf <= 0 or shares <= 0: return 0
    cf = fcf; pv = 0
    for i, g in enumerate(growth): cf *= (1+g); pv += cf/(1+wacc)**(i+1)
    pv += cf*tm/(1+wacc)**len(growth)
    return max((pv+nc)/shares, 0)

def dcf_ev(fcff, growth, tm, wacc, shares, nd=0):
    if fcff <= 0 or shares <= 0: return 0
    cf = fcff; pv = 0
    for i, g in enumerate(growth): cf *= (1+g); pv += cf/(1+wacc)**(i+1)
    pv += cf*tm/(1+wacc)**len(growth)
    return max((pv-nd)/shares, 0)

def dcf_rev_margin(rev, growth, start_margin, target_margin, tm, wacc, shares, nc=0):
    """Revenue-based DCF for pre-profit companies: models margin expansion over time."""
    if rev <= 0 or shares <= 0: return 0
    r = rev; pv = 0; n = len(growth)
    for i, g in enumerate(growth):
        r *= (1 + g)
        margin = start_margin + (target_margin - start_margin) * min((i + 1) / n, 1)
        fcf_year = r * margin
        if fcf_year > 0: pv += fcf_year / (1 + wacc) ** (i + 1)
    term_fcf = r * target_margin
    if term_fcf > 0: pv += term_fcf * tm / (1 + wacc) ** n
    return max((pv + nc) / shares, 0)

def ddm_bank(equity, trailing_roe, near_roe, terminal_roe, coe, shares, payout=0.55):
    """Refined bank DDM anchored to actual trailing ROE.
    near_roe: scenario ROE for years 1-3
    terminal_roe: long-run sustainable ROE (years 8-10+)
    Fade: years 4-7 linear interpolation from near to terminal
    Terminal: capped justified P/TBV (0.4-2.0x)
    
    Payout = 55% reflects total capital return (dividends + buybacks).
    Large banks return 55-70% of earnings; 55% is conservative.
    """
    if equity <= 0 or shares <= 0: return 0
    retention = 1 - payout
    b = equity; pv = 0
    for yr in range(1, 11):
        if yr <= 3: roe = near_roe
        elif yr <= 7: roe = near_roe + (terminal_roe - near_roe) * ((yr - 3) / 4)
        else: roe = terminal_roe
        roe = max(roe, 0.005)
        earnings = b * roe
        pv += earnings * payout / (1 + coe) ** yr
        b += earnings * retention
    # Terminal value via justified P/TBV, capped at 2.0x
    # Banks rarely sustain >2x TBV; even JPM peaked at ~2.3x
    g = min(retention * terminal_roe, 0.035)  # terminal growth capped at 3.5% for regulated banks
    if g >= coe: g = coe - 0.005
    justified = (terminal_roe - g) / (coe - g) if (coe > g and terminal_roe > g) else 1.0
    term_ptbv = min(max(justified, 0.4), 2.0)
    pv += b * term_ptbv / (1 + coe) ** 10
    return max(pv / shares, 0)

def make_bank_scenarios(trailing_roe):
    """Generate 5 bank scenarios dynamically anchored to actual trailing ROE.
    
    Key calibration: cap near-term ROE projections to prevent over-extrapolation
    of peak-cycle profitability. Banks mean-revert — 17%+ ROE is exceptional.
    """
    # Cap trailing ROE for projection purposes — don't extrapolate peak-cycle earnings
    capped_roe = min(trailing_roe, 0.16)  # 16% cap on projected near-term ROE
    base_terminal = max(min(capped_roe * 0.82, 0.13), 0.08)  # terminal ROE: 82% of capped, max 13%
    return [
        ('\U0001f525 Rate Tailwind', 0.10,
         min(capped_roe + 0.01, 0.17), min(base_terminal + 0.015, 0.14)),
        ('\U0001f4c8 Strong Cycle', 0.25,
         min(capped_roe, 0.16), min(base_terminal + 0.005, 0.135)),
        ('\U0001f4ca Base', 0.35,
         min(capped_roe * 0.95, 0.15), base_terminal),
        ('\U0001f4c9 NIM Compress', 0.20,
         max(capped_roe - 0.03, 0.06), max(base_terminal - 0.02, 0.07)),
        ('\U0001f480 Credit Crisis', 0.10,
         max(capped_roe - 0.07, 0.03), max(base_terminal - 0.04, 0.05)),
    ]

def solve_implied(fcf, price, shares, nc, wacc, tm):
    if fcf <= 0 or price <= 0 or shares <= 0: return None
    target = price * shares - nc
    def pv(g):
        cf = fcf; s = 0
        for t in range(1,11): cf *= (1+g); s += cf/(1+wacc)**t
        return s + cf*tm/(1+wacc)**10 - target
    lo, hi = -0.20, 0.80
    if pv(lo)*pv(hi) >= 0: return None if pv(hi) < 0 else lo
    for _ in range(50):
        m = (lo+hi)/2
        if pv(m) > 0: hi = m
        else: lo = m
    return (lo+hi)/2

def solve_implied_rev(rev, fcf, price, shares, nc, wacc, tm, sector):
    if rev <= 0 or fcf <= 0 or price <= 0: return None
    target = price * shares - nc
    cm = fcf / rev
    mm_map = {'hyperscaler':0.20,'saas_tech':0.25,'pharma':0.22,'fintech':0.20,'payment_network':0.50,'consumer':0.10,
              'industrial':0.12,'ep':0.08,'midstream':0.15,'bank':0.15,'specialty_lender':0.12,'utility':0.12,'telecom':0.12,
              'insurance':0.10,'aero_defense':0.09,'data_analytics':0.30,'general':0.12}
    mm = max(cm, mm_map.get(sector, 0.12))
    def pv(g):
        r = rev; s = 0
        for t in range(1,11):
            r *= (1+g); margin = cm + (mm-cm)*(t/10); s += r*margin/(1+wacc)**t
        return s + r*mm*tm/(1+wacc)**10 - target
    lo, hi = -0.20, 0.80
    if pv(lo)*pv(hi) >= 0: return None if pv(hi) < 0 else lo
    for _ in range(50):
        m = (lo+hi)/2
        if pv(m) > 0: hi = m
        else: lo = m
    return (lo+hi)/2

# Aliases for compatibility with v7 run_dcf
solve_implied_growth = solve_implied
solve_implied_rev_growth = solve_implied_rev

CAPEX_FACTORS = {
    'hyperscaler': 0.65, 'saas_tech': 0.75, 'pharma': 0.80, 'fintech': 0.80, 'payment_network': 0.85,
    'consumer': 0.85, 'industrial': 0.95, 'telecom': 0.90, 'utility': 1.10,
    'ep': 0.90, 'midstream': 0.90, 'bank': 1.00, 'specialty_lender': 0.90, 'insurance': 0.95,
    'aero_defense': 0.90, 'data_analytics': 0.80, 'general': 0.85,
}

def compute_capex_normalization(fins, sector, model='da_proxy', persistence_pct=25):
    ocf = fins.get('operating_cf', 0)
    capex = fins.get('capex', 0)
    da = fins.get('depreciation', 0)
    if not (ocf > 0) or not (capex > 0): return None
    if model == 'reported':
        return {'fcf_reported': ocf - capex, 'fcf_used': ocf - capex,
                'maintenance_capex': capex, 'growth_capex': 0, 'method': 'Reported (OCF − CapEx)'}
    factor = CAPEX_FACTORS.get(sector, CAPEX_FACTORS['general'])
    if da > 0: maint = da * factor
    else: maint = capex * min(factor, 0.80)
    maint = min(maint, capex)
    growth = capex - maint
    if model == 'blend' and growth > 0:
        p = max(0, min(persistence_pct / 100, 0.60))
        fcf_used = ocf - maint - growth * p
        method = f'D&A×{factor} + {int(p*100)}% growth persistence'
    else:
        fcf_used = ocf - maint
        method = f'D&A×{factor} proxy'
    return {'fcf_reported': ocf - capex, 'fcf_used': max(fcf_used, 0),
            'maintenance_capex': maint, 'growth_capex': growth, 'method': method}

def _compute_fcff(oi, da, maint_capex, ebitda, effective_fcf, sector, margin_factor):
    """Compute Free Cash Flow to Firm (unlevered FCF) for EV-based DCF.
    Prefer NOPAT + D&A - maintenance capex when components available.
    Fall back to sector-aware EBITDA conversion."""
    if oi > 0 and da > 0:
        # NOPAT + D&A - maintenance capex = proper unlevered FCF
        return (oi * 0.79 + da - maint_capex) * margin_factor
    elif ebitda > 0:
        # Sector-aware EBITDA-to-FCFF conversion
        # Capital-light sectors: higher conversion (less maintenance capex drag)
        # Capital-heavy sectors: lower conversion (heavy maintenance needs)
        factor = {
            'saas_tech': 0.65, 'fintech': 0.65, 'payment_network': 0.70, 'data_analytics': 0.65, 'hyperscaler': 0.60,
            'pharma': 0.60, 'consumer': 0.55, 'aero_defense': 0.55, 'general': 0.55,
            'industrial': 0.50, 'telecom': 0.48, 'utility': 0.45, 'ep': 0.45, 'midstream': 0.50,
        }.get(sector, 0.55)
        return ebitda * factor * margin_factor
    else:
        return effective_fcf * 1.3

def run_dcf(fins, price, shares_mil, sector, beta=None, capex_model='da_proxy', capex_persistence=25):
    # EPS-based shares sanity check: if the filing has EPS, the implied share count
    # (NI / EPS) is the most reliable diluted count. For multi-class stocks, external
    # share sources often return per-class or float counts that produce absurd valuations.
    eps = fins.get('eps_diluted', 0)
    ni_check = fins.get('net_income', 0)
    if eps and eps > 0 and ni_check and ni_check > 0 and shares_mil > 0:
        eps_implied_shares_mil = ni_check / (eps * 1e6)
        ratio = shares_mil / eps_implied_shares_mil
        if ratio < 0.50 or ratio > 2.0:
            # shares_mil is wildly inconsistent with EPS — override
            shares_mil = eps_implied_shares_mil

    shares = shares_mil * 1e6
    mcap = price * shares

    # ══════════════════════════════════════════════
    #  BDC / INVESTMENT COMPANY VALUATION
    # ══════════════════════════════════════════════
    # BDCs are valued on NAV (book value), dividend yield, and ROE — NOT DCF.
    # Assets are marked-to-market, so NAV is a true fair value anchor.
    if fins.get('_is_bdc') or sector == 'bdc':
        nav = fins.get('stockholders_equity', 0) or 0
        nav_per_share = fins.get('nav_per_share', nav / shares if shares > 0 else 0)
        nii = fins.get('operating_income', 0) or fins.get('net_income', 0) or 0  # Net Investment Income
        total_ii = fins.get('revenue', 0) or 0  # Total investment income
        debt = fins.get('long_term_debt', 0) or 0
        cash = fins.get('cash', 0) or 0

        roe = nii / nav if nav > 0 else 0
        nii_per_share = nii / shares if shares > 0 else 0
        leverage = debt / nav if nav > 0 else 0

        # BDC valuation: 5-scenario NAV multiple model
        # Well-managed BDCs with high ROE trade at premium to NAV
        # Poorly managed or at-risk BDCs trade at discount
        coe = calc_wacc('general', beta=0.85)  # BDC beta ~0.85, use consistent Rf/ERP

        # Justified P/NAV = (ROE - g) / (COE - g) where g = retention × ROE
        # BDCs must distribute 90%+ of income, so retention is low (~10%)
        payout = 0.90
        g = min((1 - payout) * roe, 0.03)
        justified_p_nav = (roe - g) / (coe - g) if coe > g and roe > 0 else 0.8

        # Scenarios based on credit quality and NII sustainability
        scenarios = [
            ('🔥 NII Growth',    0.10, min(justified_p_nav * 1.15, 1.40)),
            ('📈 Stable NII',    0.25, min(justified_p_nav * 1.05, 1.25)),
            ('📊 Base',          0.35, min(justified_p_nav, 1.15)),
            ('📉 NII Decline',   0.20, max(justified_p_nav * 0.85, 0.70)),
            ('💀 Credit Losses', 0.10, max(justified_p_nav * 0.60, 0.50)),
        ]

        scen_results = []; pw_fv = 0
        for name, prob, p_nav in scenarios:
            fv = nav_per_share * p_nav
            upside = (fv - price) / price * 100 if price > 0 else 0
            pw_fv += prob * fv
            scen_results.append({'name': name, 'prob': prob, 'fv': round(fv, 2), 'upside': round(upside, 1)})

        pw_up = (pw_fv - price) / price * 100 if price > 0 else 0
        if pw_up > 30: verdict = 'SIGNIFICANTLY UNDERVALUED'
        elif pw_up > 10: verdict = 'UNDERVALUED'
        elif pw_up > -10: verdict = 'FAIR VALUE'
        elif pw_up > -25: verdict = 'OVERVALUED'
        else: verdict = 'SIGNIFICANTLY OVERVALUED'

        # Premium/discount to NAV
        p_nav_current = price / nav_per_share if nav_per_share > 0 else 0
        div_yield = nii_per_share * payout / price * 100 if price > 0 else 0

        return {
            'price': price, 'pw_fv': round(pw_fv, 2), 'pw_up': round(pw_up, 1),
            'verdict': verdict, 'wacc': round(coe, 4),
            'scenarios': scen_results,
            'trailing_growth': (total_ii - fins.get('revenue_prior', 0)) / fins['revenue_prior'] if fins.get('revenue_prior', 0) > 0 else None,
            'implied_fcf_growth': None, 'implied_rev_growth': None,
            'sanity_flags': [
                f'BDC: NAV-based valuation (not DCF)',
                f'P/NAV: {p_nav_current:.2f}x ({("premium" if p_nav_current > 1 else "discount")})',
                f'Dividend yield: {div_yield:.1f}% (est)',
                f'ROE (NII/NAV): {roe*100:.1f}%',
                f'Leverage: {leverage*100:.0f}% of NAV',
            ],
            'is_buyback_machine': False, 'sbc_haircut': 0,
            'dynamic_probs': False, 'use_rev_margin': False, 'use_hybrid': False,
            'market_implies': None, 'asset_floor': {
                'book_per_share': round(nav_per_share, 2),
                'tangible_floor': round(nav_per_share, 2),  # BDC assets are already at fair value
                'book_to_price': round(nav_per_share / price * 100, 1) if price > 0 else None,
                'total_assets': fins.get('total_assets', 0),
            },
            'ev_rev_multiple': None, 'comps_check': None, 'capex': None,
            'inputs': {
                'revenue': total_ii, 'net_income': nii, 'fcf': nii,
                'ebitda': nii, 'operating_cf': nii, 'capex': 0,
                'depreciation': 0, 'cash': cash, 'debt': debt,
                'equity': nav, 'shares': shares, 'nav_per_share': nav_per_share,
                'fcf_reported': nii, 'capex_method': 'BDC: NII = distributable cash',
                'cash_ni': nii, 'normalized_ocf': nii,
            }
        }
    fcf = fins.get('fcf', 0) or 0
    ni = fins.get('net_income', 0) or 0
    rev = fins.get('revenue', 0) or 0
    oi = fins.get('operating_income', 0) or 0
    da = fins.get('depreciation', 0) or 0
    ocf = fins.get('operating_cf', 0) or 0
    capex = fins.get('capex', 0) or 0
    cash = fins.get('cash', 0) or 0
    debt = fins.get('long_term_debt', 0) or 0
    equity = fins.get('stockholders_equity', 0) or 0
    gp = fins.get('gross_profit', 0) or 0
    # Derive gross profit for companies that don't report it explicitly
    # (e.g. SaaS companies that report cost of subscription + cost of professional services)
    if not gp and rev > 0 and oi > 0:
        # Approximate: GP = Revenue - COGS. For SaaS, COGS ≈ 30-40% of revenue
        # If operating costs (opex) are known, GP = Revenue - (Total costs - SGA - R&D)
        # Simple fallback: if OI margin > 5%, estimate GP from industry norms
        if sector in ('saas_tech', 'hyperscaler', 'fintech'):
            gp = rev * 0.75  # SaaS gross margins typically 70-80%
        else:
            gp = rev * 0.60  # conservative default
    amort_intangible = fins.get('amort_intangible', 0) or 0
    sbc = fins.get('sbc', 0) or 0
    inventory_change = fins.get('inventory_change', 0) or 0  # negative = build

    # ══════════════════════════════════════════════
    #  PHASE 1: DERIVE NORMALIZED FCF
    # ══════════════════════════════════════════════

    # ── Insurance-specific: use NI as FCF proxy ──
    # Insurance OCF includes policyholder reserve flows, premium collections,
    # and loss payment timing that make it unreliable as a FCF proxy.
    # NI is the correct earnings measure for insurance valuation.
    #
    # Mid-cycle adjustment: insurance earnings are deeply cyclical.
    # Only apply haircut when NI margin suggests peak-cycle earnings.
    # Typical mid-cycle NI margin: 5-8% of premiums. Above 10% = likely peak.
    # Below 5% = already depressed, no haircut needed.
    if sector == 'insurance' and ni > 0:
        ni_margin_ins = ni / rev if rev > 0 else 0
        if ni_margin_ins > 0.10:
            # Peak cycle: heavy haircut (margins well above historical avg)
            mid_cycle_factor = 0.80
        elif ni_margin_ins > 0.07:
            # Above-average cycle: moderate haircut
            mid_cycle_factor = 0.90
        else:
            # At or below mid-cycle: no haircut
            mid_cycle_factor = 1.0
        fcf = ni * mid_cycle_factor + da * 0.3
        ocf = fcf  # Override OCF to prevent later normalization from inflating
        capex = 0

    # ── Utility-specific: rate base expansion makes reported FCF meaningless ──
    # Utilities invest heavily in regulated assets (rate base) that earn guaranteed
    # returns. CapEx >> D&A is normal and productive, not a problem.
    # Use earnings-based proxy: NI + D&A (approximates regulated cash earnings)
    # then let the EV-based DCF handle debt subtraction.
    if sector == 'utility' and ni > 0 and capex > da * 1.5:
        # For growth utilities: FCF = NI + D&A (proxy for regulated earnings power)
        # This is the cash earnings before growth investment
        fcf = ni + da * 0.8  # NI + 80% of D&A (conservative)
        ocf = fcf + capex  # reconstruct OCF so later logic doesn't override
        # Keep capex so maintenance vs growth split still works

    # ── Step 1a: Compute "cash NI" by adding back intangible amortization ──
    # Intangible amortization (from M&A purchase accounting) is a non-cash charge
    # that depresses GAAP NI but doesn't reflect real economic cost
    cash_ni = ni + amort_intangible  # NI with intangible amort added back

    # ── Step 1b: Normalize OCF for working capital swings ──
    # Large WC drains during growth ramps are temporary, not structural.
    # Detect: if inventory build is large relative to revenue growth, normalize it.
    wc_adjustment = 0
    trailing_growth_raw = None
    if rev > 0 and fins.get('revenue_prior', 0) > 0:
        trailing_growth_raw = (rev - fins['revenue_prior']) / fins['revenue_prior']

    if inventory_change < 0 and rev > 0:
        inv_build = abs(inventory_change)
        inv_to_rev = inv_build / rev
        # If inventory build > 4% of revenue AND capex is also heavy, this is a ramp
        # Normalize by assuming steady-state inventory grows at ~2% of revenue
        if inv_to_rev > 0.04 and capex > da * 1.3:
            normal_inv_growth = rev * 0.02 * max(trailing_growth_raw or 0.10, 0.05)
            wc_adjustment = inv_build - normal_inv_growth  # add back the excess
    
    normalized_ocf = ocf + wc_adjustment

    # ── Step 1c: Maintenance vs growth capex ──
    capex_der = compute_capex_normalization(fins, sector, capex_model, capex_persistence)
    factor = CAPEX_FACTORS.get(sector, CAPEX_FACTORS['general'])
    if da > 0:
        maint_capex = da * factor  # D&A proxy for maintenance (using real D&A including intangible amort)
        # But maintenance capex should be based on REAL depreciation, not intangible amort
        real_dep = da - amort_intangible if amort_intangible > 0 else da
        maint_capex_real = max(real_dep, da * 0.5) * factor  # at least half of D&A
        maint_capex = min(maint_capex_real, capex)  # can't exceed total capex
    else:
        maint_capex = capex * min(factor, 0.80)

    # ── Step 1d: Assemble FCF candidates ──
    # Method A: Reported FCF
    fcf_reported = ocf - capex

    # Method B: Normalized OCF - maintenance capex (best for growth-phase companies)
    # IMPROVEMENT: Apply capex conversion discount — not all growth capex creates value.
    # Competitive arms races, failed projects, and infrastructure overkill mean some
    # portion of growth capex is "waste" that won't generate future returns.
    growth_capex = max(capex - maint_capex, 0)
    CAPEX_WASTE = {
        'hyperscaler': 0.30, 'saas_tech': 0.20, 'pharma': 0.25, 'fintech': 0.15, 'payment_network': 0.10,
        'consumer': 0.15, 'industrial': 0.15, 'ep': 0.20, 'midstream': 0.15,
        'utility': 0.10, 'telecom': 0.20, 'insurance': 0.05, 'bank': 0.05, 'specialty_lender': 0.05,
        'aero_defense': 0.15, 'data_analytics': 0.10, 'general': 0.20,
    }
    waste_pct = CAPEX_WASTE.get(sector, 0.20)
    # Scale waste higher for extreme capex intensity (capex > 70% of OCF)
    # Only applies to sectors where extreme capex is unusual (tech, fintech)
    # NOT for capital-intensive sectors (consumer, utility, industrial, ep) where heavy capex is normal
    if ocf > 0 and capex > ocf * 0.70 and sector in ('hyperscaler', 'saas_tech', 'fintech', 'pharma', 'telecom'):
        intensity_scale = min(capex / ocf, 2.0)  # 1.0 - 2.0x
        waste_pct = min(waste_pct * intensity_scale, 0.45)
    capex_waste = growth_capex * waste_pct
    fcf_normalized = normalized_ocf - maint_capex - capex_waste

    # Method C: Cash NI proxy (for when OCF is severely distorted)
    # Cash NI × conversion factor, minus maintenance capex
    ni_conversion = 0.85 if sector in ('hyperscaler','saas_tech','pharma','fintech','industrial') else 0.75
    fcf_cash_ni = cash_ni * ni_conversion

    # ── Step 1e: Choose best FCF estimate ──
    fcf_method = ''
    
    # BNPL/Lending override: if loan originations are material (>30% of OCF),
    # the standard OCF-capex FCF is misleading because loan originations are
    # classified as investing CF but represent the core business's capital deployment.
    # Use NI as the FCF proxy instead — it already accounts for credit losses.
    loan_orig = fins.get('loan_originations', 0) or 0
    is_lending_business = loan_orig > 0 and ocf > 0 and loan_orig > ocf * 0.30
    
    if is_lending_business and ni > 0:
        # For lending/BNPL: use NI × conversion as FCF
        # NI already incorporates credit loss provisions (the "cost" of the loan book)
        # and doesn't include the loan originations that inflate OCF
        fcf = cash_ni * ni_conversion if cash_ni > 0 else ni * ni_conversion
        fcf_method = f'NI×{ni_conversion} (lending adj: ${loan_orig/1e6:.0f}M originations)'
        # Still report the raw FCF for transparency
        if capex_der:
            capex_der['loan_originations'] = loan_orig
    else:
        # Score each method based on quality signals
        ocf_quality = ocf / cash_ni if cash_ni > 0 else 0
        
        if fcf_reported > 0 and ocf_quality > 0.50:
            # Healthy OCF conversion — use normalized capex version
            fcf = max(fcf_normalized, fcf_reported * 0.8)
            fcf_method = f'Normalized (OCF-maint)'
        elif fcf_normalized > 0 and wc_adjustment > 0:
            # WC-adjusted OCF is positive — growth ramp detected
            fcf = fcf_normalized
            fcf_method = f'WC-normalized (inv adj ${wc_adjustment/1e6:.0f}M)'
        elif cash_ni > 0:
            # Fall back to cash NI proxy
            fcf = fcf_cash_ni
            if amort_intangible > 0:
                fcf_method = f'Cash NI (NI+${amort_intangible/1e6:.0f}M amort)×{ni_conversion}'
            else:
                fcf_method = f'NI×{ni_conversion} proxy'
        elif ni > 0:
            fcf = ni * ni_conversion
            fcf_method = f'NI×{ni_conversion} fallback'
    
    # Update capex_der for reporting
    if capex_der:
        capex_der['method'] = fcf_method
        capex_der['fcf_used'] = fcf
        capex_der['maintenance_capex'] = maint_capex
        capex_der['wc_adjustment'] = wc_adjustment
    else:
        capex_der = {'fcf_reported': fcf_reported, 'fcf_used': fcf,
                     'maintenance_capex': maint_capex, 'growth_capex': max(capex - maint_capex, 0),
                     'method': fcf_method, 'wc_adjustment': wc_adjustment}

    ebitda = (oi + da) if (oi and da) else oi * 1.25 if oi else ocf * 1.1 if ocf else ni / 0.65 if ni else 0
    nd = debt - cash
    nc = cash - debt

    is_pre_profit = ni < 0 and rev > 0
    is_high_capex_growth = rev > 0 and capex > da * 2 and capex > rev * 0.3

    if fcf <= 0:
        if cash_ni > 0:
            fcf = cash_ni * ni_conversion
            fcf_method = f'Cash NI fallback'
        elif ni > 0:
            fcf = ni * ni_conversion
            fcf_method = f'NI fallback'
        elif rev > 0:
            gp_margin = gp / rev if gp > 0 else 0
            proxy_margin = {'hyperscaler':.15,'saas_tech':.12,'pharma':.10,'ep':.08,'utility':.10,'insurance':.06}.get(sector, .07)
            if gp_margin > 0.40:
                proxy_margin = max(proxy_margin, gp_margin * 0.25)
            fcf = rev * proxy_margin
            fcf_method = f'Rev×{proxy_margin:.0%} proxy'
        else:
            return {'error': 'Cannot derive FCF from extracted data'}
    if fcf <= 0:
        return {'error': 'Negative FCF — cannot value'}

    # ── SBC haircut ──
    # SBC is a real cost (dilution) but the market values high-SBC SaaS companies on
    # non-GAAP basis. Scale the haircut based on SBC intensity.
    sbc_haircut = 0
    if sbc > 0:
        sbc_pct_of_rev = sbc / rev if rev > 0 else 0
        if sbc_pct_of_rev > 0.15:
            # High-SBC company (>15% of rev): market prices on non-GAAP, lighter haircut
            sbc_haircut = sbc * 0.35
        elif sbc_pct_of_rev > 0.08:
            # Moderate SBC (8-15%): standard haircut
            sbc_haircut = sbc * 0.55
        else:
            # Low SBC (<8%): full treatment
            sbc_haircut = sbc * 0.75
    elif sector in ('saas_tech', 'hyperscaler', 'fintech', 'data_analytics') and ni > 0 and ocf > 0:
        # Estimate SBC from OCF-NI gap. BUT for fintech/lending businesses,
        # the OCF-NI gap is dominated by credit loss provisions (non-cash charges
        # added back in CF), NOT stock-based compensation. Only use this estimate
        # when credit provisions are not the primary driver of the gap.
        sbc_est = max(ocf - ni - da * 0.3, 0)
        # Guard: if provision for credit losses is a large portion of the gap,
        # the estimate is unreliable — credit provisions, not SBC, explain it.
        provision = fins.get('provision_credit_losses', 0)
        if provision > 0 and provision > sbc_est * 0.5:
            sbc_est = 0  # Credit provisions explain the gap, not SBC
        # Also guard: if the estimated SBC exceeds 20% of revenue, it's likely
        # a misattribution (very few companies have SBC > 20% of revenue)
        if sbc_est > rev * 0.20:
            sbc_est = 0
        if sbc_est > ni * 0.10:
            sbc_haircut = sbc_est * 0.50
    if sbc_haircut > 0 and sbc_haircut < fcf * 0.50:
        fcf -= sbc_haircut
        fcf_method += f' → SBC -${sbc_haircut/1e6:.0f}M'

    if capex_der:
        capex_der['method'] = fcf_method
        capex_der['fcf_used'] = fcf

    # ── Negative equity / buyback machine detection ──
    is_buyback_machine = (
        equity < 0 and ni > 0 and fcf > 0 and
        ocf > debt * 0.08
    )
    if is_buyback_machine:
        max_debt_penalty = fcf * 3
        effective_nd = min(nd, max_debt_penalty)
        nc = -effective_nd

    ev = mcap + nd
    dr = min(debt / max(ev, 1), 0.6) if ev > 0 else 0
    wacc = calc_wacc(sector, beta, dr)

    # ══════════════════════════════════════════════
    #  PHASE 2: HYBRID FCF / REVENUE-MARGIN DCF
    # ══════════════════════════════════════════════

    # Determine margin trajectory for revenue-margin DCF
    # Current FCF margin (using our normalized FCF)
    cur_fcf_margin = fcf / rev if rev > 0 else 0
    # Gross profit margin trend signals durability of growth
    gp_margin = gp / rev if (gp > 0 and rev > 0) else 0
    # Cash NI margin (better proxy for true earnings power)
    cash_ni_margin = cash_ni / rev if (cash_ni > 0 and rev > 0) else 0

    # Determine if hybrid rev-margin DCF should be used
    # Use it when: margins are clearly expanding, OR FCF is distorted by investment phase
    use_hybrid = False
    margin_expanding = False

    # Compute gross profit if not explicitly extracted
    if gp == 0 and rev > 0:
        cogs = fins.get('cost_of_revenue', 0) or fins.get('cost_of_goods_sold', 0)
        if cogs > 0:
            gp = rev - cogs
    gp_margin = gp / rev if (gp > 0 and rev > 0) else 0

    if rev > 0 and trailing_growth_raw is not None:
        # Signals that warrant revenue-margin modeling:
        margin_gap = cash_ni_margin - cur_fcf_margin
        
        # Guard: skip hybrid if FCF margin already exceeds the sector target margin.
        # Hybrid rev-margin modeling assumes current FCF understates long-term earning power.
        # If FCF margin is ALREADY high, hybrid would regress margins DOWN toward the target,
        # penalizing companies that have genuinely superior unit economics (e.g., NVDA at 45% FCF margin).
        target_margin = TARGET_MARGINS.get(sector, 0.12)
        fcf_already_mature = cur_fcf_margin > target_margin * 1.3  # 30% buffer above target
        
        # 1. Heavy capex relative to D&A with solid growth (building for the future)
        if capex > da * 1.5 and trailing_growth_raw > 0.10 and cash_ni_margin > 0.08 and not fcf_already_mature:
            use_hybrid = True
        # 2. Large amort addback — cash NI margin materially above FCF margin  
        elif amort_intangible > 0 and margin_gap > 0.03 and trailing_growth_raw > 0.08 and not fcf_already_mature:
            use_hybrid = True
            margin_expanding = True
        # 3. High gross margins with compressed FCF (operating leverage ahead)
        elif gp_margin > 0.30 and cur_fcf_margin < 0.12 and trailing_growth_raw > 0.10:
            use_hybrid = True
            margin_expanding = True
        # 4. Fast growth with significant WC normalization (ramp phase)
        elif wc_adjustment > rev * 0.03 and trailing_growth_raw > 0.12 and not fcf_already_mature:
            use_hybrid = True
        # 5. High-SBC SaaS: SBC depresses GAAP margins but non-GAAP margins are strong
        #    These companies have margin expansion potential as SBC decreases as % of rev
        #    SaaS inherently has 60-80% gross margins; the SBC/rev + sector check is sufficient
        elif (sbc > rev * 0.10 and sector in ('saas_tech', 'hyperscaler', 'fintech')
              and trailing_growth_raw > 0.05):
            use_hybrid = True
            margin_expanding = True

    # For hybrid: blend FCF-DCF and rev-margin-DCF
    # Weight based on FCF quality — high-quality FCF gets more weight
    if use_hybrid:
        fcf_weight = min(max(ocf_quality, 0.0), 0.6)  # 0-60% weight on FCF model
        rev_weight = 1.0 - fcf_weight
    else:
        fcf_weight = 1.0
        rev_weight = 0.0

    # Determine use_rev_margin for pre-profit (backward compat)
    use_rev_margin = is_pre_profit and rev > 0 and fcf < rev * 0.05

    # ── Bank-specific DDM ──
    if sector in ('bank', 'specialty_lender'):
        trailing_roe = ni / equity if equity > 0 else 0.10
        trailing_roe = max(min(trailing_roe, 0.30), 0.01)
        bank_scens = make_bank_scenarios(trailing_roe)
        trailing_growth = (rev - fins['revenue_prior']) / fins['revenue_prior'] if (rev > 0 and fins.get('revenue_prior', 0) > 0) else None

        scen_results = []; pw_fv = 0
        for name, prob, near_roe, term_roe in bank_scens:
            fv = ddm_bank(equity if equity > 0 else mcap * 0.6, trailing_roe, near_roe, term_roe, wacc, shares)
            upside = (fv - price) / price * 100 if price > 0 else 0
            pw_fv += prob * fv
            scen_results.append({'name': name, 'prob': prob, 'fv': round(fv, 2), 'upside': round(upside, 1)})

        pw_up = (pw_fv - price) / price * 100 if price > 0 else 0
        if pw_up > 30: verdict = 'SIGNIFICANTLY UNDERVALUED'
        elif pw_up > 10: verdict = 'UNDERVALUED'
        elif pw_up > -10: verdict = 'FAIR VALUE'
        elif pw_up > -25: verdict = 'OVERVALUED'
        else: verdict = 'SIGNIFICANTLY OVERVALUED'

        # ── Bank-specific beyond-DCF layers ──

        # Asset Floor (book value)
        asset_floor = None
        ta = fins.get('total_assets', 0) or 0
        tl = fins.get('total_liabilities', 0) or 0
        if equity > 0 and shares > 0:
            book_per_share = equity / shares
            tangible = max(equity * 0.85, 0)  # banks have some goodwill
            asset_floor = {
                'book_per_share': round(book_per_share, 2),
                'tangible_floor': round(tangible / shares, 2),
                'book_to_price': round(book_per_share / price * 100, 1) if price > 0 else None,
                'total_assets': ta,
            }

        # EV/Revenue for banks (P/Revenue essentially since debt is operational)
        ev_rev = None
        if rev > 0 and mcap > 0:
            ps = mcap / rev
            sector_median = 3.0
            ev_rev = {
                'current': round(ps, 2),
                'sector_median': sector_median,
                'premium_pct': round((ps / sector_median - 1) * 100, 1),
                'at_median_price': round(sector_median * rev / shares, 2) if shares > 0 else None,
            }

        # Comps: P/E and P/B
        comps_check = None
        pe_comp = 12.0  # bank sector median P/E
        if ni > 0 and shares > 0 and price > 0:
            pe_now = mcap / ni
            fv_pe = (ni * pe_comp) / shares
            pb_now = price / (equity / shares) if equity > 0 else 0
            comps_check = {
                'current_pe': round(pe_now, 1),
                'sector_pe': pe_comp,
                'pe_fv': round(fv_pe, 2),
                'evebitda_fv': None,  # not meaningful for banks
                'sector_evebitda': None,
                # Bank-specific extras
                'pb_now': round(pb_now, 2),
                'pb_justified': round(trailing_roe / wacc, 2) if wacc > 0 else None,
                'roe': round(trailing_roe * 100, 1),
            }

        # Sanity flags
        sanity_flags = ['Bank DDM: dividend discount model (not FCF-based)']
        if trailing_roe < wacc:
            sanity_flags.append(f'ROE {trailing_roe*100:.1f}% < COE {wacc*100:.1f}% — destroying value')
        if equity > 0 and price / (equity / shares) > 2.5:
            sanity_flags.append(f'P/B {price/(equity/shares):.1f}x — rich for a bank')
        if all(s['upside'] > 20 for s in scen_results):
            sanity_flags.append('All scenarios bullish — model may be overly optimistic')
        if all(s['upside'] < -20 for s in scen_results):
            sanity_flags.append('All scenarios bearish — model may miss value drivers')

        # 10-year intrinsic value projection for banks (based on book value growth)
        price_paths = None
        if equity > 0 and shares > 0 and price > 0:
            price_paths = {'years': list(range(0, 11)), 'scenarios': [], 'pw_path': [pw_fv], 'market_price': price}
            pw_path_accum = [0.0] * 10
            for si, (name, prob, near_roe, term_roe) in enumerate(bank_scens):
                sc = scen_results[si]
                fv_0 = sc['fv']
                # Project BV growth + terminal P/B to get intrinsic value path
                bv = equity
                yearly = [fv_0]
                payout = 0.40
                for yr in range(10):
                    if yr < 3: roe = near_roe
                    elif yr < 7: roe = near_roe + (term_roe - near_roe) * ((yr - 3) / 4)
                    else: roe = term_roe
                    roe = max(roe, 0.005)
                    bv *= (1 + roe * (1 - payout))
                    # Intrinsic value = BV × justified P/B at that ROE
                    g_t = min((1 - payout) * term_roe, 0.04)
                    jpb = (roe - g_t) / (wacc - g_t) if wacc > g_t else 1.0
                    jpb = min(max(jpb, 0.5), 2.5)
                    iv = bv * jpb / shares
                    yearly.append(round(max(iv, 0), 2))
                for yr in range(10):
                    pw_path_accum[yr] += prob * yearly[yr + 1]
                price_paths['scenarios'].append({'name': name, 'path': yearly})
            price_paths['pw_path'] = [round(pw_fv, 2)] + [round(p, 2) for p in pw_path_accum]

        return {
            'price': price, 'pw_fv': round(pw_fv, 2), 'pw_up': round(pw_up, 1),
            'verdict': verdict, 'wacc': round(wacc, 4),
            'scenarios': scen_results, 'trailing_growth': trailing_growth,
            'implied_fcf_growth': None, 'implied_rev_growth': None,
            'sanity_flags': sanity_flags,
            'is_buyback_machine': False, 'sbc_haircut': 0,
            'dynamic_probs': False, 'use_rev_margin': False,
            'market_implies': None,
            'asset_floor': asset_floor,
            'ev_rev_multiple': ev_rev,
            'comps_check': comps_check,
            'capex': None,
            'price_paths': price_paths,
            'inputs': {
                'revenue': rev, 'net_income': ni, 'fcf': ocf - capex if ocf else None, 'ebitda': ebitda,
                'operating_cf': ocf, 'capex': capex, 'depreciation': da,
                'cash': cash, 'debt': debt, 'equity': equity, 'shares': shares,
                'fcf_reported': ocf - capex if ocf and capex else None,
                'capex_method': 'Bank DDM (equity × ROE)',
            }
        }

    # ── Standard / Rev-Margin DCF ──
    templates = SCENARIOS.get(sector, SCENARIOS['general'])

    trailing_growth = trailing_growth_raw
    sector_base_y1 = templates[2][2][0]

    # Dynamic probability weighting
    probs = [t[1] for t in templates]
    if rev > 0:
        is_large = rev > 10e9
        is_mature = trailing_growth is not None and abs(trailing_growth) < 0.08
        is_hypergrowth = trailing_growth is not None and trailing_growth > 0.40
        ni_margin = ni / rev if rev > 0 else 0
        oi_margin = oi / rev if (oi > 0 and rev > 0) else 0
        # Sector-aware profitability: low-margin businesses (retail, consumer) 
        # are "profitable" at much lower margins than tech/pharma
        is_profitable = (ni_margin > 0.10 or 
                        (ni > 0 and fcf > 0 and (oi_margin > 0.03 or fcf / rev > 0.015)))

        if is_large and is_mature and is_profitable:
            probs = [0.05, 0.20, 0.45, 0.20, 0.10]
        elif is_hypergrowth and not is_large and not is_profitable:
            probs = [0.08, 0.18, 0.30, 0.22, 0.22]
        elif is_hypergrowth and not is_large and is_profitable:
            probs = [0.12, 0.22, 0.30, 0.20, 0.16]
        elif not is_profitable:
            probs = [0.05, 0.20, 0.30, 0.25, 0.20]

    # ── Fix 2: Detect mature compounders that shouldn't be growth-penalized ──
    # A $400B revenue company growing 3% isn't a "slow hyperscaler" —
    # it's a mature franchise whose value is in current FCF, not future growth.
    is_mature_compounder = (
        rev > 50e9 and ni > 0 and (ni / rev) > 0.08 and
        fcf > 0 and ocf > 0 and ocf > ni * 0.5
    )
    
    # Stable compounders: companies with very consistent growth that shouldn't 
    # see heavy deceleration in projections. If trailing growth is between 2-8%
    # and the company is profitable with good FCF conversion, maintain that rate.
    is_stable_compounder = (
        trailing_growth is not None and 0.02 < trailing_growth < 0.10 and
        ni > 0 and fcf > 0 and rev > 3e9 and
        (ni / rev > 0.02 or (oi > 0 and oi / rev > 0.03) or fcf / rev > 0.02)
    )

    # For large stable compounders, shift probability toward base/bull
    # These companies have durable moats that make extreme bear outcomes unlikely
    if is_stable_compounder and is_large:
        # Compress bear scenarios: less weight on disruption, more on base
        probs = [0.08, 0.22, 0.45, 0.18, 0.07]

    # ── IMPROVEMENT: Valuation-level probability softener ──
    # Expensive stocks (low FCF yield) should have more bear weight.
    # Cheap stocks (high FCF yield) should have more bull weight.
    # Uses FCF yield (more universal than P/E across sectors).
    if fcf > 0 and mcap > 0:
        fcf_yield = fcf / mcap
        if fcf_yield < 0.015:
            # Very expensive (>67x FCF) — tilt bear
            bear_tilt = 0.04
            probs[0] = max(probs[0] - bear_tilt * 0.3, 0.03)
            probs[1] = max(probs[1] - bear_tilt * 0.3, 0.10)
            probs[3] = probs[3] + bear_tilt * 0.4
            probs[4] = probs[4] + bear_tilt * 0.2
        elif fcf_yield < 0.025:
            # Expensive (>40x FCF) — slight bear tilt
            bear_tilt = 0.02
            probs[0] = max(probs[0] - bear_tilt * 0.4, 0.03)
            probs[3] = probs[3] + bear_tilt * 0.4
        elif fcf_yield > 0.08:
            # Cheap (<12.5x FCF) — tilt bull
            bull_tilt = 0.03
            probs[0] = probs[0] + bull_tilt * 0.3
            probs[1] = probs[1] + bull_tilt * 0.4
            probs[4] = max(probs[4] - bull_tilt * 0.5, 0.03)
            probs[3] = max(probs[3] - bull_tilt * 0.2, 0.05)
        # Normalize probs to sum to 1.0
        total = sum(probs)
        probs = [p / total for p in probs]

    # ── Fix 3: Quality-adjusted terminal multiple ──
    # High-ROIC, high-FCF-conversion businesses deserve premium terminals.
    # These are durable competitive advantages that persist past year 10.
    quality_tm_multiplier = 1.0
    if rev > 0 and fcf > 0:
        # ROIC = NOPAT / Invested Capital
        # Invested capital = Equity + Debt - Cash (or at minimum, half of revenue)
        # This avoids the ROE trap where leveraged companies look great on thin equity
        invested_capital = max(equity + debt - cash, rev * 0.5) if equity > 0 else max(debt, rev * 0.5)
        nopat = oi * 0.79 if oi > 0 else ni  # OI × (1-tax) preferred
        roic = nopat / invested_capital if invested_capital > 0 else 0
        fcf_conversion = fcf / max(ni, 1) if ni > 0 else 0
        fcf_margin = fcf / rev

        # Premium for high-quality compounders
        if roic > 0.20 and fcf_conversion > 0.70:
            quality_tm_multiplier = 1.30  # 30% terminal premium
        elif roic > 0.15 and fcf_conversion > 0.60:
            quality_tm_multiplier = 1.15  # 15% terminal premium
        elif ni / rev > 0.15 and fcf_margin > 0.10:
            quality_tm_multiplier = 1.10  # 10% terminal premium

        # Penalty for low-quality / capital-intensive businesses
        if roic < 0.05 and fcf_conversion < 0.30:
            quality_tm_multiplier = 0.85

    # HIGH-MARGIN TERMINAL CAP: Companies with FCF margin > 40% that are NOT
    # in hypergrowth have limited margin expansion potential at maturity.
    # Score = margin × (1 - min(growth, 0.50)): higher = more mature + high margin.
    # Hypergrowth companies (NVDA) get exempted because their margins may compress.
    if rev > 0 and fcf > 0:
        fcf_margin_check = fcf / rev
        growth_for_cap = trailing_growth if trailing_growth is not None else 0.10
        maturity_score = fcf_margin_check * (1 - min(growth_for_cap, 0.50))
        if maturity_score > 0.30 and fcf_margin_check > 0.40:
            quality_tm_multiplier = min(quality_tm_multiplier, 1.0)  # no premium
        if maturity_score > 0.40 and fcf_margin_check > 0.50:
            quality_tm_multiplier = min(quality_tm_multiplier, 0.85)  # 15% discount

    # Negative equity (buyback machines) shouldn't get ROIC-based premium
    # because ROIC on negative equity is meaningless
    if equity <= 0:
        quality_tm_multiplier = min(quality_tm_multiplier, 1.0)

    scen_results = []
    pw_fv = 0

    for idx, (name, _default_prob, path_template, terminal, margin_factor) in enumerate(templates):
        prob = probs[idx]
        path = list(path_template)

        # Growth anchoring with proportional cap
        if trailing_growth is not None:
            premium = trailing_growth - sector_base_y1

            if premium < 0 and is_mature_compounder:
                # Don't penalize mature compounders for growing below sector template.
                # Their value is in CURRENT cash generation compounding, not growth rate.
                # Allow slight negative adjustment but floor it at -2% to avoid
                # treating Apple like a growth stock that's decelerating.
                premium = max(premium, -0.02)
            else:
                # Logarithmic compression: empirically, companies growing >40% decelerate
                # ~50% per year (McKinsey). Linear 0.60x scaling over-extrapolates extreme
                # growth. Log scaling keeps moderate growth (~15-25%) mostly intact while
                # compressing hypergrowth (60%+) to realistic deceleration rates.
                if premium > 0:
                    max_premium = min(0.55 * math.log(1 + trailing_growth), 0.40)
                    # Revenue-size dampening: larger revenue bases decelerate faster.
                    # $50M → 1.0x, $500M → 0.85x, $5B → 0.65x, $50B → 0.50x
                    if rev > 50e6:
                        size_factor = max(1.0 - 0.12 * math.log10(rev / 50e6), 0.50)
                        max_premium *= size_factor
                else:
                    max_premium = 0
                premium = max(-0.20, min(premium, max_premium))

            # HIGH-MARGIN CAP: When FCF margin > 35%, revenue growth overstates
            # FCF growth because there's little margin expansion left. A company
            # at 54% FCF margin growing revenue 25% won't grow FCF 25% — the
            # incremental FCF margin converges to the blended margin, not higher.
            # Scale down the premium proportionally to how exhausted margins are.
            fcf_margin = fcf / rev if rev > 0 and fcf > 0 else 0
            if fcf_margin > 0.35 and premium > 0.05:
                # At 35% margin: no reduction. At 55%: ~50% reduction.
                margin_dampening = min((fcf_margin - 0.35) / 0.30, 0.60)
                premium *= (1 - margin_dampening)

            # Fade growth more slowly when margins are expanding (durable growth signal)
            fade_speed = 5 if abs(premium) < 0.15 else 3
            if margin_expanding and trailing_growth > 0.10:
                fade_speed = min(fade_speed + 2, 8)  # slower fade for expanding-margin growers
            if abs(premium) > 0.02:
                path = [g + premium * max(0, 1 - i / fade_speed) for i, g in enumerate(path)]

        # For stable compounders, floor the growth path at ~trailing growth
        # A company growing at 5% reliably shouldn't decelerate to 2% in projections
        if is_stable_compounder and trailing_growth is not None:
            floor_g = trailing_growth * 0.65  # floor at ~65% of trailing (allows some decel)
            floor_g = max(floor_g, 0.015)  # never below 1.5%
            path = [max(g, floor_g) for g in path]

        # Apply quality-adjusted terminal multiple using Gordon Growth Model
        # The template's terminal value is now used as a relative hint:
        # - Base scenario template terminal → scenario_hint = 1.0
        # - Bull scenarios with higher template terminals → hint > 1.0  
        # - Bear scenarios with lower template terminals → hint < 1.0
        base_terminal = templates[2][3]  # base case template terminal
        if base_terminal > 0:
            scenario_hint = terminal / base_terminal  # ratio to base
        else:
            scenario_hint = 1.0
        # Compute base terminal from Gordon Growth (scenario-adjusted growth rate)
        adj_terminal = compute_terminal_multiple(wacc, sector, scenario_hint)
        # Apply quality multiplier AFTER Gordon Growth (linear, predictable impact)
        # This avoids the non-linearity of scaling the growth input, where
        # the same multiplier has wildly different effects at different WACC levels
        adj_terminal = max(6, min(adj_terminal * quality_tm_multiplier, 
                                   adj_terminal * 1.5))  # cap at 50% premium
        # Hard cap at sector terminal maximum to prevent runaway valuations
        adj_terminal = min(adj_terminal, TERMINAL_CAPS.get(sector, 20))

        # Apply margin factor to FCF
        effective_fcf = fcf * margin_factor

        # ── Determine DCF routing ──
        # Key principle: companies with significant net debt must use EV-based DCF
        # (dcf_ev) which subtracts net debt from enterprise value to get equity value.
        # Only companies with net cash surplus use dcf_fcf (which adds cash to equity).
        # EXCEPTION: Banks and insurers — their "debt" is operational (deposits, reserves)
        # and should NOT be subtracted from enterprise value.
        use_ev_dcf = (nd > mcap * 0.10 and sector not in ('bank', 'insurance', 'specialty_lender'))

        if use_rev_margin:
            # Pre-profit revenue-margin expansion DCF
            adj_ebitda_margin = (ni + da) / rev if (ni + da) > 0 else 0
            cur_margin = max(min(adj_ebitda_margin, fcf / rev if fcf > 0 else 0), 0.02)
            tgt_margin = TARGET_MARGINS.get(sector, 0.12) * margin_factor
            fv = dcf_rev_margin(rev, path, cur_margin, tgt_margin, adj_terminal, wacc, shares, max(nc, 0))
        elif use_hybrid:
            # ── HYBRID: blend FCF-DCF and revenue-margin-DCF ──
            if use_ev_dcf:
                fcff = _compute_fcff(oi, da, maint_capex, ebitda, effective_fcf, sector, margin_factor)
                fv_fcf = dcf_ev(fcff, path, adj_terminal, wacc, shares, max(nd, 0))
            else:
                fv_fcf = dcf_fcf(effective_fcf, path, adj_terminal, wacc, shares, max(nc, 0))

            # Revenue-margin component: models margin expansion from current to target
            start_m = max(cur_fcf_margin, 0.02)
            # Target: use cash NI margin as intermediate target, sector margin as terminal
            intermediate_target = min(cash_ni_margin * 0.85, TARGET_MARGINS.get(sector, 0.12) * 1.5)
            tgt_m = max(TARGET_MARGINS.get(sector, 0.12), intermediate_target) * margin_factor
            fv_rev = dcf_rev_margin(rev, path, start_m, tgt_m, adj_terminal, wacc, shares, max(nc, 0))

            # Blend
            fv = fv_fcf * fcf_weight + fv_rev * rev_weight
        elif use_ev_dcf:
            # EV-based DCF for any company with significant net debt
            fcff = _compute_fcff(oi, da, maint_capex, ebitda, effective_fcf, sector, margin_factor)
            fv = dcf_ev(fcff, path, adj_terminal, wacc, shares, max(nd, 0))
        else:
            fv = dcf_fcf(effective_fcf, path, adj_terminal, wacc, shares, max(nc, 0))

        upside = (fv - price) / price * 100 if price > 0 else 0
        pw_fv += prob * fv
        scen_results.append({'name': name, 'prob': prob, 'fv': round(fv, 2), 'upside': round(upside, 1)})

    pw_up = (pw_fv - price) / price * 100 if price > 0 else 0

    # ══════════════════════════════════════════════
    #  BEYOND-DCF LAYERS
    # ══════════════════════════════════════════════

    # ── 1. Market-Implies Analysis ──
    # What revenue CAGR + terminal margin does the current price require?
    market_implies = None
    if rev > 0 and price > 0 and shares > 0:
        target_ev = mcap + nd
        if target_ev > 0:
            # Solve for what growth rate the market needs at sector target margin
            tgt_m = TARGET_MARGINS.get(sector, 0.12)
            base_tm = compute_terminal_multiple(wacc, sector, 1.0)  # base scenario Gordon Growth terminal
            def _mi_pv(g):
                r = rev; s = 0
                for t in range(1, 11):
                    r *= (1 + g)
                    # Linear margin ramp from current to target
                    cm = max(fcf / rev if rev > 0 and fcf > 0 else 0.02, 0.02)
                    m = cm + (tgt_m - cm) * (t / 10)
                    s += r * m / (1 + wacc) ** t
                return s + r * tgt_m * base_tm / (1 + wacc) ** 10 - target_ev
            lo, hi = -0.20, 1.50
            if _mi_pv(lo) * _mi_pv(hi) < 0:
                for _ in range(60):
                    mid = (lo + hi) / 2
                    if _mi_pv(mid) > 0: hi = mid
                    else: lo = mid
                implied_cagr = (lo + hi) / 2
                implied_rev_10y = rev * (1 + implied_cagr) ** 10
                market_implies = {
                    'implied_rev_cagr': round(implied_cagr * 100, 1),
                    'implied_rev_10y': implied_rev_10y,
                    'assumed_margin': round(tgt_m * 100, 1),
                    'implied_ps_now': round(mcap / rev, 1) if rev > 0 else None,
                }
            elif _mi_pv(hi) < 0:
                market_implies = {
                    'implied_rev_cagr': '>150%',
                    'implied_rev_10y': None,
                    'assumed_margin': round(tgt_m * 100, 1),
                    'implied_ps_now': round(mcap / rev, 1) if rev > 0 else None,
                }

    # ── 2. Asset-Based Floor ──
    # PP&E + cash - debt gives a liquidation floor for asset-heavy companies
    asset_floor = None
    ta = fins.get('total_assets', 0) or 0
    tl = fins.get('total_liabilities', 0) or 0
    if ta > 0 and shares > 0:
        # Book value approach
        book_value = equity if equity > 0 else (ta - tl)
        book_per_share = book_value / shares if book_value > 0 else 0
        # Tangible: discount intangibles (assume 30% of assets above equity are intangibles)
        tangible_floor = max(book_value * 0.7, 0) / shares if book_value > 0 else 0
        asset_floor = {
            'book_per_share': round(book_per_share, 2),
            'tangible_floor': round(tangible_floor, 2),
            'book_to_price': round(book_per_share / price * 100, 1) if price > 0 else None,
            'total_assets': ta,
        }

    # ── 3. EV/Revenue Multiple ──
    ev_rev = None
    if rev > 0 and ev > 0:
        ev_rev_now = ev / rev
        # Sector median EV/Rev benchmarks (approximate)
        sector_ev_rev = {
            'hyperscaler': 8.0, 'saas_tech': 6.0, 'pharma': 4.0, 'fintech': 5.0, 'payment_network': 16.0,
            'consumer': 2.0, 'industrial': 2.0, 'ep': 2.5, 'midstream': 3.0,
            'utility': 3.0, 'telecom': 2.5, 'insurance': 1.5, 'bank': 3.0, 'specialty_lender': 1.5,
            'aero_defense': 2.0, 'data_analytics': 10.0, 'general': 2.5
        }
        median = sector_ev_rev.get(sector, 2.5)
        ev_rev = {
            'current': round(ev_rev_now, 2),
            'sector_median': median,
            'premium_pct': round((ev_rev_now / median - 1) * 100, 1),
            'at_median_price': round((median * rev - nd) / shares, 2) if shares > 0 else None,
        }

    # ── Sanity flags ──
    sanity_flags = []
    if rev > 0 and pw_fv > 0:
        implied_ps = (pw_fv * shares) / rev
        if implied_ps < 1.0 and ni > 0 and (ni / rev) > 0.15:
            sanity_flags.append(f'FV implies {implied_ps:.1f}x rev on {ni/rev:.0%} margin')
        if implied_ps > 50:
            sanity_flags.append(f'FV implies {implied_ps:.0f}x rev (extreme)')
        if all(s['upside'] > 20 for s in scen_results):
            sanity_flags.append('All scenarios bullish — model may be overly optimistic')
        if all(s['upside'] < -20 for s in scen_results):
            sanity_flags.append('All scenarios bearish — model may miss value drivers')
    if use_rev_margin:
        sanity_flags.append('Pre-profit: using revenue×margin expansion DCF')
    if use_hybrid:
        sanity_flags.append(f'Hybrid DCF: {fcf_weight:.0%} FCF + {rev_weight:.0%} rev-margin')
    if amort_intangible > 0:
        sanity_flags.append(f'Intangible amort addback: ${amort_intangible/1e6:.0f}M/yr')
    if wc_adjustment > 0:
        sanity_flags.append(f'WC normalized: ${wc_adjustment/1e6:.0f}M inventory build')
    if capex_waste > 0 and growth_capex > 0:
        sanity_flags.append(f'CapEx conversion: {(1-waste_pct)*100:.0f}% of ${growth_capex/1e9:.0f}B growth capex credited')

    # ── IMPROVEMENT 2: Relative valuation comparables check ──
    # Compute P/E and EV/EBITDA implied fair values as sanity check.
    # Flag when DCF diverges significantly from relative valuation.
    SECTOR_PE = {
        'hyperscaler': 32, 'saas_tech': 35, 'pharma': 16, 'fintech': 28, 'payment_network': 37,
        'consumer': 25, 'industrial': 20, 'ep': 12, 'midstream': 12,
        'utility': 20, 'telecom': 10, 'insurance': 11, 'bank': 12, 'specialty_lender': 8,
        'aero_defense': 22, 'data_analytics': 28, 'general': 18
    }
    SECTOR_EV_EBITDA = {
        'hyperscaler': 22, 'saas_tech': 25, 'pharma': 14, 'fintech': 20, 'payment_network': 30,
        'consumer': 15, 'industrial': 14, 'ep': 7, 'midstream': 9,
        'utility': 14, 'telecom': 7, 'insurance': 10, 'bank': 12, 'specialty_lender': 8,
        'aero_defense': 16, 'data_analytics': 22, 'general': 12
    }
    comps_check = None
    eps_dil = fins.get('eps_diluted', ni / shares if (ni and shares > 0) else 0)
    if eps_dil and abs(eps_dil) > 0.01 and sector not in ('bank',):
        spe_static = SECTOR_PE.get(sector, 18)
        sev_static = SECTOR_EV_EBITDA.get(sector)

        # Use live comps if available (same blending logic as valuation_comps)
        live_comps = fins.get('_live_comps')
        WEAK_PEER_SECTORS = {'payment_network', 'data_analytics', 'fintech', 'specialty_lender', 'bdc'}
        if live_comps and live_comps.get('pe'):
            lpe = live_comps['pe']
            lev = live_comps.get('ev_ebitda')
            pc = live_comps.get('peer_count', 0)
            if sector in WEAK_PEER_SECTORS:
                spe = round(lpe * 0.4 + spe_static * 0.6, 1)
                sev = round((lev or sev_static) * 0.4 + (sev_static or 12) * 0.6, 1) if sev_static else sev_static
            elif pc >= 5:
                spe = round(lpe, 1)
                sev = round(lev, 1) if lev else sev_static
            else:
                spe = round(lpe * 0.6 + spe_static * 0.4, 1)
                sev = round((lev or sev_static) * 0.6 + (sev_static or 12) * 0.4, 1) if sev_static else sev_static
            comps_source = f"live {pc}p"
        else:
            spe = spe_static
            sev = sev_static
            comps_source = "static"

        pe_fv = eps_dil * spe
        evebitda_fv = None
        if sev and ebitda > 0 and shares > 0:
            evebitda_fv = round((ebitda * sev - nd) / shares, 2)
        comps_check = {
            'pe_fv': round(pe_fv, 2),
            'sector_pe': spe,
            'sector_pe_static': spe_static,
            'comps_source': comps_source,
            'current_pe': round(price / eps_dil, 1) if eps_dil > 0 else None,
            'evebitda_fv': evebitda_fv,
            'sector_evebitda': sev,
        }
        # Flag large divergence between DCF and comparables
        avg_comp = pe_fv
        if evebitda_fv and evebitda_fv > 0:
            avg_comp = (pe_fv + evebitda_fv) / 2
        if avg_comp > 0 and pw_fv > 0:
            dcf_vs_comps = (pw_fv - avg_comp) / avg_comp
            if dcf_vs_comps > 0.50:
                sanity_flags.append(f'DCF {dcf_vs_comps*100:+.0f}% vs comps (${avg_comp:.0f}) — check assumptions')
            elif dcf_vs_comps < -0.40:
                sanity_flags.append(f'DCF {dcf_vs_comps*100:+.0f}% vs comps (${avg_comp:.0f}) — may undervalue')

            # ── COMPS-ANCHORED FV ADJUSTMENT ──
            # When DCF is ABOVE comps, the model may be too optimistic on growth
            # compounding. Gently blend toward comps as a reality check.
            #
            # CRITICAL: Only anchor DOWN (DCF > comps), never UP.
            # Pulling UP when DCF < comps would penalize genuinely cheap stocks
            # and reward sector-wide overvaluation (bubble anchoring).
            #
            # Skip for hypergrowth (>40% trailing) where P/E is misleading.
            is_hypergrowth = trailing_growth is not None and trailing_growth > 0.40
            if not is_hypergrowth and dcf_vs_comps > 0.40:
                divergence = dcf_vs_comps
                # Progressive blend: 0% at 40% divergence, up to 30% at extreme gaps
                comps_weight = min((divergence - 0.40) * 0.50, 0.30)
                old_pw_fv = pw_fv
                pw_fv = pw_fv * (1 - comps_weight) + avg_comp * comps_weight
                pw_up = (pw_fv - price) / price * 100 if price > 0 else 0
                if comps_weight > 0.03:
                    sanity_flags.append(f'FV anchored {comps_weight*100:.0f}% toward comps (${old_pw_fv:.0f}→${pw_fv:.0f})')

    # ── IMPROVEMENT 4: Earnings growth divergence flag ──
    # Compare revenue growth to earnings growth direction
    if trailing_growth is not None and ni > 0 and rev > 0:
        ni_margin = ni / rev
        # If we had prior-year NI we could compute directly; approximate from EPS
        prior_eps = fins.get('eps_diluted_prior')
        if prior_eps and eps_dil and abs(prior_eps) > 0.01:
            eps_growth = (eps_dil - prior_eps) / abs(prior_eps)
            if eps_growth > trailing_growth + 0.10:
                sanity_flags.append(f'Operating leverage: EPS +{eps_growth*100:.0f}% vs Rev +{trailing_growth*100:.0f}%')
            elif eps_growth < trailing_growth - 0.10 and eps_growth < 0:
                sanity_flags.append(f'Margin pressure: EPS {eps_growth*100:+.0f}% vs Rev +{trailing_growth*100:.0f}%')

    if pw_up > 30: verdict = 'SIGNIFICANTLY UNDERVALUED'
    elif pw_up > 10: verdict = 'UNDERVALUED'
    elif pw_up > -10: verdict = 'FAIR VALUE'
    elif pw_up > -25: verdict = 'OVERVALUED'
    else: verdict = 'SIGNIFICANTLY OVERVALUED'

    # Implied growth rates
    base_tm = compute_terminal_multiple(wacc, sector, 1.0)
    implied_fcf = solve_implied_growth(fcf, price, shares, nc, wacc, base_tm)
    implied_rev = solve_implied_rev_growth(rev, fcf, price, shares, nc, wacc, base_tm, sector)

    # ── Projected Price Paths (10-year) ──
    # The DCF fair value is what the stock is worth TODAY. The 10-year projection
    # shows where intrinsic value goes from here, growing at each scenario's
    # FCF growth rates. This answers "if I buy at fair value, what's it worth in 10 years?"
    #
    # Year 0 = scenario FV (today's intrinsic value)
    # Years 1-10 = FV compounding at the scenario's weighted-average growth rate
    # Current market price shown as reference line
    price_paths = None
    if fcf > 0 and shares > 0 and price > 0 and not (sector in ('bank', 'specialty_lender') or fins.get('_is_bdc')):
        price_paths = {'years': list(range(0, 11)), 'scenarios': [], 'pw_path': [pw_fv], 'market_price': price}
        pw_path_accum = [0.0] * 10

        for idx, (name, _dp, path_template, terminal, margin_factor) in enumerate(templates):
            sc = scen_results[idx]
            fv_0 = sc['fv']  # This scenario's fair value TODAY
            prob = sc['prob']
            path = list(path_template)

            # Re-apply same growth anchoring as main DCF
            if trailing_growth is not None:
                premium_raw = trailing_growth - sector_base_y1
                if premium_raw < 0 and is_mature_compounder:
                    premium_raw = max(premium_raw, -0.02)
                else:
                    if premium_raw > 0:
                        max_prem = min(0.55 * math.log(1 + trailing_growth), 0.40)
                        if rev > 50e6:
                            size_factor = max(1.0 - 0.12 * math.log10(rev / 50e6), 0.50)
                            max_prem *= size_factor
                    else:
                        max_prem = 0
                    premium_raw = max(-0.20, min(premium_raw, max_prem))
                fcf_m = fcf / rev if rev > 0 and fcf > 0 else 0
                if fcf_m > 0.35 and premium_raw > 0.05:
                    md = min((fcf_m - 0.35) / 0.30, 0.60)
                    premium_raw *= (1 - md)
                fade_spd = 5 if abs(premium_raw) < 0.15 else 3
                if margin_expanding and trailing_growth and trailing_growth > 0.10:
                    fade_spd = min(fade_spd + 2, 8)
                if abs(premium_raw) > 0.02:
                    path = [g + premium_raw * max(0, 1 - i / fade_spd) for i, g in enumerate(path)]
            if is_stable_compounder and trailing_growth is not None:
                fl = max(trailing_growth * 0.65, 0.015)
                path = [max(g, fl) for g in path]

            # Project FV forward: today's FV growing at scenario growth + WACC return
            # Investor return = FCF growth + multiple stability (approx WACC for fairly valued stock)
            # Simplified: FV compounds at the scenario's avg FCF growth rate + a base return
            avg_growth = sum(path[:5]) / 5  # weighted toward near-term
            # Price appreciation ≈ FCF growth (at stable multiples)
            yearly = [round(fv_0, 2)]
            cumulative = fv_0
            for yr in range(10):
                # Growth decelerates over time (use actual path rates)
                yr_growth = path[yr] if yr < len(path) else path[-1]
                cumulative *= (1 + yr_growth)
                yearly.append(round(max(cumulative, 0), 2))

            price_paths['scenarios'].append({
                'name': sc['name'], 'prob': prob, 'path': yearly
            })
            for yr in range(10):
                pw_path_accum[yr] += prob * yearly[yr + 1]

        price_paths['pw_path'] = [round(pw_fv, 2)] + [round(p, 2) for p in pw_path_accum]

    return {
        'price': price, 'pw_fv': round(pw_fv, 2), 'pw_up': round(pw_up, 1),
        'verdict': verdict, 'wacc': round(wacc, 4),
        'scenarios': scen_results, 'capex': capex_der,
        'trailing_growth': trailing_growth, 'implied_fcf_growth': implied_fcf,
        'implied_rev_growth': implied_rev,
        'sanity_flags': sanity_flags,
        'is_buyback_machine': is_buyback_machine if equity < 0 else False,
        'sbc_haircut': sbc_haircut,
        'dynamic_probs': probs != [t[1] for t in templates],
        'use_rev_margin': use_rev_margin,
        'use_hybrid': use_hybrid,
        'price_paths': price_paths,
        # Beyond-DCF layers
        'market_implies': market_implies,
        'asset_floor': asset_floor,
        'ev_rev_multiple': ev_rev,
        'comps_check': comps_check,
        'inputs': {
            'revenue': rev, 'net_income': ni, 'cash_ni': cash_ni, 'fcf': fcf, 'ebitda': ebitda,
            'operating_cf': ocf, 'normalized_ocf': normalized_ocf, 'capex': capex, 'depreciation': da,
            'amort_intangible': amort_intangible, 'sbc': sbc,
            'cash': cash, 'debt': debt, 'equity': equity, 'shares': shares,
            'fcf_reported': capex_der['fcf_reported'] if capex_der else (ocf - capex if ocf and capex else None),
            'capex_method': 'Rev×Margin DCF' if use_rev_margin else (capex_der['method'] if capex_der else '—'),
            'wc_adjustment': wc_adjustment,
            'hybrid_weights': f'{fcf_weight:.0%}/{rev_weight:.0%}' if use_hybrid else None,
        }
    }


# ════════════════════════════════════════════════════════
#  ADDITIONAL VALUATION MODELS
# ════════════════════════════════════════════════════════

def valuation_epv(fins, wacc, shares_mil, sector):
    """Earnings Power Value: no-growth capitalized earnings.
    Represents the value of the company if it never grows, just maintains current earnings.
    Serves as a conservative floor valuation.
    """
    shares = shares_mil * 1e6
    if shares <= 0 or wacc <= 0:
        return None

    ni = fins.get('net_income', 0) or 0
    oi = fins.get('operating_income', 0) or 0
    da = fins.get('depreciation', 0) or 0
    capex = fins.get('capex', 0) or 0
    debt = fins.get('long_term_debt', 0) or 0
    cash = fins.get('cash', 0) or 0

    # NOPAT = Operating Income x (1 - tax rate)
    nopat = oi * 0.79 if oi > 0 else ni

    if nopat <= 0:
        return None

    # Maintenance capex: use D&A as proxy
    factor = CAPEX_FACTORS.get(sector, CAPEX_FACTORS['general'])
    if da > 0:
        maint = da * factor
        maint = min(maint, capex) if capex > 0 else maint
    elif capex > 0:
        maint = capex * min(factor, 0.80)
    else:
        maint = 0

    # Owner earnings = NOPAT + D&A - maintenance capex
    owner_earnings = nopat + da - maint
    if owner_earnings <= 0:
        owner_earnings = nopat * 0.8

    # Capitalize at WACC (no growth assumed)
    enterprise_value = owner_earnings / wacc

    # Equity value = EV - net debt
    equity_value = enterprise_value - debt + cash

    return max(equity_value / shares, 0)


def valuation_residual_income(fins, wacc, shares_mil, sector, years=10):
    """Residual Income Model: Book Value + PV of future excess earnings.
    
    V0 = BV0 + Sum(RI_t / (1+r)^t) + Terminal RI
    where RI_t = NI_t - (r x BV_{t-1})
    
    For high-ROE companies (>40%), the model caps projected ROE at 4x COE
    and applies faster moat decay. This prevents unrealistic book value explosion
    for capital-light businesses where ROE reflects intangible/IP value rather
    than deployable capital returns. The confidence scorer gives these estimates
    very low blend weight, so they serve as a sanity-check reference rather than
    a primary valuation driver.
    """
    shares = shares_mil * 1e6
    bv = fins.get('stockholders_equity', 0) or 0
    ni = fins.get('net_income', 0) or 0

    if bv <= 0 or ni <= 0 or shares <= 0 or wacc <= 0:
        return None

    roe = ni / bv
    if roe < 0.01 or roe > 1.50:
        return None  # Only exclude truly nonsensical ROE (negative equity / extreme)

    # Approximate COE from WACC
    debt = fins.get('long_term_debt', 0) or 0
    equity_val = bv
    if equity_val > 0 and debt > 0:
        debt_ratio = debt / (equity_val + debt)
        cod_after_tax = 0.052 * 0.79
        coe = (wacc - cod_after_tax * debt_ratio) / max(1 - debt_ratio, 0.3)
        coe = max(min(coe, 0.20), 0.04)
    else:
        coe = wacc

    # For high-ROE companies, cap the projected starting ROE
    # Rationale: extremely high ROE cannot be sustainably deployed on a growing capital base.
    # The excess above 4x COE reflects intangible/IP value, not replicable returns on capital.
    if roe > coe * 4.0:
        projected_start_roe = coe * 4.0  # e.g., 36% if COE=9%
    else:
        projected_start_roe = roe

    # Retention ratio by sector — cap for high-ROE (buyback-heavy companies)
    retention = 0.60
    if sector in ('utility', 'telecom', 'midstream', 'ep'):
        retention = 0.40
    elif sector in ('saas_tech', 'hyperscaler', 'pharma'):
        retention = 0.80
    elif sector in ('bank',):
        retention = 0.45
    # For very high ROE, cap retention — these companies return capital via buybacks
    if roe > 0.40:
        retention = min(retention, 0.50)

    # Dynamic moat decay: faster mean-reversion for higher ROE
    # Base moat factors by sector
    base_moat = {'data_analytics': 0.85, 'hyperscaler': 0.80, 'saas_tech': 0.75,
                 'consumer': 0.70, 'pharma': 0.65, 'fintech': 0.70,
                 'aero_defense': 0.75, 'utility': 0.80,
                 'general': 0.60}.get(sector, 0.60)
    # Accelerate decay when ROE is far above normal (competitive advantages erode faster
    # at extreme levels — reversion to the mean is stronger)
    if roe > 0.30:
        roe_excess = min((roe - 0.30) / 0.50, 1.0)  # normalized [0, 1]
        moat_factor = base_moat * (1 - 0.25 * roe_excess)
    else:
        moat_factor = base_moat

    bv_current = bv
    pv_ri = 0
    ri = 0

    for yr in range(1, years + 1):
        decay = moat_factor ** (yr / years)
        projected_roe = coe + (projected_start_roe - coe) * decay
        projected_roe = max(projected_roe, coe * 0.5)

        earnings = bv_current * projected_roe
        equity_charge = bv_current * coe
        ri = earnings - equity_charge
        pv_ri += ri / (1 + coe) ** yr

        bv_current += earnings * retention

    # Terminal residual income with persistence factor
    persistence = {'data_analytics': 0.60, 'hyperscaler': 0.55, 'saas_tech': 0.50,
                   'consumer': 0.45, 'pharma': 0.40, 'fintech': 0.45, 'payment_network': 0.65,
                   'aero_defense': 0.45, 'utility': 0.50,
                   'general': 0.35}.get(sector, 0.35)
    
    terminal_g = TERMINAL_GROWTH.get(sector, 0.025) * 0.8
    terminal_g = min(terminal_g, coe - 0.01)
    terminal_ri = ri * persistence / max(coe - terminal_g, 0.01)
    pv_terminal = terminal_ri / (1 + coe) ** years

    intrinsic = bv + pv_ri + pv_terminal
    return max(intrinsic / shares, 0)


def valuation_comps(fins, sector, shares_mil, live_comps=None):
    """Comparable company multiples valuation.
    
    Uses LIVE peer comps from EDGAR XBRL when available (fetched via fetch_live_comps),
    falling back to hardcoded sector medians if live data is unavailable or stale.
    
    Applies growth-adjusted multiples when trailing growth materially exceeds
    sector norms, so high-growth outliers aren't penalized by average-company multiples.
    """
    shares = shares_mil * 1e6
    if shares <= 0:
        return None

    ni = fins.get('net_income', 0) or 0
    oi = fins.get('operating_income', 0) or 0
    da = fins.get('depreciation', 0) or 0
    debt = fins.get('long_term_debt', 0) or 0
    cash = fins.get('cash', 0) or 0
    rev = fins.get('revenue', 0) or 0
    ebitda = oi + da if (oi > 0 and da > 0) else 0
    nd = debt - cash

    # Hardcoded fallback multiples
    SECTOR_PE = {
        'hyperscaler': 32, 'saas_tech': 35, 'pharma': 16, 'fintech': 28, 'payment_network': 37,
        'consumer': 25, 'industrial': 20, 'ep': 12, 'midstream': 12,
        'utility': 20, 'telecom': 10, 'insurance': 11, 'bank': 12, 'specialty_lender': 8,
        'aero_defense': 22, 'data_analytics': 28, 'general': 18
    }
    SECTOR_EV_EBITDA = {
        'hyperscaler': 22, 'saas_tech': 25, 'pharma': 14, 'fintech': 20, 'payment_network': 30,
        'consumer': 15, 'industrial': 14, 'ep': 7, 'midstream': 9,
        'utility': 14, 'telecom': 7, 'insurance': 10, 'bank': 12, 'specialty_lender': 8,
        'aero_defense': 16, 'data_analytics': 22, 'general': 12
    }

    # Use live comps if available, otherwise hardcoded
    # For sectors with poor peer comparability, blend live + hardcoded
    WEAK_PEER_SECTORS = {'payment_network', 'data_analytics', 'fintech', 'specialty_lender', 'bdc'}
    spe_hardcoded = SECTOR_PE.get(sector, 18)
    sev_hardcoded = SECTOR_EV_EBITDA.get(sector, 12)

    if live_comps and live_comps.get('pe'):
        peer_count = live_comps.get('peer_count', 0)
        if sector in WEAK_PEER_SECTORS:
            # Blend: 40% live, 60% hardcoded (peers aren't truly comparable)
            spe_base = live_comps['pe'] * 0.4 + spe_hardcoded * 0.6
            sev_live = live_comps.get('ev_ebitda') or sev_hardcoded
            sev_base = sev_live * 0.4 + sev_hardcoded * 0.6
        elif peer_count >= 5:
            # Strong peer group: use live
            spe_base = live_comps['pe']
            sev_base = live_comps.get('ev_ebitda') or sev_hardcoded
        else:
            # Thin peer group: 60% live, 40% hardcoded
            spe_base = live_comps['pe'] * 0.6 + spe_hardcoded * 0.4
            sev_live = live_comps.get('ev_ebitda') or sev_hardcoded
            sev_base = sev_live * 0.6 + sev_hardcoded * 0.4
    else:
        spe_base = spe_hardcoded
        sev_base = sev_hardcoded

    # Typical sector growth rates (used to compute PEG-style adjustments)
    SECTOR_GROWTH = {
        'hyperscaler': 0.15, 'saas_tech': 0.18, 'pharma': 0.06, 'fintech': 0.15, 'payment_network': 0.11,
        'consumer': 0.06, 'industrial': 0.05, 'ep': 0.03, 'midstream': 0.04,
        'utility': 0.04, 'telecom': 0.02, 'insurance': 0.04, 'bank': 0.05, 'specialty_lender': 0.04,
        'aero_defense': 0.05, 'data_analytics': 0.12, 'general': 0.06
    }

    # Compute trailing growth for PEG adjustment
    trailing_g = None
    rev_prior = fins.get('revenue_prior', 0)
    if rev_prior and rev_prior > 0 and rev > 0:
        trailing_g = (rev - rev_prior) / rev_prior

    # Growth-adjust multiples
    sector_g = SECTOR_GROWTH.get(sector, 0.06)
    growth_adj = 1.0
    if trailing_g is not None and trailing_g > sector_g * 1.5:
        relative_growth = trailing_g / max(sector_g, 0.01)
        adj_factor = math.sqrt(relative_growth)
        growth_adj = 1.0 + min((adj_factor - 1.0) * 0.4, 0.6)

    estimates = []

    if ni > 0:
        spe = spe_base * growth_adj
        estimates.append((ni * spe) / shares)

    if ebitda > 0:
        sev = sev_base * growth_adj
        equity_val = ebitda * sev - nd
        if equity_val > 0:
            estimates.append(equity_val / shares)

    if not estimates:
        return None

    return sum(estimates) / len(estimates)

def valuation_ev_revenue(fins, sector, shares_mil, wacc):
    """EV/Revenue with normalized margin valuation.
    
    For early-profit or pre-profit companies where P/E and EV/EBITDA are unreliable
    (thin margins), this estimates value by applying sector target FCF margins to
    current revenue, then capitalizing at a sector-appropriate EV/Revenue multiple.
    
    Logic: "If this company reaches mature margins, what is the implied equity value
    at a fair EV/Revenue multiple?"
    
    Most useful when: net margin < 10%, revenue growing > 15%, or EBITDA is negative/thin.
    """
    shares = shares_mil * 1e6
    if shares <= 0:
        return None
    
    rev = fins.get('revenue', 0) or 0
    ni = fins.get('net_income', 0) or 0
    debt = fins.get('long_term_debt', 0) or 0
    cash = fins.get('cash', 0) or 0
    nd = debt - cash
    
    if rev <= 0:
        return None
    
    net_margin = ni / rev if ni > 0 else 0
    
    # Sector target FCF margins at maturity
    SECTOR_TARGET_MARGIN = {
        'hyperscaler': 0.22, 'saas_tech': 0.25, 'pharma': 0.20, 'fintech': 0.18,
        'payment_network': 0.50, 'consumer': 0.08, 'industrial': 0.10,
        'ep': 0.15, 'midstream': 0.20, 'utility': 0.12, 'telecom': 0.12,
        'insurance': 0.10, 'bank': 0.25, 'specialty_lender': 0.20, 'aero_defense': 0.10,
        'data_analytics': 0.25, 'general': 0.10,
    }
    
    # Sector EV/Revenue multiples (at mature margins)
    SECTOR_EV_REV = {
        'hyperscaler': 8.0, 'saas_tech': 7.0, 'pharma': 4.0, 'fintech': 5.0,
        'payment_network': 16.0, 'consumer': 2.0, 'industrial': 2.0,
        'ep': 2.5, 'midstream': 3.5, 'utility': 3.5, 'telecom': 2.5,
        'insurance': 1.5, 'bank': 3.0, 'specialty_lender': 1.5, 'aero_defense': 2.5,
        'data_analytics': 10.0, 'general': 2.5,
    }
    
    target_margin = SECTOR_TARGET_MARGIN.get(sector, 0.10)
    sector_ev_rev = SECTOR_EV_REV.get(sector, 2.5)
    
    # Trailing growth for forward revenue estimate
    rev_prior = fins.get('revenue_prior', 0)
    trailing_g = (rev - rev_prior) / rev_prior if rev_prior and rev_prior > 0 else 0
    
    # Project revenue 3 years forward at decelerating growth
    g1 = min(trailing_g, 0.50) if trailing_g > 0 else 0.05
    g2 = g1 * 0.80
    g3 = g2 * 0.80
    fwd_rev = rev * (1 + g1) * (1 + g2) * (1 + g3)
    
    # Margin ramp: blend current margin toward target over 3 years
    # But if current margin already exceeds target, use current (already mature)
    current_fcf_margin = max(net_margin, 0.0)
    ramp_margin = current_fcf_margin * 0.3 + target_margin * 0.7
    blended_margin = max(ramp_margin, current_fcf_margin)
    
    # Normalized FCF at maturity
    normalized_fcf = fwd_rev * blended_margin
    if normalized_fcf <= 0:
        return None
    
    # Value via capitalized normalized earnings (Gordon Growth exit)
    tg = TERMINAL_GROWTH.get(sector, 0.025)
    if wacc <= tg:
        return None
    exit_multiple = (1 + tg) / (wacc - tg)
    exit_multiple = min(exit_multiple, 35)  # cap
    
    # Discount back 3 years
    ev = normalized_fcf * exit_multiple / (1 + wacc) ** 3
    equity = ev - nd
    
    # Also compute EV/Rev method as a cross-check
    ev_rev_val = rev * sector_ev_rev - nd
    
    # Blend: 60% normalized-margin DCF, 40% EV/Revenue
    blended_equity = equity * 0.60 + ev_rev_val * 0.40
    
    if blended_equity <= 0:
        return None
    
    return blended_equity / shares


def valuation_roic_fade(fins, wacc, shares_mil, sector, years=15):
    """ROIC Fade / Economic Profit Model (Morningstar-style).
    
    Projects invested capital and ROIC explicitly, with ROIC fading toward WACC
    over a moat-dependent period. Value = Invested Capital + PV(Economic Profits).
    
    Economic Profit = (ROIC - WACC) × Invested Capital
    
    This naturally handles:
    - Capital allocation quality (reinvestment rate × ROIC spread)
    - Moat durability (fade speed to WACC)
    - Growth companies deploying capital at high returns
    
    Key advantage over simple DCF: ties terminal value to competitive position,
    not arbitrary multiples.
    """
    shares = shares_mil * 1e6
    if shares <= 0 or wacc <= 0:
        return None
    
    ni = fins.get('net_income', 0) or 0
    oi = fins.get('operating_income', 0) or 0
    rev = fins.get('revenue', 0) or 0
    da = fins.get('depreciation', 0) or 0
    capex = fins.get('capex', 0) or 0
    debt = fins.get('long_term_debt', 0) or 0
    cash = fins.get('cash', 0) or 0
    equity = fins.get('stockholders_equity', 0) or 0
    
    # Invested Capital = Total Equity + Net Debt (operating capital base)
    nd = debt - cash
    invested_capital = equity + debt  # total capital deployed
    if invested_capital <= 0:
        return None
    
    # NOPAT (Net Operating Profit After Tax)
    nopat = oi * 0.79 if oi > 0 else ni
    if nopat <= 0:
        return None
    
    # ROIC = NOPAT / Invested Capital
    roic = nopat / invested_capital
    if roic < 0.01 or roic > 1.50:
        return None
    
    # Cap projected ROIC: extremely high ROICs (>50%) reflect intangible assets
    # not captured in book IC. Cap at 4x WACC for projections.
    projected_roic = min(roic, wacc * 4.0)
    
    # Moat-dependent fade: how many years until ROIC converges to WACC
    # Wide moat: slow fade (20+ years), narrow: medium (10-15), none: fast (5-8)
    MOAT_YEARS = {
        'payment_network': 20, 'data_analytics': 18, 'hyperscaler': 15,
        'saas_tech': 12, 'consumer': 12, 'pharma': 10, 'aero_defense': 12,
        'fintech': 10, 'industrial': 8, 'utility': 15, 'midstream': 12,
        'telecom': 8, 'ep': 6, 'insurance': 8, 'bank': 10, 'specialty_lender': 6, 'general': 8,
    }
    moat_years = MOAT_YEARS.get(sector, 8)
    
    # Reinvestment rate: fraction of NOPAT reinvested (rest returned to shareholders)
    REINVESTMENT_RATE = {
        'hyperscaler': 0.55, 'saas_tech': 0.50, 'pharma': 0.45, 'fintech': 0.50,
        'payment_network': 0.30, 'consumer': 0.40, 'industrial': 0.45,
        'ep': 0.50, 'midstream': 0.35, 'utility': 0.50, 'telecom': 0.45,
        'insurance': 0.35, 'bank': 0.40, 'specialty_lender': 0.35, 'aero_defense': 0.40,
        'data_analytics': 0.45, 'general': 0.40,
    }
    reinvest_rate = REINVESTMENT_RATE.get(sector, 0.40)
    
    # Trailing growth for initial growth calibration
    rev_prior = fins.get('revenue_prior', 0)
    trailing_g = (rev - rev_prior) / rev_prior if rev_prior and rev_prior > 0 else 0.05
    
    # Project invested capital and economic profits
    ic = invested_capital
    pv_economic_profit = 0
    
    for yr in range(1, years + 1):
        # ROIC fade: linear interpolation from current to WACC over moat_years
        fade_frac = min(yr / moat_years, 1.0)
        yr_roic = projected_roic + (wacc - projected_roic) * fade_frac
        yr_roic = max(yr_roic, wacc * 0.8)  # floor at 80% of WACC (some value destruction ok)
        
        # Economic profit = excess return × capital base
        economic_profit = (yr_roic - wacc) * ic
        pv_economic_profit += economic_profit / (1 + wacc) ** yr
        
        # Grow invested capital: reinvest fraction of NOPAT
        yr_nopat = ic * yr_roic
        reinvestment = yr_nopat * reinvest_rate
        
        # Decelerate reinvestment as ROIC approaches WACC (less incentive to invest)
        if yr_roic < wacc * 1.1:
            reinvestment *= 0.5  # sharply reduce investment when returns are thin
        
        ic += reinvestment
    
    # Terminal value: perpetuity of residual economic profit after fade
    # At the end of the projection, ROIC should be near WACC
    terminal_roic = projected_roic + (wacc - projected_roic) * min(years / moat_years, 1.0)
    terminal_ep = (terminal_roic - wacc) * ic
    tg = TERMINAL_GROWTH.get(sector, 0.025) * 0.7  # conservative terminal growth
    tg = min(tg, wacc - 0.01)
    
    if terminal_ep > 0 and wacc > tg:
        pv_terminal = terminal_ep / (wacc - tg) / (1 + wacc) ** years
    else:
        pv_terminal = 0
    
    # Intrinsic value = Current IC + PV(all future economic profits)
    intrinsic_ev = invested_capital + pv_economic_profit + pv_terminal
    
    # Equity value = EV - net debt
    equity_value = intrinsic_ev - nd
    
    if equity_value <= 0:
        return None
    
    return equity_value / shares


def valuation_ddm(fins, wacc, shares_mil, sector):
    """Dividend Discount Model for non-bank mature yielders.
    
    Applies to utilities, telecoms, midstream, REITs, consumer staples, and
    other sectors with stable, high payout ratios. Uses a 3-stage DDM:
    - Stage 1 (years 1-5): near-term growth at trailing rate (dampened)
    - Stage 2 (years 6-10): fade toward terminal growth
    - Stage 3: Gordon Growth perpetuity
    
    Total shareholder return = dividends + buybacks (estimated from payout ratio).
    """
    shares = shares_mil * 1e6
    if shares <= 0 or wacc <= 0:
        return None
    
    ni = fins.get('net_income', 0) or 0
    ocf = fins.get('operating_cf', 0) or 0
    rev = fins.get('revenue', 0) or 0
    equity = fins.get('stockholders_equity', 0) or 0
    
    if ni <= 0:
        return None
    
    # Only apply DDM to sectors with meaningful, stable payouts
    DDM_SECTORS = {
        'utility': 0.70, 'telecom': 0.65, 'midstream': 0.75, 'ep': 0.50,
        'consumer': 0.55, 'insurance': 0.50, 'industrial': 0.45,
        'aero_defense': 0.45, 'payment_network': 0.60,
    }
    
    if sector not in DDM_SECTORS:
        return None
    
    payout = DDM_SECTORS[sector]
    
    # Estimate total shareholder return (dividends + buybacks) as % of NI
    current_distribution = ni * payout
    eps = ni / shares
    dps = current_distribution / shares
    
    if dps <= 0:
        return None
    
    # Cost of equity: approximate from WACC
    debt = fins.get('long_term_debt', 0) or 0
    if equity > 0 and debt > 0:
        debt_ratio = debt / (equity + debt)
        cod_after_tax = 0.052 * 0.79
        coe = (wacc - cod_after_tax * debt_ratio) / max(1 - debt_ratio, 0.3)
        coe = max(min(coe, 0.16), 0.05)
    else:
        coe = wacc
    
    # Growth rates
    rev_prior = fins.get('revenue_prior', 0)
    trailing_g = (rev - rev_prior) / rev_prior if rev_prior and rev_prior > 0 else 0.03
    trailing_g = max(min(trailing_g, 0.20), -0.05)  # clamp
    
    tg = TERMINAL_GROWTH.get(sector, 0.025)
    
    # Stage 1 (years 1-5): dampened trailing growth
    g1 = trailing_g * 0.70  # don't fully extrapolate
    g1 = max(g1, tg)  # floor at terminal growth
    
    # Stage 2 (years 6-10): linear fade to terminal
    # Stage 3: Gordon Growth perpetuity
    
    pv = 0
    d = dps
    for yr in range(1, 11):
        if yr <= 5:
            g = g1
        else:
            # Linear fade from g1 to tg over years 6-10
            g = g1 + (tg - g1) * ((yr - 5) / 5)
        d *= (1 + g)
        pv += d / (1 + coe) ** yr
    
    # Terminal value
    d_terminal = d * (1 + tg)
    if coe > tg:
        tv = d_terminal / (coe - tg)
        pv += tv / (1 + coe) ** 10
    
    return max(pv, 0)


# ════════════════════════════════════════════════════════

def _estimate_model_confidence(model_name, fins, sector, data_quality):
    """Estimate confidence (precision proxy) for each model. [0.05, 0.95]."""
    if model_name == 'dcf':
        score = 0.50
        ocf = fins.get('operating_cf', 0) or 0
        ni = fins.get('net_income', 0) or 0
        if ni > 0 and ocf > 0:
            conversion = ocf / ni
            if 0.7 < conversion < 1.5: score += 0.12
            elif conversion < 0.3 or conversion > 3.0: score -= 0.10
        q_avail = data_quality.get('quarters_available', 1)
        if q_avail >= 4: score += 0.12
        elif q_avail >= 2: score += 0.06
        fcf_cv = data_quality.get('fcf_cv')
        if fcf_cv is not None:
            if fcf_cv < 0.20: score += 0.08
            elif fcf_cv > 0.50: score -= 0.08
        if sector in ('saas_tech', 'hyperscaler', 'consumer', 'industrial', 'data_analytics'):
            score += 0.06
        elif sector in ('bank', 'insurance', 'bdc', 'specialty_lender'):
            score -= 0.15
        return max(0.05, min(score, 0.95))

    elif model_name == 'residual_income':
        score = 0.45
        bv = fins.get('stockholders_equity', 0) or 0
        ni = fins.get('net_income', 0) or 0
        if bv > 0 and ni > 0:
            roe = ni / bv
            # RI works best for moderate-ROE businesses with meaningful book values
            if 0.05 < roe < 0.25: score += 0.15  # ideal range
            elif 0.25 <= roe < 0.40: score += 0.00  # decent but neutral
            elif 0.40 <= roe < 0.60: score -= 0.20  # ROE heavily capped, unreliable
            elif roe >= 0.60: score -= 0.35  # model is structurally inappropriate
            elif roe < 0.01: score -= 0.10
            # LOW ROE penalty: when ROE < 12%, excess earnings (ROE - COE) are negligible
            # and RI essentially returns book value. This is meaningless for growth companies
            # that are early in profitability — their value is in future earnings, not current BV.
            if 0.01 <= roe < 0.12:
                score -= 0.20  # RI is unreliable when spread is near zero
            # P/B penalty: RI is book-value-anchored, so it's structurally inappropriate
            # when the company is capital-light. Use revenue/BV as a proxy for capital intensity.
            rev = fins.get('revenue', 0) or 0
            if bv > 0 and rev > 0 and rev / bv > 3.0:
                # Revenue >> book value → capital-light business → RI less meaningful
                score -= min((rev / bv - 3.0) * 0.03, 0.15)
        if sector in ('bank', 'utility', 'industrial', 'aero_defense', 'midstream', 'ep', 'specialty_lender'):
            score += 0.12  # RI is well-suited for asset-heavy sectors
        elif sector in ('saas_tech', 'hyperscaler', 'pharma', 'fintech', 'payment_network'):
            score -= 0.10  # capital-light sectors where book value understates true capital
        if data_quality.get('quarters_available', 1) >= 4: score += 0.08
        return max(0.05, min(score, 0.95))

    elif model_name == 'comps':
        score = 0.35
        ebitda = (fins.get('operating_income', 0) or 0) + (fins.get('depreciation', 0) or 0)
        if ebitda > 0 and (fins.get('net_income', 0) or 0) > 0: score += 0.10
        if sector in ('utility', 'bank', 'telecom', 'midstream', 'ep', 'insurance', 'specialty_lender'):
            score += 0.12
        elif sector in ('pharma', 'saas_tech'): score -= 0.08
        return max(0.05, min(score, 0.95))

    elif model_name == 'ev_revenue':
        # EV/Revenue normalized margin: most useful for early-profit, high-growth companies
        # where P/E and EV/EBITDA are unreliable due to thin margins
        score = 0.15  # low base — only meaningful when margins are thin
        ni = fins.get('net_income', 0) or 0
        rev = fins.get('revenue', 0) or 0
        if rev > 0 and ni > 0:
            net_margin = ni / rev
            if net_margin < 0.05:
                score += 0.30  # very thin margins → EV/Rev is essential
            elif net_margin < 0.10:
                score += 0.20  # thin margins → useful supplement
            elif net_margin < 0.15:
                score += 0.10  # moderate margins → mild contribution
            else:
                score -= 0.05  # healthy margins → P/E and EV/EBITDA are better
        elif rev > 0 and ni <= 0:
            score += 0.35  # pre-profit → EV/Revenue is the primary comp
        # Growth bonus: more useful for fast-growing companies
        rev_prior = fins.get('revenue_prior', 0)
        if rev_prior and rev_prior > 0 and rev > 0:
            g = (rev - rev_prior) / rev_prior
            if g > 0.20: score += 0.08
        if sector in ('saas_tech', 'fintech', 'hyperscaler', 'data_analytics'):
            score += 0.05  # these sectors commonly valued on EV/Rev
        return max(0.05, min(score, 0.95))

    elif model_name == 'roic_fade':
        # ROIC fade / economic profit: works best when ROIC and IC are meaningful
        score = 0.25
        equity = fins.get('stockholders_equity', 0) or 0
        ni = fins.get('net_income', 0) or 0
        oi = fins.get('operating_income', 0) or 0
        rev = fins.get('revenue', 0) or 0
        debt = fins.get('long_term_debt', 0) or 0
        ic = equity + debt
        nopat = oi * 0.79 if oi > 0 else ni
        if ic > 0 and nopat > 0:
            roic = nopat / ic
            if 0.08 < roic < 0.25: score += 0.15  # ideal range
            elif 0.25 <= roic < 0.40: score += 0.05
            elif roic >= 0.40: score -= 0.15  # very high ROIC = intangible-driven
            elif roic < 0.05: score -= 0.10
            # IC/Revenue: capital-light businesses have IC << revenue
            if rev > 0 and ic / rev < 0.5:
                score -= 0.10
        else:
            score -= 0.15
        if sector in ('industrial', 'consumer', 'utility', 'midstream', 'telecom', 'aero_defense', 'specialty_lender'):
            score += 0.10
        elif sector in ('saas_tech', 'hyperscaler', 'pharma', 'payment_network', 'data_analytics'):
            score -= 0.10
        if data_quality.get('quarters_available', 1) >= 4: score += 0.05
        return max(0.05, min(score, 0.95))
    elif model_name == 'ddm':
        # DDM: only for mature yielders with stable payouts
        # Structurally undervalues compounders that retain & reinvest at high returns
        score = 0.15  # lower base than other models
        ni = fins.get('net_income', 0) or 0
        if ni <= 0:
            return 0.05
        # Sector fit: DDM best for high-payout, low-growth sectors
        DDM_HIGH_FIT = {'utility', 'telecom', 'midstream', 'ep'}  # 65-75% payout
        DDM_MODERATE_FIT = {'consumer', 'insurance', 'industrial', 'aero_defense', 'payment_network'}
        if sector in DDM_HIGH_FIT:
            score += 0.20
        elif sector in DDM_MODERATE_FIT:
            score += 0.10
        else:
            return 0.05
        # Revenue growth penalty: DDM is less reliable for growing companies
        # because retained earnings compound creates value DDM doesn't capture
        rev = fins.get('revenue', 0) or 0
        rev_prior = fins.get('revenue_prior', 0)
        if rev_prior and rev_prior > 0 and rev > 0:
            g = (rev - rev_prior) / rev_prior
            if abs(g) < 0.05: score += 0.10  # very stable → DDM ideal
            elif g < 0.10: score += 0.05  # stable
            elif g > 0.15: score -= 0.10  # growing fast → DDM undervalues
            elif g > 0.30: score -= 0.15  # high growth → DDM very unreliable
        if data_quality.get('quarters_available', 1) >= 4: score += 0.05
        return max(0.05, min(score, 0.95))

    elif model_name == 'epv':
        score = 0.30
        ni = fins.get('net_income', 0) or 0
        rev = fins.get('revenue', 0) or 0
        if sector in ('utility', 'consumer', 'telecom', 'midstream', 'data_analytics'):
            score += 0.12
        elif sector in ('saas_tech', 'pharma', 'hyperscaler'):
            score -= 0.10
        if ni > 0 and rev > 0 and (ni / rev) > 0.08: score += 0.08
        fcf_cv = data_quality.get('fcf_cv')
        if fcf_cv is not None and fcf_cv < 0.25: score += 0.08
        return max(0.05, min(score, 0.95))

    return 0.20


def bayesian_triangulation(model_outputs, fins, sector, data_quality):
    """Combine valuation estimates via inverse-variance weighting (Yee 2008).
    Returns: (blended_fv, weights_dict, details_dict)
    """
    valid = {name: fv for name, fv in model_outputs.items() if fv is not None and fv > 0}
    if not valid:
        return None, {}, {}
    if len(valid) == 1:
        name = list(valid.keys())[0]
        return valid[name], {name: 1.0}, {'agreement': 'SINGLE_MODEL', 'spread': 0}

    confidences = {name: _estimate_model_confidence(name, fins, sector, data_quality)
                   for name in valid}
    total_conf = sum(confidences.values())
    weights = {name: conf / total_conf for name, conf in confidences.items()}
    blended = sum(valid[name] * weights[name] for name in weights)

    values = list(valid.values())
    # Use WEIGHT-AWARE spread for agreement scoring
    # Raw min/max spread is distorted by low-weight outlier models (e.g., RI at 4% weight).
    # Weighted MAD (mean absolute deviation) reflects how much the *influential* models disagree.
    if blended > 0:
        weighted_mad = sum(weights[name] * abs(valid[name] - blended) for name in weights) / blended
        # Also compute raw spread for reference
        raw_spread = (max(values) - min(values)) / blended
        # Use weighted MAD as the primary agreement metric
        spread = weighted_mad
    else:
        spread = 0
        raw_spread = 0

    # Outlier dampening
    if spread > 0.50 and len(values) >= 3:
        median_fv = sorted(values)[len(values) // 2]
        adjusted = {}
        for name, conf in confidences.items():
            deviation = abs(valid[name] - median_fv) / median_fv if median_fv > 0 else 0
            penalty = max(0.25, 1.0 - max(deviation - 0.30, 0) * 1.5)
            adjusted[name] = conf * penalty
        total_adj = sum(adjusted.values())
        weights = {name: c / total_adj for name, c in adjusted.items()}
        blended = sum(valid[name] * weights[name] for name in weights)
        spread = sum(weights[name] * abs(valid[name] - blended) for name in weights) / blended if blended > 0 else 0
        raw_spread = (max(values) - min(values)) / blended if blended > 0 else 0

    if spread < 0.20: agreement = 'HIGH'
    elif spread < 0.40: agreement = 'MODERATE'
    elif spread < 0.60: agreement = 'LOW'
    else: agreement = 'VERY_LOW'

    details = {
        'agreement': agreement,
        'spread': round(spread * 100, 1),
        'individual': {name: round(fv, 2) for name, fv in valid.items()},
        'confidences': {name: round(c, 3) for name, c in confidences.items()},
    }
    return round(blended, 2), {name: round(w, 3) for name, w in weights.items()}, details


def monte_carlo_dcf(fins, wacc_base, shares_mil, sector, price, data_quality=None,
                    normalized_fcf=None, iterations=5000):
    """Monte Carlo simulation over DCF inputs to produce probability distribution.
    
    CRITICAL: Uses normalized FCF (from the main DCF engine's capex/SBC adjustments),
    NOT raw reported FCF. This ensures the MC distribution is centered around the
    same value the DCF scenarios produce.
    
    Samples: growth rate, WACC, terminal growth, FCF margin
    Output: percentile distribution of fair values (P10, P25, median, P75, P90)
    """
    import random

    shares = shares_mil * 1e6
    if shares <= 0:
        return None

    rev = fins.get('revenue', 0) or 0
    debt = fins.get('long_term_debt', 0) or 0
    cash = fins.get('cash', 0) or 0
    nd = debt - cash

    # Use normalized FCF if provided (from DCF engine), otherwise fall back to reported
    fcf = normalized_fcf if normalized_fcf and normalized_fcf > 0 else (fins.get('fcf', 0) or 0)

    if rev <= 0 or fcf <= 0:
        return None

    # Base parameters from sector templates
    # Use probability-weighted average of ALL scenario Y1 growth rates (not just base)
    # This anchors MC to the same growth assumptions as the DCF scenarios
    scenarios = SCENARIOS.get(sector, SCENARIOS['general'])
    pw_growth = sum(s[1] * s[2][0] for s in scenarios)  # prob-weighted Y1 growth
    base_growth = scenarios[2][2][0]  # base scenario Y1 growth
    terminal_g_base = TERMINAL_GROWTH.get(sector, 0.025)
    fcf_margin = fcf / rev  # margin based on normalized FCF

    # Trailing growth anchoring — but limit its influence to avoid MC >> DCF divergence
    # The DCF scenarios already incorporate sector-appropriate growth ranges;
    # trailing growth should nudge the center, not dominate it
    trailing_g = None
    rev_prior = fins.get('revenue_prior', 0)
    if rev_prior and rev_prior > 0:
        trailing_g = (rev - rev_prior) / rev_prior

    # Calibrate: blend trailing growth with scenario-weighted growth
    # Cap trailing influence: if trailing >> scenario range, fade it
    if trailing_g is not None and trailing_g > -0.30:
        # Use scenario max as a soft ceiling for MC growth center
        max_scenario_g = max(s[2][0] for s in scenarios)  # highest scenario Y1
        # Trailing gets 30% weight (down from 50%) — scenarios should dominate
        blended_g = trailing_g * 0.30 + pw_growth * 0.70
        # Cap at midpoint between max scenario and trailing to prevent runaway
        growth_center = min(blended_g, (max_scenario_g + pw_growth) / 2 * 1.3)
    else:
        growth_center = pw_growth

    # Standard deviations — tighter with more data
    q_avail = (data_quality or {}).get('quarters_available', 1)
    fcf_cv = (data_quality or {}).get('fcf_cv')

    growth_sd = 0.06 if q_avail >= 4 else 0.08 if q_avail >= 2 else 0.10
    wacc_sd = 0.006 if q_avail >= 4 else 0.008
    terminal_sd = 0.005
    margin_sd = 0.03 if (fcf_cv and fcf_cv < 0.25) else 0.05

    results = []
    random.seed(42)  # reproducible

    for _ in range(iterations):
        g1 = random.gauss(growth_center, growth_sd)
        g1 = max(-0.20, min(g1, 0.60))

        wacc_sample = random.gauss(wacc_base, wacc_sd)
        wacc_sample = max(0.04, min(wacc_sample, 0.18))

        tg = random.gauss(terminal_g_base, terminal_sd)
        tg = max(0.005, min(tg, wacc_sample - 0.01))

        margin_sample = random.gauss(fcf_margin, margin_sd)
        margin_sample = max(0.01, min(margin_sample, 0.60))

        # Build 10-year growth path with natural deceleration
        path = []
        for yr in range(10):
            fade = yr / 10
            g_yr = g1 * (1 - fade) + tg * fade
            path.append(g_yr)

        # DCF calculation
        r_val = rev
        pv = 0
        for yr, g_yr in enumerate(path):
            r_val *= (1 + g_yr)
            fcf_yr = r_val * margin_sample
            if fcf_yr > 0:
                pv += fcf_yr / (1 + wacc_sample) ** (yr + 1)

        # Terminal value
        terminal_fcf = r_val * margin_sample
        if terminal_fcf > 0 and wacc_sample > tg:
            tv = terminal_fcf * (1 + tg) / (wacc_sample - tg)
            pv += tv / (1 + wacc_sample) ** 10

        equity_val = pv - nd
        fv_per_share = max(equity_val / shares, 0)
        results.append(fv_per_share)

    results.sort()
    n = len(results)

    return {
        'p10': round(results[int(n * 0.10)], 2),
        'p25': round(results[int(n * 0.25)], 2),
        'median': round(results[int(n * 0.50)], 2),
        'p75': round(results[int(n * 0.75)], 2),
        'p90': round(results[int(n * 0.90)], 2),
        'mean': round(sum(results) / n, 2),
        'iterations': iterations,
        'prob_above_price': round(sum(1 for r in results if r > price) / n * 100, 1) if price > 0 else None,
    }


def run_full_valuation(fins, price, shares_mil, sector, beta=None, data_quality=None,
                       capex_model='da_proxy', capex_persistence=25):
    """Run all valuation models and combine via Bayesian triangulation.
    
    Blend: DCF + Residual Income + Comps (3 models)
    EPV: computed but NOT blended — displayed as floor reference only.
    Monte Carlo: runs on top of DCF to produce confidence intervals.
    """
    if data_quality is None:
        data_quality = {'quarters_available': 1}

    # EPS-based shares sanity check (applied once, before any sub-model)
    # For multi-class stocks, external share sources often return per-class or float
    # counts. The filing's EPS is the most reliable basis for diluted share count.
    eps = fins.get('eps_diluted', 0)
    ni_check = fins.get('net_income', 0)
    if eps and eps > 0 and ni_check and ni_check > 0 and shares_mil > 0:
        eps_implied_shares_mil = ni_check / (eps * 1e6)
        ratio = shares_mil / eps_implied_shares_mil
        if ratio < 0.50 or ratio > 2.0:
            shares_mil = eps_implied_shares_mil

    dcf_result = run_dcf(fins, price, shares_mil, sector, beta, capex_model, capex_persistence)

    if sector == 'bdc' or fins.get('_is_bdc'):
        dcf_result['multi_model'] = None
        return dcf_result

    wacc = dcf_result.get('wacc', 0.09)

    epv_fv = valuation_epv(fins, wacc, shares_mil, sector)
    ri_fv = valuation_residual_income(fins, wacc, shares_mil, sector)
    # Live comps: try EDGAR peer data, fall back to hardcoded
    live_comps = fins.get('_live_comps')  # may be pre-fetched by GUI
    comps_fv = valuation_comps(fins, sector, shares_mil, live_comps=live_comps)
    ev_rev_fv = valuation_ev_revenue(fins, sector, shares_mil, wacc)
    roic_fv = valuation_roic_fade(fins, wacc, shares_mil, sector)
    ddm_fv = valuation_ddm(fins, wacc, shares_mil, sector)
    dcf_fv = dcf_result.get('pw_fv', 0)

    # EPV excluded from blend — it's a no-growth floor, not a fair value estimate
    model_outputs = {
        'dcf': dcf_fv if dcf_fv and dcf_fv > 0 else None,
        'residual_income': ri_fv,
        'comps': comps_fv,
        'ev_revenue': ev_rev_fv,
        'roic_fade': roic_fv,
        'ddm': ddm_fv,
    }

    blended_fv, weights, tri_details = bayesian_triangulation(
        model_outputs, fins, sector, data_quality
    )

    # Monte Carlo confidence intervals — use NORMALIZED FCF from DCF engine
    dcf_inputs = dcf_result.get('inputs', {})
    normalized_fcf = dcf_inputs.get('fcf', 0) or 0
    mc_result = monte_carlo_dcf(fins, wacc, shares_mil, sector, price, data_quality,
                                 normalized_fcf=normalized_fcf)

    if blended_fv and blended_fv > 0 and price > 0:
        blended_up = (blended_fv - price) / price * 100

        # Initial verdict from blended FV
        if blended_up > 30: blended_verdict = 'SIGNIFICANTLY UNDERVALUED'
        elif blended_up > 10: blended_verdict = 'UNDERVALUED'
        elif blended_up > -10: blended_verdict = 'FAIR VALUE'
        elif blended_up > -25: blended_verdict = 'OVERVALUED'
        else: blended_verdict = 'SIGNIFICANTLY OVERVALUED'

        # MC consistency check: if MC strongly disagrees with the blended verdict,
        # soften the verdict (e.g., SELL→HOLD) but NEVER alter the reported FV or upside.
        # Upside = (FV - price) / price, always. MC probability is separate context.
        if mc_result and mc_result.get('prob_above_price') is not None:
            mc_prob = mc_result['prob_above_price']
            mc_median = mc_result.get('median', 0)
            mc_up = (mc_median - price) / price * 100 if price > 0 else 0

            # Only soften verdict if MC and blended strongly disagree directionally
            mc_strongly_bullish = mc_prob > 80 and mc_up > 20
            mc_strongly_bearish = mc_prob < 20 and mc_up < -20
            verdict_bearish = blended_verdict in ('OVERVALUED', 'SIGNIFICANTLY OVERVALUED')
            verdict_bullish = blended_verdict in ('UNDERVALUED', 'SIGNIFICANTLY UNDERVALUED')

            if mc_strongly_bullish and verdict_bearish:
                # MC says 80%+ upside probability but blend says overvalued
                # Soften by one notch
                if blended_verdict == 'SIGNIFICANTLY OVERVALUED': blended_verdict = 'OVERVALUED'
                elif blended_verdict == 'OVERVALUED': blended_verdict = 'FAIR VALUE'
            elif mc_strongly_bearish and verdict_bullish:
                # MC says 80%+ downside probability but blend says undervalued
                if blended_verdict == 'SIGNIFICANTLY UNDERVALUED': blended_verdict = 'UNDERVALUED'
                elif blended_verdict == 'UNDERVALUED': blended_verdict = 'FAIR VALUE'
    else:
        blended_fv = dcf_fv
        blended_up = dcf_result.get('pw_up', 0)
        blended_verdict = dcf_result.get('verdict', 'FAIR VALUE')

    dcf_result['multi_model'] = {
        'blended_fv': blended_fv,
        'blended_up': round(blended_up, 1),
        'blended_verdict': blended_verdict,
        'weights': weights,
        'individual': {
            'dcf': round(dcf_fv, 2) if dcf_fv else None,
            'residual_income': round(ri_fv, 2) if ri_fv else None,
            'comps': round(comps_fv, 2) if comps_fv else None,
            'ev_revenue': round(ev_rev_fv, 2) if ev_rev_fv else None,
            'roic_fade': round(roic_fv, 2) if roic_fv else None,
            'ddm': round(ddm_fv, 2) if ddm_fv else None,
        },
        'epv_floor': round(epv_fv, 2) if epv_fv else None,
        'monte_carlo': mc_result,
        'agreement': tri_details.get('agreement', 'N/A'),
        'spread': tri_details.get('spread', 0),
        'data_quality': data_quality,
    }

    return dcf_result


# ════════════════════════════════════════════════════════
#  UTILITY FUNCTIONS (moved from GUI section)
# ════════════════════════════════════════════════════════

def fmt(x):
    if x is None: return '—'
    if abs(x) >= 1e9: return f'${x/1e9:.1f}B'
    if abs(x) >= 1e6: return f'${x/1e6:.0f}M'
    if abs(x) >= 1e3: return f'${x/1e3:.0f}K'
    return f'${x:.0f}'


def infer_ticker(filepath):
    fn = os.path.basename(filepath).upper()
    m = re.match(r'^([A-Z]{1,5})[-_\s]', fn)
    if m and m.group(1) not in ('10', '20'): return m.group(1)
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            head = f.read(5000).lower()
        m = re.search(r'<title>\s*([a-z]{1,5})-20\d{6}', head)
        if m: return m.group(1).upper()
        m = re.search(r'([a-z]{1,5})-20\d{6}', head)
        if m and m.group(1) not in ('us', 'en', 'http', 'xbrl'): return m.group(1).upper()
    except: pass
    return None
