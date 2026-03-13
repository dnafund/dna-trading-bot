"""Validate parameter suggestions by running the existing backtest engine."""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from learning.adaptive.suggestions import ParameterSuggestion
from learning.config import PROJECT_ROOT

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ValidationResult:
    """Result of backtesting a parameter suggestion."""

    suggestion: ParameterSuggestion
    baseline_pnl: Optional[float]
    suggested_pnl: Optional[float]
    improvement_pct: Optional[float]
    validated: bool
    error: Optional[str] = None


class BacktestValidator:
    """Validate suggestions by running backtest with modified params.

    Uses the existing backtest engine via subprocess to avoid coupling.
    """

    def __init__(self, backtest_days: int = 90):
        self._backtest_days = backtest_days
        self._backtest_script = PROJECT_ROOT / "src" / "trading" / "backtest" / "engine.py"

    def validate(self, suggestion: ParameterSuggestion) -> ValidationResult:
        """Run backtest comparison: baseline vs suggested params.

        Returns ValidationResult with improvement data.
        """
        if not self._backtest_script.exists():
            return ValidationResult(
                suggestion=suggestion,
                baseline_pnl=None,
                suggested_pnl=None,
                improvement_pct=None,
                validated=False,
                error="Backtest engine not found",
            )

        try:
            # Run baseline backtest
            baseline_pnl = self._run_backtest({})
            if baseline_pnl is None:
                return ValidationResult(
                    suggestion=suggestion,
                    baseline_pnl=None,
                    suggested_pnl=None,
                    improvement_pct=None,
                    validated=False,
                    error="Baseline backtest failed",
                )

            # Run backtest with suggested param
            overrides = {suggestion.config_key: suggestion.suggested_value}
            suggested_pnl = self._run_backtest(overrides)
            if suggested_pnl is None:
                return ValidationResult(
                    suggestion=suggestion,
                    baseline_pnl=baseline_pnl,
                    suggested_pnl=None,
                    improvement_pct=None,
                    validated=False,
                    error="Suggested backtest failed",
                )

            improvement = (
                ((suggested_pnl - baseline_pnl) / abs(baseline_pnl) * 100)
                if baseline_pnl != 0 else 0
            )

            return ValidationResult(
                suggestion=suggestion,
                baseline_pnl=round(baseline_pnl, 2),
                suggested_pnl=round(suggested_pnl, 2),
                improvement_pct=round(improvement, 1),
                validated=suggested_pnl > baseline_pnl,
            )

        except Exception as e:
            logger.error("Validation failed for %s: %s", suggestion.config_key, e)
            return ValidationResult(
                suggestion=suggestion,
                baseline_pnl=None,
                suggested_pnl=None,
                improvement_pct=None,
                validated=False,
                error=str(e),
            )

    def validate_all(
        self, suggestions: list[ParameterSuggestion]
    ) -> list[ValidationResult]:
        """Validate all suggestions. Returns results in same order."""
        results = []
        for suggestion in suggestions:
            logger.info("Validating: %s = %s → %s",
                        suggestion.config_key,
                        suggestion.current_value,
                        suggestion.suggested_value)
            result = self.validate(suggestion)
            results.append(result)
            if result.validated:
                logger.info(
                    "  ✓ Improvement: %+.1f%% (baseline: $%.2f → suggested: $%.2f)",
                    result.improvement_pct or 0,
                    result.baseline_pnl or 0,
                    result.suggested_pnl or 0,
                )
            else:
                logger.info("  ✗ Not validated: %s", result.error or "no improvement")
        return results

    def _run_backtest(self, config_overrides: dict) -> Optional[float]:
        """Run backtest engine and extract total PNL.

        Uses subprocess to avoid importing bot code directly.
        """
        try:
            # Write temp overrides file
            overrides_file = PROJECT_ROOT / "data" / "_learning_temp_overrides.json"
            overrides_file.write_text(
                json.dumps(config_overrides, indent=2), encoding="utf-8"
            )

            result = subprocess.run(
                [
                    "python3", "-m", "src.trading.backtest.engine",
                    "--days", str(self._backtest_days),
                    "--config-overrides", str(overrides_file),
                    "--output-format", "json",
                ],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(PROJECT_ROOT),
            )

            # Cleanup
            if overrides_file.exists():
                overrides_file.unlink()

            if result.returncode != 0:
                logger.warning("Backtest failed: %s", result.stderr[:500])
                return None

            # Parse JSON output for total PNL
            output = result.stdout.strip()
            if not output:
                return None

            data = json.loads(output)
            return data.get("total_pnl", data.get("pnl", None))

        except subprocess.TimeoutExpired:
            logger.error("Backtest timed out after 300s")
            return None
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Failed to parse backtest output: %s", e)
            return None
        except Exception as e:
            logger.error("Backtest execution error: %s", e)
            return None
