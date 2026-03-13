/**
 * useAuth — authentication hook.
 * Supports Google OAuth + username/password login.
 * Stores JWT in localStorage, provides login/logout/authFetch.
 * Tracks user role (admin/user) for conditional UI rendering.
 */

import { useState, useCallback, useEffect } from 'react'

const TOKEN_KEY = 'mlx_token'
const USER_KEY = 'mlx_user'
const PICTURE_KEY = 'mlx_picture'
const ROLE_KEY = 'mlx_role'

// Skip auth on localhost for dev preview
const IS_LOCAL_DEV = ['localhost', '127.0.0.1'].includes(window.location.hostname)

export function useAuth() {
  const [token, setToken] = useState(() => IS_LOCAL_DEV ? 'dev-local' : localStorage.getItem(TOKEN_KEY))
  const [username, setUsername] = useState(() => IS_LOCAL_DEV ? 'Dev User' : localStorage.getItem(USER_KEY))
  const [picture, setPicture] = useState(() => IS_LOCAL_DEV ? null : localStorage.getItem(PICTURE_KEY))
  const [role, setRole] = useState(() => IS_LOCAL_DEV ? 'admin' : (localStorage.getItem(ROLE_KEY) || 'user'))
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const isAuthenticated = !!token
  const isAdmin = role === 'admin'

  // Persist token changes
  useEffect(() => {
    if (token) {
      localStorage.setItem(TOKEN_KEY, token)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
  }, [token])

  useEffect(() => {
    if (username) {
      localStorage.setItem(USER_KEY, username)
    } else {
      localStorage.removeItem(USER_KEY)
    }
  }, [username])

  useEffect(() => {
    if (picture) {
      localStorage.setItem(PICTURE_KEY, picture)
    } else {
      localStorage.removeItem(PICTURE_KEY)
    }
  }, [picture])

  useEffect(() => {
    if (role) {
      localStorage.setItem(ROLE_KEY, role)
    } else {
      localStorage.removeItem(ROLE_KEY)
    }
  }, [role])

  const logout = useCallback(() => {
    setToken(null)
    setUsername(null)
    setPicture(null)
    setRole('user')
    setError(null)
  }, [])

  // Username/password login (kept as fallback)
  const login = useCallback(async (user, password) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: user, password }),
      })
      const data = await res.json()
      if (data.success) {
        setToken(data.data.token)
        setUsername(data.data.username)
        setRole(data.data.role || 'user')
        return true
      }
      setError(data.error || 'Login failed')
      return false
    } catch (err) {
      setError(`Network error: ${err.message}`)
      return false
    } finally {
      setLoading(false)
    }
  }, [])

  // Google OAuth login
  const loginWithGoogle = useCallback(async (credential) => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch('/api/auth/google', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ credential }),
      })
      const data = await res.json()
      if (data.success) {
        setToken(data.data.token)
        setUsername(data.data.username)
        setPicture(data.data.picture || null)
        setRole(data.data.role || 'user')
        return true
      }
      setError(data.error || 'Google login failed')
      return false
    } catch (err) {
      setError(`Network error: ${err.message}`)
      return false
    } finally {
      setLoading(false)
    }
  }, [])

  // Verify stored token on mount — also refresh role from server
  useEffect(() => {
    if (!token || IS_LOCAL_DEV) return
    fetch('/api/auth/me', {
      headers: { Authorization: `Bearer ${token}` },
    })
      .then((res) => res.json())
      .then((data) => {
        if (!data.success) {
          logout()
        } else if (data.data?.role) {
          setRole(data.data.role)
        }
      })
      .catch(() => {
        // Network error — keep token, will retry later
      })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  /**
   * Authenticated fetch wrapper.
   * Adds Authorization header and handles 401 → auto-logout.
   */
  const authFetch = useCallback(
    async (url, options = {}) => {
      if (IS_LOCAL_DEV) {
        return fetch(url, options)
      }
      const headers = {
        ...options.headers,
        Authorization: `Bearer ${token}`,
      }
      const res = await fetch(url, { ...options, headers })
      if (res.status === 401) {
        logout()
        throw new Error('Session expired')
      }
      return res
    },
    [token, logout],
  )

  return {
    token,
    username,
    picture,
    role,
    isAuthenticated,
    isAdmin,
    loading,
    error,
    login,
    loginWithGoogle,
    logout,
    authFetch,
  }
}
