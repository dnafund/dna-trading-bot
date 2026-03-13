import { useCallback } from 'react';
import { Save, RotateCcw, X, AlertTriangle, Check, Loader2 } from 'lucide-react';
import ConfigSection from '../components/ConfigSection';
import DynamicPairsConfig from '../components/DynamicPairsConfig';
import LeverageConfig from '../components/LeverageConfig';
import { useConfig } from '../hooks/useConfig';
import { useToast } from '../hooks/useToast';
import { ToastContainer } from '../components/Toast';

const SECTION_ORDER = [
  'STANDARD_ENTRY', 'ENTRY',
  'CHANDELIER_EXIT', 'RISK_MANAGEMENT',
  'STANDARD_EXIT', 'EMA610_EXIT', 'RSI_DIV_EXIT', 'SD_ENTRY_CONFIG', 'DIVERGENCE_CONFIG', 'EMA610_ENTRY',
  'SMART_SL', 'INDICATORS',
  'DYNAMIC_PAIRS', 'LEVERAGE',
];

export default function Settings({ authFetch }) {
  const { config, defaults, rules, loading, error, saving, changeCount, updateField, saveConfig, resetConfig, discardChanges } = useConfig(authFetch);
  const { toasts, addToast, removeToast } = useToast();

  const handleSave = useCallback(async () => {
    if (changeCount === 0) {
      addToast('No changes to save', 'info');
      return;
    }
    const result = await saveConfig();
    if (result.success) {
      const changedCount = result.data?.changed ? Object.values(result.data.changed).reduce((sum, section) => sum + Object.keys(section).length, 0) : changeCount;
      addToast(`Saved ${changedCount} changes — bot updated`, 'success');
    } else {
      const errors = result.data?.errors || result.errors || [result.error || 'Unknown error'];
      addToast(`Save failed: ${errors.join(', ')}`, 'error', 8000);
    }
  }, [changeCount, saveConfig, addToast]);

  const handleReset = useCallback(async () => {
    if (!window.confirm('Reset ALL config sections to defaults? This affects the live bot immediately.')) return;
    const result = await resetConfig();
    if (result.success) {
      addToast('Reset all config to defaults', 'success');
    } else {
      addToast(`Reset failed: ${result.error || 'Unknown error'}`, 'error');
    }
  }, [resetConfig, addToast]);

  const handleDiscard = useCallback(() => {
    discardChanges();
    addToast('Discarded local changes', 'info');
  }, [discardChanges, addToast]);

  if (loading) return <div className="flex h-96 items-center justify-center text-white/30 animate-pulse"><Loader2 className="w-8 h-8 animate-spin mr-3" /> Loading Configuration...</div>;
  if (error) return <div className="flex h-96 items-center justify-center flex-col text-rose-500"><AlertTriangle className="w-10 h-10 mb-2" /> <p>Failed to load config</p><p className="text-sm opacity-50">{error}</p></div>;
  if (!config || !defaults) return null;

  return (
    <div className="max-w-5xl mx-auto space-y-8 pb-20">

      {/* Sticky Header with Actions */}
      <div className="sticky top-0 z-20 glass-panel p-4 rounded-xl flex items-center justify-between mb-8 shadow-2xl ring-1 ring-white/10">
        <div>
          <h2 className="text-lg font-bold text-white">Strategy Configuration</h2>
          <p className="text-xs text-white/40">Real-time parameter tuning</p>
        </div>

        <div className="flex items-center gap-3">
          {changeCount > 0 && (
            <span className="text-xs text-warning mr-2 animate-pulse font-medium">
              {changeCount} unsaved change{changeCount !== 1 ? 's' : ''}
            </span>
          )}

          <button onClick={handleDiscard} disabled={saving || changeCount === 0} className="p-2 rounded-lg text-white/40 hover:text-white hover:bg-white/5 disabled:opacity-20 transition-colors" title="Discard Changes">
            <X className="w-5 h-5" />
          </button>

          <button onClick={handleReset} disabled={saving} className="p-2 rounded-lg text-white/40 hover:text-rose-400 hover:bg-rose-500/10 disabled:opacity-20 transition-colors" title="Reset Defaults">
            <RotateCcw className="w-5 h-5" />
          </button>

          <div className="w-[1px] h-8 bg-white/10 mx-1" />

          <button
            onClick={handleSave}
            disabled={saving || changeCount === 0}
            className="flex items-center gap-2 px-6 py-2 rounded-lg bg-primary hover:bg-white/90 text-black font-medium shadow-lg shadow-white/10 hover:shadow-white/20 disabled:opacity-50 disabled:cursor-not-allowed transition-all active:scale-95"
          >
            {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
            Save Changes
          </button>
        </div>
      </div>

      {/* Accordion List */}
      <div className="grid grid-cols-1 gap-4 animate-fade-in-up">
        {SECTION_ORDER.filter((key) => config[key] !== undefined).map((sectionKey) => (
          sectionKey === 'DYNAMIC_PAIRS' ? (
            <DynamicPairsConfig
              key={sectionKey}
              values={config[sectionKey]}
              defaults={defaults[sectionKey]}
              rules={rules?.[sectionKey]}
              authFetch={authFetch}
              onChange={updateField}
            />
          ) : sectionKey === 'LEVERAGE' ? (
            <LeverageConfig
              key={sectionKey}
              values={config[sectionKey]}
              defaults={defaults[sectionKey]}
              onChange={updateField}
              authFetch={authFetch}
            />
          ) : (
            <ConfigSection
              key={sectionKey}
              sectionKey={sectionKey}
              values={config[sectionKey]}
              defaults={defaults[sectionKey]}
              rules={rules?.[sectionKey]}
              onChange={updateField}
              defaultOpen={sectionKey === 'CHANDELIER_EXIT'}
            />
          )
        ))}
      </div>

      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </div>
  );
}
