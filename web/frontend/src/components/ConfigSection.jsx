import React, { useState, useMemo } from 'react';
import { ChevronDown, Bell, Target, Shield, Activity, TrendingDown, Brain, Zap, Rocket, Ruler, Clock, Settings, DollarSign, RefreshCw, BarChart2 } from 'lucide-react';
import ConfigField from './ConfigField';
import { flattenToEntries, flattenToObject } from '../utils/flattenConfig';
import { cn } from '../utils/cn';

const SECTION_META = {
  CHANDELIER_EXIT: { name: 'Chandelier Exit', description: 'Trailing stop-loss based on ATR volatility', icon: Bell },
  RISK_MANAGEMENT: { name: 'Risk Management', description: 'Margin, position limits, and hard stop-loss', icon: Shield },
  TRAILING_SL: { name: 'Trailing Stop-Loss (Legacy)', description: 'Replaced by Chandelier Exit — kept for reference only', icon: TrendingDown },
  SMART_SL: { name: 'Smart SL', description: 'Volume-based breathing room before triggering SL', icon: Brain },
  STANDARD_ENTRY: { name: 'Standard Entry', description: 'Enable/disable + EMA tolerance per timeframe (M5, M15, H1, H4)', icon: Target },
  STANDARD_EXIT: { name: 'Standard Exit', description: 'TP/SL per timeframe for Standard M5, M15, H1, H4 entries', icon: BarChart2 },
  EMA610_EXIT: { name: 'EMA610 Exit', description: 'TP/SL for EMA610 H1 and H4 entries', icon: Activity },
  RSI_DIV_EXIT: { name: 'RSI Divergence Exit', description: 'TP/SL for RSI divergence M15, H1, H4 entries', icon: TrendingDown },
  DIVERGENCE_CONFIG: { name: 'RSI Divergence Detection', description: 'Lookback, swing params, and scan settings', icon: Activity },
  EMA610_ENTRY: { name: 'EMA610 Entry', description: 'EMA610 touch zone for H1 + H4 (shared tolerance)', icon: Rocket },
  INDICATORS: { name: 'Indicators', description: 'EMA periods, RSI, and wick thresholds', icon: Ruler },
  TIMEFRAMES: { name: 'Timeframes', description: 'Trend, filter, and entry timeframes (read-only)', icon: Clock },
  SD_ENTRY_CONFIG: { name: 'Supply & Demand Entry', description: 'SD zone entry config: wick rejection, volume, TP/SL per timeframe', icon: Target },
  ENTRY: { name: 'Entry Filters', description: 'ADX filter, wick threshold, RSI levels, EMA610 toggles', icon: Settings },
  FEES: { name: 'Trading Fees', description: 'Maker and taker fee rates', icon: DollarSign },
  DYNAMIC_PAIRS: { name: 'Dynamic Pairs', description: 'Auto-fetch top volume pairs configuration', icon: RefreshCw },
  LEVERAGE: { name: 'Leverage', description: 'Per-symbol leverage tiers', icon: Zap },
};

const READONLY_SECTIONS = new Set(['TIMEFRAMES']);

// Per-section sub-group labels for nested config (e.g. EMA610_EXIT.h1, STANDARD_EXIT.m15)
const SUB_GROUP_LABELS = {
  STANDARD_ENTRY: { m5: 'Standard M5', m15: 'Standard M15', h1: 'Standard H1', h4: 'Standard H4' },
  STANDARD_EXIT: { m5: 'Standard M5', m15: 'Standard M15', h1: 'Standard H1', h4: 'Standard H4' },
  EMA610_EXIT: { h1: 'EMA610 H1', h4: 'EMA610 H4' },
  RSI_DIV_EXIT: { m15: 'RSI Div M15', h1: 'RSI Div H1', h4: 'RSI Div H4' },
  SD_ENTRY_CONFIG: { m15: 'SD M15', h1: 'SD H1', h4: 'SD H4' },
};

export default function ConfigSection({
  sectionKey,
  values,
  defaults,
  rules,
  onChange,
  defaultOpen,
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen || false);

  const meta = SECTION_META[sectionKey] || { name: sectionKey, description: '', icon: Settings };
  const Icon = meta.icon;
  const isReadonly = READONLY_SECTIONS.has(sectionKey);

  const flatValues = useMemo(() => flattenToEntries(values), [values]);
  const defaultMap = useMemo(() => (defaults ? flattenToObject(defaults) : {}), [defaults]);

  const changeCount = useMemo(
    () => flatValues.filter(([key, val]) => key in defaultMap && defaultMap[key] !== val).length,
    [flatValues, defaultMap],
  );

  const getRule = (key) => (rules ? rules[key] || null : null);

  return (
    <div className="glass-panel overflow-hidden transition-all duration-300 rounded-xl border border-white/5 hover:border-white/10">
      <button
        onClick={() => setIsOpen((prev) => !prev)}
        aria-expanded={isOpen}
        aria-controls={`section-${sectionKey}`}
        className="w-full flex items-center gap-4 px-6 py-4 hover:bg-white/5 transition-colors group active:scale-[0.99]"
      >
        <div className="p-2 rounded-lg bg-bg-main border border-white/5 text-primary group-hover:scale-110 transition-transform">
          <Icon className="w-5 h-5" />
        </div>

        <div className="flex-1 text-left">
          <div className="flex items-center gap-3">
            <span className="text-base font-semibold text-white">{meta.name}</span>
            {isReadonly && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-white/5 text-white/40 border border-white/5 uppercase tracking-wider">
                Read-only
              </span>
            )}
            {changeCount > 0 && (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-warning/20 text-warning border border-warning/20 font-medium animate-pulse">
                {changeCount} changes
              </span>
            )}
          </div>
          <p className="text-xs text-white/40 mt-1 font-mono">{meta.description}</p>
        </div>

        <ChevronDown className={cn("w-5 h-5 text-white/20 transition-transform duration-300", isOpen && "rotate-180 text-primary")} />
      </button>

      {isOpen && (
        <div id={`section-${sectionKey}`} className="px-6 py-4 bg-bg-main/30 border-t border-white/5 space-y-4">
          {(() => {
            // Group fields by prefix (e.g. h1.tp1_roi → group "h1")
            const groups = [];
            let currentPrefix = null;
            let currentFields = [];

            for (const [key, val] of flatValues) {
              const dotIdx = key.indexOf('.');
              const prefix = dotIdx > -1 ? key.slice(0, dotIdx) : null;

              if (prefix !== currentPrefix) {
                if (currentFields.length > 0) {
                  groups.push({ prefix: currentPrefix, fields: currentFields });
                }
                currentPrefix = prefix;
                currentFields = [];
              }
              currentFields.push([key, val]);
            }
            if (currentFields.length > 0) {
              groups.push({ prefix: currentPrefix, fields: currentFields });
            }

            const hasSubgroups = groups.some(g => g.prefix !== null);

            return groups.map((group, gi) => (
              <div key={gi}>
                {hasSubgroups && group.prefix && (
                  <div className="flex items-center gap-3 mb-3 mt-1">
                    <span className="text-xs font-semibold uppercase tracking-wider text-primary">
                      {(SUB_GROUP_LABELS[sectionKey] && SUB_GROUP_LABELS[sectionKey][group.prefix]) || group.prefix}
                    </span>
                    <div className="flex-1 h-px bg-white/5" />
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
                  {group.fields.map(([key, val]) => {
                    const defaultVal = key in defaultMap ? defaultMap[key] : val;
                    return (
                      <ConfigField
                        key={key}
                        fieldKey={key}
                        value={val}
                        defaultValue={defaultVal}
                        rule={getRule(key)}
                        onChange={(fieldKey, newVal) => onChange(sectionKey, fieldKey, newVal)}
                        disabled={isReadonly}
                      />
                    );
                  })}
                </div>
              </div>
            ));
          })()}
        </div>
      )}
    </div>
  );
}
