# data/data_engine.py
"""
APEX Data Engine  --  Indian Markets Edition
============================================
1. Fetch daily/hourly OHLCV via yfinance for NSE/BSE instruments.
   Tickers auto-suffixed .NS (NSE) or .BO (BSE) as needed.
2. LiquidityFilter  -- rolling 20-day SMA of (Volume x Close INR).
   Threshold: Rs.10 Crore (Rs.100,000,000). Below -> UNTRADABLE_ILLIQUID.
3. Parquet cache for offline/repeated runs.
4. Index watchlists: nifty50, nifty100, sensex30, banknifty, midcap50.
"""
from __future__ import annotations
import time
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
warnings.filterwarnings("ignore", category=FutureWarning)
try:
    import yfinance as yf
except ImportError as e:
    raise ImportError("yfinance is required: pip install yfinance") from e
from utils.logger import logger
from utils.constants import (
    LIQUIDITY_WINDOW_DAYS, MIN_DAILY_TURNOVER_INR,
    BACKTEST_YEARS, DAILY_INTERVAL, HOURLY_INTERVAL,
    MAX_RETRIES, RETRY_BACKOFF_S,
    NSE_SUFFIX, BSE_SUFFIX, DEFAULT_EXCHANGE, CURRENCY_SYMBOL,
)
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)
OHLCV_COLS = ["Open", "High", "Low", "Close", "Volume"]


def normalise_ticker(ticker: str, exchange: str = DEFAULT_EXCHANGE) -> str:
    t = ticker.strip().upper()
    if t.startswith("^"):
        return t
    if t.endswith(NSE_SUFFIX) or t.endswith(BSE_SUFFIX):
        return t
    return t + (NSE_SUFFIX if exchange.upper() == "NSE" else BSE_SUFFIX)


def display_ticker(ticker: str) -> str:
    return ticker.replace(NSE_SUFFIX, "").replace(BSE_SUFFIX, "")


def _retry_fetch(fn, *args, **kwargs) -> Optional[pd.DataFrame]:
    delay = RETRY_BACKOFF_S
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = fn(*args, **kwargs)
            if result is not None and not (isinstance(result, pd.DataFrame) and result.empty):
                return result
            logger.warning(f"Empty result on attempt {attempt}/{MAX_RETRIES}")
        except Exception as exc:
            logger.warning(f"Attempt {attempt}/{MAX_RETRIES} failed: {exc}")
        if attempt < MAX_RETRIES:
            time.sleep(min(delay * 2 ** (attempt - 1), 60.0))
    logger.error(f"All {MAX_RETRIES} fetch attempts exhausted.")
    return None


def _cache_path(ticker: str, interval: str) -> Path:
    safe = ticker.replace(".", "_").replace("^", "IDX_")
    return CACHE_DIR / f"{safe}_{interval}.parquet"


def _normalise_columns(df: pd.DataFrame, ticker: str = "") -> pd.DataFrame:
    if df is None or df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    df = df.copy()
    df.columns = [str(c).strip().title() for c in df.columns]
    if "Adj Close" in df.columns:
        df.rename(columns={"Adj Close": "AdjClose"}, inplace=True)
    missing = [c for c in OHLCV_COLS if c not in df.columns]
    if missing:
        logger.warning(f"[{ticker}] Missing columns after normalise: {missing}")
    return df


class LiquidityFilter:
    """
    Rolling 20-day SMA of (Volume x Close INR).
    Threshold: Rs.10 Crore. Below -> UNTRADABLE_ILLIQUID.
    """
    def __init__(self, window: int = LIQUIDITY_WINDOW_DAYS, min_turnover: float = MIN_DAILY_TURNOVER_INR):
        self.window = window
        self.min_turnover = min_turnover

    def evaluate(self, ticker: str, daily_df: pd.DataFrame) -> Tuple[bool, float]:
        if daily_df is None or daily_df.empty:
            logger.warning(f"[{ticker}] No data for liquidity check.")
            return False, 0.0
        required = {"Close", "Volume"}
        if not required.issubset(daily_df.columns):
            logger.error(f"[{ticker}] Missing {required - set(daily_df.columns)}")
            return False, 0.0
        df = daily_df[["Close", "Volume"]].copy()
        df["Close"]  = pd.to_numeric(df["Close"],  errors="coerce")
        df["Volume"] = pd.to_numeric(df["Volume"], errors="coerce")
        df.dropna(inplace=True)
        if df.empty:
            return False, 0.0
        df["Turnover"]    = df["Close"] * df["Volume"]
        df["AvgTurnover"] = df["Turnover"].rolling(window=self.window, min_periods=max(1, self.window//2)).mean()
        valid      = df["AvgTurnover"].dropna()
        latest_avg = float(valid.iloc[-1]) if not valid.empty else 0.0
        is_tradable = latest_avg >= self.min_turnover
        crore = latest_avg / 1e7
        thr_c = self.min_turnover / 1e7
        if is_tradable:
            logger.info(f"[{display_ticker(ticker)}] LIQUID        avg {self.window}-day turnover = {CURRENCY_SYMBOL}{crore:.2f} Cr")
        else:
            logger.warning(f"[{display_ticker(ticker)}] UNTRADABLE_ILLIQUID  turnover {CURRENCY_SYMBOL}{crore:.2f} Cr < threshold {CURRENCY_SYMBOL}{thr_c:.0f} Cr")
        return is_tradable, latest_avg


class DataEngine:
    def __init__(self, exchange: str = DEFAULT_EXCHANGE, use_cache: bool = True):
        self.exchange         = exchange
        self.use_cache        = use_cache
        self.liquidity_filter = LiquidityFilter()
        self._status_registry: Dict[str, str] = {}

    def _resolve(self, ticker: str) -> str:
        return normalise_ticker(ticker, self.exchange)

    def _load_cache(self, ticker: str, interval: str) -> Optional[pd.DataFrame]:
        path = _cache_path(ticker, interval)
        if self.use_cache and path.exists():
            try:
                return pd.read_parquet(path)
            except Exception as exc:
                logger.warning(f"[{ticker}] Cache read failed: {exc}")
        return None

    def _save_cache(self, df: pd.DataFrame, ticker: str, interval: str):
        try:
            df.to_parquet(_cache_path(ticker, interval))
        except Exception as exc:
            logger.warning(f"[{ticker}] Cache write failed: {exc}")

    def _fetch_yfinance(self, ticker: str, period: str, interval: str) -> Optional[pd.DataFrame]:
        def _dl():
            return yf.download(ticker, period=period, interval=interval,
                               auto_adjust=True, progress=False, threads=False)
        raw = _retry_fetch(_dl)
        if raw is None or raw.empty:
            logger.error(f"[{display_ticker(ticker)}] Failed to fetch {interval} data.")
            return None
        df = _normalise_columns(raw, ticker=ticker)
        df.index = pd.to_datetime(df.index)
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)
        df.sort_index(inplace=True)
        df.dropna(subset=["Close", "Volume"], inplace=True)
        return df

    def get_daily(self, ticker: str, years: int = BACKTEST_YEARS, force_refresh: bool = False) -> Optional[pd.DataFrame]:
        yf_ticker = self._resolve(ticker)
        if not force_refresh:
            cached = self._load_cache(yf_ticker, DAILY_INTERVAL)
            if cached is not None:
                return cached
        logger.info(f"[{display_ticker(yf_ticker)}] Fetching daily data ({years}y) ...")
        df = self._fetch_yfinance(yf_ticker, period=f"{years}y", interval=DAILY_INTERVAL)
        if df is not None:
            self._save_cache(df, yf_ticker, DAILY_INTERVAL)
        return df

    def get_hourly(self, ticker: str, force_refresh: bool = False) -> Optional[pd.DataFrame]:
        yf_ticker = self._resolve(ticker)
        if not force_refresh:
            cached = self._load_cache(yf_ticker, HOURLY_INTERVAL)
            if cached is not None:
                return cached
        logger.info(f"[{display_ticker(yf_ticker)}] Fetching hourly data (60d) ...")
        df = self._fetch_yfinance(yf_ticker, period="60d", interval=HOURLY_INTERVAL)
        if df is not None:
            self._save_cache(df, yf_ticker, HOURLY_INTERVAL)
        return df

    def is_liquid(self, ticker: str, daily_df: Optional[pd.DataFrame] = None) -> bool:
        if daily_df is None:
            daily_df = self.get_daily(ticker)
        yf_ticker   = self._resolve(ticker)
        tradable, _ = self.liquidity_filter.evaluate(yf_ticker, daily_df)
        self._status_registry[yf_ticker] = "LIQUID" if tradable else "UNTRADABLE_ILLIQUID"
        return tradable

    def scan_tickers(self, tickers: List[str]) -> Tuple[List[str], List[str]]:
        liquid, illiquid = [], []
        for raw in tickers:
            yf_t  = self._resolve(raw)
            daily = self.get_daily(raw)
            if daily is None:
                logger.error(f"[{display_ticker(yf_t)}] No data -- skipping.")
                illiquid.append(yf_t)
                continue
            (liquid if self.is_liquid(raw, daily) else illiquid).append(yf_t)
        logger.info(f"Scan: {len(liquid)} liquid / {len(illiquid)} illiquid of {len(tickers)} tickers.")
        return liquid, illiquid

    def status_report(self) -> pd.DataFrame:
        if not self._status_registry:
            return pd.DataFrame(columns=["Ticker", "Display", "Status"])
        rows = [(t, display_ticker(t), s) for t, s in self._status_registry.items()]
        return pd.DataFrame(rows, columns=["Ticker", "Display", "Status"])

    def get_nifty50_tickers(self) -> List[str]:
        hardcoded = [
            "RELIANCE.NS","TCS.NS","HDFCBANK.NS","ICICIBANK.NS","INFY.NS",
            "HINDUNILVR.NS","ITC.NS","SBIN.NS","BHARTIARTL.NS","KOTAKBANK.NS",
            "LT.NS","AXISBANK.NS","ASIANPAINT.NS","MARUTI.NS","TITAN.NS",
            "WIPRO.NS","ULTRACEMCO.NS","BAJFINANCE.NS","HCLTECH.NS","SUNPHARMA.NS",
            "TATAMOTORS.NS","POWERGRID.NS","NTPC.NS","TECHM.NS","NESTLEIND.NS",
            "ONGC.NS","JSWSTEEL.NS","TATASTEEL.NS","ADANIENT.NS","ADANIPORTS.NS",
            "COALINDIA.NS","DIVISLAB.NS","DRREDDY.NS","CIPLA.NS","EICHERMOT.NS",
            "BAJAJFINSV.NS","BAJAJ-AUTO.NS","HEROMOTOCO.NS","BPCL.NS","BRITANNIA.NS",
            "GRASIM.NS","HINDALCO.NS","APOLLOHOSP.NS","TATACONSUM.NS","SBILIFE.NS",
            "HDFCLIFE.NS","M&M.NS","INDUSINDBK.NS","SHRIRAMFIN.NS","BEL.NS",
        ]
        try:
            tables = pd.read_html("https://en.wikipedia.org/wiki/NIFTY_50", header=0)
            for tbl in tables:
                sym_col = next((c for c in tbl.columns if "symbol" in c.lower()), None)
                if sym_col and len(tbl) >= 40:
                    tickers = tbl[sym_col].str.strip().str.upper().apply(
                        lambda x: x if x.endswith(".NS") else x + ".NS").tolist()
                    logger.info(f"Loaded {len(tickers)} Nifty 50 tickers from Wikipedia.")
                    return tickers
        except Exception as exc:
            logger.warning(f"Wikipedia Nifty50 scrape failed ({exc}). Using hardcoded list.")
        return hardcoded

    def get_sensex30_tickers(self) -> List[str]:
        return [
            "RELIANCE.BO","TCS.BO","HDFCBANK.BO","ICICIBANK.BO","INFY.BO",
            "HINDUNILVR.BO","ITC.BO","SBIN.BO","BHARTIARTL.BO","KOTAKBANK.BO",
            "LT.BO","AXISBANK.BO","ASIANPAINT.BO","MARUTI.BO","TITAN.BO",
            "WIPRO.BO","BAJFINANCE.BO","HCLTECH.BO","SUNPHARMA.BO","NTPC.BO",
            "POWERGRID.BO","TATAMOTORS.BO","JSWSTEEL.BO","TATASTEEL.BO","ONGC.BO",
            "BAJAJFINSV.BO","BAJAJ-AUTO.BO","M&M.BO","INDUSINDBK.BO","NESTLEIND.BO",
        ]

    def get_banknifty_tickers(self) -> List[str]:
        return [
            "HDFCBANK.NS","ICICIBANK.NS","KOTAKBANK.NS","AXISBANK.NS",
            "SBIN.NS","INDUSINDBK.NS","BANDHANBNK.NS","IDFCFIRSTB.NS",
            "FEDERALBNK.NS","AUBANK.NS","PNB.NS","BANKBARODA.NS",
        ]

    def get_nifty_midcap50_tickers(self) -> List[str]:
        return [
            "ABCAPITAL.NS","APLAPOLLO.NS","ASTRAL.NS","BIKAJI.NS","BLUESTARCO.NS",
            "CAMS.NS","CANFINHOME.NS","CDSL.NS","CHOLAFIN.NS","CRISIL.NS",
            "DELHIVERY.NS","FINCABLES.NS","GLENMARK.NS","GODREJPROP.NS","GSPL.NS",
            "HFCL.NS","IDFC.NS","IIFL.NS","INDIANB.NS","INDIAMART.NS",
            "INTELLECT.NS","IRFC.NS","JKPAPER.NS","JUBILANT.NS","KPITTECH.NS",
            "LALPATHLAB.NS","LAURUSLABS.NS","LICHSGFIN.NS","LTIM.NS","LTTS.NS",
            "MARICO.NS","MFSL.NS","MPHASIS.NS","NAM-INDIA.NS","NAUKRI.NS",
            "PAGEIND.NS","PERSISTENT.NS","PGHH.NS","RADICO.NS","RAJESHEXPO.NS",
            "RBLBANK.NS","RITES.NS","ROUTE.NS","SBICARD.NS","SOLARINDS.NS",
            "SUPREMEIND.NS","TATACOMM.NS","TORNTPHARM.NS","TRENT.NS","ZYDUSLIFE.NS",
        ]

    def get_watchlist(self, name: str) -> List[str]:
        name = name.lower().strip()
        if name in ("nifty50", "nifty_50"):
            return self.get_nifty50_tickers()
        if name in ("sensex30", "sensex"):
            return self.get_sensex30_tickers()
        if name in ("banknifty", "bank_nifty"):
            return self.get_banknifty_tickers()
        if name in ("midcap50", "nifty_midcap50"):
            return self.get_nifty_midcap50_tickers()
        if name == "nifty100":
            seen, combined = set(), []
            for t in self.get_nifty50_tickers() + self.get_nifty_midcap50_tickers():
                if t not in seen:
                    seen.add(t); combined.append(t)
            return combined
        raise ValueError(f"Unknown watchlist '{name}'. Valid: nifty50, nifty100, sensex30, banknifty, midcap50")
# Alias for compatibility
resolve_ticker = normalise_ticker
