"""UTC timestamp formatting shared by ledger writers."""

from datetime import datetime, timezone


def now_iso() -> str:
    """Return the current UTC time at second precision with a Z suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
