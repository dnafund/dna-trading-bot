# Futures Trading Strategy V7.2 - Reference

## Overview

| Parameter | Value |
|---|---|
| **Version** | V7.2 |
| **Margin** | Fixed $2,000/trade |
| **Leverage** | BTC 20x, ETH/SOL/XRP/BNB 10x, Mid-cap 7x, Default 5x |
| **Min Balance** | $200 to open new trades |
| **Fees** | Maker 0.02% (limit), Taker 0.05% (stop-market) |
| **Data** | Binance Futures OHLCV |
| **Timeframes** | M15 (simulation loop), H1 (filter + EMA610), H4 (trend + EMA610) |
| **Warmup** | 120 days (EMA610 on H4 needs 610 candles) |

---

## Entry Types

### Entry 1: Standard (Multi-Timeframe)

Quy trinh: H4 Trend -> H1 RSI Filter -> H1+H4 Divergence Check -> M15 Wick Entry

#### Step 1: H4 Trend Detection
- Tinh EMA34 va EMA89 tren H4
- **BUY_TREND**: EMA34 > EMA89 **VA** price > EMA89
- **SELL_TREND**: EMA34 < EMA89 **VA** price < EMA89
- Khong co trend ro rang -> bo qua, khong vao lenh

#### Step 2: H1 RSI Filter
- Tinh RSI(14) tren H1
- **BUY_TREND** + RSI >= 70 (overbought) -> **BLOCK** (khong vao BUY)
- **SELL_TREND** + RSI <= 30 (oversold) -> **BLOCK** (khong vao SELL)

#### Step 3: H1 + H4 Divergence Check
- Detect RSI divergence tren ca H1 (lookback 160 candles ~6.7 ngay) va H4 (lookback 80 candles ~13.3 ngay)
- Swing window = 2 (5-bar fractal), min swing distance = 5 bars
- Neu co divergence **chong lai** huong trade -> **BLOCK**
  - VD: Muon BUY nhung co bearish divergence tren H1 hoac H4 -> khong vao

#### Step 4: M15 Entry Signal
- Price phai **gan EMA34 hoac EMA89** tren M15 (tolerance +-0.2%)
- Nen M15 vua dong phai co **wick dai**:
  - **BUY**: lower wick >= 40% cua candle range (high - low)
  - **SELL**: upper wick >= 40% cua candle range
- Entry price = close cua nen M15 vua dong
- Lenh su dung **limit order** (maker fee 0.02%)

#### Standard TP/SL

| Level | Logic | Fee |
|---|---|---|
| **TP1** | Entry +/- 1x ATR(14) M15, dong **70%** volume | Maker 0.02% |
| **TP2** | Entry +/- 3x ATR(14) M15, dong **30%** con lai | Maker 0.02% |
| **Chandelier SL** | Trailing SL tren M15, period=22, multiplier=2.0 | Maker 0.02% |
| **Smart SL** | Khi Chandelier trigger: neu volume < 20% avg(21) -> **cho tho**, KHONG cat ngay | - |
| **EMA200 Break** | Dang breathing + price close qua EMA200 M15 -> cat ngay | Maker 0.02% |
| **Hard SL** | -20% ROI (safety net, luon dat san) | Taker 0.05% |

**Chandelier Exit M15 chi tiet:**
- BUY: SL = max(chandelier_long hien tai, chandelier_long truoc do) -> chi tang, khong giam
- SELL: SL = min(chandelier_short hien tai, chandelier_short truoc do) -> chi giam, khong tang
- Khi price cham chandelier SL -> kiem tra Smart SL truoc khi dong

**Smart SL chi tiet:**
- Chandelier trigger nhung volume < 20% cua SMA(volume, 21) -> **breathing room** (giu lenh)
- Neu dang breathing MA price close duoi EMA200 (BUY) hoac tren EMA200 (SELL) -> dong ngay (EMA200_BREAK_SL)

#### Standard Pyramiding
- Khi lenh truoc **dat TP1** -> co the mo lenh moi neu co signal hop le
- Lenh moi phai thoa man **day du** dieu kien: H4 trend + H1 RSI + Divergence + M15 wick
- **Khong gioi han** so lenh pyramiding
- Khi mo lenh moi -> reset `std_last_tp1 = False` cho den khi lenh nay dat TP1
- Cooldown: khong mo lenh moi cung nen M15 voi lenh vua dong

---

### Entry 2: EMA610 (Mean-Reversion)

Chay **dong thoi** tren ca H1 va H4, moi khung thoi gian co slot rieng.

#### Dieu kien vao lenh
1. **H4 trend dua vao EMA610 H4** (KHAC voi Standard entry):
   - Tinh EMA34, EMA89, EMA610 tren khung H4
   - **BUY**: EMA34 > EMA610 **VA** EMA89 > EMA610 (ca 2 EMA nam TREN EMA610)
   - **SELL**: EMA34 < EMA610 **VA** EMA89 < EMA610 (ca 2 EMA nam DUOI EMA610)
   - **Sideways**: Mot EMA tren, mot EMA duoi -> **KHONG VAO LENH**
2. **Price cham EMA610** tren H1 hoac H4 (tolerance +-0.2%):
   - Tinh `ema_upper = EMA610 * 1.002`, `ema_lower = EMA610 * 0.998`
   - Nen hien tai: `candle_low <= ema_upper` VA `candle_high >= ema_lower`
   - -> Price da di vao vung EMA610
3. **Khong can cho nen dong** - check intra-candle (high/low)
4. **Side theo H4 trend**: H4 = BUY -> vao BUY, H4 = SELL -> vao SELL
5. **Entry price = gia tri EMA610** (limit order dat san tai EMA610)
6. Lenh su dung **limit order** (maker fee 0.02%)

#### Dedup (chong trung lap)
- Moi nen H1/H4 chi duoc trigger **1 lan entry**
- Track `last_ema610_h1_entry_candle_ts` va `last_ema610_h4_entry_candle_ts`
- Neu nen H1/H4 hien tai == nen da entry truoc do -> **SKIP**
- Dam bao pyramiding chi xay ra tren nen MOI, khong phai cung 1 nen check nhieu lan qua M15

#### EMA610 TP/SL

| Level | H1 | H4 | Fee |
|---|---|---|---|
| **TP1** | +40% ROI, dong **70%** | +40% ROI, dong **70%** | Maker 0.02% |
| **TP2** | +80% ROI, dong **30%** | +80% ROI, dong **30%** | Maker 0.02% |
| **Chandelier SL** | Period=22, Mult=2.0 **tren H1** | Period=22, Mult=2.0 **tren H4** | Maker 0.02% |
| **Hard SL** | **-30% ROI** | **-50% ROI** | Taker 0.05% |

**Luu y quan trong:**
- Chandelier Exit chay tren **khung entry** (H1 dung chandelier H1, H4 dung chandelier H4)
- **KHONG co Smart SL** cho EMA610 (khong co breathing room)
- Hard SL H4 rong hon (-50%) vi H4 bien dong lon hon
- Exit check chay moi khi co du lieu H1/H4 moi (khong phai moi nen M15)

#### EMA610 Pyramiding
- Khi lenh truoc **dat TP1** -> co the mo lenh moi neu:
  1. Price van cham EMA610 +-0.2%
  2. Nen H1/H4 hien tai **KHAC** nen da entry truoc do (dedup)
- TP/SL cho lenh moi **giong het** lenh cu
- H1 va H4 chay **doc lap** (co the co 1 lenh H1 + 1 lenh H4 cung luc)

---

## Fee Structure

| Hanh dong | Loai lenh | Fee |
|---|---|---|
| **Mo lenh** (Standard + EMA610) | Limit order | **0.02%** (maker) |
| **Dong TP1, TP2** | Limit order | **0.02%** (maker) |
| **Dong Chandelier SL** | Limit order | **0.02%** (maker) |
| **Dong Hard SL** | Stop-market order | **0.05%** (taker) |
| **Force close** (cuoi backtest) | Market order | **0.05%** (taker) |

**Cach tinh fee:**
```
position_value = margin * leverage
entry_fee = position_value * 0.0002
exit_fee = position_value * fee_rate  (0.0002 hoac 0.0005)
total_fee = entry_fee + exit_fee
PNL_after_fee = PNL_raw - total_fee
```

**Vi du BTC 20x, margin $2,000:**
- Position value = $40,000
- Entry fee = $40,000 * 0.02% = $8
- Exit limit fee = $8 -> total $16/trade
- Exit taker fee = $40,000 * 0.05% = $20 -> total $28/trade (hard SL)

---

## Indicators Pre-computed

### M15
| Indicator | Config |
|---|---|
| ATR(14) | Cho TP1/TP2 calculation |
| Chandelier Exit | Period=22, Mult=2.0 (long + short) |
| EMA200 | Smart SL safety check |
| Volume SMA(21) | Smart SL breathing threshold |
| EMA34, EMA89 | Entry signal (price near EMA) |

### H1
| Indicator | Config |
|---|---|
| EMA610 | Mean-reversion entry |
| Chandelier Exit | Period=22, Mult=2.0 (EMA610 H1 SL) |
| EMA34, EMA89 | Trend support |
| RSI(14) | Via get_all_indicators() |

### H4
| Indicator | Config |
|---|---|
| EMA610 | Mean-reversion entry |
| Chandelier Exit | Period=22, Mult=2.0 (EMA610 H4 SL) |
| EMA34, EMA89 | Trend detection |

---

## Simulation Flow (moi nen M15)

```
FOR moi nen M15 trong range:
  |
  |-- 1. LAY GIA TRI INDICATORS
  |     M15: chandelier, EMA200, volume, ATR
  |     H1: close, high, low, chandelier (nen H1 gan nhat)
  |     H4: close, high, low, chandelier (nen H4 gan nhat)
  |
  |-- 2. XU LY EXIT (theo thu tu uu tien)
  |     |
  |     |-- Standard positions:
  |     |     Hard SL -> Chandelier+Smart SL -> TP1 (70%) -> TP2 (30%)
  |     |
  |     |-- EMA610 H1 positions:
  |     |     Hard SL -30% -> Chandelier H1 -> TP1 ROI 40% -> TP2 ROI 80%
  |     |
  |     |-- EMA610 H4 positions:
  |           Hard SL -50% -> Chandelier H4 -> TP1 ROI 40% -> TP2 ROI 80%
  |
  |-- 3. DETECT H4 TREND (dung chung cho Standard va EMA610)
  |     EMA34 vs EMA89 tren H4
  |
  |-- 4. ENTRY: EMA610 H1
  |     can_open = (khong co lenh HOAC lenh cuoi dat TP1) VA (nen H1 khac nen da entry)
  |     -> check touch EMA610 +-0.2% tren H1
  |     -> mo limit order tai EMA610
  |
  |-- 5. ENTRY: EMA610 H4
  |     can_open = (khong co lenh HOAC lenh cuoi dat TP1) VA (nen H4 khac nen da entry)
  |     -> check touch EMA610 +-0.2% tren H4
  |     -> mo limit order tai EMA610
  |
  |-- 6. ENTRY: Standard
  |     can_open = (khong co lenh active HOAC lenh cuoi dat TP1)
  |     -> H4 trend + H1 RSI + Divergence + M15 wick
  |     -> mo limit order tai M15 close
  |
  NEXT nen M15
```

---

## Position Management

| Entry Type | Slot | Max dong thoi | Pyramiding |
|---|---|---|---|
| **Standard** | Chung | Khong gioi han | Sau TP1 + co signal moi |
| **EMA610 H1** | Rieng | 1 active (+ pyramid sau TP1) | Sau TP1 + nen H1 moi cham EMA610 |
| **EMA610 H4** | Rieng | 1 active (+ pyramid sau TP1) | Sau TP1 + nen H4 moi cham EMA610 |

**3 loai position chay song song va doc lap:**
- Standard, EMA610_H1, EMA610_H4 khong anh huong lan nhau
- Co the co dong thoi: N lenh Standard + 1 lenh EMA610_H1 + 1 lenh EMA610_H4

---

## Thay doi so voi V7

| Feature | V7 | V7.2 |
|---|---|---|
| **Entry 3 (Pattern)** | Co (Hammer, Shooting Star, Morning/Evening Star) | **DA BO** |
| **EMA610 timeframe** | Chi H1 | **H1 + H4 dong thoi** |
| **EMA610 entry** | Cho nen dong | **Khong cho** - check intra-candle, limit order |
| **EMA610 TP** | Dung chung voi Standard (ATR-based) | **ROI-based**: TP1 +40%, TP2 +80% |
| **EMA610 SL** | Chandelier M15 + Smart SL | **Chandelier tren khung entry** (H1/H4), khong Smart SL |
| **EMA610 Hard SL** | -20% ROI | **H1: -30%, H4: -50%** |
| **EMA610 Pyramiding** | Khong | **Co** - sau TP1, nen moi cham EMA610 |
| **Standard Pyramiding** | Khong | **Co** - sau TP1, co signal moi (khong gioi han) |
| **Fees** | Khong tinh | **Co**: Maker 0.02%, Taker 0.05% |
| **PNL** | Truoc fee | **Sau fee** |
| **Stats** | Chung | **Tach theo entry type** (STANDARD, EMA610_H1, EMA610_H4) |
| **Warmup** | 60 days | **120 days** (EMA610 H4 can nhieu data hon) |

---

## Backtest Result V7.2 (BTCUSDT, 2025-01-01 -> 2026-01-31)

| Metric | Value |
|---|---|
| Total Trades | 2,154 |
| Win Rate | 65.5% |
| Total PNL (after fees) | +$162,061 |
| Total Fees | $30,860 |
| Profit Factor | 3.54 |
| Risk/Reward | 1.86 |
| Max Drawdown | $2,569 |
| Starting Balance | $10,000 |
| Final Balance | $172,061 (+1,620.6%) |

### Entry Type Breakdown

| Type | Trades | WR | PNL | Fees | Avg PNL |
|---|---|---|---|---|---|
| **EMA610_H1** | 259 | 80.3% | +$98,695 | $4,180 | +$381 |
| **EMA610_H4** | 67 | 47.8% | +$8,376 | $1,144 | +$125 |
| **STANDARD** | 1,828 | 64.1% | +$54,990 | $25,536 | +$30 |

### Close Type Breakdown

| Type | Trades | Avg PNL | Total |
|---|---|---|---|
| CHANDELIER_SL (Standard M15) | 1,511 | +$29.64 | +$44,784 |
| CHANDELIER_H1 (EMA610 H1) | 256 | +$392.89 | +$100,579 |
| CHANDELIER_H4 (EMA610 H4) | 61 | +$238.42 | +$14,544 |
| TP1_ATR | 244 | +$50.93 | +$12,427 |
| TP2_ATR | 57 | +$81.17 | +$4,627 |
| HARD_SL (-20%) | 16 | -$428 | -$6,848 |
| HARD_SL_30PCT (EMA610 H1) | 3 | -$628 | -$1,884 |
| HARD_SL_50PCT (EMA610 H4) | 6 | -$1,028 | -$6,168 |

### SL Breakdown

| SL Type | Trades | Avg PNL | Total |
|---|---|---|---|
| CHANDELIER_SL (normal) | 1,508 | +$29.52 | +$44,515 |
| EMA200_BREAK_SL | 3 | +$89.91 | +$270 |
| HARD_SL (Standard) | 16 | -$428 | -$6,848 |
| HARD_SL_30PCT (EMA610 H1) | 3 | -$628 | -$1,884 |
| HARD_SL_50PCT (EMA610 H4) | 6 | -$1,028 | -$6,168 |
