# utils/synthetic_data.py
"""
Synthetic OHLCV generator — India Edition
==========================================
Produces realistic NSE/BSE-style price series via GBM + 4 regime shifts.
All prices in INR.  Seed-stable for reproducible backtests.

Preset profiles
---------------
  large_cap   : ₹1,500–₹4,000  (NIFTY 50 names like RELIANCE, TCS)
  mid_cap     : ₹300–₹800      (NIFTY Midcap 100)
  small_cap   : ₹50–₹300       (NIFTY Smallcap)
  micro_cap   : ₹10–₹100       (SME / illiquid)

Usage
-----
  from utils.synthetic_data import make_nse_portfolio, generate_ohlcv
  portfolio = make_nse_portfolio()   # returns dict of ticker->DataFrame
"""

from __future__ import annotations
import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, Optional


def generate_ohlcv(
    ticker:       str   = "NSE_SIM",
    days:         int   = 1260,
    start_price:  float = 500.0,
    base_volume:  float = 10_00_000,
    annual_mu:    float | None = None,   # override all-regime drift
    annual_sig:   float | None = None,   # override all-regime vol
    seed: Optional[int] = 42,
) -> pd.DataFrame:
    """Generate synthetic daily OHLCV (INR) for testing."""
    rng = np.random.default_rng(seed)
    dt  = 1 / 252

    regimes = [
        (0.28 if annual_mu is None else annual_mu,
         0.20 if annual_sig is None else annual_sig),   # bull / steady
        (-0.35, 0.40 if annual_sig is None else annual_sig + 0.15),  # crash
        (0.55 if annual_mu is None else annual_mu + 0.15,
         0.28 if annual_sig is None else annual_sig + 0.05),         # recovery
        (0.18 if annual_mu is None else annual_mu,
         0.18 if annual_sig is None else annual_sig),                 # consolidation
    ]
    regime_len = days // len(regimes)

    closes = [start_price]
    sigmas: list[float] = []
    ri, in_reg = 0, 0

    while len(closes) - 1 < days:
        mu, sig = regimes[ri % len(regimes)]
        shock = rng.normal((mu - 0.5 * sig**2) * dt, sig * np.sqrt(dt))
        closes.append(max(closes[-1] * np.exp(shock), 0.05))
        sigmas.append(abs(shock))
        in_reg += 1
        if in_reg >= regime_len:
            ri += 1
            in_reg = 0

    closes_arr = np.array(closes[1:days + 1])
    sigmas_arr = np.array(sigmas[:days])
    prev_c     = np.concatenate([[start_price], closes_arr[:-1]])

    rng2      = np.random.default_rng((seed or 0) + 1)
    rng_pct   = np.abs(rng2.normal(0, 1, days)) * sigmas_arr * 2 + 0.002
    opens     = prev_c  * (1 + rng2.normal(0, 0.002, days))
    highs     = np.maximum(opens, closes_arr) * (1 + rng_pct * rng2.uniform(0.3, 1.0, days))
    lows      = np.minimum(opens, closes_arr) * (1 - rng_pct * rng2.uniform(0.3, 1.0, days))
    lows      = np.maximum(lows, 0.05)

    vol_z     = (sigmas_arr - sigmas_arr.mean()) / (sigmas_arr.std() + 1e-9)
    vol_mult  = np.clip(1 + 3 * vol_z, 0.3, 5.0)
    volumes   = (base_volume * vol_mult * rng2.lognormal(0, 0.3, days)).astype(int)

    idx = pd.bdate_range(start=datetime(2019, 1, 2), periods=days)
    df  = pd.DataFrame({
        "Open":   np.round(opens,       2),
        "High":   np.round(highs,       2),
        "Low":    np.round(lows,        2),
        "Close":  np.round(closes_arr,  2),
        "Volume": volumes,
    }, index=idx)
    df.index.name = "Date"
    return df


def make_nse_portfolio(days: int = 1300) -> Dict[str, pd.DataFrame]:
    """
    Return a ready-made dict of 8 synthetic NSE tickers spanning a wide
    price range — suitable for demonstrating the engine with ₹10,000 capital.

    Tickers are calibrated so that stocks priced ₹200–₹1,500 are the
    sweet spot for a ₹10,000 account (can buy 5–50 shares).
    """
    specs = [
        # ticker,           price,  vol/day,     mu,    sig,  seed  description
        ("TECHM_SIM.NS",   1_200, 20_00_000,   0.22,  0.22,   10), # IT mid-large
        ("IDFCFIRSTB.NS",    80,  50_00_000,   0.25,  0.30,   11), # Private bank
        ("IRCTC_SIM.NS",    700,   5_00_000,   0.35,  0.25,   12), # Rail / tourism
        ("ZOMATO_SIM.NS",   150,  80_00_000,   0.30,  0.45,   13), # New-age
        ("ONGC_SIM.NS",     230,  40_00_000,   0.15,  0.22,   14), # PSU energy
        ("TATACHEM_SIM.NS", 980,   6_00_000,   0.20,  0.24,   15), # Chemical
        ("RBLBANK_SIM.NS",  230,  20_00_000,   0.10,  0.38,   16), # Pvt bank stress
        ("LALPATHLAB.NS",  2400,   2_00_000,   0.18,  0.20,   17), # Healthcare
    ]
    portfolio: Dict[str, pd.DataFrame] = {}
    for ticker, price, vol, mu, sig, seed in specs:
        portfolio[ticker] = generate_ohlcv(
            ticker=ticker, days=days, start_price=float(price),
            base_volume=float(vol), annual_mu=mu, annual_sig=sig, seed=seed,
        )
    return portfolio


def describe(df: pd.DataFrame, ticker: str = "SIM") -> str:
    ret  = ((df["Close"].iloc[-1] / df["Close"].iloc[0]) - 1) * 100
    t20  = (df["Close"] * df["Volume"]).rolling(20).mean().dropna().iloc[-1]
    last = df["Close"].iloc[-1]
    return (
        f"  {ticker:22s}  ₹{df['Close'].iloc[0]:>8.2f} → ₹{last:>8.2f}"
        f"  ({ret:+6.1f}%)  turnover=₹{t20/1e7:.1f}Cr  bars={len(df)}"
    )
