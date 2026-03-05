"""Tests for the SignalValue and SignalStore.

Validates:
- set/get round-trip for float and string values
- Quality flag preserved and validated
- get returns None for missing signals
- get_value returns default for missing signals
- get_all returns all entries
- signal_ids returns sorted list
- Containment, length, iteration
- Update in-place (no duplicate entries)
- clear() empties the store

PRD Reference: Section 8.2 (Data Flow), Section 8.3 (Concurrency Model)
CLAUDE.md Rule 9: Single Writer, No Locks
"""

from __future__ import annotations

import pytest

from factory_simulator.store import QUALITY_FLAGS, SignalStore, SignalValue

# ---------------------------------------------------------------------------
# SignalValue dataclass
# ---------------------------------------------------------------------------


class TestSignalValue:
    def test_float_value(self) -> None:
        sv = SignalValue(signal_id="press.line_speed", value=120.5, timestamp=1.0)
        assert sv.signal_id == "press.line_speed"
        assert sv.value == 120.5
        assert sv.timestamp == 1.0
        assert sv.quality == "good"  # default

    def test_string_value(self) -> None:
        sv = SignalValue(
            signal_id="mixer.batch_id", value="B-20240115-001", timestamp=2.5
        )
        assert sv.value == "B-20240115-001"
        assert isinstance(sv.value, str)

    def test_quality_custom(self) -> None:
        sv = SignalValue(
            signal_id="s1", value=0.0, timestamp=0.0, quality="uncertain"
        )
        assert sv.quality == "uncertain"

    def test_quality_bad(self) -> None:
        sv = SignalValue(signal_id="s1", value=0.0, timestamp=0.0, quality="bad")
        assert sv.quality == "bad"


# ---------------------------------------------------------------------------
# SignalStore construction
# ---------------------------------------------------------------------------


class TestStoreConstruction:
    def test_empty_store(self) -> None:
        store = SignalStore()
        assert len(store) == 0
        assert store.get("nonexistent") is None

    def test_quality_flags_constant(self) -> None:
        assert {"good", "uncertain", "bad"} == QUALITY_FLAGS


# ---------------------------------------------------------------------------
# set / get round-trip
# ---------------------------------------------------------------------------


class TestSetGet:
    def test_set_and_get_float(self) -> None:
        store = SignalStore()
        store.set("press.line_speed", 150.0, timestamp=1.0)
        sv = store.get("press.line_speed")
        assert sv is not None
        assert sv.signal_id == "press.line_speed"
        assert sv.value == 150.0
        assert sv.timestamp == 1.0
        assert sv.quality == "good"

    def test_set_and_get_string(self) -> None:
        store = SignalStore()
        store.set("mixer.batch_id", "B-20240115-001", timestamp=5.0)
        sv = store.get("mixer.batch_id")
        assert sv is not None
        assert sv.value == "B-20240115-001"
        assert isinstance(sv.value, str)

    def test_set_preserves_quality(self) -> None:
        store = SignalStore()
        store.set("s1", 42.0, timestamp=1.0, quality="uncertain")
        sv = store.get("s1")
        assert sv is not None
        assert sv.quality == "uncertain"

    def test_set_bad_quality(self) -> None:
        store = SignalStore()
        store.set("s1", 0.0, timestamp=0.0, quality="bad")
        sv = store.get("s1")
        assert sv is not None
        assert sv.quality == "bad"

    def test_get_missing_returns_none(self) -> None:
        store = SignalStore()
        assert store.get("does_not_exist") is None

    def test_set_invalid_quality_rejected(self) -> None:
        store = SignalStore()
        with pytest.raises(ValueError, match="quality must be one of"):
            store.set("s1", 1.0, timestamp=0.0, quality="invalid")

    def test_set_empty_quality_rejected(self) -> None:
        store = SignalStore()
        with pytest.raises(ValueError, match="quality must be one of"):
            store.set("s1", 1.0, timestamp=0.0, quality="")

    def test_set_zero_value(self) -> None:
        store = SignalStore()
        store.set("s1", 0.0, timestamp=0.0)
        sv = store.get("s1")
        assert sv is not None
        assert sv.value == 0.0

    def test_set_negative_value(self) -> None:
        store = SignalStore()
        store.set("s1", -99.5, timestamp=0.0)
        sv = store.get("s1")
        assert sv is not None
        assert sv.value == -99.5


# ---------------------------------------------------------------------------
# Update in place
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_set_updates_existing(self) -> None:
        store = SignalStore()
        store.set("s1", 10.0, timestamp=1.0)
        store.set("s1", 20.0, timestamp=2.0)
        assert len(store) == 1  # no duplicates
        sv = store.get("s1")
        assert sv is not None
        assert sv.value == 20.0
        assert sv.timestamp == 2.0

    def test_update_changes_quality(self) -> None:
        store = SignalStore()
        store.set("s1", 10.0, timestamp=1.0, quality="good")
        store.set("s1", 10.0, timestamp=2.0, quality="bad")
        sv = store.get("s1")
        assert sv is not None
        assert sv.quality == "bad"

    def test_update_float_to_string(self) -> None:
        """Value type can change across updates (unlikely but valid)."""
        store = SignalStore()
        store.set("s1", 10.0, timestamp=1.0)
        store.set("s1", "hello", timestamp=2.0)
        sv = store.get("s1")
        assert sv is not None
        assert sv.value == "hello"

    def test_identity_preserved_on_update(self) -> None:
        """Same SignalValue object is reused (no extra allocation)."""
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        sv1 = store.get("s1")
        store.set("s1", 2.0, timestamp=1.0)
        sv2 = store.get("s1")
        assert sv1 is sv2  # same object mutated in place


# ---------------------------------------------------------------------------
# get_value
# ---------------------------------------------------------------------------


class TestGetValue:
    def test_get_value_returns_value(self) -> None:
        store = SignalStore()
        store.set("s1", 42.0, timestamp=1.0)
        assert store.get_value("s1") == 42.0

    def test_get_value_missing_returns_default(self) -> None:
        store = SignalStore()
        assert store.get_value("missing") == 0.0

    def test_get_value_custom_default(self) -> None:
        store = SignalStore()
        assert store.get_value("missing", default=-1.0) == -1.0

    def test_get_value_string_default(self) -> None:
        store = SignalStore()
        assert store.get_value("missing", default="N/A") == "N/A"

    def test_get_value_string_signal(self) -> None:
        store = SignalStore()
        store.set("batch", "B-001", timestamp=0.0)
        assert store.get_value("batch") == "B-001"


# ---------------------------------------------------------------------------
# get_all
# ---------------------------------------------------------------------------


class TestGetAll:
    def test_get_all_empty(self) -> None:
        store = SignalStore()
        assert store.get_all() == {}

    def test_get_all_returns_all(self) -> None:
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        store.set("s2", 2.0, timestamp=0.0)
        store.set("s3", 3.0, timestamp=0.0)
        all_signals = store.get_all()
        assert len(all_signals) == 3
        assert set(all_signals.keys()) == {"s1", "s2", "s3"}

    def test_get_all_reflects_updates(self) -> None:
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        store.set("s1", 99.0, timestamp=1.0)
        all_signals = store.get_all()
        assert all_signals["s1"].value == 99.0

    def test_get_all_not_mutable(self) -> None:
        """get_all() returns a read-only view; mutation raises TypeError."""
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        view = store.get_all()
        with pytest.raises(TypeError):
            view["s2"] = SignalValue(signal_id="s2", value=2.0, timestamp=0.0)  # type: ignore[index]
        with pytest.raises(TypeError):
            del view["s1"]  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# signal_ids
# ---------------------------------------------------------------------------


class TestSignalIds:
    def test_signal_ids_empty(self) -> None:
        store = SignalStore()
        assert store.signal_ids() == []

    def test_signal_ids_sorted(self) -> None:
        store = SignalStore()
        store.set("z_signal", 0.0, timestamp=0.0)
        store.set("a_signal", 0.0, timestamp=0.0)
        store.set("m_signal", 0.0, timestamp=0.0)
        assert store.signal_ids() == ["a_signal", "m_signal", "z_signal"]


# ---------------------------------------------------------------------------
# Container protocol (__len__, __contains__, __iter__)
# ---------------------------------------------------------------------------


class TestContainerProtocol:
    def test_len_empty(self) -> None:
        store = SignalStore()
        assert len(store) == 0

    def test_len_after_sets(self) -> None:
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        store.set("s2", 2.0, timestamp=0.0)
        assert len(store) == 2

    def test_len_no_duplicates(self) -> None:
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        store.set("s1", 2.0, timestamp=1.0)
        assert len(store) == 1

    def test_contains_present(self) -> None:
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        assert "s1" in store

    def test_contains_absent(self) -> None:
        store = SignalStore()
        assert "s1" not in store

    def test_iter(self) -> None:
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        store.set("s2", 2.0, timestamp=0.0)
        ids = list(store)
        assert set(ids) == {"s1", "s2"}


# ---------------------------------------------------------------------------
# clear
# ---------------------------------------------------------------------------


class TestClear:
    def test_clear_empties_store(self) -> None:
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        store.set("s2", 2.0, timestamp=0.0)
        assert len(store) == 2
        store.clear()
        assert len(store) == 0
        assert store.get("s1") is None

    def test_usable_after_clear(self) -> None:
        store = SignalStore()
        store.set("s1", 1.0, timestamp=0.0)
        store.clear()
        store.set("s1", 99.0, timestamp=1.0)
        sv = store.get("s1")
        assert sv is not None
        assert sv.value == 99.0


# ---------------------------------------------------------------------------
# Many signals (realistic scale)
# ---------------------------------------------------------------------------


class TestRealisticScale:
    def test_48_packaging_signals(self) -> None:
        """Store handles the packaging profile's 48 signals."""
        store = SignalStore()
        for i in range(48):
            store.set(f"signal_{i:02d}", float(i), timestamp=0.1 * i)
        assert len(store) == 48
        # Spot check first and last
        assert store.get("signal_00") is not None
        assert store.get("signal_00").value == 0.0  # type: ignore[union-attr]
        assert store.get("signal_47") is not None
        assert store.get("signal_47").value == 47.0  # type: ignore[union-attr]

    def test_68_fnb_signals(self) -> None:
        """Store handles the F&B profile's 68 signals."""
        store = SignalStore()
        for i in range(68):
            store.set(f"fnb.signal_{i:02d}", float(i) * 0.5, timestamp=float(i))
        assert len(store) == 68

    def test_rapid_updates(self) -> None:
        """Simulate many ticks updating all signals."""
        store = SignalStore()
        n_signals = 47
        n_ticks = 1000
        for sig_idx in range(n_signals):
            store.set(f"s{sig_idx}", 0.0, timestamp=0.0)

        for tick in range(1, n_ticks + 1):
            ts = tick * 0.1
            for sig_idx in range(n_signals):
                store.set(f"s{sig_idx}", float(tick + sig_idx), timestamp=ts)

        # After 1000 ticks, check last values
        assert len(store) == n_signals
        sv = store.get("s0")
        assert sv is not None
        assert sv.value == 1000.0
        assert sv.timestamp == 100.0

        sv_last = store.get(f"s{n_signals - 1}")
        assert sv_last is not None
        assert sv_last.value == float(n_ticks + n_signals - 1)
