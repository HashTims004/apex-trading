# backtesting/report.py
"""
APEX HTML Report Generator — India Edition
==========================================
Generates a self-contained, styled HTML backtest report from a list of
BacktestResult objects. No external dependencies beyond Python stdlib.

Output: single .html file, embeds all CSS/JS inline.
Charts rendered via Chart.js CDN.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from backtesting.evaluator import BacktestResult
from utils.constants import CURRENCY_SYMBOL, COMMISSION_PCT, DP_CHARGE_WITH_GST

C = CURRENCY_SYMBOL


def _badge(valid: bool) -> str:
    if valid:
        return '<span class="badge valid">✓ VALID — DEPLOY</span>'
    return '<span class="badge invalid">✗ INVALID — DO NOT DEPLOY</span>'


def _score_bar(score: float) -> str:
    colour = "#27ae60" if score >= 60 else ("#e67e22" if score >= 35 else "#e74c3c")
    return (
        f'<div class="score-bar-wrap">'
        f'<div class="score-bar" style="width:{score:.0f}%;background:{colour}"></div>'
        f'<span class="score-label">{score:.0f}</span></div>'
    )


def _metric_row(label: str, value: str, note: str = "", highlight: str = "") -> str:
    cls = f' class="{highlight}"' if highlight else ""
    return f'<tr{cls}><td class="metric-label">{label}</td><td class="metric-val">{value}</td><td class="metric-note">{note}</td></tr>'


def _card(result: BacktestResult) -> str:
    r   = result
    rc  = "pos" if r.total_return_pct >= 0 else "neg"
    ac  = "pos" if r.alpha_pct >= 0 else "neg"
    pf  = f"{r.profit_factor:.3f}" if r.profit_factor != float("inf") else "∞"
    valid_cls = "valid-card" if r.is_valid else "invalid-card"

    rows = [
        _metric_row("Total Return",         f'<span class="{rc}">{r.total_return_pct:+.2f}%</span>'),
        _metric_row("CAGR",                 f'<span class="{rc}">{r.cagr_pct:+.2f}%</span>'),
        _metric_row("Benchmark (B&H)",      f"{r.benchmark_return_pct:+.2f}%"),
        _metric_row("Alpha vs Benchmark",   f'<span class="{ac}">{r.alpha_pct:+.2f}%</span>',  "> 0%"),
        _metric_row("Max Drawdown",         f"{r.max_drawdown_pct:.2f}%",                       "< 20% ideal"),
        _metric_row("Win Rate",             f"{r.win_rate_pct:.1f}%",                           "≥ 45% required"),
        _metric_row("Profit Factor",        pf,                                                 "≥ 1.4 required"),
        _metric_row("Sharpe (trade, rf=7%)",f"{r.sharpe_ratio:.3f}",                            "≥ 1.0 ideal"),
        _metric_row("Calmar Ratio",         f"{r.calmar_ratio:.3f}",                            "≥ 1.0 ideal"),
        _metric_row("Total Trades",         str(r.total_trades)),
        _metric_row("Avg Hold (days)",      f"{r.avg_hold_days:.0f}"),
        _metric_row("Best Trade",           f'<span class="pos">{r.best_trade_pct:+.2f}%</span>'),
        _metric_row("Worst Trade",          f'<span class="neg">{r.worst_trade_pct:+.2f}%</span>'),
        _metric_row("Win / Loss Streak",    f'<span class="pos">{r.max_win_streak}W</span> / <span class="neg">{r.max_loss_streak}L</span>'),
        _metric_row("Initial Equity",       f"{C}{r.initial_equity:,.2f}"),
        _metric_row("Final Equity",         f'<span class="{rc}">{C}{r.final_equity:,.2f}</span>'),
        _metric_row("Gross Profit",         f'<span class="pos">{C}{r.gross_profit:,.2f}</span>'),
        _metric_row("Gross Loss",           f'<span class="neg">{C}{r.gross_loss:,.2f}</span>'),
        _metric_row("Broker",               '<span class="broker">Groww  ₹0 brokerage</span>', "STT+Exch+GST+Stamp+DP"),
    ]

    validation_block = ""
    if not r.is_valid:
        reasons = "".join(f"<li>{m}</li>" for m in r.validation_messages)
        validation_block = f'<div class="fail-reasons"><strong>⚠ Fail Reasons:</strong><ul>{reasons}</ul></div>'

    return f"""
    <div class="result-card {valid_cls}">
      <div class="card-header">
        <h2>{r.ticker}</h2>
        <div class="card-header-right">
          {_badge(r.is_valid)}
          <div class="score-wrap">Score {_score_bar(r.score)}</div>
        </div>
      </div>
      <table class="metrics-table">{''.join(rows)}</table>
      {validation_block}
    </div>"""


def generate_html_report(
    results:    List[BacktestResult],
    output_path: Optional[Path] = None,
    title:      str = "APEX Backtest Report — NSE/BSE",
) -> Path:
    """
    Generate a self-contained HTML report and write to output_path.
    Returns the path of the written file.
    """
    if output_path is None:
        ts          = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = Path("reports") / f"apex_report_{ts}.html"
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    now       = datetime.now().strftime("%d %b %Y  %H:%M:%S")
    valid_ct  = sum(1 for r in results if r.is_valid)
    cards     = "\n".join(_card(r) for r in results)

    # Mini summary bar chart data
    labels  = json.dumps([r.ticker for r in results])
    returns = json.dumps([round(r.total_return_pct, 2) for r in results])
    bmarks  = json.dumps([round(r.benchmark_return_pct, 2) for r in results])
    scores  = json.dumps([r.score for r in results])

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  :root {{
    --orange: #e67e22; --orange-dark: #ca6f1e;
    --green:  #27ae60; --red:   #e74c3c;
    --bg:     #0f0f0f; --card:  #1a1a1a; --border: #2e2e2e;
    --text:   #e8e8e8; --muted: #888;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--text); font-family: 'Segoe UI', sans-serif; font-size: 14px; }}
  header {{ background: linear-gradient(135deg, #1a0a00 0%, #2e1500 100%);
            border-bottom: 2px solid var(--orange); padding: 24px 32px; }}
  header h1 {{ color: var(--orange); font-size: 26px; letter-spacing: 1px; }}
  header .sub {{ color: var(--muted); font-size: 13px; margin-top: 4px; }}
  .summary-bar {{ display: flex; gap: 16px; flex-wrap: wrap; padding: 18px 32px;
                  background: #111; border-bottom: 1px solid var(--border); }}
  .summary-item {{ background: var(--card); border: 1px solid var(--border);
                   border-radius: 8px; padding: 12px 20px; min-width: 120px; text-align: center; }}
  .summary-item .val {{ font-size: 22px; font-weight: 700; color: var(--orange); }}
  .summary-item .lbl {{ font-size: 11px; color: var(--muted); margin-top: 2px; text-transform: uppercase; }}
  .charts-row {{ display: flex; gap: 20px; flex-wrap: wrap; padding: 24px 32px; }}
  .chart-box {{ background: var(--card); border: 1px solid var(--border); border-radius: 10px;
               padding: 16px; flex: 1; min-width: 320px; max-width: 600px; }}
  .chart-box h3 {{ color: var(--orange); font-size: 13px; margin-bottom: 12px; text-transform: uppercase; letter-spacing: 0.5px; }}
  .results-grid {{ display: flex; flex-wrap: wrap; gap: 20px; padding: 0 32px 32px; }}
  .result-card {{ background: var(--card); border: 1px solid var(--border);
                  border-radius: 10px; padding: 20px; flex: 1; min-width: 340px; max-width: 520px; }}
  .valid-card   {{ border-left: 4px solid var(--green); }}
  .invalid-card {{ border-left: 4px solid var(--red); }}
  .card-header  {{ display: flex; justify-content: space-between; align-items: flex-start;
                   margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }}
  .card-header h2 {{ font-size: 20px; color: var(--orange); }}
  .card-header-right {{ display: flex; flex-direction: column; align-items: flex-end; gap: 6px; }}
  .badge {{ padding: 4px 10px; border-radius: 4px; font-size: 12px; font-weight: 700; }}
  .badge.valid   {{ background: #1a4a2a; color: var(--green); border: 1px solid var(--green); }}
  .badge.invalid {{ background: #4a1a1a; color: var(--red);   border: 1px solid var(--red); }}
  .score-wrap {{ font-size: 12px; color: var(--muted); display: flex; align-items: center; gap: 8px; }}
  .score-bar-wrap {{ width: 80px; height: 8px; background: #333; border-radius: 4px; position: relative; display: inline-block; }}
  .score-bar {{ height: 100%; border-radius: 4px; transition: width 0.4s; }}
  .score-label {{ position: absolute; right: -26px; top: -4px; font-size: 12px; color: var(--text); font-weight: 700; }}
  .metrics-table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  .metrics-table td {{ padding: 5px 4px; border-bottom: 1px solid var(--border); vertical-align: top; }}
  .metric-label {{ color: var(--muted); width: 48%; }}
  .metric-val   {{ font-weight: 600; width: 28%; }}
  .metric-note  {{ color: var(--muted); font-size: 11px; width: 24%; }}
  .pos {{ color: var(--green); }} .neg {{ color: var(--red); }}
  .broker {{ color: var(--orange); }}
  .fail-reasons {{ background: #2a1010; border: 1px solid var(--red); border-radius: 6px;
                   padding: 10px 14px; margin-top: 14px; font-size: 12px; color: #f08080; }}
  .fail-reasons ul {{ margin-top: 4px; padding-left: 18px; }}
  footer {{ text-align: center; padding: 20px; color: var(--muted); font-size: 12px;
            border-top: 1px solid var(--border); }}
</style>
</head>
<body>
<header>
  <h1>⚡ APEX Backtest Report — NSE / BSE</h1>
  <div class="sub">
    Generated: {now}  ·  Broker: <strong>Groww</strong> (₹0 delivery brokerage)  ·
    Charges: STT + Exch + GST + Stamp + DP (≈{COMMISSION_PCT*100:.3f}% r/t + ₹{DP_CHARGE_WITH_GST:.2f} flat)  ·
    Sharpe: trade-based, rf = 7% (India 10Y G-Sec)
  </div>
</header>

<div class="summary-bar">
  <div class="summary-item"><div class="val">{len(results)}</div><div class="lbl">Tickers</div></div>
  <div class="summary-item"><div class="val" style="color:{'#27ae60' if valid_ct else '#e74c3c'}">{valid_ct}</div><div class="lbl">Valid</div></div>
  <div class="summary-item"><div class="val" style="color:#e74c3c">{len(results)-valid_ct}</div><div class="lbl">Invalid</div></div>
  <div class="summary-item"><div class="val">{max((r.total_return_pct for r in results), default=0):+.1f}%</div><div class="lbl">Best Return</div></div>
  <div class="summary-item"><div class="val">{max((r.score for r in results), default=0):.0f}</div><div class="lbl">Best Score</div></div>
  <div class="summary-item"><div class="val">{sum(r.total_trades for r in results)}</div><div class="lbl">Total Trades</div></div>
</div>

<div class="charts-row">
  <div class="chart-box">
    <h3>Strategy Return vs Benchmark (Buy &amp; Hold)</h3>
    <canvas id="returnChart" height="180"></canvas>
  </div>
  <div class="chart-box">
    <h3>Composite Score (ranked)</h3>
    <canvas id="scoreChart" height="180"></canvas>
  </div>
</div>

<div class="results-grid">{cards}</div>

<footer>
  APEX Engine — India Edition v2.3.0  ·  NSE / BSE  ·  Strategy: EMA-50/200 × RSI(14) × ATR(14)
</footer>

<script>
const labels  = {labels};
const returns = {returns};
const bmarks  = {bmarks};
const scores  = {scores};

new Chart(document.getElementById('returnChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [
      {{ label: 'Strategy Return %',  data: returns, backgroundColor: returns.map(v => v>=0?'rgba(39,174,96,0.7)':'rgba(231,76,60,0.7)') }},
      {{ label: 'Benchmark B&H %',    data: bmarks,  backgroundColor: 'rgba(230,126,34,0.4)', borderColor:'rgba(230,126,34,0.8)', borderWidth:1 }},
    ]
  }},
  options: {{ plugins: {{ legend: {{ labels: {{ color:'#ccc' }} }} }},
              scales: {{ x: {{ ticks: {{ color:'#888' }} }}, y: {{ ticks: {{ color:'#888' }}, grid: {{ color:'#222' }} }} }} }}
}});

new Chart(document.getElementById('scoreChart'), {{
  type: 'bar',
  data: {{
    labels,
    datasets: [{{ label: 'Score /100', data: scores,
      backgroundColor: scores.map(v => v>=60?'rgba(39,174,96,0.7)':v>=35?'rgba(230,126,34,0.7)':'rgba(231,76,60,0.7)') }}]
  }},
  options: {{ plugins: {{ legend: {{ labels: {{ color:'#ccc' }} }} }},
              scales: {{ x: {{ ticks: {{ color:'#888' }} }}, y: {{ min:0,max:100, ticks: {{ color:'#888' }}, grid: {{ color:'#222' }} }} }} }}
}});
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
    return output_path
