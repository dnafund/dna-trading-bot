"""
CLI entry point for the self-learning module.

Usage:
    python -m learning.run stats [--days N]            Quick stats
    python -m learning.run weekly [--no-llm]           Full weekly analysis
    python -m learning.run reflect [--days N]          LLM reflection only
    python -m learning.run suggest [--validate]        Parameter suggestions
"""

import argparse
import logging
import sys
from datetime import datetime

from learning.config import OUTPUT_DIR, POSITIONS_FILE, CONFIG_FILE, CONFIG_OVERRIDES_FILE
from learning.data.trade_reader import TradeReader
from learning.data.config_reader import ConfigReader
from learning.adaptive.stats import StatsAnalyzer
from learning.reports.generator import ReportGenerator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("learning")


def cmd_stats(args: argparse.Namespace) -> None:
    """Run quick stats analysis."""
    reader = TradeReader(POSITIONS_FILE)
    trades = reader.load_recent_trades(days=args.days)

    if not trades:
        print(f"No closed trades found in last {args.days} days.")
        return

    analyzer = StatsAnalyzer(trades)
    overall = analyzer.overall

    print(f"\n{'='*60}")
    print(f"  Trading Stats — Last {args.days} Days")
    print(f"{'='*60}")
    print(f"  Trades:        {overall.count}")
    print(f"  Win Rate:      {overall.win_rate}%")
    print(f"  Total PNL:     ${overall.total_pnl:+.2f}")
    print(f"  Avg PNL:       ${overall.avg_pnl:+.2f}")
    print(f"  Profit Factor: {overall.profit_factor}")
    print(f"  Avg Duration:  {overall.avg_duration_hours}h")
    print(f"{'='*60}")

    print("\n  By Entry Type:")
    for s in analyzer.by_entry_type():
        print(f"    {s.label:20s} — {s.count:3d} trades, {s.win_rate}% WR, ${s.total_pnl:+.2f}")

    print("\n  By Side:")
    for s in analyzer.by_side():
        print(f"    {s.label:20s} — {s.count:3d} trades, {s.win_rate}% WR, ${s.total_pnl:+.2f}")

    print("\n  Top 3 Symbols:")
    for s in analyzer.by_symbol()[:3]:
        print(f"    {s.label:20s} — {s.count:3d} trades, {s.win_rate}% WR, ${s.total_pnl:+.2f}")

    streaks = analyzer.streak_analysis()
    print(f"\n  Max Win Streak:  {streaks['max_win_streak']}")
    print(f"  Max Loss Streak: {streaks['max_loss_streak']}")
    print()


def cmd_weekly(args: argparse.Namespace) -> None:
    """Run full weekly analysis."""
    reader = TradeReader(POSITIONS_FILE)

    # Use both recent (for weekly) and all (for lifetime stats)
    recent = reader.load_recent_trades(days=args.days)
    all_trades = reader.load_closed_trades()

    if not all_trades:
        print("No closed trades found.")
        return

    print(f"Loaded {len(all_trades)} total trades, {len(recent)} in last {args.days} days.")

    # Stats analysis (use all trades for better sample size)
    analyzer = StatsAnalyzer(all_trades)
    period = datetime.now().strftime("%Y-W%W")

    # Memory + patterns (run before LLM so patterns feed into reflection)
    patterns = None
    patterns_text = None
    try:
        from learning.memory.store import MemoryStore
        from learning.memory.patterns import PatternDetector
        from learning.config import LEARNING_DB

        LEARNING_DB.parent.mkdir(parents=True, exist_ok=True)
        memory = MemoryStore(LEARNING_DB)
        memory.sync_trades(all_trades)
        detector = PatternDetector(memory, all_trades)
        patterns = detector.detect_all()
        print(f"Detected {len(patterns)} patterns.")

        # Build text for LLM context
        if patterns:
            patterns_text = "\n".join(
                f"- [{p.category}] {p.label}: {p.detail}" for p in patterns
            )
    except ImportError:
        logger.warning("Memory module not available, skipping pattern detection.")
    except Exception as e:
        logger.error("Pattern detection failed: %s", e)

    # Read current config for LLM context
    config_text = None
    config_reader = ConfigReader(CONFIG_FILE, CONFIG_OVERRIDES_FILE)
    try:
        exit_params = config_reader.get_exit_params()
        config_text = "\n".join(f"- {k}: {v}" for k, v in exit_params.items())
    except Exception as e:
        logger.debug("Config read failed: %s", e)

    # Parameter suggestions
    suggestions_data = None
    try:
        from learning.adaptive.suggestions import SuggestionEngine

        engine = SuggestionEngine(analyzer, config_reader)
        param_suggestions = engine.generate_all()
        if param_suggestions:
            print(f"Generated {len(param_suggestions)} parameter suggestions.")
            suggestions_data = [
                {
                    "config_key": s.config_key,
                    "current_value": s.current_value,
                    "suggested_value": s.suggested_value,
                    "change_pct": s.change_pct,
                    "confidence": s.confidence,
                    "reason": s.reason,
                    "category": s.category,
                    "backtest_validated": False,
                }
                for s in param_suggestions
            ]
    except ImportError:
        logger.warning("Suggestions module not available.")
    except Exception as e:
        logger.error("Suggestion generation failed: %s", e)

    # LLM reflection (optional)
    reflection_text = None
    if not args.no_llm:
        try:
            from learning.reflection.analyzer import ReflectionAnalyzer
            from learning.reflection.llm_client import LLMClient

            recent_analyzer = StatsAnalyzer(recent) if recent else analyzer
            llm = LLMClient()
            ref = ReflectionAnalyzer(llm)
            reflection_text = ref.reflect(
                recent_analyzer, analyzer,
                patterns_text=patterns_text,
                config_text=config_text,
            )
            print("LLM reflection completed.")
        except ImportError:
            logger.warning("Reflection module not available, skipping LLM analysis.")
        except Exception as e:
            logger.error("LLM reflection failed: %s", e)

    # Generate report
    reporter = ReportGenerator(OUTPUT_DIR)
    report_path, json_path = reporter.generate(
        analyzer=analyzer,
        period=period,
        reflection_text=reflection_text,
        patterns=patterns,
        suggestions=suggestions_data,
    )
    print(f"\nReport:      {report_path}")
    print(f"Suggestions: {json_path}")


def cmd_reflect(args: argparse.Namespace) -> None:
    """Run LLM reflection only."""
    reader = TradeReader(POSITIONS_FILE)
    recent = reader.load_recent_trades(days=args.days)
    all_trades = reader.load_closed_trades()

    if not recent:
        print(f"No closed trades found in last {args.days} days.")
        return

    from learning.reflection.analyzer import ReflectionAnalyzer
    from learning.reflection.llm_client import LLMClient

    recent_analyzer = StatsAnalyzer(recent)
    all_analyzer = StatsAnalyzer(all_trades)

    llm = LLMClient()
    ref = ReflectionAnalyzer(llm)
    text = ref.reflect(recent_analyzer, all_analyzer)
    print(text)


def cmd_suggest(args: argparse.Namespace) -> None:
    """Generate parameter suggestions with optional backtest validation."""
    reader = TradeReader(POSITIONS_FILE)
    all_trades = reader.load_closed_trades()

    if not all_trades:
        print("No closed trades found.")
        return

    from learning.adaptive.suggestions import SuggestionEngine

    analyzer = StatsAnalyzer(all_trades)
    config_reader = ConfigReader(CONFIG_FILE, CONFIG_OVERRIDES_FILE)
    engine = SuggestionEngine(analyzer, config_reader)

    suggestions = engine.generate_all()
    if not suggestions:
        print("No suggestions generated (insufficient data or confidence).")
        return

    print(f"\n{'='*70}")
    print(f"  Parameter Suggestions ({len(suggestions)} found)")
    print(f"{'='*70}")
    for s in suggestions:
        print(f"\n  [{s.category.upper()}] {s.config_key}")
        print(f"    Current:    {s.current_value}")
        print(f"    Suggested:  {s.suggested_value} ({s.change_pct:+.1f}%)")
        print(f"    Confidence: {s.confidence:.0%}")
        print(f"    Reason:     {s.reason}")

    # Optional backtest validation
    if args.validate:
        print(f"\n{'─'*70}")
        print("  Running backtest validation...")
        from learning.adaptive.validator import BacktestValidator

        validator = BacktestValidator(backtest_days=args.backtest_days)
        results = validator.validate_all(suggestions)

        validated_count = sum(1 for r in results if r.validated)
        print(f"\n  Validated: {validated_count}/{len(results)}")
        for r in results:
            status = "✓ PASS" if r.validated else "✗ FAIL"
            if r.baseline_pnl is not None and r.suggested_pnl is not None:
                print(
                    f"    {status} {r.suggestion.config_key}: "
                    f"${r.baseline_pnl:+.2f} → ${r.suggested_pnl:+.2f} "
                    f"({r.improvement_pct:+.1f}%)"
                )
            else:
                print(f"    {status} {r.suggestion.config_key}: {r.error}")

    print()


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="EMA-Trading-Bot Self-Learning Module",
        prog="learning",
    )
    subparsers = parser.add_subparsers(dest="command")

    # stats
    stats_p = subparsers.add_parser("stats", help="Quick performance stats")
    stats_p.add_argument("--days", type=int, default=30, help="Lookback days")

    # weekly
    weekly_p = subparsers.add_parser("weekly", help="Full weekly analysis")
    weekly_p.add_argument("--days", type=int, default=7, help="Lookback days for recent")
    weekly_p.add_argument("--no-llm", action="store_true", help="Skip LLM reflection")

    # reflect
    reflect_p = subparsers.add_parser("reflect", help="LLM reflection only")
    reflect_p.add_argument("--days", type=int, default=7, help="Lookback days")

    # suggest
    suggest_p = subparsers.add_parser("suggest", help="Parameter suggestions")
    suggest_p.add_argument("--validate", action="store_true", help="Run backtest validation")
    suggest_p.add_argument("--backtest-days", type=int, default=90, help="Backtest lookback days")

    args = parser.parse_args()

    commands = {
        "stats": cmd_stats,
        "weekly": cmd_weekly,
        "reflect": cmd_reflect,
        "suggest": cmd_suggest,
    }

    handler = commands.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)

    handler(args)


if __name__ == "__main__":
    main()
