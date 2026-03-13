import React from 'react';
import { LayoutDashboard, History, Settings, ChevronRight, LogOut, TrendingUp, Users, DollarSign, Shield, FlaskConical } from 'lucide-react';
import { cn } from '../../utils/cn';

const MENU_ITEMS = [
  { id: 'dashboard', label: 'Overview', icon: LayoutDashboard },
  { id: 'positions', label: 'Active Positions', icon: TrendingUp },
  { id: 'history', label: 'Trade History', icon: History },
  { id: 'backtest', label: 'Backtest', icon: FlaskConical },
  { id: 'settings', label: 'Configuration', icon: Settings },
];

const ADMIN_ITEMS = [
  { id: 'admin-users', label: 'User Management', icon: Users },
  { id: 'admin-revenue', label: 'Revenue', icon: DollarSign },
];

export default function Sidebar({ activePage, onNavigate, connected, isCollapsed, onToggleCollapse, username, picture, onLogout, role = 'user' }) {
  const initials = (username || 'U').slice(0, 2).toUpperCase();
  const isAdmin = role === 'admin';

  const renderMenuItem = (item) => {
    const isActive = activePage === item.id;
    const Icon = item.icon;

    return (
      <button
        key={item.id}
        onClick={() => onNavigate(item.id)}
        aria-current={isActive ? "page" : undefined}
        className={cn(
          "w-full flex items-center rounded-lg transition-all duration-300 group relative overflow-hidden border border-transparent active:scale-[0.97] focus-visible:ring-2 focus-visible:ring-primary/50 focus-visible:outline-none",
          isCollapsed ? "justify-center py-4" : "justify-between px-4 py-3.5",
          isActive
            ? "bg-primary/10 text-white border-primary/20 shadow-[0_0_15px_rgba(34,197,94,0.1)]"
            : "text-text-muted hover:text-white hover:bg-white/5 hover:border-white/5"
        )}
        title={isCollapsed ? item.label : ""}
      >
        {isActive && (
          <div className="absolute left-0 top-1/2 -translate-y-1/2 h-8 w-1 bg-primary rounded-r-full shadow-[0_0_10px_rgba(34,197,94,0.8)]" />
        )}

        <div className="flex items-center gap-3">
          <Icon className={cn("transition-colors shrink-0", isCollapsed ? "w-6 h-6" : "w-5 h-5", isActive ? "text-primary" : "group-hover:text-white")} />
          {!isCollapsed && <span className="font-medium animate-fade-in">{item.label}</span>}
        </div>

        {isActive && !isCollapsed && <ChevronRight className="w-4 h-4 text-primary animate-pulse" />}
      </button>
    );
  };

  return (
    <aside className={cn(
      "h-screen flex flex-col border-r border-white/5 bg-bg-secondary/30 backdrop-blur-xl transition-all duration-300 relative",
      isCollapsed ? "w-20" : "w-64"
    )}>
      {/* Toggle Button */}
      <button
        onClick={onToggleCollapse}
        className="absolute -right-4 top-12 w-8 h-8 rounded-full bg-bg-main border border-white/10 flex items-center justify-center text-white hover:text-primary hover:bg-white/5 transition-all z-50 shadow-lg"
      >
        <ChevronRight className={cn("w-4 h-4 transition-transform duration-300", isCollapsed ? "" : "rotate-180")} />
      </button>

      {/* Brand */}
      <div className={cn(
        "h-24 flex items-center border-b border-white/5 relative overflow-hidden transition-all duration-300",
        isCollapsed ? "px-4 justify-center" : "px-8"
      )}>
        <div className="absolute inset-0 bg-primary/5 blur-xl pointer-events-none" />
        <div className="relative flex items-center gap-3 group">
          <div className="w-10 h-10 rounded-xl overflow-hidden group-hover:scale-110 transition-transform duration-300 border border-white/10 shrink-0 bg-black">
            <img src="/logo.jpg" alt="DNA Trading Bot" className="w-full h-full object-cover" />
          </div>
          {!isCollapsed && (
            <div className="animate-fade-in-right">
              <h1 className="text-lg font-bold font-display text-white tracking-wide">
                DNA Trading Bot
              </h1>
              <div className="flex items-center gap-2 mt-1">
                <span className={connected === true ? "live-dot" : connected === false ? "live-dot live-dot--danger" : "live-dot live-dot--warning"} />
                <span className="text-[10px] font-medium text-primary/60 uppercase tracking-widest whitespace-nowrap">
                  {connected === true ? "System Online" : connected === false ? "Disconnected" : "Connecting..."}
                </span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Navigation */}
      <nav role="navigation" aria-label="Main navigation" className={cn(
        "flex-1 py-8 space-y-2 overflow-y-auto no-scrollbar transition-all duration-300",
        isCollapsed ? "px-2" : "px-4"
      )}>
        {!isCollapsed && (
          <div className="px-4 mb-4 text-[10px] font-bold text-text-dim uppercase tracking-[0.2em] animate-fade-in">
            Main Interface
          </div>
        )}
        {MENU_ITEMS.map(renderMenuItem)}

        {/* Admin Section */}
        {isAdmin && (
          <>
            {!isCollapsed && (
              <div className="px-4 mb-2 mt-6 text-[10px] font-bold text-amber-400/60 uppercase tracking-[0.2em] animate-fade-in flex items-center gap-2">
                <Shield className="w-3 h-3" />
                Admin Panel
              </div>
            )}
            {isCollapsed && <div className="border-t border-amber-500/20 my-3 mx-2" />}
            {ADMIN_ITEMS.map(renderMenuItem)}
          </>
        )}
      </nav>

      {/* Footer User Profile + Logout */}
      <div className={cn("border-t border-white/5 transition-all duration-300", isCollapsed ? "p-2" : "p-4")}>
        <div className={cn(
          "glass-card rounded-xl flex items-center border border-transparent transition-all",
          isCollapsed ? "w-12 h-12 justify-center mx-auto" : "p-3 gap-3"
        )}>
          {picture ? (
            <img src={picture} alt="" className="w-9 h-9 rounded-lg border border-white/10 shrink-0 object-cover" referrerPolicy="no-referrer" />
          ) : (
            <div className="w-9 h-9 rounded-lg bg-bg-main border border-white/10 flex items-center justify-center shadow-inner shrink-0">
              <span className="text-sm font-bold text-primary font-display">{initials}</span>
            </div>
          )}
          {!isCollapsed && (
            <div className="flex-1 overflow-hidden animate-fade-in">
              <h4 className="text-sm font-bold text-text-main truncate font-display">{username || 'User'}</h4>
              <p className={cn(
                "text-[10px] truncate",
                isAdmin ? "text-amber-400" : "text-text-muted"
              )}>
                {isAdmin ? 'ADMIN' : 'USER'}
              </p>
            </div>
          )}
          {!isCollapsed && onLogout && (
            <button
              onClick={onLogout}
              className="p-1.5 rounded-lg text-text-dim hover:text-rose-400 hover:bg-rose-500/10 transition-all shrink-0"
              title="Sign out"
            >
              <LogOut className="w-4 h-4" />
            </button>
          )}
        </div>
        {isCollapsed && onLogout && (
          <button
            onClick={onLogout}
            className="w-12 h-10 mx-auto mt-2 flex items-center justify-center rounded-lg text-text-dim hover:text-rose-400 hover:bg-rose-500/10 transition-all"
            title="Sign out"
          >
            <LogOut className="w-4 h-4" />
          </button>
        )}
      </div>
    </aside>
  );
}
