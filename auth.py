"""
Clarity Auth Module - Firebase Authentication + Firestore
Tiers: visitor (popular tickers only), free (5 runs/mo), pro (unlimited)
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


# ================================================================
#  SESSION STATE HELPERS
# ================================================================

def _init_auth_state():
    defaults = {
        "auth_user": None,
        "auth_initialized": False,
        "auth_show_modal": False,
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
    return user.get("tier", "free")


def is_signed_in():
    return get_user() is not None


def get_monthly_usage():
    user = get_user()
    if user:
        return user.get("monthly_runs", 0)
    return 0


def can_run_analysis(ticker=None):
    """Returns (allowed: bool, reason: str). reason is 'sign_in' or 'limit_reached'."""
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
#  FIREBASE JS COMPONENT
# ================================================================

def _firebase_auth_js():
    """Firebase Auth JS - loads Firebase, listens for auth state,
    syncs user data back to Streamlit via query params."""
    config_json = json.dumps(FIREBASE_CONFIG)
    return (
        '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"></script>'
        '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js"></script>'
        '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js"></script>'
        '<script>'
        '(function(){'
        'if(window._clarityAuthInit)return;'
        'window._clarityAuthInit=true;'
        'var config=' + config_json + ';'
        'var app=firebase.initializeApp(config);'
        'var auth=firebase.auth();'
        'var db=firebase.firestore();'
        'auth.onAuthStateChanged(async function(user){'
          'if(user){'
            'var userDoc=await db.collection("users").doc(user.uid).get();'
            'var ud=userDoc.exists?userDoc.data():{};'
            'if(!userDoc.exists){'
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
              'await db.collection("users").doc(user.uid).update({monthly_runs:0,monthly_reset:cm});'
            '}'
            'var ad={uid:user.uid,email:user.email,'
            'displayName:user.displayName||(user.email?user.email.split("@")[0]:"User"),'
            'photoURL:user.photoURL||"",'
            'tier:ud.tier||"free",monthly_runs:ud.monthly_runs||0,'
            'watchlist:ud.watchlist||[],saved_count:(ud.saved_analyses||[]).length};'
            'var enc=encodeURIComponent(JSON.stringify(ad));'
            'var url=new URL(window.parent.location.href);'
            'if(url.searchParams.get("auth_data")!==enc){'
              'url.searchParams.set("auth_data",enc);'
              'url.searchParams.delete("auth_action");'
              'window.parent.history.replaceState({},"",url);'
              'window.parent.location.reload();'
            '}'
          '}else{'
            'var url=new URL(window.parent.location.href);'
            'if(url.searchParams.has("auth_data")){'
              'url.searchParams.delete("auth_data");'
              'window.parent.history.replaceState({},"",url);'
              'window.parent.location.reload();'
            '}'
          '}'
        '});'
        'var pu=new URL(window.parent.location.href);'
        'var action=pu.searchParams.get("auth_action");'
        'if(action==="google_signin"){'
          'var p=new firebase.auth.GoogleAuthProvider();'
          'auth.signInWithPopup(p).catch(function(e){console.error("Auth error:",e)});'
        '}else if(action==="signout"){'
          'auth.signOut().then(function(){'
            'var u=new URL(window.parent.location.href);'
            'u.searchParams.delete("auth_data");'
            'u.searchParams.delete("auth_action");'
            'window.parent.history.replaceState({},"",u);'
            'window.parent.location.reload();'
          '});'
        '}'
        'window._clarityAuth={signInWithGoogle:function(){'
          'var p=new firebase.auth.GoogleAuthProvider();auth.signInWithPopup(p);},'
          'signOut:function(){auth.signOut();},'
          'getDb:function(){return db;},getAuth:function(){return auth;}};'
        '})();'
        '</script>'
    ).replace('</script>', '</script>')


def _render_auth_component():
    """Render hidden Firebase component and sync auth state from query params."""
    _init_auth_state()
    params = st.query_params
    auth_data_str = params.get("auth_data", None)
    if auth_data_str:
        try:
            auth_data = json.loads(auth_data_str)
            st.session_state.auth_user = auth_data
            st.session_state.auth_initialized = True
        except (json.JSONDecodeError, TypeError):
            st.session_state.auth_user = None
    else:
        st.session_state.auth_user = None
        st.session_state.auth_initialized = True
    st.components.v1.html(_firebase_auth_js(), height=0)


# ================================================================
#  SIGN-IN MODAL
# ================================================================

_AUTH_CSS = """
<style>
.auth-overlay{position:fixed;top:0;left:0;right:0;bottom:0;
background:rgba(4,6,14,0.85);backdrop-filter:blur(20px);z-index:10000;
display:flex;align-items:center;justify-content:center;animation:authFadeIn .3s ease}
@keyframes authFadeIn{from{opacity:0}to{opacity:1}}
@keyframes authSlideUp{from{transform:translateY(20px);opacity:0}to{transform:translateY(0);opacity:1}}
.auth-modal{background:linear-gradient(165deg,rgba(15,22,36,0.98),rgba(8,12,24,0.99));
border:1px solid rgba(62,207,142,0.12);border-radius:20px;padding:40px 36px;
width:420px;max-width:90vw;box-shadow:0 24px 80px rgba(0,0,0,0.6),0 0 60px rgba(62,207,142,0.05);
animation:authSlideUp .4s ease .1s both;text-align:center;position:relative}
.auth-modal-close{position:absolute;top:16px;right:18px;background:none;border:none;
color:#475569;font-size:1.3rem;cursor:pointer;padding:4px 8px;border-radius:8px;transition:all .2s}
.auth-modal-close:hover{color:#94a3b8;background:rgba(255,255,255,0.04)}
.auth-logo{width:52px;height:52px;border-radius:14px;
background:linear-gradient(135deg,rgba(62,207,142,0.15),rgba(30,120,90,0.3));
border:1px solid rgba(62,207,142,0.2);display:flex;align-items:center;justify-content:center;
font-family:'Playfair Display',serif;font-weight:700;font-style:italic;
font-size:1.6rem;color:#3ecf8e;margin:0 auto 20px}
.auth-title{font-family:Inter,sans-serif;font-weight:700;font-size:1.35rem;color:#e2e8f0;
margin-bottom:6px;letter-spacing:-0.01em}
.auth-subtitle{font-family:Inter,sans-serif;font-size:0.82rem;color:#64748b;
margin-bottom:28px;line-height:1.5}
.auth-google-btn{display:flex;align-items:center;justify-content:center;gap:10px;
width:100%;padding:13px 20px;background:rgba(255,255,255,0.04);
border:1px solid rgba(255,255,255,0.08);border-radius:12px;cursor:pointer;
font-family:Inter,sans-serif;font-size:0.88rem;font-weight:500;color:#e2e8f0;
transition:all .25s;text-decoration:none}
.auth-google-btn:hover{background:rgba(255,255,255,0.08);
border-color:rgba(62,207,142,0.2);box-shadow:0 4px 20px rgba(62,207,142,0.06)}
.auth-google-btn img{width:20px;height:20px}
.auth-features{text-align:left;margin-top:24px;padding-top:20px;
border-top:1px solid rgba(62,207,142,0.06)}
.auth-feature{display:flex;align-items:center;gap:10px;padding:6px 0;
font-family:Inter,sans-serif;font-size:0.78rem;color:#8b95a8}
.auth-feature-icon{color:#3ecf8e;font-size:0.7rem;flex-shrink:0}
.auth-limit-badge{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;
border-radius:20px;background:rgba(62,207,142,0.08);border:1px solid rgba(62,207,142,0.12);
font-family:JetBrains Mono,monospace;font-size:0.65rem;color:#3ecf8e;font-weight:600}
.auth-visitor-note{margin-top:16px;padding:12px 16px;background:rgba(62,207,142,0.03);
border:1px solid rgba(62,207,142,0.06);border-radius:10px;font-family:Inter,sans-serif;
font-size:0.72rem;color:#64748b;line-height:1.5}
.auth-user-pill{display:inline-flex;align-items:center;gap:8px;padding:5px 14px 5px 6px;
background:rgba(62,207,142,0.04);border:1px solid rgba(62,207,142,0.08);border-radius:24px;
cursor:pointer;transition:all .2s;text-decoration:none}
.auth-user-pill:hover{background:rgba(62,207,142,0.08);border-color:rgba(62,207,142,0.15)}
.auth-user-avatar{width:26px;height:26px;border-radius:50%;
background:linear-gradient(135deg,rgba(62,207,142,0.2),rgba(30,120,90,0.4));
display:flex;align-items:center;justify-content:center;font-family:Inter,sans-serif;
font-weight:700;font-size:0.65rem;color:#3ecf8e;overflow:hidden}
.auth-user-avatar img{width:100%;height:100%;object-fit:cover;border-radius:50%}
.auth-user-name{font-family:Inter,sans-serif;font-weight:500;font-size:0.75rem;color:#c0c8d8}
.auth-usage-bar{height:3px;border-radius:2px;background:rgba(62,207,142,0.1);
margin-top:4px;overflow:hidden}
.auth-usage-fill{height:100%;border-radius:2px;
background:linear-gradient(90deg,#3ecf8e,#2ea97a);transition:width .3s ease}
</style>
"""


def render_sign_in_modal():
    """Render the glassmorphism sign-in modal overlay."""
    html = _AUTH_CSS + (
        '<div class="auth-overlay" id="authOverlay">'
        '<div class="auth-modal">'
        '<button class="auth-modal-close" onclick="'
        "var u=new URL(window.location.href);"
        "u.searchParams.delete('show_auth');"
        "window.location.href=u.toString();"
        '">&times;</button>'
        '<div class="auth-logo"><em>C</em></div>'
        '<div class="auth-title">Sign in to Clarity</div>'
        '<div class="auth-subtitle">Unlock full analysis capabilities with a free account</div>'
        '<a class="auth-google-btn" onclick="'
        "var u=new URL(window.location.href);"
        "u.searchParams.set('auth_action','google_signin');"
        "u.searchParams.delete('show_auth');"
        "window.location.href=u.toString();"
        '">'
        '<img src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg" alt="G">'
        'Continue with Google</a>'
        '<div class="auth-features">'
        '<div class="auth-feature"><span class="auth-feature-icon">&#10003;</span>'
        '<span>5 free analyses per month</span>'
        '<span class="auth-limit-badge">FREE</span></div>'
        '<div class="auth-feature"><span class="auth-feature-icon">&#10003;</span>'
        '<span>Save &amp; revisit past valuations</span></div>'
        '<div class="auth-feature"><span class="auth-feature-icon">&#10003;</span>'
        '<span>Build a personal watchlist</span></div>'
        '<div class="auth-feature"><span class="auth-feature-icon">&#10003;</span>'
        '<span>Multi-model DCF, comparables &amp; more</span></div>'
        '</div>'
        '<div class="auth-visitor-note">'
        'No account? You can still explore popular tickers like AAPL, NVDA, MSFT '
        'and more &mdash; no sign-in required.</div>'
        '</div></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


def render_limit_reached_modal():
    """Render modal when user hits their monthly limit."""
    html = _AUTH_CSS + (
        '<div class="auth-overlay" id="limitOverlay">'
        '<div class="auth-modal">'
        '<button class="auth-modal-close" onclick="'
        "document.getElementById('limitOverlay').style.display='none';"
        '">&times;</button>'
        '<div class="auth-logo"><em>C</em></div>'
        '<div class="auth-title">Monthly Limit Reached</div>'
        '<div class="auth-subtitle">You have used all 5 free analyses this month.<br>'
        'Your limit resets on the 1st of next month.</div>'
        '<div style="padding:16px;background:rgba(62,207,142,0.04);'
        'border:1px solid rgba(62,207,142,0.08);border-radius:12px;margin:16px 0">'
        '<div style="font-family:Inter,sans-serif;font-size:0.8rem;color:#94a3b8;margin-bottom:8px">'
        'Want unlimited analyses?</div>'
        '<div style="font-family:Inter,sans-serif;font-size:0.9rem;color:#3ecf8e;font-weight:600">'
        'Pro plan coming soon</div></div>'
        '<div class="auth-visitor-note">'
        'You can still revisit your saved analyses and explore popular tickers while you wait.'
        '</div></div></div>'
    )
    st.markdown(html, unsafe_allow_html=True)


# ================================================================
#  USER HEADER (sign-in button or user pill)
# ================================================================

def render_user_header():
    """Return HTML for the auth section (top-right area)."""
    user = get_user()
    if user:
        name = user.get("displayName", "User")
        photo = user.get("photoURL", "")
        runs = user.get("monthly_runs", 0)
        limit = TIER_LIMITS.get(user.get("tier", "free"), 5)
        pct = min(100, int((runs / max(limit, 1)) * 100))
        if photo:
            avatar = '<img src="' + photo + '" alt="" referrerpolicy="no-referrer">'
        else:
            avatar = name[0].upper() if name else "U"
        return (
            '<div style="display:flex;align-items:center;gap:10px">'
            '<div class="auth-user-pill" onclick="'
            "var u=new URL(window.location.href);"
            "u.searchParams.set('auth_action','signout');"
            "window.location.href=u.toString();"
            '" title="Click to sign out">'
            '<div class="auth-user-avatar">' + avatar + '</div>'
            '<div>'
            '<div class="auth-user-name">' + name + '</div>'
            '<div class="auth-usage-bar" style="width:80px" title="'
            + str(runs) + '/' + str(limit) + ' analyses used">'
            '<div class="auth-usage-fill" style="width:' + str(pct) + '%"></div>'
            '</div></div></div>'
            '<span style="font-family:JetBrains Mono,monospace;font-size:0.6rem;color:#475569">'
            + str(runs) + '/' + str(limit) + '</span></div>'
        )
    else:
        return (
            '<a class="auth-google-btn" style="width:auto;padding:8px 18px;'
            'font-size:0.78rem;border-radius:10px" onclick="'
            "var u=new URL(window.location.href);"
            "u.searchParams.set('show_auth','true');"
            "window.location.href=u.toString();"
            '">Sign in</a>'
        )


# ================================================================
#  FIRESTORE: SAVE ANALYSIS & WATCHLIST
# ================================================================

def _firestore_save_js(uid, analysis_data):
    data_json = json.dumps(analysis_data)
    return (
        '<script>'
        '(function(){'
        'if(!window._clarityAuth)return;'
        'var db=window._clarityAuth.getDb();'
        'var ref=db.collection("users").doc("' + uid + '");'
        'ref.get().then(function(doc){'
        'if(doc.exists){'
        'var d=doc.data();var a=d.saved_analyses||[];'
        'a.unshift(' + data_json + ');'
        'if(a.length>50)a.length=50;'
        'ref.update({saved_analyses:a,monthly_runs:(d.monthly_runs||0)+1});'
        '}'
        '});'
        '})();'
        '</script>'
    )


def save_analysis(ticker, result_summary):
    """Save an analysis to Firestore and increment usage counter."""
    user = get_user()
    if not user:
        return
    analysis_data = {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fair_value": result_summary.get("fair_value"),
        "price": result_summary.get("price"),
        "verdict": result_summary.get("verdict"),
        "sector": result_summary.get("sector"),
        "upside_pct": result_summary.get("upside_pct"),
    }
    st.components.v1.html(_firestore_save_js(user["uid"], analysis_data), height=0)
    increment_usage()


def _firestore_watchlist_js(uid, action, ticker):
    if action == "add":
        op = 'firebase.firestore.FieldValue.arrayUnion("' + ticker + '")'
    else:
        op = 'firebase.firestore.FieldValue.arrayRemove("' + ticker + '")'
    return (
        '<script>'
        '(function(){'
        'if(!window._clarityAuth)return;'
        'var db=window._clarityAuth.getDb();'
        'db.collection("users").doc("' + uid + '").update({watchlist:' + op + '});'
        '})();'
        '</script>'
    )


def toggle_watchlist(ticker):
    """Add or remove a ticker from the user watchlist."""
    user = get_user()
    if not user:
        return
    watchlist = user.get("watchlist", [])
    if ticker in watchlist:
        watchlist.remove(ticker)
        action = "remove"
    else:
        watchlist.append(ticker)
        action = "add"
    user["watchlist"] = watchlist
    st.session_state.auth_user = user
    st.components.v1.html(_firestore_watchlist_js(user["uid"], action, ticker), height=0)


def is_in_watchlist(ticker):
    user = get_user()
    if not user:
        return False
    return ticker in user.get("watchlist", [])


# ================================================================
#  INIT (call once at top of app.py)
# ================================================================

def init_auth():
    """Initialize Firebase auth. Call after set_page_config."""
    _init_auth_state()
    _render_auth_component()
    params = st.query_params
    if params.get("show_auth") == "true":
        render_sign_in_modal()
