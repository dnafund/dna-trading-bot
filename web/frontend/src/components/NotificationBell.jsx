import { useState, useRef, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Bell } from 'lucide-react'

const TYPE_STYLES = {
  warning: { dot: 'bg-amber-400', bg: 'bg-amber-500/5 border-amber-500/20' },
  error: { dot: 'bg-rose-400', bg: 'bg-rose-500/5 border-rose-500/20' },
  info: { dot: 'bg-cyan-400', bg: 'bg-cyan-500/5 border-cyan-500/20' },
  success: { dot: 'bg-emerald-400', bg: 'bg-emerald-500/5 border-emerald-500/20' },
}

function timeAgo(timestamp) {
  if (!timestamp) return ''
  const diff = Date.now() - new Date(timestamp).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

export default function NotificationBell({ notifications, onClear }) {
  const [open, setOpen] = useState(false)
  const [panelPos, setPanelPos] = useState({ top: 0, right: 0 })
  const panelRef = useRef(null)
  const buttonRef = useRef(null)

  // Calculate panel position from button, clamped to viewport
  const updatePosition = useCallback(() => {
    if (!buttonRef.current) return
    const rect = buttonRef.current.getBoundingClientRect()
    const panelWidth = window.innerWidth >= 640 ? 384 : 320 // sm:w-96 / w-80
    const rightEdge = window.innerWidth - rect.right
    // Ensure panel doesn't overflow left edge (8px margin)
    const maxRight = window.innerWidth - panelWidth - 8
    setPanelPos({
      top: rect.bottom + 8,
      right: Math.min(rightEdge, maxRight),
    })
  }, [])

  // Close on outside click
  useEffect(() => {
    if (!open) return
    updatePosition()
    const handler = (e) => {
      if (
        panelRef.current && !panelRef.current.contains(e.target) &&
        buttonRef.current && !buttonRef.current.contains(e.target)
      ) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    window.addEventListener('resize', updatePosition)
    return () => {
      document.removeEventListener('mousedown', handler)
      window.removeEventListener('resize', updatePosition)
    }
  }, [open, updatePosition])

  const unreadCount = notifications.length

  return (
    <>
      {/* Bell Button */}
      <button
        ref={buttonRef}
        onClick={() => setOpen((prev) => !prev)}
        className="relative p-2 rounded-lg hover:bg-white/5 transition-colors"
        aria-label="Notifications"
      >
        <Bell className={`w-5 h-5 ${unreadCount > 0 ? 'text-amber-400' : 'text-text-dim'}`} />
        {unreadCount > 0 && (
          <span className="absolute -top-0.5 -right-0.5 min-w-[18px] h-[18px] flex items-center justify-center rounded-full bg-amber-500 text-[10px] font-bold text-black px-1">
            {unreadCount > 99 ? '99+' : unreadCount}
          </span>
        )}
      </button>

      {/* Dropdown Panel — rendered via portal to escape stacking context */}
      {open && createPortal(
        <div
          ref={panelRef}
          className="fixed w-80 sm:w-96 max-h-[420px] rounded-xl border border-white/10 bg-bg-card/95 backdrop-blur-xl shadow-2xl overflow-hidden"
          style={{ top: panelPos.top, right: panelPos.right, zIndex: 9999 }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3 border-b border-white/5">
            <span className="text-sm font-semibold text-white">Notifications</span>
            {unreadCount > 0 && (
              <button
                onClick={onClear}
                className="text-xs text-text-dim hover:text-amber-400 transition-colors"
              >
                Clear all
              </button>
            )}
          </div>

          {/* List */}
          <div className="overflow-y-auto max-h-[360px] divide-y divide-white/5">
            {notifications.length === 0 ? (
              <div className="px-4 py-10 text-center text-text-dim text-sm">
                No notifications
              </div>
            ) : (
              [...notifications].reverse().map((notif) => {
                const style = TYPE_STYLES[notif.type] || TYPE_STYLES.info
                return (
                  <div key={notif.id} className={`px-4 py-3 hover:bg-white/3 transition-colors ${style.bg} border-l-2`}>
                    <div className="flex items-start gap-3">
                      <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${style.dot}`} />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm text-text-primary leading-snug">{notif.message}</p>
                        <p className="text-[10px] text-text-dim mt-1 font-mono">{timeAgo(notif.timestamp)}</p>
                      </div>
                    </div>
                  </div>
                )
              })
            )}
          </div>
        </div>,
        document.body
      )}
    </>
  )
}
