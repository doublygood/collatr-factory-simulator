"""Tests for factory_simulator.time_utils shared time constants and conversions."""

from datetime import UTC, datetime

from factory_simulator.time_utils import (
    REFERENCE_EPOCH,
    REFERENCE_EPOCH_TS,
    sim_time_to_datetime,
    sim_time_to_iso,
)


def test_reference_epoch_value() -> None:
    """REFERENCE_EPOCH_TS matches datetime(2026, 1, 1, UTC).timestamp()."""
    expected = datetime(2026, 1, 1, tzinfo=UTC).timestamp()
    assert expected == REFERENCE_EPOCH_TS


def test_reference_epoch_datetime() -> None:
    """REFERENCE_EPOCH is 2026-01-01T00:00:00Z."""
    assert datetime(2026, 1, 1, tzinfo=UTC) == REFERENCE_EPOCH


def test_sim_time_to_datetime_zero() -> None:
    """sim_time=0 returns the reference epoch."""
    result = sim_time_to_datetime(0.0)
    assert result == datetime(2026, 1, 1, tzinfo=UTC)


def test_sim_time_to_datetime_positive() -> None:
    """sim_time=3600 returns one hour after epoch."""
    result = sim_time_to_datetime(3600.0)
    assert result == datetime(2026, 1, 1, 1, 0, 0, tzinfo=UTC)


def test_sim_time_to_datetime_offset() -> None:
    """offset_s shifts the result by the given seconds."""
    result = sim_time_to_datetime(0.0, offset_s=7200.0)
    assert result == datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)


def test_sim_time_to_iso_format_zero() -> None:
    """sim_time=0 produces '2026-01-01T00:00:00.000Z'."""
    result = sim_time_to_iso(0.0)
    assert result == "2026-01-01T00:00:00.000Z"


def test_sim_time_to_iso_format_milliseconds() -> None:
    """Verify millisecond precision in the ISO string."""
    # 1.5 seconds → 500 milliseconds
    result = sim_time_to_iso(1.5)
    assert result == "2026-01-01T00:00:01.500Z"


def test_sim_time_to_iso_offset() -> None:
    """offset_s is applied to the ISO string."""
    # 1 hour offset
    result = sim_time_to_iso(0.0, offset_s=3600.0)
    assert result == "2026-01-01T01:00:00.000Z"


def test_sim_time_to_iso_z_suffix() -> None:
    """ISO string always ends with 'Z'."""
    result = sim_time_to_iso(12345.678)
    assert result.endswith("Z")
    # Verify it's a valid format: YYYY-MM-DDTHH:MM:SS.mmmZ
    assert len(result) == 24  # "2026-01-01T03:25:45.678Z"
