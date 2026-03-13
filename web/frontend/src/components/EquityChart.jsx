import { useState, useEffect } from 'react';
import { AreaChart, Area, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts';
import { ChartSkeleton } from './Skeleton';

function CustomTooltip({ active, payload }) {
  if (!active || !payload || !payload.length) return null;
  const data = payload[0].payload;
  const isProfit = data.trade_pnl >= 0;

  return (
    <div className="glass-panel px-4 py-3 border border-white/10 shadow-2xl rounded-xl">
      <p className="text-white/60 text-xs mb-1 font-mono">{data.symbol || 'Unknown'}</p>
      <div className="flex items-center justify-between gap-4">
        <span className="text-white/40 text-xs">Trade PnL</span>
        <span className={`font-mono font-medium text-sm ${isProfit ? 'text-emerald-400' : 'text-rose-400'}`}>
          {isProfit ? '+' : ''}{data.trade_pnl}
        </span>
      </div>
      <div className="flex items-center justify-between gap-4 mt-1 pt-1 border-t border-white/5">
        <span className="text-white/40 text-xs">Total Equity</span>
        <span className="text-white font-mono font-bold text-sm text-right">${data.pnl.toLocaleString()}</span>
      </div>
      <div className="text-white/20 text-[10px] mt-1 text-right font-mono">
        {new Date(data.time).toLocaleString()}
      </div>
    </div>
  );
}

export default function EquityChart() {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/equity')
      .then(res => res.json())
      .then(res => {
        if (res.success) {
          // Ensure data is sorted by time just in case
          const sortedData = res.data.sort((a, b) => new Date(a.time) - new Date(b.time));
          console.log("Equity Data Loaded:", sortedData.length, "points", sortedData);
          setData(sortedData);
        }
      })
      .catch(err => console.error("Equity chart error:", err))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <ChartSkeleton />;
  if (!data || data.length === 0) return (
    <div className="h-full w-full flex flex-col items-center justify-center gap-2">
      <div className="w-12 h-12 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center">
        <svg className="w-6 h-6 text-white/20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 13l4-4 4 4 4-4 4 4" /></svg>
      </div>
      <span className="text-sm text-white/30">No equity data</span>
    </div>
  );

  const currentPnl = data[data.length - 1]?.pnl || 0;
  const isPositive = currentPnl >= 0;
  const color = isPositive ? '#10B981' : '#EF4444';

  return (
    <div className="w-full h-full min-h-[300px]">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 10, right: 0, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="colorPnl" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={color} stopOpacity={0.3} />
              <stop offset="95%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          <XAxis
            dataKey="time"
            hide
          />
          <YAxis
            hide
            domain={['auto', 'auto']}
          />
          <Tooltip
            content={<CustomTooltip />}
            cursor={{ stroke: 'rgba(255,255,255,0.1)', strokeWidth: 1, strokeDasharray: '4 4' }}
          />
          <Area
            type="monotone"
            dataKey="pnl"
            stroke={color}
            strokeWidth={2}
            fill="url(#colorPnl)"
            isAnimationActive={true}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
