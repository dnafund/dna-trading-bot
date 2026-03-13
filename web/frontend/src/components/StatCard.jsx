
import React from 'react';
import { ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react';
import { useCountUp } from '../hooks/useCountUp';
import { cn } from '../utils/cn';

export default function StatCard({ title, value, numericValue, formatValue, subtitle, trend, icon: Icon, className }) {
  const isUp = trend === 'up';
  const isDown = trend === 'down';
  const animated = useCountUp(typeof numericValue === 'number' ? numericValue : 0);
  const displayValue = typeof numericValue === 'number' && formatValue
    ? formatValue(animated)
    : value;

  return (
    <div className={cn("glass-card p-4 xl:p-5 2xl:p-6 rounded-2xl relative overflow-hidden group border border-white/10", className)}>
      <div className="relative z-10 flex flex-col h-full justify-between">
        <div className="flex justify-between items-start mb-5">
          <div className="p-3 rounded-xl bg-white/5 border border-white/10 text-white group-hover:scale-110 transition-transform duration-300 shadow-inner">
            {Icon ? <Icon className="w-5 h-5" /> : null}
          </div>

          {(trend || trend === null) && (
            <div className={cn(
              "flex items-center gap-1 text-[10px] font-bold px-2 py-1 rounded-full border tracking-wide uppercase",
              isUp ? "bg-emerald-500/10 text-emerald-400 border-emerald-500/20" :
                isDown ? "bg-rose-500/10 text-rose-400 border-rose-500/20" :
                  "bg-white/5 text-white/40 border-white/10"
            )}>
              {isUp ? <ArrowUpRight className="w-3 h-3" /> : isDown ? <ArrowDownRight className="w-3 h-3" /> : <Minus className="w-3 h-3" />}
            </div>
          )}
        </div>

        <div>
          <h3 className="text-text-dim text-[10px] font-bold uppercase tracking-[0.2em] mb-1.5">{title}</h3>
          <div className={cn(
            "text-xl xl:text-[1.35rem] 2xl:text-2xl font-bold tracking-tight font-mono leading-none",
            isUp ? "text-emerald-400" : isDown ? "text-rose-400" : "text-white"
          )}>{displayValue}</div>
          {subtitle && (
            <p className="text-xs text-text-muted mt-3 font-mono border-t border-white/5 pt-3 inline-block w-full">
              {subtitle}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
