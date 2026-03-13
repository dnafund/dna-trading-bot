import React, { useState, useEffect } from 'react';
import { Activity, Zap, RefreshCw, WifiOff, XCircle, Clock } from 'lucide-react';
import PositionsTable from '../components/PositionsTable';
import { ToastContainer } from '../components/Toast';
import { useToast } from '../hooks/useToast';

function timeAgo(date) {
  if (!date) return 'never';
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (seconds < 3) return 'just now';
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

function Positions({ data, connected, lastUpdated, refresh, authFetch, highlightPositionId, onPositionViewed }) {
  const { positions, stats } = data;
  const [spinning, setSpinning] = useState(false);
  const [displayAgo, setDisplayAgo] = useState('');
  const [confirmCloseAll, setConfirmCloseAll] = useState(false);
  const [closingAll, setClosingAll] = useState(false);
  const [pendingOrders, setPendingOrders] = useState([]);
  const [cancellingOrder, setCancellingOrder] = useState(null);
  const { toasts, addToast, removeToast } = useToast();
  const doFetch = authFetch || fetch;

  // Fetch pending EMA610 limit orders
  useEffect(() => {
    const fetchPending = async () => {
      try {
        const res = await doFetch('/api/ema610-orders');
        const json = await res.json();
        if (json.success) setPendingOrders(json.data || []);
      } catch { /* ignore */ }
    };
    fetchPending();
    const timer = setInterval(fetchPending, 30000);
    return () => clearInterval(timer);
  }, [doFetch]);

  const handleCancelEma610 = async (order) => {
    const key = `${order.symbol}_${order.timeframe}`;
    setCancellingOrder(key);
    try {
      const res = await doFetch('/api/ema610-orders/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: order.symbol, timeframe: order.timeframe }),
      });
      const data = await res.json();
      if (data.success) {
        addToast(data.message, 'success', 5000);
        setPendingOrders(prev => prev.filter(o =>
          !(o.symbol === order.symbol && o.timeframe === order.timeframe)
        ));
      } else {
        addToast(data.error || 'Cancel failed', 'error', 6000);
      }
    } catch (err) {
      addToast(`Network error: ${err.message}`, 'error', 6000);
    } finally {
      setCancellingOrder(null);
    }
  };

  const handlePositionAction = async (action, positionId, params = {}) => {
    const endpoints = {
      close: `/api/positions/${positionId}/close`,
      partial_close: `/api/positions/${positionId}/partial-close`,
      cancel_tp: `/api/positions/${positionId}/cancel-tp`,
      modify_sl: `/api/positions/${positionId}/modify-sl`,
      modify_tp: `/api/positions/${positionId}/modify-tp`,
    };

    const url = endpoints[action];
    if (!url) return;

    const body = action === 'close' ? {} :
                 action === 'partial_close' ? { percent: params.percent } :
                 action === 'cancel_tp' ? { level: params.level } :
                 action === 'modify_sl' ? { price: params.price } :
                 action === 'modify_tp' ? { level: params.level, price: params.price } : {};

    try {
      const res = await doFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const data = await res.json();

      if (data.success) {
        addToast(data.message, 'success', 5000);
        if (refresh) {
          refresh();
          setTimeout(() => refresh(), 1500);
        }
      } else {
        addToast(data.error || 'Command failed', 'error', 6000);
      }
    } catch (err) {
      addToast(`Network error: ${err.message}`, 'error', 6000);
    }
  };

  const handleCloseAll = async () => {
    setClosingAll(true);
    try {
      const res = await doFetch('/api/positions/close-all', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
      });
      const data = await res.json();

      if (data.success) {
        addToast(data.message, 'success', 6000);
        if (refresh) {
          refresh();
          setTimeout(() => refresh(), 2000);
        }
      } else {
        addToast(data.error || 'Close all failed', 'error', 6000);
      }
    } catch (err) {
      addToast(`Network error: ${err.message}`, 'error', 6000);
    } finally {
      setClosingAll(false);
      setConfirmCloseAll(false);
    }
  };

  // Update the "X seconds ago" display every second
  useEffect(() => {
    setDisplayAgo(timeAgo(lastUpdated));
    const timer = setInterval(() => {
      setDisplayAgo(timeAgo(lastUpdated));
    }, 1000);
    return () => clearInterval(timer);
  }, [lastUpdated]);

  const handleRefresh = () => {
    if (refresh) refresh();
    setSpinning(true);
    setTimeout(() => setSpinning(false), 600);
  };

  return (
    <div className="space-y-6">
      {/* Header with Stats */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white mb-2">Active Positions</h1>
          <p className="text-text-muted">
            Manage your open positions and monitor real-time performance.
          </p>
        </div>

        {/* Quick Stats */}
        <div className="flex gap-4">
          <div className="glass-panel px-4 py-3 rounded-xl flex items-center gap-3">
            <div className="p-2 rounded-lg bg-primary/10 text-primary">
              <Activity className="w-5 h-5" />
            </div>
            <div>
              <p className="text-xs text-text-muted uppercase tracking-wider">Open Positions</p>
              <p className="text-xl font-bold text-white font-mono">{positions.length}</p>
            </div>
          </div>

          {(() => {
            const openPnl = positions.reduce((sum, p) => sum + (p.pnl_usd || 0), 0);
            const rounded = Math.round(openPnl * 100) / 100;
            return (
              <div className="glass-panel px-4 py-3 rounded-xl flex items-center gap-3">
                <div className={`p-2 rounded-lg ${rounded >= 0 ? 'bg-profit/10 text-profit' : 'bg-loss/10 text-loss'}`}>
                  <Zap className="w-5 h-5" />
                </div>
                <div>
                  <p className="text-xs text-text-muted uppercase tracking-wider">Total PnL</p>
                  <p className={`text-xl font-bold font-mono ${rounded >= 0 ? 'text-profit' : 'text-loss'}`}>
                    {rounded >= 0 ? '+' : ''}{rounded.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    <span className="text-sm font-normal text-text-muted ml-1">USDT</span>
                  </p>
                </div>
              </div>
            );
          })()}

          {pendingOrders.length > 0 && (
            <div className="glass-panel px-4 py-3 rounded-xl flex items-center gap-3">
              <div className="p-2 rounded-lg bg-amber-500/10 text-amber-400">
                <Clock className="w-5 h-5" />
              </div>
              <div>
                <p className="text-xs text-text-muted uppercase tracking-wider">EMA610 Limits</p>
                <p className="text-xl font-bold text-amber-400 font-mono">{pendingOrders.length}</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Pending EMA610 Limit Orders */}
      {pendingOrders.length > 0 && (
        <div className="glass-panel rounded-2xl overflow-hidden">
          <div className="px-6 py-3 border-b border-white/5 bg-white/[0.02]">
            <h2 className="text-sm font-medium text-text-muted uppercase tracking-wider">
              Pending EMA610 Limit Orders
            </h2>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-text-muted text-xs uppercase tracking-wider border-b border-white/5">
                  <th className="px-4 py-2 text-left">Symbol</th>
                  <th className="px-4 py-2 text-left">TF</th>
                  <th className="px-4 py-2 text-left">Side</th>
                  <th className="px-4 py-2 text-right">Limit Price</th>
                  <th className="px-4 py-2 text-right">Margin</th>
                  <th className="px-4 py-2 text-right">Leverage</th>
                  <th className="px-4 py-2 text-right w-16"></th>
                </tr>
              </thead>
              <tbody>
                {pendingOrders.map((o, i) => (
                  <tr key={i} className="border-b border-white/5 hover:bg-white/[0.02]">
                    <td className="px-4 py-2 text-white font-medium">{o.symbol?.replace('USDT', '')}</td>
                    <td className="px-4 py-2 text-text-muted uppercase">{o.timeframe}</td>
                    <td className={`px-4 py-2 font-medium ${o.side === 'BUY' ? 'text-profit' : 'text-loss'}`}>
                      {o.side}
                    </td>
                    <td className="px-4 py-2 text-right text-white font-mono">
                      {Number(o.limit_price).toFixed(o.limit_price < 0.01 ? 8 : 4)}
                    </td>
                    <td className="px-4 py-2 text-right text-text-muted">${o.margin}</td>
                    <td className="px-4 py-2 text-right text-text-muted">{o.leverage}x</td>
                    <td className="px-4 py-2 text-right">
                      <button
                        onClick={() => handleCancelEma610(o)}
                        disabled={cancellingOrder === `${o.symbol}_${o.timeframe}`}
                        className="p-1.5 rounded-lg text-white/30 hover:text-rose-400 hover:bg-rose-500/10 transition-colors disabled:opacity-50"
                        title={`Cancel ${o.symbol?.replace('USDT', '')} ${o.timeframe?.toUpperCase()}`}
                      >
                        <XCircle className="w-4 h-4" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Main Table Panel */}
      <div className="glass-panel rounded-2xl overflow-hidden">
        {/* Table toolbar */}
        <div className="flex items-center justify-between px-6 py-3 border-b border-white/5 bg-white/[0.02]">
          {/* Connection status + last updated */}
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-1.5">
              {connected === true ? (
                <>
                  <span className="live-dot" />
                  <span className="text-xs text-emerald-400/80 font-medium">Live</span>
                </>
              ) : connected === false ? (
                <>
                  <WifiOff className="w-3.5 h-3.5 text-rose-400/60" />
                  <span className="text-xs text-rose-400/60 font-medium">Disconnected</span>
                </>
              ) : (
                <>
                  <span className="live-dot live-dot--warning" />
                  <span className="text-xs text-amber-400/80 font-medium">Connecting...</span>
                </>
              )}
            </div>
            {lastUpdated && (
              <span className="text-[11px] text-white/25 font-mono">
                Updated {displayAgo}
              </span>
            )}
          </div>

          {/* Actions */}
          <div className="flex items-center gap-2">
            {/* Close All */}
            {positions.length > 0 && (
              confirmCloseAll ? (
                <div className="flex items-center gap-2 animate-in fade-in duration-200">
                  <span className="text-xs text-rose-400 font-mono">Close {positions.length} positions?</span>
                  <button
                    onClick={handleCloseAll}
                    disabled={closingAll}
                    className="px-3 py-1.5 rounded-lg text-xs font-bold bg-rose-500/20 text-rose-400 border border-rose-500/30 hover:bg-rose-500/30 transition-colors disabled:opacity-50"
                  >
                    {closingAll ? 'Closing...' : 'Confirm'}
                  </button>
                  <button
                    onClick={() => setConfirmCloseAll(false)}
                    className="px-2 py-1.5 rounded-lg text-xs text-white/40 hover:text-white/80 hover:bg-white/5 transition-colors"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmCloseAll(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-rose-400/70 hover:text-rose-400 hover:bg-rose-500/10 border border-transparent hover:border-rose-500/20 transition-all active:scale-[0.95]"
                  title="Close all positions"
                >
                  <XCircle className="w-3.5 h-3.5" />
                  Close All
                </button>
              )
            )}

            {/* Refresh */}
            <button
              onClick={handleRefresh}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-white/50 hover:text-white/90 hover:bg-white/5 transition-colors active:scale-[0.95]"
              title="Refresh positions"
            >
              <RefreshCw className={`w-3.5 h-3.5 transition-transform duration-500 ${spinning ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </div>
        </div>

        {/* Table */}
        <div className="p-6">
          <PositionsTable positions={positions} onAction={handlePositionAction} highlightId={highlightPositionId} onPositionViewed={onPositionViewed} />
        </div>
      </div>
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </div>
  );
}

export default Positions;
