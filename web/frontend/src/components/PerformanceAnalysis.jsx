import { useState, useEffect } from 'react';
import { BarChart3, ChevronDown, ChevronUp, TrendingUp, TrendingDown, Trophy, Target } from 'lucide-react';
import { cn } from '../utils/cn';

const ET_LABELS = {
  standard_m5: 'Std M5',
  standard_m15: 'Std M15',
  standard_h1: 'Std H1',
  standard_h4: 'Std H4',
  ema610_h1: 'EMA610 H1',
  ema610_h4: 'EMA610 H4',
  rsi_div_m15: 'RSI Div M15',
  rsi_div_h1: 'RSI Div H1',
  rsi_div_h4: 'RSI Div H4',
  sd_demand_m15: 'SD Demand M15',
  sd_demand_h1: 'SD Demand H1',
  sd_demand_h4: 'SD Demand H4',
  sd_supply_m15: 'SD Supply M15',
  sd_supply_h1: 'SD Supply H1',
  sd_supply_h4: 'SD Supply H4',
};

function formatPnl(v) {
  if (v == null) return '—';
  const sign = v > 0 ? '+' : '';
  return `${sign}$${v.toFixed(2)}`;
}

function pnlColor(v) {
  if (v > 0) return 'text-emerald-400';
  if (v < 0) return 'text-rose-400';
  return 'text-white/50';
}

function PFBadge({ value }) {
  if (value >= 999) return <span className="text-emerald-400 font-mono">∞</span>;
  const color = value >= 2 ? 'text-emerald-400' : value >= 1 ? 'text-amber-400' : 'text-rose-400';
  return <span className={cn('font-mono', color)}>{value.toFixed(2)}</span>;
}

export default function PerformanceAnalysis({ authFetch, period = 'all' }) {
  const doFetch = authFetch || fetch;
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState('entry_type'); // entry_type | symbol | exit

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    doFetch(`/api/stats/analysis?period=${period}`)
      .then(res => {
        const ct = res.headers.get('content-type') || '';
        if (!ct.includes('application/json')) {
          throw new Error(`API returned ${res.status} (not JSON). Server may need restart.`);
        }
        return res.json();
      })
      .then(res => {
        if (res.success) {
          setData(res.data);
        } else {
          setError(res.error || 'Failed to load analysis');
        }
      })
      .catch(err => setError(err.message || 'Network error'))
      .finally(() => setLoading(false));
  }, [open, period]);

  return (
    <div className="glass-panel rounded-2xl overflow-hidden">
      {/* Toggle header */}
      <button
        onClick={() => setOpen(prev => !prev)}
        className="w-full px-5 py-4 flex items-center justify-between hover:bg-white/[0.02] transition-colors"
      >
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-lg bg-violet-500/10">
            <BarChart3 className="w-4 h-4 text-violet-400" />
          </div>
          <span className="text-sm font-semibold text-white">Performance Analysis</span>
          {data?.totals && (
            <div className="hidden md:flex items-center gap-4 ml-4 text-xs">
              <span className="text-white/40">{data.totals.trades} trades</span>
              <span className={pnlColor(data.totals.total_pnl)}>{formatPnl(data.totals.total_pnl)}</span>
              <span className="text-white/40">WR {data.totals.win_rate}%</span>
              <span className="text-white/40">PF <PFBadge value={data.totals.profit_factor} /></span>
            </div>
          )}
        </div>
        {open ? <ChevronUp className="w-4 h-4 text-white/40" /> : <ChevronDown className="w-4 h-4 text-white/40" />}
      </button>

      {/* Content */}
      {open && (
        <div className="px-5 pb-5 space-y-4">
          {/* Totals row */}
          {data?.totals && (
            <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
              <MiniStat icon={Target} label="Win Rate" value={`${data.totals.win_rate}%`} sub={`${data.totals.wins}W / ${data.totals.losses}L`} color="text-amber-400" />
              <MiniStat icon={TrendingUp} label="Total PnL" value={formatPnl(data.totals.total_pnl)} color={pnlColor(data.totals.total_pnl)} />
              <MiniStat icon={Trophy} label="Profit Factor" value={data.totals.profit_factor >= 999 ? '∞' : data.totals.profit_factor.toFixed(2)} color={data.totals.profit_factor >= 2 ? 'text-emerald-400' : 'text-amber-400'} />
              <MiniStat icon={TrendingDown} label="Avg PnL" value={formatPnl(data.totals.avg_pnl)} color={pnlColor(data.totals.avg_pnl)} sub={`Fee: $${data.totals.total_fee}`} />
            </div>
          )}

          {/* Tab selector */}
          <div className="flex gap-1 bg-white/[0.03] rounded-lg p-0.5 w-fit">
            <TabBtn active={tab === 'entry_type'} onClick={() => setTab('entry_type')}>By Strategy</TabBtn>
            <TabBtn active={tab === 'exit'} onClick={() => setTab('exit')}>By Exit</TabBtn>
            <TabBtn active={tab === 'symbol'} onClick={() => setTab('symbol')}>By Symbol</TabBtn>
          </div>

          {/* Table */}
          {loading ? (
            <div className="py-8 text-center text-white/30 text-sm">Loading...</div>
          ) : error ? (
            <div className="py-6 text-center text-rose-400/70 text-sm">{error}</div>
          ) : tab === 'entry_type' ? (
            <EntryTypeTable rows={data?.by_entry_type || []} totalPnl={data?.totals?.total_pnl} />
          ) : tab === 'exit' ? (
            <ExitTypeTable rows={data?.by_exit || []} totalPnl={data?.totals?.total_pnl} />
          ) : (
            <SymbolTable rows={data?.by_symbol || []} totalPnl={data?.totals?.total_pnl} />
          )}
        </div>
      )}
    </div>
  );
}

function MiniStat({ icon: Icon, label, value, sub, color = 'text-white' }) {
  return (
    <div className="bg-white/[0.03] rounded-xl px-4 py-3">
      <div className="flex items-center gap-2 mb-1">
        <Icon className="w-3.5 h-3.5 text-white/30" />
        <span className="text-[11px] text-white/40 uppercase tracking-wider">{label}</span>
      </div>
      <div className={cn('text-lg font-bold font-mono', color)}>{value}</div>
      {sub && <div className="text-[11px] text-white/30 mt-0.5">{sub}</div>}
    </div>
  );
}

function TabBtn({ active, onClick, children }) {
  return (
    <button
      onClick={onClick}
      className={cn(
        'px-3 py-1.5 text-xs font-medium rounded-md transition-colors',
        active ? 'bg-white/10 text-white' : 'text-white/40 hover:text-white/60'
      )}
    >
      {children}
    </button>
  );
}

function BreakdownNote({ rows, totalPnl }) {
  if (totalPnl == null || !rows.length) return null;
  const breakdownSum = rows.reduce((s, r) => s + (r.total_pnl || 0), 0);
  const diff = Math.abs(totalPnl - breakdownSum);
  if (diff < 0.5) return null;
  return (
    <div className="text-[11px] text-white/25 text-right mt-1 px-3">
      Breakdown covers last {rows.reduce((s, r) => s + r.trades, 0)} trades only
    </div>
  );
}

function EntryTypeTable({ rows, totalPnl }) {
  if (!rows.length) return <div className="py-6 text-center text-white/30 text-sm">No data</div>;

  const total = rows.reduce((s, r) => s + r.trades, 0) || 1;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] text-white/30 uppercase tracking-wider border-b border-white/5">
            <th className="text-left px-3 py-2.5 font-medium">Strategy</th>
            <th className="text-right px-3 py-2.5 font-medium">Trades</th>
            <th className="text-right px-3 py-2.5 font-medium">%</th>
            <th className="text-right px-3 py-2.5 font-medium">Win Rate</th>
            <th className="text-right px-3 py-2.5 font-medium">Avg PnL</th>
            <th className="text-right px-3 py-2.5 font-medium">Total PnL</th>
            <th className="text-right px-3 py-2.5 font-medium">Avg ROI</th>
            <th className="text-right px-3 py-2.5 font-medium">PF</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const pc = pnlColor(r.total_pnl);
            const pct = ((r.trades / total) * 100).toFixed(1);
            return (
              <tr key={r.entry_type} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
                <td className="px-3 py-2.5">
                  <span className="text-xs font-mono text-white/70 border border-white/10 px-1.5 py-0.5 rounded">
                    {ET_LABELS[r.entry_type] || r.entry_type}
                  </span>
                </td>
                <td className="text-right px-3 py-2.5 font-mono text-white/70">{r.trades}</td>
                <td className="text-right px-3 py-2.5 font-mono text-white/30">{pct}%</td>
                <td className="text-right px-3 py-2.5 font-mono">
                  <span className={r.win_rate >= 50 ? 'text-emerald-400' : 'text-rose-400'}>{r.win_rate}%</span>
                </td>
                <td className={cn('text-right px-3 py-2.5 font-mono', pc)}>{formatPnl(r.avg_pnl)}</td>
                <td className={cn('text-right px-3 py-2.5 font-mono font-semibold', pc)}>{formatPnl(r.total_pnl)}</td>
                <td className={cn('text-right px-3 py-2.5 font-mono', pnlColor(r.avg_roi))}>
                  {r.avg_roi > 0 ? '+' : ''}{r.avg_roi}%
                </td>
                <td className="text-right px-3 py-2.5"><PFBadge value={r.profit_factor} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <BreakdownNote rows={rows} totalPnl={totalPnl} />
    </div>
  );
}

function ExitTypeTable({ rows, totalPnl }) {
  if (!rows.length) return <div className="py-6 text-center text-white/30 text-sm">No data</div>;

  const total = rows.reduce((s, r) => s + r.trades, 0) || 1;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] text-white/30 uppercase tracking-wider border-b border-white/5">
            <th className="text-left px-3 py-2.5 font-medium">Exit Type</th>
            <th className="text-right px-3 py-2.5 font-medium">Trades</th>
            <th className="text-right px-3 py-2.5 font-medium">%</th>
            <th className="text-right px-3 py-2.5 font-medium">Win Rate</th>
            <th className="text-right px-3 py-2.5 font-medium">Avg PnL</th>
            <th className="text-right px-3 py-2.5 font-medium">Total PnL</th>
            <th className="text-right px-3 py-2.5 font-medium">PF</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const pc = pnlColor(r.total_pnl);
            const pct = ((r.trades / total) * 100).toFixed(1);
            return (
              <tr key={r.exit_type} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
                <td className="px-3 py-2.5">
                  <span className="text-xs font-mono text-white/70 border border-white/10 px-1.5 py-0.5 rounded">
                    {r.exit_type}
                  </span>
                </td>
                <td className="text-right px-3 py-2.5 font-mono text-white/70">{r.trades}</td>
                <td className="text-right px-3 py-2.5 font-mono text-white/30">{pct}%</td>
                <td className="text-right px-3 py-2.5 font-mono">
                  <span className={r.win_rate >= 50 ? 'text-emerald-400' : 'text-rose-400'}>{r.win_rate}%</span>
                </td>
                <td className={cn('text-right px-3 py-2.5 font-mono', pc)}>{formatPnl(r.avg_pnl)}</td>
                <td className={cn('text-right px-3 py-2.5 font-mono font-semibold', pc)}>{formatPnl(r.total_pnl)}</td>
                <td className="text-right px-3 py-2.5"><PFBadge value={r.profit_factor} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <BreakdownNote rows={rows} totalPnl={totalPnl} />
    </div>
  );
}

function SymbolTable({ rows, totalPnl }) {
  if (!rows.length) return <div className="py-6 text-center text-white/30 text-sm">No data</div>;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-[11px] text-white/30 uppercase tracking-wider border-b border-white/5">
            <th className="text-left px-3 py-2.5 font-medium">Symbol</th>
            <th className="text-right px-3 py-2.5 font-medium">Trades</th>
            <th className="text-right px-3 py-2.5 font-medium">Win Rate</th>
            <th className="text-right px-3 py-2.5 font-medium">Avg PnL</th>
            <th className="text-right px-3 py-2.5 font-medium">Total PnL</th>
            <th className="text-right px-3 py-2.5 font-medium">PF</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(r => {
            const pc = pnlColor(r.total_pnl);
            return (
              <tr key={r.symbol} className="border-b border-white/[0.03] hover:bg-white/[0.02] transition-colors">
                <td className="px-3 py-2.5">
                  <span className="text-sm font-medium text-white/80">{r.symbol.replace('USDT', '')}</span>
                </td>
                <td className="text-right px-3 py-2.5 font-mono text-white/70">{r.trades}</td>
                <td className="text-right px-3 py-2.5 font-mono">
                  <span className={r.win_rate >= 50 ? 'text-emerald-400' : 'text-rose-400'}>{r.win_rate}%</span>
                </td>
                <td className={cn('text-right px-3 py-2.5 font-mono', pc)}>{formatPnl(r.avg_pnl)}</td>
                <td className={cn('text-right px-3 py-2.5 font-mono font-semibold', pc)}>{formatPnl(r.total_pnl)}</td>
                <td className="text-right px-3 py-2.5"><PFBadge value={r.profit_factor} /></td>
              </tr>
            );
          })}
        </tbody>
      </table>
      <BreakdownNote rows={rows} totalPnl={totalPnl} />
    </div>
  );
}
