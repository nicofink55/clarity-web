"""
C L A R I T Y  —  Web Edition  (v2 — visual overhaul)
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

st.set_page_config(page_title="Clarity — Equity Valuation Engine", page_icon="📊", layout="wide", initial_sidebar_state="collapsed")

# ════════════════════════════════════════
#  THEME & CSS
# ════════════════════════════════════════
st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&family=Playfair+Display:ital,wght@0,700;1,700&display=swap');

    /* ── Logo animations ── */
    @keyframes logoFloat { 0%,100% { transform: translateY(0); } 50% { transform: translateY(-4px); } }
    @keyframes holoShift { 0% { filter: hue-rotate(0deg) brightness(1); } 50% { filter: hue-rotate(15deg) brightness(1.15); } 100% { filter: hue-rotate(0deg) brightness(1); } }
    .logo-icon {
        width: 42px; height: 42px; border-radius: 12px;
        background: linear-gradient(135deg, rgba(62,207,142,0.2), rgba(30,120,90,0.4));
        border: 1px solid rgba(62,207,142,0.25);
        display: flex; align-items: center; justify-content: center;
        font-family: 'Playfair Display', serif; font-weight: 700; font-style: italic;
        font-size: 1.4rem; color: #3ecf8e;
        box-shadow: 0 4px 20px rgba(62,207,142,0.15), inset 0 1px 0 rgba(255,255,255,0.05);
        animation: logoFloat 4s ease-in-out infinite, holoShift 6s ease-in-out infinite;
    }
    .logo-text { font-family: Inter,sans-serif; font-weight: 600; font-size: 1.1rem; color: #e2e8f0; letter-spacing: 0.2em; text-transform: uppercase; }

    /* ══ BASE ══ */
    .stApp { background: radial-gradient(ellipse at 20% 50%, rgba(10,25,20,1) 0%, #060910 50%, #050810 100%); }
    section[data-testid="stSidebar"] { background: rgba(8,12,20,0.97); border-right: 1px solid rgba(62,207,142,0.06); }
    section[data-testid="stSidebar"], section[data-testid="stSidebar"] > div,
    section[data-testid="stSidebar"] [data-testid="stSidebarContent"],
    section[data-testid="stSidebar"] .stMainBlockContainer {
        overflow-y: auto !important; overflow-x: hidden !important;
        max-height: 100vh !important;
    }
    h1,h2,h3 { color: #e2e8f0 !important; font-family: Inter,sans-serif !important; }
    .stMarkdown p, .stMarkdown li { color: #8b95a8; font-family: Inter,sans-serif; }
    #MainMenu, footer { visibility: hidden; }
    .block-container { padding-top: 2.5rem !important; }

    /* ── Z-index layering for neural bg ── */
    .stApp > * { position: relative; z-index: 1; }
    section[data-testid="stSidebar"] > * { position: relative; z-index: 1; }

    /* ── Hide injector img ── */
    img[onerror] { display: none !important; }

    /* ══ METRICS ══ */
    [data-testid="stMetricValue"] { color: #3ecf8e !important; font-family: JetBrains Mono,monospace !important; font-weight: 600 !important; }
    [data-testid="stMetricLabel"] { color: #5a6478 !important; text-transform: uppercase; font-size: 0.65rem !important; letter-spacing: 0.1em; font-family: Inter,sans-serif !important; }

    /* ══ INPUTS ══ */
    .stTextInput input, .stNumberInput input {
        background: rgba(12,18,30,0.7) !important; color: #3ecf8e !important;
        border: 1px solid rgba(62,207,142,0.12) !important; border-radius: 10px !important;
        font-family: JetBrains Mono,monospace !important; font-weight: 600 !important;
        backdrop-filter: blur(12px); transition: all 0.3s ease;
    }
    .stTextInput input:focus, .stNumberInput input:focus {
        border-color: rgba(62,207,142,0.4) !important;
        box-shadow: 0 0 20px rgba(62,207,142,0.1), inset 0 0 20px rgba(62,207,142,0.03) !important;
    }
    .stSelectbox > div > div { background: rgba(12,18,30,0.7); color: #cbd5e1; border: 1px solid rgba(62,207,142,0.08); border-radius: 10px; }
    input[aria-label="Ticker"] { text-transform: uppercase !important; font-size: 1.1rem !important; }

    /* ══ BUTTONS ══ */
    .stButton > button, .stFormSubmitButton > button {
        background: rgba(12,18,30,0.5); color: #8b95a8; border: 1px solid rgba(62,207,142,0.08);
        font-weight: 600; border-radius: 10px; transition: all 0.3s ease; font-family: Inter,sans-serif;
        backdrop-filter: blur(8px);
    }
    .stButton > button:hover, .stFormSubmitButton > button:hover {
        background: rgba(16,24,40,0.7); border-color: rgba(62,207,142,0.3); color: #3ecf8e;
        box-shadow: 0 0 24px rgba(62,207,142,0.06), inset 0 0 24px rgba(62,207,142,0.02);
    }
    button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
        background: linear-gradient(135deg, #3ecf8e 0%, #1fa870 100%) !important;
        color: #060910 !important; border: none !important; font-weight: 700 !important;
        border-radius: 10px !important; box-shadow: 0 4px 24px rgba(62,207,142,0.2), 0 0 60px rgba(62,207,142,0.06);
    }
    button[kind="primary"]:hover, .stFormSubmitButton > button[kind="primary"]:hover {
        box-shadow: 0 4px 32px rgba(62,207,142,0.35), 0 0 80px rgba(62,207,142,0.1) !important;
        transform: translateY(-1px);
    }

    /* ══ TABS ══ */
    .stTabs [data-baseweb="tab-list"] { gap: 0; border-bottom: 1px solid rgba(62,207,142,0.06); }
    .stTabs [data-baseweb="tab"] { color: #3d4655; font-family: Inter,sans-serif; font-weight: 500; font-size: 0.85rem; padding: 12px 22px; transition: all 0.3s ease; }
    .stTabs [aria-selected="true"] { color: #3ecf8e !important; border-bottom: 2px solid #3ecf8e !important; font-weight: 600; text-shadow: 0 0 24px rgba(62,207,142,0.3); }
    .stTabs [data-baseweb="tab"]:hover { color: #8b95a8; }
    hr { border-color: rgba(62,207,142,0.04) !important; margin: 0.75rem 0 !important; }

    /* ══ GLASS CARD — Horizon style ══ */
    .metric-card {
        background: rgba(14,20,32,0.85);
        border: 1px solid rgba(62,207,142,0.08);
        border-radius: 16px; padding: 22px 24px;
        text-align: center; backdrop-filter: blur(16px) saturate(1.2);
        transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative; overflow: hidden;
        box-shadow: 0 4px 24px rgba(0,0,0,0.3), inset 0 1px 0 rgba(255,255,255,0.03);
    }
    .metric-card::before {
        content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px;
        background: linear-gradient(90deg, transparent 10%, rgba(62,207,142,0.25) 50%, transparent 90%);
        opacity: 0; transition: opacity 0.4s ease;
    }
    .metric-card::after {
        content: ''; position: absolute; inset: 0; border-radius: 16px;
        background: radial-gradient(ellipse at 50% 0%, rgba(62,207,142,0.03) 0%, transparent 70%);
        pointer-events: none;
    }
    .metric-card:hover {
        border-color: rgba(62,207,142,0.2); transform: translateY(-3px);
        box-shadow: 0 12px 40px rgba(0,0,0,0.4), 0 0 30px rgba(62,207,142,0.04), inset 0 1px 0 rgba(255,255,255,0.05);
    }
    .metric-card:hover::before { opacity: 1; }

    /* Verdict card variants */
    .metric-card.verdict-sell { border-color: rgba(248,81,73,0.12); }
    .metric-card.verdict-sell:hover { border-color: rgba(248,81,73,0.3); box-shadow: 0 12px 40px rgba(0,0,0,0.4), 0 0 30px rgba(248,81,73,0.06); }
    .metric-card.verdict-sell::before { background: linear-gradient(90deg, transparent 10%, rgba(248,81,73,0.25) 50%, transparent 90%); }
    .metric-card.verdict-sell::after { background: radial-gradient(ellipse at 50% 0%, rgba(248,81,73,0.04) 0%, transparent 70%); }
    .metric-card.verdict-buy { border-color: rgba(62,207,142,0.12); }
    .metric-card.verdict-buy:hover { border-color: rgba(62,207,142,0.3); box-shadow: 0 12px 40px rgba(0,0,0,0.4), 0 0 30px rgba(62,207,142,0.06); }
    .metric-card.verdict-hold { border-color: rgba(210,153,34,0.12); }
    .metric-card.verdict-hold:hover { border-color: rgba(210,153,34,0.3); box-shadow: 0 12px 40px rgba(0,0,0,0.4), 0 0 30px rgba(210,153,34,0.06); }
    .metric-card.verdict-hold::before { background: linear-gradient(90deg, transparent 10%, rgba(210,153,34,0.25) 50%, transparent 90%); }
    .metric-card.verdict-hold::after { background: radial-gradient(ellipse at 50% 0%, rgba(210,153,34,0.04) 0%, transparent 70%); }

    .metric-card .label { color: #5a6478; font-size: 0.6rem; text-transform: uppercase; letter-spacing: 0.12em; font-family: Inter,sans-serif; margin-bottom: 8px; }
    .metric-card .value { font-family: JetBrains Mono,monospace; font-weight: 700; font-size: 1.6rem; line-height: 1.2; }
    .metric-card .sub { font-family: JetBrains Mono,monospace; font-size: 0.8rem; margin-top: 6px; }
    .metric-card.green .value { color: #3ecf8e; text-shadow: 0 0 30px rgba(62,207,142,0.15); }
    .metric-card.red .value { color: #f85149; text-shadow: 0 0 30px rgba(248,81,73,0.15); }
    .metric-card.white .value { color: #e2e8f0; }
    .metric-card.amber .value { color: #d29922; text-shadow: 0 0 30px rgba(210,153,34,0.15); }

    /* ══ SECTION CARD — Glass panel ══ */
    .section-card {
        background: rgba(14,20,32,0.8);
        border: 1px solid rgba(62,207,142,0.06);
        border-radius: 16px; padding: 28px; margin-bottom: 16px;
        backdrop-filter: blur(16px) saturate(1.2);
        transition: all 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        box-shadow: 0 4px 24px rgba(0,0,0,0.2), inset 0 1px 0 rgba(255,255,255,0.02);
    }
    .section-card::before {
        content: ''; position: absolute; top: 0; left: 20px; right: 20px; height: 1px;
        background: linear-gradient(90deg, transparent, rgba(62,207,142,0.15), transparent);
    }
    .section-card:hover { border-color: rgba(62,207,142,0.12); box-shadow: 0 8px 32px rgba(0,0,0,0.3), 0 0 20px rgba(62,207,142,0.02); }
    .section-card h4 { color: #3ecf8e; font-family: Inter,sans-serif; font-weight: 600; font-size: 0.9rem; margin: 0 0 18px 0; letter-spacing: 0.01em; }

    /* ══ TABLE — Horizon glass rows ══ */
    .styled-table { width: 100%; border-collapse: separate; border-spacing: 0; border-radius: 10px; overflow: hidden; font-family: Inter,sans-serif; }
    .styled-table thead th {
        background: rgba(10,16,28,0.9); color: #5a6478; font-size: 0.65rem; text-transform: uppercase;
        letter-spacing: 0.1em; padding: 12px 18px; text-align: left; font-weight: 600;
        border-bottom: 1px solid rgba(62,207,142,0.06);
    }
    .styled-table tbody td {
        padding: 13px 18px; color: #c0c8d8; font-size: 0.85rem;
        border-bottom: 1px solid rgba(62,207,142,0.03);
        transition: all 0.25s ease;
    }
    .styled-table tbody tr { background: rgba(10,16,26,0.7); transition: all 0.25s ease; }
    .styled-table tbody tr:hover { background: rgba(62,207,142,0.03); }
    .styled-table .num { font-family: JetBrains Mono,monospace; font-weight: 500; }
    .styled-table .pos { color: #3ecf8e; }
    .styled-table .neg { color: #f85149; }
    .styled-table .dim { color: #3d4655; }

    /* ══ VERDICT BADGES ══ */
    .verdict-badge {
        display: inline-block; padding: 8px 22px; border-radius: 10px;
        font-family: JetBrains Mono,monospace; font-weight: 800; font-size: 1.05rem;
        letter-spacing: 0.06em;
    }
    .verdict-badge.buy { background: rgba(62,207,142,0.1); color: #3ecf8e; border: 1px solid rgba(62,207,142,0.2); animation: buyPulse 4s ease-in-out infinite; }
    .verdict-badge.sell { background: rgba(248,81,73,0.1); color: #f85149; border: 1px solid rgba(248,81,73,0.2); animation: sellPulse 4s ease-in-out infinite; }
    .verdict-badge.hold { background: rgba(210,153,34,0.1); color: #d29922; border: 1px solid rgba(210,153,34,0.2); animation: holdPulse 4s ease-in-out infinite; }

    @keyframes buyPulse {
        0%,100% { box-shadow: 0 0 12px rgba(62,207,142,0.08); }
        50% { box-shadow: 0 0 28px rgba(62,207,142,0.18), 0 0 60px rgba(62,207,142,0.04); }
    }
    @keyframes sellPulse {
        0%,100% { box-shadow: 0 0 12px rgba(248,81,73,0.08); }
        50% { box-shadow: 0 0 28px rgba(248,81,73,0.18), 0 0 60px rgba(248,81,73,0.04); }
    }
    @keyframes holdPulse {
        0%,100% { box-shadow: 0 0 12px rgba(210,153,34,0.08); }
        50% { box-shadow: 0 0 28px rgba(210,153,34,0.18), 0 0 60px rgba(210,153,34,0.04); }
    }

    /* ── Breathing glow ── */
    @keyframes breatheGlow {
        0%,100% { text-shadow: 0 0 20px rgba(62,207,142,0.15); }
        50% { text-shadow: 0 0 40px rgba(62,207,142,0.3), 0 0 80px rgba(62,207,142,0.06); }
    }
    .glow-value { animation: breatheGlow 4s ease-in-out infinite; }

    /* ── Shimmer divider ── */
    @keyframes shimmer { 0% { background-position: -200% 0; } 100% { background-position: 200% 0; } }
    .shimmer-line {
        height: 1px; margin: 16px 0;
        background: linear-gradient(90deg, transparent, rgba(62,207,142,0.25), transparent);
        background-size: 200% 100%; animation: shimmer 4s ease-in-out infinite;
    }

    /* ── Utility ── */
    div[data-testid="stExpander"] { background: rgba(10,15,25,0.6); border: 1px solid rgba(62,207,142,0.06); border-radius: 12px; backdrop-filter: blur(12px); }
    .sidebar-section { color: #8b95a8; font-family: Inter,sans-serif; font-weight: 600; font-size: 0.75rem; letter-spacing: 0.06em; margin-bottom: 8px; text-transform: uppercase; }
    .stAlert { border-radius: 10px; backdrop-filter: blur(8px); }

    /* ── Scrollbar ── */
    ::-webkit-scrollbar { width: 5px; }
    ::-webkit-scrollbar-track { background: transparent; }
    ::-webkit-scrollbar-thumb { background: rgba(62,207,142,0.1); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: rgba(62,207,142,0.2); }

    /* ── Mobile responsive ── */
    @media (max-width: 768px) {
        .block-container { padding-top: 1rem !important; padding-left: 0.5rem !important; padding-right: 0.5rem !important; }
        .metric-card { padding: 14px 16px; border-radius: 12px; margin-bottom: 8px; }
        .metric-card .value { font-size: 1.3rem; }
        .metric-card .label { font-size: 0.55rem; margin-bottom: 4px; }
        .metric-card .sub { font-size: 0.7rem; }
        .section-card { padding: 16px; border-radius: 12px; }
        .section-card h4 { font-size: 0.85rem; margin-bottom: 12px; }
        .styled-table thead th { padding: 8px 10px; font-size: 0.6rem; }
        .styled-table tbody td { padding: 8px 10px; font-size: 0.8rem; }
        .verdict-badge { padding: 6px 16px; font-size: 0.9rem; }
    }

    /* ── Landing page overrides ── */
    [data-testid="stForm"] { background: transparent !important; border: none !important; padding: 0 !important; }
    [data-testid="stForm"] .stTextInput input {
        background: rgba(14,20,32,0.8) !important; color: #e2e8f0 !important;
        border: 1px solid rgba(62,207,142,0.12) !important; border-radius: 12px !important;
        font-family: Inter,sans-serif !important; font-size: 0.95rem !important;
        font-weight: 400 !important; padding: 14px 18px !important;
        height: auto !important;
    }
    [data-testid="stForm"] .stTextInput input::placeholder { color: #3d4655 !important; }
    [data-testid="stForm"] .stTextInput input:focus {
        border-color: rgba(62,207,142,0.35) !important;
        box-shadow: 0 0 24px rgba(62,207,142,0.08) !important;
    }
</style>
""", unsafe_allow_html=True)


# Neural network background - inject directly into main page via img onerror
# (components.html iframes get throttled on desktop Chrome, killing the animation)
st.markdown("""
<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" style="display:none"
onerror="
if(!document.getElementById('neural-canvas')){
var c=document.createElement('canvas');c.id='neural-canvas';
c.style.cssText='position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:0;pointer-events:none;';
document.body.appendChild(c);
var ctx=c.getContext('2d'),w,h,ns=[];
function _nr(){w=c.width=document.documentElement.clientWidth;h=c.height=document.documentElement.clientHeight;}
_nr();window.addEventListener('resize',_nr);
for(var i=0;i<90;i++){ns.push({x:Math.random()*2000,y:Math.random()*1200,vx:(Math.random()-0.5)*0.4,vy:(Math.random()-0.5)*0.4,r:Math.random()*2.2+1,p:Math.random()*Math.PI*2,ps:Math.random()*0.01+0.005});}
if(window._nInt)clearInterval(window._nInt);
window._nInt=setInterval(function(){
ctx.clearRect(0,0,w,h);
for(var i=0;i<ns.length;i++){var n=ns[i];n.x+=n.vx;n.y+=n.vy;n.p+=n.ps;
if(n.x<-30)n.x=w+30;if(n.x>w+30)n.x=-30;if(n.y<-30)n.y=h+30;if(n.y>h+30)n.y=-30;
n.vx+=(Math.random()-0.5)*0.012;n.vy+=(Math.random()-0.5)*0.012;n.vx*=0.999;n.vy*=0.999;
var sp=Math.sqrt(n.vx*n.vx+n.vy*n.vy);if(sp<0.15){n.vx+=(Math.random()-0.5)*0.3;n.vy+=(Math.random()-0.5)*0.3;}
for(var j=i+1;j<ns.length;j++){var m=ns[j],dx=n.x-m.x,dy=n.y-m.y,d=Math.sqrt(dx*dx+dy*dy);
if(d<200){ctx.beginPath();ctx.moveTo(n.x,n.y);ctx.lineTo(m.x,m.y);ctx.strokeStyle='rgba(62,207,142,'+((1-d/200)*0.25)+')';ctx.lineWidth=0.6;ctx.stroke();}}
var a=0.5+Math.sin(n.p)*0.25;ctx.beginPath();ctx.arc(n.x,n.y,n.r,0,Math.PI*2);ctx.fillStyle='rgba(62,207,142,'+a+')';ctx.fill();
if(n.r>1.5){ctx.beginPath();ctx.arc(n.x,n.y,n.r+4,0,Math.PI*2);ctx.fillStyle='rgba(62,207,142,'+(a*0.25)+')';ctx.fill();}}
},33);
}
">
""", unsafe_allow_html=True)



# ════════════════════════════════════════
#  HELPERS
# ════════════════════════════════════════

def card(label, value, sub="", style="green", glow=False):
    glow_cls = " glow-value" if glow else ""
    sub_html = f'<div class="sub" style="color:{"#3ecf8e" if "+" in sub else "#f85149" if "-" in sub else "#64748b"}">{sub}</div>' if sub else ""
    return f'<div class="metric-card {style}"><div class="label">{label}</div><div class="value{glow_cls}">{value}</div>{sub_html}</div>'

def html_table(headers, rows, col_styles=None):
    """Build a styled HTML table. col_styles: list of dicts per column for CSS classes."""
    html = '<table class="styled-table"><thead><tr>'
    for h in headers:
        html += f'<th>{h}</th>'
    html += '</tr></thead><tbody>'
    for row in rows:
        html += '<tr>'
        for i, cell in enumerate(row):
            cls = col_styles[i] if col_styles and i < len(col_styles) else ""
            html += f'<td class="{cls}">{cell}</td>'
        html += '</tr>'
    html += '</tbody></table>'
    return html

def color_val(val_str):
    """Wrap a string value with pos/neg color class."""
    if not val_str or val_str == "—":
        return f'<span class="dim">{val_str}</span>'
    if val_str.startswith('+') or (val_str.startswith('$') and not val_str.startswith('-')):
        return f'<span class="num pos">{val_str}</span>'
    if val_str.startswith('-'):
        return f'<span class="num neg">{val_str}</span>'
    return f'<span class="num">{val_str}</span>'


# ════════════════════════════════════════
#  SESSION STATE
# ════════════════════════════════════════

for key, default in [('fins', None), ('dcf_result', None), ('ticker', ''), ('price', 0.0),
    ('shares_mil', 0.0), ('sector', 'general'), ('beta', None),
    ('data_quality', {'quarters_available': 1}), ('log_messages', []),
    ('shares_source', ''), ('company_name', ''), ('filing_loaded', False),
    ('auto_loaded', False), ('filing_info', ''), ('price_ts', '')]:
    if key not in st.session_state:
        st.session_state[key] = default

def log(msg, level="info"):
    st.session_state.log_messages.append((time.strftime("%H:%M:%S"), msg, level))

def quick_run(ticker_val, form="10-K"):
    """Pull filing + market data + run valuation in one shot."""
    st.session_state.ticker = ticker_val
    st.session_state.dcf_result = None
    cik, name = lookup_cik(ticker_val)
    st.session_state.company_name = name
    info = find_filing(cik, form)
    st.session_state.filing_info = f"{info.get('form', form)} · Filed {info.get('date', '?')}"
    with tempfile.TemporaryDirectory() as tmpdir:
        path, size = download_filing(info, tmpdir)
        if path.lower().endswith('.pdf'): st.session_state.fins = parse_pdf(path)
        else: st.session_state.fins = parse_html(path)
    st.session_state.sector = st.session_state.fins.get('_sector', 'general')
    st.session_state.data_quality = {'quarters_available': 1}
    st.session_state.filing_loaded = True
    data = fetch_market_data(ticker_val)
    st.session_state.price = data['price']
    st.session_state.shares_mil = data['shares_mil']
    st.session_state.company_name = data.get('name', ticker_val)
    st.session_state.beta = data.get('beta')
    st.session_state.price_ts = time.strftime("%b %d, %Y · %I:%M %p", time.localtime())
    try:
        live_comps = fetch_live_comps(st.session_state.sector, log_fn=lambda m, t: log(m, t))
        if live_comps: st.session_state.fins['_live_comps'] = live_comps
    except: pass
    result = run_full_valuation(st.session_state.fins, st.session_state.price,
                                 st.session_state.shares_mil, st.session_state.sector,
                                 beta=st.session_state.beta, data_quality=st.session_state.data_quality)
    st.session_state.dcf_result = result

# ── URL Query Params: ?ticker=NVDA ──
qp = st.query_params
if qp.get("ticker") and not st.session_state.auto_loaded and not st.session_state.dcf_result:
    t = qp["ticker"].upper().strip()
    if t:
        st.session_state.auto_loaded = True
        try:
            with st.spinner(f"Loading {t}..."):
                quick_run(t)
            st.rerun()
        except Exception as e:
            st.error(f"Could not load {t}: {e}")

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
    st.markdown("""<div style="display:flex;align-items:center;gap:14px;padding:8px 0 16px 0">
        <div class="logo-icon">C</div>
        <div><span class="logo-text">Clarity</span><br><span style="color:#3d4655;font-size:0.6rem;font-family:Inter,sans-serif;letter-spacing:0.1em;text-transform:uppercase">Valuation Engine</span></div>
    </div>""", unsafe_allow_html=True)
    st.markdown('<hr style="margin: 0 0 16px 0; border-color: #1a1f2e">', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-section">Pull Filing</div>', unsafe_allow_html=True)
    with st.form("pull_form"):
        col1, col2 = st.columns([2, 1])
        with col1:
            ticker_input = st.text_input("Ticker", value=st.session_state.ticker, placeholder="AAPL", label_visibility="collapsed").upper().strip()
        with col2:
            form_type = st.selectbox("Form", ["10-Q", "10-K"], label_visibility="collapsed")
        filing_count = st.select_slider("Filings", options=[1, 2, 3, 4], value=1)
        pull_submitted = st.form_submit_button("Pull from EDGAR", use_container_width=True, type="primary")

    if pull_submitted and ticker_input:
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
                st.session_state.company_name = name
                progress.progress(30, f"Found {name}...")
                info = find_filing(cik, form_type)
                log(f"Found {info['form']} filed {info['date']}", "ok")
                st.session_state.filing_info = f"{info.get('form', form_type)} · Filed {info.get('date', '?')}"
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

            progress.progress(70, "Fetching market data...")
            try:
                data = fetch_market_data(ticker_input)
                st.session_state.price = data['price']
                st.session_state.shares_mil = data['shares_mil']
                st.session_state.company_name = data.get('name', ticker_input)
                st.session_state.beta = data.get('beta')
                st.session_state.shares_source = data.get('shares_source', '')
                filing_shares = st.session_state.fins.get('shares_diluted', 0) / 1e6 if st.session_state.fins else 0
                if data['shares_mil'] > 0 and filing_shares > 0:
                    ratio = data['shares_mil'] / filing_shares
                    if ratio < 0.50 or ratio > 2.0:
                        st.session_state.shares_mil = filing_shares
                        log(f"Shares corrected: {data['shares_mil']:.0f}M → {filing_shares:.0f}M", "warn")
                log(f"Market: ${data['price']:.2f} | {st.session_state.shares_mil:.1f}M shares", "ok")
                st.session_state.price_ts = time.strftime("%b %d, %Y · %I:%M %p", time.localtime())
            except Exception as e:
                log(f"Market data failed: {e}", "warn")
                status.warning(f"Market data failed: {e}")

            if st.session_state.price > 0 and st.session_state.shares_mil > 0:
                progress.progress(85, "Running valuation...")
                try:
                    run_valuation()
                    progress.progress(100, "Done!")
                    time.sleep(0.3)
                    progress.empty()
                except Exception as e:
                    progress.empty()
                    status.error(f"Valuation error: {e}")
                    log(f"Valuation error: {e}\n{traceback.format_exc()}", "err")
            else:
                progress.empty()
                status.warning("Enter price & shares, then Run Valuation.")
        except Exception as e:
            progress.empty()
            status.error(f"Error: {e}")
            log(f"Error: {e}\n{traceback.format_exc()}", "err")
        if st.session_state.ticker:
            st.query_params["ticker"] = st.session_state.ticker
        st.rerun()

    st.markdown('<hr style="margin: 16px 0; border-color: #1a1f2e">', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-section">Upload Filing</div>', unsafe_allow_html=True)
    uploaded = st.file_uploader("SEC filing (HTML/PDF)", type=["htm", "html", "pdf"], label_visibility="collapsed")
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

    st.markdown('<hr style="margin: 16px 0; border-color: #1a1f2e">', unsafe_allow_html=True)

    st.markdown('<div class="sidebar-section">Market Data</div>', unsafe_allow_html=True)
    new_price = st.number_input("Price ($)", value=float(st.session_state.price), min_value=0.0, step=0.01, format="%.2f")
    new_shares = st.number_input("Shares (M)", value=float(st.session_state.shares_mil), min_value=0.0, step=0.1, format="%.1f")
    if abs(new_price - st.session_state.price) > 0.001: st.session_state.price = new_price
    if abs(new_shares - st.session_state.shares_mil) > 0.001: st.session_state.shares_mil = new_shares

    sector_opts = list(SECTOR_NAMES.keys())
    cidx = sector_opts.index(st.session_state.sector) if st.session_state.sector in sector_opts else 0
    new_sector = st.selectbox("Sector", options=sector_opts, index=cidx,
                               format_func=lambda x: SECTOR_NAMES.get(x, x))
    st.session_state.sector = new_sector

    st.markdown('<div style="height: 8px"></div>', unsafe_allow_html=True)
    can_run = st.session_state.filing_loaded and st.session_state.price > 0 and st.session_state.shares_mil > 0
    if st.button("▶  Run Valuation", use_container_width=True, type="primary", disabled=not can_run):
        with st.spinner("Running valuation..."):
            try:
                run_valuation()
                st.rerun()
            except Exception as e:
                st.error(f"Error: {e}")
                log(f"Error: {e}\n{traceback.format_exc()}", "err")

    if st.session_state.log_messages:
        with st.expander("Log", expanded=False):
            for ts, msg, level in st.session_state.log_messages[-20:]:
                color = {"ok": "#3ecf8e", "err": "#f85149", "warn": "#d29922", "info": "#58a6ff"}.get(level, "#64748b")
                st.markdown(f"<span style='color:{color};font-family:JetBrains Mono,monospace;font-size:0.7rem'>{ts}</span> <span style='color:#94a3b8;font-size:0.75rem'>{msg}</span>", unsafe_allow_html=True)


# ════════════════════════════════════════
#  LANDING
# ════════════════════════════════════════

if not st.session_state.dcf_result:
    if st.session_state.filing_loaded and (st.session_state.price <= 0 or st.session_state.shares_mil <= 0):
        st.warning("Filing loaded — enter price & shares in the sidebar, then click **Run Valuation**.")
    elif st.session_state.filing_loaded:
        st.info("Filing loaded. Click **Run Valuation** in the sidebar.")
    else:
        # ── LANDING PAGE ──
        st.markdown("""
        <div style="text-align:center;padding:60px 20px 0">
            <div style="display:inline-flex;align-items:center;gap:14px;margin-bottom:20px">
                <div class="logo-icon" style="width:52px;height:52px;font-size:1.8rem;border-radius:14px">C</div>
                <span class="logo-text" style="font-size:1.6rem">Clarity</span>
            </div>
            <div class="shimmer-line" style="max-width:200px;margin:12px auto"></div>
            <div style="color:#8b95a8;font-size:1.05rem;margin:8px 0 0;font-family:Inter,sans-serif;max-width:520px;margin-left:auto;margin-right:auto;line-height:1.6">
                Multi-model equity valuation from SEC filings & live market data.<br>
                <span style="color:#5a6478">DCF · Residual Income · Comps · Monte Carlo — in seconds.</span>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Search bar ──
        st.markdown('<div style="height:32px"></div>', unsafe_allow_html=True)
        _, search_col, _ = st.columns([1, 2, 1])
        with search_col:
            with st.form("landing_search", clear_on_submit=False):
                s_c1, s_c2 = st.columns([3, 1])
                with s_c1:
                    search_ticker = st.text_input("Search", placeholder="Enter any ticker — AAPL, NVDA, MSFT...",
                                                   label_visibility="collapsed").upper().strip()
                with s_c2:
                    search_form = st.selectbox("Filing", ["10-K", "10-Q"], label_visibility="collapsed")
                search_go = st.form_submit_button("Analyze", use_container_width=True, type="primary")

        if search_go and search_ticker:
            try:
                with st.spinner(f"Analyzing {search_ticker}..."):
                    quick_run(search_ticker, form=search_form)
                st.query_params["ticker"] = search_ticker
                st.session_state.auto_loaded = True
                st.rerun()
            except Exception as e:
                st.error(f"Could not load {search_ticker}: {e}")

        # ── Featured tickers ──
        st.markdown('<div style="text-align:center;margin-top:28px;margin-bottom:8px;color:#3d4655;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.12em;font-family:Inter,sans-serif">Popular tickers</div>', unsafe_allow_html=True)
        featured = ["AAPL", "NVDA", "MSFT", "GOOG", "AMZN", "META", "SEZL", "PLTR"]
        feat_cols = st.columns(len(featured))
        for i, t in enumerate(featured):
            with feat_cols[i]:
                if st.button(t, key=f"feat_{t}", use_container_width=True):
                    try:
                        with st.spinner(f"Loading {t}..."):
                            quick_run(t)
                        st.query_params["ticker"] = t
                        st.session_state.auto_loaded = True
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error: {e}")

        # ── How it works ──
        st.markdown("""
        <div style="max-width:700px;margin:48px auto 0;text-align:center">
            <div style="color:#3ecf8e;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.12em;font-family:Inter,sans-serif;margin-bottom:16px">How it works</div>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px">
                <div class="section-card" style="text-align:center;padding:20px">
                    <div style="font-size:1.4rem;margin-bottom:8px">📄</div>
                    <div style="color:#e2e8f0;font-size:0.8rem;font-weight:600;font-family:Inter,sans-serif;margin-bottom:4px">Pull Filing</div>
                    <div style="color:#5a6478;font-size:0.72rem;font-family:Inter,sans-serif;line-height:1.5">Live 10-K/10-Q from SEC EDGAR with auto-parsed financials</div>
                </div>
                <div class="section-card" style="text-align:center;padding:20px">
                    <div style="font-size:1.4rem;margin-bottom:8px">🔬</div>
                    <div style="color:#e2e8f0;font-size:0.8rem;font-weight:600;font-family:Inter,sans-serif;margin-bottom:4px">Multi-Model</div>
                    <div style="color:#5a6478;font-size:0.72rem;font-family:Inter,sans-serif;line-height:1.5">DCF, Residual Income, Comps, ROIC Fade, DDM — triangulated</div>
                </div>
                <div class="section-card" style="text-align:center;padding:20px">
                    <div style="font-size:1.4rem;margin-bottom:8px">🎯</div>
                    <div style="color:#e2e8f0;font-size:0.8rem;font-weight:600;font-family:Inter,sans-serif;margin-bottom:4px">Fair Value</div>
                    <div style="color:#5a6478;font-size:0.72rem;font-family:Inter,sans-serif;line-height:1.5">Blended valuation with Monte Carlo confidence intervals</div>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        # ── Disclaimer ──
        st.markdown("""
        <div style="text-align:center;margin-top:60px;padding-bottom:30px;color:#2d3548;font-size:0.65rem;font-family:Inter,sans-serif;line-height:1.6">
            Not financial advice. For educational and research purposes only.<br>
            Valuations are model-driven estimates, not recommendations to buy or sell securities.
        </div>
        """, unsafe_allow_html=True)
    st.stop()


# ════════════════════════════════════════
#  RESULTS
# ════════════════════════════════════════

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
mc = mm.get('monte_carlo') if mm else None
ticker = st.session_state.ticker or "—"
company = st.session_state.company_name or ""

# ── Header ──
display_name = company if company and company.upper() != ticker else ""
filing_info = st.session_state.filing_info or ""
header_html = f'<div style="display:flex;align-items:baseline;gap:14px;margin-bottom:4px;padding-top:4px;flex-wrap:wrap">'
header_html += f'<span style="font-family:JetBrains Mono,monospace;font-size:2rem;font-weight:800;color:#e2e8f0;letter-spacing:-0.02em">{ticker}</span>'
if display_name:
    header_html += f'<span style="color:#4b5563;font-size:0.95rem;font-family:Inter,sans-serif;font-weight:400">{display_name}</span>'
if filing_info:
    header_html += f'<span style="color:#3d4655;font-size:0.65rem;font-family:JetBrains Mono,monospace;background:rgba(62,207,142,0.06);border:1px solid rgba(62,207,142,0.1);border-radius:6px;padding:3px 10px;letter-spacing:0.02em">{filing_info}</span>'
header_html += '</div>'
st.markdown(header_html, unsafe_allow_html=True)

# ── Sector verification bar ──
sector_name = SECTOR_NAMES.get(sector, sector)
confidence = st.session_state.fins.get('_sector_conf', 'medium') if st.session_state.fins else 'medium'
conf_color = '#3ecf8e' if confidence == 'high' else '#d29922' if confidence == 'medium' else '#f85149'
conf_icon = '✓' if confidence == 'high' else '⚠' if confidence == 'medium' else '⚠'
sv1, sv2, sv3 = st.columns([3, 2, 1])
with sv1:
    st.markdown(f'<div style="display:flex;align-items:center;gap:8px;padding:4px 0"><span style="color:{conf_color};font-size:0.75rem">{conf_icon}</span><span style="color:#8b95a8;font-size:0.78rem;font-family:Inter,sans-serif">Classified as <strong style="color:#c0c8d8">{sector_name}</strong></span><span style="color:#3d4655;font-size:0.65rem;font-family:Inter,sans-serif">({confidence} confidence)</span></div>', unsafe_allow_html=True)
with sv2:
    sector_opts = list(SECTOR_NAMES.keys())
    cidx = sector_opts.index(sector) if sector in sector_opts else 0
    new_sec = st.selectbox("Change sector", options=sector_opts, index=cidx,
                            format_func=lambda x: SECTOR_NAMES.get(x, x),
                            label_visibility="collapsed", key="sector_override")
with sv3:
    if st.button("Re-run", key="rerun_sector", use_container_width=True):
        st.session_state.sector = new_sec
        try:
            run_valuation()
            st.rerun()
        except Exception as e:
            st.error(f"Error: {e}")
if new_sec != sector:
    st.markdown(f'<div style="color:#d29922;font-size:0.72rem;font-family:Inter,sans-serif;margin:-4px 0 4px">Sector changed to {SECTOR_NAMES.get(new_sec, new_sec)} — click <strong>Re-run</strong> to update valuation</div>', unsafe_allow_html=True)

# ── Top metric cards ──
price_sub = st.session_state.price_ts if st.session_state.price_ts else ""
c1, c2, c3, c4, c5 = st.columns(5, gap="small")
with c1:
    st.markdown(card("Fair Value", f"${fv:,.2f}", f"{upside:+.1f}%", "green" if upside >= 0 else "red", glow=True), unsafe_allow_html=True)
with c2:
    st.markdown(card("Market Price", f"${price:,.2f}", price_sub, "white"), unsafe_allow_html=True)
with c3:
    st.markdown(card("WACC", f"{r.get('wacc',0)*100:.2f}%", "", "white"), unsafe_allow_html=True)
with c4:
    prob = mc.get('prob_above_price', 0) if mc else 0
    prob_style = "green" if prob >= 60 else "amber" if prob >= 40 else "red"
    st.markdown(card("P(Upside)", f"{prob:.0f}%" if prob else "—", f"{mc.get('iterations',5000):,} sims" if mc else "", prob_style), unsafe_allow_html=True)
with c5:
    st.markdown(f'<div class="metric-card verdict-{verdict_class}" style="background:{"rgba(62,207,142,0.06)" if "BUY" in verdict else "rgba(248,81,73,0.06)" if "SELL" in verdict else "rgba(210,153,34,0.06)"}"><div class="label">Verdict</div><div class="verdict-badge {verdict_class}">{verdict}</div></div>', unsafe_allow_html=True)

st.markdown('<div class="shimmer-line"></div>', unsafe_allow_html=True)

# ── TABS ──
tab_overview, tab_models, tab_scenarios, tab_financials, tab_context = st.tabs(["📈 Overview", "🔬 Models", "🎯 Scenarios", "📋 Financials", "🌐 Market Context"])


# ─────────────────────────────────
#  TAB: Overview
# ─────────────────────────────────
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
        # Outer uncertainty range (full scenario spread)
        fig.add_trace(go.Scatter(x=years+years[::-1], y=upper+lower[::-1],
            fill='toself', fillcolor='rgba(62,207,142,0.04)', line=dict(width=0),
            showlegend=False, hoverinfo='skip'))
        # Inner confidence band (1σ)
        fig.add_trace(go.Scatter(x=years+years[::-1], y=iu+il[::-1],
            fill='toself', fillcolor='rgba(62,207,142,0.08)', line=dict(width=0),
            showlegend=False, hoverinfo='skip'))
        # Gradient fill under fair value line
        fig.add_trace(go.Scatter(x=years, y=[0]*ny, mode='lines', line=dict(width=0),
            showlegend=False, hoverinfo='skip'))
        fig.add_trace(go.Scatter(x=years, y=pw_s, mode='lines', fill='tonexty',
            fillcolor='rgba(62,207,142,0.15)', line=dict(width=0),
            showlegend=False, hoverinfo='skip'))
        # Fair value line (main)
        fig.add_trace(go.Scatter(x=years, y=pw_s, mode='lines+markers', name='Fair Value',
            line=dict(color='#3ecf8e', width=2.5, shape='spline', smoothing=1.2),
            marker=dict(size=5, color='#3ecf8e', line=dict(width=1.5, color='#0a1018')),
            hovertemplate='Year %{x}: $%{y:,.2f}<extra></extra>'))
        # Market price reference
        if mkt_price > 0:
            fig.add_hline(y=mkt_price, line_dash="dot", line_color="rgba(248,81,73,0.4)", line_width=1,
                          annotation_text=f"Market ${mkt_price:,.0f}",
                          annotation_font=dict(color="#f85149", size=9, family="JetBrains Mono"),
                          annotation_position="bottom right")
        # Scenario paths (hidden by default)
        sc_colors = ['#22c55e','#3b82f6','#94a3b8','#f59e0b','#ef4444']
        for i, (sc, sp) in enumerate(zip(pp['scenarios'], scen_s)):
            fig.add_trace(go.Scatter(x=years, y=sp, mode='lines', name=sc.get('name',''),
                line=dict(color=sc_colors[i%5], width=1, dash='dot'), opacity=0.4,
                visible='legendonly',
                hovertemplate=f"{sc.get('name','')}: $%{{y:,.2f}}<extra></extra>"))

        fig.update_layout(
            plot_bgcolor='rgba(10,16,24,0.9)', paper_bgcolor='rgba(0,0,0,0)',
            xaxis=dict(title=None, gridcolor='rgba(62,207,142,0.05)', color='#3d4655', dtick=1,
                       zeroline=False, showline=True, linecolor='rgba(62,207,142,0.08)', linewidth=1,
                       tickfont=dict(family='JetBrains Mono', size=10)),
            yaxis=dict(title=None, gridcolor='rgba(62,207,142,0.05)', color='#3d4655', tickformat='$,.0f',
                       zeroline=False, showline=True, linecolor='rgba(62,207,142,0.08)', linewidth=1,
                       tickfont=dict(family='JetBrains Mono', size=10)),
            legend=dict(font=dict(color='#5a6478', size=9, family='Inter'), bgcolor='rgba(0,0,0,0)',
                       orientation='h', yanchor='bottom', y=1.02, xanchor='center', x=0.5),
            margin=dict(l=55, r=15, t=8, b=30), height=350, hovermode='x unified',
            hoverlabel=dict(bgcolor='rgba(14,20,32,0.95)', bordercolor='rgba(62,207,142,0.15)',
                           font=dict(family='JetBrains Mono', size=11, color='#e2e8f0')),
        )
        st.markdown('<div class="section-card" style="padding:16px 16px 8px 16px">', unsafe_allow_html=True)
        st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        st.markdown('</div>', unsafe_allow_html=True)

        # Projection table
        table_rows = []
        for yr in [0, 1, 3, 5, 7, 10]:
            if yr < len(pw_s):
                cagr = (pw_s[yr]/pw_s[0])**(1/max(yr,1))-1 if pw_s[0]>0 and yr>0 else 0
                yrlabel = "Now" if yr == 0 else f"Y{yr}"
                fv_str = f'<span class="num">${pw_s[yr]:,.2f}</span>'
                cagr_str = color_val(f"{cagr*100:+.1f}%") if yr > 0 else '<span class="dim">—</span>'
                table_rows.append([yrlabel, fv_str, cagr_str])
        st.markdown(f'<div class="section-card"><h4>10-Year Projection</h4>{html_table(["Year", "Projected FV", "CAGR"], table_rows)}</div>', unsafe_allow_html=True)

    # Monte Carlo
    if mc:
        p_vals = [('P10', mc.get('p10',0)), ('P25', mc.get('p25',0)), ('Median', mc.get('median',0)), ('P75', mc.get('p75',0)), ('P90', mc.get('p90',0))]
        mc_html = '<div style="display:grid;grid-template-columns:repeat(5,1fr);gap:8px">'
        for label, val in p_vals:
            col = "#f85149" if val < price else "#3ecf8e"
            mc_html += f'<div style="text-align:center"><div style="color:#64748b;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.08em;font-family:Inter,sans-serif">{label}</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.3rem;color:{col}">${val:,.0f}</div></div>'
        mc_html += '</div>'
        prob = mc.get('prob_above_price')
        prob_bar_pct = prob if prob is not None else 50
        bar_color = "#3ecf8e" if prob_bar_pct >= 60 else "#d29922" if prob_bar_pct >= 40 else "#f85149"
        prob_html = f"""
        <div style="margin-top:16px">
            <div style="display:flex;justify-content:space-between;margin-bottom:6px">
                <span style="color:#64748b;font-size:0.7rem;font-family:Inter,sans-serif">PROBABILITY ABOVE MARKET PRICE</span>
                <span style="color:{bar_color};font-family:JetBrains Mono,monospace;font-weight:600;font-size:0.85rem">{prob_bar_pct:.0f}%</span>
            </div>
            <div style="background:#141825;border-radius:4px;height:8px;overflow:hidden">
                <div style="background:{bar_color};width:{prob_bar_pct}%;height:100%;border-radius:4px;transition:width 0.5s"></div>
            </div>
            <div style="color:#475569;font-size:0.7rem;margin-top:4px;font-family:Inter,sans-serif">{mc.get('iterations',5000):,} iterations</div>
        </div>"""
        st.markdown(f'<div class="section-card"><h4>Monte Carlo Distribution</h4>{mc_html}{prob_html}</div>', unsafe_allow_html=True)


# ─────────────────────────────────
#  TAB: Models
# ─────────────────────────────────
with tab_models:
    if mm and mm.get('blended_fv'):
        ind = mm.get('individual', {}); wts = mm.get('weights', {})
        MN = {'dcf':'DCF (Scenario-Wtd)','residual_income':'Residual Income','comps':'Comparable Cos','ev_revenue':'EV / Revenue','roic_fade':'ROIC Fade','ddm':'Dividend Discount'}
        MC = {'dcf':'#3ecf8e','residual_income':'#3b82f6','comps':'#a78bfa','ev_revenue':'#f59e0b','roic_fade':'#ec4899','ddm':'#06b6d4'}

        # Build table
        table_rows = []; active = []
        for k in ['dcf','residual_income','comps','ev_revenue','roic_fade','ddm']:
            v = ind.get(k); w = wts.get(k, 0)
            if v and v > 0:
                vp = (v - price) / price * 100 if price > 0 else 0
                dot = f'<span style="color:{MC[k]}">●</span>'
                fv_str = f'<span class="num">${v:,.2f}</span>'
                w_str = f'<span class="num">{w*100:.1f}%</span>'
                vp_str = color_val(f"{vp:+.1f}%")
                table_rows.append([f'{dot} {MN.get(k,k)}', fv_str, w_str, vp_str])
                active.append(k)

        st.markdown(f'<div class="section-card"><h4>Multi-Model Triangulation</h4>{html_table(["Model", "Fair Value", "Weight", "vs Price"], table_rows)}</div>', unsafe_allow_html=True)

        # Visual weight breakdown - horizontal stacked bar
        if active:
            fig_w = go.Figure()
            for k in active:
                w = wts.get(k, 0); v = ind[k]
                fig_w.add_trace(go.Bar(
                    y=['Blend'], x=[w*100], orientation='h', name=f"{MN.get(k,k)[:12]}",
                    marker_color=MC[k], hovertemplate=f"{MN.get(k,k)}: {w*100:.1f}% × ${v:,.0f}<extra></extra>",
                    text=f"{w*100:.0f}%", textposition='inside', textfont=dict(color='white', size=11, family='JetBrains Mono'),
                ))
            fig_w.update_layout(
                barmode='stack', height=60, showlegend=False,
                plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                xaxis=dict(visible=False), yaxis=dict(visible=False),
                margin=dict(l=0, r=0, t=0, b=0),
            )
            st.plotly_chart(fig_w, use_container_width=True)

        # Agreement & EPV
        agree = mm.get('agreement','N/A'); spread = mm.get('spread',0)
        agree_color = "#3ecf8e" if agree == "HIGH" else "#d29922" if agree in ("MODERATE","LOW") else "#f85149"
        epv = mm.get('epv_floor')
        meta = f'<div style="display:flex;gap:24px;align-items:center;margin-top:4px">'
        meta += f'<div><span style="color:#64748b;font-size:0.7rem;font-family:Inter,sans-serif">AGREEMENT</span> <span style="color:{agree_color};font-family:JetBrains Mono,monospace;font-weight:600">{agree}</span> <span style="color:#475569;font-size:0.75rem">({spread:.0f}% spread)</span></div>'
        if epv and epv > 0:
            meta += f'<div><span style="color:#64748b;font-size:0.7rem;font-family:Inter,sans-serif">EPV FLOOR</span> <span style="color:#94a3b8;font-family:JetBrains Mono,monospace;font-weight:500">${epv:,.2f}</span></div>'
        meta += '</div>'
        st.markdown(meta, unsafe_allow_html=True)
    else:
        st.info("Multi-model triangulation not available.")


# ─────────────────────────────────
#  TAB: Scenarios
# ─────────────────────────────────
with tab_scenarios:
    scens = r.get('scenarios', [])
    if scens:
        # Table
        table_rows = []
        for s in scens:
            if isinstance(s, dict):
                up_str = color_val(f"{s['upside']:+.1f}%")
                table_rows.append([s['name'], f'<span class="num">{s["prob"]*100:.0f}%</span>',
                                   f'<span class="num">${s["fv"]:,.2f}</span>', up_str])
        st.markdown(f'<div class="section-card"><h4>DCF Scenario Analysis</h4>{html_table(["Scenario", "Probability", "Fair Value", "Upside"], table_rows)}</div>', unsafe_allow_html=True)

        # Horizon-style horizontal scenario bars
        names = [s['name'] for s in scens if isinstance(s, dict)]
        fvs = [s['fv'] for s in scens if isinstance(s, dict)]
        probs = [s['prob'] for s in scens if isinstance(s, dict)]
        max_fv = max(fvs) if fvs else 1

        bars_html = ''
        for name, fv_val, prob in zip(names, fvs, probs):
            pct = (fv_val / max_fv) * 100
            above = fv_val >= price
            bar_col = 'linear-gradient(90deg, rgba(62,207,142,0.3), rgba(62,207,142,0.6))' if above else 'linear-gradient(90deg, rgba(248,81,73,0.3), rgba(248,81,73,0.6))'
            txt_col = '#3ecf8e' if above else '#f85149'
            up_pct = (fv_val - price) / price * 100 if price > 0 else 0
            bars_html += f'''<div style="display:grid;grid-template-columns:140px 1fr 90px 70px;align-items:center;gap:12px;margin-bottom:6px">
                <div style="font-family:Inter,sans-serif;font-size:0.82rem;color:#8b95a8">{name}</div>
                <div style="position:relative;height:28px;background:rgba(62,207,142,0.04);border-radius:6px;overflow:hidden">
                    <div style="position:absolute;left:0;top:0;bottom:0;width:{pct:.1f}%;background:{bar_col};border-radius:6px;transition:width 0.6s ease"></div>
                    <div style="position:absolute;left:{min(price/max_fv*100, 98):.1f}%;top:0;bottom:0;width:1px;background:rgba(248,81,73,0.4)"></div>
                </div>
                <div style="font-family:JetBrains Mono,monospace;font-size:0.85rem;font-weight:600;color:#e2e8f0;text-align:right">${fv_val:,.0f}</div>
                <div style="font-family:JetBrains Mono,monospace;font-size:0.75rem;color:{txt_col};text-align:right">{up_pct:+.0f}%</div>
            </div>'''
        # Probability row
        bars_html += '<div style="display:grid;grid-template-columns:140px 1fr 90px 70px;align-items:center;gap:12px;margin-top:8px;padding-top:8px;border-top:1px solid rgba(62,207,142,0.06)">'
        bars_html += '<div style="color:#3d4655;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.08em;font-family:Inter,sans-serif">Probability</div>'
        prob_bar = '<div style="display:flex;height:6px;border-radius:3px;overflow:hidden;gap:1px">'
        p_colors = ['#22c55e','#3b82f6','#94a3b8','#f59e0b','#ef4444']
        for i, (p, c) in enumerate(zip(probs, p_colors)):
            prob_bar += f'<div style="width:{p*100:.1f}%;background:{c};border-radius:3px" title="{names[i]}: {p*100:.0f}%"></div>'
        prob_bar += '</div>'
        bars_html += f'<div>{prob_bar}</div>'
        bars_html += f'<div style="color:#3d4655;font-size:0.7rem;font-family:JetBrains Mono,monospace;text-align:right">Mkt ${price:,.0f}</div><div></div></div>'

        st.markdown(f'<div class="section-card" style="padding:20px 24px"><h4 style="margin-bottom:14px">Scenario Spectrum</h4>{bars_html}</div>', unsafe_allow_html=True)

    # Growth metrics
    tg = r.get('trailing_growth', r.get('trailing'))
    ig = r.get('implied_fcf_growth', r.get('implied_fcf'))
    ir = r.get('implied_rev_growth', r.get('implied_rev'))
    growth_html = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:8px">'
    for label, val in [("Trailing Revenue", tg), ("Implied FCF Growth", ig), ("Implied Rev Growth", ir)]:
        if val:
            v_str = f"{val*100:.1f}%"; col = "#3ecf8e" if val > 0 else "#f85149"
        else:
            v_str = ">80%" if label != "Trailing Revenue" else "—"; col = "#64748b"
        growth_html += f'<div style="background:#111520;border:1px solid #1a2030;border-radius:8px;padding:14px;text-align:center"><div style="color:#64748b;font-size:0.65rem;text-transform:uppercase;letter-spacing:0.06em;font-family:Inter,sans-serif">{label}</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.3rem;color:{col};margin-top:4px">{v_str}</div></div>'
    growth_html += '</div>'
    st.markdown(growth_html, unsafe_allow_html=True)

    for flag in r.get('sanity_flags', []):
        st.markdown(f'<div style="background:rgba(248,81,73,0.06);border:1px solid rgba(248,81,73,0.15);border-radius:8px;padding:10px 14px;margin-top:8px;color:#fca5a5;font-size:0.85rem;font-family:Inter,sans-serif">⚠ {flag}</div>', unsafe_allow_html=True)


# ─────────────────────────────────
#  TAB: Financials
# ─────────────────────────────────
with tab_financials:
    if fins:
        inp = r.get('inputs', {})

        if sector in ('bank', 'specialty_lender'):
            items = [("Revenue (NII)", fins.get('revenue')), ("Net Income", fins.get('net_income')),
                     ("Total Assets", fins.get('total_assets')), ("Equity", fins.get('stockholders_equity')),
                     ("Cash", fins.get('cash')), ("LT Debt", fins.get('long_term_debt')),
                     ("Op CF", fins.get('operating_cf')), ("EPS", fins.get('eps_diluted'))]
        else:
            items = [("Revenue", fins.get('revenue')), ("Net Income", fins.get('net_income')),
                     ("Op Income", fins.get('operating_income')), ("Op CF", fins.get('operating_cf')),
                     ("CapEx", fins.get('capex')), ("D&A", fins.get('depreciation')),
                     ("FCF (used)", inp.get('fcf', r.get('fcf'))),
                     ("Cash", fins.get('cash')), ("LT Debt", fins.get('long_term_debt')),
                     ("Equity", fins.get('stockholders_equity')), ("EPS", fins.get('eps_diluted'))]

        # Split into two columns for a cleaner look
        valid = [(l, v) for l, v in items if v is not None]
        mid = (len(valid) + 1) // 2
        left_items = valid[:mid]
        right_items = valid[mid:]

        left_rows = [[l, f'<span class="num">{fmt(v) if l != "EPS" else f"${v:.2f}"}</span>'] for l, v in left_items]
        right_rows = [[l, f'<span class="num">{fmt(v) if l != "EPS" else f"${v:.2f}"}</span>'] for l, v in right_items]

        c1, c2 = st.columns(2)
        with c1:
            is_quarterly = fins.get('_form', '') in ('10-Q', '10-Q/A')
            ann_note = ' <span style="color:#64748b;font-size:0.7rem;font-weight:400">(annualized)</span>' if is_quarterly else ''
            st.markdown(f'<div class="section-card"><h4>Income & Cash Flow{ann_note}</h4>{html_table(["Metric", "Value"], left_rows)}</div>', unsafe_allow_html=True)
        with c2:
            st.markdown(f'<div class="section-card"><h4>Balance Sheet</h4>{html_table(["Metric", "Value"], right_rows)}</div>', unsafe_allow_html=True)

        # Metadata
        capex_method = inp.get('capex_method', r.get('capex_method', '—'))
        if isinstance(r.get('capex'), dict): capex_method = r['capex'].get('method', capex_method)
        lc = fins.get('_live_comps')
        comps_info = f"Live ({lc['peer_count']} peers)" if lc else "Static"
        form_type_str = fins.get('_form', '?')
        meta_html = f'<div style="display:flex;gap:24px;flex-wrap:wrap;margin-top:4px">'
        meta_html += f'<span style="color:#475569;font-size:0.75rem;font-family:Inter,sans-serif">FCF: {capex_method}</span>'
        meta_html += f'<span style="color:#475569;font-size:0.75rem;font-family:Inter,sans-serif">Sector: {SECTOR_NAMES.get(sector, sector)}</span>'
        meta_html += f'<span style="color:#475569;font-size:0.75rem;font-family:Inter,sans-serif">Filing: {form_type_str}{"  ·  All figures annualized" if is_quarterly else ""}</span>'
        meta_html += f'<span style="color:#475569;font-size:0.75rem;font-family:Inter,sans-serif">Comps: {comps_info}</span>'
        meta_html += '</div>'
        st.markdown(meta_html, unsafe_allow_html=True)

        # Trailing data
        dq = mm.get('data_quality', {}) if mm else st.session_state.data_quality
        q_avail = dq.get('quarters_available', 1)
        if q_avail > 1:
            st.markdown('<div style="height:16px"></div>', unsafe_allow_html=True)
            trail_metrics = '<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px">'
            if dq.get('rev_cagr') is not None:
                trail_metrics += f'<div style="background:#111520;border:1px solid #1a2030;border-radius:8px;padding:14px;text-align:center"><div style="color:#64748b;font-size:0.65rem;text-transform:uppercase;font-family:Inter,sans-serif">Rev CAGR</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:#3ecf8e;margin-top:4px">{dq["rev_cagr"]*100:.1f}%</div></div>'
            if dq.get('fcf_cv') is not None:
                stab = 'Stable' if dq['fcf_cv'] < 0.25 else 'Moderate' if dq['fcf_cv'] < 0.50 else 'Volatile'
                sc = "#3ecf8e" if dq['fcf_cv'] < 0.25 else "#d29922" if dq['fcf_cv'] < 0.50 else "#f85149"
                trail_metrics += f'<div style="background:#111520;border:1px solid #1a2030;border-radius:8px;padding:14px;text-align:center"><div style="color:#64748b;font-size:0.65rem;text-transform:uppercase;font-family:Inter,sans-serif">FCF Stability</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:{sc};margin-top:4px">{stab}</div></div>'
            if dq.get('margin_trend') is not None:
                mt = dq['margin_trend']; d = 'Expanding' if mt > 0.01 else 'Compressing' if mt < -0.01 else 'Stable'
                mc_color = "#3ecf8e" if mt > 0.01 else "#f85149" if mt < -0.01 else "#d29922"
                trail_metrics += f'<div style="background:#111520;border:1px solid #1a2030;border-radius:8px;padding:14px;text-align:center"><div style="color:#64748b;font-size:0.65rem;text-transform:uppercase;font-family:Inter,sans-serif">Margins</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:{mc_color};margin-top:4px">{d}</div></div>'
            trail_metrics += '</div>'
            st.markdown(f'<div class="section-card"><h4>Trailing Data ({q_avail} filings)</h4>{trail_metrics}</div>', unsafe_allow_html=True)


# ─────────────────────────────────
#  TAB: Market Context
# ─────────────────────────────────
with tab_context:
    mi = r.get('market_implies'); af = r.get('asset_floor'); evr = r.get('ev_rev_multiple'); cc = r.get('comps_check')
    has_data = mi or af or evr or cc

    if not has_data:
        st.info("No market context data available.")
    else:
        # Row 1: Market-Implied Growth + Comps side-by-side
        ctx_c1, ctx_c2 = st.columns(2)
        with ctx_c1:
            if mi:
                cagr = mi.get('implied_rev_cagr')
                if isinstance(cagr, str):
                    content = f'<div style="color:#f85149;font-family:JetBrains Mono,monospace;font-size:0.9rem">Implied CAGR: {cagr}</div>'
                else:
                    ps = mi.get('implied_ps_now')
                    content = '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">'
                    content += f'<div style="text-align:center"><div style="color:#5a6478;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;font-family:Inter,sans-serif">Rev CAGR</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:#3ecf8e;margin-top:4px">{cagr:.1f}%</div></div>'
                    content += f'<div style="text-align:center"><div style="color:#5a6478;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;font-family:Inter,sans-serif">At Margin</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:#c0c8d8;margin-top:4px">{mi.get("assumed_margin","")}%</div></div>'
                    if ps: content += f'<div style="text-align:center"><div style="color:#5a6478;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;font-family:Inter,sans-serif">P/S</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:#c0c8d8;margin-top:4px">{ps:.1f}x</div></div>'
                    content += '</div>'
                st.markdown(f'<div class="section-card"><h4>Market-Implied Growth</h4>{content}</div>', unsafe_allow_html=True)
        with ctx_c2:
            if cc:
                cpe = cc.get('current_pe')
                if cpe:
                    src = cc.get('comps_source', 'static')
                    rows = [[f'<span class="num">{cpe:.1f}x</span>', f'<span class="num">{cc["sector_pe"]}x</span>', f'<span class="num">${cc["pe_fv"]:,.2f}</span>']]
                    eveb = cc.get('evebitda_fv')
                    if eveb:
                        rows.append([f'<span class="dim">EV/EBITDA</span>', f'<span class="num">{cc["sector_evebitda"]}x</span>', f'<span class="num">${eveb:,.0f}</span>'])
                    content = html_table(["P/E", f"Sector ({src})", "Fair Value"], rows)
                    st.markdown(f'<div class="section-card"><h4>Comparable Companies</h4>{content}</div>', unsafe_allow_html=True)

        # Row 2: EV/Revenue + Asset Floor side-by-side
        ctx_c3, ctx_c4 = st.columns(2)
        with ctx_c3:
            if evr:
                prem = evr['premium_pct']
                prem_col = "#f85149" if prem > 50 else "#d29922" if prem > 0 else "#3ecf8e"
                content = '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">'
                content += f'<div style="text-align:center"><div style="color:#5a6478;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;font-family:Inter,sans-serif">Current</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:#c0c8d8;margin-top:4px">{evr["current"]:.1f}x</div></div>'
                content += f'<div style="text-align:center"><div style="color:#5a6478;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;font-family:Inter,sans-serif">Sector</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:#c0c8d8;margin-top:4px">{evr["sector_median"]:.1f}x</div></div>'
                content += f'<div style="text-align:center"><div style="color:#5a6478;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;font-family:Inter,sans-serif">Premium</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:{prem_col};margin-top:4px">{prem:+.0f}%</div></div>'
                content += '</div>'
                if evr.get('at_median_price'):
                    content += f'<div style="color:#3d4655;font-size:0.7rem;margin-top:6px;font-family:Inter,sans-serif">At sector median: ${evr["at_median_price"]:,.2f}/share</div>'
                st.markdown(f'<div class="section-card"><h4>EV / Revenue</h4>{content}</div>', unsafe_allow_html=True)
        with ctx_c4:
            if af:
                btp = af.get('book_to_price')
                content = '<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px">'
                content += f'<div style="text-align:center"><div style="color:#5a6478;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;font-family:Inter,sans-serif">Book/Share</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:#c0c8d8;margin-top:4px">${af["book_per_share"]:,.2f}</div></div>'
                content += f'<div style="text-align:center"><div style="color:#5a6478;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;font-family:Inter,sans-serif">Tangible</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:#c0c8d8;margin-top:4px">${af["tangible_floor"]:,.2f}</div></div>'
                if btp:
                    content += f'<div style="text-align:center"><div style="color:#5a6478;font-size:0.6rem;text-transform:uppercase;letter-spacing:0.1em;font-family:Inter,sans-serif">Book %</div><div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:1.2rem;color:#c0c8d8;margin-top:4px">{btp:.0f}%</div></div>'
                content += '</div>'
                st.markdown(f'<div class="section-card"><h4>Asset Floor</h4>{content}</div>', unsafe_allow_html=True)

    # Footer notes
    if r.get('is_buyback_machine'):
        st.markdown('<div style="background:rgba(210,153,34,0.06);border:1px solid rgba(210,153,34,0.15);border-radius:8px;padding:10px 14px;margin-top:8px;color:#fcd34d;font-size:0.85rem;font-family:Inter,sans-serif">Buyback Machine: negative equity with strong cash gen — debt penalty capped</div>', unsafe_allow_html=True)
    if r.get('sbc_haircut', 0) > 0:
        st.markdown(f'<div style="color:#475569;font-size:0.75rem;margin-top:8px;font-family:Inter,sans-serif">SBC Haircut: {fmt(r["sbc_haircut"])}</div>', unsafe_allow_html=True)

# ════════════════════════════════════════
#  FOOTER
# ════════════════════════════════════════

st.markdown('<div class="shimmer-line" style="margin-top:32px"></div>', unsafe_allow_html=True)

# Share + New Analysis row
ft1, ft2, ft3 = st.columns([1, 2, 1])
with ft1:
    share_url = f"https://clarity-web.streamlit.app/?ticker={ticker}"
    import streamlit.components.v1 as components
    components.html(f'''
    <button id="share-btn" style="cursor:pointer;padding:8px 20px;background:rgba(62,207,142,0.08);border:1px solid rgba(62,207,142,0.15);border-radius:8px;color:#3ecf8e;font-family:Inter,sans-serif;font-size:0.8rem;font-weight:600;transition:all 0.3s;outline:none">🔗 Share {ticker}</button>
    <script>
    document.getElementById('share-btn').addEventListener('click', function() {{
        var btn = this;
        navigator.clipboard.writeText('{share_url}').then(function() {{
            btn.innerText = '✓ Copied!';
            btn.style.borderColor = 'rgba(62,207,142,0.4)';
            setTimeout(function() {{ btn.innerText = '🔗 Share {ticker}'; btn.style.borderColor = 'rgba(62,207,142,0.15)'; }}, 2000);
        }});
    }});
    </script>
    ''', height=45)
with ft3:
    if st.button("← New Analysis", key="new_analysis"):
        for k in ['fins', 'dcf_result', 'ticker', 'price', 'shares_mil', 'sector', 'beta',
                   'company_name', 'filing_loaded', 'auto_loaded', 'filing_info', 'price_ts']:
            if k in st.session_state: del st.session_state[k]
        st.query_params.clear()
        st.rerun()

st.markdown("""
<div style="text-align:center;margin-top:24px;padding-bottom:32px;color:#2d3548;font-size:0.6rem;font-family:Inter,sans-serif;line-height:1.6">
    Not financial advice. For educational and research purposes only.<br>
    Valuations are model-driven estimates from SEC filings, not recommendations to buy or sell securities.<br>
    <span style="color:#1e2536">Clarity Valuation Engine</span>
</div>
""", unsafe_allow_html=True)
