# Clarity — Web Edition

**SEC Filing → Multi-Model Valuation in seconds.**

Full analytical engine from the desktop app, now running in Streamlit. Zero CORS issues, all SEC/Yahoo API calls run server-side in Python.

## Quick Start

```bash
# Clone or copy these files
cd clarity_web

# Install dependencies
pip install -r requirements.txt

# Run locally
streamlit run app.py
```

Open `http://localhost:8501` in your browser.

## Deploy to Streamlit Community Cloud (Free)

1. Push this folder to a **GitHub repo**
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Set `app.py` as the main file
5. Deploy — done. You'll get a public URL like `https://your-app.streamlit.app`

## Deploy to Other Platforms

### Railway / Render
```bash
# Procfile
web: streamlit run app.py --server.port $PORT --server.address 0.0.0.0
```

### Docker
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

## Architecture

```
clarity_web/
├── app.py              # Streamlit UI (replaces tkinter)
├── engine.py           # Full analytical engine (unchanged from desktop)
├── requirements.txt    # Python dependencies
├── .streamlit/
│   └── config.toml     # Dark theme + server config
└── README.md
```

**What changed from desktop:**
- `engine.py` = lines 1-4200 of the original `clarity.py` (all SEC fetching, XBRL parsing, DCF, Monte Carlo, residual income, comps, ROIC fade, DDM, Bayesian triangulation) — **completely untouched**
- `app.py` = new Streamlit UI replacing the tkinter `ClarityApp` class
- Charts: tkinter Canvas → Plotly (interactive, better looking)
- No threading needed (Streamlit handles it)
- No CORS issues (server-side Python makes all API calls)

**What's identical:**
- All SEC EDGAR API calls and XBRL parsing
- HTML/PDF filing parsers
- Every valuation model (DCF, RI, Comps, EV/Rev, ROIC Fade, DDM)
- Monte Carlo simulation
- Bayesian model triangulation
- Sector detection and scenario generation
- Normalized FCF calculations
- Live peer comp fetching

## SEC EDGAR Note

EDGAR requires a `User-Agent` header. The engine uses `ClarityApp/1.0 (nico@example.com)`. Update this in `engine.py` line ~40 with your own email for production use.
"# clarity-web" 
