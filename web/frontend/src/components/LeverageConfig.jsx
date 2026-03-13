import { useState, useEffect, useMemo, useCallback } from 'react';
import { Zap, Plus, X, ChevronDown, Pencil, Check, Pin, Pause, Play, Clock } from 'lucide-react';
import CryptoIcon from './CryptoIcon';
import { cn } from '../utils/cn';

// ── Tier colors based on leverage ────────────────────────────
const getTierInfo = (leverage) => {
  if (leverage >= 20) return { label: 'Tier 1 — Major', color: 'text-amber-400', bg: 'bg-amber-500/10 border-amber-500/20', dot: 'bg-amber-400' };
  if (leverage >= 10) return { label: 'Tier 2 — Large Cap', color: 'text-blue-400', bg: 'bg-blue-500/10 border-blue-500/20', dot: 'bg-blue-400' };
  if (leverage >= 7)  return { label: 'Tier 3 — Mid Cap', color: 'text-purple-400', bg: 'bg-purple-500/10 border-purple-500/20', dot: 'bg-purple-400' };
  return { label: 'Tier 4 — Altcoins', color: 'text-emerald-400', bg: 'bg-emerald-500/10 border-emerald-500/20', dot: 'bg-emerald-400' };
};

// ── Single Leverage Row ──────────────────────────────────────
function LeverageRow({ symbol, leverage, isDefault, isGhost, pauseInfo, onChangeValue, onRemove, onPin, onPause, onUnpause }) {
  const [editing, setEditing] = useState(false);
  const [tempVal, setTempVal] = useState(leverage);
  const tier = getTierInfo(leverage);
  const isPaused = !!pauseInfo;

  const handleSave = () => {
    const val = Math.max(1, Math.min(125, Math.round(tempVal)));
    onChangeValue(symbol, val);
    setEditing(false);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleSave();
    if (e.key === 'Escape') { setTempVal(leverage); setEditing(false); }
  };

  const displaySymbol = symbol === 'default' ? 'Default' : symbol.replace('USDT', '');

  return (
    <div className={cn(
      'flex items-center gap-3 py-2 px-3 rounded-lg transition-colors group',
      isGhost ? 'hover:bg-white/3 opacity-60' : 'hover:bg-white/5',
      isPaused && 'bg-orange-500/5 border-l-2 border-l-orange-500/40'
    )}>
      {/* Icon + Symbol */}
      <div className="flex items-center gap-2 flex-1 min-w-0">
        {symbol === 'default' ? (
          <div className="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center">
            <Zap className="w-3.5 h-3.5 text-white/50" />
          </div>
        ) : (
          <CryptoIcon symbol={symbol} className="w-6 h-6" />
        )}
        <span className={cn(
          'text-sm font-mono',
          symbol === 'default' ? 'text-white/50 italic' : 'text-white/90',
          isPaused && 'text-orange-300/70'
        )}>
          {displaySymbol}
        </span>
        {isGhost && !isPaused && (
          <span className="text-[10px] text-white/20 ml-1">default</span>
        )}
        {/* Pause badge */}
        {isPaused && (
          <button
            onClick={() => onUnpause(symbol)}
            className="flex items-center gap-1 px-1.5 py-0.5 rounded-full bg-orange-500/20 border border-orange-500/30 text-[10px] text-orange-400 hover:bg-orange-500/30 transition-colors cursor-pointer"
            title="Click to unpause"
          >
            <Pause className="w-2.5 h-2.5" />
            {pauseInfo.remaining_display}
          </button>
        )}
      </div>

      {/* Leverage value + actions */}
      <div className="flex items-center gap-2">
        {editing ? (
          <div className="flex items-center gap-1">
            <input
              type="number"
              value={tempVal}
              onChange={(e) => setTempVal(Number(e.target.value))}
              onKeyDown={handleKeyDown}
              autoFocus
              min={1}
              max={125}
              className="w-16 bg-bg-primary border border-white/20 rounded px-2 py-1 text-sm text-white text-center focus:outline-none focus:border-emerald-500"
            />
            <button onClick={handleSave} className="p-1 text-emerald-400 hover:text-emerald-300">
              <Check className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : isGhost ? (
          /* Ghost row: show default value + pin button */
          <div className="flex items-center gap-2">
            <span className={cn('px-3 py-1 rounded-md border text-sm font-bold', tier.bg, tier.color)}>
              {leverage}x
            </span>
            <button
              onClick={() => onPin(symbol)}
              className="p-1 text-white/0 group-hover:text-white/30 hover:!text-amber-400 transition-colors"
              title="Pin — add custom leverage for this symbol"
            >
              <Pin className="w-3.5 h-3.5" />
            </button>
          </div>
        ) : (
          <button
            onClick={() => { setTempVal(leverage); setEditing(true); }}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1 rounded-md border text-sm font-bold transition-all',
              'hover:scale-105 cursor-pointer',
              tier.bg, tier.color
            )}
          >
            {leverage}x
            <Pencil className="w-3 h-3 opacity-0 group-hover:opacity-60 transition-opacity" />
          </button>
        )}

        {/* Pause/Unpause button (not for default) */}
        {!isDefault && !editing && (
          isPaused ? (
            <button
              onClick={() => onUnpause(symbol)}
              className="p-1 text-orange-400/60 hover:text-emerald-400 transition-colors"
              title="Resume trading"
            >
              <Play className="w-3.5 h-3.5" />
            </button>
          ) : (
            <button
              onClick={() => onPause(symbol)}
              className="p-1 text-white/0 group-hover:text-white/20 hover:!text-orange-400 transition-colors"
              title="Pause trading for 8 hours"
            >
              <Pause className="w-3.5 h-3.5" />
            </button>
          )
        )}

        {/* Remove button (not for default or ghost) */}
        {!isDefault && !isGhost && (
          <button
            onClick={() => onRemove(symbol)}
            className="p-1 text-white/0 group-hover:text-white/30 hover:!text-rose-400 transition-colors"
            title="Remove"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        )}
      </div>
    </div>
  );
}

// ── Add Symbol Form ──────────────────────────────────────────
function AddSymbolForm({ existingSymbols, onAdd }) {
  const [symbol, setSymbol] = useState('');
  const [leverage, setLeverage] = useState(5);
  const [isOpen, setIsOpen] = useState(false);

  const handleAdd = () => {
    let sym = symbol.trim().toUpperCase();
    if (!sym) return;
    if (!sym.endsWith('USDT')) sym += 'USDT';
    if (existingSymbols.includes(sym)) return;
    onAdd(sym, Math.max(1, Math.min(125, Math.round(leverage))));
    setSymbol('');
    setLeverage(5);
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleAdd();
    }
  };

  if (!isOpen) {
    return (
      <button
        onClick={() => setIsOpen(true)}
        className="flex items-center gap-2 px-3 py-2 text-sm text-white/40 hover:text-white/70 hover:bg-white/5 rounded-lg transition-colors w-full"
      >
        <Plus className="w-4 h-4" />
        Add symbol
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2 px-3 py-2 bg-white/5 rounded-lg">
      <input
        type="text"
        value={symbol}
        onChange={(e) => setSymbol(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="e.g. DOGE"
        autoFocus
        className="flex-1 bg-transparent border border-white/10 rounded px-2 py-1 text-sm text-white placeholder:text-white/20 focus:outline-none focus:border-white/30 font-mono"
      />
      <input
        type="number"
        value={leverage}
        onChange={(e) => setLeverage(Number(e.target.value))}
        onKeyDown={handleKeyDown}
        min={1}
        max={125}
        className="w-16 bg-transparent border border-white/10 rounded px-2 py-1 text-sm text-white text-center focus:outline-none focus:border-white/30"
        placeholder="5x"
      />
      <button
        onClick={handleAdd}
        disabled={!symbol.trim()}
        className="p-1.5 bg-emerald-500/20 text-emerald-400 rounded hover:bg-emerald-500/30 disabled:opacity-30 transition-colors"
      >
        <Plus className="w-4 h-4" />
      </button>
      <button
        onClick={() => { setIsOpen(false); setSymbol(''); }}
        className="p-1.5 text-white/30 hover:text-white/60 transition-colors"
      >
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

// ── Main LeverageConfig ──────────────────────────────────────
export default function LeverageConfig({ values, defaults, onChange, authFetch }) {
  const doFetch = authFetch || fetch;
  const [isOpen, setIsOpen] = useState(false);
  const [openPositionSymbols, setOpenPositionSymbols] = useState([]);
  const [scanningSymbols, setScanningSymbols] = useState([]);
  const [pausedSymbols, setPausedSymbols] = useState({});
  const [recentSymbols, setRecentSymbols] = useState([]);

  // Fetch open positions + scanning pairs + paused symbols + recent activity
  useEffect(() => {
    if (!isOpen) return;

    const fetchData = async () => {
      try {
        const [posRes, pairsRes, pausedRes, activityRes] = await Promise.all([
          doFetch('/api/positions').catch(() => null),
          doFetch('/api/active-pairs').catch(() => null),
          doFetch('/api/symbols/paused').catch(() => null),
          doFetch('/api/activity').catch(() => null),
        ]);

        if (posRes?.ok) {
          const data = await posRes.json();
          if (data.success && Array.isArray(data.data)) {
            const syms = [...new Set(data.data.map((p) => p.symbol).filter(Boolean))];
            setOpenPositionSymbols(syms);
          }
        }

        if (pairsRes?.ok) {
          const data = await pairsRes.json();
          if (data.success && data.data?.pairs) {
            setScanningSymbols(data.data.pairs.map((p) => p.symbol || p));
          }
        }

        if (pausedRes?.ok) {
          const data = await pausedRes.json();
          if (data.success && data.data) {
            setPausedSymbols(data.data);
          }
        }

        if (activityRes?.ok) {
          const data = await activityRes.json();
          if (data.success && Array.isArray(data.data)) {
            const syms = [...new Set(data.data.map((t) => t.symbol).filter(Boolean))];
            setRecentSymbols(syms);
          }
        }
      } catch {
        // silent
      }
    };

    fetchData();
    const interval = setInterval(fetchData, 15000);
    return () => clearInterval(interval);
  }, [isOpen]);

  // Build local leverage map from values prop
  const leverageMap = useMemo(() => {
    return values && typeof values === 'object' ? { ...values } : {};
  }, [values]);

  const defaultLev = leverageMap.default || 5;

  // Count changes from defaults
  const changeCount = useMemo(() => {
    if (!defaults) return 0;
    let count = 0;
    const allKeys = new Set([...Object.keys(leverageMap), ...Object.keys(defaults)]);
    for (const key of allKeys) {
      if (leverageMap[key] !== defaults[key]) count++;
    }
    return count;
  }, [leverageMap, defaults]);

  // Open position symbols — show with their leverage (custom or default)
  const openPairsWithLev = useMemo(() => {
    return openPositionSymbols
      .filter((sym) => sym !== 'default')
      .map((sym) => [sym, leverageMap[sym] || defaultLev])
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [openPositionSymbols, leverageMap, defaultLev]);

  // Scanning pairs NOT already in open positions
  const scanPairsWithLev = useMemo(() => {
    const openSet = new Set(openPositionSymbols);
    return scanningSymbols
      .filter((sym) => sym !== 'default' && !openSet.has(sym))
      .map((sym) => [sym, leverageMap[sym] || defaultLev])
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [scanningSymbols, openPositionSymbols, leverageMap, defaultLev]);

  // Recently traded symbols NOT in open positions or scanning
  const recentPairsWithLev = useMemo(() => {
    const shownSet = new Set([...openPositionSymbols, ...scanningSymbols]);
    return recentSymbols
      .filter((sym) => !shownSet.has(sym))
      .slice(0, 10)
      .map((sym) => [sym, leverageMap[sym] || defaultLev]);
  }, [recentSymbols, openPositionSymbols, scanningSymbols, leverageMap, defaultLev]);

  // Count of paused symbols
  const pausedCount = Object.keys(pausedSymbols).length;

  // Total visible symbols
  const visibleCount = openPairsWithLev.length + scanPairsWithLev.length + recentPairsWithLev.length;

  // Send full LEVERAGE dict update
  const sendUpdate = useCallback((newMap) => {
    onChange('LEVERAGE', '__full__', newMap);
  }, [onChange]);

  const handleChangeValue = useCallback((symbol, newLev) => {
    sendUpdate({ ...leverageMap, [symbol]: newLev });
  }, [leverageMap, sendUpdate]);

  const handleRemove = useCallback((symbol) => {
    const next = { ...leverageMap };
    delete next[symbol];
    sendUpdate(next);
  }, [leverageMap, sendUpdate]);

  const handleAdd = useCallback((symbol, lev) => {
    sendUpdate({ ...leverageMap, [symbol]: lev });
  }, [leverageMap, sendUpdate]);

  // Pin a ghost pair: add it to LEVERAGE with default value
  const handlePin = useCallback((symbol) => {
    sendUpdate({ ...leverageMap, [symbol]: defaultLev });
  }, [leverageMap, defaultLev, sendUpdate]);

  // Pause a symbol for 8 hours
  const handlePause = useCallback(async (symbol) => {
    try {
      const res = await doFetch(`/api/symbols/${encodeURIComponent(symbol)}/pause`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hours: 8 }),
      });
      const data = await res.json();
      if (data.success) {
        setPausedSymbols((prev) => ({
          ...prev,
          [symbol]: { expiry: data.expiry, remaining_seconds: 8 * 3600, remaining_display: '8h 0m' },
        }));
      }
    } catch {
      // silent
    }
  }, [doFetch]);

  // Unpause a symbol
  const handleUnpause = useCallback(async (symbol) => {
    try {
      const res = await doFetch(`/api/symbols/${encodeURIComponent(symbol)}/pause`, {
        method: 'DELETE',
      });
      const data = await res.json();
      if (data.success) {
        setPausedSymbols((prev) => {
          const next = { ...prev };
          delete next[symbol];
          return next;
        });
      }
    } catch {
      // silent
    }
  }, [doFetch]);

  return (
    <div className="glass-panel overflow-hidden transition-all duration-300 rounded-xl border border-white/5 hover:border-white/10">
      {/* Header */}
      <button
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full flex items-center gap-4 px-6 py-4 hover:bg-white/5 transition-colors group"
      >
        <div className="p-2 rounded-lg bg-bg-main border border-white/5 text-primary group-hover:scale-110 transition-transform">
          <Zap className="w-5 h-5" />
        </div>
        <div className="flex-1 text-left">
          <div className="flex items-center gap-3">
            <span className="text-base font-semibold text-white">Leverage</span>
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-white/40 border border-white/5 uppercase tracking-wider">
              {visibleCount} pairs
            </span>
            {pausedCount > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-orange-500/20 text-orange-400 border border-orange-500/20 font-medium">
                {pausedCount} paused
              </span>
            )}
            {changeCount > 0 && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-warning/20 text-warning border border-warning/20 font-medium animate-pulse">
                {changeCount} changes
              </span>
            )}
          </div>
          <p className="text-xs text-white/40 mt-1 font-mono">Per-symbol leverage tiers (1–125x)</p>
        </div>
        <ChevronDown className={cn(
          'w-5 h-5 text-white/20 transition-transform duration-300',
          isOpen && 'rotate-180 text-primary'
        )} />
      </button>

      {/* Content */}
      {isOpen && (
        <div className="px-6 py-4 bg-bg-main/30 border-t border-white/5 space-y-4">
          {/* Default leverage */}
          <div className="border border-white/5 rounded-lg overflow-hidden">
            <div className="px-3 py-2 bg-white/5 border-b border-white/5">
              <span className="text-xs text-white/50 font-medium">Default Leverage</span>
              <span className="text-xs text-white/30 ml-2">— applies to all unlisted symbols</span>
            </div>
            <LeverageRow
              symbol="default"
              leverage={defaultLev}
              isDefault={true}
              isGhost={false}
              pauseInfo={null}
              onChangeValue={handleChangeValue}
              onRemove={() => {}}
              onPin={() => {}}
              onPause={() => {}}
              onUnpause={() => {}}
            />
          </div>

          {/* Open positions */}
          {openPairsWithLev.length > 0 && (
            <div className="border border-white/5 rounded-lg overflow-hidden">
              <div className="px-3 py-2 bg-white/5 border-b border-white/5 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                <span className="text-xs font-medium text-white/50">Open Positions</span>
                <span className="text-xs text-white/20 ml-auto">{openPairsWithLev.length}</span>
              </div>
              <div className="divide-y divide-white/5">
                {openPairsWithLev.map(([sym, lev]) => (
                  <LeverageRow
                    key={sym}
                    symbol={sym}
                    leverage={lev}
                    isDefault={false}
                    isGhost={!(sym in leverageMap)}
                    pauseInfo={pausedSymbols[sym] || null}
                    onChangeValue={handleChangeValue}
                    onRemove={sym in leverageMap ? handleRemove : () => {}}
                    onPin={!(sym in leverageMap) ? handlePin : () => {}}
                    onPause={handlePause}
                    onUnpause={handleUnpause}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Scanning pairs (not in open positions) */}
          {scanPairsWithLev.length > 0 && (
            <div className="border border-white/5 rounded-lg overflow-hidden">
              <div className="px-3 py-2 bg-white/5 border-b border-white/5 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-blue-400/60 animate-pulse" />
                <span className="text-xs font-medium text-white/40">Scanning</span>
                <span className="text-xs text-white/20 ml-auto">{scanPairsWithLev.length}</span>
              </div>
              <div className="divide-y divide-white/5">
                {scanPairsWithLev.map(([sym, lev]) => (
                  <LeverageRow
                    key={sym}
                    symbol={sym}
                    leverage={lev}
                    isDefault={false}
                    isGhost={!(sym in leverageMap)}
                    pauseInfo={pausedSymbols[sym] || null}
                    onChangeValue={handleChangeValue}
                    onRemove={sym in leverageMap ? handleRemove : () => {}}
                    onPin={!(sym in leverageMap) ? handlePin : () => {}}
                    onPause={handlePause}
                    onUnpause={handleUnpause}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Empty state */}
          {openPairsWithLev.length === 0 && scanPairsWithLev.length === 0 && recentPairsWithLev.length === 0 && (
            <div className="text-center py-6 text-white/20 text-xs">
              No open positions or recent trades
            </div>
          )}

          {/* Recently traded pairs (not in open positions) */}
          {recentPairsWithLev.length > 0 && (
            <div className="border border-white/5 border-dashed rounded-lg overflow-hidden">
              <div className="px-3 py-2 bg-white/3 border-b border-white/5 flex items-center gap-2">
                <Clock className="w-3 h-3 text-white/20" />
                <span className="text-xs font-medium text-white/25">Recently Traded</span>
                <span className="text-xs text-white/20 ml-auto">{recentPairsWithLev.length}</span>
              </div>
              <div className="divide-y divide-white/5">
                {recentPairsWithLev.map(([sym, lev]) => (
                  <LeverageRow
                    key={sym}
                    symbol={sym}
                    leverage={lev}
                    isDefault={false}
                    isGhost={!(sym in leverageMap)}
                    pauseInfo={pausedSymbols[sym] || null}
                    onChangeValue={handleChangeValue}
                    onRemove={sym in leverageMap ? handleRemove : () => {}}
                    onPin={!(sym in leverageMap) ? handlePin : () => {}}
                    onPause={handlePause}
                    onUnpause={handleUnpause}
                  />
                ))}
              </div>
            </div>
          )}

          {/* Add new symbol */}
          <AddSymbolForm
            existingSymbols={[...openPositionSymbols, ...scanningSymbols, ...recentSymbols, ...Object.keys(leverageMap)]}
            onAdd={handleAdd}
          />
        </div>
      )}
    </div>
  );
}
