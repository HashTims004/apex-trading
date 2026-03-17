# backtesting/evaluator.py
"""
APEX Backtest Evaluator — India Edition v2.3.0
================================================
Metrics
-------
  Total Return (%)    Max Drawdown (%)    Win Rate (%)
  Profit Factor       CAGR (%)            Avg Hold (days)
  Best Trade (%)      Worst Trade (%)     Win Streak / Loss Streak
  Sharpe* (rf=7%)     Calmar Ratio        Benchmark Alpha vs NIFTY

* Sharpe computed from per-trade returns (annualised), not daily bar returns.
  Daily-bar Sharpe is near-useless for low-frequency strategies.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

try:
    import backtrader as bt
    import backtrader.analyzers as bta
except ImportError as e:
    raise ImportError("backtrader required: pip install backtrader") from e

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
except ImportError as e:
    raise ImportError("rich required: pip install rich") from e

from strategies.apex_confluence import ApexConfluenceStrategy
from utils.constants import (
    DEFAULT_INITIAL_CAPITAL, COMMISSION_PCT,
    MIN_PROFIT_FACTOR, MIN_WIN_RATE_PCT, BACKTEST_YEARS,
    CURRENCY_SYMBOL, CRORE,
)
from utils.logger import logger

console = Console()
C = CURRENCY_SYMBOL
RISK_FREE_RATE = 0.07        # India 10Y G-Sec ≈ 7%
TRADING_DAYS   = 252


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    ticker:              str
    # Core metrics
    total_return_pct:    float = 0.0
    cagr_pct:            float = 0.0
    max_drawdown_pct:    float = 0.0
    win_rate_pct:        float = 0.0
    profit_factor:       float = 0.0
    sharpe_ratio:        float = 0.0
    calmar_ratio:        float = 0.0
    # Trade detail
    total_trades:        int   = 0
    avg_hold_days:       float = 0.0
    best_trade_pct:      float = 0.0
    worst_trade_pct:     float = 0.0
    max_win_streak:      int   = 0
    max_loss_streak:     int   = 0
    gross_profit:        float = 0.0
    gross_loss:          float = 0.0
    # Capital
    initial_equity:      float = DEFAULT_INITIAL_CAPITAL
    final_equity:        float = 0.0
    # Benchmark
    benchmark_return_pct: float = 0.0   # NIFTY/buy-and-hold
    alpha_pct:            float = 0.0   # strategy - benchmark
    # Validity
    is_valid:             bool  = False
    validation_messages:  List[str] = field(default_factory=list)

    # Composite score (0-100) for ranking
    @property
    def score(self) -> float:
        if self.total_trades == 0:
            return 0.0
        s  = max(0, min(self.total_return_pct, 100)) * 0.25
        s += max(0, min(self.win_rate_pct, 100))     * 0.25
        s += max(0, min(self.profit_factor * 20, 100))* 0.20
        s += max(0, min(self.cagr_pct, 100))         * 0.15
        s += max(0, 100 - self.max_drawdown_pct * 3) * 0.15
        return round(s, 1)

    def to_dict(self) -> Dict:
        return {
            "Ticker":          self.ticker,
            "Return (%)":      f"{self.total_return_pct:+.2f}",
            "CAGR (%)":        f"{self.cagr_pct:+.2f}",
            "Max DD (%)":      f"{self.max_drawdown_pct:.2f}",
            "Win Rate (%)":    f"{self.win_rate_pct:.1f}",
            "Profit Factor":   f"{self.profit_factor:.3f}" if self.profit_factor != float("inf") else "∞",
            "Sharpe":          f"{self.sharpe_ratio:.3f}",
            "Calmar":          f"{self.calmar_ratio:.3f}",
            "Avg Hold (d)":    f"{self.avg_hold_days:.0f}",
            "Best Tr (%)":     f"{self.best_trade_pct:+.1f}",
            "Worst Tr (%)":    f"{self.worst_trade_pct:+.1f}",
            "Trades":          self.total_trades,
            "Score":           self.score,
            "Valid":           "YES" if self.is_valid else "NO",
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _trade_sharpe(trade_returns_pct: List[float], avg_hold_days: float) -> float:
    """
    Annualised Sharpe from per-trade return series.
    Much more meaningful than daily-bar Sharpe for low-frequency strategies.

    Annualisation factor = sqrt(TRADING_DAYS / avg_hold_days)
    so a 50-day avg hold annualises over ~5 trades/year.
    """
    if len(trade_returns_pct) < 2 or avg_hold_days <= 0:
        return 0.0
    arr = np.array(trade_returns_pct) / 100.0
    # Per-trade risk-free: rf_annual / (TRADING_DAYS / avg_hold)
    trades_per_year = TRADING_DAYS / avg_hold_days
    rf_per_trade    = RISK_FREE_RATE / trades_per_year
    excess          = arr - rf_per_trade
    std             = np.std(excess, ddof=1)
    if std == 0:
        return 0.0
    sharpe_per_trade = np.mean(excess) / std
    # Annualise
    return round(float(sharpe_per_trade * math.sqrt(trades_per_year)), 3)


def _cagr(total_return_pct: float, years: float) -> float:
    if years <= 0:
        return 0.0
    r = total_return_pct / 100.0
    return round(((1 + r) ** (1 / years) - 1) * 100, 2)


def _benchmark_return(daily_df: pd.DataFrame, years: int) -> float:
    """Simple buy-and-hold return over the same period."""
    cutoff = daily_df.index.max() - pd.DateOffset(years=years)
    sub    = daily_df[daily_df.index >= cutoff]["Close"].dropna()
    if len(sub) < 2:
        return 0.0
    return round((sub.iloc[-1] / sub.iloc[0] - 1) * 100, 2)


def _extract_trade_details(trade_an) -> dict:
    """Pull per-trade detail from Backtrader's TradeAnalyzer."""
    ta = trade_an
    try:
        total_closed = ta.total.closed
        avg_hold     = float(ta.len.average) if total_closed > 0 else 0.0
        won          = ta.won.total
        lost         = ta.lost.total

        # Best / worst individual trade as % return
        best_gross   = float(ta.won.pnl.max)   if won  > 0 else 0.0
        worst_gross  = float(ta.lost.pnl.max)  if lost > 0 else 0.0   # bt stores as negative
        max_win_str  = int(ta.streak.won.longest)
        max_los_str  = int(ta.streak.lost.longest)

        # Per-trade return % list — approximate using avg pnl vs avg gross
        # (BT doesn't expose entry price per trade in the analyzer directly)
        trade_returns: List[float] = []
        if won > 0:
            avg_win_pnl = float(ta.won.pnl.average)
            trade_returns += [avg_win_pnl] * won
        if lost > 0:
            avg_los_pnl = float(ta.lost.pnl.average)   # negative
            trade_returns += [avg_los_pnl] * lost

        return {
            "total_closed":   total_closed,
            "avg_hold_days":  avg_hold,
            "best_pnl":       best_gross,
            "worst_pnl":      worst_gross,
            "max_win_streak": max_win_str,
            "max_los_streak": max_los_str,
            "trade_returns":  trade_returns,
        }
    except Exception:
        return {
            "total_closed": 0, "avg_hold_days": 0,
            "best_pnl": 0, "worst_pnl": 0,
            "max_win_streak": 0, "max_los_streak": 0,
            "trade_returns": [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Evaluator
# ─────────────────────────────────────────────────────────────────────────────

class BacktestEvaluator:
    def __init__(
        self,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
        commission:      float = COMMISSION_PCT,
    ) -> None:
        self.initial_capital = initial_capital
        self.commission      = commission

    def _prepare_feed(self, df: pd.DataFrame) -> pd.DataFrame:
        rmap = {}
        for c in df.columns:
            k = c.strip().lower()
            if k == "open":   rmap[c] = "Open"
            elif k == "high": rmap[c] = "High"
            elif k == "low":  rmap[c] = "Low"
            elif k == "close":rmap[c] = "Close"
            elif k in ("volume", "vol"): rmap[c] = "Volume"
        return df.rename(columns=rmap)

    def _build_cerebro(self, df: pd.DataFrame) -> bt.Cerebro:
        cerebro = bt.Cerebro(stdstats=False)
        feed    = bt.feeds.PandasData(
            dataname=self._prepare_feed(df),
            open="Open", high="High", low="Low",
            close="Close", volume="Volume", openinterest=-1,
        )
        cerebro.adddata(feed)
        cerebro.broker.setcash(self.initial_capital)
        cerebro.broker.setcommission(commission=self.commission)
        cerebro.addstrategy(ApexConfluenceStrategy, printlog=False)
        cerebro.addanalyzer(bta.DrawDown,      _name="drawdown")
        cerebro.addanalyzer(bta.Returns,       _name="returns")
        cerebro.addanalyzer(bta.TradeAnalyzer, _name="trades")
        return cerebro

    def _validate(self, result: BacktestResult) -> BacktestResult:
        msgs, ok = [], True
        if result.profit_factor < MIN_PROFIT_FACTOR:
            ok = False
            msgs.append(f"Profit Factor {result.profit_factor:.3f} < required {MIN_PROFIT_FACTOR}")
        if result.win_rate_pct < MIN_WIN_RATE_PCT:
            ok = False
            msgs.append(f"Win Rate {result.win_rate_pct:.1f}% < required {MIN_WIN_RATE_PCT:.0f}%")
        result.is_valid            = ok
        result.validation_messages = msgs
        return result

    # ── Public run API ────────────────────────────────────────────────────

    def run(
        self,
        ticker:    str,
        daily_df:  pd.DataFrame,
        years:     int = BACKTEST_YEARS,
        benchmark_df: Optional[pd.DataFrame] = None,
    ) -> BacktestResult:
        result = BacktestResult(ticker=ticker, initial_equity=self.initial_capital)

        if daily_df is None or daily_df.empty:
            logger.error(f"[{ticker}] No data supplied.")
            return result

        cutoff = daily_df.index.max() - pd.DateOffset(years=years)
        df     = daily_df[daily_df.index >= cutoff].copy()
        if len(df) < 250:
            logger.warning(f"[{ticker}] Only {len(df)} bars — need >= 250.")
            return result

        actual_years = (df.index[-1] - df.index[0]).days / 365.25
        logger.info(f"[{ticker}] Backtesting {len(df)} bars "
                    f"({df.index[0].date()} → {df.index[-1].date()}) ...")

        try:
            cerebro   = self._build_cerebro(df)
            strategies = cerebro.run()
            strat      = strategies[0]
        except Exception as exc:
            logger.error(f"[{ticker}] Cerebro run failed: {exc}")
            return result

        # ── Core equity metrics ───────────────────────────────────────────
        final_equity  = cerebro.broker.getvalue()
        total_return  = (final_equity - self.initial_capital) / self.initial_capital * 100

        dd_info   = strat.analyzers.drawdown.get_analysis()
        max_dd    = dd_info.get("max", {}).get("drawdown", 0.0) or 0.0
        calmar    = (total_return / max_dd) if max_dd > 0 else 0.0

        # ── Strategy stats from get_stats() ──────────────────────────────
        stats = strat.get_stats()

        # ── Rich trade analytics ──────────────────────────────────────────
        td    = _extract_trade_details(strat.analyzers.trades.get_analysis())
        avg_hold = td["avg_hold_days"]

        # Trade-based Sharpe — only meaningful with >= 3 trades
        trade_returns_approx = td["trade_returns"]
        sharpe = _trade_sharpe(trade_returns_approx, avg_hold) if stats["total_trades"] >= 3 else 0.0

        # Best/worst trade as % of entry (approximate: pnl / initial_capital * 100)
        best_pct  = td["best_pnl"]  / self.initial_capital * 100
        worst_pct = td["worst_pnl"] / self.initial_capital * 100   # already negative

        # ── Benchmark ─────────────────────────────────────────────────────
        bm_df  = benchmark_df if benchmark_df is not None else df
        bm_ret = _benchmark_return(bm_df, years)
        alpha  = round(total_return - bm_ret, 2)

        # ── Populate result ───────────────────────────────────────────────
        result.total_return_pct   = round(total_return, 2)
        result.cagr_pct           = _cagr(total_return, actual_years)
        result.max_drawdown_pct   = round(max_dd, 2)
        result.win_rate_pct       = stats["win_rate_pct"]
        result.profit_factor      = stats["profit_factor"]
        result.sharpe_ratio       = sharpe
        result.calmar_ratio       = round(calmar, 3)
        result.total_trades       = stats["total_trades"]
        result.avg_hold_days      = round(avg_hold, 1)
        result.best_trade_pct     = round(best_pct, 2)
        result.worst_trade_pct    = round(worst_pct, 2)
        result.max_win_streak     = td["max_win_streak"]
        result.max_loss_streak    = td["max_los_streak"]
        result.gross_profit       = stats["gross_profit"]
        result.gross_loss         = stats["gross_loss"]
        result.final_equity       = final_equity
        result.benchmark_return_pct = bm_ret
        result.alpha_pct          = alpha
        result = self._validate(result)

        logger.info(
            f"[{ticker}] return={total_return:+.2f}%  cagr={result.cagr_pct:+.2f}%  "
            f"dd={max_dd:.2f}%  wr={stats['win_rate_pct']:.1f}%  "
            f"pf={stats['profit_factor']:.3f}  sharpe={sharpe:.3f}  "
            f"trades={stats['total_trades']}  hold={avg_hold:.0f}d  "
            f"alpha={alpha:+.2f}%  valid={'YES' if result.is_valid else 'NO'}"
        )
        return result

    def run_batch(
        self,
        tickers:    List[str],
        daily_data: Dict[str, pd.DataFrame],
        years:      int = BACKTEST_YEARS,
    ) -> List[BacktestResult]:
        results = []
        for t in tickers:
            r = self.run(t, daily_data.get(t), years=years)
            results.append(r)
            self.print_dashboard(r)
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    # ── Terminal dashboard ────────────────────────────────────────────────

    def print_dashboard(self, result: BacktestResult) -> None:
        valid_tag = "[bold green]VALID ✓[/]" if result.is_valid else "[bold red on dark_red] INVALID ✗ [/]"
        header    = (
            f"[bold color(208)]APEX BACKTEST  [NSE/BSE][/]  "
            f"[bold white]{result.ticker}[/]  [dim]|[/]  {valid_tag}  "
            f"[dim]|  Score: {result.score:.0f}/100[/]"
        )

        tbl = Table(box=box.ROUNDED, header_style="bold magenta", padding=(0, 2))
        tbl.add_column("Metric",    style="bold white", min_width=24)
        tbl.add_column("Value",     justify="right",    min_width=16)
        tbl.add_column("Threshold", justify="right",    style="dim", min_width=16)
        tbl.add_column("Pass",      justify="center",   min_width=6)

        def tick(v, t, hi=True):
            return "[green]✓[/]" if (v >= t if hi else v <= t) else "[red]✗[/]"

        rc = "green" if result.total_return_pct >= 0 else "red"
        ac = "green" if result.alpha_pct >= 0 else "red"

        tbl.add_row("Total Return",       f"[{rc}]{result.total_return_pct:+.2f}%[/]",          "—", "—")
        tbl.add_row("CAGR",               f"[{rc}]{result.cagr_pct:+.2f}%[/]",                  "—", "—")
        tbl.add_row("Benchmark (B&H)",    f"{result.benchmark_return_pct:+.2f}%",                "—", "—")
        tbl.add_row("Alpha vs Benchmark", f"[{ac}]{result.alpha_pct:+.2f}%[/]",                  "> 0%", tick(result.alpha_pct, 0))
        tbl.add_section()

        dc = "green" if result.max_drawdown_pct < 20 else ("yellow" if result.max_drawdown_pct < 35 else "red")
        tbl.add_row("Max Drawdown",       f"[{dc}]{result.max_drawdown_pct:.2f}%[/]",            "< 20%", tick(result.max_drawdown_pct, 20, False))

        wc = "green" if result.win_rate_pct >= MIN_WIN_RATE_PCT else "red"
        tbl.add_row("Win Rate",           f"[{wc}]{result.win_rate_pct:.1f}%[/]",                f">= {MIN_WIN_RATE_PCT:.0f}%", tick(result.win_rate_pct, MIN_WIN_RATE_PCT))

        pf_s = f"{result.profit_factor:.3f}" if result.profit_factor != float("inf") else "∞"
        pc   = "green" if result.profit_factor >= MIN_PROFIT_FACTOR else "red"
        tbl.add_row("Profit Factor",      f"[{pc}]{pf_s}[/]",                                    f">= {MIN_PROFIT_FACTOR}", tick(result.profit_factor, MIN_PROFIT_FACTOR))

        sc = "green" if result.sharpe_ratio >= 1.0 else ("yellow" if result.sharpe_ratio >= 0 else "red")
        tbl.add_row("Sharpe (trade-based, rf=7%)", f"[{sc}]{result.sharpe_ratio:.3f}[/]",        ">= 1.0 ideal", tick(result.sharpe_ratio, 1.0))

        cc = "green" if result.calmar_ratio >= 1.0 else "yellow"
        tbl.add_row("Calmar Ratio",       f"[{cc}]{result.calmar_ratio:.3f}[/]",                  ">= 1.0 ideal", tick(result.calmar_ratio, 1.0))
        tbl.add_section()

        tbl.add_row("Total Trades",       str(result.total_trades),                              "—", "—")
        tbl.add_row("Avg Hold (days)",    f"{result.avg_hold_days:.0f}",                          "—", "—")
        tbl.add_row("Best Trade",         f"[green]{result.best_trade_pct:+.2f}%[/]",            "—", "—")
        tbl.add_row("Worst Trade",        f"[red]{result.worst_trade_pct:+.2f}%[/]",             "—", "—")
        tbl.add_row("Win Streak",         f"[green]{result.max_win_streak}[/]",                   "—", "—")
        tbl.add_row("Loss Streak",        f"[red]{result.max_loss_streak}[/]",                    "—", "—")
        tbl.add_section()

        tbl.add_row("Initial Equity",     f"{C}{result.initial_equity:,.2f}",                    "—", "—")
        tbl.add_row("Final Equity",       f"[{rc}]{C}{result.final_equity:,.2f}[/]",             "—", "—")
        tbl.add_row("Gross Profit",       f"[green]{C}{result.gross_profit:,.2f}[/]",            "—", "—")
        tbl.add_row("Gross Loss",         f"[red]{C}{result.gross_loss:,.2f}[/]",                "—", "—")
        tbl.add_row("[dim]Broker[/]",     "[dim][green]Groww  ₹0 brokerage[/green][/]",          "[dim]STT+Exch+GST+Stamp+DP[/]", "—")

        console.print(Panel(tbl, title=header, border_style="color(208)", padding=(1, 2)))

        if not result.is_valid:
            warn = Text(justify="center")
            warn.append(f"\n  ⚠   STRATEGY INVALID FOR {result.ticker} — DO NOT DEPLOY   ⚠\n",
                        style="bold white on red")
            for msg in result.validation_messages:
                warn.append(f"\n  Reason: {msg}\n", style="red")
            warn.append("\n")
            console.print(Panel(warn, border_style="red", padding=(0, 4)))

    def print_batch_summary(self, results: List[BacktestResult]) -> None:
        # ── Table 1: Return / Risk / Score ───────────────────────────────
        t1 = Table(
            title="[bold color(208)]APEX  ·  NSE/BSE Batch Summary  (ranked by Score)[/]",
            box=box.HEAVY_EDGE, header_style="bold magenta", min_width=90,
        )
        for col, just in [("#","left"),("Ticker","left"),("Return","right"),
                          ("CAGR","right"),("Max DD","right"),("Win%","right"),
                          ("PF","right"),("Sharpe","right"),("Calmar","right"),
                          ("Score","right"),("✓","center")]:
            t1.add_column(col, justify=just)

        valid_ct = 0
        for i, r in enumerate(results, 1):
            d   = r.to_dict()
            sty = "green" if r.is_valid else "red"
            rc  = "green" if r.total_return_pct >= 0 else "red"
            pf  = d["Profit Factor"]
            t1.add_row(
                str(i), d["Ticker"],
                f"[{rc}]{d['Return (%)']}%[/]",
                f"[{rc}]{d['CAGR (%)']}%[/]",
                f"{d['Max DD (%)']}%",
                f"{d['Win Rate (%)']}%",
                pf, d["Sharpe"], d["Calmar"],
                f"[bold]{d['Score']}[/]",
                "[green]✓[/]" if r.is_valid else "[red]✗[/]",
                style=sty,
            )
            if r.is_valid:
                valid_ct += 1

        console.print(t1)

        # ── Table 2: Trade detail ─────────────────────────────────────────
        t2 = Table(box=box.SIMPLE, header_style="bold dim", min_width=90)
        for col, just in [("Ticker","left"),("Trades","right"),("Hold(d)","right"),
                          ("Best Trade","right"),("Worst Trade","right"),
                          ("Win Str","right"),("Los Str","right"),("Alpha","right"),
                          ("Benchmark","right")]:
            t2.add_column(col, justify=just)

        for r in results:
            ac = "green" if r.alpha_pct >= 0 else "red"
            t2.add_row(
                r.ticker,
                str(r.total_trades),
                f"{r.avg_hold_days:.0f}",
                f"[green]{r.best_trade_pct:+.2f}%[/]",
                f"[red]{r.worst_trade_pct:+.2f}%[/]",
                f"[green]{r.max_win_streak}[/]",
                f"[red]{r.max_loss_streak}[/]",
                f"[{ac}]{r.alpha_pct:+.1f}%[/]",
                f"{r.benchmark_return_pct:+.1f}%",
            )

        console.print(t2)
        console.print(
            f"  Validated: [bold green]{valid_ct}[/] / {len(results)} tickers  "
            f"[dim]|  Score = return 25% + win_rate 25% + PF 20% + CAGR 15% + low_DD 15%[/]\n"
        )
