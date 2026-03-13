import React from 'react';
import { Calendar } from 'lucide-react';
import { cn } from '../../utils/cn';
import NotificationBell from '../NotificationBell';

export default function Header({ title, isSidebarCollapsed, notifications, onClearNotifications }) {
    return (
        <header className={cn(
            "h-20 fixed top-0 right-0 left-0 z-30 px-6 md:px-10 flex items-center justify-between border-b border-white/5 bg-bg-main/50 backdrop-blur-md transition-all duration-300",
            isSidebarCollapsed ? "lg:left-20" : "lg:left-64"
        )}>

            {/* Page Title & Breadcrumbs */}
            <div>
                <h2 className="text-3xl font-bold text-white tracking-wide font-display uppercase">{title}</h2>
                <div className="flex items-center gap-2 text-xs text-text-dim mt-1 font-mono tracking-wider">
                    <span>SYSTEM</span>
                    <span>/</span>
                    <span className="text-primary font-medium">DASHBOARD</span>
                </div>
            </div>

            {/* Right side: Notification Bell + Date/Time */}
            <div className="flex items-center gap-4">
                <NotificationBell
                    notifications={notifications || []}
                    onClear={onClearNotifications}
                />
                <div className="flex flex-col items-end">
                    <span className="text-xs font-bold text-text-main font-display tracking-widest">
                        {new Date().toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).toUpperCase()}
                    </span>
                    <span className="text-[10px] text-primary/80 font-mono">
                        {new Date().toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' })}
                    </span>
                </div>
            </div>
        </header>
    );
}
