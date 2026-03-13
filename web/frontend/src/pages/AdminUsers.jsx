import { useState, useEffect, useCallback } from 'react'
import { Users, Search, Shield, ShieldOff, UserCheck, UserX, ChevronLeft, ChevronRight } from 'lucide-react'
import { cn } from '../utils/cn'

export default function AdminUsers({ authFetch }) {
  const [users, setUsers] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(0)
  const limit = 20

  const fetchUsers = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ limit, offset: page * limit })
      if (search) params.set('search', search)
      const res = await authFetch(`/api/admin/users?${params}`)
      const data = await res.json()
      if (data.success) {
        setUsers(data.data.users)
        setTotal(data.data.total)
      }
    } catch (err) {
      // Silently handle fetch errors
    } finally {
      setLoading(false)
    }
  }, [authFetch, page, search])

  useEffect(() => { fetchUsers() }, [fetchUsers])

  const updateRole = async (userId, newRole) => {
    try {
      const res = await authFetch(`/api/admin/users/${userId}/role`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ role: newRole }),
      })
      const data = await res.json()
      if (data.success) {
        setUsers(prev => prev.map(u => u.id === userId ? { ...u, role: newRole } : u))
      }
    } catch (err) {
      // Handle error
    }
  }

  const updateStatus = async (userId, isActive) => {
    try {
      const res = await authFetch(`/api/admin/users/${userId}/status`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ is_active: isActive }),
      })
      const data = await res.json()
      if (data.success) {
        setUsers(prev => prev.map(u => u.id === userId ? { ...u, is_active: isActive } : u))
      }
    } catch (err) {
      // Handle error
    }
  }

  const totalPages = Math.ceil(total / limit)

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/20">
            <Users className="w-6 h-6 text-amber-400" />
          </div>
          <div>
            <h2 className="text-xl font-bold text-white font-display">User Management</h2>
            <p className="text-sm text-text-muted">{total} users total</p>
          </div>
        </div>
      </div>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-text-dim" />
        <input
          type="text"
          placeholder="Search by name, email, or username..."
          value={search}
          onChange={(e) => { setSearch(e.target.value); setPage(0) }}
          className="w-full pl-10 pr-4 py-2.5 bg-white/5 border border-white/10 rounded-lg text-white placeholder:text-text-dim focus:outline-none focus:border-primary/50 transition-colors"
        />
      </div>

      {/* Table */}
      <div className="glass-card rounded-xl border border-white/5 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-white/5 text-text-muted">
                <th className="text-left px-4 py-3 font-medium">User</th>
                <th className="text-left px-4 py-3 font-medium">Email</th>
                <th className="text-center px-4 py-3 font-medium">Role</th>
                <th className="text-center px-4 py-3 font-medium">Status</th>
                <th className="text-left px-4 py-3 font-medium">Joined</th>
                <th className="text-center px-4 py-3 font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={6} className="text-center py-8 text-text-muted">Loading...</td></tr>
              ) : users.length === 0 ? (
                <tr><td colSpan={6} className="text-center py-8 text-text-muted">No users found</td></tr>
              ) : users.map((user) => (
                <tr key={user.id} className="border-b border-white/5 hover:bg-white/[0.02] transition-colors">
                  <td className="px-4 py-3">
                    <div className="font-medium text-white">{user.name || user.username}</div>
                    <div className="text-xs text-text-dim">@{user.username}</div>
                  </td>
                  <td className="px-4 py-3 text-text-muted">{user.email || '—'}</td>
                  <td className="px-4 py-3 text-center">
                    <span className={cn(
                      "px-2 py-0.5 rounded-full text-xs font-medium",
                      user.role === 'admin'
                        ? "bg-amber-500/20 text-amber-400 border border-amber-500/30"
                        : "bg-blue-500/20 text-blue-400 border border-blue-500/30"
                    )}>
                      {user.role}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className={cn(
                      "px-2 py-0.5 rounded-full text-xs font-medium",
                      user.is_active
                        ? "bg-emerald-500/20 text-emerald-400"
                        : "bg-rose-500/20 text-rose-400"
                    )}>
                      {user.is_active ? 'Active' : 'Disabled'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-text-muted text-xs">
                    {user.created_at ? new Date(user.created_at).toLocaleDateString() : '—'}
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-center gap-1">
                      <button
                        onClick={() => updateRole(user.id, user.role === 'admin' ? 'user' : 'admin')}
                        className={cn(
                          "p-1.5 rounded-lg transition-all",
                          user.role === 'admin'
                            ? "text-amber-400 hover:bg-amber-500/10"
                            : "text-text-dim hover:text-amber-400 hover:bg-amber-500/10"
                        )}
                        title={user.role === 'admin' ? 'Demote to user' : 'Promote to admin'}
                      >
                        {user.role === 'admin' ? <ShieldOff className="w-4 h-4" /> : <Shield className="w-4 h-4" />}
                      </button>
                      <button
                        onClick={() => updateStatus(user.id, !user.is_active)}
                        className={cn(
                          "p-1.5 rounded-lg transition-all",
                          user.is_active
                            ? "text-text-dim hover:text-rose-400 hover:bg-rose-500/10"
                            : "text-text-dim hover:text-emerald-400 hover:bg-emerald-500/10"
                        )}
                        title={user.is_active ? 'Disable user' : 'Enable user'}
                      >
                        {user.is_active ? <UserX className="w-4 h-4" /> : <UserCheck className="w-4 h-4" />}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        {totalPages > 1 && (
          <div className="flex items-center justify-between px-4 py-3 border-t border-white/5">
            <span className="text-xs text-text-muted">
              Page {page + 1} of {totalPages}
            </span>
            <div className="flex gap-2">
              <button
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
                className="p-1.5 rounded-lg text-text-dim hover:text-white hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
              >
                <ChevronLeft className="w-4 h-4" />
              </button>
              <button
                onClick={() => setPage(p => Math.min(totalPages - 1, p + 1))}
                disabled={page >= totalPages - 1}
                className="p-1.5 rounded-lg text-text-dim hover:text-white hover:bg-white/5 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
              >
                <ChevronRight className="w-4 h-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
