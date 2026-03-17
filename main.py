#!/usr/bin/env python3
# main.py
"""
APEX Algorithmic Trading Engine — India Edition v2.1
=====================================================
Broker : Groww  (₹0 delivery brokerage)
Capital: ₹10,000 default
Markets: NSE (.NS) / BSE (.BO)

Modes
-----
  demo       Fully offline demo — 8 synthetic NSE tickers, no internet needed
  backtest   5-year Backtrader backtest on live yfinance data
  scan       Liquidity scan (₹50 Cr gate) across a watchlist
  paper      Live paper-trading loop (IST market-hours aware)
  liquidity  Quick liquidity check on specific tickers
  charges    Print full Groww charge breakdown for a trade size

CLI examples
------------
  python main.py --mode demo
  python main.py --mode demo     --capital 10000
  python main.py --mode backtest --ticker RELIANCE
  python main.py --mode backtest --ticker RELIANCE,TCS,INFY --capital 50000
  python main.py --mode backtest --ticker HDFCBANK --exchange BSE
  python main.py --mode scan     --list nifty50
  python main.py --mode scan     --list nifty50  --max-price 1000
  python main.py --mode scan     --list sensex   --exchange BSE
  python main.py --mode paper    --ticker RELIANCE,TCS  --cycles 5
  python main.py --mode liquidity --ticker RELIANCE,ZOMATO,PAYTM
  python main.py --mode charges  --capital 10000
"""

from __future__ import annotations
import argparse
import sys
from typing import List, Optional

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich import box
except ImportError:
    print("ERROR: 'rich' not installed. Run: pip install -r requirements.txt")
    sys.exit(1)

from utils.constants import (
    CURRENCY_SYMBOL, DEFAULT_EXCHANGE, CRORE,
    MIN_DAILY_TURNOVER_INR, COMMISSION_PCT,
    DP_CHARGE_WITH_GST, DEFAULT_INITIAL_CAPITAL,
)

C       = CURRENCY_SYMBOL
console = Console()

VALID_LISTS = ("nifty50", "nifty100", "nifty500", "sensex", "banknifty", "midcap50")

# ─────────────────────────────────────────────────────────────────────────────
# Banner
# ─────────────────────────────────────────────────────────────────────────────

def print_banner() -> None:
    console.print(Panel(
        "[bold color(208)]\n"
        "  ░█████╗░██████╗░███████╗██╗  ██╗\n"
        "  ██╔══██╗██╔══██╗██╔════╝╚██╗██╔╝\n"
        "  ███████║██████╔╝█████╗   ╚███╔╝ \n"
        "  ██╔══██║██╔═══╝ ██╔══╝   ██╔██╗ \n"
        "  ██║  ██║██║     ███████╗██╔╝ ██╗\n"
        "  ╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝\n[/bold color(208)]"
        f"[dim]  India Edition v2.1  |  Broker: Groww  |  "
        f"Default Capital: {C}{DEFAULT_INITIAL_CAPITAL:,.0f}[/dim]\n"
        "[dim]  Strategy : EMA-50/200 × RSI(14) × ATR(14) Trailing Stop     [/dim]\n"
        "[dim]  Charges  : STT + Exch + Stamp + SEBI + GST + DP  (~0.12% r/t)[/dim]\n",
        border_style="color(208)", padding=(0, 2),
    ))


# ─────────────────────────────────────────────────────────────────────────────
# Mode: demo
# ─────────────────────────────────────────────────────────────────────────────

def mode_demo(capital: float) -> None:
    """Fully offline demo — no internet required."""
    from utils.synthetic_data import make_nse_portfolio, describe
    from data.data_engine import LiquidityFilter
    from backtesting.evaluator import BacktestEvaluator
    from execution.paper_trader import _calc_charges, _charge_breakdown
    from utils.constants import (
        STT_DELIVERY_PCT, EXCHANGE_TXN_CHARGES, STAMP_DUTY_PCT,
        SEBI_CHARGES, CRORE, MIN_DAILY_TURNOVER_INR, COMMISSION_PCT,
        DP_CHARGE_WITH_GST,
    )

    console.print(Panel(
        f"[bold color(208)]  APEX OFFLINE DEMO[/]\n"
        f"[dim]  8 synthetic NSE tickers  |  Broker: Groww  |  Capital: {C}{capital:,.0f}[/dim]\n"
        f"[dim]  No internet required — all prices generated via GBM simulation.[/dim]\n",
        border_style="color(208)", padding=(0, 2),
    ))

    # Phase 1 — Portfolio
    console.rule("[bold color(208)]  1 · Synthetic NSE Portfolio  [/]")
    portfolio = make_nse_portfolio(days=1300)
    console.print()
    for t, df in portfolio.items():
        console.print(describe(df, t))
    console.print()

    # Phase 2 — Liquidity gate
    console.rule(f"[bold color(208)]  2 · Liquidity Gate  ({C}{MIN_DAILY_TURNOVER_INR/CRORE:.0f} Cr threshold)  [/]")
    lf          = LiquidityFilter()
    liquid_data = {}
    console.print()
    for ticker, df in portfolio.items():
        ok, tv = lf.evaluate(ticker, df)
        last   = df["Close"].iloc[-1]
        col    = "green" if ok else "red"
        status = "LIQUID ✓          " if ok else "UNTRADABLE_ILLIQUID ✗"
        shares = int(capital / last) if last > 0 else 0
        af_tag = f"  [dim]LTP ₹{last:.0f} → can buy ~{shares} shares[/]"
        console.print(f"  [{col}]{status}[/]  [bold]{ticker:26s}[/]  ₹{tv/CRORE:.1f} Cr{af_tag}")
        if ok:
            liquid_data[ticker] = df
    console.print(f"\n  [bold green]{len(liquid_data)} liquid[/] / [bold red]{len(portfolio)-len(liquid_data)} illiquid[/]\n")

    # Phase 3 — Charges
    console.rule("[bold color(208)]  3 · Groww Delivery Charges  [/]")
    bd    = _charge_breakdown(capital, capital, n_scrips_sold=1)
    total = _calc_charges(capital, capital, n_scrips_sold=1)
    tbl   = Table(
        title=f"[bold]Groww charges on {C}{capital:,.0f} round-trip[/]",
        box=box.ROUNDED, header_style="bold magenta",
    )
    tbl.add_column("Component",     style="bold white", min_width=30)
    tbl.add_column("Rate",          justify="right",    min_width=22)
    tbl.add_column(f"Amount ({C})", justify="right",    min_width=12)
    for name, rate, amt in [
        ("Brokerage",                  "[bold green]FREE — ₹0[/]",                0.0),
        ("STT  (0.1% buy+sell)",       f"{STT_DELIVERY_PCT*100:.3f}%",            bd["STT"]),
        ("NSE Exch Txn  (0.00297%/leg)",f"{EXCHANGE_TXN_CHARGES*100:.5f}% × 2",  bd["Exch Txn Charges"]),
        ("SEBI Fee  (₹10/Cr)",          f"{SEBI_CHARGES*100:.6f}%",               bd["SEBI Fee"]),
        ("Stamp Duty  (buy only)",      f"{STAMP_DUTY_PCT*100:.4f}%",             bd["Stamp Duty"]),
        ("GST  (18% on exch only)",     "18%",                                    bd["GST (18%)"]),
        ("DP Charges  (₹13.5+GST sell)","₹15.93 flat/scrip/day",                 bd["DP Charges"]),
    ]:
        tbl.add_row(name, rate, f"[green]{C}0.0000[/]" if amt == 0 else f"{C}{amt:.4f}")
    tbl.add_section()
    tbl.add_row("[bold]TOTAL[/]", f"≈{COMMISSION_PCT*100:.4f}% + {C}{DP_CHARGE_WITH_GST:.2f} DP",
                f"[bold]{C}{total:.4f}[/]")
    console.print(tbl)
    console.print(f"  [dim]{C}{total:.2f} total on {C}{capital:,.0f} trade = {total/capital*100:.3f}% of value[/]\n")

    # Phase 4 — Backtests
    console.rule("[bold color(208)]  4 · Apex Confluence Strategy Backtest  [/]")
    ev      = BacktestEvaluator(initial_capital=capital)
    results = []
    for ticker, df in liquid_data.items():
        r = ev.run(ticker, df, years=5)
        results.append(r)
        ev.print_dashboard(r)

    # Phase 5 — Summary
    console.rule("[bold color(208)]  5 · Validation Gate Summary  [/]")
    ev.print_batch_summary(results)
    valid = [r for r in results if r.is_valid]
    if valid:
        best = max(valid, key=lambda r: r.total_return_pct)
        console.print(Panel(
            f"[bold green]  ✓  {len(valid)}/{len(results)} ticker(s) passed validation gates.\n\n"
            f"  Best : [bold]{best.ticker}[/]  "
            f"Return {best.total_return_pct:+.2f}%  "
            f"PF {best.profit_factor:.3f}  "
            f"WR {best.win_rate_pct:.1f}%  "
            f"Sharpe {best.sharpe_ratio:.3f}[/]",
            border_style="green", padding=(0, 2),
        ))
    else:
        console.print(Panel(
            "[bold red]  ✗  No tickers passed both PF ≥ 1.4 and WR ≥ 45% on this synthetic dataset.\n"
            "  This is expected — synthetic GBM data doesn't mimic real trending markets.\n"
            "  Run on live NSE data:  python main.py --mode backtest --list nifty50[/]",
            border_style="red", padding=(0, 2),
        ))


# ─────────────────────────────────────────────────────────────────────────────
# Mode: backtest
# ─────────────────────────────────────────────────────────────────────────────

def mode_backtest(
    tickers:   List[str],
    capital:   float,
    years:     int,
    exchange:  str,
    max_price: Optional[float],
) -> None:
    from data.data_engine import DataEngine, normalise_ticker
    from backtesting.evaluator import BacktestEvaluator

    engine    = DataEngine(exchange=exchange)
    evaluator = BacktestEvaluator(initial_capital=capital)
    liquid_tickers, daily_data = [], {}

    for sym in tickers:
        console.print(f"\n[bold]▶  {sym}  ({exchange})[/]")
        daily = engine.get_daily(sym, years=years)
        if daily is None:
            console.print(f"  [red]No data — skipping.[/]")
            continue

        # Price filter for small accounts
        if max_price is not None:
            ltp = daily["Close"].iloc[-1]
            if ltp > max_price:
                console.print(f"  [yellow]LTP ₹{ltp:.0f} > --max-price ₹{max_price:.0f} — skipping.[/]")
                continue

        if not engine.is_liquid(sym, daily):
            console.print(f"  [red]Illiquid (avg turnover < {C}{MIN_DAILY_TURNOVER_INR/CRORE:.0f} Cr) — removed.[/]")
            continue

        ticker = normalise_ticker(sym, exchange)
        liquid_tickers.append(ticker)
        daily_data[ticker] = daily

    if not liquid_tickers:
        console.print("[bold red]\nNo liquid tickers to backtest.[/]")
        return

    if len(liquid_tickers) == 1:
        r = evaluator.run(liquid_tickers[0], daily_data[liquid_tickers[0]], years=years)
        evaluator.print_dashboard(r)
    else:
        results = evaluator.run_batch(liquid_tickers, daily_data)
        evaluator.print_batch_summary(results)


# ─────────────────────────────────────────────────────────────────────────────
# Mode: scan
# ─────────────────────────────────────────────────────────────────────────────

def mode_scan(
    tickers:   List[str],
    exchange:  str,
    capital:   float,
    max_price: Optional[float],
) -> None:
    from data.data_engine import DataEngine

    engine = DataEngine(exchange=exchange)

    tbl = Table(
        title=(
            f"[bold cyan]APEX Liquidity Scan — {exchange}[/]  "
            f"[dim](threshold: {C}{MIN_DAILY_TURNOVER_INR/CRORE:.0f} Cr"
            + (f"  |  max price: {C}{max_price:.0f}" if max_price else "")
            + ")[/]"
        ),
        box=box.ROUNDED, header_style="bold magenta",
    )
    tbl.add_column("Symbol",              min_width=20)
    tbl.add_column("LTP (₹)",             justify="right", min_width=10)
    tbl.add_column(f"Affordable ({C}{capital:,.0f})", justify="center", min_width=14)
    tbl.add_column("Avg Turnover",        justify="right", min_width=16)
    tbl.add_column("Status",              min_width=24)

    liquid, illiquid = 0, 0
    for sym in tickers:
        daily = engine.get_daily(sym)
        if daily is None:
            illiquid += 1
            tbl.add_row(sym, "—", "—", "—", "[red]NO DATA[/]")
            continue

        ok, tv = engine.liquidity_filter.evaluate(sym, daily)
        ltp    = daily["Close"].iloc[-1]
        crores = tv / CRORE
        price_ok = (max_price is None or ltp <= max_price)
        tradable = ok and price_ok

        shares = int(capital / ltp) if ltp > 0 else 0
        af_str = (
            f"[green]~{shares} sh[/]" if ltp <= capital else "[red]< 1 share[/]"
        )
        col    = "green" if tradable else "red"
        status = "LIQUID ✓" if ok else "ILLIQUID ✗"
        if ok and not price_ok:
            status = "PRICE TOO HIGH ✗"
            col    = "yellow"

        tbl.add_row(
            sym, f"{ltp:.2f}", af_str,
            f"[{col}]{C}{crores:.1f} Cr[/]",
            f"[{col}]{status}[/]",
        )
        if tradable:
            liquid += 1
        else:
            illiquid += 1

    console.print(tbl)
    console.print(f"\n  [bold green]{liquid} tradable[/]  /  [bold red]{illiquid} filtered[/]  from {len(tickers)} symbols\n")


# ─────────────────────────────────────────────────────────────────────────────
# Mode: paper
# ─────────────────────────────────────────────────────────────────────────────

def mode_paper(
    tickers: List[str], capital: float, cycles: int, exchange: str
) -> None:
    from execution.paper_trader import PaperTrader
    PaperTrader(
        symbols=tickers, capital=capital,
        exchange=exchange, cycle_seconds=60,
    ).run_loop(cycles=cycles if cycles > 0 else None)


# ─────────────────────────────────────────────────────────────────────────────
# Mode: liquidity
# ─────────────────────────────────────────────────────────────────────────────

def mode_liquidity(tickers: List[str], exchange: str) -> None:
    from data.data_engine import DataEngine, normalise_ticker
    engine = DataEngine(exchange=exchange)
    for sym in tickers:
        ticker = normalise_ticker(sym, exchange)
        daily  = engine.get_daily(sym)
        if daily is None:
            console.print(f"  [red][{ticker}] Failed to fetch data.[/]")
            continue
        ok, tv = engine.liquidity_filter.evaluate(ticker, daily)
        crores = tv / CRORE
        ltp    = daily["Close"].iloc[-1]
        status = "[green]LIQUID ✓[/]" if ok else "[red]UNTRADABLE_ILLIQUID ✗[/]"
        console.print(
            f"  {ticker:22s}  {status}  "
            f"turnover=[bold]{C}{crores:.2f} Cr[/]  "
            f"LTP=[bold]{C}{ltp:.2f}[/]"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Mode: report
# ─────────────────────────────────────────────────────────────────────────────

def mode_report(
    tickers: List[str],
    capital: float,
    years: int,
    exchange: str,
) -> None:
    """Backtest all tickers and produce a self-contained HTML report."""
    from pathlib import Path
    from data.data_engine import DataEngine, resolve_ticker
    from backtesting.evaluator import BacktestEvaluator
    from backtesting.report import generate_html_report

    engine    = DataEngine(exchange=exchange)
    evaluator = BacktestEvaluator(initial_capital=capital)

    liquid_tickers, daily_data = [], {}
    for sym in tickers:
        daily = engine.get_daily(sym, years=years)
        if daily is None:
            console.print(f"[red]  {sym}: no data.[/]")
            continue
        if not engine.is_liquid(sym, daily):
            console.print(f"[yellow]  {sym}: illiquid — skipped.[/]")
            continue
        ticker = resolve_ticker(sym, exchange)
        liquid_tickers.append(ticker)
        daily_data[ticker] = daily

    if not liquid_tickers:
        console.print("[bold red]No liquid tickers to report on.[/]")
        return

    results = []
    for t in liquid_tickers:
        console.print(f"  Backtesting [bold cyan]{t}[/] ...")
        r = evaluator.run(t, daily_data[t], years=years)
        results.append(r)

    results.sort(key=lambda x: x.score, reverse=True)

    ts      = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    outfile = Path("reports") / f"apex_report_{ts}.html"
    path    = generate_html_report(results, output_path=outfile)
    console.print(f"\n[bold green]✓ Report written:[/] [bold]{path}[/]")
    evaluator.print_batch_summary(results)


# ─────────────────────────────────────────────────────────────────────────────
# Mode: charges
# ─────────────────────────────────────────────────────────────────────────────

def mode_charges(capital: float) -> None:
    from execution.paper_trader import _calc_charges, _charge_breakdown
    from utils.constants import (
        STT_DELIVERY_PCT, EXCHANGE_TXN_CHARGES, STAMP_DUTY_PCT,
        SEBI_CHARGES, COMMISSION_PCT, DP_CHARGE_WITH_GST,
    )

    bd    = _charge_breakdown(capital, capital, n_scrips_sold=1)
    total = _calc_charges(capital, capital, n_scrips_sold=1)

    tbl = Table(
        title=f"[bold]Groww Equity Delivery Charges  |  Trade value = {C}{capital:,.2f}[/]",
        box=box.ROUNDED, header_style="bold magenta",
    )
    tbl.add_column("Component",         style="bold white", min_width=34)
    tbl.add_column("Rate / Rule",       justify="right",    min_width=24)
    tbl.add_column(f"Amount ({C})",     justify="right",    min_width=14)

    for name, rate, amt in [
        ("Brokerage (equity delivery)",    "[bold green]₹0  —  FREE[/]",              0.0),
        ("STT on buy side (0.1%)",         f"{STT_DELIVERY_PCT*100:.3f}% × buy value",bd["STT"] / 2),
        ("STT on sell side (0.1%)",        f"{STT_DELIVERY_PCT*100:.3f}% × sell value",bd["STT"] / 2),
        ("NSE Exchange Txn (buy)",         f"{EXCHANGE_TXN_CHARGES*100:.5f}%",        bd["Exch Txn Charges"] / 2),
        ("NSE Exchange Txn (sell)",        f"{EXCHANGE_TXN_CHARGES*100:.5f}%",        bd["Exch Txn Charges"] / 2),
        ("SEBI Turnover Fee",              "₹10 per Crore turnover",                  bd["SEBI Fee"]),
        ("Stamp Duty (buy only)",          f"{STAMP_DUTY_PCT*100:.4f}% of buy value", bd["Stamp Duty"]),
        ("GST (18% on exchange charges)",  "18% × exchange txn charges",              bd["GST (18%)"]),
        ("DP Charges (Depository)",        "₹13.5 + 18% GST per scrip per sell day",  bd["DP Charges"]),
    ]:
        tbl.add_row(name, rate, f"[green]{C}0.0000[/]" if amt == 0 else f"{C}{amt:.4f}")

    tbl.add_section()
    tbl.add_row(
        "[bold]TOTAL (full round trip)[/]",
        f"≈{COMMISSION_PCT*100:.4f}% variable  +  {C}{DP_CHARGE_WITH_GST:.2f} flat",
        f"[bold]{C}{total:.4f}[/]",
    )
    console.print(tbl)
    console.print(
        f"\n  On a [bold]{C}{capital:,.2f}[/] buy+sell:\n"
        f"  → Total charges = [bold]{C}{total:.2f}[/]"
        f"  ({total/capital*100:.3f}% of trade value)\n"
        f"  → Break-even move needed = [bold]₹{total/int(capital/100):.2f}[/] per ₹100 invested\n"
        f"\n  [dim italic]Source: Groww charges page — https://groww.in/charges[/dim italic]\n"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apex",
        description="APEX Algo Trading Engine — India Edition (Groww / NSE / BSE)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""\
Quick start:
  python main.py --mode demo                          # offline demo, no internet
  python main.py --mode demo        --capital 50000
  python main.py --mode backtest    --ticker RELIANCE
  python main.py --mode backtest    --ticker TCS,INFY,WIPRO  --capital 50000
  python main.py --mode backtest    --ticker HDFCBANK  --exchange BSE
  python main.py --mode scan        --list nifty50
  python main.py --mode scan        --list nifty50     --max-price 1000
  python main.py --mode scan        --list RELIANCE,SBIN,ZOMATO
  python main.py --mode paper       --ticker RELIANCE,TCS  --cycles 5
  python main.py --mode liquidity   --ticker RELIANCE,ZOMATO,PAYTM
  python main.py --mode charges     --capital 10000

Index lists (--list): {', '.join(VALID_LISTS)}
Exchange suffixes resolved automatically:  RELIANCE → RELIANCE.NS (.NS / .BO)
        """,
    )
    p.add_argument("--mode", required=True,
                   choices=["demo", "backtest", "scan", "paper", "liquidity", "charges", "report"],
                   help="Operating mode")
    p.add_argument("--ticker", type=str, default=None,
                   help="Comma-separated NSE/BSE symbols")
    p.add_argument("--list", type=str, default=None, dest="watchlist",
                   help=f"Index watchlist or comma-separated symbols: {', '.join(VALID_LISTS)}")
    p.add_argument("--exchange", type=str, default=DEFAULT_EXCHANGE, choices=["NSE", "BSE"],
                   help="Exchange for suffix resolution (default: NSE)")
    p.add_argument("--capital", type=float, default=DEFAULT_INITIAL_CAPITAL,
                   help=f"Starting capital in INR (default: {C}{DEFAULT_INITIAL_CAPITAL:,.0f})")
    p.add_argument("--years", type=int, default=5,
                   help="Backtest history in years (default: 5)")
    p.add_argument("--cycles", type=int, default=0,
                   help="Paper trading cycles (0 = infinite, default: 0)")
    p.add_argument("--max-price", type=float, default=None, dest="max_price",
                   help=f"Skip stocks with LTP above this price (useful for small accounts)")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_watchlist(watchlist: str, exchange: str) -> List[str]:
    from data.data_engine import DataEngine
    engine = DataEngine(exchange=exchange)
    wl = watchlist.strip().lower()
    if wl == "nifty50":    return engine.get_nifty50_tickers()
    if wl == "nifty100":   return engine.get_nifty100_tickers()
    if wl == "nifty500":   return engine.get_nifty500_tickers()
    if wl == "sensex":     return engine.get_sensex_tickers()
    if wl == "banknifty":  return engine.get_banknifty_tickers()
    if wl == "midcap50":   return engine.get_midcap50_tickers()
    # Treat as comma-separated symbols
    return [t.strip().upper() for t in wl.split(",") if t.strip()]


def main() -> None:
    print_banner()
    parser = build_parser()
    args   = parser.parse_args()
    exchange = args.exchange.upper()

    tickers: List[str] = []
    if args.ticker:
        tickers += [t.strip().upper() for t in args.ticker.split(",") if t.strip()]
    if args.watchlist:
        tickers += _resolve_watchlist(args.watchlist, exchange)
    tickers = list(dict.fromkeys(tickers))  # deduplicate, preserve order

    if args.mode not in ("demo", "charges") and not tickers:
        parser.error(f"--mode {args.mode} requires symbols via --ticker or --list.")

    try:
        if   args.mode == "demo":
            mode_demo(capital=args.capital)
        elif args.mode == "backtest":
            mode_backtest(tickers, capital=args.capital, years=args.years,
                          exchange=exchange, max_price=args.max_price)
        elif args.mode == "scan":
            mode_scan(tickers, exchange=exchange, capital=args.capital,
                      max_price=args.max_price)
        elif args.mode == "paper":
            mode_paper(tickers, capital=args.capital, cycles=args.cycles, exchange=exchange)
        elif args.mode == "liquidity":
            mode_liquidity(tickers, exchange=exchange)
        elif args.mode == "charges":
            mode_charges(args.capital)
        elif args.mode == "report":
            mode_report(tickers, capital=args.capital, years=args.years, exchange=exchange)
    except KeyboardInterrupt:
        console.print("\n[bold yellow]Interrupted.[/]")
        sys.exit(0)
    except Exception as exc:
        console.print_exception(show_locals=False)
        console.print(f"\n[bold red]Fatal error:[/] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
