# APEX — Free Cloud Deployment Guide

## Option A: Streamlit Community Cloud (Recommended)
Free, permanent URL, no credit card needed.

### Steps

**1. Push to GitHub**
```bash
cd apex/               # your project folder
git init
git add .
git commit -m "APEX India Edition v2.3.0"
# Create a new repo on github.com, then:
git remote add origin https://github.com/YOUR_USERNAME/apex-trading.git
git push -u origin main
```

**2. Deploy on Streamlit Cloud**
1. Go to → https://share.streamlit.io
2. Sign in with GitHub
3. Click **"New app"**
4. Repository: `YOUR_USERNAME/apex-trading`
5. Branch: `main`
6. Main file path: `app.py`
7. Click **Deploy** — takes ~2–3 minutes

Your app is now live at:
```
https://YOUR_USERNAME-apex-trading-app-XXXXX.streamlit.app
```
Share this link with anyone. It's always on, free forever.

---

## Option B: Render.com (if you want more control)
Free tier — spins down after 15 min inactivity (cold start ~30s).

**Add this file as `render.yaml`:**
```yaml
services:
  - type: web
    name: apex-trading
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: streamlit run app.py --server.port $PORT --server.address 0.0.0.0
    envVars:
      - key: PYTHON_VERSION
        value: 3.11.0
```
Then push to GitHub and connect at https://render.com

---

## Option C: Hugging Face Spaces
Free, GPU optional, good for ML projects.

1. Create account at https://huggingface.co
2. New Space → SDK: **Streamlit** → Python 3.11
3. Upload all files, rename `app.py` as is
4. URL: `https://huggingface.co/spaces/YOUR_NAME/apex-trading`

---

## Local Run (always works)
```bash
pip install -r requirements.txt
streamlit run app.py
# Opens at http://localhost:8501
```

---

## Project structure required on GitHub
```
apex-trading/
├── app.py                      ← Streamlit entry point
├── requirements.txt
├── .streamlit/
│   └── config.toml             ← Dark saffron theme
├── main.py
├── data/
│   ├── __init__.py
│   └── data_engine.py
├── strategies/
│   ├── __init__.py
│   └── apex_confluence.py
├── backtesting/
│   ├── __init__.py
│   ├── evaluator.py
│   └── report.py
├── execution/
│   ├── __init__.py
│   └── paper_trader.py
└── utils/
    ├── __init__.py
    ├── constants.py
    ├── logger.py
    └── synthetic_data.py
```

## Notes
- yfinance data fetch works fine on all platforms
- Streamlit caches results for 1 hour (`@st.cache_data(ttl=3600)`)
- No API keys needed — all data is free via Yahoo Finance
