# utils/constants.py
"""
Global constants — APEX Engine (India Edition)
================================================
Broker  : Groww  (zero-brokerage delivery)
Capital : ₹10,000 starting capital
Exchange: NSE / BSE
"""

# ── Currency & Locale ──────────────────────────────────────────────────────────
CURRENCY_SYMBOL:    str   = "₹"
CURRENCY_CODE:      str   = "INR"
CRORE:              float = 1_00_00_000.0    # 10,000,000

# ── Exchanges ──────────────────────────────────────────────────────────────────
EXCHANGE_NSE:       str   = "NSE"
EXCHANGE_BSE:       str   = "BSE"
DEFAULT_EXCHANGE:   str   = EXCHANGE_NSE
NSE_SUFFIX:         str   = ".NS"
BSE_SUFFIX:         str   = ".BO"

# ── Liquidity gate — ₹50 Crore avg daily turnover ─────────────────────────────
LIQUIDITY_WINDOW_DAYS:   int   = 20
MIN_DAILY_TURNOVER_INR:  float = 50 * CRORE      # ₹50,00,00,000

# ── Strategy ───────────────────────────────────────────────────────────────────
EMA_FAST:                int   = 50
EMA_SLOW:                int   = 200
RSI_PERIOD:              int   = 14
RSI_LOW:                 float = 40.0
RSI_HIGH:                float = 65.0
ATR_PERIOD:              int   = 14
ATR_MULTIPLIER:          float = 2.5
MAX_RISK_PER_TRADE_PCT:  float = 0.02            # 2% per trade

# ── Backtest validation ────────────────────────────────────────────────────────
MIN_PROFIT_FACTOR:  float = 1.4
MIN_WIN_RATE_PCT:   float = 45.0

# ── Data ───────────────────────────────────────────────────────────────────────
BACKTEST_YEARS:     int   = 5
DAILY_INTERVAL:     str   = "1d"
HOURLY_INTERVAL:    str   = "1h"

# ── Capital — Groww retail entry level ─────────────────────────────────────────
DEFAULT_INITIAL_CAPITAL: float = 10_000.0        # ₹10,000

# ── Groww Equity Delivery Charges ─────────────────────────────────────────────
# Source: https://groww.in/charges
#
# Brokerage          : ₹0 (FREE on delivery — Groww's USP)
# STT                : 0.1% on buy+sell turnover
# NSE Exch Txn Chg   : 0.00297% per leg (NSE circular, FY 2024–25)
# SEBI Turnover Fee  : ₹10 per Crore = 0.000001
# Stamp Duty         : 0.015% on buy value only
# GST                : 18% on exchange charges (brokerage=0, so no GST on it)
# DP Charges         : ₹13.5 + GST per scrip per day on sell (flat; not % based)
#                      → modelled separately in paper trader per trade

BROKERAGE_PCT:           float = 0.0             # ₹0 delivery brokerage
STT_DELIVERY_PCT:        float = 0.001           # 0.100%
EXCHANGE_TXN_CHARGES:    float = 0.0000297       # 0.00297% per leg
SEBI_CHARGES:            float = 0.000001        # 0.000100%
STAMP_DUTY_PCT:          float = 0.00015         # 0.015% on buy
GST_RATE:                float = 0.18            # 18% on exchange charges
DP_CHARGE_PER_SELL:      float = 13.5            # ₹13.5 flat + 18% GST per scrip/day sold
DP_CHARGE_WITH_GST:      float = DP_CHARGE_PER_SELL * (1 + GST_RATE)  # ₹15.93

# Composite % commission for Backtrader (excludes flat DP charge handled separately)
# = STT + exch×2 + sebi×2 + stamp + GST_on_exch×2
COMMISSION_PCT: float = (
    STT_DELIVERY_PCT                          # 0.100000%
    + EXCHANGE_TXN_CHARGES * 2               # 0.005940%
    + SEBI_CHARGES * 2                       # 0.000200%
    + STAMP_DUTY_PCT                         # 0.015000%
    + EXCHANGE_TXN_CHARGES * 2 * GST_RATE   # 0.001069%
)
# ≈ 0.1222% round-trip  (Groww delivery, excl. DP flat charges)

# ── IST Market Hours ───────────────────────────────────────────────────────────
MARKET_OPEN_IST:    str = "09:15"
MARKET_CLOSE_IST:   str = "15:30"
IST_OFFSET_HOURS:   int = 5
IST_OFFSET_MINUTES: int = 30

# ── Rate-limit / retry ─────────────────────────────────────────────────────────
MAX_RETRIES:        int   = 5
RETRY_BACKOFF_S:    float = 2.0
