"""
Cost Tracking — know what you're spending.

Tracks costs for API backends (OpenRouter, etc.). Local models are $0.
Costs are stored in the messages table (cost_usd column) and queried via SQL.
"""

from __future__ import annotations

import logging
from beigebox.storage.sqlite_store import SQLiteStore

logger = logging.getLogger(__name__)


class CostTracker:
    """Query and aggregate cost data from the messages table."""

    def __init__(self, sqlite: SQLiteStore):
        self.sqlite = sqlite

    def get_stats(self, days: int = 30) -> dict:
        """
        Get cost stats for a given period.

        Returns:
            {
                "total": 1.23,
                "average_daily": 0.041,
                "by_model": {"gpt-4-turbo": 0.89, ...},
                "by_day": {"2026-02-20": 0.05, ...},
                "by_conversation": [{"conversation_id": "...", "cost": 0.10, "messages": 5}, ...]
            }
        """
        with self.sqlite._connect() as conn:
            # Total cost in period
            total = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM messages "
                "WHERE cost_usd IS NOT NULL AND timestamp > datetime('now', ?)",
                (f"-{days} days",),
            ).fetchone()[0]

            # Cost by model
            model_rows = conn.execute(
                "SELECT model, COUNT(*) as msg_count, COALESCE(SUM(cost_usd), 0) as cost "
                "FROM messages "
                "WHERE cost_usd IS NOT NULL AND timestamp > datetime('now', ?) "
                "GROUP BY model ORDER BY cost DESC",
                (f"-{days} days",),
            ).fetchall()
            by_model = {
                row["model"]: {"cost": row["cost"], "messages": row["msg_count"]}
                for row in model_rows
            }

            # Cost by day
            day_rows = conn.execute(
                "SELECT DATE(timestamp) as day, COALESCE(SUM(cost_usd), 0) as cost "
                "FROM messages "
                "WHERE cost_usd IS NOT NULL AND timestamp > datetime('now', ?) "
                "GROUP BY DATE(timestamp) ORDER BY day DESC",
                (f"-{days} days",),
            ).fetchall()
            by_day = {row["day"]: row["cost"] for row in day_rows}

            # Top conversations by cost
            conv_rows = conn.execute(
                "SELECT conversation_id, COUNT(*) as msg_count, "
                "COALESCE(SUM(cost_usd), 0) as cost "
                "FROM messages "
                "WHERE cost_usd IS NOT NULL AND timestamp > datetime('now', ?) "
                "GROUP BY conversation_id ORDER BY cost DESC LIMIT 20",
                (f"-{days} days",),
            ).fetchall()
            by_conversation = [
                {
                    "conversation_id": row["conversation_id"],
                    "cost": row["cost"],
                    "messages": row["msg_count"],
                }
                for row in conv_rows
            ]

        return {
            "total": round(total, 6),
            "average_daily": round(total / max(days, 1), 6),
            "days_queried": days,
            "by_model": by_model,
            "by_day": by_day,
            "by_conversation": by_conversation,
        }

    def get_total(self) -> float:
        """Get all-time total cost."""
        with self.sqlite._connect() as conn:
            total = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM messages WHERE cost_usd IS NOT NULL"
            ).fetchone()[0]
        return round(total, 6)
