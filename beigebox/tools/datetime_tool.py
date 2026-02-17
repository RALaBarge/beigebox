"""
DateTime tool — provides current time, date, and timezone conversions.

LLMs don't know what time it is. This tool does.

Examples the decision LLM would route here:
  "What time is it?"
  "What's today's date?"
  "What time is it in Tokyo?"
  "How many days until March 15?"
"""

import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)

# Common timezone offsets (no pytz dependency needed)
TIMEZONE_OFFSETS = {
    "utc": 0, "gmt": 0,
    "est": -5, "edt": -4, "et": -5,
    "cst": -6, "cdt": -5, "ct": -6,
    "mst": -7, "mdt": -6, "mt": -7,
    "pst": -8, "pdt": -7, "pt": -8,
    "gmt+1": 1, "cet": 1, "cest": 2,
    "gmt+2": 2, "eet": 2, "eest": 3,
    "gmt+3": 3, "msk": 3,
    "gmt+5:30": 5.5, "ist": 5.5,
    "gmt+8": 8, "cst_china": 8, "sgt": 8,
    "jst": 9, "kst": 9,
    "aest": 10, "aedt": 11,
    "nzst": 12, "nzdt": 13,
    # City shortcuts
    "new york": -5, "los angeles": -8, "chicago": -6, "denver": -7,
    "london": 0, "paris": 1, "berlin": 1, "moscow": 3,
    "dubai": 4, "mumbai": 5.5, "delhi": 5.5,
    "bangkok": 7, "beijing": 8, "shanghai": 8, "hong kong": 8,
    "tokyo": 9, "seoul": 9, "sydney": 10, "auckland": 12,
    "ann arbor": -5, "detroit": -5, "michigan": -5,
}


class DateTimeTool:
    """Current time and date information."""

    def __init__(self, local_tz_offset: float = -5.0):
        """
        Args:
            local_tz_offset: Local timezone offset from UTC in hours.
                             Default -5 (EST / Ann Arbor).
        """
        self.local_offset = local_tz_offset
        logger.info("DateTimeTool initialized (local UTC%+.1f)", local_tz_offset)

    def run(self, query: str) -> str:
        """Answer time/date queries."""
        query_lower = query.lower().strip()
        now_utc = datetime.now(timezone.utc)

        # Check for timezone conversion
        for tz_name, offset in TIMEZONE_OFFSETS.items():
            if tz_name in query_lower:
                tz = timezone(timedelta(hours=offset))
                now_tz = now_utc.astimezone(tz)
                return (
                    f"Current time in {tz_name.upper()}: "
                    f"{now_tz.strftime('%I:%M %p, %A %B %d, %Y')} "
                    f"(UTC{offset:+.1f})"
                )

        # Default: local time
        local_tz = timezone(timedelta(hours=self.local_offset))
        now_local = now_utc.astimezone(local_tz)

        lines = [
            f"Local time: {now_local.strftime('%I:%M %p, %A %B %d, %Y')} (UTC{self.local_offset:+.1f})",
            f"UTC time: {now_utc.strftime('%I:%M %p, %A %B %d, %Y')}",
            f"Unix timestamp: {int(now_utc.timestamp())}",
        ]
        return "\n".join(lines)
