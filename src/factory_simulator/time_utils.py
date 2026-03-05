"""Shared time constants and conversion utilities.

All simulation timestamps are offsets from the reference epoch
(2026-01-01T00:00:00Z).  These utilities convert between sim_time
floats and wall-clock datetime/ISO representations.
"""

from __future__ import annotations

from datetime import UTC, datetime

# Reference epoch: 2026-01-01T00:00:00Z
# All sim_time values are seconds from this epoch.
REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
REFERENCE_EPOCH_TS: float = REFERENCE_EPOCH.timestamp()


def sim_time_to_datetime(sim_time: float, offset_s: float = 0.0) -> datetime:
    """Convert sim_time to a timezone-aware datetime.

    Parameters
    ----------
    sim_time:
        Seconds from the reference epoch.
    offset_s:
        Optional offset in seconds (e.g. clock drift).
    """
    return datetime.fromtimestamp(
        REFERENCE_EPOCH_TS + sim_time + offset_s, tz=UTC,
    )


def sim_time_to_iso(sim_time: float, offset_s: float = 0.0) -> str:
    """Convert sim_time to ISO 8601 string with millisecond precision.

    Returns format: ``2026-01-01T00:00:00.000Z``
    """
    dt = sim_time_to_datetime(sim_time, offset_s)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"
