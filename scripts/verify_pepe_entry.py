"""Verify PEPE H4 entry on 2026-02-22 - check actual candle + EMA values"""
import sys
sys.path.insert(0, r"C:\Claude Work\EMA-Trading-Bot")

from src.trading.exchanges.okx import OKXFuturesClient
from src.trading.core.indicators import TechnicalIndicators
from src.trading.core.config import INDICATORS
from dotenv import load_dotenv
import os

load_dotenv()

client = OKXFuturesClient(
    api_key=os.getenv('OKX_API_KEY'),
    api_secret=os.getenv('OKX_API_SECRET'),
    passphrase=os.getenv('OKX_PASSPHRASE'),
)

symbol = "PEPEUSDT"

# Fetch H4 data
df_h4 = client.fetch_ohlcv(symbol, '4h', 100)

# Find the candle at 2026-02-22 04:00:00 UTC (the one bot checked)
print("=== Recent H4 candles for PEPE ===")
print(f"{'Timestamp':<22} {'Open':>14} {'High':>14} {'Low':>14} {'Close':>14}")
for i in range(-10, 0):
    c = df_h4.iloc[i]
    ts = str(c.name) if hasattr(c, 'name') else str(df_h4.index[len(df_h4)+i])
    print(f"{ts:<22} {c['open']:.10f} {c['high']:.10f} {c['low']:.10f} {c['close']:.10f}")

# Calculate EMA34, EMA89 using closed candles (same as bot)
indicators = TechnicalIndicators.get_all_indicators(
    df_h4,
    ema_fast=INDICATORS['ema_fast'],
    ema_slow=INDICATORS['ema_slow'],
    use_closed_candle=True
)

print(f"\n=== Current H4 indicators (closed candle) ===")
print(f"EMA34:  {indicators.ema34:.10f}")
print(f"EMA89:  {indicators.ema89:.10f}")
print(f"Price:  {indicators.current_price:.10f}")

# Check the candle the bot evaluated (iloc[-2] at that time = candle 04:00 UTC Feb 22)
# Now time has passed, so let's find it by timestamp
target_ts = "2026-02-22 04:00:00"
for i in range(len(df_h4)):
    ts = str(df_h4.index[i])
    if target_ts in ts:
        candle = df_h4.iloc[i]
        # Recalculate EMA at that point (using all candles up to and including that one)
        closed_up_to = df_h4['close'].iloc[:i+1]  # closed candles up to that point
        if len(closed_up_to) >= 89:
            ema34_at = float(TechnicalIndicators.calculate_ema(closed_up_to, 34).iloc[-1])
            ema89_at = float(TechnicalIndicators.calculate_ema(closed_up_to, 89).iloc[-1])

            o, h, l, c = candle['open'], candle['high'], candle['low'], candle['close']
            candle_range = h - l
            lower_wick = (min(o, c) - l)
            upper_wick = h - max(o, c)
            lower_wick_pct = (lower_wick / candle_range * 100) if candle_range > 0 else 0
            upper_wick_pct = (upper_wick / candle_range * 100) if candle_range > 0 else 0

            tolerance = 0.002
            touches_ema34 = l <= ema34_at * (1 + tolerance) and c > ema34_at
            touches_ema89 = l <= ema89_at * (1 + tolerance) and c > ema89_at

            is_bullish = TechnicalIndicators.is_bullish_rejection(o, h, l, c, threshold=40)

            print(f"\n=== Candle at {target_ts} (the one bot checked) ===")
            print(f"Open:   {o:.10f}")
            print(f"High:   {h:.10f}")
            print(f"Low:    {l:.10f}")
            print(f"Close:  {c:.10f}")
            print(f"Range:  {candle_range:.10f}")
            print(f"Lower wick: {lower_wick:.10f} ({lower_wick_pct:.1f}%)")
            print(f"Upper wick: {upper_wick:.10f} ({upper_wick_pct:.1f}%)")
            print(f"\nEMA34 at that time: {ema34_at:.10f}")
            print(f"EMA89 at that time: {ema89_at:.10f}")
            print(f"\nTouches EMA34? {touches_ema34} (low={l:.10f} <= {ema34_at*(1+tolerance):.10f} AND close={c:.10f} > {ema34_at:.10f})")
            print(f"Touches EMA89? {touches_ema89} (low={l:.10f} <= {ema89_at*(1+tolerance):.10f} AND close={c:.10f} > {ema89_at:.10f})")
            print(f"Bullish rejection (>=40%)? {is_bullish}")
            print(f"\n=== Verdict ===")
            if touches_ema89 and is_bullish:
                print("Signal VALID according to code logic")
            elif touches_ema34 and is_bullish:
                print("Signal VALID according to code logic (touches EMA34)")
            else:
                print("Signal should NOT have triggered!")
                if not touches_ema34 and not touches_ema89:
                    print("  -> Wick did NOT touch EMA34 or EMA89")
                if not is_bullish:
                    print("  -> Candle is NOT a bullish rejection")
        break
