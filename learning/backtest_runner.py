"""Run backtest and export trades to JSON for learning module analysis.

Usage:
    python -m learning.backtest_runner --symbol BTCUSDT --start 2025-01-01 --end 2026-02-20
    python -m learning.backtest_runner --symbol BTCUSDT --start 2025-01-01 --end 2026-02-20 --analyze
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("learning.backtest_runner")

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def run_backtest(symbol: str, start: str, end: str, balance: float) -> dict:
    """Run backtest and return structured result."""
    from src.trading.backtest.engine import FuturesBacktester

    logger.info("Running backtest: %s %s → %s (balance: $%.2f)", symbol, start, end, balance)

    bt = FuturesBacktester(symbols=[symbol], initial_balance=balance)
    result = bt.backtest(start, end)

    # Convert trades to serializable format
    trades = []
    for t in result.trades:
        trade = {
            "position_id": f"{t['symbol']}_{t['open_time'].isoformat().replace(':', '').replace('-', '')}",
            "symbol": t["symbol"],
            "side": t["side"],
            "entry_type": t["entry_type"].lower(),
            "entry_price": float(t["entry_price"]),
            "close_price": float(t["close_price"]),
            "pnl_usd": float(t["pnl"]),
            "pnl_before_fees": float(t.get("pnl_before_fees", t["pnl"])),
            "roi_percent": float(t["pnl_percent"]),
            "leverage": int(t["leverage"]),
            "margin": float(t["margin"]),
            "fees": float(t["fees"]),
            "close_reason": t["close_type"],
            "close_percent": float(t.get("close_percent", 1.0)),
            "entry_time": str(t["open_time"]),
            "close_time": str(t["close_time"]),
            "tp1_closed": bool(t.get("_tp1_hit_tracked", False)),
            "tp2_closed": bool(t.get("_tp2_hit_tracked", False)),
            "status": "CLOSED",
        }
        trades.append(trade)

    summary = {
        "symbol": symbol,
        "start_date": start,
        "end_date": end,
        "initial_balance": balance,
        "total_trades": result.total_trades,
        "winning_trades": result.winning_trades,
        "losing_trades": result.losing_trades,
        "win_rate": round(result.win_rate, 2),
        "total_pnl": round(result.total_pnl, 2),
        "total_fees": round(result.total_fees, 2),
        "profit_factor": round(result.profit_factor, 4) if result.profit_factor else 0,
        "max_drawdown": round(result.max_drawdown, 2),
        "avg_win": round(result.avg_win, 2) if result.avg_win else 0,
        "avg_loss": round(result.avg_loss, 2) if result.avg_loss else 0,
        "tp1_hits": result.tp1_hits,
        "tp2_hits": result.tp2_hits,
    }

    return {"summary": summary, "trades": trades}


def export_to_json(data: dict, output_path: Path) -> None:
    """Write backtest data to JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("Exported %d trades to %s", len(data["trades"]), output_path)


def export_to_positions_format(data: dict, output_path: Path) -> None:
    """Export as positions.json format for direct learning module consumption."""
    positions = {}
    for t in data["trades"]:
        positions[t["position_id"]] = {
            "position_id": t["position_id"],
            "symbol": t["symbol"],
            "side": t["side"],
            "entry_type": t["entry_type"],
            "status": "CLOSED",
            "leverage": t["leverage"],
            "margin": t["margin"],
            "realized_pnl": t["pnl_usd"],
            "fees": t["fees"],
            "close_reason": t["close_reason"],
            "close_percent": t["close_percent"],
            "roi_percent": t["roi_percent"],
            "entry_price": t["entry_price"],
            "close_price": t["close_price"],
            "created_at": t["entry_time"],
            "closed_at": t["close_time"],
            "tp1_closed": t["tp1_closed"],
            "tp2_closed": t["tp2_closed"],
        }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(positions, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    logger.info("Exported %d positions to %s", len(positions), output_path)


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Run backtest and export for learning")
    parser.add_argument("--symbol", default="BTCUSDT", help="Symbol to backtest")
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    parser.add_argument("--balance", type=float, default=10000, help="Initial balance")
    parser.add_argument("--analyze", action="store_true", help="Run learning analysis after backtest")
    args = parser.parse_args()

    end_date = args.end or datetime.now().strftime("%Y-%m-%d")

    # Run backtest
    data = run_backtest(args.symbol, args.start, end_date, args.balance)

    # Print summary
    s = data["summary"]
    print(f"\n{'='*60}")
    print(f"  Backtest: {s['symbol']}  ({s['start_date']} → {s['end_date']})")
    print(f"{'='*60}")
    print(f"  Trades:        {s['total_trades']}")
    print(f"  Win Rate:      {s['win_rate']}%")
    print(f"  Total PNL:     ${s['total_pnl']:+.2f}")
    print(f"  Profit Factor: {s['profit_factor']}")
    print(f"  Max Drawdown:  ${s['max_drawdown']:.2f}")
    print(f"  Avg Win:       ${s['avg_win']:+.2f}")
    print(f"  Avg Loss:      ${s['avg_loss']:+.2f}")
    print(f"  TP1 Hits:      {s['tp1_hits']}")
    print(f"  TP2 Hits:      {s['tp2_hits']}")
    print(f"{'='*60}")

    # Export files
    output_dir = PROJECT_ROOT / "learning" / "output"
    safe_symbol = args.symbol.lower()
    tag = f"{safe_symbol}_{args.start.replace('-', '')}_{end_date.replace('-', '')}"

    json_path = output_dir / f"backtest_{tag}.json"
    export_to_json(data, json_path)

    positions_path = output_dir / f"backtest_{tag}_positions.json"
    export_to_positions_format(data, positions_path)

    print(f"\n  Trades JSON:     {json_path}")
    print(f"  Positions JSON:  {positions_path}")

    # Optional: run learning analysis
    if args.analyze:
        print(f"\n{'─'*60}")
        print("  Running learning analysis on backtest data...")
        _run_analysis(positions_path, data["summary"])


def _run_analysis(positions_path: Path, summary: dict) -> None:
    """Run learning module analysis on backtest positions."""
    from learning.data.trade_reader import TradeReader
    from learning.adaptive.stats import StatsAnalyzer
    from learning.memory.store import MemoryStore
    from learning.memory.patterns import PatternDetector
    from learning.reports.generator import ReportGenerator
    from learning.config import OUTPUT_DIR

    reader = TradeReader(positions_path)
    trades = reader.load_closed_trades()

    if not trades:
        print("  No closed trades found in backtest output.")
        return

    print(f"  Loaded {len(trades)} trades for analysis.")

    # Stats
    analyzer = StatsAnalyzer(trades)

    # Memory + Patterns
    db_path = positions_path.parent / "backtest_learning.db"
    memory = MemoryStore(db_path)
    memory.sync_trades(trades)
    detector = PatternDetector(memory, trades)
    patterns = detector.detect_all()
    print(f"  Detected {len(patterns)} patterns.")
    memory.close()

    # Report
    symbol = summary.get("symbol", "unknown")
    period = f"backtest_{symbol}_{summary['start_date']}_to_{summary['end_date']}"
    reporter = ReportGenerator(OUTPUT_DIR)
    report_path, json_path = reporter.generate(
        analyzer=analyzer,
        period=period,
        patterns=patterns,
    )
    print(f"\n  Report:      {report_path}")
    print(f"  Suggestions: {json_path}")

    # Print key patterns
    if patterns:
        print(f"\n  Top Patterns:")
        for p in patterns[:8]:
            print(f"    [{p.category}] {p.label}")
            print(f"      {p.detail}")


if __name__ == "__main__":
    main()
