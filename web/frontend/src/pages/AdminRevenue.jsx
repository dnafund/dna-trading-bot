import { useState, useEffect } from 'react'
import { DollarSign, Users, TrendingUp, Server } from 'lucide-react'
import { cn } from '../utils/cn'
import StatCard from '../components/StatCard'

export default function AdminRevenue({ authFetch }) {
  const [revenue, setRevenue] = useState(null)
  const [system, setSystem] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      setLoading(true)
      try {
        const [revRes, sysRes] = await Promise.all([
          authFetch('/api/admin/revenue'),
          authFetch('/api/admin/system'),
        ])
        const revData = await revRes.json()
        const sysData = await sysRes.json()
        if (revData.success) setRevenue(revData.data)
        if (sysData.success) setSystem(sysData.data)
      } catch (err) {
        // Silently handle fetch errors
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [authFetch])

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="text-text-muted">Loading revenue data...</div>
      </div>
    )
  }

  const statusColor = (status) => {
    if (status === 'healthy') return 'text-emerald-400'
    if (status === 'unavailable' || status === 'unhealthy') return 'text-rose-400'
    return 'text-amber-400'
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="p-2.5 rounded-xl bg-amber-500/10 border border-amber-500/20">
          <DollarSign className="w-6 h-6 text-amber-400" />
        </div>
        <div>
          <h2 className="text-xl font-bold text-white font-display">Revenue Dashboard</h2>
          <p className="text-sm text-text-muted">Subscription metrics and system health</p>
        </div>
      </div>

      {/* Summary Cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <StatCard
          title="Monthly Revenue"
          value={`$${revenue?.total_mrr?.toFixed(2) || '0.00'}`}
          icon={DollarSign}
          trend={revenue?.total_mrr > 0 ? 'up' : null}
        />
        <StatCard
          title="Total Users"
          value={revenue?.total_users || 0}
          icon={Users}
        />
        <StatCard
          title="Paid Subscribers"
          value={revenue?.tiers?.reduce((sum, t) => sum + t.count, 0) || 0}
          icon={TrendingUp}
        />
      </div>

      {/* Tier Breakdown */}
      <div className="glass-card rounded-xl border border-white/5 p-6">
        <h3 className="text-lg font-bold text-white mb-4">Subscription Tiers</h3>
        {revenue?.tiers?.length > 0 ? (
          <div className="space-y-3">
            {revenue.tiers.map((tier) => (
              <div key={tier.tier} className="flex items-center justify-between p-3 rounded-lg bg-white/[0.02] border border-white/5">
                <div>
                  <span className="font-medium text-white capitalize">{tier.tier}</span>
                  <span className="text-text-muted ml-2">${tier.price_usd}/mo</span>
                </div>
                <div className="flex items-center gap-4">
                  <span className="text-text-muted">{tier.count} subscribers</span>
                  <span className="font-mono text-emerald-400">${tier.mrr.toFixed(2)}/mo</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-text-muted text-center py-4">No active subscriptions yet</p>
        )}
      </div>

      {/* System Health */}
      {system && (
        <div className="glass-card rounded-xl border border-white/5 p-6">
          <div className="flex items-center gap-2 mb-4">
            <Server className="w-5 h-5 text-text-muted" />
            <h3 className="text-lg font-bold text-white">System Health</h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {Object.entries(system).map(([service, status]) => (
              <div key={service} className="flex items-center justify-between p-3 rounded-lg bg-white/[0.02] border border-white/5">
                <span className="text-text-muted capitalize">{service}</span>
                <span className={cn("font-medium capitalize", statusColor(status))}>
                  {status}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
