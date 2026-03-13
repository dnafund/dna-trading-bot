import React, { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, LineSeries, ColorType, LineStyle, CrosshairMode, createSeriesMarkers } from 'lightweight-charts';

const COLORS = {
  bg: '#0a0a0f',
  grid: 'rgba(255,255,255,0.03)',
  text: 'rgba(255,255,255,0.5)',
  ema34: '#facc15',
  ema89: '#22d3ee',
  ema610: '#f97316',
  adx: '#38bdf8',
  chandelier: '#ef4444',
  rsi: '#facc15',
};

const ENTRY_TYPE_COLORS = {
  STANDARD_M5:  '#f472b6',  // pink
  STANDARD_M15: '#a78bfa',  // violet
  STANDARD_H1:  '#38bdf8',  // sky blue
  STANDARD_H4:  '#facc15',  // yellow
  EMA610_H1:    '#22c55e',  // green
  EMA610_H4:    '#f97316',  // orange
  RSI_DIV_M15:  '#ef4444',  // red
  RSI_DIV_H1:   '#dc2626',  // dark red
  RSI_DIV_H4:   '#b91c1c',  // darker red
  SD_DEMAND_M15:'#ff9800',  // orange
  SD_DEMAND_H1: '#ff6d00',  // dark orange
  SD_DEMAND_H4: '#e65100',  // darker orange
  SD_SUPPLY_M15:'#ff9800',
  SD_SUPPLY_H1: '#ff6d00',
  SD_SUPPLY_H4: '#e65100',
};

const ENTRY_TYPE_LABELS = {
  STANDARD_M5:  'STD M5',
  STANDARD_M15: 'STD M15',
  STANDARD_H1:  'STD H1',
  STANDARD_H4:  'STD H4',
  EMA610_H1:    'E610 H1',
  EMA610_H4:    'E610 H4',
  RSI_DIV_M15:  'DIV M15',
  RSI_DIV_H1:   'DIV H1',
  RSI_DIV_H4:   'DIV H4',
  SD_DEMAND_M15:'SD Dem M15',
  SD_DEMAND_H1: 'SD Dem H1',
  SD_DEMAND_H4: 'SD Dem H4',
  SD_SUPPLY_M15:'SD Sup M15',
  SD_SUPPLY_H1: 'SD Sup H1',
  SD_SUPPLY_H4: 'SD Sup H4',
};

function EntryTypeLegend({ trades }) {
  if (!trades || trades.length === 0) return null;

  // Count unique positions (not trades — 1 position may have TP1 + remaining close)
  const seen = new Set();
  const counts = {};
  for (const t of trades) {
    const et = t.entry_type || 'STANDARD_M15';
    const key = `${t.time}_${t.side}_${et}`;
    if (!seen.has(key)) {
      seen.add(key);
      counts[et] = (counts[et] || 0) + 1;
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-3">
      {Object.entries(ENTRY_TYPE_COLORS).map(([type, color]) => {
        const count = counts[type];
        if (!count) return null;
        return (
          <span key={type} className="text-[10px] font-mono font-medium" style={{ color }}>
            {ENTRY_TYPE_LABELS[type] || type} ({count})
          </span>
        );
      })}
    </div>
  );
}

export default function BacktestChart({ data }) {
  const mainRef = useRef(null);
  const rsiRef = useRef(null);
  const adxRef = useRef(null);
  const chartRef = useRef(null);
  const rsiChartRef = useRef(null);
  const adxChartRef = useRef(null);

  useEffect(() => {
    if (!data || !data.candles || !mainRef.current) return;

    // Cleanup previous charts
    if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
    if (rsiChartRef.current) { rsiChartRef.current.remove(); rsiChartRef.current = null; }
    if (adxChartRef.current) { adxChartRef.current.remove(); adxChartRef.current = null; }

    try {

    // ── Main Chart ──
    const mainWidth = Math.max(mainRef.current.clientWidth, 100);
    const chart = createChart(mainRef.current, {
      layout: { background: { type: ColorType.Solid, color: COLORS.bg }, textColor: COLORS.text, fontFamily: "'JetBrains Mono', monospace" },
      grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
      timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: true, secondsVisible: false },
      width: mainWidth,
      height: 500,
    });
    chartRef.current = chart;

    // Candlestick series
    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#22c55e',
      downColor: '#ef4444',
      borderDownColor: '#ef4444',
      borderUpColor: '#22c55e',
      wickDownColor: '#ef4444',
      wickUpColor: '#22c55e',
    });
    // Filter out candles with null OHLC values (lightweight-charts throws on null)
    const validCandles = data.candles.filter(
      c => c.open != null && c.high != null && c.low != null && c.close != null
    );
    candleSeries.setData(validCandles);

    // EMA + Chandelier line overlays
    const indicators = data.indicators || {};

    const addLine = (key, color, width = 1) => {
      if (!indicators[key] || indicators[key].length === 0) return;
      const line = chart.addSeries(LineSeries, {
        color,
        lineWidth: width,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData(indicators[key]);
    };

    addLine('ema34', COLORS.ema34, 1);
    addLine('ema89', COLORS.ema89, 1);
    addLine('ema610', COLORS.ema610, 2);
    addLine('chandelier_long', COLORS.chandelier, 1);

    // Trade markers — entry arrows + close markers with exit label
    if (data.trades && data.trades.length > 0) {
      const markers = [];

      const closeLabel = (ct) => {
        if (!ct) return '✕';
        const u = ct.toUpperCase();
        if (u.startsWith('TP1')) return 'TP1';
        if (u.startsWith('TP2')) return 'TP2';
        if (u.includes('CHANDELIER')) return 'CE';
        if (u.includes('HARD_SL')) return 'HSL';
        if (u === 'END_OF_BACKTEST') return 'END';
        return '✕';
      };

      // Deduplicate entry arrows: 1 position may have multiple trades
      // (e.g. TP1 partial + Hard SL remaining). Only draw 1 entry arrow per position.
      const seenEntries = new Set();

      for (const trade of data.trades) {
        const isLong = trade.side === 'BUY';
        const entryType = trade.entry_type || 'STANDARD_M15';
        const color = ENTRY_TYPE_COLORS[entryType] || '#ffffff';

        // Entry marker — only once per unique position (same time + side + entry_type)
        const entryKey = `${trade.time}_${trade.side}_${entryType}`;
        if (!seenEntries.has(entryKey)) {
          seenEntries.add(entryKey);
          markers.push({
            time: trade.time,
            position: isLong ? 'belowBar' : 'aboveBar',
            color,
            shape: isLong ? 'arrowUp' : 'arrowDown',
            text: '',
          });
        }

        // Close marker — circle with exit type label (always draw, one per trade)
        if (trade.close_time) {
          markers.push({
            time: trade.close_time,
            position: isLong ? 'aboveBar' : 'belowBar',
            color,
            shape: 'circle',
            text: closeLabel(trade.close_type),
          });
        }
      }

      markers.sort((a, b) => a.time - b.time);
      createSeriesMarkers(candleSeries, markers);
    }

    // ── RSI Chart (separate pane below) ──
    if (indicators.rsi && indicators.rsi.length > 0 && rsiRef.current) {
      const rsiChart = createChart(rsiRef.current, {
        layout: { background: { type: ColorType.Solid, color: COLORS.bg }, textColor: COLORS.text, fontFamily: "'JetBrains Mono', monospace" },
        grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
        rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)', scaleMargins: { top: 0.1, bottom: 0.1 } },
        timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: true, visible: true },
        width: rsiRef.current.clientWidth,
        height: 150,
      });
      rsiChartRef.current = rsiChart;

      const rsiLine = rsiChart.addSeries(LineSeries, {
        color: COLORS.rsi,
        lineWidth: 1.5,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      rsiLine.setData(indicators.rsi);

      // Overbought/Oversold horizontal lines
      const addHorizLine = (value, color) => {
        const line = rsiChart.addSeries(LineSeries, {
          color,
          lineWidth: 1,
          lineStyle: LineStyle.Dashed,
          priceLineVisible: false,
          lastValueVisible: false,
          crosshairMarkerVisible: false,
        });
        line.setData([
          { time: indicators.rsi[0].time, value },
          { time: indicators.rsi[indicators.rsi.length - 1].time, value },
        ]);
      };
      addHorizLine(70, 'rgba(239,68,68,0.5)');
      addHorizLine(30, 'rgba(34,197,94,0.5)');

      // Sync time scales (main ↔ RSI)
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (range) rsiChart.timeScale().setVisibleLogicalRange(range);
      });
      rsiChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (range) chart.timeScale().setVisibleLogicalRange(range);
      });
    }

    // ── ADX Chart (separate pane below RSI) ──
    if (indicators.adx && indicators.adx.length > 0 && adxRef.current) {
      const adxChart = createChart(adxRef.current, {
        layout: { background: { type: ColorType.Solid, color: COLORS.bg }, textColor: COLORS.text, fontFamily: "'JetBrains Mono', monospace" },
        grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
        rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)', scaleMargins: { top: 0.1, bottom: 0.1 } },
        timeScale: { borderColor: 'rgba(255,255,255,0.1)', timeVisible: true, visible: true },
        width: adxRef.current.clientWidth,
        height: 150,
      });
      adxChartRef.current = adxChart;

      const adxLine = adxChart.addSeries(LineSeries, {
        color: COLORS.adx,
        lineWidth: 1.5,
        priceLineVisible: false,
        lastValueVisible: true,
      });
      adxLine.setData(indicators.adx);

      // ADX threshold line (25 = trending market)
      const adxThreshold = adxChart.addSeries(LineSeries, {
        color: 'rgba(250,204,21,0.4)',
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      adxThreshold.setData([
        { time: indicators.adx[0].time, value: 25 },
        { time: indicators.adx[indicators.adx.length - 1].time, value: 25 },
      ]);

      // Sync time scales (main ↔ ADX)
      chart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (range) adxChart.timeScale().setVisibleLogicalRange(range);
      });
      adxChart.timeScale().subscribeVisibleLogicalRangeChange((range) => {
        if (range) chart.timeScale().setVisibleLogicalRange(range);
      });
    }

    // Resize handler
    const handleResize = () => {
      if (mainRef.current && chartRef.current) chartRef.current.applyOptions({ width: mainRef.current.clientWidth });
      if (rsiRef.current && rsiChartRef.current) rsiChartRef.current.applyOptions({ width: rsiRef.current.clientWidth });
      if (adxRef.current && adxChartRef.current) adxChartRef.current.applyOptions({ width: adxRef.current.clientWidth });
    };
    window.addEventListener('resize', handleResize);
    chart.timeScale().fitContent();

    } catch (err) {
      console.error('[BacktestChart] render error:', err);
      if (mainRef.current) {
        mainRef.current.innerHTML = `<div style="padding:16px;color:#f87171;font-family:monospace;font-size:12px;white-space:pre-wrap;">Chart error: ${err.message}\n${err.stack}</div>`;
      }
    }

    return () => {
      if (chartRef.current) { chartRef.current.remove(); chartRef.current = null; }
      if (rsiChartRef.current) { rsiChartRef.current.remove(); rsiChartRef.current = null; }
      if (adxChartRef.current) { adxChartRef.current.remove(); adxChartRef.current = null; }
    };
  }, [data]);

  if (!data || !data.candles) {
    return (
      <div className="glass-card rounded-2xl p-8 flex items-center justify-center h-[650px] border border-white/10">
        <p className="text-text-muted text-sm">Run a backtest to see the chart</p>
      </div>
    );
  }

  return (
    <div className="glass-card rounded-2xl overflow-hidden border border-white/10">
      <div className="px-4 py-3 border-b border-white/5 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <span className="text-xs text-text-muted font-mono uppercase tracking-wider">
            Price Chart — {data.timeframe || '15m'}
          </span>
          <EntryTypeLegend trades={data.trades} />
        </div>
        <span className="text-xs text-text-dim font-mono">{data.candles.length.toLocaleString()} candles</span>
      </div>
      <div ref={mainRef} />
      {data.indicators?.rsi && (
        <>
          <div className="px-4 py-2 border-t border-white/5">
            <span className="text-xs text-text-muted font-mono uppercase tracking-wider">RSI (14)</span>
          </div>
          <div ref={rsiRef} />
        </>
      )}
      {data.indicators?.adx && (
        <>
          <div className="px-4 py-2 border-t border-white/5">
            <span className="text-xs text-text-muted font-mono uppercase tracking-wider">ADX (14)</span>
          </div>
          <div ref={adxRef} />
        </>
      )}
    </div>
  );
}
