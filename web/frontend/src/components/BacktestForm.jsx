import React, { useState, useEffect } from 'react';
import { Play, Loader2, SlidersHorizontal } from 'lucide-react';

export default function BacktestForm({ onSubmit, loading, authFetch, configChangeCount = 0 }) {
  const [symbols, setSymbols] = useState([]);
  const [symbol, setSymbol] = useState('BTCUSDT');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [balance, setBalance] = useState(10000);
  const [divergence, setDivergence] = useState(true);

  useEffect(() => {
    const now = new Date();
    const ago = new Date(now);
    ago.setDate(now.getDate() - 30);
    setEndDate(now.toISOString().split('T')[0]);
    setStartDate(ago.toISOString().split('T')[0]);
  }, []);

  useEffect(() => {
    const fetchSymbols = async () => {
      try {
        const raw = await authFetch('/api/backtest/symbols');
        const res = await raw.json();
        if (res.success) setSymbols(res.data);
      } catch (err) {
        // Symbols unavailable
      }
    };
    fetchSymbols();
  }, [authFetch]);

  const handleSubmit = (e) => {
    e.preventDefault();
    onSubmit({ symbol, start_date: startDate, end_date: endDate, initial_balance: balance, enable_divergence: divergence });
  };

  return (
    <form onSubmit={handleSubmit} className="glass-card rounded-2xl p-5 border border-white/10">
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
        <div>
          <label className="block text-[10px] font-bold text-text-dim uppercase tracking-[0.15em] mb-1.5">Symbol</label>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)} className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white font-mono focus:border-primary/50 focus:outline-none transition-colors">
            {symbols.map((s) => <option key={s} value={s} className="bg-[#0a0a0f]">{s.replace('USDT', '')}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-[10px] font-bold text-text-dim uppercase tracking-[0.15em] mb-1.5">Start Date</label>
          <input type="date" value={startDate} onChange={(e) => setStartDate(e.target.value)} className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white font-mono focus:border-primary/50 focus:outline-none transition-colors [color-scheme:dark]" />
        </div>
        <div>
          <label className="block text-[10px] font-bold text-text-dim uppercase tracking-[0.15em] mb-1.5">End Date</label>
          <input type="date" value={endDate} onChange={(e) => setEndDate(e.target.value)} className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white font-mono focus:border-primary/50 focus:outline-none transition-colors [color-scheme:dark]" />
        </div>
        <div>
          <label className="block text-[10px] font-bold text-text-dim uppercase tracking-[0.15em] mb-1.5">Balance ($)</label>
          <input type="number" value={balance} onChange={(e) => setBalance(Number(e.target.value))} min={100} step={100} className="w-full bg-white/5 border border-white/10 rounded-lg px-3 py-2.5 text-sm text-white font-mono focus:border-primary/50 focus:outline-none transition-colors" />
        </div>
        <div>
          <label className="block text-[10px] font-bold text-text-dim uppercase tracking-[0.15em] mb-1.5">Divergence</label>
          <button type="button" onClick={() => setDivergence(!divergence)} className={`w-full rounded-lg px-3 py-2.5 text-sm font-mono border transition-colors ${divergence ? 'bg-primary/10 border-primary/30 text-primary' : 'bg-white/5 border-white/10 text-text-muted'}`}>
            {divergence ? 'ON' : 'OFF'}
          </button>
        </div>
        <div className="flex items-end">
          <button type="submit" disabled={loading || !startDate} className="w-full flex items-center justify-center gap-2 bg-primary/20 hover:bg-primary/30 border border-primary/30 text-primary font-bold rounded-lg px-4 py-2.5 text-sm transition-all disabled:opacity-50 disabled:cursor-not-allowed">
            {loading ? <><Loader2 className="w-4 h-4 animate-spin" /> Running...</> : <><Play className="w-4 h-4" /> Run Backtest</>}
          </button>
        </div>
      </div>
      {configChangeCount > 0 && (
        <div className="mt-3 flex items-center gap-2 text-xs text-amber-400/80 font-mono">
          <SlidersHorizontal className="w-3.5 h-3.5" />
          <span>Custom config: {configChangeCount} change{configChangeCount !== 1 ? 's' : ''} from live</span>
        </div>
      )}
    </form>
  );
}
