import React, { useState, useCallback, useRef } from 'react';
import BacktestForm from '../components/BacktestForm';
import BacktestChart from '../components/BacktestChart';
import BacktestStats from '../components/BacktestStats';
import BacktestConfigPanel from '../components/BacktestConfigPanel';
import BacktestExitStats from '../components/BacktestExitStats';
import { useBacktestConfig } from '../hooks/useBacktestConfig';

const POLL_INTERVAL = 2000; // 2 seconds
const TF_LABELS = { '5m': 'M5', '15m': 'M15', '1h': 'H1', '4h': 'H4' };

export default function Backtest({ authFetch }) {
  const [result, setResult] = useState(null);
  const [loading, setLoading] = useState(false);
  const [tfLoading, setTfLoading] = useState(false);
  const [error, setError] = useState(null);
  const [elapsed, setElapsed] = useState(0);
  const [activeTimeframe, setActiveTimeframe] = useState(null);
  const pollRef = useRef(null);
  const jobIdRef = useRef(null);

  const {
    config: btConfig,
    defaults: btDefaults,
    rules: btRules,
    loading: btConfigLoading,
    changeCount: btChangeCount,
    updateField: btUpdateField,
    getOverrides: btGetOverrides,
    discardChanges: btDiscardChanges,
  } = useBacktestConfig(authFetch);

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const pollJob = useCallback((jobId) => {
    stopPolling();
    pollRef.current = setInterval(async () => {
      try {
        const raw = await authFetch(`/api/backtest/status/${jobId}`);
        const res = await raw.json();

        if (!res.success) {
          stopPolling();
          setError(res.error || 'Backtest failed');
          setLoading(false);
          return;
        }

        const { status, elapsed: jobElapsed } = res.data;
        setElapsed(jobElapsed || 0);

        if (status === 'completed') {
          stopPolling();
          setResult(res.data);
          setActiveTimeframe(res.data.chart?.timeframe || res.data.chart?.default_timeframe || '1h');
          setLoading(false);
        }
      } catch (err) {
        stopPolling();
        setError(err.message || 'Network error while polling');
        setLoading(false);
      }
    }, POLL_INTERVAL);
  }, [authFetch, stopPolling]);

  const handleRun = useCallback(async (params) => {
    setLoading(true);
    setError(null);
    setResult(null);
    setElapsed(0);
    setActiveTimeframe(null);
    stopPolling();

    // Merge config overrides (only changed fields, or null)
    const overrides = btGetOverrides();
    const fullParams = { ...params, config_overrides: overrides };

    try {
      const raw = await authFetch('/api/backtest/run', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(fullParams),
      });
      const res = await raw.json();

      if (!res.success) {
        setError(res.error || 'Backtest failed');
        setLoading(false);
        return;
      }

      const { job_id, status } = res.data;
      jobIdRef.current = job_id;

      if (status === 'completed') {
        setResult(res.data);
        setActiveTimeframe(res.data.chart?.timeframe || '1h');
        setLoading(false);
      } else {
        pollJob(job_id);
      }
    } catch (err) {
      setError(err.message || 'Network error');
      setLoading(false);
    }
  }, [authFetch, pollJob, stopPolling, btGetOverrides]);

  const switchTimeframe = useCallback(async (tf) => {
    if (!jobIdRef.current || tf === activeTimeframe) return;

    setTfLoading(true);
    try {
      const raw = await authFetch(`/api/backtest/status/${jobIdRef.current}?timeframe=${tf}`);
      const res = await raw.json();

      if (res.success && res.data.status === 'completed') {
        setResult(res.data);
        setActiveTimeframe(tf);
      }
    } catch (err) {
      setError(err.message || 'Failed to switch timeframe');
    } finally {
      setTfLoading(false);
    }
  }, [authFetch, activeTimeframe]);

  const availableTfs = result?.chart?.available_timeframes || [];

  return (
    <div className="space-y-4">
      <BacktestForm
        onSubmit={handleRun}
        loading={loading}
        authFetch={authFetch}
        configChangeCount={btChangeCount}
      />

      <BacktestConfigPanel
        config={btConfig}
        defaults={btDefaults}
        rules={btRules}
        changeCount={btChangeCount}
        updateField={btUpdateField}
        discardChanges={btDiscardChanges}
        loading={btConfigLoading}
        authFetch={authFetch}
      />

      {loading && (
        <div className="glass-card rounded-2xl p-4 border border-white/5">
          <p className="text-sm text-zinc-400 font-mono">
            Running backtest... {elapsed > 0 ? `${Math.round(elapsed)}s` : ''}
          </p>
        </div>
      )}

      {error && (
        <div className="glass-card rounded-2xl p-4 border border-rose-500/20 bg-rose-500/5">
          <p className="text-sm text-rose-400 font-mono">{error}</p>
        </div>
      )}

      {result && <BacktestStats summary={result.summary} />}

      {result && availableTfs.length > 0 && (
        <div className="flex items-center gap-2">
          {availableTfs.map((tf) => (
            <button
              key={tf}
              onClick={() => switchTimeframe(tf)}
              disabled={tfLoading}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${
                tf === activeTimeframe
                  ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                  : 'bg-white/5 text-zinc-400 border border-white/10 hover:bg-white/10 hover:text-zinc-200'
              } ${tfLoading ? 'opacity-50 cursor-wait' : ''}`}
            >
              {TF_LABELS[tf] || tf}
            </button>
          ))}
          {tfLoading && (
            <span className="text-xs text-zinc-500 font-mono ml-2">Loading...</span>
          )}
        </div>
      )}

      <BacktestChart data={result?.chart} />

      {result && <BacktestExitStats exitStats={result.summary?.exit_stats} />}
    </div>
  );
}
