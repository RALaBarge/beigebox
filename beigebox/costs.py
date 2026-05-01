"""
Cost Tracking — know what you're spending.

Tracks costs for API backends (OpenRouter, etc.). Local models are $0.
Costs are stored in the messages table (cost_usd column) and queried via SQL.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from beigebox.storage.db.base import BaseDB

logger = logging.getLogger(__name__)


class CostTracker:
    """Query and aggregate cost data from the messages table.

    Takes a ``BaseDB`` (not a high-level repo) — the queries are pure
    aggregates over the messages table, no domain logic, so going through
    the DB shim directly avoids growing ConversationRepo's surface with
    cost-shaped read methods.
    """

    def __init__(self, db: "BaseDB"):
        self._db = db

    def get_stats(self, days: int = 30) -> dict:
        """Get cost stats for a given period."""
        ph = self._db._placeholder()
        ts = f"-{days} days"

        total = self._db.fetchone(
            f"SELECT COALESCE(SUM(cost_usd), 0) AS n FROM messages "
            f"WHERE cost_usd IS NOT NULL AND timestamp > datetime('now', {ph})",
            (ts,),
        )["n"]

        model_rows = self._db.fetchall(
            f"SELECT model, COUNT(*) as msg_count, COALESCE(SUM(cost_usd), 0) as cost "
            f"FROM messages "
            f"WHERE cost_usd IS NOT NULL AND timestamp > datetime('now', {ph}) "
            f"GROUP BY model ORDER BY cost DESC",
            (ts,),
        )
        by_model = {
            row["model"]: {"cost": row["cost"], "messages": row["msg_count"]}
            for row in model_rows
        }

        day_rows = self._db.fetchall(
            f"SELECT DATE(timestamp) as day, COALESCE(SUM(cost_usd), 0) as cost "
            f"FROM messages "
            f"WHERE cost_usd IS NOT NULL AND timestamp > datetime('now', {ph}) "
            f"GROUP BY DATE(timestamp) ORDER BY day DESC",
            (ts,),
        )
        by_day = {row["day"]: row["cost"] for row in day_rows}

        conv_rows = self._db.fetchall(
            f"SELECT conversation_id, COUNT(*) as msg_count, "
            f"COALESCE(SUM(cost_usd), 0) as cost "
            f"FROM messages "
            f"WHERE cost_usd IS NOT NULL AND timestamp > datetime('now', {ph}) "
            f"GROUP BY conversation_id ORDER BY cost DESC LIMIT 20",
            (ts,),
        )
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
        total = self._db.fetchone(
            "SELECT COALESCE(SUM(cost_usd), 0) AS n FROM messages WHERE cost_usd IS NOT NULL",
        )["n"]
        return round(total, 6)
