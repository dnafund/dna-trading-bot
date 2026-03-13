import React, { useState, useEffect, useRef } from 'react';
import { cn } from '../utils/cn';

// ── Gradient letter avatar ─────────────────────────────────────────

const GRADIENTS = [
    ['#FF6B6B', '#EE5A24'],
    ['#A29BFE', '#6C5CE7'],
    ['#74B9FF', '#0984E3'],
    ['#55EFC4', '#00B894'],
    ['#FDCB6E', '#E17055'],
    ['#FF9FF3', '#F368E0'],
    ['#48DBFB', '#0ABDE3'],
    ['#FECA57', '#FF9F43'],
    ['#00D2D3', '#01A3A4'],
    ['#5F27CD', '#341F97'],
];

function getGradient(symbol) {
    let hash = 0;
    for (let i = 0; i < symbol.length; i++) {
        hash = symbol.charCodeAt(i) + ((hash << 5) - hash);
    }
    return GRADIENTS[Math.abs(hash) % GRADIENTS.length];
}

// Global cache: cleanSymbol -> blob URL (shared across all instances)
const blobCache = new Map();
// Track in-flight requests to avoid duplicate fetches
const pendingFetches = new Map();

function getToken() {
    return localStorage.getItem('mlx_token');
}

async function fetchLogoBlobUrl(cleanSymbol) {
    // Return cached blob URL if available
    if (blobCache.has(cleanSymbol)) {
        return blobCache.get(cleanSymbol);
    }

    // Join existing in-flight request
    if (pendingFetches.has(cleanSymbol)) {
        return pendingFetches.get(cleanSymbol);
    }

    const promise = (async () => {
        const token = getToken();
        if (!token) return null;

        // Try backend proxy first (has Binance + CoinGecko fallback)
        try {
            const res = await fetch(`/api/coin-logo/${cleanSymbol}`, {
                headers: { Authorization: `Bearer ${token}` },
            });
            if (res.ok) {
                const blob = await res.blob();
                const url = URL.createObjectURL(blob);
                blobCache.set(cleanSymbol, url);
                return url;
            }
        } catch { /* fallback below */ }

        // Try CDN sources (no auth needed)
        const cdnSources = [
            `https://cdn.jsdelivr.net/npm/cryptocurrency-icons@0.18.1/svg/color/${cleanSymbol}.svg`,
            `https://raw.githubusercontent.com/spothq/cryptocurrency-icons/master/128/color/${cleanSymbol}.png`,
        ];

        for (const src of cdnSources) {
            try {
                const res = await fetch(src);
                if (res.ok) {
                    const blob = await res.blob();
                    const url = URL.createObjectURL(blob);
                    blobCache.set(cleanSymbol, url);
                    return url;
                }
            } catch { /* try next */ }
        }

        // All sources failed
        blobCache.set(cleanSymbol, null);
        return null;
    })();

    pendingFetches.set(cleanSymbol, promise);
    const result = await promise;
    pendingFetches.delete(cleanSymbol);
    return result;
}

// ── Component ──────────────────────────────────────────────────────

export default function CryptoIcon({ symbol, className }) {
    const [blobUrl, setBlobUrl] = useState(null);
    const [failed, setFailed] = useState(false);
    const mountedRef = useRef(true);

    const cleanSymbol = symbol
        .replace(/USDT$|BUSD$|USD$|PERP$/i, '')
        .toLowerCase();

    useEffect(() => {
        mountedRef.current = true;
        return () => { mountedRef.current = false; };
    }, []);

    useEffect(() => {
        setBlobUrl(null);
        setFailed(false);

        // Check cache synchronously
        if (blobCache.has(cleanSymbol)) {
            const cached = blobCache.get(cleanSymbol);
            if (cached) {
                setBlobUrl(cached);
            } else {
                setFailed(true);
            }
            return;
        }

        fetchLogoBlobUrl(cleanSymbol).then(url => {
            if (!mountedRef.current) return;
            if (url) {
                setBlobUrl(url);
            } else {
                setFailed(true);
            }
        });
    }, [cleanSymbol]);

    if (failed || !blobUrl) {
        if (!failed && !blobUrl) {
            // Still loading — show placeholder
            const [from, to] = getGradient(cleanSymbol);
            const letter = cleanSymbol.charAt(0).toUpperCase();
            return (
                <div
                    className={cn(
                        "flex items-center justify-center font-bold rounded-full text-white shrink-0 animate-pulse",
                        className
                    )}
                    style={{
                        background: `linear-gradient(135deg, ${from}, ${to})`,
                        fontSize: '0.6em',
                        textShadow: '0 1px 2px rgba(0,0,0,0.3)',
                    }}
                >
                    {letter}
                </div>
            );
        }

        // Failed — show letter avatar
        const [from, to] = getGradient(cleanSymbol);
        const letter = cleanSymbol.charAt(0).toUpperCase();
        return (
            <div
                className={cn(
                    "flex items-center justify-center font-bold rounded-full text-white shrink-0",
                    className
                )}
                style={{
                    background: `linear-gradient(135deg, ${from}, ${to})`,
                    fontSize: '0.6em',
                    textShadow: '0 1px 2px rgba(0,0,0,0.3)',
                }}
            >
                {letter}
            </div>
        );
    }

    return (
        <img
            src={blobUrl}
            alt={cleanSymbol.toUpperCase()}
            className={cn("rounded-full object-cover shrink-0", className)}
            onError={() => setFailed(true)}
            loading="lazy"
        />
    );
}
