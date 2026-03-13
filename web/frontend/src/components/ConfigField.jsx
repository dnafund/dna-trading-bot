/**
 * ConfigField — renders a single config field with appropriate input type.
 * Supports: number, boolean toggle, text (readonly strings).
 * Shows validation range hints and highlights changed values.
 */

import { useState } from 'react'

function ConfigField({ label, fieldKey, value, defaultValue, rule, onChange, disabled }) {
  const [error, setError] = useState(null)
  const isChanged = value !== defaultValue
  const isBool = typeof value === 'boolean'
  const isNumber = typeof value === 'number'
  const isReadonly = disabled || (!isBool && !isNumber)

  const handleNumberChange = (e) => {
    const raw = e.target.value
    if (raw === '' || raw === '-') return

    // Preserve int/float type from original value
    const parsed = typeof defaultValue === 'number' && Number.isInteger(defaultValue)
      ? parseInt(raw, 10)
      : parseFloat(raw)

    if (!isNaN(parsed)) {
      setError(null)
      onChange(fieldKey, parsed)
    }
  }

  const handleNumberBlur = () => {
    if (!rule) return
    if (rule.min != null && value < rule.min) {
      setError(`Min ${rule.min}`)
      onChange(fieldKey, rule.min)
    } else if (rule.max != null && value > rule.max) {
      setError(`Max ${rule.max}`)
      onChange(fieldKey, rule.max)
    } else {
      setError(null)
    }
  }

  const handleToggle = () => {
    onChange(fieldKey, !value)
  }

  // Format label from snake_case key
  const formatLabel = (key) => {
    const lastPart = key.includes('.') ? key.split('.').pop() : key
    return lastPart
      .replace(/_/g, ' ')
      .replace(/\b\w/g, (c) => c.toUpperCase())
  }

  const displayLabel = label || formatLabel(fieldKey)

  // Range hint text
  const rangeHint = rule
    ? `${rule.min} – ${rule.max}`
    : null

  // Detect small decimal values that represent percentages (e.g. 0.005 = 0.5%)
  const PERCENT_HINT_KEYS = ['tolerance', 'price_ema_tolerance', 'maker', 'taker']
  const isPercentHint = isNumber && (
    PERCENT_HINT_KEYS.some(k => fieldKey.toLowerCase().includes(k)) ||
    (Math.abs(value) > 0 && Math.abs(value) < 1 && fieldKey.toLowerCase().includes('percent') === false)
  )
  const percentHint = isPercentHint && value !== 0
    ? `${(value * 100).toFixed(2).replace(/\.?0+$/, '')}%`
    : null

  if (isBool) {
    return (
      <div className="flex items-center justify-between py-2.5 px-1 group">
        <div className="flex-1 min-w-0">
          <span className="text-sm text-text-secondary">{displayLabel}</span>
        </div>
        <button
          onClick={handleToggle}
          disabled={disabled}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
            value ? 'bg-emerald-500' : 'bg-white/10'
          } ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
              value ? 'translate-x-6' : 'translate-x-1'
            }`}
          />
        </button>
        {isChanged && (
          <span className="ml-2 w-1.5 h-1.5 rounded-full bg-warning flex-shrink-0" title="Modified" />
        )}
      </div>
    )
  }

  if (isNumber) {
    return (
      <div className="flex items-center gap-3 py-2.5 px-1 group">
        <div className="flex-1 min-w-0">
          <span className="text-sm text-text-secondary">{displayLabel}</span>
          {rangeHint && (
            <span className="text-xs text-text-muted ml-2">({rangeHint})</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <div className="flex flex-col items-end">
            <input
              type="number"
              value={value}
              onChange={handleNumberChange}
              onBlur={handleNumberBlur}
              min={rule?.min}
              max={rule?.max}
              step={Number.isInteger(defaultValue) ? 1 : 0.01}
              disabled={disabled}
              className={`w-28 bg-bg-primary border rounded-lg px-3 py-1.5 text-sm text-text-primary text-right
                focus:outline-none focus:ring-1 focus:ring-accent focus:border-accent
                disabled:opacity-50 disabled:cursor-not-allowed
                ${error ? 'border-rose-500/60' : isChanged ? 'border-warning' : 'border-border'}`}
            />
            {error && (
              <span className="text-[10px] text-rose-400 mt-0.5">{error}</span>
            )}
          </div>
          {percentHint && (
            <span className="text-xs text-emerald-400/70 font-mono min-w-[3rem]" title="Percentage equivalent">
              ≈ {percentHint}
            </span>
          )}
          {isChanged && (
            <span className="w-1.5 h-1.5 rounded-full bg-warning flex-shrink-0" title="Modified" />
          )}
        </div>
      </div>
    )
  }

  // Read-only text/string field
  return (
    <div className="flex items-center gap-3 py-2.5 px-1">
      <div className="flex-1 min-w-0">
        <span className="text-sm text-text-secondary">{displayLabel}</span>
      </div>
      <span className="text-sm text-text-muted font-mono">
        {typeof value === 'object' ? JSON.stringify(value) : String(value)}
      </span>
    </div>
  )
}

export default ConfigField
