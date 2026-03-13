import { useState, useEffect } from 'react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from 'recharts';
import { ChartSkeleton } from './Skeleton';
import { AlertCircle, RefreshCw } from 'lucide-react';

function CustomTooltip({ active, payload, label }) {
    if (!active || !payload || !payload.length) return null;
    const data = payload[0].payload;
    const isProfit = data.pnl >= 0;

    return (
        <div className="glass-panel px-4 py-3 border border-white/10 shadow-2xl rounded-xl">
            <p className="text-white/60 text-xs mb-1 font-mono">{label}</p>
            <div className="flex items-center justify-between gap-4">
                <span className="text-white/40 text-xs">PnL</span>
                <span className={`font-mono font-medium text-sm ${isProfit ? 'text-emerald-400' : 'text-rose-400'}`}>
                    {isProfit ? '+' : ''}{data.pnl}
                </span>
            </div>
            <div className="flex items-center justify-between gap-4 mt-1 pt-1 border-t border-white/5">
                <span className="text-white/40 text-xs">Trades</span>
                <span className="text-white font-mono font-bold text-sm">{data.count}</span>
            </div>
        </div>
    );
}

export default function ProfitChart({ authFetch }) {
    const [data, setData] = useState([]);
    const [period, setPeriod] = useState('daily');
    const [timeRange, setTimeRange] = useState('30d'); // '30d' or 'all'
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);
    const doFetch = authFetch || fetch;

    const fetchData = () => {
        setLoading(true);
        setError(false);
        doFetch(`/api/stats/profit?period=${period}&time_range=${timeRange}`)
            .then(res => res.json())
            .then(res => {
                if (res.success) setData(res.data);
            })
            .catch(() => setError(true))
            .finally(() => setLoading(false));
    };

    useEffect(() => { fetchData(); }, [period, timeRange]);

    // For '30d', filter client-side (backend returns all bills data)
    const filteredData = timeRange === '30d'
        ? data.filter(item => {
            const date = new Date(item.timestamp);
            const thirtyDaysAgo = new Date();
            thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
            return date >= thirtyDaysAgo;
        })
        : data;

    const periods = [
        { id: 'daily', label: 'Daily' },
        { id: 'weekly', label: 'Weekly' },
        { id: 'monthly', label: 'Monthly' },
    ];

    const ranges = [
        { id: '30d', label: 'Last 30 Days' },
        { id: 'all', label: 'All Time' },
    ];

    return (
        <div className="flex flex-col h-full w-full">
            <div className="flex flex-wrap items-center justify-between mb-4 gap-2">
                {/* Time Range Selector */}
                <div className="flex bg-white/5 rounded-lg p-1">
                    {ranges.map(r => (
                        <button
                            key={r.id}
                            onClick={() => setTimeRange(r.id)}
                            className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${timeRange === r.id
                                ? 'bg-blue-500/20 text-blue-300 shadow-sm'
                                : 'text-white/40 hover:text-white/60'
                                }`}
                        >
                            {r.label}
                        </button>
                    ))}
                </div>

                {/* Period Selector */}
                <div className="flex bg-white/5 rounded-lg p-1">
                    {periods.map(p => (
                        <button
                            key={p.id}
                            onClick={() => setPeriod(p.id)}
                            className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${period === p.id
                                ? 'bg-purple-500/20 text-purple-300 shadow-sm'
                                : 'text-white/40 hover:text-white/60'
                                }`}
                        >
                            {p.label}
                        </button>
                    ))}
                </div>
            </div>

            <div className="flex-1 w-full min-h-[250px]">
                {loading ? (
                    <ChartSkeleton />
                ) : error ? (
                    <div className="h-full w-full flex flex-col items-center justify-center gap-2">
                        <AlertCircle className="w-5 h-5 text-rose-400/60" />
                        <span className="text-xs text-rose-400/60">Failed to load chart data</span>
                        <button onClick={fetchData} className="flex items-center gap-1.5 text-xs text-white/40 hover:text-white/70 transition-colors">
                            <RefreshCw className="w-3 h-3" /> Retry
                        </button>
                    </div>
                ) : filteredData.length === 0 ? (
                    <div className="h-full w-full flex flex-col items-center justify-center gap-2">
                        <div className="w-12 h-12 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center">
                            <svg className="w-6 h-6 text-white/20" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 13l4-4 4 4 4-4 4 4" /></svg>
                        </div>
                        <span className="text-sm text-white/30">No data available</span>
                    </div>
                ) : (
                    <ResponsiveContainer width="100%" height="100%">
                        <BarChart data={filteredData}>
                            <XAxis
                                dataKey="time"
                                axisLine={false}
                                tickLine={false}
                                tick={{ fill: 'rgba(255,255,255,0.3)', fontSize: 10 }}
                                minTickGap={30}
                            />
                            <Tooltip
                                content={<CustomTooltip />}
                                cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                            />
                            <Bar dataKey="pnl" radius={[4, 4, 4, 4]}>
                                {filteredData.map((entry, index) => (
                                    <Cell key={`cell-${index}`} fill={entry.pnl >= 0 ? '#10B981' : '#EF4444'} />
                                ))}
                            </Bar>
                        </BarChart>
                    </ResponsiveContainer>
                )}
            </div>
        </div>
    );
}
