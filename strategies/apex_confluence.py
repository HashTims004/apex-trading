# strategies/apex_confluence.py
"""
APEX Confluence Strategy  -  Backtrader Implementation
=======================================================
Buy Rules
---------
  1. 50-EMA crosses ABOVE 200-EMA  (golden cross on signal bar)
  2. RSI(14) is in the range [40, 65]  (momentum building, not exhausted)
  3. Ticker passed the external LiquidityFilter  (enforced by caller)

Risk Management
---------------
  - Stop loss   : entry_price - (ATR(14) x 2.5)  -- ATR trailing stop
  - Position size: risk_per_trade = 2% of account equity
                   shares = risk_$ / (ATR(14) x 2.5)
  - Stop trails upward only; never moved down.

Exit Rules
----------
  - Trailing ATR stop hit (primary)
  - EMA death cross (50-EMA crosses below 200-EMA) -- secondary graceful exit
"""

from __future__ import annotations

import math
from typing import Optional

try:
    import backtrader as bt
except ImportError as e:
    raise ImportError("backtrader is required: pip install backtrader") from e

from utils.constants import (
    EMA_FAST,
    EMA_SLOW,
    RSI_PERIOD,
    RSI_LOW,
    RSI_HIGH,
    ATR_PERIOD,
    ATR_MULTIPLIER,
    MAX_RISK_PER_TRADE_PCT,
)
from utils.logger import logger


# ─────────────────────────────────────────────────────────────────────────────
# Apex Confluence Strategy
# ─────────────────────────────────────────────────────────────────────────────

class ApexConfluenceStrategy(bt.Strategy):
    """
    Apex Confluence Strategy.

    Parameters
    ----------
    ema_fast        : fast EMA period (default 50)
    ema_slow        : slow EMA period (default 200)
    rsi_period      : RSI period (default 14)
    rsi_low         : RSI lower bound for entry (default 40)
    rsi_high        : RSI upper bound for entry (default 65)
    atr_period      : ATR period for stop calculation (default 14)
    atr_mult        : ATR multiplier for stop distance (default 2.5)
    risk_pct        : max risk per trade as fraction of equity (default 0.02)
    printlog        : if True, log every bar action to loguru
    """

    params = dict(
        ema_fast=EMA_FAST,
        ema_slow=EMA_SLOW,
        rsi_period=RSI_PERIOD,
        rsi_low=RSI_LOW,
        rsi_high=RSI_HIGH,
        atr_period=ATR_PERIOD,
        atr_mult=ATR_MULTIPLIER,
        risk_pct=MAX_RISK_PER_TRADE_PCT,
        printlog=False,
    )

    def __init__(self) -> None:
        # Core indicators
        self.ema_fast = bt.indicators.EMA(self.data.close, period=self.p.ema_fast)
        self.ema_slow = bt.indicators.EMA(self.data.close, period=self.p.ema_slow)
        self.rsi      = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.atr      = bt.indicators.ATR(self.data, period=self.p.atr_period)

        # CrossOver: +1 when fast crosses above slow (golden), -1 when below (death)
        self.cross = bt.indicators.CrossOver(self.ema_fast, self.ema_slow)

        # State
        self.order:         Optional[bt.Order] = None
        self.entry_price:   float = 0.0
        self.trailing_stop: float = 0.0

        # Trade tracking (used by evaluator)
        self.wins:         int = 0
        self.losses:       int = 0
        self.gross_profit: float = 0.0
        self.gross_loss:   float = 0.0

    # -------------------------------------------------------------------------

    def _log(self, msg: str) -> None:
        if self.p.printlog:
            logger.debug(f"[{self.data.datetime.date(0)}] {msg}")

    # ── Order lifecycle callbacks ─────────────────────────────────────────

    def notify_order(self, order: bt.Order) -> None:
        if order.status in (order.Submitted, order.Accepted):
            return

        if order.status == order.Completed:
            if order.isbuy():
                self.entry_price = order.executed.price
                stop_dist        = self.atr[0] * self.p.atr_mult
                self.trailing_stop = self.entry_price - stop_dist
                self._log(
                    f"BUY  @ {self.entry_price:.2f}  "
                    f"size={order.executed.size:.0f}  "
                    f"stop={self.trailing_stop:.2f}"
                )
            elif order.issell():
                self._log(f"SELL @ {order.executed.price:.2f}")

        elif order.status in (order.Canceled, order.Margin, order.Rejected):
            self._log(f"Order FAILED  status={order.getstatusname()}")

        self.order = None   # clear pending flag

    def notify_trade(self, trade: bt.Trade) -> None:
        if not trade.isclosed:
            return

        pnl = trade.pnlcomm
        if pnl > 0:
            self.wins         += 1
            self.gross_profit += pnl
        else:
            self.losses   += 1
            self.gross_loss += abs(pnl)

        self._log(
            f"TRADE CLOSED  PnL={pnl:+.2f}  "
            f"equity={self.broker.getvalue():.2f}"
        )

    # ── Position sizing ───────────────────────────────────────────────────

    def _calc_position_size(self) -> int:
        """
        Shares = (equity * risk_pct) / (ATR * atr_mult)
        Capped at what cash can actually afford at current price.

        Small-capital fallback
        ----------------------
        When risk-based sizing gives 0 shares (stop_dist > risk_₹),
        we fall back to buying exactly 1 share IF the single share costs
        <= MAX_SINGLE_SHARE_PCT of current equity (default 50%).
        This keeps the engine usable at ₹10,000 capital where risk-math
        would otherwise always produce 0 for large-cap stocks.
        """
        MAX_SINGLE_SHARE_PCT = 0.50   # never put more than 50% on one share

        equity       = self.broker.getvalue()
        cash         = self.broker.getcash()
        price        = self.data.close[0]
        stop_dist    = self.atr[0] * self.p.atr_mult

        if stop_dist <= 0 or price <= 0:
            return 0

        risk_inr       = equity * self.p.risk_pct
        size_by_risk   = int(risk_inr / stop_dist)
        size_by_cash   = int(cash / price)
        size           = min(size_by_risk, size_by_cash)

        if size < 1:
            # Small-capital fallback: buy 1 share if affordable
            if price <= equity * MAX_SINGLE_SHARE_PCT and cash >= price:
                size = 1
            else:
                return 0   # stock too expensive for this capital

        return size

    # ── Main bar loop ─────────────────────────────────────────────────────

    def next(self) -> None:
        if self.order:   # pending order outstanding
            return

        in_position = self.position.size > 0

        if in_position:
            # ── Trail the stop only upward ──────────────────────────────────
            candidate = self.data.close[0] - (self.atr[0] * self.p.atr_mult)
            if candidate > self.trailing_stop:
                self.trailing_stop = candidate

            # ── Exit 1: trailing stop breached ─────────────────────────────
            if self.data.close[0] <= self.trailing_stop:
                self._log(
                    f"STOP HIT  close={self.data.close[0]:.2f}  "
                    f"stop={self.trailing_stop:.2f}"
                )
                self.order = self.sell()
                return

            # ── Exit 2: death cross ─────────────────────────────────────────
            if self.cross[0] == -1.0:
                self._log("DEATH CROSS exit")
                self.order = self.sell()
                return

        else:
            # ── Entry: golden cross + RSI filter + valid ATR ────────────────
            golden_cross = self.cross[0] == 1.0
            rsi_ok       = self.p.rsi_low <= self.rsi[0] <= self.p.rsi_high
            atr_ok       = self.atr[0] > 0

            if golden_cross and rsi_ok and atr_ok:
                size = self._calc_position_size()
                if size > 0:
                    self._log(
                        f"ENTRY  EMA50={self.ema_fast[0]:.2f}  "
                        f"EMA200={self.ema_slow[0]:.2f}  "
                        f"RSI={self.rsi[0]:.1f}  "
                        f"size={size}"
                    )
                    self.order = self.buy(size=size)

    # ── Aggregate stats accessor ──────────────────────────────────────────

    def get_stats(self) -> dict:
        total = self.wins + self.losses
        win_rate = (self.wins / total * 100) if total > 0 else 0.0
        profit_factor = (
            self.gross_profit / self.gross_loss
            if self.gross_loss > 0
            else float("inf")
        )
        return {
            "total_trades":   total,
            "wins":           self.wins,
            "losses":         self.losses,
            "win_rate_pct":   win_rate,
            "gross_profit":   self.gross_profit,
            "gross_loss":     self.gross_loss,
            "profit_factor":  profit_factor,
        }
