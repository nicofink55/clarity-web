"""
Clarity Auth v3 - Firebase Auth
The sign-in button is rendered inside st.components.v1.html as a real clickable
button. When the user clicks it, signInWithPopup fires from within the iframe.
Browsers allow popups from iframes when triggered by genuine user clicks.
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

ADMIN_EMAILS = [
    "nicofink55@gmail.com",
]


def _init_auth_state():
    for k, v in {"auth_user": None, "auth_initialized": False}.items():
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
    return user.get("monthly_runs", 0) if user else 0

def can_run_analysis(ticker=None):
    tier = get_tier()
    if tier == "pro":
        return True, ""
    if tier == "visitor":
        if ticker and ticker.upper() in POPULAR_TICKERS:
            return True, ""
        return False, "sign_in"
    usage = get_monthly_usage()
    if usage >= TIER_LIMITS["free"]:
        return False, "limit_reached"
    return True, ""

def increment_usage():
    user = get_user()
    if user:
        user["monthly_runs"] = user.get("monthly_runs", 0) + 1
        st.session_state.auth_user = user


# ================================================================
#  FIREBASE HTML COMPONENTS
# ================================================================

_FB_SCRIPTS = (
    '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js"><\/script>'
    '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-auth-compat.js"><\/script>'
    '<script src="https://www.gstatic.com/firebasejs/10.12.2/firebase-firestore-compat.js"><\/script>'
).replace('<\\/script>', '</script>')

_FB_INIT = (
    'var app;try{app=firebase.app();}catch(e){'
    'app=firebase.initializeApp(%s);}'
    'var auth=firebase.auth();'
    'var db=firebase.firestore();'
) % json.dumps(FIREBASE_CONFIG)

# Auth state listener JS (shared between components)
_AUTH_LISTENER = '''
auth.onAuthStateChanged(async function(user){
  if(user){
    var doc=await db.collection("users").doc(user.uid).get();
    var ud=doc.exists?doc.data():{};
    if(!doc.exists){
      ud={email:user.email,displayName:user.displayName||"",
      photoURL:user.photoURL||"",tier:"free",
      created:new Date().toISOString(),monthly_runs:0,
      monthly_reset:new Date().toISOString().slice(0,7),
      watchlist:[],saved_analyses:[]};
      await db.collection("users").doc(user.uid).set(ud);
    }
    var cm=new Date().toISOString().slice(0,7);
    if(ud.monthly_reset!==cm){
      ud.monthly_runs=0;ud.monthly_reset=cm;
      await db.collection("users").doc(user.uid).update({monthly_runs:0,monthly_reset:cm});
    }
    var ad=JSON.stringify({uid:user.uid,email:user.email,
      displayName:user.displayName||(user.email?user.email.split("@")[0]:"User"),
      photoURL:user.photoURL||"",tier:ud.tier||"free",
      monthly_runs:ud.monthly_runs||0,
      watchlist:ud.watchlist||[],
      saved_count:(ud.saved_analyses||[]).length});
    var enc=encodeURIComponent(ad);
    var u=new URL(window.parent.location.href);
    if(u.searchParams.get("auth_data")!==enc){
      u.searchParams.set("auth_data",enc);
      window.parent.history.replaceState({},"",u);
      window.parent.location.reload();
    }
  }else{
    var u=new URL(window.parent.location.href);
    if(u.searchParams.has("auth_data")){
      u.searchParams.delete("auth_data");
      window.parent.history.replaceState({},"",u);
      window.parent.location.reload();
    }
  }
});
'''


def _render_google_button(height=46):
    """Visible Google sign-in button inside iframe. User click = popup allowed."""
    html = (
        '<!DOCTYPE html><html><head>'
        '<link href="https://fonts.googleapis.com/css2?family=Inter:wght@500&display=swap" rel="stylesheet">'
        '<style>'
        '*{margin:0;padding:0;box-sizing:border-box}'
        'body{background:transparent;font-family:Inter,sans-serif;display:flex;justify-content:center}'
        '.g-btn{display:flex;align-items:center;justify-content:center;gap:10px;'
        'width:100%;max-width:340px;padding:11px 20px;'
        'background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.10);'
        'border-radius:10px;cursor:pointer;font-family:Inter,sans-serif;'
        'font-size:0.85rem;font-weight:500;color:#e2e8f0;transition:all 0.2s;outline:none}'
        '.g-btn:hover{background:rgba(255,255,255,0.08);border-color:rgba(62,207,142,0.25);'
        'box-shadow:0 2px 16px rgba(62,207,142,0.08)}'
        '.g-btn:active{transform:scale(0.98)}'
        '.g-btn img{width:18px;height:18px}'
        '.g-btn.ld{opacity:0.6;pointer-events:none}'
        '</style>'
        + _FB_SCRIPTS +
        '</head><body>'
        '<button class="g-btn" id="gb" onclick="doSignIn()">'
        '<img src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg">'
        'Continue with Google</button>'
        '<script>'
        + _FB_INIT +
        'function doSignIn(){'
        'var b=document.getElementById("gb");'
        'b.classList.add("ld");b.innerHTML=\'<img src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg">Signing in...\';'
        'var p=new firebase.auth.GoogleAuthProvider();'
        'auth.signInWithPopup(p).then(async function(r){'
        'var user=r.user;'
        'var docRef=db.collection("users").doc(user.uid);'
        'var doc=await docRef.get();'
        'var ud=doc.exists?doc.data():{};'
        'if(!doc.exists){'
        'ud={email:user.email,displayName:user.displayName||"",'
        'photoURL:user.photoURL||"",tier:"free",'
        'created:new Date().toISOString(),monthly_runs:0,'
        'monthly_reset:new Date().toISOString().slice(0,7),'
        'watchlist:[],saved_analyses:[]};'
        'await docRef.set(ud);}'
        'var cm=new Date().toISOString().slice(0,7);'
        'if(ud.monthly_reset!==cm){'
        'ud.monthly_runs=0;ud.monthly_reset=cm;'
        'await docRef.update({monthly_runs:0,monthly_reset:cm});}'
        'var ad=JSON.stringify({uid:user.uid,email:user.email,'
        'displayName:user.displayName||(user.email?user.email.split("@")[0]:"User"),'
        'photoURL:user.photoURL||"",tier:ud.tier||"free",'
        'monthly_runs:ud.monthly_runs||0,'
        'watchlist:ud.watchlist||[],'
        'saved_count:(ud.saved_analyses||[]).length});'
        'var enc=encodeURIComponent(ad);'
        'var u=new URL(window.parent.location.href);'
        'u.searchParams.set("auth_data",enc);'
        'window.parent.history.replaceState({},"",u);'
        'window.parent.location.reload();'
        '}).catch(function(e){'
        'console.error("Sign-in error:",e);'
        'b.classList.remove("ld");'
        'b.innerHTML=\'<img src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg">Continue with Google\';'
        '});}'
        '</script></body></html>'
    )
    st.components.v1.html(html, height=height, scrolling=False)


def _render_silent_listener():
    """Hidden component: listens for auth state, handles sign-out."""
    do_signout = "true" if st.session_state.get("_auth_do_signout") else "false"
    if st.session_state.get("_auth_do_signout"):
        del st.session_state["_auth_do_signout"]

    html = (
        _FB_SCRIPTS +
        '<script>'
        '(function(){'
        + _FB_INIT +
        'if(' + do_signout + '){'
        'auth.signOut().then(function(){'
        'var u=new URL(window.parent.location.href);'
        'u.searchParams.delete("auth_data");'
        'window.parent.history.replaceState({},"",u);'
        'window.parent.location.reload();});}'
        + _AUTH_LISTENER +
        '})();'
        '</script>'
    )
    st.components.v1.html(html, height=0)


def _render_firestore_action(js_code):
    """Hidden component to run a Firestore operation."""
    html = (
        _FB_SCRIPTS +
        '<script>(function(){'
        + _FB_INIT +
        js_code +
        '})();</script>'
    )
    st.components.v1.html(html, height=0)


# ================================================================
#  INIT
# ================================================================

def init_auth():
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
    _render_silent_listener()


# ================================================================
#  UI
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
        line-height:1.5;margin-bottom:2px">
        Free account &mdash; 5 analyses/month, saved valuations, watchlists</div>
    </div>
    """, unsafe_allow_html=True)
    _, btn_col, _ = st.columns([1.2, 1.6, 1.2])
    with btn_col:
        _render_google_button(height=46)
    st.markdown(
        '<div style="text-align:center;font-size:0.68rem;color:#475569;'
        'font-family:Inter,sans-serif;margin-top:4px">'
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
        tier_label = "PRO" if tier == "pro" else "FREE"
        tier_color = "#3ecf8e" if tier == "pro" else "#64748b"
        usage_text = "" if tier == "pro" else (" &middot; " + str(runs) + "/" + str(limit))
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
            + tier_color + ';font-weight:600;letter-spacing:0.05em">'
            + tier_label + usage_text + '</div>'
            '</div></div></div>', unsafe_allow_html=True)
        if st.button("Sign out", key="signout_btn", use_container_width=True):
            st.session_state["_auth_do_signout"] = True
            st.session_state.auth_user = None
            st.rerun()
    else:
        st.markdown(
            '<div style="font-family:Inter,sans-serif;font-size:0.72rem;color:#64748b;'
            'padding:4px 0 4px">Sign in for full access</div>', unsafe_allow_html=True)
        _render_google_button(height=44)


# ================================================================
#  ACTIONS
# ================================================================

def save_analysis(ticker, result_summary):
    user = get_user()
    if not user:
        return
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
        'var ref=db.collection("users").doc("' + user["uid"] + '");'
        'ref.get().then(function(doc){'
        'if(doc.exists){'
        'var d=doc.data();var a=d.saved_analyses||[];'
        'a.unshift(' + data + ');'
        'if(a.length>50)a.length=50;'
        'ref.update({saved_analyses:a,monthly_runs:(d.monthly_runs||0)+1});}});'
    )
    _render_firestore_action(js)
    increment_usage()


def toggle_watchlist(ticker):
    user = get_user()
    if not user:
        return
    wl = user.get("watchlist", [])
    if ticker in wl:
        wl.remove(ticker)
        op = 'firebase.firestore.FieldValue.arrayRemove("' + ticker + '")'
    else:
        wl.append(ticker)
        op = 'firebase.firestore.FieldValue.arrayUnion("' + ticker + '")'
    user["watchlist"] = wl
    st.session_state.auth_user = user
    js = 'db.collection("users").doc("' + user["uid"] + '").update({watchlist:' + op + '});'
    _render_firestore_action(js)


def is_in_watchlist(ticker):
    user = get_user()
    if not user:
        return False
    return ticker in user.get("watchlist", [])
