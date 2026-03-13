/**
 * Toast — lightweight notification component.
 * Auto-dismisses after duration. Supports success/error/info types.
 */

import { useState, useEffect, useCallback } from 'react'

const ICONS = {
  success: (
    <svg className="w-5 h-5 text-profit" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
    </svg>
  ),
  error: (
    <svg className="w-5 h-5 text-loss" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
    </svg>
  ),
  info: (
    <svg className="w-5 h-5 text-accent" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  warning: (
    <svg className="w-5 h-5 text-amber-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z" />
    </svg>
  ),
}

const BG_COLORS = {
  success: 'border-profit/30 bg-profit/5',
  error: 'border-loss/30 bg-loss/5',
  info: 'border-accent/30 bg-accent/5',
  warning: 'border-amber-500/30 bg-amber-500/5',
}

function Toast({ message, type = 'info', duration = 4000, onClose }) {
  const [visible, setVisible] = useState(true)

  const dismiss = useCallback(() => {
    setVisible(false)
    setTimeout(() => onClose?.(), 300) // Wait for fade-out animation
  }, [onClose])

  useEffect(() => {
    if (duration <= 0) return
    const timer = setTimeout(dismiss, duration)
    return () => clearTimeout(timer)
  }, [duration, dismiss])

  return (
    <div
      className={`flex items-start gap-3 px-4 py-3 rounded-xl border shadow-lg backdrop-blur-sm
        transition-all duration-300 max-w-md
        ${BG_COLORS[type] || BG_COLORS.info}
        ${visible ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-2'}`}
    >
      <div className="flex-shrink-0 mt-0.5">{ICONS[type] || ICONS.info}</div>
      <p className="text-sm text-text-primary flex-1">{message}</p>
      <button
        onClick={dismiss}
        className="flex-shrink-0 text-text-muted hover:text-text-primary transition-colors"
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  )
}

/**
 * ToastContainer — renders a stack of toast notifications.
 * Use with useToast hook.
 */
function ToastContainer({ toasts, onRemove }) {
  if (toasts.length === 0) return null

  // Show max 3 toasts to prevent overflow
  const visibleToasts = toasts.slice(-3)

  return (
    <div className="fixed bottom-4 right-4 sm:right-6 sm:bottom-6 z-50 flex flex-col gap-2 max-w-sm" role="alert" aria-live="polite">
      {visibleToasts.map((toast) => (
        <Toast
          key={toast.id}
          message={toast.message}
          type={toast.type}
          duration={toast.duration}
          onClose={() => onRemove(toast.id)}
        />
      ))}
    </div>
  )
}

export { Toast, ToastContainer }
export default Toast
