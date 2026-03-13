import { useState, useEffect, useCallback } from 'react';
import { Clock, Filter, ChevronLeft, ChevronRight, ChevronDown, ChevronUp, Search, Target, Shield, Zap } from 'lucide-react';
import CryptoIcon from '../components/CryptoIcon';
import PerformanceAnalysis from '../components/PerformanceAnalysis';
import { TableRowSkeleton } from '../components/Skeleton';
import { cn } from '../utils/cn';

function formatPrice(price) {
  if (price == null) return '—';
  const num = Number(price);
  if (num >= 1000) return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (num >= 1) return num.toFixed(4);
  return num.toFixed(6);
}

function formatDuration(entry, close) {
  if (!entry || !close) return '—';
  const ms = new Date(close) - new Date(entry);
  const hours = Math.floor(ms / 3600000);
  const mins = Math.floor((ms % 3600000) / 60000);
  if (hours > 24) return `${Math.floor(hours / 24)}d ${hours % 24}h`;
  return `${hours}h ${mins}m`;
}

function formatTime(iso) {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleString();
  } catch { return '—'; }
}

/** Sortable column header with arrow indicator */
function SortHeader({ label, field, sortBy, sortOrder, onSort, className = '' }) {
  const isActive = sortBy === field;

  return (
    <th
      className={cn("px-6 py-4 font-medium cursor-pointer select-none group/sort hover:text-white/60 transition-colors", className)}
      onClick={() => onSort(field)}
    >
      <div className={cn("flex items-center gap-1", className.includes('text-right') && "justify-end")}>
        {label}
        <span className={cn("flex flex-col -space-y-1 transition-opacity", isActive ? "opacity-100" : "opacity-0 group-hover/sort:opacity-40")}>
          <ChevronUp className={cn("w-3 h-3", isActive && sortOrder === 'asc' ? "text-primary" : "text-white/30")} />
          <ChevronDown className={cn("w-3 h-3", isActive && sortOrder === 'desc' ? "text-primary" : "text-white/30")} />
        </span>
      </div>
    </th>
  );
}

/** Single trade row with expand/collapse */
function TradeRow({ trade, expanded, onToggle }) {
  const pnl = Number(trade.pnl_usd || 0);
  const roi = Number(trade.roi_percent || 0);
  const isWin = pnl >= 0;

  return (
    <>
      <tr
        onClick={onToggle}
        className={cn(
          "transition-all cursor-pointer border-b border-white/5",
          expanded ? "bg-white/[0.03]" : "hover:bg-white/5 hover:shadow-[inset_3px_0_0_rgba(255,255,255,0.2)]"
        )}
      >
        <td className="px-6 py-4 font-medium text-white group-hover:text-primary transition-colors">
          <div className="flex items-center gap-3">
            <CryptoIcon symbol={trade.symbol} className="w-6 h-6 rounded-full bg-white/5 p-0.5" />
            <div>
              <div className="flex items-center gap-2">
                <span>{(trade.symbol || '').replace('USDT', '')}</span>
                <ChevronDown className={cn("w-3 h-3 text-white/30 transition-transform", expanded && "rotate-180")} />
              </div>
              <div className="flex items-center gap-1 mt-0.5">
                {trade.status === 'PARTIAL_CLOSE' && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-blue-500/20 text-blue-400">PARTIAL</span>}
                {(trade.tp1_closed || /TP1|TP2|TP2_ROI/.test(trade.close_reason || '')) && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">TP1✓</span>}
                {(trade.tp2_closed || /TP2|TP2_ROI/.test(trade.close_reason || '')) && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">TP2✓</span>}
              </div>
            </div>
          </div>
        </td>
        <td className="px-6 py-4">
          <span className={cn("text-xs font-bold px-2 py-0.5 rounded", trade.side === 'BUY' ? "bg-emerald-500/10 text-emerald-500" : "bg-rose-500/10 text-rose-500")}>
            {trade.side === 'BUY' ? 'LONG' : 'SHORT'}
          </span>
        </td>
        <td className="px-6 py-4 hidden md:table-cell">
          <span className="text-[10px] font-mono opacity-50 border border-white/10 px-1 rounded">{trade.entry_type}</span>
        </td>
        <td className="px-6 py-4 text-right font-mono text-white/60 hidden md:table-cell">{formatPrice(trade.entry_price)}</td>
        <td className="px-6 py-4 text-right font-mono text-white hidden md:table-cell">{formatPrice(trade.current_price)}</td>
        <td className={cn("px-6 py-4 text-right font-mono font-medium", isWin ? "text-emerald-400" : "text-rose-400")}>
          {isWin ? '+' : ''}${pnl.toFixed(2)}
        </td>
        <td className={cn("px-6 py-4 text-right font-mono font-medium", isWin ? "text-emerald-400" : "text-rose-400")}>
          {isWin ? '+' : ''}{roi.toFixed(1)}%
        </td>
        <td className="px-6 py-4 text-xs max-w-[150px] hidden lg:table-cell">
          {trade.close_reason ? (
            <span className={cn(
              "text-[10px] font-medium px-1.5 py-0.5 rounded relative group/tip cursor-default",
              /TP/i.test(trade.close_reason) ? "bg-emerald-500/10 text-emerald-400" :
              /CHANDELIER/i.test(trade.close_reason) ? "bg-amber-500/10 text-amber-400" :
              /HARD_SL/i.test(trade.close_reason) ? "bg-rose-500/10 text-rose-400" :
              /TRAIL/i.test(trade.close_reason) ? "bg-amber-500/10 text-amber-400" :
              "bg-white/5 text-white/40"
            )}>
              {trade.close_reason}
              {trade.close_reason.length > 20 && (
                <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 rounded-lg bg-black/90 border border-white/10 text-xs text-white whitespace-nowrap opacity-0 group-hover/tip:opacity-100 transition-opacity pointer-events-none z-50 shadow-xl">
                  {trade.close_reason}
                </span>
              )}
            </span>
          ) : (
            <span className="text-white/20">—</span>
          )}
        </td>
        <td className="px-6 py-4 text-right text-xs text-white/30 font-mono hidden lg:table-cell">
          {formatDuration(trade.timestamp, trade.close_time)}
        </td>
      </tr>

      {/* Expanded Detail Row */}
      {expanded && (
        <tr className="bg-white/[0.02] border-b border-white/5">
          <td colSpan={9} className="px-6 py-5 shadow-inner">
            <div className="grid grid-cols-1 md:grid-cols-4 gap-6 animate-fade-in-down">

              {/* Entry Details */}
              <div className="space-y-3">
                <h4 className="flex items-center gap-2 text-xs font-bold text-white/40 uppercase tracking-wider">
                  <Clock className="w-3 h-3" /> Entry Details
                </h4>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Entry Price</span>
                    <span className="font-mono text-white">{formatPrice(trade.entry_price)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Entry Time</span>
                    <span className="font-mono text-white text-xs">{formatTime(trade.timestamp)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Close Time</span>
                    <span className="font-mono text-white text-xs">{formatTime(trade.close_time)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Leverage</span>
                    <span className="font-mono text-amber-400">{trade.leverage}x</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Margin</span>
                    <span className="font-mono text-white">${Number(trade.margin || 0).toFixed(2)}</span>
                  </div>
                </div>
              </div>

              {/* Take Profits */}
              <div className="space-y-3">
                <h4 className="flex items-center gap-2 text-xs font-bold text-emerald-500/60 uppercase tracking-wider">
                  <Target className="w-3 h-3" /> Take Profits
                </h4>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">TP1</span>
                    <div className="flex items-center gap-2">
                      <span className={cn("font-mono", trade.take_profit_1 ? "text-emerald-400" : "text-white/20")}>
                        {formatPrice(trade.take_profit_1)}
                      </span>
                      {trade.tp1_closed && <span className="text-[9px] bg-emerald-500/20 text-emerald-400 px-1 rounded">HIT</span>}
                    </div>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">TP2</span>
                    <div className="flex items-center gap-2">
                      <span className={cn("font-mono", trade.take_profit_2 ? "text-emerald-400" : "text-white/20")}>
                        {formatPrice(trade.take_profit_2)}
                      </span>
                      {trade.tp2_closed && <span className="text-[9px] bg-emerald-500/20 text-emerald-400 px-1 rounded">HIT</span>}
                    </div>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Exit Price</span>
                    <span className="font-mono text-white">{formatPrice(trade.current_price)}</span>
                  </div>
                </div>
              </div>

              {/* Stop Loss */}
              <div className="space-y-3">
                <h4 className="flex items-center gap-2 text-xs font-bold text-rose-500/60 uppercase tracking-wider">
                  <Shield className="w-3 h-3" /> Stop Loss
                </h4>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Hard SL</span>
                    <span className="font-mono text-rose-400">{formatPrice(trade.stop_loss)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Trailing SL</span>
                    <span className="font-mono text-amber-400">{formatPrice(trade.trailing_sl)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Chandelier</span>
                    <span className="font-mono text-blue-400">{formatPrice(trade.chandelier_sl)}</span>
                  </div>
                </div>
              </div>

              {/* PnL & Fees */}
              <div className="space-y-3">
                <h4 className="flex items-center gap-2 text-xs font-bold text-blue-500/60 uppercase tracking-wider">
                  <Zap className="w-3 h-3" /> PnL & Fees
                </h4>
                <div className="space-y-1.5">
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Realized PnL</span>
                    <span className={cn("font-mono font-medium", pnl >= 0 ? "text-emerald-400" : "text-rose-400")}>
                      {pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}
                    </span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">ROI</span>
                    <span className={cn("font-mono font-medium", roi >= 0 ? "text-emerald-400" : "text-rose-400")}>
                      {roi >= 0 ? '+' : ''}{roi.toFixed(2)}%
                    </span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Entry Fee</span>
                    <span className="font-mono text-white/50">${Number(trade.entry_fee || 0).toFixed(4)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Exit Fees</span>
                    <span className="font-mono text-white/50">${Number(trade.total_exit_fees || 0).toFixed(4)}</span>
                  </div>
                  <div className="flex justify-between text-sm">
                    <span className="text-white/40">Close Reason</span>
                    <span className="font-mono text-white/60 text-xs">{trade.close_reason || '—'}</span>
                  </div>
                </div>
              </div>

            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export default function History({ authFetch }) {
  const doFetch = authFetch || fetch;
  const [trades, setTrades] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState(null); // position_id or null
  const [filters, setFilters] = useState({ symbol: '', entry_type: '', result: '', offset: 0 });
  const [sortBy, setSortBy] = useState('close_time');
  const [sortOrder, setSortOrder] = useState('desc');
  const limit = 20;

  const fetchTrades = useCallback(() => {
    setLoading(true);
    const params = new URLSearchParams({ limit, offset: filters.offset, sort_by: sortBy, sort_order: sortOrder });
    if (filters.symbol) params.set('symbol', filters.symbol);
    if (filters.entry_type) params.set('entry_type', filters.entry_type);
    if (filters.result) params.set('result', filters.result);

    doFetch(`/api/positions/closed?${params}`)
      .then(res => res.json())
      .then(data => {
        if (data.success) {
          setTrades(data.data.positions || []);
          setTotal(data.data.total || 0);
        }
      })
      .catch(() => { })
      .finally(() => setLoading(false));
  }, [filters, sortBy, sortOrder]);

  useEffect(() => {
    fetchTrades();
  }, [fetchTrades]);

  const updateFilter = (key, value) => {
    setFilters(prev => ({ ...prev, [key]: value, offset: 0 }));
  };

  const handleSort = (field) => {
    if (sortBy === field) {
      setSortOrder(prev => prev === 'desc' ? 'asc' : 'desc');
    } else {
      setSortBy(field);
      setSortOrder('desc');
    }
    setFilters(prev => ({ ...prev, offset: 0 }));
  };

  const nextPage = () => setFilters(prev => ({ ...prev, offset: prev.offset + limit }));
  const prevPage = () => setFilters(prev => ({ ...prev, offset: Math.max(0, prev.offset - limit) }));

  return (
    <div className="space-y-6">
      {/* Header & Filters */}
      <div className="glass-panel p-5 rounded-2xl flex flex-col md:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-primary/10 text-primary">
            <Clock className="w-5 h-5" />
          </div>
          <div>
            <h2 className="text-lg font-bold text-white">Trade History</h2>
            <p className="text-xs text-white/40">{total} total trades executed</p>
          </div>
        </div>

        <div className="flex items-center gap-3 w-full md:w-auto">
          <div className="relative group flex-1 md:flex-none">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-white/30 group-focus-within:text-primary transition-colors" />
            <input
              type="text"
              placeholder="Symbol..."
              value={filters.symbol}
              onChange={(e) => updateFilter('symbol', e.target.value.toUpperCase())}
              className="w-full md:w-40 bg-bg-main border border-white/10 rounded-lg py-2 pl-9 pr-3 text-sm text-white focus:outline-none focus:border-primary/50 transition-all placeholder:text-white/20"
            />
          </div>

          <select
            value={filters.entry_type}
            onChange={(e) => updateFilter('entry_type', e.target.value)}
            className="bg-bg-main border border-white/10 rounded-lg px-3 py-2 text-sm text-white/80 focus:outline-none focus:border-primary/50 cursor-pointer"
          >
            <option value="">All Types</option>
            <option value="standard_m5">Std M5</option>
            <option value="standard_m15">Std M15</option>
            <option value="standard_h1">Std H1</option>
            <option value="standard_h4">Std H4</option>
            <option value="ema610_h1">EMA610 H1</option>
            <option value="ema610_h4">EMA610 H4</option>
            <option value="rsi_div_m15">RSI Div M15</option>
            <option value="rsi_div_h1">RSI Div H1</option>
            <option value="rsi_div_h4">RSI Div H4</option>
            <option value="sd_demand_m15">SD Demand M15</option>
            <option value="sd_demand_h1">SD Demand H1</option>
            <option value="sd_demand_h4">SD Demand H4</option>
            <option value="sd_supply_m15">SD Supply M15</option>
            <option value="sd_supply_h1">SD Supply H1</option>
            <option value="sd_supply_h4">SD Supply H4</option>
          </select>

          <select
            value={filters.result}
            onChange={(e) => updateFilter('result', e.target.value)}
            className="bg-bg-main border border-white/10 rounded-lg px-3 py-2 text-sm text-white/80 focus:outline-none focus:border-primary/50 cursor-pointer"
          >
            <option value="">All Results</option>
            <option value="win">Winners</option>
            <option value="loss">Losers</option>
          </select>
        </div>
      </div>

      {/* Performance Analysis */}
      <PerformanceAnalysis authFetch={doFetch} />

      {/* Table */}
      <div className="glass-panel rounded-2xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm border-collapse">
            <thead>
              <tr className="text-xs text-white/40 uppercase tracking-wider border-b border-white/5 bg-white/[0.02]">
                <th className="px-6 py-4 font-medium">Symbol</th>
                <th className="px-6 py-4 font-medium">Side</th>
                <th className="px-6 py-4 font-medium hidden md:table-cell">Type</th>
                <th className="px-6 py-4 font-medium text-right hidden md:table-cell">Entry</th>
                <th className="px-6 py-4 font-medium text-right hidden md:table-cell">Exit</th>
                <SortHeader label="PnL" field="pnl_usd" sortBy={sortBy} sortOrder={sortOrder} onSort={handleSort} className="text-right" />
                <SortHeader label="ROI" field="roi_percent" sortBy={sortBy} sortOrder={sortOrder} onSort={handleSort} className="text-right" />
                <th className="px-6 py-4 font-medium hidden lg:table-cell">Reason</th>
                <SortHeader label="Duration" field="duration" sortBy={sortBy} sortOrder={sortOrder} onSort={handleSort} className="text-right hidden lg:table-cell" />
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {loading ? (
                <>
                  {[1, 2, 3, 4, 5].map(i => <TableRowSkeleton key={i} cols={9} />)}
                </>
              ) : trades.length === 0 ? (
                <tr><td colSpan={9} className="px-6 py-16 text-center">
                  <div className="flex flex-col items-center gap-3">
                    <div className="w-12 h-12 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center">
                      <Filter className="w-6 h-6 text-white/20" />
                    </div>
                    <p className="text-sm text-white/30">No trades match your filters</p>
                    <p className="text-xs text-white/15">Try adjusting your search criteria</p>
                  </div>
                </td></tr>
              ) : (
                trades.map((trade) => (
                  <TradeRow
                    key={trade.position_id}
                    trade={trade}
                    expanded={expanded === trade.position_id}
                    onToggle={() => setExpanded(prev => prev === trade.position_id ? null : trade.position_id)}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Pagination */}
      {total > limit && (
        <div className="flex items-center justify-between pt-4 border-t border-white/5">
          <p className="text-xs text-white/30">
            Showing {filters.offset + 1}–{Math.min(filters.offset + limit, total)} of {total}
          </p>
          <div className="flex gap-2">
            <button onClick={prevPage} disabled={filters.offset === 0} className="p-2 rounded-lg bg-white/5 hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors active:scale-[0.93]">
              <ChevronLeft className="w-4 h-4 text-white" />
            </button>
            <button onClick={nextPage} disabled={filters.offset + limit >= total} className="p-2 rounded-lg bg-white/5 hover:bg-white/10 disabled:opacity-30 disabled:cursor-not-allowed transition-colors active:scale-[0.93]">
              <ChevronRight className="w-4 h-4 text-white" />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
