# execution/paper_trader.py
"""
APEX Paper Trading Engine — India Edition (Groww)
==================================================
Simulates live NSE/BSE signal generation. All values in ₹ INR.
Zero-brokerage delivery trades as per Groww's charge structure.

Groww Delivery Charges applied per trade
-----------------------------------------
  Brokerage       : ₹0 (free)
  STT             : 0.1% on buy+sell turnover
  NSE Exch Txn    : 0.00297% per leg
  SEBI Fee        : ₹10/Cr per leg
  Stamp Duty      : 0.015% on buy value
  GST             : 18% on exchange charges
  DP Charges      : ₹13.5 + 18% GST = ₹15.93 flat per scrip per sell day
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import pandas as pd
import numpy as np

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
except ImportError as e:
    raise ImportError("rich required: pip install rich") from e

from data.data_engine import DataEngine, normalise_ticker as resolve_ticker
from utils.constants import (
    EMA_FAST, EMA_SLOW, RSI_PERIOD, RSI_LOW, RSI_HIGH,
    ATR_PERIOD, ATR_MULTIPLIER, MAX_RISK_PER_TRADE_PCT,
    DEFAULT_INITIAL_CAPITAL, DEFAULT_EXCHANGE,
    CURRENCY_SYMBOL, CRORE,
    MARKET_OPEN_IST, MARKET_CLOSE_IST,
    IST_OFFSET_HOURS, IST_OFFSET_MINUTES,
    STT_DELIVERY_PCT, EXCHANGE_TXN_CHARGES,
    STAMP_DUTY_PCT, GST_RATE, SEBI_CHARGES,
    DP_CHARGE_WITH_GST,
)
from utils.logger import logger

console = Console()
IST = timezone(timedelta(hours=IST_OFFSET_HOURS, minutes=IST_OFFSET_MINUTES))
C   = CURRENCY_SYMBOL


def _ist_now() -> datetime:
    return datetime.now(IST)


def _market_is_open() -> bool:
    now = _ist_now()
    if now.weekday() >= 5:
        return False
    oh, om = map(int, MARKET_OPEN_IST.split(":"))
    ch, cm = map(int, MARKET_CLOSE_IST.split(":"))
    return now.replace(hour=oh, minute=om, second=0, microsecond=0) <= now <= \
           now.replace(hour=ch, minute=cm, second=0, microsecond=0)


def _calc_charges(buy_value: float, sell_value: float, n_scrips_sold: int = 1) -> float:
    """
    Groww Equity Delivery charge calculator.

    Parameters
    ----------
    buy_value       : total buy-side turnover (₹)
    sell_value      : total sell-side turnover (₹)
    n_scrips_sold   : number of distinct scrips sold (for DP flat charge)

    Returns
    -------
    Total charges in ₹
    """
    stt         = (buy_value + sell_value) * STT_DELIVERY_PCT       # 0.1% both sides
    exch_buy    = buy_value  * EXCHANGE_TXN_CHARGES                  # 0.00297% buy
    exch_sell   = sell_value * EXCHANGE_TXN_CHARGES                  # 0.00297% sell
    exch_total  = exch_buy + exch_sell
    sebi        = (buy_value + sell_value) * SEBI_CHARGES            # ₹10/Cr
    stamp       = buy_value * STAMP_DUTY_PCT                         # 0.015% buy only
    gst         = exch_total * GST_RATE                              # 18% on exch only (brok=0)
    dp          = DP_CHARGE_WITH_GST * n_scrips_sold                 # ₹15.93 per scrip sold
    total       = stt + exch_total + sebi + stamp + gst + dp
    return round(total, 2)


def _charge_breakdown(buy_value: float, sell_value: float, n_scrips_sold: int = 1) -> dict:
    """Returns itemised Groww charge breakdown for display."""
    exch_total = (buy_value + sell_value) * EXCHANGE_TXN_CHARGES
    return {
        "Brokerage":        0.0,
        "STT":              round((buy_value + sell_value) * STT_DELIVERY_PCT, 4),
        "Exch Txn Charges": round(exch_total, 4),
        "SEBI Fee":         round((buy_value + sell_value) * SEBI_CHARGES, 4),
        "Stamp Duty":       round(buy_value * STAMP_DUTY_PCT, 4),
        "GST (18%)":        round(exch_total * GST_RATE, 4),
        "DP Charges":       round(DP_CHARGE_WITH_GST * n_scrips_sold, 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Data containers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PaperPosition:
    ticker:        str
    entry_price:   float
    shares:        int
    stop_price:    float
    entry_time:    datetime = field(default_factory=lambda: datetime.now(IST))
    current_price: float = 0.0
    charges_paid:  float = 0.0

    @property
    def unrealised_pnl(self)  -> float: return (self.current_price - self.entry_price) * self.shares
    @property
    def unrealised_pct(self)  -> float: return ((self.current_price / self.entry_price) - 1) * 100 if self.entry_price else 0.0
    @property
    def market_value(self)    -> float: return self.current_price * self.shares


@dataclass
class TradeRecord:
    ticker:      str
    entry_price: float
    exit_price:  float
    shares:      int
    pnl_gross:   float
    charges:     float
    reason:      str
    entry_time:  datetime
    exit_time:   datetime = field(default_factory=lambda: datetime.now(IST))

    @property
    def pnl_net(self)    -> float: return self.pnl_gross - self.charges
    @property
    def return_pct(self) -> float: return ((self.exit_price / self.entry_price) - 1) * 100 if self.entry_price else 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Signal computation
# ─────────────────────────────────────────────────────────────────────────────

def _compute_signals(df: pd.DataFrame) -> pd.DataFrame:
    try:
        import pandas_ta as ta
    except ImportError:
        raise ImportError("pandas-ta required: pip install pandas-ta")

    df = df.copy()
    df.columns = [c.title() for c in df.columns]
    df[f"EMA{EMA_FAST}"] = ta.ema(df["Close"], length=EMA_FAST)
    df[f"EMA{EMA_SLOW}"] = ta.ema(df["Close"], length=EMA_SLOW)
    df["RSI"]            = ta.rsi(df["Close"], length=RSI_PERIOD)
    df["ATR"]            = ta.atr(df["High"], df["Low"], df["Close"], length=ATR_PERIOD)
    fast, slow           = df[f"EMA{EMA_FAST}"], df[f"EMA{EMA_SLOW}"]
    df["GoldenCross"]    = (fast > slow) & (fast.shift(1) <= slow.shift(1))
    df["DeathCross"]     = (fast < slow) & (fast.shift(1) >= slow.shift(1))
    df["RSIInRange"]     = df["RSI"].between(RSI_LOW, RSI_HIGH)
    df["EntrySignal"]    = df["GoldenCross"] & df["RSIInRange"]
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Paper Trader
# ─────────────────────────────────────────────────────────────────────────────

class PaperTrader:
    def __init__(
        self,
        symbols:       List[str],
        capital:       float = DEFAULT_INITIAL_CAPITAL,
        exchange:      str   = DEFAULT_EXCHANGE,
        cycle_seconds: int   = 900,
        skip_closed:   bool  = True,
    ) -> None:
        self.exchange        = exchange.upper()
        self.tickers         = [resolve_ticker(s, self.exchange) for s in symbols]
        self.initial_capital = capital
        self.cash            = capital
        self.cycle_seconds   = cycle_seconds
        self.skip_closed     = skip_closed
        self.engine          = DataEngine(exchange=self.exchange, use_cache=False)
        self.positions:      Dict[str, PaperPosition] = {}
        self.trade_history:  List[TradeRecord] = []

    @property
    def portfolio_value(self) -> float:
        return self.cash + sum(p.market_value for p in self.positions.values())

    @property
    def total_return_pct(self) -> float:
        return (self.portfolio_value / self.initial_capital - 1) * 100

    def _evaluate_ticker(self, ticker: str) -> None:
        daily = self.engine.get_daily(ticker, years=2, force_refresh=True)
        if daily is None or daily.empty:
            logger.warning(f"[{ticker}] No data this cycle.")
            return
        if not self.engine.is_liquid(ticker, daily):
            return
        try:
            df = _compute_signals(daily)
        except Exception as exc:
            logger.error(f"[{ticker}] Signal error: {exc}")
            return

        last    = df.iloc[-1]
        price   = float(last["Close"])
        atr_val = float(last["ATR"]) if not pd.isna(last["ATR"]) else 0.0

        if ticker in self.positions:
            pos               = self.positions[ticker]
            pos.current_price = price
            if atr_val > 0:
                candidate = price - atr_val * ATR_MULTIPLIER
                if candidate > pos.stop_price:
                    pos.stop_price = candidate
            if price <= pos.stop_price:
                self._close(ticker, price, "STOP_HIT")
            elif bool(last["DeathCross"]):
                self._close(ticker, price, "DEATH_CROSS")

        elif bool(last["EntrySignal"]) and atr_val > 0:
            risk_inr  = self.portfolio_value * MAX_RISK_PER_TRADE_PCT
            stop_dist = atr_val * ATR_MULTIPLIER
            shares    = int(risk_inr / stop_dist)

            # Small-capital fallback: try 1 share if risk-math gives 0
            if shares < 1:
                if float(last["Close"]) <= self.portfolio_value * 0.50:
                    shares = 1
                else:
                    logger.info(
                        f"[{ticker}] Signal found but stock price "
                        f"(₹{float(last['Close']):,.2f}) exceeds 50% of capital — skipping."
                    )
                    return
            cost    = shares * price
            charges = _calc_charges(cost, 0, n_scrips_sold=0)   # entry only, no DP on buy
            if (cost + charges) <= self.cash:
                self._open(ticker, price, shares, stop_dist, charges)
            else:
                logger.info(
                    f"[{ticker}] Signal but insufficient cash "
                    f"(need {C}{cost + charges:,.2f}, have {C}{self.cash:,.2f})"
                )

    def _open(self, ticker: str, price: float, shares: int,
              stop_dist: float, charges: float) -> None:
        cost            = price * shares
        self.cash      -= (cost + charges)
        self.positions[ticker] = PaperPosition(
            ticker=ticker, entry_price=price, shares=shares,
            stop_price=price - stop_dist, current_price=price, charges_paid=charges,
        )
        logger.info(
            f"[GROWW BUY]  {ticker}  {shares} sh @ {C}{price:.2f}  "
            f"stop={C}{price - stop_dist:.2f}  cost={C}{cost:,.2f}  "
            f"entry charges={C}{charges:.2f}  [brokerage=₹0]"
        )

    def _close(self, ticker: str, price: float, reason: str) -> None:
        if ticker not in self.positions:
            return
        pos      = self.positions.pop(ticker)
        proceeds = price * pos.shares
        # Exit charges: include DP flat charge on sell
        exit_chg = _calc_charges(0, proceeds, n_scrips_sold=1)
        total_chg = pos.charges_paid + exit_chg
        self.cash += (proceeds - exit_chg)
        gross_pnl = (price - pos.entry_price) * pos.shares
        net_pnl   = gross_pnl - total_chg
        self.trade_history.append(TradeRecord(
            ticker=ticker, entry_price=pos.entry_price, exit_price=price,
            shares=pos.shares, pnl_gross=gross_pnl, charges=total_chg,
            reason=reason, entry_time=pos.entry_time,
        ))
        col = "green" if net_pnl >= 0 else "red"
        logger.info(
            f"[GROWW SELL] {ticker}  reason={reason}  "
            f"gross={C}{gross_pnl:+,.2f}  charges={C}{total_chg:.2f}  "
            f"(incl DP {C}{DP_CHARGE_WITH_GST:.2f})  "
            f"net=[{col}]{C}{net_pnl:+,.2f}[/{col}]"
        )

    def _render_dashboard(self) -> Table:
        rc  = "green" if self.total_return_pct >= 0 else "red"
        ist = _ist_now().strftime("%H:%M:%S IST  %d-%b-%Y")
        tbl = Table(
            title=(
                f"[bold color(208)]APEX PAPER TRADER — Groww (NSE/BSE)[/]  "
                f"equity=[bold]{C}{self.portfolio_value:,.2f}[/]  "
                f"return=[{rc}]{self.total_return_pct:+.2f}%[/{rc}]  "
                f"[dim]{ist}[/]"
            ),
            box=box.ROUNDED, header_style="bold magenta",
        )
        for col in ["Ticker", "Qty", f"Buy ({C})", f"LTP ({C})",
                    f"Stop ({C})", "Unr. P&L", "%", "Broker"]:
            tbl.add_column(col, justify="right" if col != "Ticker" else "left")

        for ticker, pos in self.positions.items():
            c = "green" if pos.unrealised_pnl >= 0 else "red"
            tbl.add_row(
                ticker, str(pos.shares),
                f"{pos.entry_price:.2f}", f"{pos.current_price:.2f}",
                f"{pos.stop_price:.2f}",
                f"[{c}]{C}{pos.unrealised_pnl:+,.2f}[/]",
                f"[{c}]{pos.unrealised_pct:+.2f}%[/]",
                "[green]₹0[/]",
            )
        if not self.positions:
            tbl.add_row("—", "—", "—", "No open positions", "", "", "", "")
        return tbl

    def run_loop(self, cycles: Optional[int] = None) -> None:
        console.print(
            f"[bold color(208)]APEX Paper Trader — Groww (NSE/BSE)[/]  "
            f"Capital: {C}{self.initial_capital:,.2f}  "
            f"Brokerage: [green]₹0[/] delivery  "
            f"Symbols: {', '.join(self.tickers)}\n"
        )
        n = 0
        try:
            while cycles is None or n < cycles:
                if self.skip_closed and not _market_is_open():
                    ist = _ist_now().strftime("%H:%M IST")
                    console.print(
                        f"[dim][{ist}] Market closed. "
                        f"NSE: {MARKET_OPEN_IST}–{MARKET_CLOSE_IST} IST. "
                        f"Sleeping {self.cycle_seconds}s ...[/]"
                    )
                    time.sleep(self.cycle_seconds)
                    continue
                n += 1
                ts = _ist_now().strftime("%Y-%m-%d %H:%M:%S IST")
                console.rule(f"[color(208)]Cycle {n}  ·  {ts}[/]")
                for ticker in self.tickers:
                    self._evaluate_ticker(ticker)
                console.print(self._render_dashboard())
                if cycles is None or n < cycles:
                    time.sleep(self.cycle_seconds)
        except KeyboardInterrupt:
            console.print("\n[bold yellow]Paper Trader stopped.[/]")
        self._summary()

    def _summary(self) -> None:
        console.rule("[bold color(208)]SESSION SUMMARY — Groww (NSE/BSE)[/]")
        net_pnl  = sum(t.pnl_net   for t in self.trade_history)
        charges  = sum(t.charges   for t in self.trade_history)
        wins     = [t for t in self.trade_history if t.pnl_net > 0]
        wr       = len(wins) / len(self.trade_history) * 100 if self.trade_history else 0
        console.print(
            f"  Broker           : [bold color(208)]Groww[/]  (delivery brokerage = [green]₹0[/])\n"
            f"  Initial Capital  : [bold]{C}{self.initial_capital:,.2f}[/]\n"
            f"  Final Equity     : [bold]{C}{self.portfolio_value:,.2f}[/]\n"
            f"  Total Return     : [bold]{self.total_return_pct:+.2f}%[/]\n"
            f"  Trades Closed    : {len(self.trade_history)}\n"
            f"  Win Rate (net)   : {wr:.1f}%\n"
            f"  Total Charges    : [red]{C}{charges:,.2f}[/]  (STT+exch+stamp+GST+DP)\n"
            f"  Net P&L          : [bold]{C}{net_pnl:+,.2f}[/]\n"
        )
