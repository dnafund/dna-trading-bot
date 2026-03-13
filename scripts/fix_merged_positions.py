"""Fix all merged OKX positions using per-fill data from OKX bills API."""
import os
import sys
import time
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from itertools import permutations

from dotenv import load_dotenv

load_dotenv()
# Add both project root and src to path for imports
project_root = os.path.join(os.path.dirname(__file__), '..')
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'src'))

from trading.exchanges.okx import OKXFuturesClient


def fetch_all_bills(client):
    """Fetch all available bills from recent + archive endpoints."""
    all_bills = []
    for endpoint_fn in [
        client.exchange.privateGetAccountBills,
        client.exchange.privateGetAccountBillsArchive,
    ]:
        after = None
        for page in range(30):
            params = {'instType': 'SWAP', 'type': '2', 'limit': '100'}
            if after:
                params['after'] = after
            try:
                result = endpoint_fn(params=params)
            except Exception as e:
                print(f"  API error: {e}")
                break
            batch = result.get('data', [])
            if not batch:
                break
            all_bills.extend(batch)
            after = batch[-1].get('billId', '')
            time.sleep(0.15)
            if len(batch) < 100:
                break

    # Deduplicate
    seen = set()
    unique = []
    for b in all_bills:
        bid = b.get('billId', '')
        if bid not in seen:
            seen.add(bid)
            unique.append(b)
    return unique


def find_merged_groups(conn):
    """Find position groups that were merged on OKX (same symbol+side, close within 5 min)."""
    rows = conn.execute(
        """SELECT position_id, symbol, side, entry_type, entry_price, exit_price,
                  pnl_usd, roi_percent, close_reason, close_time, margin, leverage,
                  stop_loss, take_profit_1, take_profit_2, size
           FROM closed_trades ORDER BY close_time DESC"""
    ).fetchall()

    groups = []
    used_ids = set()
    for r1 in rows:
        if r1['position_id'] in used_ids:
            continue
        group = [dict(r1)]
        used_ids.add(r1['position_id'])
        for r2 in rows:
            if r2['position_id'] in used_ids:
                continue
            if r2['symbol'] == r1['symbol'] and r2['side'] == r1['side']:
                try:
                    t1 = datetime.fromisoformat(r1['close_time'])
                    t2 = datetime.fromisoformat(r2['close_time'])
                    if abs((t1 - t2).total_seconds()) < 300:
                        group.append(dict(r2))
                        used_ids.add(r2['position_id'])
                except (ValueError, TypeError):
                    pass
        if len(group) > 1:
            groups.append(group)
    return groups


def match_fills_to_positions(close_events, group):
    """Match OKX close events to bot positions using TP/SL + PnL estimate."""
    if len(close_events) != len(group):
        return None

    best_assignment = None
    best_score = float('inf')

    for perm in permutations(range(len(close_events))):
        score = 0.0
        for pos_idx, fill_idx in enumerate(perm):
            p = group[pos_idx]
            f = close_events[fill_idx]
            fp = f['fill_price']

            # TP/SL price match (strong signal)
            for target in [p.get('take_profit_1'), p.get('take_profit_2')]:
                if target and target > 0 and fp > 0:
                    if abs(fp - target) / target < 0.002:
                        score -= 1000
                        break
            sl = p.get('stop_loss')
            if sl and sl > 0 and fp > 0:
                if abs(fp - sl) / sl < 0.003:
                    score -= 1000

            # PnL estimate closeness
            p_entry = p.get('entry_price', 0) or 0
            p_margin = p.get('margin', 0) or 0
            p_lev = p.get('leverage', 1) or 1
            if p_entry > 0 and p_margin > 0:
                price_pct = (fp - p_entry) / p_entry
                if p['side'] == 'SELL':
                    price_pct = -price_pct
                est_pnl = price_pct * p_margin * p_lev
                score += abs(est_pnl - f['pnl'])

        if score < best_score:
            best_score = score
            best_assignment = perm

    if best_assignment is None:
        return None

    return {
        group[pos_idx]['position_id']: close_events[fill_idx]
        for pos_idx, fill_idx in enumerate(best_assignment)
    }


def infer_close_reason(p, fill_price):
    """Infer close reason from fill price vs TP/SL levels."""
    if not fill_price or fill_price <= 0:
        return None
    tp1 = p.get('take_profit_1')
    tp2 = p.get('take_profit_2')
    sl = p.get('stop_loss')

    if tp1 and tp1 > 0 and abs(fill_price - tp1) / tp1 < 0.0015:
        return "TP1"
    if tp2 and tp2 > 0 and abs(fill_price - tp2) / tp2 < 0.0015:
        return "TP2"
    if sl and sl > 0 and abs(fill_price - sl) / sl < 0.003:
        return "HARD_SL"
    return None


def main():
    client = OKXFuturesClient(
        api_key=os.getenv('OKX_API_KEY'),
        api_secret=os.getenv('OKX_API_SECRET'),
        passphrase=os.getenv('OKX_PASSPHRASE'),
    )

    print("Fetching OKX bills...")
    all_bills = fetch_all_bills(client)
    close_bills = [b for b in all_bills if abs(float(b.get('pnl', 0) or 0)) > 0.001]
    print(f"Total bills: {len(all_bills)}, close bills: {len(close_bills)}")

    if all_bills:
        oldest = min(int(b['ts']) for b in all_bills)
        newest = max(int(b['ts']) for b in all_bills)
        print(
            f"Range: {datetime.fromtimestamp(oldest / 1000, tz=timezone.utc).strftime('%m/%d %H:%M')} "
            f"to {datetime.fromtimestamp(newest / 1000, tz=timezone.utc).strftime('%m/%d %H:%M')} UTC"
        )

    conn = sqlite3.connect('data/trades.db')
    conn.row_factory = sqlite3.Row

    groups = find_merged_groups(conn)
    print(f"Merged groups: {len(groups)}")

    # Load OKX history for matching
    fixes = []
    reason_fixes = []

    for group in groups:
        symbol = group[0]['symbol']
        side = group[0]['side']
        bot_close = datetime.fromisoformat(group[0]['close_time'])

        base = symbol.replace('USDT', '')
        inst_id = f"{base}-USDT-SWAP"

        # Bot close time is local (UTC+7), convert to UTC
        bot_close_utc = bot_close - timedelta(hours=7)

        # Find OKX history entry
        okx_rows = conn.execute(
            """SELECT pos_id, realized_pnl, fee, funding_fee, close_time, close_price
               FROM okx_history WHERE symbol = ? AND side = ?
               ORDER BY close_time DESC""",
            [symbol, 'short' if side == 'SELL' else 'long'],
        ).fetchall()

        best_okx = None
        total_pnl_bot = sum(p['pnl_usd'] or 0 for p in group)
        for okx in okx_rows:
            okx_ct = okx['close_time'] or ''
            if not okx_ct:
                continue
            try:
                okx_dt = datetime.fromisoformat(okx_ct).replace(tzinfo=None)
                diff = abs((bot_close_utc - okx_dt).total_seconds())
                if diff < 600 and abs((okx['realized_pnl'] or 0) - total_pnl_bot) < 2.0:
                    best_okx = dict(okx)
                    break
            except (ValueError, TypeError):
                pass

        if not best_okx:
            continue

        okx_net = best_okx['realized_pnl']
        okx_close_utc = datetime.fromisoformat(best_okx['close_time']).replace(tzinfo=None)

        # Find close bills matching instId + time range
        matching_bills = []
        for b in close_bills:
            if b.get('instId') != inst_id:
                continue
            bill_ts = int(b['ts']) / 1000
            bill_dt = datetime.fromtimestamp(bill_ts, tz=timezone.utc).replace(tzinfo=None)
            if abs((bill_dt - okx_close_utc).total_seconds()) < 1800:
                matching_bills.append(b)

        if not matching_bills:
            continue

        # Group by ordId
        order_groups = defaultdict(
            lambda: {
                'notional': 0.0, 'total_size': 0.0, 'pnl': 0.0,
                'fee': 0.0, 'timestamp': '', 'order_id': '',
            }
        )
        for b in matching_bills:
            ord_id = b.get('ordId', '')
            g = order_groups[ord_id]
            px = float(b.get('px', 0) or 0)
            sz = float(b.get('sz', 0) or 0)
            g['notional'] += px * sz
            g['total_size'] += sz
            g['pnl'] += float(b.get('pnl', 0) or 0)
            g['fee'] += float(b.get('fee', 0) or 0)
            g['order_id'] = ord_id
            ts = b.get('ts', '')
            if ts > g['timestamp']:
                g['timestamp'] = ts

        close_events = []
        for g in order_groups.values():
            if g['total_size'] > 0:
                g['fill_price'] = g['notional'] / g['total_size']
            else:
                g['fill_price'] = 0
            del g['notional']
            close_events.append(g)
        close_events.sort(key=lambda e: e['timestamp'])

        # Verify bills total matches OKX total
        bills_total = sum(e['pnl'] for e in close_events)
        if abs(bills_total - okx_net) > 2.0:
            continue

        if len(close_events) != len(group):
            print(
                f"  {symbol} {side}: {len(close_events)} events != {len(group)} positions, skip"
            )
            continue

        # Match fills to positions
        matches = match_fills_to_positions(close_events, group)
        if not matches:
            continue

        fill_pnl_total = sum(f['pnl'] for f in close_events)
        has_changes = False
        details = []

        for p in group:
            f = matches.get(p['position_id'])
            if not f:
                continue

            if fill_pnl_total != 0:
                p_share = f['pnl'] / fill_pnl_total
            else:
                p_share = 1.0 / len(group)

            new_pnl = round(okx_net * p_share, 2)
            new_margin = p['margin'] or 50
            new_roi = round((new_pnl / new_margin) * 100, 2) if new_margin else 0
            new_exit = f['fill_price']

            ts_ms = int(f['timestamp'])
            utc_dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
            local_dt = (utc_dt + timedelta(hours=7)).replace(tzinfo=None)
            new_close_time = local_dt.isoformat()

            old_pnl = p['pnl_usd'] or 0
            old_exit = p['exit_price'] or 0
            old_reason = p['close_reason'] or ''

            pnl_diff = abs(old_pnl - new_pnl)
            exit_diff = abs(old_exit - new_exit) / max(new_exit, 1e-10) if new_exit else 0

            # Infer close reason from actual fill price
            inferred = infer_close_reason(p, new_exit)

            if pnl_diff > 0.05 or exit_diff > 0.001:
                has_changes = True
                fixes.append((new_pnl, new_roi, new_exit, new_close_time, p['position_id']))
                marker = "FIX"
            else:
                marker = "OK "

            # Fix close reason if inferred differs
            if inferred and inferred != old_reason:
                reason_fixes.append((inferred, p['position_id']))
                details.append(
                    f"  {marker} {p['entry_type']}: pnl ${old_pnl:.2f} -> ${new_pnl:.2f}, "
                    f"exit {old_exit:.8f} -> {new_exit:.8f}, "
                    f"reason {old_reason} -> {inferred}"
                )
            else:
                details.append(
                    f"  {marker} {p['entry_type']}: pnl ${old_pnl:.2f} -> ${new_pnl:.2f}, "
                    f"exit {old_exit:.8f} -> {new_exit:.8f}"
                )

        if has_changes or any(r[0] != p['close_reason'] for r in reason_fixes[-len(group):]
                              if len(reason_fixes) >= len(group)):
            print(f"\n{symbol} {side} (OKX: ${okx_net:.2f}):")
            for d in details:
                print(d)

    # Apply PnL/exit fixes
    print(f"\n=== Applying {len(fixes)} PnL/exit fixes, {len(reason_fixes)} reason fixes ===")
    for new_pnl, new_roi, new_exit, new_close_time, pid in fixes:
        conn.execute(
            """UPDATE closed_trades
               SET pnl_usd = ?, roi_percent = ?, exit_price = ?, close_time = ?
               WHERE position_id = ?""",
            [new_pnl, new_roi, new_exit, new_close_time, pid],
        )

    for new_reason, pid in reason_fixes:
        conn.execute(
            "UPDATE closed_trades SET close_reason = ? WHERE position_id = ?",
            [new_reason, pid],
        )

    conn.commit()
    print(f"Done! {len(fixes)} PnL/exit + {len(reason_fixes)} reason updates applied.")
    conn.close()


if __name__ == '__main__':
    main()
