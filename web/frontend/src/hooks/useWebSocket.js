import { useState, useEffect, useRef, useCallback } from 'react'

const INITIAL_DELAY = 500
const MAX_DELAY = 15000
const IS_LOCAL_DEV = ['localhost', '127.0.0.1'].includes(window.location.hostname)

const MOCK_DATA = {
  positions: [
    {
      position_id: 'mock-1',
      symbol: 'BTCUSDT',
      side: 'long',
      leverage: 20,
      entry_price: 95250.5,
      current_price: 96100.0,
      margin: 50.0,
      pnl_usd: 8.92,
      roi_percent: 17.84,
      status: 'ACTIVE',
      timestamp: new Date(Date.now() - 3600000).toISOString(),
      entry_type: 'ema610_h4',
      take_profit_1: 97000.0,
      take_profit_2: 99000.0,
      stop_loss: 93500.0,
      trailing_sl: 94800.0,
      chandelier_sl: 94500.0,
      tp1_closed: false,
      tp2_closed: false,
    },
    {
      position_id: 'mock-2',
      symbol: 'ETHUSDT',
      side: 'short',
      leverage: 15,
      entry_price: 2750.0,
      current_price: 2720.0,
      margin: 40.0,
      pnl_usd: 6.55,
      roi_percent: 16.36,
      status: 'ACTIVE',
      timestamp: new Date(Date.now() - 7200000).toISOString(),
      entry_type: 'standard_h1',
      take_profit_1: 2680.0,
      take_profit_2: 2600.0,
      stop_loss: 2820.0,
      trailing_sl: 2790.0,
      chandelier_sl: 2800.0,
      tp1_closed: false,
      tp2_closed: false,
    },
  ],
  stats: {
    total_pnl: 94.13,
    total_trades: 132,
    win_rate: 58.3,
    profit_factor: 1.87,
    active_positions: 2,
    today_pnl: 15.47,
    today_trades: 5,
    account_balance: 1250.0,
    unrealized_pnl: 15.47,
    total_margin: 90.0,
  },
}

export function useWebSocket(token, { onNotification } = {}) {
  const [data, setData] = useState(IS_LOCAL_DEV ? MOCK_DATA : { positions: [], stats: {} })
  const [connected, setConnected] = useState(IS_LOCAL_DEV ? true : null)
  const [lastUpdated, setLastUpdated] = useState(IS_LOCAL_DEV ? new Date() : null)
  const wsRef = useRef(null)
  const reconnectTimer = useRef(null)
  const reconnectDelay = useRef(INITIAL_DELAY)
  const unmountedRef = useRef(false)
  const onNotificationRef = useRef(onNotification)
  onNotificationRef.current = onNotification

  const connect = useCallback(() => {
    if (unmountedRef.current || !token) return

    try {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const wsUrl = `${proto}//${window.location.host}/ws?token=${encodeURIComponent(token)}`
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        if (unmountedRef.current) { ws.close(); return }
        setConnected(true)
        reconnectDelay.current = INITIAL_DELAY
        if (reconnectTimer.current) {
          clearTimeout(reconnectTimer.current)
          reconnectTimer.current = null
        }
      }

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data)
          if (msg.type === 'update') {
            setData({
              positions: msg.positions || [],
              stats: msg.stats || {},
            })
            setLastUpdated(msg.timestamp ? new Date(msg.timestamp) : new Date())
          } else if (msg.type === 'notification') {
            onNotificationRef.current?.(msg)
          }
        } catch (err) {
          // ignore parse errors
        }
      }

      ws.onclose = (event) => {
        setConnected(false)
        wsRef.current = null

        // Auth rejection — don't reconnect
        if (event.code === 4001) return

        // Only reconnect if not unmounted
        if (!unmountedRef.current) {
          const delay = reconnectDelay.current
          reconnectDelay.current = Math.min(delay * 2, MAX_DELAY)
          reconnectTimer.current = setTimeout(connect, delay)
        }
      }

      ws.onerror = () => {
        ws.close()
      }
    } catch (err) {
      if (!unmountedRef.current) {
        const delay = reconnectDelay.current
        reconnectDelay.current = Math.min(delay * 2, MAX_DELAY)
        reconnectTimer.current = setTimeout(connect, delay)
      }
    }
  }, [token])

  useEffect(() => {
    unmountedRef.current = false

    // Close existing connection when token changes
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    if (reconnectTimer.current) {
      clearTimeout(reconnectTimer.current)
      reconnectTimer.current = null
    }

    if (token && !IS_LOCAL_DEV) {
      connect()
    }

    return () => {
      unmountedRef.current = true
      if (reconnectTimer.current) {
        clearTimeout(reconnectTimer.current)
        reconnectTimer.current = null
      }
      if (wsRef.current) {
        wsRef.current.close()
        wsRef.current = null
      }
    }
  }, [connect, token])

  // Send a manual refresh request to the server
  const refresh = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'refresh' }))
    }
  }, [])

  return { data, connected, lastUpdated, refresh }
}
