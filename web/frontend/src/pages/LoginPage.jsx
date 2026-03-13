import { useState, useEffect, useRef, useCallback } from 'react'
import { Activity, Loader2 } from 'lucide-react'

const GOOGLE_CLIENT_ID = '510555117830-4ah5d17iqnnhqjk1qm5o35svsake7fv5.apps.googleusercontent.com'

export default function LoginPage({ onLoginWithGoogle, loading, error }) {
  const googleBtnRef = useRef(null)
  const [gsiReady, setGsiReady] = useState(false)

  const handleGoogleResponse = useCallback((response) => {
    if (response.credential) {
      onLoginWithGoogle(response.credential)
    }
  }, [onLoginWithGoogle])

  // Load Google Identity Services script
  useEffect(() => {
    if (window.google?.accounts?.id) {
      setGsiReady(true)
      return
    }

    const script = document.createElement('script')
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    script.defer = true
    script.onload = () => setGsiReady(true)
    document.head.appendChild(script)
  }, [])

  // Initialize Google button when GSI is ready
  useEffect(() => {
    if (!gsiReady || !googleBtnRef.current) return

    try {
      window.google.accounts.id.initialize({
        client_id: GOOGLE_CLIENT_ID,
        callback: handleGoogleResponse,
      })
      window.google.accounts.id.renderButton(googleBtnRef.current, {
        type: 'standard',
        theme: 'outline',
        size: 'medium',
        width: 220,
        text: 'signin_with',
        shape: 'pill',
        logo_alignment: 'left',
      })
    } catch (err) {
      // GSI init error
    }
  }, [gsiReady, handleGoogleResponse])

  return (
    <div className="min-h-screen bg-bg-main flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        {/* Brand */}
        <div className="flex flex-col items-center mb-8">
          <div className="w-16 h-16 rounded-2xl overflow-hidden shadow-lg shadow-primary/20 border border-white/10 mb-4 bg-black">
            <img src="/logo.jpg" alt="DNA Trading Bot" className="w-full h-full object-cover" />
          </div>
          <h1 className="text-3xl font-bold font-display text-white tracking-wider">
            DNA Trading Bot
          </h1>
          <p className="text-sm text-white/40 mt-2">Trading Dashboard</p>
        </div>

        {/* Login Card */}
        <div className="glass-panel rounded-2xl p-8 border border-white/5 space-y-6">
          <div className="text-center mb-2">
            <h2 className="text-lg font-semibold text-white">Sign In</h2>
            <p className="text-xs text-white/40 mt-1">Use your Google account to continue</p>
          </div>

          {/* Error */}
          {error && (
            <div className="bg-rose-500/10 border border-rose-500/20 rounded-lg px-4 py-3 text-sm text-rose-400">
              {error}
            </div>
          )}

          {/* Loading overlay */}
          {loading && (
            <div className="flex items-center justify-center gap-2 py-4">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
              <span className="text-sm text-white/60">Signing in...</span>
            </div>
          )}

          {/* Google Sign-In Button (GSI rendered) */}
          {!loading && (
            <div className="flex justify-center">
              {gsiReady ? (
                <div ref={googleBtnRef} />
              ) : (
                <div className="flex items-center gap-2 py-3 text-white/30 text-sm">
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Loading...
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <p className="text-center text-[10px] text-white/15 mt-6 font-mono">
          V7.4 Futures Trading System
        </p>
      </div>
    </div>
  )
}
