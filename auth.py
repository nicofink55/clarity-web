"""
Clarity Auth v5 - Firebase via img onerror injection
Same st.markdown + img onerror trick as neural network canvas.
Injects directly into Streamlit page (no iframe) so popups work.
"""

import streamlit as st
import json
import urllib.parse
from datetime import datetime, timezone

FIREBASE_CONFIG = {
    "apiKey": "AIzaSyCMexVW_EEeywHxs37gifWk-pvlSoaYDIA",
    "authDomain": "clarity-d792e.firebaseapp.com",
    "projectId": "clarity-d792e",
    "storageBucket": "clarity-d792e.firebasestorage.app",
    "messagingSenderId": "914425965590",
    "appId": "1:914425965590:web:003e7a99679a402f7e9937",
    "measurementId": "G-HC274HEBXP",
}

TIER_LIMITS = {"visitor": 0, "free": 5, "pro": 999999}
POPULAR_TICKERS = ["AAPL", "NVDA", "MSFT", "GOOG", "AMZN", "META", "TSLA", "PLTR"]
ADMIN_EMAILS = ["nicofink55@gmail.com"]

_CFG = json.dumps(FIREBASE_CONFIG)


# ================================================================
#  SESSION STATE
# ================================================================

def _init():
    for k, v in {"auth_user": None, "auth_initialized": False}.items():
        if k not in st.session_state:
            st.session_state[k] = v

def get_user():
    _init()
    return st.session_state.auth_user

def get_tier():
    u = get_user()
    if not u: return "visitor"
    if u.get("email","").lower() in [e.lower() for e in ADMIN_EMAILS]: return "pro"
    return u.get("tier", "free")

def is_signed_in():
    return get_user() is not None

def get_monthly_usage():
    u = get_user()
    return u.get("monthly_runs", 0) if u else 0

def can_run_analysis(ticker=None):
    t = get_tier()
    if t == "pro": return True, ""
    if t == "visitor":
        if ticker and ticker.upper() in POPULAR_TICKERS: return True, ""
        return False, "sign_in"
    if get_monthly_usage() >= TIER_LIMITS["free"]: return False, "limit_reached"
    return True, ""

def increment_usage():
    u = get_user()
    if u:
        u["monthly_runs"] = u.get("monthly_runs", 0) + 1
        st.session_state.auth_user = u


# ================================================================
#  JS INJECTION via img onerror
# ================================================================

def _inject(js):
    encoded = urllib.parse.quote(js.strip(), safe='')
    st.markdown(
        '<img src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7" '
        'style="display:none" '
        'onerror="var s=document.createElement(\'script\');'
        "s.textContent=decodeURIComponent('" + encoded + "');"
        'document.head.appendChild(s);">',
        unsafe_allow_html=True)


# Firebase loader + auth listener + sign-in/out functions + button binder
_LOADER = (
    'if(!window._cfDone){window._cfDone=true;'
    'function _ld(u,cb){var s=document.createElement("script");s.src=u;s.onload=cb;document.head.appendChild(s);}'
    '_ld("https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js",function(){'
    '_ld("https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js",function(){'
    '_ld("https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js",function(){'
    'var app;try{app=firebase.app();}catch(e){app=firebase.initializeApp(' + _CFG + ');}'
    'window._cfA=firebase.auth();window._cfD=firebase.firestore();'
    # Auth state listener
    'window._cfA.onAuthStateChanged(async function(user){'
    'if(user){'
    'var doc=await window._cfD.collection("users").doc(user.uid).get();'
    'var ud=doc.exists?doc.data():{};'
    'if(!doc.exists){'
    'ud={email:user.email,displayName:user.displayName||"",'
    'photoURL:user.photoURL||"",tier:"free",'
    'created:new Date().toISOString(),monthly_runs:0,'
    'monthly_reset:new Date().toISOString().slice(0,7),'
    'watchlist:[],saved_analyses:[]};'
    'await window._cfD.collection("users").doc(user.uid).set(ud);}'
    'var cm=new Date().toISOString().slice(0,7);'
    'if(ud.monthly_reset!==cm){'
    'ud.monthly_runs=0;ud.monthly_reset=cm;'
    'await window._cfD.collection("users").doc(user.uid).update({monthly_runs:0,monthly_reset:cm});}'
    'var ad=JSON.stringify({uid:user.uid,email:user.email,'
    'displayName:user.displayName||(user.email?user.email.split("@")[0]:"User"),'
    'photoURL:user.photoURL||"",tier:ud.tier||"free",'
    'monthly_runs:ud.monthly_runs||0,'
    'watchlist:ud.watchlist||[],'
    'saved_count:(ud.saved_analyses||[]).length});'
    'var enc=encodeURIComponent(ad);'
    'var u=new URL(window.location.href);'
    'if(u.searchParams.get("auth_data")!==enc){'
    'u.searchParams.set("auth_data",enc);'
    'window.history.replaceState({},"",u);'
    'window.location.reload();}}'
    'else{'
    'var u=new URL(window.location.href);'
    'if(u.searchParams.has("auth_data")){'
    'u.searchParams.delete("auth_data");'
    'window.history.replaceState({},"",u);'
    'window.location.reload();}}'
    '});'
    # Sign-in function
    'window._cfSignIn=function(){'
    'if(!window._cfA){setTimeout(window._cfSignIn,300);return;}'
    'var p=new firebase.auth.GoogleAuthProvider();'
    'window._cfA.signInWithPopup(p).catch(function(e){console.error("Auth:",e);});};'
    # Sign-out function
    'window._cfSignOut=function(){'
    'if(!window._cfA)return;'
    'window._cfA.signOut().then(function(){'
    'var u=new URL(window.location.href);'
    'u.searchParams.delete("auth_data");'
    'window.history.replaceState({},"",u);'
    'window.location.reload();});};'
    # Button binder - finds all [data-auth-action] buttons and adds click listeners
    'window._cfBind=function(){'
    'document.querySelectorAll("[data-auth-action]").forEach(function(b){'
    'if(b._cf)return;b._cf=true;'
    'b.addEventListener("click",function(){'
    'var a=b.getAttribute("data-auth-action");'
    'if(a==="signin")window._cfSignIn();'
    'if(a==="signout")window._cfSignOut();'
    '});});};'
    'setTimeout(window._cfBind,300);'
    'setTimeout(window._cfBind,1000);'
    'setTimeout(window._cfBind,2500);'
    'setTimeout(window._cfBind,5000);'
    '});});});}'
)


def init_auth():
    _init()
    params = st.query_params
    ad = params.get("auth_data", None)
    if ad:
        try: st.session_state.auth_user = json.loads(ad)
        except: st.session_state.auth_user = None
    else:
        st.session_state.auth_user = None
    st.session_state.auth_initialized = True

    # Handle sign-out triggered by Streamlit button
    extra = ""
    if st.session_state.get("_auth_do_signout"):
        del st.session_state["_auth_do_signout"]
        extra = "setTimeout(function(){if(window._cfSignOut)window._cfSignOut();},2000);"

    _inject(_LOADER + extra)


# ================================================================
#  GOOGLE SIGN-IN BUTTON (HTML with data-auth-action attribute)
# ================================================================

def _render_google_button(btn_id="gsi"):
    st.markdown(
        '<div style="display:flex;justify-content:center;padding:6px 0">'
        '<button data-auth-action="signin" id="' + btn_id + '" style="'
        'display:flex;align-items:center;justify-content:center;gap:10px;'
        'width:100%;max-width:340px;padding:11px 20px;'
        'background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.10);'
        'border-radius:10px;cursor:pointer;font-family:Inter,sans-serif;'
        'font-size:0.85rem;font-weight:500;color:#e2e8f0;transition:all 0.2s;outline:none'
        '">'
        '<img src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg" '
        'style="width:18px;height:18px">'
        'Continue with Google</button></div>',
        unsafe_allow_html=True)


# ================================================================
#  UI COMPONENTS
# ================================================================

def show_sign_in_prompt():
    st.markdown("""
    <div style="background:linear-gradient(165deg,rgba(15,22,36,0.95),rgba(8,12,24,0.98));
    border:1px solid rgba(62,207,142,0.12);border-radius:16px;padding:28px 32px 16px;
    max-width:440px;margin:24px auto;text-align:center">
        <div style="width:44px;height:44px;border-radius:12px;
        background:linear-gradient(135deg,rgba(62,207,142,0.15),rgba(30,120,90,0.3));
        border:1px solid rgba(62,207,142,0.2);display:flex;align-items:center;
        justify-content:center;font-family:'Playfair Display',serif;font-weight:700;
        font-style:italic;font-size:1.3rem;color:#3ecf8e;margin:0 auto 14px">C</div>
        <div style="font-family:Inter,sans-serif;font-weight:700;font-size:1.15rem;
        color:#e2e8f0;margin-bottom:6px">Sign in to analyze any ticker</div>
        <div style="font-family:Inter,sans-serif;font-size:0.78rem;color:#64748b;
        line-height:1.5;margin-bottom:12px">
        Free account &mdash; 5 analyses/month, saved valuations, watchlists</div>
    </div>
    """, unsafe_allow_html=True)
    _render_google_button("gsi_prompt")
    st.markdown(
        '<div style="text-align:center;font-size:0.68rem;color:#475569;'
        'font-family:Inter,sans-serif;margin-top:6px">'
        'Popular tickers (AAPL, NVDA, MSFT...) work without an account'
        '</div>', unsafe_allow_html=True)


def show_limit_reached():
    st.markdown("""
    <div style="background:linear-gradient(165deg,rgba(15,22,36,0.95),rgba(8,12,24,0.98));
    border:1px solid rgba(210,153,34,0.15);border-radius:16px;padding:32px;
    max-width:440px;margin:24px auto;text-align:center">
        <div style="font-family:Inter,sans-serif;font-weight:700;font-size:1.2rem;
        color:#e2e8f0;margin-bottom:8px">Monthly Limit Reached</div>
        <div style="font-family:Inter,sans-serif;font-size:0.8rem;color:#64748b;
        line-height:1.5;margin-bottom:16px">
        You've used all 5 free analyses this month. Resets on the 1st.</div>
        <div style="padding:12px;background:rgba(62,207,142,0.04);
        border:1px solid rgba(62,207,142,0.08);border-radius:10px">
            <div style="font-family:Inter,sans-serif;font-size:0.85rem;
            color:#3ecf8e;font-weight:600">Pro plan coming soon</div></div>
    </div>
    """, unsafe_allow_html=True)


def render_auth_sidebar():
    user = get_user()
    if user:
        name = user.get("displayName", "User")
        tier = get_tier()
        runs = user.get("monthly_runs", 0)
        limit = TIER_LIMITS.get(tier, 5)
        tl = "PRO" if tier == "pro" else "FREE"
        tc = "#3ecf8e" if tier == "pro" else "#64748b"
        ut = "" if tier == "pro" else (" &middot; " + str(runs) + "/" + str(limit))
        st.markdown(
            '<div style="padding:8px 0 4px">'
            '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
            '<div style="width:28px;height:28px;border-radius:50%;'
            'background:linear-gradient(135deg,rgba(62,207,142,0.2),rgba(30,120,90,0.4));'
            'display:flex;align-items:center;justify-content:center;'
            'font-family:Inter,sans-serif;font-weight:700;font-size:0.65rem;color:#3ecf8e">'
            + name[0].upper() + '</div>'
            '<div><div style="font-family:Inter,sans-serif;font-size:0.78rem;'
            'color:#c0c8d8;font-weight:500">' + name + '</div>'
            '<div style="font-family:JetBrains Mono,monospace;font-size:0.58rem;color:'
            + tc + ';font-weight:600;letter-spacing:0.05em">'
            + tl + ut + '</div>'
            '</div></div></div>', unsafe_allow_html=True)
        if st.button("Sign out", key="signout_btn", use_container_width=True):
            st.session_state["_auth_do_signout"] = True
            st.session_state.auth_user = None
            st.rerun()
    else:
        st.markdown(
            '<div style="font-family:Inter,sans-serif;font-size:0.72rem;color:#64748b;'
            'padding:4px 0 4px">Sign in for full access</div>', unsafe_allow_html=True)
        _render_google_button("gsi_sidebar")


# ================================================================
#  FIRESTORE ACTIONS
# ================================================================

def save_analysis(ticker, result_summary):
    user = get_user()
    if not user: return
    data = json.dumps({
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fair_value": result_summary.get("fair_value"),
        "price": result_summary.get("price"),
        "verdict": result_summary.get("verdict"),
        "sector": result_summary.get("sector"),
        "upside_pct": result_summary.get("upside_pct"),
    })
    js = (
        '(function _s(){if(!window._cfD){setTimeout(_s,500);return;}'
        'var ref=window._cfD.collection("users").doc("' + user["uid"] + '");'
        'ref.get().then(function(doc){'
        'if(doc.exists){'
        'var d=doc.data();var a=d.saved_analyses||[];'
        'a.unshift(' + data + ');'
        'if(a.length>50)a.length=50;'
        'ref.update({saved_analyses:a,monthly_runs:(d.monthly_runs||0)+1});}});})();'
    )
    _inject(js)
    increment_usage()

def toggle_watchlist(ticker):
    user = get_user()
    if not user: return
    wl = user.get("watchlist", [])
    if ticker in wl:
        wl.remove(ticker)
        op = 'firebase.firestore.FieldValue.arrayRemove("' + ticker + '")'
    else:
        wl.append(ticker)
        op = 'firebase.firestore.FieldValue.arrayUnion("' + ticker + '")'
    user["watchlist"] = wl
    st.session_state.auth_user = user
    js = (
        '(function _w(){if(!window._cfD){setTimeout(_w,500);return;}'
        'window._cfD.collection("users").doc("' + user["uid"] + '")'
        '.update({watchlist:' + op + '});})();'
    )
    _inject(js)

def is_in_watchlist(ticker):
    u = get_user()
    return u is not None and ticker in u.get("watchlist", [])
