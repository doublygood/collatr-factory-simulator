"""Tests for the StringGeneratorModel.

PRD Reference: Section 4.2.14 (String Generator)
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from factory_simulator.models.string_generator import StringGeneratorModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Default start: 2024-01-15 06:00:00 UTC (Monday morning)
DEFAULT_START = "2024-01-15T06:00:00+00:00"
DT = 1.0  # 1 second tick


def _make_model(
    *,
    template: str = "{date:%y%m%d}-{line}-{seq:03d}",
    line_id: str = "L1",
    reset_at: str = "00:00",
    start_time: str | datetime | None = DEFAULT_START,
) -> StringGeneratorModel:
    return StringGeneratorModel(
        template=template,
        line_id=line_id,
        reset_at=reset_at,
        start_time=start_time,
    )


def _advance_to(model: StringGeneratorModel, seconds: float) -> str:
    """Generate at a specific sim_time and return the value."""
    return model.generate(seconds, DT)


# ===================================================================
# Construction
# ===================================================================


class TestConstruction:
    def test_defaults(self) -> None:
        m = StringGeneratorModel()
        assert m.template == "{date:%y%m%d}-{line}-{seq:03d}"
        assert m.line_id == "L1"
        assert m.sequence == 0

    def test_explicit_params(self) -> None:
        m = StringGeneratorModel(
            template="{line}-{seq:04d}",
            line_id="L3",
            reset_at="06:00",
            start_time="2026-03-02T08:00:00+00:00",
        )
        assert m.template == "{line}-{seq:04d}"
        assert m.line_id == "L3"

    def test_start_time_datetime(self) -> None:
        dt = datetime(2026, 3, 2, 8, 0, 0, tzinfo=UTC)
        m = StringGeneratorModel(start_time=dt)
        assert m.sequence == 0

    def test_invalid_reset_at(self) -> None:
        with pytest.raises(ValueError, match="reset_at must be"):
            StringGeneratorModel(reset_at="invalid")

    def test_initial_value_empty(self) -> None:
        m = StringGeneratorModel()
        assert m.value == ""


# ===================================================================
# Basic Generation
# ===================================================================


class TestBasicGeneration:
    def test_default_format(self) -> None:
        """Default template produces expected format."""
        m = _make_model(start_time="2026-03-02T08:00:00+00:00")
        m.new_batch()  # batch 1
        result = _advance_to(m, 0.0)
        assert result == "260302-L1-001"

    def test_prd_example_output(self) -> None:
        """PRD example: '260302-L1-007' (2 March 2026, Line 1, batch 7)."""
        m = _make_model(start_time="2026-03-02T08:00:00+00:00")
        for _ in range(7):
            m.new_batch()
        result = _advance_to(m, 0.0)
        assert result == "260302-L1-007"

    def test_sequence_increments(self) -> None:
        m = _make_model()
        results = []
        for i in range(3):
            m.new_batch()
            results.append(m.generate(float(i), DT))
        assert "001" in results[0]
        assert "002" in results[1]
        assert "003" in results[2]

    def test_no_batch_sequence_zero(self) -> None:
        """Without calling new_batch, sequence stays at 0."""
        m = _make_model()
        result = _advance_to(m, 0.0)
        assert "000" in result

    def test_custom_template(self) -> None:
        m = _make_model(
            template="BATCH-{line}-{seq:05d}",
            line_id="A2",
        )
        m.new_batch()
        result = _advance_to(m, 0.0)
        assert result == "BATCH-A2-00001"

    def test_date_changes_with_sim_time(self) -> None:
        """Date component changes as sim_time advances past midnight."""
        m = _make_model(start_time="2026-03-02T23:00:00+00:00")
        m.new_batch()
        # At t=0: 2026-03-02 23:00 -> date is 260302
        result1 = m.generate(0.0, DT)
        assert "260302" in result1

        # Advance 2 hours (7200s): 2026-03-03 01:00 -> date is 260303
        result2 = m.generate(7200.0, DT)
        assert "260303" in result2

    def test_line_id_in_output(self) -> None:
        m = _make_model(line_id="LINE-7")
        m.new_batch()
        result = _advance_to(m, 0.0)
        assert "LINE-7" in result


# ===================================================================
# Midnight Reset
# ===================================================================


class TestMidnightReset:
    def test_sequence_resets_at_midnight(self) -> None:
        """Sequence resets to 0 when crossing midnight (reset_at='00:00')."""
        # Start at 23:30, so midnight is 30 minutes (1800s) away
        m = _make_model(
            start_time="2026-03-02T23:30:00+00:00",
            reset_at="00:00",
        )
        m.new_batch()
        m.new_batch()
        m.new_batch()
        assert m.sequence == 3

        # Generate before midnight
        m.generate(1700.0, DT)  # 23:58:20
        assert m.sequence == 3

        # Generate after midnight (1800s = 30 min)
        m.generate(1900.0, DT)  # 00:01:40 next day
        assert m.sequence == 0

    def test_sequence_resets_once_per_day(self) -> None:
        """Reset only happens once per midnight, not every tick after midnight."""
        m = _make_model(
            start_time="2026-03-02T23:50:00+00:00",
            reset_at="00:00",
        )
        m.new_batch()
        assert m.sequence == 1

        # Cross midnight
        m.generate(700.0, DT)  # 00:01:40
        assert m.sequence == 0

        # Add more batches after midnight
        m.new_batch()
        m.new_batch()
        assert m.sequence == 2

        # Generate again still same day - should not reset again
        m.generate(800.0, DT)
        assert m.sequence == 2

    def test_custom_reset_time(self) -> None:
        """Reset at a custom time (e.g. 06:00 shift start)."""
        m = _make_model(
            start_time="2026-03-02T05:30:00+00:00",
            reset_at="06:00",
        )
        m.new_batch()
        m.new_batch()
        assert m.sequence == 2

        # Cross 06:00 (1800s after 05:30)
        m.generate(1900.0, DT)  # 06:01:40
        assert m.sequence == 0

    def test_multiple_day_crossings(self) -> None:
        """Sequence resets on each midnight crossing."""
        m = _make_model(
            start_time="2026-03-02T23:50:00+00:00",
            reset_at="00:00",
        )

        # Day 1: add batches
        m.new_batch()
        m.new_batch()

        # Cross first midnight (600s)
        m.generate(700.0, DT)
        assert m.sequence == 0

        # Day 2: add batches
        m.new_batch()
        m.new_batch()
        m.new_batch()
        assert m.sequence == 3

        # Cross second midnight (600 + 86400 = 87000s)
        m.generate(87100.0, DT)
        assert m.sequence == 0


# ===================================================================
# new_batch
# ===================================================================


class TestNewBatch:
    def test_increments_sequence(self) -> None:
        m = _make_model()
        assert m.sequence == 0
        m.new_batch()
        assert m.sequence == 1
        m.new_batch()
        assert m.sequence == 2

    def test_many_batches(self) -> None:
        m = _make_model()
        for _ in range(50):
            m.new_batch()
        assert m.sequence == 50

    def test_sequence_survives_generate(self) -> None:
        """Sequence persists across generate calls."""
        m = _make_model()
        m.new_batch()
        m.generate(0.0, DT)
        m.new_batch()
        m.generate(1.0, DT)
        assert m.sequence == 2


# ===================================================================
# Reset
# ===================================================================


class TestReset:
    def test_reset_clears_sequence(self) -> None:
        m = _make_model()
        m.new_batch()
        m.new_batch()
        assert m.sequence == 2
        m.reset()
        assert m.sequence == 0

    def test_reset_clears_value(self) -> None:
        m = _make_model()
        m.new_batch()
        m.generate(0.0, DT)
        assert m.value != ""
        m.reset()
        assert m.value == ""

    def test_reset_clears_midnight_tracking(self) -> None:
        """After reset, midnight tracking starts fresh."""
        m = _make_model(
            start_time="2026-03-02T23:50:00+00:00",
            reset_at="00:00",
        )
        m.new_batch()
        m.generate(700.0, DT)  # Past midnight
        assert m.sequence == 0

        m.reset()
        m.new_batch()
        m.new_batch()
        assert m.sequence == 2

        # Generate at a pre-midnight time (fresh tracking)
        m.generate(100.0, DT)
        assert m.sequence == 2


# ===================================================================
# Value Property
# ===================================================================


class TestValueProperty:
    def test_value_tracks_last_generate(self) -> None:
        m = _make_model()
        m.new_batch()
        result = m.generate(0.0, DT)
        assert m.value == result

    def test_value_updates_each_generate(self) -> None:
        m = _make_model(start_time="2026-03-02T23:59:00+00:00")
        m.new_batch()
        v1 = m.generate(0.0, DT)
        v2 = m.generate(120.0, DT)  # Next day
        # Date should differ
        assert v1 != v2
        assert m.value == v2


# ===================================================================
# Template Variations
# ===================================================================


class TestTemplateVariations:
    def test_date_only(self) -> None:
        m = _make_model(
            template="{date:%Y-%m-%d}",
            start_time="2026-03-02T12:00:00+00:00",
        )
        result = m.generate(0.0, DT)
        assert result == "2026-03-02"

    def test_seq_only(self) -> None:
        m = _make_model(template="B{seq:06d}")
        m.new_batch()
        result = m.generate(0.0, DT)
        assert result == "B000001"

    def test_line_only(self) -> None:
        m = _make_model(template="LINE={line}", line_id="X9")
        result = m.generate(0.0, DT)
        assert result == "LINE=X9"

    def test_complex_template(self) -> None:
        m = _make_model(
            template="FAC-{date:%y%m%d}/{line}/BATCH-{seq:03d}",
            line_id="L2",
            start_time="2026-03-02T08:00:00+00:00",
        )
        m.new_batch()
        result = m.generate(0.0, DT)
        assert result == "FAC-260302/L2/BATCH-001"


# ===================================================================
# Edge Cases
# ===================================================================


class TestEdgeCases:
    def test_zero_sim_time(self) -> None:
        m = _make_model()
        result = m.generate(0.0, DT)
        assert isinstance(result, str)

    def test_very_large_sim_time(self) -> None:
        """365 days of simulation."""
        m = _make_model(start_time="2026-01-01T00:00:00+00:00")
        m.new_batch()
        result = m.generate(365 * 86400.0, DT)
        assert "270101" in result  # 2027-01-01

    def test_high_sequence_numbers(self) -> None:
        m = _make_model()
        for _ in range(999):
            m.new_batch()
        result = m.generate(0.0, DT)
        assert "999" in result

    def test_sequence_above_format_width(self) -> None:
        """Sequence numbers above 999 still work (format widens)."""
        m = _make_model()
        for _ in range(1500):
            m.new_batch()
        result = m.generate(0.0, DT)
        assert "1500" in result

    def test_start_time_naive_gets_utc(self) -> None:
        """Naive start time gets UTC timezone."""
        m = StringGeneratorModel(start_time="2026-03-02T08:00:00")
        m.new_batch()
        result = m.generate(0.0, DT)
        assert "260302" in result


# ===================================================================
# Package Imports
# ===================================================================


class TestPackageImports:
    def test_importable_from_models(self) -> None:
        from factory_simulator.models import StringGeneratorModel as Imported

        assert Imported is StringGeneratorModel

    def test_in_all(self) -> None:
        from factory_simulator import models

        assert "StringGeneratorModel" in models.__all__
