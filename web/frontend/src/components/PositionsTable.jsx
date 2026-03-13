import React, { useState, useEffect, useRef } from 'react';
import { ArrowUpRight, ArrowDownRight, Activity, ChevronDown, ChevronUp, Clock, Target, Shield, Zap } from 'lucide-react';
import CryptoIcon from './CryptoIcon';
import PositionActions from './PositionActions';
import { cn } from '../utils/cn';

function formatPrice(price) {
  if (price == null) return '—';
  const num = Number(price);
  if (num === 0) return '0';
  if (num >= 1000) return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (num >= 1) return num.toFixed(4);
  // Small prices (PEPE etc): count leading zeros, show 4 significant digits after
  const str = num.toFixed(20);
  const afterDot = str.split('.')[1] || '';
  let leadingZeros = 0;
  for (const ch of afterDot) { if (ch === '0') leadingZeros++; else break; }
  return num.toFixed(Math.min(leadingZeros + 4, 18));
}

function PnlBadge({ value, isPercent }) {
  if (value == null) return <span className="text-white/20">—</span>;
  const num = Number(value);
  const isProfit = num >= 0;

  return (
    <div className={cn(
      "flex items-center gap-1 font-mono font-medium",
      isProfit ? "text-emerald-400" : "text-rose-400"
    )}>
      {isProfit ? <ArrowUpRight className="w-3 h-3 flex-shrink-0" /> : <ArrowDownRight className="w-3 h-3 flex-shrink-0" />}
      {isProfit ? '+' : ''}{isPercent ? num.toFixed(2) + '%' : '$' + num.toFixed(2)}
    </div>
  );
}

function EntryTypeBadge({ type }) {
  const styles = {
    standard: "bg-blue-500/10 text-blue-400 border-blue-500/20",
    ema610_h1: "bg-purple-500/10 text-purple-400 border-purple-500/20",
    ema610_h4: "bg-amber-500/10 text-amber-400 border-amber-500/20",
    rsi_div: "bg-rose-500/10 text-rose-400 border-rose-500/20",
    sd_demand: "bg-orange-500/10 text-orange-400 border-orange-500/20",
    sd_supply: "bg-orange-500/10 text-orange-400 border-orange-500/20",
    default: "bg-white/5 text-white/40 border-white/10"
  };

  const prefix = type?.split('_').slice(0, type.startsWith('rsi_div') || type.startsWith('sd_demand') || type.startsWith('sd_supply') ? 2 : 1).join('_');
  return (
    <span className={cn(
      "text-[10px] px-2 py-1 rounded-md border font-medium uppercase tracking-wider",
      styles[type] || styles[prefix] || styles.default
    )}>
      {type?.replace('_', ' ') || 'MANUAL'}
    </span>
  );
}

function PositionRow({ pos, onAction, isHighlighted, onViewed }) {
  const [expanded, setExpanded] = useState(false);
  const rowRef = useRef(null);
  const isLong = pos.side === 'BUY';

  useEffect(() => {
    if (isHighlighted) {
      setExpanded(true);
      setTimeout(() => {
        rowRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 100);
      const timer = setTimeout(() => onViewed?.(), 2000);
      return () => clearTimeout(timer);
    }
  }, [isHighlighted]);

  return (
    <>
      <tr
        ref={rowRef}
        onClick={() => setExpanded(!expanded)}
        className={cn(
          "group transition-all cursor-pointer border-b border-white/5",
          isHighlighted ? "bg-white/[0.06] ring-1 ring-inset ring-white/20" :
          expanded ? "bg-white/[0.03]" : "hover:bg-white/5 hover:shadow-[inset_3px_0_0_rgba(255,255,255,0.2)]"
        )}
      >
        <td className="px-4 py-4">
          <div className="flex items-center gap-3">
            <CryptoIcon symbol={pos.symbol} className="w-8 h-8 rounded-full bg-white/5 p-0.5" />
            <div>
              <div className="font-bold text-white text-sm flex items-center gap-2">
                {pos.symbol?.replace('USDT', '')}
                <ChevronDown className={cn("w-3 h-3 text-white/30 transition-transform", expanded && "rotate-180")} />
              </div>
              <div className="flex items-center gap-1 mt-0.5">
                <span className="text-[10px] text-white/30">USDT-M</span>
                {pos.tp1_closed && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">TP1 ✓</span>}
                {pos.tp2_closed && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">TP2 ✓</span>}
              </div>
            </div>
          </div>
        </td>

        <td className="px-4 py-4">
          <span className={cn(
            "text-xs font-bold px-2 py-1 rounded",
            isLong ? "bg-emerald-500/10 text-emerald-500" : "bg-rose-500/10 text-rose-500"
          )}>
            {isLong ? 'LONG' : 'SHORT'}
          </span>
        </td>

        <td className="px-4 py-4 hidden md:table-cell">
          <EntryTypeBadge type={pos.entry_type} />
        </td>

        <td className="px-4 py-4 text-right font-mono text-sm text-white/70">
          {formatPrice(pos.entry_price)}
        </td>

        <td className="px-4 py-4 text-right font-mono text-sm text-white hidden md:table-cell">
          {formatPrice(pos.current_price)}
        </td>

        <td className="px-4 py-4 text-right">
          <PnlBadge value={pos.pnl_usd} isPercent={false} />
        </td>

        <td className="px-4 py-4 text-right hidden lg:table-cell">
          <PnlBadge value={pos.roi_percent} isPercent={true} />
        </td>

        <td className="px-4 py-4 text-right hidden lg:table-cell">
          <div className="flex flex-col items-end gap-1">
            <div className="flex items-center gap-1">
              {pos.tp1_closed && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">TP1 ✓</span>}
              {pos.tp2_closed && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">TP2 ✓</span>}
              <span className="text-[10px] text-emerald-400 bg-emerald-400/10 px-1.5 py-0.5 rounded">TP: {pos.take_profit_1 ? formatPrice(pos.take_profit_1) : '—'}</span>
            </div>
            <span className={`text-[10px] ${pos.trailing_sl ? 'text-amber-400 bg-amber-400/10' : 'text-rose-400 bg-rose-400/10'} px-1.5 py-0.5 rounded`}>SL: {pos.trailing_sl ? formatPrice(pos.trailing_sl) : pos.stop_loss ? formatPrice(pos.stop_loss) : '—'}</span>
          </div>
        </td>
      </tr>

      {/* Expanded Detail Row */}
      {expanded && (
        <tr className="bg-white/[0.02]">
          <td colSpan={8} className="px-4 py-4 shadow-inner">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-6 animate-fade-in-down">

              {/* Entry Details */}
              <div className="space-y-3">
                <h4 className="flex items-center gap-2 text-xs font-bold text-white/40 uppercase tracking-wider">
                  <Clock className="w-3 h-3" /> Entry Details
                </h4>
                <div className="space-y-1">
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Entry Price</span>
                    <span className="font-mono text-white">{formatPrice(pos.entry_price)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Time</span>
                    <span className="font-mono text-white">{pos.timestamp ? new Date(pos.timestamp).toLocaleString() : '—'}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Size</span>
                    <span className="font-mono text-white">{pos.size} {pos.symbol?.replace('USDT', '')}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Leverage</span>
                    <span className="font-mono text-amber-400">{pos.leverage}x</span>
                  </div>
                </div>
              </div>

              {/* Take Profit Targets */}
              <div className="space-y-3">
                <h4 className="flex items-center gap-2 text-xs font-bold text-emerald-500/60 uppercase tracking-wider">
                  <Target className="w-3 h-3" /> Take Profits
                </h4>
                <div className="space-y-1">
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">TP 1 (Target)</span>
                    <span className={cn("font-mono", pos.take_profit_1 ? "text-emerald-400" : "text-white/20")}>
                      {formatPrice(pos.take_profit_1)}
                    </span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">TP 2 (Extension)</span>
                    <span className={cn("font-mono", pos.take_profit_2 ? "text-emerald-400" : "text-white/20")}>
                      {formatPrice(pos.take_profit_2)}
                    </span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Status</span>
                    <span className="font-mono text-white/60">
                      {pos.tp1_closed ? 'TP1 Hit' : 'Open'}
                    </span>
                  </div>
                </div>
              </div>

              {/* Stop Loss & Risk */}
              <div className="space-y-3">
                <h4 className="flex items-center gap-2 text-xs font-bold text-rose-500/60 uppercase tracking-wider">
                  <Shield className="w-3 h-3" /> Stop Loss
                </h4>
                <div className="space-y-1">
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Hard SL</span>
                    <span className="font-mono text-rose-400">{formatPrice(pos.stop_loss)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Trailing SL</span>
                    <span className="font-mono text-amber-400">{formatPrice(pos.trailing_sl)}</span>
                  </div>
                  {pos.ce_order_id && (
                    <div className="flex justify-between text-sm">
                      <span className="text-white/40">CE Order</span>
                      <span className="font-mono text-cyan-400 text-xs">
                        {pos.side === 'BUY' ? 'SELL' : 'BUY'} STOP @ {formatPrice(pos.ce_order_price)}
                      </span>
                    </div>
                  )}
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Margin</span>
                    <span className="font-mono text-white">
                      {(() => {
                        const currentMargin = (pos.size && pos.remaining_size && pos.size > 0)
                          ? pos.margin * (pos.remaining_size / pos.size)
                          : pos.margin;
                        const isReduced = pos.remaining_size && pos.size && pos.remaining_size < pos.size * 0.99;
                        return (
                          <>
                            ${currentMargin?.toFixed(2)}
                            {isReduced && (
                              <span className="text-white/20 text-[10px] ml-1">
                                / ${pos.margin?.toFixed(0)}
                              </span>
                            )}
                          </>
                        );
                      })()}
                    </span>
                  </div>
                </div>
              </div>

              {/* Technicals (Chandelier) */}
              <div className="space-y-3">
                <h4 className="flex items-center gap-2 text-xs font-bold text-blue-500/60 uppercase tracking-wider">
                  <Zap className="w-3 h-3" /> Indicators
                </h4>
                <div className="space-y-1">
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Chandelier</span>
                    <span className="font-mono text-blue-400">{formatPrice(pos.chandelier_sl)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Entry Strategy</span>
                    <span className="font-mono text-white/60 text-xs">{pos.entry_type}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Trend</span>
                    <span className="font-mono text-white/60">{isLong ? 'Bullish' : 'Bearish'}</span>
                  </div>
                </div>
              </div>

            </div>

            {/* Action Buttons */}
            <div className="mt-5 pt-4 border-t border-white/5">
              <h4 className="flex items-center gap-2 text-xs font-bold text-white/40 uppercase tracking-wider mb-3">
                <Zap className="w-3 h-3" /> Actions
              </h4>
              {onAction ? (
                <PositionActions position={pos} onAction={onAction} />
              ) : (
                <p className="text-xs text-white/20">Go to Active Positions page for full controls</p>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function PositionsTable({ positions, onAction, highlightId, onPositionViewed }) {
  if (!positions || positions.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-20 gap-3">
        <div className="w-14 h-14 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center">
          <Activity className="w-7 h-7 text-white/20" />
        </div>
        <p className="text-sm text-white/30">No active positions</p>
        <p className="text-xs text-white/15">New positions will appear here in real-time</p>
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left border-collapse">
        <thead>
          <tr className="text-xs text-white/40 uppercase tracking-wider border-b border-white/5">
            <th className="px-4 py-4 font-medium">Symbol</th>
            <th className="px-4 py-4 font-medium">Side</th>
            <th className="px-4 py-4 font-medium hidden md:table-cell">Strategy</th>
            <th className="px-4 py-4 font-medium text-right">Entry</th>
            <th className="px-4 py-4 font-medium text-right hidden md:table-cell">Mark</th>
            <th className="px-4 py-4 font-medium text-right">PnL (uPnL)</th>
            <th className="px-4 py-4 font-medium text-right hidden lg:table-cell">ROI</th>
            <th className="px-4 py-4 font-medium text-right hidden lg:table-cell">TP / SL</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((pos) => (
            <PositionRow
              key={pos.position_id}
              pos={pos}
              onAction={onAction}
              isHighlighted={highlightId === pos.position_id}
              onViewed={onPositionViewed}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
