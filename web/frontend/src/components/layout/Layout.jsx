import React, { useState } from 'react';
import Sidebar from './Sidebar';
import Header from './Header';
import { Menu, X, WifiOff } from 'lucide-react';

export default function Layout({ children, activePage, onNavigate, connected, title, username, picture, onLogout, role, notifications, onClearNotifications }) {
    const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
    const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

    const toggleMobileMenu = () => setIsMobileMenuOpen(!isMobileMenuOpen);
    const toggleSidebarCollapse = () => setIsSidebarCollapsed(!isSidebarCollapsed);

    return (
        <div className="flex h-screen bg-bg-main overflow-hidden font-body text-text-main selection:bg-primary/30">
            {/* Background grid only — no glow blobs */}
            <div className="fixed inset-0 pointer-events-none z-0">
                <div className="absolute inset-0 bg-grid-pattern opacity-20" />
            </div>

            {/* Mobile Menu Button - Visible primarily on mobile */}
            <button
                onClick={toggleMobileMenu}
                className="fixed top-4 left-4 z-50 p-2 rounded-lg bg-black/50 border border-white/10 text-white lg:hidden backdrop-blur-md"
            >
                {isMobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
            </button>

            {/* Sidebar - Responsive behavior:
                - Hidden on mobile by default (transform -translate-x-full)
                - Visible on mobile when open (translate-x-0)
                - Always visible on desktop (lg:translate-x-0)
             */}
            <div className={`fixed inset-y-0 left-0 z-40 transform transition-all duration-300 ease-in-out lg:translate-x-0 lg:static lg:inset-auto ${isMobileMenuOpen ? 'translate-x-0' : '-translate-x-full shadow-none w-0 lg:w-auto'}`}>
                <Sidebar
                    activePage={activePage}
                    onNavigate={onNavigate}
                    connected={connected}
                    isCollapsed={isSidebarCollapsed}
                    onToggleCollapse={toggleSidebarCollapse}
                    username={username}
                    picture={picture}
                    onLogout={onLogout}
                    role={role}
                />
            </div>

            {/* Overlay for mobile when menu is open */}
            {isMobileMenuOpen && (
                <div
                    className="fixed inset-0 bg-black/80 z-30 lg:hidden backdrop-blur-sm"
                    onClick={() => setIsMobileMenuOpen(false)}
                />
            )}

            <div className="flex-1 flex flex-col relative z-10 w-full lg:w-auto overflow-hidden">
                {/* Header needs to adapt margin on mobile since sidebar is hidden */}
                <div className={`transition-all duration-300 ${isSidebarCollapsed ? 'lg:pl-20' : 'lg:pl-64'}`}>
                    <Header title={title || activePage.charAt(0).toUpperCase() + activePage.slice(1)} isSidebarCollapsed={isSidebarCollapsed} notifications={notifications} onClearNotifications={onClearNotifications} />
                </div>

                {/* WebSocket Disconnect Banner */}
                {connected === false && (
                    <div className="fixed top-20 left-0 right-0 z-20 bg-rose-500/10 border-b border-rose-500/20 py-1.5 text-center">
                        <div className="flex items-center justify-center gap-2 text-xs text-rose-400">
                            <WifiOff className="w-3.5 h-3.5" />
                            <span>Connection lost — reconnecting...</span>
                            <span className="live-dot live-dot--danger" />
                        </div>
                    </div>
                )}

                <main className={`flex-1 overflow-y-auto pt-24 px-4 md:px-8 pb-8 scroll-smooth lg:pl-0 transition-all duration-300`}>
                    {/* Relaxed max-width for Ultra-Wide: 2560px (4k) support */}
                    <div className="w-full max-w-[1400px] xl:max-w-[1900px] 3xl:max-w-[2400px] 4xl:max-w-[3600px] mx-auto transition-all duration-500">
                        {children}
                    </div>
                </main>
            </div>
        </div>
    );
}
