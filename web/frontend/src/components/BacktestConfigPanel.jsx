import React, { useState } from 'react';
import { ChevronDown, RotateCcw, SlidersHorizontal, Loader2 } from 'lucide-react';
import ConfigSection from './ConfigSection';
import LeverageConfig from './LeverageConfig';

// Sections relevant to backtest, in display order
const BACKTEST_SECTIONS = [
  'STANDARD_ENTRY', 'ENTRY',
  'CHANDELIER_EXIT', 'RISK_MANAGEMENT',
  'STANDARD_EXIT', 'EMA610_EXIT', 'EMA610_ENTRY',
  'SMART_SL', 'INDICATORS',
  'FEES', 'LEVERAGE',
];

export default function BacktestConfigPanel({
  config,
  defaults,
  rules,
  changeCount,
  updateField,
  discardChanges,
  loading,
  authFetch,
}) {
  const [isOpen, setIsOpen] = useState(false);

  if (loading || !config || !defaults) {
    return null;
  }

  return (
    <div className="glass-card rounded-2xl border border-white/10 overflow-hidden">
      {/* Header — always visible */}
      <button
        onClick={() => setIsOpen((prev) => !prev)}
        className="w-full px-4 py-3 flex items-center justify-between hover:bg-white/[0.02] transition-colors"
      >
        <div className="flex items-center gap-3">
          <SlidersHorizontal className="w-4 h-4 text-cyan-400" />
          <span className="text-sm font-medium text-zinc-200">
            Strategy Config
          </span>
          {changeCount > 0 ? (
            <span className="px-2 py-0.5 rounded-full text-[10px] font-mono font-medium bg-amber-500/15 text-amber-400 border border-amber-500/20">
              {changeCount} change{changeCount !== 1 ? 's' : ''}
            </span>
          ) : (
            <span className="text-[10px] font-mono text-zinc-500">
              using live config
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {changeCount > 0 && (
            <span
              role="button"
              tabIndex={0}
              onClick={(e) => {
                e.stopPropagation();
                discardChanges();
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.stopPropagation(); discardChanges(); }
              }}
              className="flex items-center gap-1 px-2 py-1 rounded-lg text-[10px] font-mono text-zinc-400 hover:text-zinc-200 hover:bg-white/5 transition-colors"
            >
              <RotateCcw className="w-3 h-3" />
              Reset
            </span>
          )}
          <ChevronDown
            className={`w-4 h-4 text-zinc-500 transition-transform duration-200 ${
              isOpen ? 'rotate-180' : ''
            }`}
          />
        </div>
      </button>

      {/* Collapsible body */}
      {isOpen && (
        <div className="border-t border-white/5 px-4 py-4 space-y-3">
          {BACKTEST_SECTIONS.filter((key) => config[key] !== undefined).map(
            (sectionKey) =>
              sectionKey === 'LEVERAGE' ? (
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
                />
              ),
          )}
        </div>
      )}
    </div>
  );
}
