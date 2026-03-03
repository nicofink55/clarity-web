"""
Clarity Auth Module - Firebase Authentication + Firestore
Tiers: visitor (popular tickers only), free (5 runs/mo), pro (unlimited)
Uses Streamlit native buttons for UI, st.components.v1.html for Firebase JS only.
"""

import streamlit as st
import json
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

# Master accounts - always pro tier, unlimited runs
ADMIN_EMAILS = [
    "nicofink55@gmail.com",
]


# ================================================================
#  SESSION STATE
# ================================================================

def _init_auth_state():
    defaults = {
        "auth_user": None,
        "auth_initialized": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def get_user():
    _init_auth_state()
    return st.session_state.auth_user


def get_tier():
    user = get_user()
    if not user:
        return "visitor"
    if user.get("email", "").lower() in [e.lower() for e in ADMIN_EMAILS]:
        return "pro"
    return user.get("tier", "free")


def is_signed_in():
    return get_user() is not None


def get_monthly_usage():
    user = get_user()
    if user:
        return user.get("monthly_runs", 0)
    return 0


def can_run_analysis(ticker=None):
    tier = get_tier()
    if tier == "pro":
        return True, ""
    if tier == "visitor":
        if ticker and ticker.upper() in POPULAR_TICKERS:
            return True, ""
        return False, "sign_in"
    usage = get_monthly_usage()
    limit = TIER_LIMITS["free"]
    if usage >= limit:
        return False, "limit_reached"
    return True, ""


def increment_usage():
    user = get_user()
    if user:
        user["monthly_runs"] = user.get("monthly_runs", 0) + 1
        st.session_state.auth_user = user


# ================================================================
#  FIREBASE JS (runs in iframe via st.components.v1.html)
# ================================================================

def _firebase_js(action=None):
    """Firebase Auth JS component. Handles:
    - Listening for auth state and syncing to Streamlit via query params
    - Executing sign-in/sign-out when action param is set
    """
    config_json = json.dumps(FIREBASE_CONFIG)
    action_js = ""
    if action == "google_signin":
        action_js = (
            'var p=new firebase.auth.GoogleAuthProvider();'
            'auth.signInWithPopup(p).catch(function(e){'
            'console.error("Sign-in error:",e);});'
        )
    elif action == "signout":
        action_js = (
            'auth.signOut().then(function(){'
            'var u=new URL(window.parent.location.href);'
            'u.searchParams.delete("auth_data");'
            'window.parent.history.replaceState({},"",u);'
            'window.parent.location.reload();'
            '});'
        )

    return (
        '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>'
        '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js"></script>'
        '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js"></script>'
        '<script>'
        '(function(){'
        'if(window._clarityFB)return;'
        'window._clarityFB=true;'
        'var app=firebase.initializeApp(' + config_json + ');'
        'var auth=firebase.auth();'
        'var db=firebase.firestore();'
        + action_js +
        'auth.onAuthStateChanged(async function(user){'
        'if(user){'
        'var doc=await db.collection("users").doc(user.uid).get();'
        'var ud=doc.exists?doc.data():{};'
        'if(!doc.exists){'
        'ud={email:user.email,displayName:user.displayName||"",'
        'photoURL:user.photoURL||"",tier:"free",'
        'created:new Date().toISOString(),monthly_runs:0,'
        'monthly_reset:new Date().toISOString().slice(0,7),'
        'watchlist:[],saved_analyses:[]};'
        'await db.collection("users").doc(user.uid).set(ud);'
        '}'
        'var cm=new Date().toISOString().slice(0,7);'
        'if(ud.monthly_reset!==cm){'
        'ud.monthly_runs=0;ud.monthly_reset=cm;'
        'await db.collection("users").doc(user.uid).update('
        '{monthly_runs:0,monthly_reset:cm});'
        '}'
        'var ad=JSON.stringify({uid:user.uid,email:user.email,'
        'displayName:user.displayName||(user.email?user.email.split("@")[0]:"User"),'
        'photoURL:user.photoURL||"",tier:ud.tier||"free",'
        'monthly_runs:ud.monthly_runs||0,'
        'watchlist:ud.watchlist||[],'
        'saved_count:(ud.saved_analyses||[]).length});'
        'var enc=encodeURIComponent(ad);'
        'var u=new URL(window.parent.location.href);'
        'if(u.searchParams.get("auth_data")!==enc){'
        'u.searchParams.set("auth_data",enc);'
        'window.parent.history.replaceState({},"",u);'
        'window.parent.location.reload();}'
        '}else{'
        'var u=new URL(window.parent.location.href);'
        'if(u.searchParams.has("auth_data")){'
        'u.searchParams.delete("auth_data");'
        'window.parent.history.replaceState({},"",u);'
        'window.parent.location.reload();}'
        '}'
        '});'
        'window._clarityAuth={db:db,auth:auth};'
        '})();'
        '</script>'
    )


def _firestore_save_js(uid, data):
    data_json = json.dumps(data)
    return (
        '<script>'
        '(function(){'
        'if(!window._clarityAuth)return;'
        'var db=window._clarityAuth.db;'
        'var ref=db.collection("users").doc("' + uid + '");'
        'ref.get().then(function(doc){'
        'if(doc.exists){'
        'var d=doc.data();var a=d.saved_analyses||[];'
        'a.unshift(' + data_json + ');'
        'if(a.length>50)a.length=50;'
        'ref.update({saved_analyses:a,monthly_runs:(d.monthly_runs||0)+1});'
        '}});'
        '})();'
        '</script>'
    )


def _firestore_watchlist_js(uid, action, ticker):
    if action == "add":
        op = 'firebase.firestore.FieldValue.arrayUnion("' + ticker + '")'
    else:
        op = 'firebase.firestore.FieldValue.arrayRemove("' + ticker + '")'
    return (
        '<script>'
        '(function(){'
        'if(!window._clarityAuth)return;'
        'var db=window._clarityAuth.db;'
        'db.collection("users").doc("' + uid + '").update({watchlist:' + op + '});'
        '})();'
        '</script>'
    )


# ================================================================
#  CORE: init_auth (call once after set_page_config)
# ================================================================

def init_auth():
    """Initialize auth state from query params and render Firebase JS."""
    _init_auth_state()

    params = st.query_params
    auth_data_str = params.get("auth_data", None)

    if auth_data_str:
        try:
            st.session_state.auth_user = json.loads(auth_data_str)
        except (json.JSONDecodeError, TypeError):
            st.session_state.auth_user = None
    else:
        st.session_state.auth_user = None

    st.session_state.auth_initialized = True

    # Determine if we need to trigger a Firebase action
    action = None
    if st.session_state.get("_auth_do_signin"):
        action = "google_signin"
        del st.session_state["_auth_do_signin"]
    elif st.session_state.get("_auth_do_signout"):
        action = "signout"
        del st.session_state["_auth_do_signout"]

    # Render Firebase JS (hidden iframe, 0 height)
    st.components.v1.html(_firebase_js(action=action), height=0)


# ================================================================
#  UI: Sign-in dialog (uses st.dialog - Streamlit native)
# ================================================================

def show_sign_in_prompt():
    """Show sign-in prompt using Streamlit native components.
    Call this when a visitor tries to use a non-popular ticker."""

    st.markdown("""
    <div style="background:linear-gradient(165deg,rgba(15,22,36,0.95),rgba(8,12,24,0.98));
    border:1px solid rgba(62,207,142,0.12);border-radius:16px;padding:32px;
    max-width:440px;margin:24px auto;text-align:center">
        <div style="width:48px;height:48px;border-radius:12px;
        background:linear-gradient(135deg,rgba(62,207,142,0.15),rgba(30,120,90,0.3));
        border:1px solid rgba(62,207,142,0.2);display:flex;align-items:center;
        justify-content:center;font-family:'Playfair Display',serif;font-weight:700;
        font-style:italic;font-size:1.4rem;color:#3ecf8e;margin:0 auto 16px">C</div>
        <div style="font-family:Inter,sans-serif;font-weight:700;font-size:1.2rem;
        color:#e2e8f0;margin-bottom:6px">Sign in to analyze any ticker</div>
        <div style="font-family:Inter,sans-serif;font-size:0.8rem;color:#64748b;
        margin-bottom:4px;line-height:1.5">
        Free account includes 5 analyses per month, saved valuations, and watchlists.
        </div>
    </div>
    """, unsafe_allow_html=True)

    _, btn_col, _ = st.columns([1, 2, 1])
    with btn_col:
        if st.button("Sign in with Google", key="signin_prompt_btn",
                      use_container_width=True, type="primary"):
            st.session_state["_auth_do_signin"] = True
            st.rerun()

        st.markdown(
            '<div style="text-align:center;font-size:0.7rem;color:#475569;'
            'font-family:Inter,sans-serif;margin-top:8px">'
            'Popular tickers (AAPL, NVDA, MSFT...) are free without an account'
            '</div>',
            unsafe_allow_html=True
        )


def show_limit_reached():
    """Show limit reached message using Streamlit native components."""
    st.markdown("""
    <div style="background:linear-gradient(165deg,rgba(15,22,36,0.95),rgba(8,12,24,0.98));
    border:1px solid rgba(210,153,34,0.15);border-radius:16px;padding:32px;
    max-width:440px;margin:24px auto;text-align:center">
        <div style="font-family:Inter,sans-serif;font-weight:700;font-size:1.2rem;
        color:#e2e8f0;margin-bottom:8px">Monthly Limit Reached</div>
        <div style="font-family:Inter,sans-serif;font-size:0.8rem;color:#64748b;
        line-height:1.5;margin-bottom:16px">
        You've used all 5 free analyses this month. Resets on the 1st.
        </div>
        <div style="padding:12px;background:rgba(62,207,142,0.04);
        border:1px solid rgba(62,207,142,0.08);border-radius:10px">
            <div style="font-family:Inter,sans-serif;font-size:0.85rem;
            color:#3ecf8e;font-weight:600">Pro plan coming soon</div>
        </div>
    </div>
    """, unsafe_allow_html=True)


# ================================================================
#  UI: User status in sidebar or header
# ================================================================

def render_auth_sidebar():
    """Render auth status and sign-in/out button in sidebar."""
    user = get_user()
    if user:
        name = user.get("displayName", "User")
        email = user.get("email", "")
        tier = get_tier()
        runs = user.get("monthly_runs", 0)
        limit = TIER_LIMITS.get(tier, 5)
        tier_label = "PRO" if tier == "pro" else "FREE"
        tier_color = "#3ecf8e" if tier == "pro" else "#64748b"

        st.markdown(
            '<div style="padding:8px 0 4px">'
            '<div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">'
            '<div style="width:28px;height:28px;border-radius:50%;'
            'background:linear-gradient(135deg,rgba(62,207,142,0.2),rgba(30,120,90,0.4));'
            'display:flex;align-items:center;justify-content:center;'
            'font-family:Inter,sans-serif;font-weight:700;font-size:0.65rem;color:#3ecf8e">'
            + name[0].upper() +
            '</div>'
            '<div>'
            '<div style="font-family:Inter,sans-serif;font-size:0.78rem;color:#c0c8d8;font-weight:500">'
            + name + '</div>'
            '<div style="font-family:JetBrains Mono,monospace;font-size:0.58rem;color:'
            + tier_color + ';font-weight:600;letter-spacing:0.05em">'
            + tier_label + (' &middot; ' + str(runs) + '/' + str(limit) if tier != "pro" else '')
            + '</div>'
            '</div></div></div>',
            unsafe_allow_html=True,
        )

        if st.button("Sign out", key="signout_btn", use_container_width=True):
            st.session_state["_auth_do_signout"] = True
            st.session_state.auth_user = None
            st.rerun()
    else:
        st.markdown(
            '<div style="font-family:Inter,sans-serif;font-size:0.75rem;color:#64748b;'
            'padding:4px 0 8px">Sign in for full access</div>',
            unsafe_allow_html=True,
        )
        if st.button("Sign in with Google", key="signin_sidebar_btn",
                      use_container_width=True, type="primary"):
            st.session_state["_auth_do_signin"] = True
            st.rerun()


# ================================================================
#  ACTIONS: Save analysis, watchlist
# ================================================================

def save_analysis(ticker, result_summary):
    user = get_user()
    if not user:
        return
    data = {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fair_value": result_summary.get("fair_value"),
        "price": result_summary.get("price"),
        "verdict": result_summary.get("verdict"),
        "sector": result_summary.get("sector"),
        "upside_pct": result_summary.get("upside_pct"),
    }
    st.components.v1.html(_firestore_save_js(user["uid"], data), height=0)
    increment_usage()


def toggle_watchlist(ticker):
    user = get_user()
    if not user:
        return
    wl = user.get("watchlist", [])
    if ticker in wl:
        wl.remove(ticker)
        action = "remove"
    else:
        wl.append(ticker)
        action = "add"
    user["watchlist"] = wl
    st.session_state.auth_user = user
    st.components.v1.html(_firestore_watchlist_js(user["uid"], action, ticker), height=0)


def is_in_watchlist(ticker):
    user = get_user()
    if not user:
        return False
    return ticker in user.get("watchlist", [])
