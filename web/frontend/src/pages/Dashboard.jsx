import React, { useState } from 'react';
import { Wallet, TrendingUp, Activity, Target, Zap, AlertCircle, RefreshCw, History, Clock, ChevronDown, ArrowUpRight, ArrowDownRight } from 'lucide-react';
import StatCard from '../components/StatCard';
import PositionsTable from '../components/PositionsTable';
import ProfitChart from '../components/ProfitChart';
import CryptoIcon from '../components/CryptoIcon';
import { StatCardSkeleton, ActivitySkeleton } from '../components/Skeleton';
import { ToastContainer } from '../components/Toast';
import { useToast } from '../hooks/useToast';
import { cn } from '../utils/cn';

function formatTime(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  } catch { return isoStr; }
}

function formatPrice(v) {
  if (v == null) return '—';
  const num = Number(v);
  return isNaN(num) ? '—' : num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
}

function DetailRow({ label, value, color }) {
  return (
    <div className="flex justify-between items-center">
      <span className="text-[10px] text-text-dim uppercase tracking-wider">{label}</span>
      <span className={cn("text-[11px] font-mono font-medium", color || "text-white/70")}>{value}</span>
    </div>
  );
}

function TradeHistoryItem({ item }) {
  const [expanded, setExpanded] = useState(false);
  const pnl = item.pnl ?? item.amount ?? 0;
  const isWin = pnl >= 0;
  const isLong = item.side === 'BUY';
  const isPartial = item.status === 'PARTIAL_CLOSE';

  return (
    <div
      className={cn(
        "rounded-xl border transition-all cursor-pointer",
        isPartial ? (expanded ? "bg-blue-500/[0.04] border-blue-500/15" : "bg-blue-500/[0.03] border-blue-500/10 hover:border-blue-500/20") :
        expanded ? "bg-white/[0.04] border-white/10" : "bg-white/5 border-white/5 hover:border-white/10"
      )}
      onClick={() => setExpanded(prev => !prev)}
    >
      {/* Summary row */}
      <div className="flex items-start justify-between p-3.5 gap-3">
        <div className="flex items-start gap-2.5 min-w-0 flex-1">
          <CryptoIcon symbol={item.symbol} className="w-8 h-8 rounded-full bg-white/5 p-0.5 shrink-0 mt-0.5" />
          <div className="min-w-0">
            <div className="flex items-center gap-1.5">
              <span className="text-sm font-bold text-text-main leading-tight truncate">{item.symbol}</span>
              <span className={cn(
                "text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0",
                isLong ? "bg-emerald-500/20 text-emerald-400" : "bg-rose-500/20 text-rose-400"
              )}>
                {isLong ? 'LONG' : 'SHORT'}
              </span>
              <span className={cn(
                "text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0",
                isWin ? "bg-emerald-500/10 text-emerald-400/80" : "bg-rose-500/10 text-rose-400/80"
              )}>
                {isWin ? 'WIN' : 'LOSS'}
              </span>
            </div>
            <div className="flex items-center flex-wrap gap-1 mt-1">
              <span className="text-[10px] text-text-dim font-mono">{item.strategy || 'standard'}</span>
              {isPartial && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-blue-500/20 text-blue-400">PARTIAL</span>}
              {(item.tp1_closed || /TP1|TP2|TP2_ROI/.test(item.close_reason || '')) && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-emerald-500/15 text-emerald-400/80">TP1✓</span>}
              {(item.tp2_closed || /TP2|TP2_ROI/.test(item.close_reason || '')) && <span className="text-[8px] font-bold px-1 py-0.5 rounded bg-emerald-500/15 text-emerald-400/80">TP2✓</span>}
              {item.close_reason && (
                <span className={cn("text-[8px] font-bold px-1 py-0.5 rounded whitespace-nowrap",
                  item.close_reason.includes('TP') ? "bg-emerald-500/10 text-emerald-400/60" :
                  item.close_reason.includes('CHANDELIER') ? "bg-amber-500/10 text-amber-400/60" :
                  item.close_reason.includes('HARD_SL') ? "bg-rose-500/10 text-rose-400/60" :
                  item.close_reason.includes('TRAILING') ? "bg-amber-500/10 text-amber-400/60" :
                  "bg-white/5 text-white/30"
                )}>
                  {item.close_reason.replace(/_/g, ' ').replace('CHANDELIER SL', 'CHAND.SL').replace('MANUAL WEB', 'MANUAL')}
                </span>
              )}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0 pt-0.5">
          <div className="text-right">
            <span className={cn(
              "text-sm font-bold font-mono leading-tight block",
              isWin ? "text-emerald-400" : "text-rose-400"
            )}>
              {isWin ? '+' : ''}{typeof pnl === 'number' ? pnl.toFixed(2) : pnl}
            </span>
            {item.roi != null && (
              <span className={cn("text-[10px] font-mono block", isWin ? "text-emerald-400/60" : "text-rose-400/60")}>
                {isWin ? '+' : ''}{Number(item.roi).toFixed(1)}%
              </span>
            )}
          </div>
          <ChevronDown className={cn(
            "w-3.5 h-3.5 text-text-dim transition-transform",
            expanded && "rotate-180"
          )} />
        </div>
      </div>

      {/* Expandable detail */}
      {expanded && (
        <div className="px-3.5 pb-3.5 pt-0 space-y-2 border-t border-white/5 mt-0 pt-3">
          <DetailRow label="Entry" value={formatPrice(item.entry_price)} />
          <DetailRow label="Opened" value={formatTime(item.entry_time)} />
          <DetailRow label="Closed" value={formatTime(item.close_time)} />
          {(item.tp1 != null || item.tp1_closed || item.tp1_cancelled) && (
            <DetailRow label="TP1" value={
              <span className="flex items-center gap-1.5">
                {item.tp1 != null ? formatPrice(item.tp1) : '—'}
                {item.tp1_closed && <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">HIT ✓</span>}
                {item.tp1_cancelled && <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-white/10 text-white/30">OFF</span>}
              </span>
            } color="text-emerald-400/70" />
          )}
          {(item.tp2 != null || item.tp2_closed || item.tp2_cancelled) && (
            <DetailRow label="TP2" value={
              <span className="flex items-center gap-1.5">
                {item.tp2 != null ? formatPrice(item.tp2) : '—'}
                {item.tp2_closed && <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-emerald-500/20 text-emerald-400">HIT ✓</span>}
                {item.tp2_cancelled && <span className="text-[9px] font-bold px-1 py-0.5 rounded bg-white/10 text-white/30">OFF</span>}
              </span>
            } color="text-emerald-400/70" />
          )}
          {item.hard_sl != null && <DetailRow label="Hard SL" value={formatPrice(item.hard_sl)} color="text-rose-400/70" />}
          {item.trailing_sl != null && <DetailRow label="Trailing SL" value={formatPrice(item.trailing_sl)} color="text-amber-400/70" />}
          {item.close_reason && (
            <DetailRow
              label="Reason"
              value={item.close_reason.replace(/_/g, ' ')}
              color={item.close_reason.includes('TP') ? "text-emerald-400/80" : "text-rose-400/80"}
            />
          )}
        </div>
      )}
    </div>
  );
}

function TradeHistory({ activity, loading, error, onRetry }) {
  if (loading) {
    return (
      <div className="glass-panel p-6 rounded-2xl h-full border border-white/10">
        <h3 className="text-sm font-bold text-text-muted uppercase tracking-widest mb-6 flex items-center gap-2">
          <History className="w-4 h-4 text-white" />
          Trade History
        </h3>
        <ActivitySkeleton />
      </div>
    );
  }

  if (error) {
    return (
      <div className="glass-panel p-6 rounded-2xl flex flex-col items-center justify-center h-full min-h-[200px] gap-3">
        <AlertCircle className="w-6 h-6 text-rose-400/60" />
        <p className="text-xs text-rose-400/60">Failed to load history</p>
        <button onClick={onRetry} className="flex items-center gap-1.5 text-xs text-white/40 hover:text-white/70 transition-colors">
          <RefreshCw className="w-3 h-3" /> Retry
        </button>
      </div>
    );
  }

  if (!activity || activity.length === 0) {
    return (
      <div className="glass-panel p-6 rounded-2xl flex flex-col items-center justify-center h-full min-h-[200px] gap-3">
        <div className="w-14 h-14 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center">
          <History className="w-7 h-7 text-white/20" />
        </div>
        <p className="text-sm text-white/30">No trade history</p>
        <p className="text-xs text-white/15">Closed trades will appear here</p>
      </div>
    );
  }

  return (
    <div className="glass-panel p-6 rounded-2xl h-full border border-white/10 flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-sm font-bold text-text-muted uppercase tracking-widest flex items-center gap-2">
          <History className="w-4 h-4 text-white" />
          Trade History
        </h3>
        <span className="text-xs text-text-dim font-mono">{activity.length} recent</span>
      </div>
      <div className="space-y-2 overflow-y-auto flex-1 pr-1">
        {activity.map((item, index) => (
          <TradeHistoryItem key={index} item={item} />
        ))}
      </div>
    </div>
  );
}

const PERIOD_OPTIONS = [
  { id: '24h', label: '24H' },
  { id: '7d', label: '7D' },
  { id: '30d', label: '30D' },
  { id: 'all', label: 'All' },
];

function PeriodSelector({ period, onChange }) {
  return (
    <div className="flex bg-white/5 rounded-lg p-0.5 gap-0.5">
      {PERIOD_OPTIONS.map(opt => (
        <button
          key={opt.id}
          onClick={() => onChange(opt.id)}
          className={cn(
            "px-2.5 py-1 text-[10px] font-semibold rounded-md transition-all",
            period === opt.id
              ? "bg-emerald-500/20 text-emerald-400"
              : "text-white/30 hover:text-white/50"
          )}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}

export default function Dashboard({ data, authFetch, refresh }) {
  const { positions, stats } = data;
  const [activity, setActivity] = React.useState([]);
  const [activityLoading, setActivityLoading] = React.useState(true);
  const [activityError, setActivityError] = React.useState(false);
  const [statsPeriod, setStatsPeriod] = React.useState('all');
  const [filteredStats, setFilteredStats] = React.useState(null);
  const { toasts, addToast, removeToast } = useToast();
  const doFetch = authFetch || fetch;

  // Fetch period stats from REST API (bills-based, accurate)
  const fetchFilteredStats = React.useCallback((period) => {
    doFetch(`/api/stats?period=${period}`)
      .then(res => res.json())
      .then(res => { if (res.success) setFilteredStats(res.data); })
      .catch(() => {});
  }, [doFetch]);

  React.useEffect(() => {
    fetchFilteredStats(statsPeriod);
    const interval = setInterval(() => fetchFilteredStats(statsPeriod), 3000);
    return () => clearInterval(interval);
  }, [statsPeriod, fetchFilteredStats]);

  const handlePositionAction = async (action, positionId, params = {}) => {
    const endpoints = {
      close: `/api/positions/${positionId}/close`,
      partial_close: `/api/positions/${positionId}/partial-close`,
      cancel_tp: `/api/positions/${positionId}/cancel-tp`,
      modify_sl: `/api/positions/${positionId}/modify-sl`,
    };
    const url = endpoints[action];
    if (!url) return;

    const body = action === 'close' ? {} :
                 action === 'partial_close' ? { percent: params.percent } :
                 action === 'cancel_tp' ? { level: params.level } :
                 action === 'modify_sl' ? { price: params.price } : {};

    try {
      const res = await doFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const result = await res.json();
      if (result.success) {
        addToast(result.message, 'success', 5000);
        if (refresh) {
          refresh();
          setTimeout(() => refresh(), 1500);
        }
      } else {
        addToast(result.error || 'Command failed', 'error', 6000);
      }
    } catch (err) {
      addToast(`Network error: ${err.message}`, 'error', 6000);
    }
  };

  const fetchActivity = React.useCallback(() => {
    setActivityLoading(true);
    setActivityError(false);
    doFetch('/api/positions/closed?limit=10&sort_by=close_time&sort_order=desc')
      .then(res => res.json())
      .then(res => {
        if (res.success) {
          const trades = (res.data.positions || []).map(p => ({
            symbol: p.symbol,
            side: p.side,
            pnl: p.pnl_usd || 0,
            roi: p.roi_percent || 0,
            strategy: p.entry_type || 'standard',
            status: p.status || 'CLOSED',
            entry_price: p.entry_price,
            entry_time: p.entry_time || p.timestamp,
            close_time: p.close_time,
            tp1: p.take_profit_1,
            tp2: p.take_profit_2,
            tp1_closed: p.tp1_closed || false,
            tp2_closed: p.tp2_closed || false,
            tp1_cancelled: p.tp1_cancelled || false,
            tp2_cancelled: p.tp2_cancelled || false,
            hard_sl: p.stop_loss,
            trailing_sl: p.trailing_sl,
            close_reason: p.close_reason || '',
          }));
          setActivity(trades);
        }
      })
      .catch(() => setActivityError(true))
      .finally(() => setActivityLoading(false));
  }, []);

  React.useEffect(() => { fetchActivity(); }, [fetchActivity]);

  // Merge: WS provides real-time (balance, unrealized, margin), REST provides PNL/fees/stats
  const mergedStats = React.useMemo(() => ({
    ...filteredStats,
    // WS real-time fields always override REST
    balance: stats.balance ?? filteredStats?.balance,
    growth_pct: stats.growth_pct ?? filteredStats?.growth_pct,
    unrealized_pnl: stats.unrealized_pnl ?? filteredStats?.unrealized_pnl,
    total_margin: stats.total_margin ?? filteredStats?.total_margin,
    open_count: stats.open_count ?? filteredStats?.open_count,
  }), [stats, filteredStats]);

  const isStatsEmpty = !filteredStats && (!stats || Object.keys(stats).length === 0);

  const ps = mergedStats;
  const totalPnl = ps.total_pnl || 0;
  const pnlTrend = totalPnl >= 0 ? 'up' : 'down';
  const winRate = ps.win_rate || 0;

  return (
    <div className="space-y-[var(--spacing-fluid-gap)] animate-fade-in-up">
      {/* Overview Stats - Responsive columns */}
      <div className="flex items-center justify-between mb-1">
        <PeriodSelector period={statsPeriod} onChange={setStatsPeriod} />
      </div>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-[var(--spacing-fluid-gap)]">
        {isStatsEmpty ? (
          <>
            {[1, 2, 3, 4, 5].map(i => <div key={i} className="card-stagger"><StatCardSkeleton /></div>)}
          </>
        ) : (
          <>
            <div className="card-stagger">
              <StatCard
                title="Total Balance"
                numericValue={stats.balance || 0}
                formatValue={(v) => `$${v.toLocaleString('en-US', { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`}
                subtitle={`Growth: ${(stats.growth_pct || 0) >= 0 ? '+' : ''}${(stats.growth_pct || 0).toFixed(2)}% | Margin: $${(stats.total_margin || 0).toLocaleString()}`}
                trend={(stats.growth_pct || 0) >= 0 ? 'up' : 'down'}
                icon={Wallet}
              />
            </div>
            <div className="card-stagger">
              <StatCard
                title="Total PnL"
                numericValue={totalPnl}
                formatValue={(v) => `$${v.toFixed(2)}`}
                trend={pnlTrend}
                subtitle={`Fees: $${(ps.total_fees || 0).toFixed(2)} | Funding: $${(ps.total_funding_fees || 0).toFixed(4)}`}
                icon={TrendingUp}
              />
            </div>
            <div className="card-stagger">
              <StatCard
                title="Win Rate"
                numericValue={winRate}
                formatValue={(v) => `${v.toFixed(1)}%`}
                trend={winRate > 50 ? 'up' : winRate > 0 ? 'down' : null}
                subtitle={`${ps.wins || 0}W / ${ps.losses || 0}L (${ps.total_trades || 0} trades)`}
                icon={Target}
              />
            </div>
            <div className="card-stagger">
              <StatCard
                title="Profit Factor"
                value={ps.profit_factor === '∞' || ps.profit_factor === Infinity ? '∞' : (ps.profit_factor || 0).toFixed ? (ps.profit_factor || 0).toFixed(2) : ps.profit_factor}
                subtitle={<>Avg: <span className="text-emerald-400">+${(ps.avg_win || 0).toFixed(2)}</span> / <span className="text-rose-400">-${(ps.avg_loss || 0).toFixed(2)}</span></>}
                trend={ps.profit_factor > 1 ? 'up' : 'down'}
                icon={Zap}
              />
            </div>
            <div className="card-stagger">
              <StatCard
                title="Active Trades"
                numericValue={positions.length}
                formatValue={(v) => Math.round(v)}
                trend={null}
                subtitle="Currently open positions"
                icon={Activity}
              />
            </div>
          </>
        )}
      </div>

      {/* Charts & Activity Section */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-[var(--spacing-fluid-gap)]">
        {/* Profit Chart */}
        <div className="col-span-12 lg:col-span-8">
          <div className="glass-panel p-[var(--spacing-fluid-gap)] h-[clamp(350px,40vh,450px)] relative overflow-hidden group rounded-2xl border border-white/10">
            <div className="absolute top-0 right-0 p-6 opacity-0 group-hover:opacity-100 transition-opacity z-10 pointer-events-none">
              <div className="w-20 h-20 bg-white/5 blur-2xl rounded-full" />
            </div>

            <h3 className="text-sm font-bold text-text-muted uppercase tracking-widest mb-6 flex items-center gap-2">
              <TrendingUp className="w-4 h-4 text-white" />
              Profit Analysis
            </h3>

            <div className="h-[calc(100%-80px)] w-full">
              <ProfitChart authFetch={authFetch} />
            </div>
          </div>
        </div>

        {/* Trade History */}
        <div className="col-span-12 lg:col-span-4">
          <TradeHistory activity={activity} loading={activityLoading} error={activityError} onRetry={fetchActivity} />
        </div>
      </div>

      {/* Active Positions Table */}
      <div className="glass-panel rounded-2xl border border-white/10">
        <div className="p-6 border-b border-white/5 flex items-center justify-between">
          <h3 className="text-lg font-bold text-white font-display tracking-wide flex items-center gap-2">
            <Activity className="w-5 h-5 text-white" />
            Active Positions
          </h3>
          <div className="flex items-center gap-3">
            <span className="live-dot" />
            <span className="text-xs text-text-dim font-mono uppercase">Live Data</span>
            {refresh && (
              <button
                onClick={() => refresh()}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white/50 hover:text-white/90 hover:bg-white/5 transition-colors active:scale-[0.95]"
                title="Refresh positions"
              >
                <RefreshCw className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>

        <div className="p-0">
          <PositionsTable positions={positions} onAction={handlePositionAction} />
        </div>
      </div>
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </div>
  );
}
