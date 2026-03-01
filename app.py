"""
C L A R I T Y  —  Web Edition
═══════════════════════════════
SEC Filing Downloader + Multi-Model Valuation Engine
Streamlit web interface — full analytical integrity, zero CORS issues.

Run:  streamlit run app.py
"""

import streamlit as st
import plotly.graph_objects as go
import json, os, math, time, tempfile

# ── Import the full engine (unchanged from desktop) ──
from engine import (
    # SEC/EDGAR
    lookup_cik, find_filing, download_filing, find_filings,
    download_and_parse_filings, fetch_shares_from_edgar,
    # Market data
    fetch_market_data,
    # Parsing
    parse_html, parse_pdf, HAS_BS4, HAS_PDF,
    # Live comps
    fetch_live_comps, PEER_GROUPS,
    # Valuation engine
    run_dcf, run_full_valuation,
    # Utilities
    detect_sector, infer_ticker, fmt,
    SECTOR_NAMES, SCENARIOS, BETAS,
)


# ════════════════════════════════════════════════════════
#  PAGE CONFIG & THEME
# ════════════════════════════════════════════════════════

st.set_page_config(
    page_title="Clarity — Multi-Model Valuation",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Dark theme CSS matching the desktop app's aesthetic
st.markdown("""
<style>
    /* Main background */
    .stApp { background-color: #0f1117; }
    section[data-testid="stSidebar"] { background-color: #131620; }
    
    /* Headers */
    h1, h2, h3 { color: #d1d5db !important; }
    h1 { font-weight: 700 !important; }
    
    /* Text */
    .stMarkdown p, .stMarkdown li { color: #d1d5db; }
    
    /* Metrics */
    [data-testid="stMetricValue"] { color: #3ecf8e !important; font-family: 'Cascadia Mono', 'Consolas', monospace !important; }
    [data-testid="stMetricLabel"] { color: #6b7280 !important; text-transform: uppercase; font-size: 0.75rem !important; }
    [data-testid="stMetricDelta"] { font-family: 'Cascadia Mono', monospace !important; }
    
    /* Cards */
    div[data-testid="stExpander"] { background-color: #161a25; border: 1px solid #252b3b; border-radius: 8px; }
    
    /* Inputs */
    .stTextInput input, .stNumberInput input { 
        background-color: #1c2030 !important; 
        color: #3ecf8e !important; 
        border: 1px solid #252b3b !important;
        font-family: 'Cascadia Mono', monospace !important;
        font-weight: bold !important;
    }
    .stSelectbox > div > div { background-color: #1c2030; color: #d1d5db; border: 1px solid #252b3b; }
    
    /* Buttons */
    .stButton > button {
        background-color: #1f2537; color: #58a6ff; border: 1px solid #252b3b;
        font-weight: 600; border-radius: 6px; transition: all 0.2s;
    }
    .stButton > button:hover { background-color: #252b3b; border-color: #3ecf8e; color: #3ecf8e; }
    
    /* Primary button */
    button[kind="primary"] {
        background-color: #3ecf8e !important; color: #0f1117 !important;
        border: none !important; font-weight: 700 !important;
    }
    button[kind="primary"]:hover { background-color: #34b27b !important; }
    
    /* Tables */
    .stDataFrame { background-color: #161a25; }
    
    /* Tabs */
    .stTabs [data-baseweb="tab"] { color: #6b7280; }
    .stTabs [aria-selected="true"] { color: #3ecf8e !important; border-bottom-color: #3ecf8e !important; }
    
    /* Dividers */
    hr { border-color: #252b3b !important; }
    
    /* Code blocks */
    code { color: #3ecf8e; background-color: #1c2030; }
    
    /* Verdict badges */
    .verdict-buy { color: #3ecf8e; font-size: 1.5rem; font-weight: 800; font-family: monospace; }
    .verdict-sell { color: #f85149; font-size: 1.5rem; font-weight: 800; font-family: monospace; }
    .verdict-hold { color: #d29922; font-size: 1.5rem; font-weight: 800; font-family: monospace; }
    
    /* Suppress Streamlit branding */
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    
    /* Compact metric cards */
    .metric-card {
        background: #161a25; border: 1px solid #252b3b; border-radius: 8px;
        padding: 12px 16px; margin: 4px 0;
    }
    .metric-label { color: #6b7280; font-size: 0.7rem; text-transform: uppercase; letter-spacing: 0.05em; }
    .metric-value { color: #d1d5db; font-size: 1.1rem; font-weight: 600; font-family: monospace; }
    .metric-green { color: #3ecf8e !important; }
    .metric-red { color: #f85149 !important; }
    .metric-yellow { color: #d29922 !important; }
    .metric-blue { color: #58a6ff !important; }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════
#  SESSION STATE INIT
# ════════════════════════════════════════════════════════

for key, default in [
    ('fins', None), ('dcf_result', None), ('ticker', ''),
    ('price', 0.0), ('shares_mil', 0.0), ('sector', 'general'),
    ('beta', None), ('data_quality', {'quarters_available': 1}),
    ('log_messages', []), ('shares_source', ''),
    ('company_name', ''), ('filing_loaded', False),
]:
    if key not in st.session_state:
        st.session_state[key] = default


def log(msg, level="info"):
    ts = time.strftime("%H:%M:%S")
    st.session_state.log_messages.append((ts, msg, level))


# ════════════════════════════════════════════════════════
#  SIDEBAR — Filing Input
# ════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("# Clarity")
    st.caption("Multi-Model Valuation Engine")
    st.divider()

    # ── Pull from EDGAR ──
    st.markdown("#### Pull Filing")
    
    col1, col2 = st.columns([2, 1])
    with col1:
        ticker_input = st.text_input("Ticker", value=st.session_state.ticker,
                                      placeholder="AAPL", key="ticker_input").upper().strip()
    with col2:
        form_type = st.selectbox("Form", ["10-Q", "10-K"], key="form_select")

    filing_count = st.select_slider("Filings to pull", options=[1, 2, 3, 4], value=1,
                                     help="Pull multiple filings for trend analysis")

    if st.button("⏎  Pull from EDGAR", use_container_width=True, type="primary"):
        if ticker_input:
            st.session_state.ticker = ticker_input
            with st.spinner(f"Pulling {filing_count}x {form_type} for {ticker_input}..."):
                try:
                    log(f"Looking up {ticker_input}...", "info")
                    
                    if filing_count > 1:
                        # Multi-filing mode
                        log_fn = lambda msg, tag: log(msg, tag)
                        quarterly_data, trailing = download_and_parse_filings(
                            ticker_input, form_type, count=filing_count, log_fn=log_fn
                        )
                        if not quarterly_data:
                            st.error("No filings found")
                        else:
                            st.session_state.fins = quarterly_data[0]
                            st.session_state.data_quality = trailing
                            sector = quarterly_data[0].get('_sector', 'general')
                            st.session_state.sector = sector
                            st.session_state.filing_loaded = True
                            log(f"Parsed {len(quarterly_data)} filings | Sector: {SECTOR_NAMES.get(sector, sector)}", "ok")
                    else:
                        # Single filing mode
                        cik, name = lookup_cik(ticker_input)
                        log(f"CIK {cik} — {name}", "ok")
                        info = find_filing(cik, form_type)
                        log(f"Found {info['form']} filed {info['date']}", "ok")
                        
                        with tempfile.TemporaryDirectory() as tmpdir:
                            path, size = download_filing(info, tmpdir)
                            log(f"Downloaded {size // 1024} KB", "ok")
                            
                            if path.lower().endswith('.pdf'):
                                st.session_state.fins = parse_pdf(path)
                            else:
                                st.session_state.fins = parse_html(path)
                        
                        sector = st.session_state.fins.get('_sector', 'general')
                        st.session_state.sector = sector
                        st.session_state.data_quality = {'quarters_available': 1}
                        st.session_state.filing_loaded = True
                        fields = len([k for k in st.session_state.fins if not k.startswith('_')])
                        log(f"Parsed: {info['form']} | {fields} fields | {SECTOR_NAMES.get(sector, sector)}", "ok")

                    # Auto-fetch market data
                    try:
                        data = fetch_market_data(ticker_input)
                        st.session_state.price = data['price']
                        st.session_state.shares_mil = data['shares_mil']
                        st.session_state.company_name = data.get('name', ticker_input)
                        st.session_state.beta = data.get('beta')
                        st.session_state.shares_source = data.get('shares_source', '')
                        
                        # Cross-validate shares
                        filing_shares = st.session_state.fins.get('shares_diluted', 0) / 1e6 if st.session_state.fins else 0
                        if data['shares_mil'] > 0 and filing_shares > 0:
                            ratio = data['shares_mil'] / filing_shares
                            if ratio < 0.50 or ratio > 2.0:
                                st.session_state.shares_mil = filing_shares
                                log(f"Shares corrected: {data['shares_mil']:.0f}M → {filing_shares:.0f}M (filing)", "warn")
                        
                        log(f"Market: ${data['price']:.2f} | {st.session_state.shares_mil:.1f}M shares", "ok")
                    except Exception as e:
                        log(f"Market data failed: {e}", "warn")

                except Exception as e:
                    st.error(f"Error: {e}")
                    log(f"Error: {e}", "err")

            st.rerun()

    st.divider()

    # ── Upload Filing ──
    st.markdown("#### Or Upload Filing")
    uploaded = st.file_uploader("Drop SEC filing (HTML/PDF)", type=["htm", "html", "pdf"],
                                 key="file_upload")
    if uploaded:
        with st.spinner("Parsing..."):
            try:
                with tempfile.NamedTemporaryFile(suffix=os.path.splitext(uploaded.name)[1], delete=False) as tmp:
                    tmp.write(uploaded.read())
                    tmp_path = tmp.name
                
                if tmp_path.lower().endswith('.pdf'):
                    st.session_state.fins = parse_pdf(tmp_path)
                else:
                    st.session_state.fins = parse_html(tmp_path)
                
                os.unlink(tmp_path)
                
                sector = st.session_state.fins.get('_sector', 'general')
                st.session_state.sector = sector
                st.session_state.filing_loaded = True
                
                # Try to infer ticker
                inferred = infer_ticker(uploaded.name)
                if inferred:
                    st.session_state.ticker = inferred
                
                log(f"Uploaded: {uploaded.name} | {SECTOR_NAMES.get(sector, sector)}", "ok")
                st.rerun()
            except Exception as e:
                st.error(f"Parse error: {e}")

    st.divider()

    # ── Market Data Overrides ──
    st.markdown("#### Market Data")
    
    price_input = st.number_input("Price ($)", value=st.session_state.price,
                                   min_value=0.0, step=0.01, format="%.2f", key="price_in")
    shares_input = st.number_input("Shares (M)", value=st.session_state.shares_mil,
                                    min_value=0.0, step=0.1, format="%.1f", key="shares_in")
    
    sector_input = st.selectbox("Sector", options=list(SECTOR_NAMES.keys()),
                                 index=list(SECTOR_NAMES.keys()).index(st.session_state.sector)
                                 if st.session_state.sector in SECTOR_NAMES else 0,
                                 format_func=lambda x: f"{x} — {SECTOR_NAMES.get(x, x)}",
                                 key="sector_in")

    st.session_state.price = price_input
    st.session_state.shares_mil = shares_input
    st.session_state.sector = sector_input

    # ── Run Valuation ──
    st.divider()
    run_clicked = st.button("▶  Run Valuation", use_container_width=True, type="primary",
                             disabled=not st.session_state.filing_loaded)

    if run_clicked and st.session_state.fins:
        with st.spinner("Running multi-model valuation..."):
            try:
                fins = st.session_state.fins
                price = st.session_state.price
                shares = st.session_state.shares_mil
                sector = st.session_state.sector
                beta = st.session_state.beta
                dq = st.session_state.data_quality

                # Fetch live comps
                try:
                    live_comps = fetch_live_comps(sector, log_fn=lambda m, t: log(m, t))
                    if live_comps:
                        fins['_live_comps'] = live_comps
                        log(f"Live comps: PE={live_comps.get('pe')}x ({live_comps.get('peer_count',0)} peers)", "info")
                except:
                    pass

                result = run_full_valuation(fins, price, shares, sector, beta=beta,
                                             data_quality=dq)
                st.session_state.dcf_result = result
                log("Valuation complete", "ok")
            except Exception as e:
                st.error(f"Valuation error: {e}")
                log(f"Error: {e}", "err")
        
        st.rerun()

    # ── Log ──
    if st.session_state.log_messages:
        with st.expander("Log", expanded=False):
            for ts, msg, level in st.session_state.log_messages[-15:]:
                color = {"ok": "green", "err": "red", "warn": "orange", "info": "blue"}.get(level, "gray")
                st.markdown(f"<small style='color:{color}'>`{ts}` {msg}</small>", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════
#  MAIN CONTENT
# ════════════════════════════════════════════════════════

if not st.session_state.dcf_result:
    # Landing state
    st.markdown("""
    <div style="text-align: center; padding: 80px 20px;">
        <h1 style="font-size: 3rem; font-weight: 800; color: #d1d5db;">Clarity</h1>
        <p style="color: #6b7280; font-size: 1.1rem; margin-top: -10px;">
            SEC Filing → Multi-Model Valuation in seconds
        </p>
        <p style="color: #4b5563; font-size: 0.9rem; margin-top: 30px;">
            Enter a ticker in the sidebar and pull a filing to get started.<br>
            DCF · Residual Income · Comps · ROIC Fade · EV/Revenue · DDM · Monte Carlo
        </p>
    </div>
    """, unsafe_allow_html=True)
    st.stop()


# ════════════════════════════════════════════════════════
#  RESULTS DISPLAY
# ════════════════════════════════════════════════════════

r = st.session_state.dcf_result
fins = st.session_state.fins
price = st.session_state.price
sector = st.session_state.sector
mm = r.get('multi_model', {})

# ── Header ──
ticker = st.session_state.ticker or "—"
company = st.session_state.company_name or ""

# Determine fair value and verdict
if mm and mm.get('blended_fv'):
    fv = mm['blended_fv']
    upside = mm['blended_up']
    verdict = mm['blended_verdict']
else:
    fv = r.get('pw_fv', 0)
    upside = r.get('pw_up', 0)
    verdict = r.get('verdict', 'HOLD')

verdict_class = "buy" if "BUY" in verdict else "sell" if "SELL" in verdict else "hold"

st.markdown(f"### {ticker} — {company}")

# ── Top metrics row ──
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Fair Value", f"${fv:.2f}", f"{upside:+.1f}%")
c2.metric("Market Price", f"${price:.2f}")
c3.metric("WACC", f"{r.get('wacc', 0)*100:.2f}%")

mc = mm.get('monte_carlo') if mm else None
if mc and mc.get('prob_above_price') is not None:
    c4.metric("P(Upside)", f"{mc['prob_above_price']:.0f}%")
else:
    c4.metric("P(Upside)", "—")

c5.markdown(f"""
<div style="text-align: center; padding-top: 8px;">
    <div style="color: #6b7280; font-size: 0.7rem; text-transform: uppercase;">Verdict</div>
    <div class="verdict-{verdict_class}">{verdict}</div>
</div>
""", unsafe_allow_html=True)

st.divider()

# ════════════════════════════════════════════════════════
#  TABS
# ════════════════════════════════════════════════════════

tab_overview, tab_models, tab_scenarios, tab_financials, tab_context = st.tabs([
    "Overview", "Models", "Scenarios", "Financials", "Market Context"
])


# ─────────────────────────────────
#  TAB: Overview (Chart + MC)
# ─────────────────────────────────
with tab_overview:
    pp = r.get('price_paths')

    if pp and pp.get('pw_path') and len(pp['pw_path']) > 1:
        pw = pp['pw_path']
        scen_paths = [sc['path'] for sc in pp['scenarios']]
        mkt_price = pp.get('market_price', price)

        # Scale to blended FV
        dcf_start = pw[0]
        blended_start = fv
        if dcf_start > 0 and blended_start > 0:
            scale = blended_start / dcf_start
            pw_scaled = [p * scale for p in pw]
            scen_scaled = []
            for sc in pp['scenarios']:
                scen_scaled.append([p * scale for p in sc['path']])
        else:
            pw_scaled = pw
            scen_scaled = [sc['path'] for sc in pp['scenarios']]

        upper = [max(s[yr] for s in scen_scaled) for yr in range(min(11, len(pw_scaled)))]
        lower = [min(s[yr] for s in scen_scaled) for yr in range(min(11, len(pw_scaled)))]

        # Inner cone (1σ)
        inner_upper, inner_lower = [], []
        for yr in range(min(11, len(pw_scaled))):
            vals = [(scen_scaled[si][yr], pp['scenarios'][si].get('prob', 0.2))
                    for si in range(len(scen_scaled))]
            pw_val = pw_scaled[yr]
            var = sum(p * (v - pw_val) ** 2 for v, p in vals)
            sigma = max(var ** 0.5, pw_val * 0.02)
            inner_upper.append(pw_val + sigma)
            inner_lower.append(max(pw_val - sigma, pw_val * 0.1))

        years = list(range(len(pw_scaled)))

        fig = go.Figure()

        # Outer cone
        fig.add_trace(go.Scatter(
            x=years + years[::-1],
            y=upper + lower[::-1],
            fill='toself', fillcolor='rgba(42, 74, 58, 0.15)',
            line=dict(width=0), showlegend=False, hoverinfo='skip'
        ))

        # Inner cone
        fig.add_trace(go.Scatter(
            x=years + years[::-1],
            y=inner_upper + inner_lower[::-1],
            fill='toself', fillcolor='rgba(30, 80, 55, 0.25)',
            line=dict(width=0), showlegend=False, hoverinfo='skip'
        ))

        # Fair Value line
        fig.add_trace(go.Scatter(
            x=years, y=pw_scaled,
            mode='lines+markers', name='Fair Value',
            line=dict(color='#3ecf8e', width=3),
            marker=dict(size=6, color='#3ecf8e'),
            hovertemplate='Year %{x}: $%{y:,.2f}<extra></extra>'
        ))

        # Market price line
        if mkt_price > 0:
            fig.add_hline(y=mkt_price, line_dash="dash", line_color="#f85149",
                          annotation_text=f"Market ${mkt_price:,.0f}",
                          annotation_font_color="#f85149")

        # Scenario paths (thin, muted)
        scenario_colors = ['#5aaa70', '#4a9a65', '#888888', '#aa6a5a', '#aa4a4a']
        for i, (sc, path) in enumerate(zip(pp['scenarios'], scen_scaled)):
            fig.add_trace(go.Scatter(
                x=years, y=path,
                mode='lines', name=sc.get('name', f'S{i+1}'),
                line=dict(color=scenario_colors[i % len(scenario_colors)], width=1, dash='dot'),
                opacity=0.5,
                hovertemplate=f"{sc.get('name', '')}: $%{{y:,.2f}}<extra></extra>"
            ))

        fig.update_layout(
            title=dict(text="Intrinsic Value · 10Y Projection", font=dict(color='#b0b8c8', size=16)),
            plot_bgcolor='#111520', paper_bgcolor='#0f1117',
            xaxis=dict(title="Year", gridcolor='#1c2030', color='#6b7280', dtick=1),
            yaxis=dict(title="$/Share", gridcolor='#1c2030', color='#6b7280', tickformat='$,.0f'),
            legend=dict(font=dict(color='#9ca3af', size=10), bgcolor='rgba(0,0,0,0)'),
            margin=dict(l=60, r=30, t=50, b=40),
            height=420,
            hovermode='x unified',
        )

        st.plotly_chart(fig, use_container_width=True)

    # Projection table
    if pp and pp.get('pw_path') and len(pp['pw_path']) > 1:
        st.markdown("##### 10-Year Projection")
        rows = []
        for yr in [0, 1, 3, 5, 7, 10]:
            if yr < len(pw_scaled):
                cagr = (pw_scaled[yr] / pw_scaled[0]) ** (1 / max(yr, 1)) - 1 if pw_scaled[0] > 0 and yr > 0 else 0
                rows.append({
                    "Year": f"Y{yr}" if yr > 0 else "Today",
                    "Projected FV": f"${pw_scaled[yr]:,.2f}",
                    "CAGR": f"{cagr*100:+.1f}%" if yr > 0 else "—"
                })
        st.dataframe(rows, use_container_width=True, hide_index=True)

    # Monte Carlo
    if mc:
        st.markdown("##### Monte Carlo Distribution")
        mc_cols = st.columns(6)
        mc_cols[0].metric("P10", f"${mc['p10']:,.0f}")
        mc_cols[1].metric("P25", f"${mc['p25']:,.0f}")
        mc_cols[2].metric("Median", f"${mc['median']:,.0f}")
        mc_cols[3].metric("P75", f"${mc['p75']:,.0f}")
        mc_cols[4].metric("P90", f"${mc['p90']:,.0f}")
        prob = mc.get('prob_above_price')
        mc_cols[5].metric("P(>Price)", f"{prob:.0f}%" if prob is not None else "—")
        st.caption(f"{mc.get('iterations', 5000):,} iterations")


# ─────────────────────────────────
#  TAB: Models
# ─────────────────────────────────
with tab_models:
    if mm and mm.get('blended_fv'):
        st.markdown("##### Multi-Model Triangulation")
        
        ind = mm.get('individual', {})
        wts = mm.get('weights', {})
        
        MODEL_NAMES = {
            'dcf': 'DCF (Scenario-Weighted)',
            'residual_income': 'Residual Income',
            'comps': 'Comparable Companies',
            'ev_revenue': 'EV/Revenue Normalized',
            'roic_fade': 'ROIC Fade / Econ Profit',
            'ddm': 'Dividend Discount Model',
        }
        
        model_rows = []
        for mname in ['dcf', 'residual_income', 'comps', 'ev_revenue', 'roic_fade', 'ddm']:
            mfv = ind.get(mname)
            mwt = wts.get(mname, 0)
            if mfv and mfv > 0:
                model_rows.append({
                    "Model": MODEL_NAMES.get(mname, mname),
                    "Fair Value": f"${mfv:,.2f}",
                    "Weight": f"{mwt*100:.1f}%",
                    "vs Price": f"{(mfv - price) / price * 100:+.1f}%" if price > 0 else "—"
                })
        
        st.dataframe(model_rows, use_container_width=True, hide_index=True)
        
        # EPV floor
        epv = mm.get('epv_floor')
        if epv and epv > 0:
            st.caption(f"EPV (no-growth floor): ${epv:,.2f}")
        
        # Agreement
        agree = mm.get('agreement', 'N/A')
        spread = mm.get('spread', 0)
        agree_color = "#3ecf8e" if agree == "HIGH" else "#d29922" if agree in ("MODERATE", "LOW") else "#f85149"
        st.markdown(f"**Model Agreement:** <span style='color:{agree_color}'>{agree}</span> ({spread:.0f}% weighted spread)",
                    unsafe_allow_html=True)

        # Stacked bar chart of model weights
        if model_rows:
            model_names = [r['Model'] for r in model_rows]
            model_weights = [wts.get(k, 0) for k in ['dcf', 'residual_income', 'comps', 'ev_revenue', 'roic_fade', 'ddm'] if ind.get(k) and ind[k] > 0]
            model_fvs = [ind[k] for k in ['dcf', 'residual_income', 'comps', 'ev_revenue', 'roic_fade', 'ddm'] if ind.get(k) and ind[k] > 0]
            
            colors = ['#3ecf8e', '#58a6ff', '#d29922', '#e3722b', '#a78bfa', '#f472b6']
            
            fig_bar = go.Figure()
            for i, (name, weight, fv_val) in enumerate(zip(model_names, model_weights, model_fvs)):
                fig_bar.add_trace(go.Bar(
                    x=[weight * 100], y=['Blend'], orientation='h',
                    name=f"{name} (${fv_val:,.0f})",
                    marker_color=colors[i % len(colors)],
                    hovertemplate=f"{name}: {weight*100:.1f}% × ${fv_val:,.2f}<extra></extra>"
                ))
            
            fig_bar.update_layout(
                barmode='stack', height=80,
                plot_bgcolor='#111520', paper_bgcolor='#0f1117',
                xaxis=dict(title='Weight %', color='#6b7280', gridcolor='#1c2030'),
                yaxis=dict(visible=False),
                legend=dict(font=dict(color='#9ca3af', size=10), bgcolor='rgba(0,0,0,0)',
                           orientation='h', yanchor='bottom', y=1.1),
                margin=dict(l=10, r=10, t=10, b=30),
            )
            st.plotly_chart(fig_bar, use_container_width=True)
    else:
        st.info("Multi-model data not available. Run valuation first.")


# ─────────────────────────────────
#  TAB: Scenarios
# ─────────────────────────────────
with tab_scenarios:
    st.markdown("##### DCF Scenario Analysis")
    
    scens = r.get('scenarios', [])
    if scens:
        # Scenario table
        scen_rows = []
        for sc in scens:
            if isinstance(sc, dict):
                scen_rows.append({
                    "Scenario": sc['name'],
                    "Probability": f"{sc['prob']*100:.0f}%",
                    "Fair Value": f"${sc['fv']:,.2f}",
                    "Upside": f"{sc['upside']:+.1f}%"
                })
        st.dataframe(scen_rows, use_container_width=True, hide_index=True)
        
        # Scenario bar chart
        names = [s['Scenario'] for s in scen_rows]
        fvs = [float(s['Fair Value'].replace('$', '').replace(',', '')) for s in scen_rows]
        colors_bar = ['#3ecf8e' if fv > price else '#f85149' for fv in fvs]
        
        fig_scen = go.Figure(go.Bar(
            x=names, y=fvs,
            marker_color=colors_bar,
            text=[f"${fv:,.0f}" for fv in fvs],
            textposition='outside', textfont=dict(color='#d1d5db', size=11),
        ))
        fig_scen.add_hline(y=price, line_dash="dash", line_color="#f85149",
                            annotation_text=f"Market ${price:,.0f}")
        fig_scen.update_layout(
            plot_bgcolor='#111520', paper_bgcolor='#0f1117',
            xaxis=dict(color='#6b7280', tickfont=dict(size=9)),
            yaxis=dict(title='$/Share', color='#6b7280', gridcolor='#1c2030', tickformat='$,.0f'),
            height=350, margin=dict(l=60, r=20, t=30, b=80),
        )
        st.plotly_chart(fig_scen, use_container_width=True)

    # Growth metrics
    st.markdown("##### Growth")
    gc1, gc2, gc3 = st.columns(3)
    tg = r.get('trailing_growth', r.get('trailing'))
    ig = r.get('implied_fcf_growth', r.get('implied_fcf'))
    ir = r.get('implied_rev_growth', r.get('implied_rev'))
    gc1.metric("Trailing Rev", f"{tg*100:.1f}%" if tg else "—")
    gc2.metric("Implied FCF Growth", f"{ig*100:.1f}%" if ig else ">80%")
    gc3.metric("Implied Rev Growth", f"{ir*100:.1f}%" if ir else ">80%")

    # Sanity flags
    flags = r.get('sanity_flags', [])
    if flags:
        st.markdown("##### Flags")
        for flag in flags:
            st.warning(f"⚠ {flag}")


# ─────────────────────────────────
#  TAB: Financials
# ─────────────────────────────────
with tab_financials:
    if fins:
        inp = r.get('inputs', {})
        
        st.markdown("##### Key Financials")

        if sector in ('bank', 'specialty_lender'):
            equity_val = fins.get('stockholders_equity', 0) or 0
            ni_val = fins.get('net_income', 0) or 0
            roe = ni_val / equity_val if equity_val > 0 else 0
            shares_val = inp.get('shares', 0)
            bvps = equity_val / shares_val if shares_val > 0 else 0
            
            items = [
                ("Revenue (NII)", fins.get('revenue')),
                ("Net Income", fins.get('net_income')),
                ("Total Assets", fins.get('total_assets')),
                ("Equity", equity_val),
                ("Cash", fins.get('cash')),
                ("LT Debt", fins.get('long_term_debt')),
                ("Op CF", fins.get('operating_cf')),
                ("EPS", fins.get('eps_diluted')),
            ]
            
            fin_rows = []
            for label, val in items:
                if val is None: continue
                f_val = fmt(val) if label != "EPS" else f"${val:.2f}"
                fin_rows.append({"Metric": label, "Value": f_val})
            st.dataframe(fin_rows, use_container_width=True, hide_index=True)
            
            bc1, bc2, bc3 = st.columns(3)
            bc1.metric("Book/Share", f"${bvps:.2f}")
            bc2.metric("Trailing ROE", f"{roe*100:.1f}%")
            ta = fins.get('total_assets', 0) or 0
            if ta > 0 and equity_val > 0:
                bc3.metric("Leverage", f"{ta/equity_val:.1f}x")
        else:
            items = [
                ("Revenue", fins.get('revenue')),
                ("Net Income", fins.get('net_income')),
                ("Operating Income", fins.get('operating_income')),
                ("Operating CF", fins.get('operating_cf')),
                ("CapEx", fins.get('capex')),
                ("D&A", fins.get('depreciation')),
                ("FCF (reported)", inp.get('fcf_reported', r.get('fcf_reported'))),
                ("FCF (used)", inp.get('fcf', r.get('fcf'))),
                ("EBITDA", inp.get('ebitda', r.get('ebitda'))),
                ("Cash", fins.get('cash')),
                ("LT Debt", fins.get('long_term_debt')),
                ("Equity", fins.get('stockholders_equity')),
                ("EPS", fins.get('eps_diluted')),
            ]
            
            fin_rows = []
            for label, val in items:
                if val is None: continue
                f_val = fmt(val) if label != "EPS" else f"${val:.2f}"
                fin_rows.append({"Metric": label, "Value": f_val})
            st.dataframe(fin_rows, use_container_width=True, hide_index=True)

        # CapEx model
        capex_method = inp.get('capex_method', r.get('capex_method', '—'))
        if isinstance(r.get('capex'), dict):
            capex_method = r['capex'].get('method', capex_method)
        st.caption(f"FCF Method: {capex_method}")
        
        lc = fins.get('_live_comps')
        comps_info = f"Live ({lc['peer_count']} peers)" if lc else "Static"
        st.caption(f"Sector: {SECTOR_NAMES.get(sector, sector)} · {fins.get('_form', '?')} · Comps: {comps_info}")

        # Trailing series
        dq = mm.get('data_quality', {}) if mm else st.session_state.data_quality
        q_avail = dq.get('quarters_available', 1)
        if q_avail > 1:
            st.divider()
            st.markdown(f"##### Trailing Data ({q_avail} filings)")
            
            tc1, tc2, tc3 = st.columns(3)
            if dq.get('rev_cagr') is not None:
                tc1.metric("Rev CAGR", f"{dq['rev_cagr']*100:.1f}%")
            if dq.get('fcf_cv') is not None:
                stab = 'Stable' if dq['fcf_cv'] < 0.25 else 'Moderate' if dq['fcf_cv'] < 0.50 else 'Volatile'
                tc2.metric("FCF Stability", f"{stab} (CV={dq['fcf_cv']:.2f})")
            if dq.get('margin_trend') is not None:
                mt = dq['margin_trend']
                direction = 'Expanding' if mt > 0.01 else 'Compressing' if mt < -0.01 else 'Stable'
                tc3.metric("Margin Trend", f"{direction} ({mt*100:+.1f}pp)")

            # History chart
            rev_s = dq.get('rev_series', [])
            ni_s = dq.get('ni_series', [])
            fcf_s = dq.get('fcf_series', [])
            is_q = dq.get('form_type', '10-Q') in ('10-Q', '10-Q/A')
            max_len = max(len(rev_s), len(ni_s), len(fcf_s))
            
            if max_len >= 2:
                labels = [f"{'Q' if is_q else 'Y'}-{i}" for i in range(max_len)]
                
                fig_hist = go.Figure()
                if rev_s:
                    fig_hist.add_trace(go.Bar(x=labels[:len(rev_s)], y=[v/1e6 for v in rev_s],
                                              name='Revenue', marker_color='#3ecf8e', opacity=0.7))
                if ni_s:
                    fig_hist.add_trace(go.Bar(x=labels[:len(ni_s)], y=[v/1e6 for v in ni_s],
                                              name='Net Income', marker_color='#58a6ff', opacity=0.7))
                if fcf_s:
                    fig_hist.add_trace(go.Bar(x=labels[:len(fcf_s)], y=[v/1e6 for v in fcf_s],
                                              name='FCF', marker_color='#d29922', opacity=0.7))
                
                fig_hist.update_layout(
                    title="Trailing Financial Series ($M)",
                    barmode='group', height=280,
                    plot_bgcolor='#111520', paper_bgcolor='#0f1117',
                    xaxis=dict(color='#6b7280'), yaxis=dict(color='#6b7280', gridcolor='#1c2030'),
                    legend=dict(font=dict(color='#9ca3af'), bgcolor='rgba(0,0,0,0)'),
                    margin=dict(l=60, r=20, t=40, b=30),
                )
                st.plotly_chart(fig_hist, use_container_width=True)


# ─────────────────────────────────
#  TAB: Market Context
# ─────────────────────────────────
with tab_context:
    mi = r.get('market_implies')
    af = r.get('asset_floor')
    evr = r.get('ev_rev_multiple')
    cc = r.get('comps_check')

    if mi:
        st.markdown("##### Market-Implied Growth")
        cagr = mi.get('implied_rev_cagr')
        if isinstance(cagr, str):
            st.error(f"Market implies: Rev CAGR {cagr}")
        else:
            mc1, mc2, mc3 = st.columns(3)
            mc1.metric("Implied Rev CAGR", f"{cagr:.1f}%")
            mc2.metric("At Margin", f"{mi.get('assumed_margin', '')}%")
            ps = mi.get('implied_ps_now')
            if ps:
                mc3.metric("Current P/S", f"{ps:.1f}x")

    if cc:
        st.markdown("##### Comparables Check")
        cc1, cc2, cc3 = st.columns(3)
        cpe = cc.get('current_pe')
        if cpe:
            src = cc.get('comps_source', 'static')
            cc1.metric("Current P/E", f"{cpe:.1f}x")
            cc2.metric(f"Sector P/E ({src})", f"{cc['sector_pe']}x")
            cc3.metric("P/E Fair Value", f"${cc['pe_fv']:,.2f}")
        
        eveb = cc.get('evebitda_fv')
        if eveb:
            st.caption(f"EV/EBITDA: sector {cc['sector_evebitda']}x → ${eveb:,.0f}")
        
        pb = cc.get('pb_now')
        if pb:
            jpb = cc.get('pb_justified')
            txt = f"P/B: {pb:.2f}x"
            if jpb:
                txt += f" (justified {jpb:.2f}x)"
            st.caption(txt)

    if evr:
        st.markdown("##### EV/Revenue Multiple")
        er1, er2, er3 = st.columns(3)
        er1.metric("Current", f"{evr['current']:.1f}x")
        er2.metric("Sector Median", f"{evr['sector_median']:.1f}x")
        er3.metric("Premium", f"{evr['premium_pct']:+.0f}%")
        if evr.get('at_median_price'):
            st.caption(f"At sector median: ${evr['at_median_price']:,.2f}/share")

    if af:
        st.markdown("##### Asset Floor")
        af1, af2, af3 = st.columns(3)
        af1.metric("Book/Share", f"${af['book_per_share']:,.2f}")
        af2.metric("Tangible Floor", f"${af['tangible_floor']:,.2f}")
        btp = af.get('book_to_price')
        if btp:
            af3.metric("Book % of Price", f"{btp:.0f}%")

    if not (mi or af or evr or cc):
        st.info("No market context data available. Run valuation first.")

    # Additional flags
    if r.get('is_buyback_machine'):
        st.warning("Buyback Machine: negative equity with strong cash generation — debt penalty capped")
    if r.get('sbc_haircut', 0) > 0:
        st.caption(f"SBC Haircut: {fmt(r['sbc_haircut'])}")
