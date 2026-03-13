import React from 'react';
import { Target, ShieldX, TrendingDown, Clock, HelpCircle } from 'lucide-react';
import { cn } from '../utils/cn';

const EXIT_META = {
  'TP1':             { icon: Target,       color: 'text-emerald-400', bg: 'bg-emerald-500/10', bar: 'bg-emerald-500/40' },
  'TP2':             { icon: Target,       color: 'text-emerald-300', bg: 'bg-emerald-500/10', bar: 'bg-emerald-500/30' },
  'Hard SL':         { icon: ShieldX,      color: 'text-rose-400',    bg: 'bg-rose-500/10',    bar: 'bg-rose-500/40' },
  'Chandelier SL':   { icon: TrendingDown, color: 'text-amber-400',   bg: 'bg-amber-500/10',   bar: 'bg-amber-500/40' },
  'End of Backtest': { icon: Clock,        color: 'text-zinc-400',    bg: 'bg-zinc-500/10',    bar: 'bg-zinc-500/40' },
  'Other':           { icon: HelpCircle,   color: 'text-zinc-500',    bg: 'bg-zinc-500/10',    bar: 'bg-zinc-500/30' },
};

const fallbackMeta = { icon: HelpCircle, color: 'text-zinc-500', bg: 'bg-zinc-500/10', bar: 'bg-zinc-500/30' };

function formatPnl(v) {
  const sign = v >= 0 ? '+' : '';
  return `${sign}$${v.toFixed(2)}`;
}

function formatRoi(v) {
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`;
}

function pnlColor(v) {
  if (v > 0) return 'text-emerald-400';
  if (v < 0) return 'text-rose-400';
  return 'text-white/50';
}

export default function BacktestExitStats({ exitStats }) {
  if (!exitStats || exitStats.length === 0) return null;

  const totalTrades = exitStats.reduce((sum, s) => sum + s.count, 0);

  return (
    <div className="glass-card rounded-2xl border border-white/5 overflow-hidden">
      {/* Header */}
      <div className="px-5 py-3 border-b border-white/5 flex items-center gap-2">
        <ShieldX className="w-4 h-4 text-white/40" />
        <span className="text-sm font-semibold text-white/80">Exit Breakdown</span>
        <span className="text-xs text-white/30 ml-auto font-mono">{totalTrades} trades</span>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-xs text-white/30 uppercase tracking-wider border-b border-white/5">
              <th className="text-left px-5 py-2.5 font-medium">Type</th>
              <th className="text-right px-3 py-2.5 font-medium">Trades</th>
              <th className="text-right px-3 py-2.5 font-medium">%</th>
              <th className="text-right px-3 py-2.5 font-medium">Win Rate</th>
              <th className="text-right px-3 py-2.5 font-medium">Avg PNL</th>
              <th className="text-right px-3 py-2.5 font-medium">Total PNL</th>
              <th className="text-right px-5 py-2.5 font-medium">Avg ROI</th>
            </tr>
          </thead>
          <tbody>
            {exitStats.map((row) => {
              const meta = EXIT_META[row.type] || fallbackMeta;
              const Icon = meta.icon;
              const pc = pnlColor(row.total_pnl);
              const subs = row.by_entry_type || [];
              const hasSubs = subs.length > 1;

              return (
                <React.Fragment key={row.type}>
                  {/* Parent row */}
                  <tr className={cn(
                    'border-b transition-colors',
                    hasSubs ? 'border-white/[0.05]' : 'border-white/[0.03] hover:bg-white/[0.02]'
                  )}>
                    <td className="px-5 py-3">
                      <div className="flex items-center gap-2.5">
                        <div className={cn('p-1.5 rounded-md', meta.bg)}>
                          <Icon className={cn('w-3.5 h-3.5', meta.color)} />
                        </div>
                        <div className="flex-1 min-w-0">
                          <span className={cn('text-sm font-medium', meta.color)}>{row.type}</span>
                          <div className="mt-1 h-1 rounded-full bg-white/5 w-24 overflow-hidden">
                            <div
                              className={cn('h-full rounded-full transition-all', meta.bar)}
                              style={{ width: `${Math.max(row.pct_of_total, 2)}%` }}
                            />
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="text-right px-3 py-3 font-mono text-white/80">{row.count}</td>
                    <td className="text-right px-3 py-3 font-mono text-white/40">{row.pct_of_total}%</td>
                    <td className="text-right px-3 py-3 font-mono">
                      <span className={row.win_rate >= 50 ? 'text-emerald-400' : 'text-rose-400'}>
                        {row.win_rate.toFixed(1)}%
                      </span>
                    </td>
                    <td className={cn('text-right px-3 py-3 font-mono', pc)}>
                      {formatPnl(row.avg_pnl)}
                    </td>
                    <td className={cn('text-right px-3 py-3 font-mono font-semibold', pc)}>
                      {formatPnl(row.total_pnl)}
                    </td>
                    <td className={cn('text-right px-5 py-3 font-mono', pnlColor(row.avg_roi))}>
                      {formatRoi(row.avg_roi)}
                    </td>
                  </tr>

                  {/* Sub-rows by entry_type (only show when >1 type) */}
                  {hasSubs && subs.map((sub) => {
                    const spc = pnlColor(sub.total_pnl);
                    return (
                      <tr
                        key={`${row.type}-${sub.entry_type}`}
                        className="border-b border-white/[0.02] hover:bg-white/[0.02] transition-colors"
                      >
                        <td className="pl-14 pr-5 py-2">
                          <span className="text-xs text-white/40">{sub.entry_type}</span>
                        </td>
                        <td className="text-right px-3 py-2 font-mono text-xs text-white/50">{sub.count}</td>
                        <td className="text-right px-3 py-2 font-mono text-xs text-white/30">{sub.pct_of_parent}%</td>
                        <td className="text-right px-3 py-2 font-mono text-xs">
                          <span className={sub.win_rate >= 50 ? 'text-emerald-400/70' : 'text-rose-400/70'}>
                            {sub.win_rate.toFixed(1)}%
                          </span>
                        </td>
                        <td className={cn('text-right px-3 py-2 font-mono text-xs', spc)}>
                          {formatPnl(sub.avg_pnl)}
                        </td>
                        <td className={cn('text-right px-3 py-2 font-mono text-xs', spc)}>
                          {formatPnl(sub.total_pnl)}
                        </td>
                        <td className={cn('text-right px-5 py-2 font-mono text-xs', pnlColor(sub.avg_roi))}>
                          {formatRoi(sub.avg_roi)}
                        </td>
                      </tr>
                    );
                  })}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
