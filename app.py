"""
C L A R I T Y  —  Web Edition
"""

import streamlit as st
import plotly.graph_objects as go
import json, os, math, time, tempfile, traceback

from engine import (
    lookup_cik, find_filing, download_filing, find_filings,
    download_and_parse_filings, fetch_shares_from_edgar,
    fetch_market_data,
    parse_html, parse_pdf, HAS_BS4, HAS_PDF,
    fetch_live_comps, PEER_GROUPS,
    run_dcf, run_full_valuation,
    detect_sector, infer_ticker, fmt,
    SECTOR_NAMES, SCENARIOS, BETAS,
)

st.set_page_config(page_title="Clarity", page_icon="📊", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
<style>
    .stApp { background-color: #0f1117; }
    section[data-testid="stSidebar"] { background-color: #131620; }
    h1, h2, h3 { color: #d1d5db !important; }
    .stMarkdown p, .stMarkdown li { color: #d1d5db; }
    [data-testid="stMetricValue"] { color: #3ecf8e !important; font-family: monospace !important; }
    [data-testid="stMetricLabel"] { color: #6b7280 !important; text-transform: uppercase; font-size: 0.75rem !important; }
    .stTextInput input, .stNumberInput input { background-color: #1c2030 !important; color: #3ecf8e !important; border: 1px solid #252b3b !important; font-family: monospace !important; font-weight: bold !important; }
    .stSelectbox > div > div { background-color: #1c2030; color: #d1d5db; border: 1px solid #252b3b; }
    .stButton > button { background-color: #1f2537; color: #58a6ff; border: 1px solid #252b3b; font-weight: 600; border-radius: 6px; }
    .stButton > button:hover { background-color: #252b3b; border-color: #3ecf8e; color: #3ecf8e; }
    button[kind="primary"] { background-color: #3ecf8e !important; color: #0f1117 !important; border: none !important; font-weight: 700 !important; }
    .stTabs [data-baseweb="tab"] { color: #6b7280; }
    .stTabs [aria-selected="true"] { color: #3ecf8e !important; border-bottom-color: #3ecf8e !important; }
    hr { border-color: #252b3b !important; }
    .verdict-buy { color: #3ecf8e; font-size: 1.5rem; font-weight: 800; font-family: monospace; }
    .verdict-sell { color: #f85149; font-size: 1.5rem; font-weight: 800; font-family: monospace; }
    .verdict-hold { color: #d29922; font-size: 1.5rem; font-weight: 800; font-family: monospace; }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ── Session State ──
for key, default in [('fins', None), ('dcf_result', None), ('ticker', ''), ('price', 0.0),
    ('shares_mil', 0.0), ('sector', 'general'), ('beta', None),
    ('data_quality', {'quarters_available': 1}), ('log_messages', []),
    ('shares_source', ''), ('company_name', ''), ('filing_loaded', False)]:
    if key not in st.session_state:
        st.session_state[key] = default

def log(msg, level="info"):
    st.session_state.log_messages.append((time.strftime("%H:%M:%S"), msg, level))

def run_valuation():
    fins = st.session_state.fins
    price = st.session_state.price
    shares = st.session_state.shares_mil
    sector = st.session_state.sector
    beta = st.session_state.beta
    dq = st.session_state.data_quality
    if not fins: raise ValueError("No filing loaded")
    if price <= 0: raise ValueError(f"Invalid price: {price}")
    if shares <= 0: raise ValueError(f"Invalid shares: {shares}")
    try:
        live_comps = fetch_live_comps(sector, log_fn=lambda m, t: log(m, t))
        if live_comps: fins['_live_comps'] = live_comps
    except Exception as e:
        log(f"Live comps failed: {e}", "warn")
    result = run_full_valuation(fins, price, shares, sector, beta=beta, data_quality=dq)
    st.session_state.dcf_result = result
    log("Valuation complete", "ok")

# ════════════════════════════════════════
#  SIDEBAR
# ════════════════════════════════════════
with st.sidebar:
    st.markdown("# Clarity")
    st.caption("Multi-Model Valuation Engine")
    st.divider()
    st.markdown("#### Pull Filing")
    col1, col2 = st.columns([2, 1])
    with col1:
        ticker_input = st.text_input("Ticker", value=st.session_state.ticker, placeholder="AAPL").upper().strip()
    with col2:
        form_type = st.selectbox("Form", ["10-Q", "10-K"])
    filing_count = st.select_slider("Filings", options=[1, 2, 3, 4], value=1)

    if st.button("⏎  Pull from EDGAR", use_container_width=True, type="primary"):
        if ticker_input:
            st.session_state.ticker = ticker_input
            st.session_state.dcf_result = None
            progress = st.empty()
            status = st.empty()
            try:
                progress.progress(10, "Looking up ticker...")
                log(f"Looking up {ticker_input}...", "info")
                if filing_count > 1:
                    progress.progress(20, f"Pulling {filing_count} filings...")
                    quarterly_data, trailing = download_and_parse_filings(
                        ticker_input, form_type, count=filing_count, log_fn=lambda m, t: log(m, t))
                    if not quarterly_data: raise ValueError("No filings found")
                    st.session_state.fins = quarterly_data[0]
                    st.session_state.data_quality = trailing
                    sector = quarterly_data[0].get('_sector', 'general')
                    st.session_state.sector = sector
                    st.session_state.filing_loaded = True
                    log(f"Parsed {len(quarterly_data)} filings | {SECTOR_NAMES.get(sector, sector)}", "ok")
                else:
                    cik, name = lookup_cik(ticker_input)
                    progress.progress(30, f"Found {name}...")
                    info = find_filing(cik, form_type)
                    log(f"Found {info['form']} filed {info['date']}", "ok")
                    progress.progress(50, "Downloading...")
                    with tempfile.TemporaryDirectory() as tmpdir:
                        path, size = download_filing(info, tmpdir)
                        progress.progress(60, "Parsing...")
                        if path.lower().endswith('.pdf'): st.session_state.fins = parse_pdf(path)
                        else: st.session_state.fins = parse_html(path)
                    sector = st.session_state.fins.get('_sector', 'general')
                    st.session_state.sector = sector
                    st.session_state.data_quality = {'quarters_available': 1}
                    st.session_state.filing_loaded = True
                    fields = len([k for k in st.session_state.fins if not k.startswith('_')])
                    log(f"Parsed: {info['form']} | {fields} fields | {SECTOR_NAMES.get(sector, sector)}", "ok")

                # Fetch market data
                progress.progress(70, "Fetching market data...")
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
                    status.warning(f"Market data failed: {e}")

                # Auto-run valuation
                if st.session_state.price > 0 and st.session_state.shares_mil > 0:
                    progress.progress(85, "Running valuation...")
                    try:
                        run_valuation()
                        progress.progress(100, "Done!")
                        time.sleep(0.3)
                        progress.empty()
                        status.success(f"✓ {ticker_input} valued")
                    except Exception as e:
                        progress.empty()
                        status.error(f"Valuation error: {e}")
                        log(f"Valuation error: {e}\n{traceback.format_exc()}", "err")
                else:
                    progress.empty()
                    status.warning("Enter price and shares manually, then Run Valuation.")
            except Exception as e:
                progress.empty()
                status.error(f"Error: {e}")
                log(f"Error: {e}\n{traceback.format_exc()}", "err")
            st.rerun()

    st.divider()
    st.markdown("#### Or Upload Filing")
    uploaded = st.file_uploader("SEC filing (HTML/PDF)", type=["htm", "html", "pdf"])
    if uploaded:
        with st.spinner("Parsing..."):
            try:
                with tempfile.NamedTemporaryFile(suffix=os.path.splitext(uploaded.name)[1], delete=False) as tmp:
                    tmp.write(uploaded.read()); tmp_path = tmp.name
                if tmp_path.lower().endswith('.pdf'): st.session_state.fins = parse_pdf(tmp_path)
                else: st.session_state.fins = parse_html(tmp_path)
                os.unlink(tmp_path)
                st.session_state.sector = st.session_state.fins.get('_sector', 'general')
                st.session_state.filing_loaded = True
                inferred = infer_ticker(uploaded.name)
                if inferred: st.session_state.ticker = inferred
                log(f"Uploaded: {uploaded.name}", "ok")
                st.rerun()
            except Exception as e:
                st.error(f"Parse error: {e}")

    st.divider()
    st.markdown("#### Market Data")
    new_price = st.number_input("Price ($)", value=float(st.session_state.price), min_value=0.0, step=0.01, format="%.2f")
    new_shares = st.number_input("Shares (M)", value=float(st.session_state.shares_mil), min_value=0.0, step=0.1, format="%.1f")
    if abs(new_price - st.session_state.price) > 0.001: st.session_state.price = new_price
    if abs(new_shares - st.session_state.shares_mil) > 0.001: st.session_state.shares_mil = new_shares

    sector_opts = list(SECTOR_NAMES.keys())
    cidx = sector_opts.index(st.session_state.sector) if st.session_state.sector in sector_opts else 0
    new_sector = st.selectbox("Sector", options=sector_opts, index=cidx,
                               format_func=lambda x: f"{x} — {SECTOR_NAMES.get(x, x)}")
    st.session_state.sector = new_sector

    st.divider()
    can_run = st.session_state.filing_loaded and st.session_state.price > 0 and st.session_state.shares_mil > 0
    if st.button("▶  Run Valuation", use_container_width=True, type="primary", disabled=not can_run):
        with st.spinner("Running valuation..."):
            try:
                run_valuation()
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
                log(f"Error: {e}\n{traceback.format_exc()}", "err")
    if not can_run and st.session_state.filing_loaded:
        st.caption("⚠ Need price > 0 and shares > 0")

    if st.session_state.log_messages:
        with st.expander("Log", expanded=False):
            for ts, msg, level in st.session_state.log_messages[-20:]:
                color = {"ok": "green", "err": "red", "warn": "orange", "info": "blue"}.get(level, "gray")
                st.markdown(f"<small style='color:{color}'>`{ts}` {msg}</small>", unsafe_allow_html=True)

# ════════════════════════════════════════
#  MAIN CONTENT
# ════════════════════════════════════════
if not st.session_state.dcf_result:
    if st.session_state.filing_loaded and (st.session_state.price <= 0 or st.session_state.shares_mil <= 0):
        st.warning("Filing loaded! Enter price and shares in the sidebar, then click **Run Valuation**.")
    elif st.session_state.filing_loaded:
        st.info("Filing loaded. Click **Run Valuation** in the sidebar.")
    else:
        st.markdown("""
        <div style="text-align: center; padding: 80px 20px;">
            <h1 style="font-size: 3rem; font-weight: 800; color: #d1d5db;">Clarity</h1>
            <p style="color: #6b7280; font-size: 1.1rem; margin-top: -10px;">SEC Filing → Multi-Model Valuation in seconds</p>
            <p style="color: #4b5563; font-size: 0.9rem; margin-top: 30px;">Enter a ticker in the sidebar and pull a filing to get started.<br>
            DCF · Residual Income · Comps · ROIC Fade · EV/Revenue · DDM · Monte Carlo</p>
        </div>""", unsafe_allow_html=True)
    st.stop()

r = st.session_state.dcf_result
fins = st.session_state.fins
price = st.session_state.price
sector = st.session_state.sector
mm = r.get('multi_model') or {}

if mm and mm.get('blended_fv'):
    fv = mm['blended_fv']; upside = mm['blended_up']; verdict = mm['blended_verdict']
else:
    fv = r.get('pw_fv', 0); upside = r.get('pw_up', 0); verdict = r.get('verdict', 'HOLD')
verdict_class = "buy" if "BUY" in verdict else "sell" if "SELL" in verdict else "hold"

st.markdown(f"### {st.session_state.ticker or '—'} — {st.session_state.company_name or ''}")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Fair Value", f"${fv:.2f}", f"{upside:+.1f}%")
c2.metric("Market Price", f"${price:.2f}")
c3.metric("WACC", f"{r.get('wacc', 0)*100:.2f}%")
mc = mm.get('monte_carlo') if mm else None
c4.metric("P(Upside)", f"{mc['prob_above_price']:.0f}%" if mc and mc.get('prob_above_price') is not None else "—")
c5.markdown(f'<div style="text-align:center;padding-top:8px"><div style="color:#6b7280;font-size:0.7rem;text-transform:uppercase">Verdict</div><div class="verdict-{verdict_class}">{verdict}</div></div>', unsafe_allow_html=True)

st.divider()

# ── TABS ──
tab_overview, tab_models, tab_scenarios, tab_financials, tab_context = st.tabs(["Overview", "Models", "Scenarios", "Financials", "Market Context"])

with tab_overview:
    pp = r.get('price_paths')
    if pp and pp.get('pw_path') and len(pp['pw_path']) > 1:
        pw = pp['pw_path']; mkt_price = pp.get('market_price', price)
        dcf_start = pw[0]; blended_start = fv
        if dcf_start > 0 and blended_start > 0:
            sf = blended_start / dcf_start
            pw_s = [p*sf for p in pw]; scen_s = [[p*sf for p in sc['path']] for sc in pp['scenarios']]
        else:
            pw_s = list(pw); scen_s = [list(sc['path']) for sc in pp['scenarios']]
        ny = min(11, len(pw_s)); years = list(range(ny))
        upper = [max(s[y] for s in scen_s) for y in range(ny)]
        lower = [min(s[y] for s in scen_s) for y in range(ny)]
        iu, il = [], []
        for y in range(ny):
            vals = [(scen_s[si][y], pp['scenarios'][si].get('prob', 0.2)) for si in range(len(scen_s))]
            pv = pw_s[y]; var = sum(p*(v-pv)**2 for v,p in vals); sig = max(var**0.5, pv*0.02)
            iu.append(pv+sig); il.append(max(pv-sig, pv*0.1))
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=years+years[::-1], y=upper+lower[::-1], fill='toself', fillcolor='rgba(42,74,58,0.15)', line=dict(width=0), showlegend=False, hoverinfo='skip'))
        fig.add_trace(go.Scatter(x=years+years[::-1], y=iu+il[::-1], fill='toself', fillcolor='rgba(30,80,55,0.25)', line=dict(width=0), showlegend=False, hoverinfo='skip'))
        fig.add_trace(go.Scatter(x=years, y=pw_s, mode='lines+markers', name='Fair Value', line=dict(color='#3ecf8e', width=3), marker=dict(size=6, color='#3ecf8e'), hovertemplate='Year %{x}: $%{y:,.2f}<extra></extra>'))
        if mkt_price > 0: fig.add_hline(y=mkt_price, line_dash="dash", line_color="#f85149", annotation_text=f"Market ${mkt_price:,.0f}", annotation_font_color="#f85149")
        sc_colors = ['#5aaa70','#4a9a65','#888888','#aa6a5a','#aa4a4a']
        for i, (sc, sp) in enumerate(zip(pp['scenarios'], scen_s)):
            fig.add_trace(go.Scatter(x=years, y=sp, mode='lines', name=sc.get('name',''), line=dict(color=sc_colors[i%5], width=1, dash='dot'), opacity=0.5))
        fig.update_layout(title=dict(text="Intrinsic Value · 10Y Projection", font=dict(color='#b0b8c8', size=16)), plot_bgcolor='#111520', paper_bgcolor='#0f1117', xaxis=dict(title="Year", gridcolor='#1c2030', color='#6b7280', dtick=1), yaxis=dict(title="$/Share", gridcolor='#1c2030', color='#6b7280', tickformat='$,.0f'), legend=dict(font=dict(color='#9ca3af', size=10), bgcolor='rgba(0,0,0,0)'), margin=dict(l=60,r=30,t=50,b=40), height=420, hovermode='x unified')
        st.plotly_chart(fig, use_container_width=True)
        st.markdown("##### 10-Year Projection")
        rows = []
        for yr in [0,1,3,5,7,10]:
            if yr < len(pw_s):
                cagr = (pw_s[yr]/pw_s[0])**(1/max(yr,1))-1 if pw_s[0]>0 and yr>0 else 0
                rows.append({"Year": f"Y{yr}" if yr>0 else "Today", "Projected FV": f"${pw_s[yr]:,.2f}", "CAGR": f"{cagr*100:+.1f}%" if yr>0 else "—"})
        st.dataframe(rows, use_container_width=True, hide_index=True)
    if mc:
        st.markdown("##### Monte Carlo Distribution")
        mc_c = st.columns(6)
        mc_c[0].metric("P10", f"${mc['p10']:,.0f}"); mc_c[1].metric("P25", f"${mc['p25']:,.0f}")
        mc_c[2].metric("Median", f"${mc['median']:,.0f}"); mc_c[3].metric("P75", f"${mc['p75']:,.0f}")
        mc_c[4].metric("P90", f"${mc['p90']:,.0f}")
        mc_c[5].metric("P(>Price)", f"{mc['prob_above_price']:.0f}%" if mc.get('prob_above_price') is not None else "—")
        st.caption(f"{mc.get('iterations',5000):,} iterations")

with tab_models:
    if mm and mm.get('blended_fv'):
        st.markdown("##### Multi-Model Triangulation")
        ind = mm.get('individual', {}); wts = mm.get('weights', {})
        MN = {'dcf':'DCF','residual_income':'Residual Income','comps':'Comps','ev_revenue':'EV/Rev Norm','roic_fade':'ROIC Fade','ddm':'DDM'}
        mrows = []; mkeys = []
        for k in ['dcf','residual_income','comps','ev_revenue','roic_fade','ddm']:
            v = ind.get(k); w = wts.get(k,0)
            if v and v > 0:
                mrows.append({"Model": MN.get(k,k), "Fair Value": f"${v:,.2f}", "Weight": f"{w*100:.1f}%", "vs Price": f"{(v-price)/price*100:+.1f}%" if price>0 else "—"})
                mkeys.append(k)
        st.dataframe(mrows, use_container_width=True, hide_index=True)
        epv = mm.get('epv_floor')
        if epv and epv > 0: st.caption(f"EPV (no-growth floor): ${epv:,.2f}")
        agree = mm.get('agreement','N/A'); spread = mm.get('spread',0)
        ac = "#3ecf8e" if agree=="HIGH" else "#d29922" if agree in ("MODERATE","LOW") else "#f85149"
        st.markdown(f"**Agreement:** <span style='color:{ac}'>{agree}</span> ({spread:.0f}% spread)", unsafe_allow_html=True)
    else:
        st.info("Multi-model data not available.")

with tab_scenarios:
    st.markdown("##### DCF Scenarios")
    scens = r.get('scenarios', [])
    if scens:
        srows = [{"Scenario": s['name'], "Prob": f"{s['prob']*100:.0f}%", "FV": f"${s['fv']:,.2f}", "Upside": f"{s['upside']:+.1f}%"} for s in scens if isinstance(s, dict)]
        st.dataframe(srows, use_container_width=True, hide_index=True)
        names = [s['Scenario'] for s in srows]; fvs = [float(s['FV'].replace('$','').replace(',','')) for s in srows]
        fig_s = go.Figure(go.Bar(x=names, y=fvs, marker_color=['#3ecf8e' if f>price else '#f85149' for f in fvs], text=[f"${f:,.0f}" for f in fvs], textposition='outside', textfont=dict(color='#d1d5db',size=11)))
        fig_s.add_hline(y=price, line_dash="dash", line_color="#f85149", annotation_text=f"Market ${price:,.0f}")
        fig_s.update_layout(plot_bgcolor='#111520', paper_bgcolor='#0f1117', xaxis=dict(color='#6b7280',tickfont=dict(size=9)), yaxis=dict(title='$/Share',color='#6b7280',gridcolor='#1c2030',tickformat='$,.0f'), height=350, margin=dict(l=60,r=20,t=30,b=80))
        st.plotly_chart(fig_s, use_container_width=True)
    st.markdown("##### Growth")
    gc1,gc2,gc3 = st.columns(3)
    tg=r.get('trailing_growth',r.get('trailing')); ig=r.get('implied_fcf_growth',r.get('implied_fcf')); ir=r.get('implied_rev_growth',r.get('implied_rev'))
    gc1.metric("Trailing Rev", f"{tg*100:.1f}%" if tg else "—"); gc2.metric("Implied FCF", f"{ig*100:.1f}%" if ig else ">80%"); gc3.metric("Implied Rev", f"{ir*100:.1f}%" if ir else ">80%")
    for flag in r.get('sanity_flags', []): st.warning(f"⚠ {flag}")

with tab_financials:
    if fins:
        inp = r.get('inputs', {}); st.markdown("##### Key Financials")
        items = [("Revenue", fins.get('revenue')), ("Net Income", fins.get('net_income')), ("Op Income", fins.get('operating_income')), ("Op CF", fins.get('operating_cf')), ("CapEx", fins.get('capex')), ("D&A", fins.get('depreciation')), ("FCF (used)", inp.get('fcf',r.get('fcf'))), ("Cash", fins.get('cash')), ("LT Debt", fins.get('long_term_debt')), ("Equity", fins.get('stockholders_equity')), ("EPS", fins.get('eps_diluted'))]
        frows = [{"Metric": l, "Value": fmt(v) if l!="EPS" else f"${v:.2f}"} for l,v in items if v is not None]
        st.dataframe(frows, use_container_width=True, hide_index=True)
        capex_method = inp.get('capex_method', r.get('capex_method', '—'))
        if isinstance(r.get('capex'), dict): capex_method = r['capex'].get('method', capex_method)
        st.caption(f"FCF Method: {capex_method}")
        lc = fins.get('_live_comps')
        st.caption(f"Sector: {SECTOR_NAMES.get(sector, sector)} · {fins.get('_form','?')} · Comps: {'Live ('+str(lc['peer_count'])+' peers)' if lc else 'Static'}")

with tab_context:
    mi=r.get('market_implies'); af=r.get('asset_floor'); evr=r.get('ev_rev_multiple'); cc=r.get('comps_check')
    if mi:
        st.markdown("##### Market-Implied Growth")
        cagr=mi.get('implied_rev_cagr')
        if isinstance(cagr, str): st.error(f"Implied CAGR: {cagr}")
        else:
            m1,m2,m3=st.columns(3); m1.metric("Implied Rev CAGR", f"{cagr:.1f}%"); m2.metric("At Margin", f"{mi.get('assumed_margin','')}%")
            ps=mi.get('implied_ps_now')
            if ps: m3.metric("Current P/S", f"{ps:.1f}x")
    if cc:
        st.markdown("##### Comps Check")
        c1,c2,c3=st.columns(3); cpe=cc.get('current_pe')
        if cpe: c1.metric("P/E", f"{cpe:.1f}x"); c2.metric(f"Sector P/E", f"{cc['sector_pe']}x"); c3.metric("P/E FV", f"${cc['pe_fv']:,.2f}")
        eveb=cc.get('evebitda_fv')
        if eveb: st.caption(f"EV/EBITDA: sector {cc['sector_evebitda']}x → ${eveb:,.0f}")
    if evr:
        st.markdown("##### EV/Revenue")
        e1,e2,e3=st.columns(3); e1.metric("Current", f"{evr['current']:.1f}x"); e2.metric("Sector", f"{evr['sector_median']:.1f}x"); e3.metric("Premium", f"{evr['premium_pct']:+.0f}%")
    if af:
        st.markdown("##### Asset Floor")
        a1,a2,a3=st.columns(3); a1.metric("Book/Share", f"${af['book_per_share']:,.2f}"); a2.metric("Tangible", f"${af['tangible_floor']:,.2f}")
        btp=af.get('book_to_price')
        if btp: a3.metric("Book % Price", f"{btp:.0f}%")
    if not (mi or af or evr or cc): st.info("No market context data.")
