/**
 * Skeleton — shimmer loading placeholders for async data.
 * Uses CSS .skeleton class from index.css.
 */

export function StatCardSkeleton() {
  return (
    <div className="glass-card p-6 rounded-2xl border border-white/10">
      <div className="flex justify-between items-start mb-5">
        <div className="skeleton w-11 h-11 rounded-xl" />
        <div className="skeleton w-14 h-6 rounded-full" />
      </div>
      <div className="skeleton w-20 h-3 rounded mb-3" />
      <div className="skeleton w-32 h-7 rounded mb-4" />
      <div className="skeleton w-40 h-3 rounded" />
    </div>
  )
}

export function ChartSkeleton() {
  return (
    <div className="h-full w-full flex flex-col gap-3 p-4">
      <div className="flex justify-between">
        <div className="skeleton w-24 h-6 rounded" />
        <div className="skeleton w-32 h-6 rounded" />
      </div>
      <div className="flex-1 flex items-end gap-1.5 pt-4">
        {[40, 65, 50, 80, 35, 70, 55, 90, 45, 75, 60, 85].map((h, i) => (
          <div key={i} className="skeleton flex-1 rounded-t" style={{ height: `${h}%` }} />
        ))}
      </div>
    </div>
  )
}

export function TableRowSkeleton({ cols = 8 }) {
  return (
    <tr className="border-b border-white/5">
      {Array.from({ length: cols }).map((_, i) => (
        <td key={i} className="px-4 py-4">
          <div className={`skeleton h-4 rounded ${i === 0 ? 'w-24' : 'w-16'}`} />
        </td>
      ))}
    </tr>
  )
}

export function ActivitySkeleton() {
  return (
    <div className="space-y-3">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="flex items-center justify-between p-3.5 rounded-xl bg-white/5 border border-white/5">
          <div className="flex items-center gap-3">
            <div className="skeleton w-8 h-8 rounded-full" />
            <div className="space-y-1.5">
              <div className="skeleton w-16 h-3.5 rounded" />
              <div className="skeleton w-12 h-2.5 rounded" />
            </div>
          </div>
          <div className="space-y-1.5 flex flex-col items-end">
            <div className="skeleton w-14 h-3.5 rounded" />
            <div className="skeleton w-10 h-2.5 rounded" />
          </div>
        </div>
      ))}
    </div>
  )
}
