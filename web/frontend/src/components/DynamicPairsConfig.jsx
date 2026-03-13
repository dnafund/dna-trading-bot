import { useState, useEffect, useCallback, useMemo } from 'react';
import { RefreshCw, Plus, X, Search, ChevronDown, Globe, Clock, TrendingUp } from 'lucide-react';
import CryptoIcon from './CryptoIcon';
import { cn } from '../utils/cn';

// ── Tag Input (for whitelist/blacklist) ─────────────────────────

function TagInput({ tags, onChange, placeholder, colorClass }) {
  const [input, setInput] = useState('');

  const addTag = () => {
    let val = input.trim().toUpperCase();
    if (!val) return;
    if (!val.endsWith('USDT')) val += 'USDT';
    if (!tags.includes(val)) {
      onChange([...tags, val]);
    }
    setInput('');
  };

  const removeTag = (tag) => {
    onChange(tags.filter((t) => t !== tag));
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      addTag();
    }
    if (e.key === 'Backspace' && !input && tags.length > 0) {
      removeTag(tags[tags.length - 1]);
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5 min-h-[32px]">
        {tags.map((tag) => (
          <span
            key={tag}
            className={cn(
              'inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-mono',
              colorClass || 'bg-white/10 text-white/70'
            )}
          >
            {tag.replace('USDT', '')}
            <button
              onClick={() => removeTag(tag)}
              className="hover:text-white transition-colors ml-0.5"
            >
              <X className="w-3 h-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value.toUpperCase())}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          className="flex-1 bg-bg-main border border-white/10 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none focus:border-primary/50 transition-colors placeholder:text-white/20 font-mono"
        />
        <button
          onClick={addTag}
          disabled={!input.trim()}
          className="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-white/60 hover:text-white disabled:opacity-30 transition-all text-sm"
        >
          <Plus className="w-4 h-4" />
        </button>
      </div>
    </div>
  );
}

// ── Volume Window Row ───────────────────────────────────────────

function VolumeWindowRow({ label, period, value, onChange, rule }) {
  const isActive = value > 0;

  return (
    <div className={cn(
      'flex items-center gap-4 px-4 py-3 rounded-xl border transition-all',
      isActive ? 'border-primary/20 bg-primary/5' : 'border-white/5 bg-white/[0.02]'
    )}>
      <div className="flex items-center gap-3 flex-1 min-w-0">
        <div className={cn(
          'w-2 h-2 rounded-full flex-shrink-0',
          isActive ? 'bg-primary animate-pulse' : 'bg-white/10'
        )} />
        <div>
          <span className="text-sm font-medium text-white">{label}</span>
          <span className="text-xs text-white/30 ml-2">volume</span>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <input
          type="number"
          value={value}
          onChange={(e) => {
            const v = parseInt(e.target.value, 10);
            if (!isNaN(v)) onChange(period, Math.max(0, Math.min(200, v)));
          }}
          min={0}
          max={200}
          className={cn(
            'w-20 bg-bg-main border rounded-lg px-3 py-1.5 text-sm text-right font-mono focus:outline-none focus:ring-1 focus:ring-primary focus:border-primary transition-all',
            isActive ? 'border-primary/30 text-white' : 'border-white/10 text-white/40'
          )}
        />
        <span className="text-xs text-white/30 w-10">pairs</span>
      </div>
    </div>
  );
}

// ── Live Active Pairs Grid ──────────────────────────────────────

function ActivePairsGrid({ authFetch }) {
  const doFetch = authFetch || fetch;
  const [data, setData] = useState(null);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const fetchPairs = useCallback(() => {
    doFetch('/api/active-pairs')
      .then((r) => r.json())
      .then((res) => {
        if (res.success) setData(res.data);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [doFetch]);

  const forceRefresh = useCallback(() => {
    setRefreshing(true);
    doFetch('/api/force-refresh-pairs', { method: 'POST' })
      .then((r) => r.json())
      .then(() => {
        // Poll for updated data after bot processes the refresh (~5-10s)
        setTimeout(() => fetchPairs(), 8000);
        setTimeout(() => { fetchPairs(); setRefreshing(false); }, 15000);
      })
      .catch(() => setRefreshing(false));
  }, [fetchPairs]);

  useEffect(() => {
    fetchPairs();
    const interval = setInterval(fetchPairs, 30000);
    return () => clearInterval(interval);
  }, [fetchPairs]);

  const filtered = useMemo(() => {
    if (!data?.pairs) return [];
    if (!search) return data.pairs;
    const q = search.toUpperCase();
    return data.pairs.filter((p) => p.symbol.includes(q));
  }, [data, search]);

  const formatVol = (vol) => {
    if (vol == null) return '—';
    if (vol >= 1e9) return `$${(vol / 1e9).toFixed(1)}B`;
    if (vol >= 1e6) return `$${(vol / 1e6).toFixed(1)}M`;
    if (vol >= 1e3) return `$${(vol / 1e3).toFixed(0)}K`;
    return `$${vol.toFixed(0)}`;
  };

  const timeAgo = (iso) => {
    if (!iso) return 'never';
    const ms = Date.now() - new Date(iso).getTime();
    const mins = Math.floor(ms / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    return `${Math.floor(mins / 60)}h ${mins % 60}m ago`;
  };

  if (loading) {
    return (
      <div className="text-center text-white/20 py-8 text-sm animate-pulse">
        Loading active pairs...
      </div>
    );
  }

  if (!data || data.total === 0) {
    return (
      <div className="text-center text-white/20 py-8 text-sm space-y-3">
        <Globe className="w-6 h-6 mx-auto opacity-30" />
        <p>No active pairs yet.</p>
        <button
          onClick={forceRefresh}
          disabled={refreshing}
          className={cn(
            'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs transition-all mx-auto',
            refreshing
              ? 'bg-primary/10 text-primary cursor-wait'
              : 'bg-white/5 text-white/50 hover:text-primary hover:bg-primary/10'
          )}
        >
          <RefreshCw className={cn('w-3 h-3', refreshing && 'animate-spin')} />
          {refreshing ? 'Sending refresh signal...' : 'Force Refresh Pairs'}
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <TrendingUp className="w-4 h-4 text-primary" />
          <span className="text-sm font-medium text-white">{data.total} Active Pairs</span>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={forceRefresh}
            disabled={refreshing}
            className={cn(
              'flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs transition-all',
              refreshing
                ? 'bg-primary/10 text-primary cursor-wait'
                : 'bg-white/5 text-white/40 hover:text-primary hover:bg-primary/10'
            )}
            title="Force bot to refresh pairs now"
          >
            <RefreshCw className={cn('w-3 h-3', refreshing && 'animate-spin')} />
            {refreshing ? 'Refreshing...' : 'Refresh Now'}
          </button>
          <span className="text-xs text-white/30 flex items-center gap-1">
            <Clock className="w-3 h-3" />
            {timeAgo(data.last_refresh)}
          </span>
          <div className="relative">
            <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-white/20" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter..."
              className="w-32 bg-bg-main border border-white/10 rounded-lg py-1 pl-7 pr-2 text-xs text-white focus:outline-none focus:border-primary/50 placeholder:text-white/20"
            />
          </div>
        </div>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-1.5 max-h-[400px] overflow-y-auto pr-1 custom-scrollbar">
        {filtered.map((pair) => (
          <div
            key={pair.symbol}
            className="flex items-center gap-2.5 px-3 py-2 rounded-lg bg-white/[0.02] border border-white/5 hover:border-white/10 transition-colors group"
          >
            <CryptoIcon symbol={pair.symbol} className="w-6 h-6 rounded-full bg-white/5 p-0.5 flex-shrink-0" />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5">
                <span className="text-xs font-medium text-white group-hover:text-primary transition-colors">
                  {pair.symbol.replace('USDT', '')}
                </span>
                {pair.source_windows?.map((w) => (
                  <span
                    key={w}
                    className={cn(
                      'text-[9px] px-1 py-0 rounded font-mono',
                      w === '24h' ? 'bg-emerald-500/10 text-emerald-400' :
                      w === '48h' ? 'bg-blue-500/10 text-blue-400' :
                      'bg-purple-500/10 text-purple-400'
                    )}
                  >
                    {w}
                  </span>
                ))}
              </div>
              <span className="text-[10px] text-white/30 font-mono">
                {formatVol(pair.volume_24h)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Main Component ──────────────────────────────────────────────

export default function DynamicPairsConfig({ values, defaults, rules, onChange, authFetch }) {
  const [isOpen, setIsOpen] = useState(false);

  if (!values) return null;

  const enabled = values.enabled ?? false;
  const volumeWindows = values.volume_windows ?? { '24h': 30, '48h': 0, '72h': 0 };
  const refreshInterval = values.refresh_interval ?? 1800;
  const whitelist = Array.isArray(values.whitelist) ? values.whitelist : [];
  const blacklist = Array.isArray(values.blacklist) ? values.blacklist : [];

  const totalPairs = Object.values(volumeWindows).reduce((s, v) => s + (v > 0 ? v : 0), 0);
  const activeWindows = Object.values(volumeWindows).filter((v) => v > 0).length;

  const handleVolumeChange = (period, value) => {
    onChange('DYNAMIC_PAIRS', `volume_windows.${period}`, value);
  };

  // Check for changes
  const defVW = defaults?.volume_windows ?? {};
  const hasChanges = (
    enabled !== (defaults?.enabled ?? false) ||
    volumeWindows['24h'] !== (defVW['24h'] ?? 30) ||
    volumeWindows['48h'] !== (defVW['48h'] ?? 0) ||
    volumeWindows['72h'] !== (defVW['72h'] ?? 0) ||
    refreshInterval !== (defaults?.refresh_interval ?? 1800) ||
    JSON.stringify(whitelist) !== JSON.stringify(defaults?.whitelist ?? []) ||
    JSON.stringify(blacklist) !== JSON.stringify(defaults?.blacklist ?? [])
  );

  return (
    <div className="glass-panel overflow-hidden transition-all duration-300 rounded-xl border border-white/5 hover:border-white/10">
      {/* Header */}
      <button
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full flex items-center gap-4 px-6 py-4 hover:bg-white/5 transition-colors group"
      >
        <div className="p-2 rounded-lg bg-bg-main border border-white/5 text-primary group-hover:scale-110 transition-transform">
          <RefreshCw className="w-5 h-5" />
        </div>

        <div className="flex-1 text-left">
          <div className="flex items-center gap-3">
            <span className="text-base font-semibold text-white">Dynamic Pairs</span>
            {enabled && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
                {activeWindows} window{activeWindows !== 1 ? 's' : ''} active
              </span>
            )}
            {!enabled && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-white/40 border border-white/5 uppercase tracking-wider">
                Disabled
              </span>
            )}
            {hasChanges && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-warning/20 text-warning border border-warning/20 font-medium animate-pulse">
                changes
              </span>
            )}
          </div>
          <p className="text-xs text-white/40 mt-1 font-mono">
            Multi-window volume scanning with whitelist/blacklist
          </p>
        </div>

        <ChevronDown className={cn("w-5 h-5 text-white/20 transition-transform duration-300", isOpen && "rotate-180 text-primary")} />
      </button>

      {isOpen && (
        <div className="px-6 py-5 bg-bg-main/30 border-t border-white/5 space-y-6">
          {/* Enabled + Refresh Interval */}
          <div className="flex items-center justify-between gap-4">
            <div className="flex items-center gap-4">
              <label className="flex items-center gap-3 cursor-pointer">
                <span className="text-sm text-white/60">Enabled</span>
                <button
                  onClick={() => onChange('DYNAMIC_PAIRS', 'enabled', !enabled)}
                  className={cn(
                    'relative inline-flex h-6 w-11 items-center rounded-full transition-colors',
                    enabled ? 'bg-emerald-500' : 'bg-white/10'
                  )}
                >
                  <span className={cn(
                    'inline-block h-4 w-4 transform rounded-full bg-white transition-transform',
                    enabled ? 'translate-x-6' : 'translate-x-1'
                  )} />
                </button>
              </label>
            </div>

            <div className="flex items-center gap-2">
              <span className="text-xs text-white/40">Refresh every</span>
              <input
                type="number"
                value={refreshInterval}
                onChange={(e) => {
                  const v = parseInt(e.target.value, 10);
                  if (!isNaN(v)) onChange('DYNAMIC_PAIRS', 'refresh_interval', Math.max(60, Math.min(86400, v)));
                }}
                min={60}
                max={86400}
                className="w-20 bg-bg-main border border-white/10 rounded-lg px-2 py-1 text-sm text-white text-right font-mono focus:outline-none focus:border-primary/50"
              />
              <span className="text-xs text-white/40">sec</span>
            </div>
          </div>

          {/* Volume Windows */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-medium text-white/50 uppercase tracking-wider">Volume Windows</h4>
              <span className="text-xs text-white/30">
                ~{totalPairs} pairs total (before dedup)
              </span>
            </div>
            <div className="space-y-2">
              <VolumeWindowRow
                label="24 Hours"
                period="24h"
                value={volumeWindows['24h'] ?? 0}
                onChange={handleVolumeChange}
              />
              <VolumeWindowRow
                label="48 Hours"
                period="48h"
                value={volumeWindows['48h'] ?? 0}
                onChange={handleVolumeChange}
              />
              <VolumeWindowRow
                label="72 Hours"
                period="72h"
                value={volumeWindows['72h'] ?? 0}
                onChange={handleVolumeChange}
              />
            </div>
          </div>

          {/* Whitelist */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-medium text-white/50 uppercase tracking-wider">
                Whitelist
                <span className="text-white/20 font-normal ml-2 normal-case">
                  {whitelist.length === 0 ? '(empty = allow all)' : `${whitelist.length} symbols`}
                </span>
              </h4>
            </div>
            <TagInput
              tags={whitelist}
              onChange={(newList) => onChange('DYNAMIC_PAIRS', 'whitelist', newList)}
              placeholder="Add symbol... (e.g. BTC)"
              colorClass="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20"
            />
          </div>

          {/* Blacklist */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-medium text-white/50 uppercase tracking-wider">
                Blacklist
                <span className="text-white/20 font-normal ml-2 normal-case">
                  {blacklist.length} symbols blocked
                </span>
              </h4>
            </div>
            <TagInput
              tags={blacklist}
              onChange={(newList) => onChange('DYNAMIC_PAIRS', 'blacklist', newList)}
              placeholder="Block symbol... (e.g. RIVER)"
              colorClass="bg-rose-500/10 text-rose-400 border border-rose-500/20"
            />
          </div>

          {/* Divider */}
          <div className="border-t border-white/5" />

          {/* Live Pairs */}
          <ActivePairsGrid authFetch={authFetch} />
        </div>
      )}
    </div>
  );
}
