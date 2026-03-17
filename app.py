# app.py  —  APEX Streamlit Web Interface
# Deploy free on: https://streamlit.io/cloud
#
# Local run:  streamlit run app.py

import sys, warnings
sys.path.insert(0, ".")
warnings.filterwarnings("ignore")

import streamlit as st
import pandas as pd
from pathlib import Path

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="APEX Trading Engine",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS (saffron theme) ────────────────────────────────────────────────
st.markdown("""
<style>
  [data-testid="stSidebar"] { background: #1a0a00; }
  .metric-card { background:#1a1a1a; border:1px solid #2e2e2e; border-radius:10px;
                 padding:16px; margin:6px 0; }
  .valid-badge   { background:#1a4a2a; color:#27ae60; padding:4px 10px;
                   border-radius:4px; font-weight:700; font-size:13px; }
  .invalid-badge { background:#4a1a1a; color:#e74c3c; padding:4px 10px;
                   border-radius:4px; font-weight:700; font-size:13px; }
  .stButton > button { background:#e67e22; color:white; font-weight:700;
                       border:none; border-radius:6px; }
  .stButton > button:hover { background:#ca6f1e; }
</style>""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
# ⚡ APEX Algorithmic Trading Engine
### India Edition · NSE/BSE · Groww (₹0 brokerage) · ₹10,000 Capital
""")
st.caption("Strategy: EMA-50/200 Golden Cross × RSI(14) × ATR(14) trailing stop · 2% equity risk per trade")
st.divider()

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚙️ Settings")
    mode     = st.selectbox("Mode", ["Backtest", "Liquidity Scan", "Charges Calculator"])
    exchange = st.selectbox("Exchange", ["NSE", "BSE"])
    capital  = st.number_input("Capital (₹)", min_value=1000, max_value=10_000_000,
                                value=10_000, step=1000)
    years    = st.slider("Backtest Years", 1, 10, 5)

    st.divider()
    st.markdown("#### Ticker Input")
    ticker_input = st.text_input(
        "Symbols (comma-separated)",
        placeholder="RELIANCE, TCS, INFY",
        help="NSE symbols only. The .NS suffix is added automatically.",
    )
    use_preset = st.selectbox(
        "Or use preset watchlist",
        ["— none —", "NIFTY 5 Sampler", "NIFTY IT", "NIFTY Bank", "NIFTY Auto"],
    )

    PRESETS = {
        "NIFTY 5 Sampler":  "RELIANCE, TCS, HDFCBANK, INFY, ICICIBANK",
        "NIFTY IT":         "TCS, INFY, HCLTECH, WIPRO, TECHM",
        "NIFTY Bank":       "HDFCBANK, ICICIBANK, KOTAKBANK, SBIN, AXISBANK",
        "NIFTY Auto":       "MARUTI, TATAMOTORS, M&M, BAJAJ-AUTO, EICHERMOT",
    }
    if use_preset != "— none —":
        ticker_input = PRESETS[use_preset]

    run_btn = st.button("▶  Run", use_container_width=True)

    st.divider()
    st.markdown("""
**Groww Charges (delivery)**
| Component | Rate |
|---|---|
| Brokerage | **₹0 FREE** |
| STT | 0.1% |
| Exch Txn | 0.00297%/leg |
| Stamp Duty | 0.015% buy |
| GST | 18% on exch |
| DP Charges | ₹15.93 flat/sell |
""")

# ── Helpers ───────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600, show_spinner=False)
def fetch_and_backtest(symbols, capital, years, exchange):
    from data.data_engine import DataEngine, normalise_ticker
    from backtesting.evaluator import BacktestEvaluator

    engine    = DataEngine(exchange=exchange, use_cache=True)
    evaluator = BacktestEvaluator(initial_capital=capital)
    results, skipped = [], []

    for sym in symbols:
        ticker = normalise_ticker(sym, exchange)
        daily  = engine.get_daily(sym, years=years)
        if daily is None or daily.empty:
            skipped.append((ticker, "No data"))
            continue
        ok, tv = engine.liquidity_filter.evaluate(ticker, daily)
        if not ok:
            from utils.constants import CRORE
            skipped.append((ticker, f"Illiquid (₹{tv/CRORE:.1f} Cr < ₹50 Cr)"))
            continue
        r = evaluator.run(ticker, daily, years=years)
        results.append(r)

    results.sort(key=lambda r: r.score, reverse=True)
    return results, skipped


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_liquidity(symbols, exchange):
    from data.data_engine import DataEngine, normalise_ticker
    from utils.constants import CRORE

    engine = DataEngine(exchange=exchange, use_cache=True)
    rows   = []
    for sym in symbols:
        ticker = normalise_ticker(sym, exchange)
        daily  = engine.get_daily(sym, years=2)
        if daily is None or daily.empty:
            rows.append({"Ticker": ticker, "Status": "NO DATA", "Avg Turnover (₹ Cr)": 0})
            continue
        ok, tv = engine.liquidity_filter.evaluate(ticker, daily)
        rows.append({
            "Ticker":              ticker,
            "Status":              "LIQUID ✓" if ok else "ILLIQUID ✗",
            "Avg Turnover (₹ Cr)": round(tv / CRORE, 2),
        })
    return pd.DataFrame(rows)


def result_to_row(r):
    pf = f"{r.profit_factor:.3f}" if r.profit_factor != float("inf") else "∞"
    return {
        "Ticker":         r.ticker,
        "Return (%)":     f"{r.total_return_pct:+.2f}",
        "CAGR (%)":       f"{r.cagr_pct:+.2f}",
        "Max DD (%)":     f"{r.max_drawdown_pct:.2f}",
        "Win Rate (%)":   f"{r.win_rate_pct:.1f}",
        "Profit Factor":  pf,
        "Sharpe":         f"{r.sharpe_ratio:.3f}",
        "Calmar":         f"{r.calmar_ratio:.3f}",
        "Trades":         r.total_trades,
        "Avg Hold (d)":   f"{r.avg_hold_days:.0f}",
        "Alpha (%)":      f"{r.alpha_pct:+.2f}",
        "Score /100":     r.score,
        "Valid":          "✓ YES" if r.is_valid else "✗ NO",
    }


# ── Main area ─────────────────────────────────────────────────────────────────

if mode == "Charges Calculator":
    st.subheader("💸 Groww Delivery Charges Calculator")
    c1, c2 = st.columns(2)
    buy_val  = c1.number_input("Buy Value (₹)", min_value=100.0, value=float(capital))
    sell_val = c2.number_input("Sell Value (₹)", min_value=0.0,  value=float(capital))
    n_scrips = st.number_input("No. of scrips sold", min_value=1, value=1)

    from utils.constants import (STT_DELIVERY_PCT, EXCHANGE_TXN_CHARGES,
                                  STAMP_DUTY_PCT, GST_RATE, SEBI_CHARGES,
                                  DP_CHARGE_WITH_GST, COMMISSION_PCT)

    stt    = (buy_val + sell_val) * STT_DELIVERY_PCT
    exch   = (buy_val + sell_val) * EXCHANGE_TXN_CHARGES
    sebi   = (buy_val + sell_val) * SEBI_CHARGES
    stamp  = buy_val * STAMP_DUTY_PCT
    gst    = exch * GST_RATE
    dp     = DP_CHARGE_WITH_GST * n_scrips
    total  = stt + exch + sebi + stamp + gst + dp

    df_charges = pd.DataFrame([
        {"Component": "Brokerage",              "Rate": "FREE ₹0",           "Amount (₹)": 0.00},
        {"Component": "STT (0.1% buy+sell)",    "Rate": "0.100%",             "Amount (₹)": round(stt,   4)},
        {"Component": "NSE Exch Txn (×2)",      "Rate": "0.00297%/leg",       "Amount (₹)": round(exch,  4)},
        {"Component": "SEBI Fee",               "Rate": "₹10/Cr",             "Amount (₹)": round(sebi,  4)},
        {"Component": "Stamp Duty (buy only)",  "Rate": "0.015%",             "Amount (₹)": round(stamp, 4)},
        {"Component": "GST on exch charges",    "Rate": "18%",                "Amount (₹)": round(gst,   4)},
        {"Component": f"DP Charges ({n_scrips} scrip{'s' if n_scrips>1 else ''})", "Rate": "₹15.93 flat", "Amount (₹)": round(dp, 4)},
        {"Component": "TOTAL",                  "Rate": f"≈{COMMISSION_PCT*100:.3f}%+DP", "Amount (₹)": round(total, 4)},
    ])
    st.dataframe(df_charges, use_container_width=True, hide_index=True)

    m1, m2, m3 = st.columns(3)
    m1.metric("Total Charges", f"₹{total:.2f}")
    m2.metric("As % of Trade", f"{total/(buy_val+sell_val or 1)*100:.3f}%")
    m3.metric("Brokerage",     "₹0 (FREE)")
    st.success("✓ Groww charges **zero brokerage** on equity delivery trades.")

elif not run_btn:
    # Landing state
    st.info("👈 Configure settings in the sidebar, enter ticker symbols, then click **▶ Run**.")

    st.markdown("### How it works")
    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("""
**📊 Backtest**
- Downloads NSE/BSE data via yfinance
- Applies ₹50 Cr liquidity filter
- Runs EMA golden cross strategy
- Shows 10+ metrics per ticker
- Ranks by composite score
        """)
    with col2:
        st.markdown("""
**🔍 Liquidity Scan**
- Checks if avg 20-day turnover
  meets ₹50 Crore minimum
- Filters out illiquid stocks
- Shows turnover in Crores
        """)
    with col3:
        st.markdown("""
**💸 Charges Calculator**
- Calculates exact Groww delivery
  charges for any trade size
- Breaks down STT, exch, stamp,
  GST and DP charges
        """)

    st.markdown("### Strategy Rules")
    st.markdown("""
| Signal | Condition |
|---|---|
| **Entry** | EMA-50 crosses above EMA-200 (golden cross) |
| | RSI(14) between 40–65 (momentum building, not overbought) |
| | Avg 20-day turnover ≥ ₹50 Crore (liquidity gate) |
| **Stop Loss** | Entry − (ATR(14) × 2.5) — trails upward only |
| **Position Size** | 2% equity risk per trade · 1-share fallback for small capital |
| **Exit** | Stop hit OR EMA death cross |
    """)

else:
    # ── Parse tickers ────────────────────────────────────────────────────
    raw_tickers = [t.strip().upper() for t in ticker_input.split(",") if t.strip()]
    if not raw_tickers:
        st.error("Please enter at least one ticker symbol.")
        st.stop()

    if mode == "Liquidity Scan":
        st.subheader(f"🔍 Liquidity Scan — {exchange}")
        with st.spinner(f"Scanning {len(raw_tickers)} tickers..."):
            df_liq = fetch_liquidity(raw_tickers, exchange)

        liquid   = df_liq[df_liq["Status"].str.startswith("LIQUID")]
        illiquid = df_liq[~df_liq["Status"].str.startswith("LIQUID")]

        c1, c2, c3 = st.columns(3)
        c1.metric("Scanned",  len(df_liq))
        c2.metric("Liquid",   len(liquid),   delta=f"{len(liquid)/len(df_liq)*100:.0f}%")
        c3.metric("Illiquid", len(illiquid), delta=f"-{len(illiquid)}", delta_color="inverse")

        st.dataframe(df_liq.style.applymap(
            lambda v: "color: green" if "LIQUID ✓" in str(v) else ("color: red" if "✗" in str(v) else ""),
        ), use_container_width=True, hide_index=True)

    elif mode == "Backtest":
        st.subheader(f"📊 Backtest — {exchange} · ₹{capital:,.0f} · {years}Y")

        with st.spinner(f"Fetching data and backtesting {len(raw_tickers)} ticker(s)... This may take a minute."):
            try:
                results, skipped = fetch_and_backtest(raw_tickers, capital, years, exchange)
            except Exception as e:
                st.error(f"Error: {e}")
                st.stop()

        if skipped:
            with st.expander(f"⚠ {len(skipped)} ticker(s) skipped"):
                for ticker, reason in skipped:
                    st.write(f"• **{ticker}** — {reason}")

        if not results:
            st.warning("No results — all tickers were skipped (no data or illiquid).")
            st.stop()

        # ── Summary metrics ───────────────────────────────────────────────
        valid_ct  = sum(1 for r in results if r.is_valid)
        best      = max(results, key=lambda r: r.total_return_pct)
        best_score= max(results, key=lambda r: r.score)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Tickers Backtested", len(results))
        c2.metric("Passed Validation",  valid_ct,
                  delta="DEPLOY ✓" if valid_ct else "NONE VALID",
                  delta_color="normal" if valid_ct else "inverse")
        c3.metric("Best Return",  f"{best.total_return_pct:+.2f}%", delta=best.ticker)
        c4.metric("Top Score",    f"{best_score.score:.0f}/100",    delta=best_score.ticker)

        # ── Summary table ─────────────────────────────────────────────────
        st.markdown("#### 📋 Results (ranked by Score)")
        df_results = pd.DataFrame([result_to_row(r) for r in results])

        def colour_valid(val):
            if "YES" in str(val): return "color: green; font-weight: bold"
            if "NO"  in str(val): return "color: red"
            return ""
        def colour_num(val):
            try:
                v = float(str(val).replace("+","").replace("%",""))
                return "color: green" if v > 0 else ("color: red" if v < 0 else "")
            except: return ""

        st.dataframe(
            df_results.style
                .applymap(colour_valid, subset=["Valid"])
                .applymap(colour_num,   subset=["Return (%)","CAGR (%)","Alpha (%)"])
                .bar(subset=["Score /100"], color="#e67e22", vmin=0, vmax=100),
            use_container_width=True, hide_index=True,
        )

        # ── HTML report download ──────────────────────────────────────────
        from backtesting.report import generate_html_report
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            path = generate_html_report(results, output_path=Path(f.name))
        html_bytes = path.read_bytes()
        st.download_button(
            "⬇ Download Full HTML Report",
            data=html_bytes,
            file_name="apex_backtest_report.html",
            mime="text/html",
            use_container_width=True,
        )

        # ── Per-ticker detail ─────────────────────────────────────────────
        st.markdown("#### 🔎 Per-Ticker Detail")
        for r in results:
            valid_html = (
                '<span class="valid-badge">✓ VALID — DEPLOY</span>'
                if r.is_valid else
                '<span class="invalid-badge">✗ INVALID — DO NOT DEPLOY</span>'
            )
            with st.expander(f"{r.ticker}   |   Return: {r.total_return_pct:+.2f}%   |   Score: {r.score:.0f}/100"):
                st.markdown(valid_html, unsafe_allow_html=True)
                if not r.is_valid:
                    for msg in r.validation_messages:
                        st.error(f"❌ {msg}")

                pf_str = f"{r.profit_factor:.3f}" if r.profit_factor != float("inf") else "∞"
                col_a, col_b, col_c = st.columns(3)
                col_a.metric("Total Return",       f"{r.total_return_pct:+.2f}%")
                col_a.metric("CAGR",               f"{r.cagr_pct:+.2f}%")
                col_a.metric("Benchmark (B&H)",    f"{r.benchmark_return_pct:+.2f}%")
                col_a.metric("Alpha vs Benchmark", f"{r.alpha_pct:+.2f}%")
                col_b.metric("Win Rate",           f"{r.win_rate_pct:.1f}%")
                col_b.metric("Profit Factor",      pf_str)
                col_b.metric("Sharpe (trade,rf=7%)",f"{r.sharpe_ratio:.3f}")
                col_b.metric("Calmar",             f"{r.calmar_ratio:.3f}")
                col_c.metric("Total Trades",       r.total_trades)
                col_c.metric("Avg Hold (days)",    f"{r.avg_hold_days:.0f}")
                col_c.metric("Best Trade",         f"{r.best_trade_pct:+.2f}%")
                col_c.metric("Worst Trade",        f"{r.worst_trade_pct:+.2f}%")

                st.markdown(f"""
| Capital | Initial | Final |
|---|---|---|
| ₹ | ₹{r.initial_equity:,.2f} | ₹{r.final_equity:,.2f} |

**Gross Profit:** ₹{r.gross_profit:,.2f}  &nbsp;·&nbsp;
**Gross Loss:** ₹{r.gross_loss:,.2f}  &nbsp;·&nbsp;
**Broker:** Groww ₹0 brokerage  &nbsp;·&nbsp;
**Max DD:** {r.max_drawdown_pct:.2f}%
                """)

# ── Footer ────────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "APEX Engine — India Edition v2.3.0  ·  NSE/BSE  ·  "
    "Strategy: EMA-50/200 × RSI(14) × ATR(14)  ·  "
    "Charges: Groww delivery (₹0 brokerage)  ·  "
    "⚠ For educational use only. Not financial advice."
)
