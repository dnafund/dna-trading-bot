import { useState, useCallback } from 'react'
import Layout from './components/layout/Layout'
import Dashboard from './pages/Dashboard'
import Positions from './pages/Positions'
import History from './pages/History'
import Settings from './pages/Settings'
import AdminUsers from './pages/AdminUsers'
import AdminRevenue from './pages/AdminRevenue'
import Backtest from './pages/Backtest'
import LoginPage from './pages/LoginPage'
import { useWebSocket } from './hooks/useWebSocket'
import { useAuth } from './hooks/useAuth'
import { useToast } from './hooks/useToast'
import { ToastContainer } from './components/Toast'

const MAX_NOTIFICATIONS = 50
const NOTIF_STORAGE_KEY = 'dna_notifications'

function loadStoredNotifications() {
  try {
    const stored = localStorage.getItem(NOTIF_STORAGE_KEY)
    return stored ? JSON.parse(stored) : []
  } catch {
    return []
  }
}

function saveNotifications(notifications) {
  try {
    localStorage.setItem(NOTIF_STORAGE_KEY, JSON.stringify(notifications))
  } catch { /* ignore quota errors */ }
}

const PAGES = {
  dashboard: Dashboard,
  positions: Positions,
  history: History,
  backtest: Backtest,
  settings: Settings,
}

const ADMIN_PAGES = {
  'admin-users': AdminUsers,
  'admin-revenue': AdminRevenue,
}

function App() {
  const [activePage, setActivePage] = useState('dashboard')
  const [notifications, setNotifications] = useState(loadStoredNotifications)
  const auth = useAuth()
  const { toasts, addToast, removeToast } = useToast()

  const handleNotification = useCallback((msg) => {
    const level = msg.level === 'warning' ? 'warning' : msg.level === 'error' ? 'error' : 'info'
    addToast(msg.message, level, 5000)

    // Store in notification history for the bell panel
    const notif = {
      id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
      message: msg.message,
      type: level,
      symbol: msg.symbol || '',
      timestamp: msg.timestamp || new Date().toISOString(),
    }
    setNotifications((prev) => {
      const updated = [...prev, notif].slice(-MAX_NOTIFICATIONS)
      saveNotifications(updated)
      return updated
    })
  }, [addToast])

  const clearNotifications = useCallback(() => {
    setNotifications([])
    saveNotifications([])
  }, [])

  const { data, connected, lastUpdated, refresh } = useWebSocket(auth.token, {
    onNotification: handleNotification,
  })

  // Auth gate
  if (!auth.isAuthenticated) {
    return (
      <LoginPage
        onLoginWithGoogle={auth.loginWithGoogle}
        loading={auth.loading}
        error={auth.error}
      />
    )
  }

  // Merge pages based on role
  const allPages = auth.isAdmin ? { ...PAGES, ...ADMIN_PAGES } : PAGES

  // Guard against non-admin accessing admin pages
  const handleNavigate = (page) => {
    if (page.startsWith('admin-') && !auth.isAdmin) return
    setActivePage(page)
  }

  const PageComponent = allPages[activePage] || (() => <div className="text-white">404 Not Found</div>)

  // Determine page props based on page type
  const isAdminPage = activePage.startsWith('admin-')
  const isAuthFetchOnly = activePage === 'settings' || activePage === 'backtest'

  const pageProps = isAdminPage || isAuthFetchOnly
    ? { authFetch: auth.authFetch }
    : { data, connected, lastUpdated, refresh, authFetch: auth.authFetch }

  return (
    <Layout
      activePage={activePage}
      onNavigate={handleNavigate}
      connected={connected}
      username={auth.username}
      picture={auth.picture}
      onLogout={auth.logout}
      role={auth.role}
      notifications={notifications}
      onClearNotifications={clearNotifications}
    >
      <div key={activePage} className="page-enter">
        <PageComponent {...pageProps} />
      </div>
      <ToastContainer toasts={toasts} onRemove={removeToast} />
    </Layout>
  )
}

export default App
