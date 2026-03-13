"""
Linear Integration for Futures Trading

Creates and updates Linear issues for position tracking
"""

import sys
import os
from typing import Optional
from datetime import datetime
import logging

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

try:
    from integrations.linear_integration import LinearAPIClient
    LINEAR_AVAILABLE = True
except ImportError:
    LINEAR_AVAILABLE = False

logger = logging.getLogger(__name__)


class FuturesLinearClient:
    """
    Linear client for futures position tracking
    """

    def __init__(self):
        """Initialize Linear client"""
        if not LINEAR_AVAILABLE:
            logger.warning("Linear integration not available")
            self.client = None
            self.team_id = None
        else:
            try:
                self.client = LinearAPIClient()
                # Get team ID from environment
                self.team_id = os.getenv('LINEAR_TEAM_ID')
                if not self.team_id:
                    logger.error("LINEAR_TEAM_ID not found in .env")
                    self.client = None
                else:
                    logger.info(f"Linear client initialized for futures trading (Team: {self.team_id})")
            except Exception as e:
                logger.error(f"Failed to initialize Linear client: {e}")
                self.client = None
                self.team_id = None

    def create_position_issue(
        self,
        position,
        signal
    ) -> Optional[str]:
        """
        Create Linear issue for a new position

        Args:
            position: Position object
            signal: TradingSignal object

        Returns:
            Linear issue ID or None
        """
        if not self.client:
            return None

        try:
            # Format title
            direction_emoji = "🟢" if position.side == "BUY" else "🔴"
            title = f"[FUTURES-{position.symbol}] {direction_emoji} {position.side} @ ${position.entry_price:.2f} ({position.leverage}x)"

            # Format description
            description = self._format_position_description(position, signal)

            # Create issue
            issue = self.client.create_issue(
                team_id=self.team_id,
                title=title,
                description=description,
                priority=2,  # Medium priority
                labels=[]  # Labels need to be created first in Linear
            )

            issue_id = issue.get('id') if issue else None

            if issue_id:
                logger.info(f"Linear issue created: {issue_id} for {position.position_id}")

            return issue_id

        except Exception as e:
            logger.error(f"Error creating Linear issue: {e}")
            return None

    def _format_position_description(self, position, signal) -> str:
        """
        Format position description for Linear issue

        Args:
            position: Position object
            signal: TradingSignal object

        Returns:
            Markdown formatted description
        """
        # Emojis
        direction = "📈 LONG" if position.side == "BUY" else "📉 SHORT"

        # Format TP targets
        tp1_str = f"${signal.take_profit_1:.2f}" if signal.take_profit_1 else "N/A"
        tp2_str = f"${signal.take_profit_2:.2f}" if signal.take_profit_2 else "N/A"
        sl_str = f"${position.stop_loss:.2f}" if position.stop_loss else "N/A"

        description = f"""## {direction} Position

### 📊 Entry Details
- **Symbol**: {position.symbol}
- **Side**: {position.side}
- **Entry Price**: ${position.entry_price:.2f}
- **Position Size**: {position.size:.6f}
- **Leverage**: {position.leverage}x
- **Margin Used**: ${position.margin:.2f}
- **Entry Time**: {position.timestamp.strftime('%Y-%m-%d %H:%M:%S')}

### 📈 Current Status
- **Current Price**: ${position.current_price:.2f}
- **PNL**: ${position.pnl_usd:.2f} ({position.pnl_percent:+.2f}%)
- **ROI**: {position.roi_percent:+.2f}% (with leverage)
- **Status**: {position.status}

### 🎯 Targets
- **TP1 (70%)**: {tp1_str} (S/R level)
- **TP2 (30%)**: {tp2_str} (Fibo 1.618)
- **Stop Loss**: {sl_str} (-50% PNL)

### 🧠 Strategy Context

**H4 Trend Analysis:**
- Trend: {signal.h4_trend}
- EMA34: ${signal.h4_ema34:.2f}
- EMA89: ${signal.h4_ema89:.2f}

**H1 Filter:**
- RSI: {signal.h1_rsi:.2f}

**M15 Entry Signal:**
- EMA34: ${signal.m15_ema34:.2f}
- EMA89: ${signal.m15_ema89:.2f}
- Wick Confirmation: {signal.wick_ratio:.1f}% ✅

### 📝 Notes
Position opened by Futures Trading Bot (Multi-timeframe Strategy).

---
*Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*
"""

        return description

    def update_position_pnl(
        self,
        issue_id: str,
        position
    ) -> bool:
        """
        Update position PNL in Linear issue (as comment)

        Args:
            issue_id: Linear issue ID
            position: Position object

        Returns:
            True if successful
        """
        if not self.client or not issue_id:
            return False

        try:
            # Format update comment
            comment = self._format_pnl_update(position)

            # Add comment
            self.client.add_comment(issue_id, comment)

            logger.debug(f"Updated Linear issue {issue_id} with PNL")
            return True

        except Exception as e:
            logger.error(f"Error updating Linear issue: {e}")
            return False

    def _format_pnl_update(self, position) -> str:
        """
        Format PNL update comment

        Args:
            position: Position object

        Returns:
            Markdown formatted comment
        """
        # Status emoji
        if position.pnl_usd > 0:
            status_emoji = "✅"
        elif position.pnl_usd < 0:
            status_emoji = "⚠️"
        else:
            status_emoji = "⏸️"

        comment = f"""### {status_emoji} PNL Update

**Current Price**: ${position.current_price:.2f}
**PNL**: ${position.pnl_usd:.2f} ({position.pnl_percent:+.2f}%)
**ROI**: {position.roi_percent:+.2f}%
**Remaining Size**: {position.remaining_size:.6f} / {position.size:.6f}

**Status**: {position.status}
"""

        # Add TP closure info
        if position.tp1_closed:
            comment += "\n✅ TP1 (70%) closed"
        if position.tp2_closed:
            comment += "\n✅ TP2 (30%) closed"

        comment += f"\n\n*Updated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"

        return comment

    def close_position_issue(
        self,
        issue_id: str,
        position
    ) -> bool:
        """
        Close Linear issue when position is closed

        Args:
            issue_id: Linear issue ID
            position: Position object

        Returns:
            True if successful
        """
        if not self.client or not issue_id:
            return False

        try:
            # Add final comment
            final_comment = self._format_final_comment(position)
            self.client.add_comment(issue_id, final_comment)

            # Update issue status to Done
            self.client.update_issue_status(issue_id, "Done")

            logger.info(f"Closed Linear issue {issue_id} for position {position.position_id}")
            return True

        except Exception as e:
            logger.error(f"Error closing Linear issue: {e}")
            return False

    def _format_final_comment(self, position) -> str:
        """
        Format final comment when position closes

        Args:
            position: Position object

        Returns:
            Markdown formatted comment
        """
        # Result emoji
        if position.pnl_usd > 0:
            result_emoji = "🎉 WIN"
            color = "green"
        elif position.pnl_usd < 0:
            result_emoji = "❌ LOSS"
            color = "red"
        else:
            result_emoji = "⚪ BREAKEVEN"
            color = "gray"

        comment = f"""## {result_emoji} - Position Closed

### 📊 Final Results
- **Close Price**: ${position.current_price:.2f}
- **Entry Price**: ${position.entry_price:.2f}
- **Final PNL**: ${position.pnl_usd:.2f} ({position.pnl_percent:+.2f}%)
- **ROI**: {position.roi_percent:+.2f}%
- **Close Reason**: {position.close_reason}

### 💰 Trade Summary
- **Symbol**: {position.symbol}
- **Side**: {position.side}
- **Leverage**: {position.leverage}x
- **Margin**: ${position.margin:.2f}
- **Duration**: {(datetime.now() - position.timestamp).total_seconds() / 3600:.1f} hours

### 📝 Review
"""

        if position.pnl_usd > 0:
            comment += "✅ **Profitable trade**. Strategy worked as expected."
        else:
            comment += "⚠️ **Loss**. Review strategy and risk management."

        comment += f"\n\n---\n*Closed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*"

        return comment

    def search_open_futures_issues(self, since_date: str = None) -> list:
        """
        Search for open futures trading issues from Linear
        Includes latest comment (PNL update) for each issue

        Args:
            since_date: Only show issues created since this date (ISO format: "2026-01-29")
                       If None, defaults to today

        Returns:
            List of issue dicts with id, identifier, title, description, url, state, comments
        """
        if not self.client or not self.team_id:
            return []

        try:
            # Default to today
            if not since_date:
                since_date = datetime.now().strftime('%Y-%m-%d')

            # Custom query to get futures issues with latest comment
            gql_query = """
            query Issues($filter: IssueFilter, $first: Int) {
                issues(filter: $filter, first: $first, orderBy: createdAt) {
                    nodes {
                        id
                        identifier
                        title
                        description
                        priority
                        url
                        createdAt
                        state {
                            name
                            type
                        }
                        comments(first: 1, orderBy: createdAt) {
                            nodes {
                                body
                                createdAt
                            }
                        }
                    }
                }
            }
            """

            # Get non-completed issues created since date
            filter_obj = {
                'team': {'id': {'eq': self.team_id}},
                'state': {'type': {'nin': ["completed", "canceled"]}},
                'createdAt': {'gte': f"{since_date}T00:00:00.000Z"}
            }

            variables = {
                'filter': filter_obj,
                'first': 50
            }

            data = self.client._execute_query(gql_query, variables)
            all_issues = data.get('issues', {}).get('nodes', [])

            # Filter only futures positions by title prefix
            futures_issues = [
                issue for issue in all_issues
                if "[FUTURES-" in (issue.get("title", ""))
            ]

            logger.info(f"[LINEAR] Found {len(futures_issues)} open futures issues (since {since_date})")
            return futures_issues

        except Exception as e:
            logger.error(f"Error searching futures issues: {e}")
            return []
