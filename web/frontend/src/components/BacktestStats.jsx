import React from 'react';
import StatCard from './StatCard';
import { TrendingUp, Target, BarChart3, AlertTriangle, Hash, Percent } from 'lucide-react';

export default function BacktestStats({ summary }) {
  if (!summary) return null;

  const cards = [
    {
      title: 'Total PNL',
      numericValue: summary.total_pnl,
      formatValue: (v) => `$${v >= 0 ? '+' : ''}${v.toFixed(2)}`,
      trend: summary.total_pnl > 0 ? 'up' : summary.total_pnl < 0 ? 'down' : null,
      icon: TrendingUp,
      subtitle: `Fees: $${summary.total_fees?.toFixed(2) || '0.00'}`,
    },
    {
      title: 'Win Rate',
      numericValue: summary.win_rate,
      formatValue: (v) => `${v.toFixed(1)}%`,
      trend: summary.win_rate >= 50 ? 'up' : 'down',
      icon: Target,
      subtitle: `${summary.winning_trades}W / ${summary.losing_trades}L`,
    },
    {
      title: 'Profit Factor',
      numericValue: summary.profit_factor,
      formatValue: (v) => v.toFixed(2),
      trend: summary.profit_factor > 1 ? 'up' : 'down',
      icon: BarChart3,
      subtitle: `R:R ${summary.risk_reward?.toFixed(2) || '\u2014'}`,
    },
    {
      title: 'Max Drawdown',
      numericValue: summary.max_drawdown,
      formatValue: (v) => `$${v.toFixed(2)}`,
      trend: 'down',
      icon: AlertTriangle,
    },
    {
      title: 'Total Trades',
      numericValue: summary.total_trades,
      formatValue: (v) => Math.round(v).toString(),
      trend: null,
      icon: Hash,
      subtitle: `Avg W: $${summary.avg_win?.toFixed(0) || '0'} | Avg L: $${summary.avg_loss?.toFixed(0) || '0'}`,
    },
    {
      title: 'Return',
      numericValue: summary.return_pct,
      formatValue: (v) => `${v >= 0 ? '+' : ''}${v.toFixed(1)}%`,
      trend: summary.return_pct > 0 ? 'up' : summary.return_pct < 0 ? 'down' : null,
      icon: Percent,
      subtitle: `$${summary.initial_balance?.toLocaleString()} \u2192 $${summary.final_balance?.toLocaleString()}`,
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {cards.map((card) => <StatCard key={card.title} {...card} />)}
    </div>
  );
}
