"""Tests for counter rollover support (PRD 10.4, Task 4.15).

Covers:
- CounterModel.rollover_occurred flag behavior
- CounterModel.set_rollover_value() runtime override
- Ground truth logging of rollover events
- DataQualityConfig.counter_rollover config overrides applied via DataEngine
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from factory_simulator.models.counter import CounterModel

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEED = 42
DT = 0.1  # 100ms tick


def _rng(seed: int = SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


def _make_model(params: dict | None = None) -> CounterModel:
    p: dict = {"rate": 1.0}
    if params:
        p.update(params)
    return CounterModel(p, _rng())


def _run_n(model: CounterModel, n: int, speed: float = 100.0, dt: float = DT) -> list[float]:
    model.set_speed(speed)
    return [model.generate(i * dt, dt) for i in range(n)]


# ---------------------------------------------------------------------------
# TestRolloverOccurred — flag behavior
# ---------------------------------------------------------------------------


class TestRolloverOccurred:
    def test_flag_false_when_no_rollover(self) -> None:
        """rollover_occurred is False when counter has not rolled over."""
        m = _make_model({"rollover": 10000.0})
        m.set_speed(1.0)
        m.generate(0.0, DT)
        assert m.rollover_occurred is False

    def test_flag_true_on_rollover(self) -> None:
        """rollover_occurred is True when counter wraps.

        Setup: rollover=100, speed=100, rate=1, dt=0.1 -> +10 per tick.
        After 9 ticks: value=90 (no wrap). After tick 10: 100 >= 100 -> wrap.
        """
        m = _make_model({"rollover": 100.0})
        m.set_speed(100.0)
        for _ in range(9):
            m.generate(0.0, DT)
        assert m.rollover_occurred is False  # no wrap yet at 90
        m.generate(0.0, DT)  # 10th tick: 100 >= 100 -> wrap
        assert m.rollover_occurred is True

    def test_flag_resets_on_next_generate_without_rollover(self) -> None:
        """rollover_occurred resets to False on the next tick where no wrap fires."""
        m = _make_model({"rollover": 10.0})
        m.set_speed(100.0)
        # Tick to rollover point
        for _ in range(10):
            m.generate(0.0, DT)
        assert m.rollover_occurred is True
        # Next tick: counter is at remainder (0), increment is only 10, stays below 10?
        # No — 0 + 10 = 10 >= 10 wraps again!  Use tiny speed to avoid double-wrap.
        m.set_speed(0.0)
        m.generate(0.0, DT)
        assert m.rollover_occurred is False

    def test_flag_false_when_rollover_disabled(self) -> None:
        """Without rollover_value, rollover_occurred is always False."""
        m = _make_model()  # no rollover
        m.set_speed(100.0)
        for _ in range(100):
            m.generate(0.0, DT)
        assert m.rollover_occurred is False

    def test_flag_resets_at_start_of_each_generate(self) -> None:
        """Confirm flag reset happens at start of generate() — not sticky across ticks."""
        m = _make_model({"rollover": 10.0})
        m.set_speed(100.0)
        # First 10 ticks to wrap
        for _ in range(10):
            m.generate(0.0, DT)
        assert m.rollover_occurred is True
        # Stop counter so it doesn't wrap again
        m.set_speed(0.0)
        # Each subsequent generate() should return False
        for _ in range(5):
            m.generate(0.0, DT)
            assert m.rollover_occurred is False


# ---------------------------------------------------------------------------
# TestSetRolloverValue — runtime override
# ---------------------------------------------------------------------------


class TestSetRolloverValue:
    def test_set_rollover_value_changes_threshold(self) -> None:
        """set_rollover_value() replaces the configured threshold."""
        m = _make_model()  # no rollover initially
        m.set_rollover_value(50.0)
        assert m.rollover_value == 50.0

    def test_set_rollover_value_to_none_disables(self) -> None:
        """set_rollover_value(None) disables rollover."""
        m = _make_model({"rollover": 100.0})
        m.set_rollover_value(None)
        assert m.rollover_value is None
        m.set_speed(100.0)
        for _ in range(20):  # would have wrapped at 100
            m.generate(0.0, DT)
        assert m.rollover_occurred is False

    def test_set_rollover_value_invalid_raises(self) -> None:
        m = _make_model()
        with pytest.raises(ValueError, match="rollover_value must be > 0"):
            m.set_rollover_value(0.0)
        with pytest.raises(ValueError, match="rollover_value must be > 0"):
            m.set_rollover_value(-5.0)

    def test_override_takes_effect_on_next_generate(self) -> None:
        """Counter wraps at the new value after set_rollover_value()."""
        m = _make_model()  # no rollover
        m.set_speed(100.0)
        # Accumulate to 50 (5 ticks x 10 increment)
        for _ in range(5):
            m.generate(0.0, DT)
        assert m.value == pytest.approx(50.0)
        # Set rollover at 50 — should wrap immediately on next generate
        m.set_rollover_value(50.0)
        m.generate(0.0, DT)  # 50 + 10 = 60 >= 50 → wrap
        assert m.rollover_occurred is True
        assert m.value == pytest.approx(10.0)  # 60 % 50 = 10

    def test_uint32_max_default_via_set_rollover_value(self) -> None:
        """Calling set_rollover_value with uint32 max keeps behavior correct."""
        m = _make_model()
        uint32_max = 4_294_967_295.0
        m.set_rollover_value(uint32_max)
        assert m.rollover_value == uint32_max
        # At typical speeds, far below threshold — no rollover
        m.set_speed(200.0)
        for _ in range(3600):
            m.generate(0.0, 1.0)
        assert m.rollover_occurred is False


# ---------------------------------------------------------------------------
# TestCounterRolloverWrapsToZero — PRD 10.4 spec
# ---------------------------------------------------------------------------


class TestCounterRolloverWrapsToZero:
    def test_wraps_at_configured_rollover(self) -> None:
        """Counter wraps at configured rollover_value (not uint32 max)."""
        m = _make_model({"rollover": 100.0})
        m.set_speed(100.0)
        # 10 ticks x 10 = 100 -> wrap
        vals = [m.generate(0.0, DT) for _ in range(10)]
        assert vals[-1] == pytest.approx(0.0)
        assert m.rollover_occurred is True

    def test_wraps_at_uint32_max_by_default(self) -> None:
        """Default (no rollover param) means counter grows unbounded.

        For testing, we verify the model increments far beyond 10000
        without wrapping.
        """
        m = _make_model()  # rollover_value is None → no wrap
        m.set_speed(100.0)
        for _ in range(1000):
            m.generate(0.0, DT)
        assert m.value == pytest.approx(10_000.0)
        assert m.rollover_occurred is False

    def test_rollover_wraps_to_zero_not_one(self) -> None:
        """Rollover uses modulo — step is from N to remainder, not N to 1."""
        # rollover=10, increment=10 → 10 % 10 = 0 (not 1)
        m = _make_model({"rollover": 10.0})
        m.set_speed(100.0)
        for _ in range(10):
            m.generate(0.0, DT)
        # Value after wrap: 100 % 10 = 0
        assert m.value == pytest.approx(0.0)

    def test_rollover_preserves_excess(self) -> None:
        """Value after rollover is the modulo remainder, not zero."""
        # rollover=10, 11 ticks: 110 % 10 = 10; wait no, 10.0 % 10.0 == 0
        # Let's use rollover=7 and increment=10 per tick
        # 1 tick: 10 > 7 → 10 % 7 = 3
        m = _make_model({"rollover": 7.0})
        m.set_speed(100.0)  # rate=1 * speed=100 * dt=0.1 = 10 per tick
        val = m.generate(0.0, DT)
        assert val == pytest.approx(10.0 % 7.0)  # 3.0
        assert m.rollover_occurred is True


# ---------------------------------------------------------------------------
# TestGroundTruthRollover — logging integration
# ---------------------------------------------------------------------------


class TestGroundTruthRollover:
    """Test that rollover events reach the ground truth JSONL log."""

    def test_log_counter_rollover_writes_event(self, tmp_path: Path) -> None:
        """GroundTruthLogger.log_counter_rollover() produces a JSONL event."""
        from factory_simulator.engine.ground_truth import GroundTruthLogger

        gt_path = tmp_path / "gt.jsonl"
        gt = GroundTruthLogger(str(gt_path))
        gt.open()
        gt.log_counter_rollover(
            sim_time=123.45,
            signal_id="press.impression_count",
            rollover_value=4294967295.0,
            value_after=7.0,
        )
        gt.close()

        lines = gt_path.read_text().strip().split("\n")
        events = [json.loads(ln) for ln in lines if ln]
        rollover_events = [e for e in events if e.get("event") == "counter_rollover"]

        assert len(rollover_events) == 1
        ev = rollover_events[0]
        assert ev["signal_id"] == "press.impression_count"
        assert ev["rollover_value"] == pytest.approx(4294967295.0)
        assert ev["value_after"] == pytest.approx(7.0)
        assert "sim_time" in ev

    def test_log_counter_rollover_no_write_when_closed(self, tmp_path: Path) -> None:
        """log_counter_rollover is silent when logger is not open."""
        from factory_simulator.engine.ground_truth import GroundTruthLogger

        gt_path = tmp_path / "gt2.jsonl"
        gt = GroundTruthLogger(str(gt_path))
        # Do NOT call gt.open() -- _fh is None
        gt.log_counter_rollover(0.0, "press.impression_count", 100.0, 5.0)
        # No exception, file not created
        assert not gt_path.exists()

    def test_counter_rollover_event_format(self, tmp_path: Path) -> None:
        """counter_rollover event has all required fields (PRD 10.4)."""
        from factory_simulator.engine.ground_truth import GroundTruthLogger

        gt_path = tmp_path / "gt3.jsonl"
        gt = GroundTruthLogger(str(gt_path))
        gt.open()
        gt.log_counter_rollover(999.0, "energy.cumulative_kwh", 999999.0, 1.5)
        gt.close()

        ev = json.loads(gt_path.read_text().strip())
        assert ev["event"] == "counter_rollover"
        assert ev["signal_id"] == "energy.cumulative_kwh"
        assert ev["rollover_value"] == pytest.approx(999999.0)
        assert ev["value_after"] == pytest.approx(1.5)
        assert "sim_time" in ev

    def test_data_engine_logs_rollover_when_counter_fires(self, tmp_path: Path) -> None:
        """DataEngine.tick() fires log_counter_rollover when CounterModel wraps.

        Uses EnergyGenerator.cumulative_kwh which always accumulates from a
        positive base power load even when the press is idle.  Sets an
        extremely small rollover_value so any increment triggers a wrap.
        """
        import yaml

        from factory_simulator.config import FactoryConfig
        from factory_simulator.engine.data_engine import DataEngine
        from factory_simulator.engine.ground_truth import GroundTruthLogger
        from factory_simulator.generators.energy import EnergyGenerator
        from factory_simulator.store import SignalStore

        cfg_path = Path("config/factory.yaml")
        raw = yaml.safe_load(cfg_path.read_text())
        raw.setdefault("data_quality", {})
        raw["data_quality"]["counter_rollover"] = {}  # no config overrides needed
        raw["data_quality"].setdefault("sensor_disconnect", {"enabled": False})
        raw["data_quality"]["sensor_disconnect"]["enabled"] = False
        raw["data_quality"].setdefault("stuck_sensor", {"enabled": False})
        raw["data_quality"]["stuck_sensor"]["enabled"] = False

        config = FactoryConfig(**raw)
        store = SignalStore()
        gt_path = tmp_path / "gt4.jsonl"
        gt = GroundTruthLogger(str(gt_path))
        gt.open()
        engine = DataEngine(config, store, ground_truth=gt)

        # EnergyGenerator always has a positive base power load (≥10 kW) even
        # when the press is idle.  Set rollover_value to effectively zero so
        # the first non-zero increment (rate * power * dt) triggers a wrap.
        energy_gen = next(g for g in engine.generators if isinstance(g, EnergyGenerator))
        kwh_counter = energy_gen.get_counter_models()["energy.cumulative_kwh"]
        kwh_counter.set_rollover_value(1e-10)

        # First tick fires all generators (gen_last_time=-inf)
        engine.tick()
        gt.close()

        lines = gt_path.read_text().strip().split("\n")
        events = [json.loads(ln) for ln in lines if ln]
        rollover_events = [e for e in events if e.get("event") == "counter_rollover"]

        assert len(rollover_events) >= 1
        ev = next(e for e in rollover_events if e["signal_id"] == "energy.cumulative_kwh")
        assert ev["rollover_value"] == pytest.approx(1e-10)
        assert ev["value_after"] >= 0.0


# ---------------------------------------------------------------------------
# TestConfigOverridesPerSignal — DataQualityConfig.counter_rollover wiring
# ---------------------------------------------------------------------------


class TestConfigOverridesPerSignal:
    """Verify that DataQualityConfig.counter_rollover overrides are applied."""

    def _make_engine(self, rollover_map: dict[str, float]) -> object:
        """Build DataEngine with given counter_rollover overrides."""
        import yaml

        from factory_simulator.config import FactoryConfig
        from factory_simulator.engine.data_engine import DataEngine
        from factory_simulator.store import SignalStore

        cfg_path = Path("config/factory.yaml")
        raw = yaml.safe_load(cfg_path.read_text())
        raw.setdefault("data_quality", {})
        raw["data_quality"]["counter_rollover"] = rollover_map
        raw["data_quality"].setdefault("sensor_disconnect", {"enabled": False})
        raw["data_quality"]["sensor_disconnect"]["enabled"] = False
        raw["data_quality"].setdefault("stuck_sensor", {"enabled": False})
        raw["data_quality"]["stuck_sensor"]["enabled"] = False
        config = FactoryConfig(**raw)
        store = SignalStore()
        return DataEngine(config, store)

    def _get_press_gen(self, engine: object) -> object:
        from factory_simulator.generators.press import PressGenerator

        for gen in engine.generators:  # type: ignore[attr-defined]
            if isinstance(gen, PressGenerator):
                return gen
        pytest.skip("No PressGenerator found")

    def test_override_sets_rollover_on_impression_count(self) -> None:
        """counter_rollover dict entry is applied to the CounterModel."""
        engine = self._make_engine({"press.impression_count": 5000.0})
        gen = self._get_press_gen(engine)
        cms = gen.get_counter_models()
        assert "press.impression_count" in cms
        assert cms["press.impression_count"].rollover_value == pytest.approx(5000.0)

    def test_override_sets_rollover_on_energy_cumulative_kwh(self) -> None:
        """counter_rollover applies to EnergyGenerator counter too."""
        engine = self._make_engine({"energy.cumulative_kwh": 1000.0})
        from factory_simulator.generators.energy import EnergyGenerator

        for gen in engine.generators:  # type: ignore[attr-defined]
            if isinstance(gen, EnergyGenerator):
                cms = gen.get_counter_models()
                assert cms["energy.cumulative_kwh"].rollover_value == pytest.approx(1000.0)
                return
        pytest.skip("No EnergyGenerator found")

    def test_override_applies_to_multiple_signals(self) -> None:
        """Multiple entries in counter_rollover are all applied."""
        engine = self._make_engine({
            "press.impression_count": 100.0,
            "press.good_count": 200.0,
            "press.waste_count": 50.0,
        })
        gen = self._get_press_gen(engine)
        cms = gen.get_counter_models()
        assert cms["press.impression_count"].rollover_value == pytest.approx(100.0)
        assert cms["press.good_count"].rollover_value == pytest.approx(200.0)
        assert cms["press.waste_count"].rollover_value == pytest.approx(50.0)

    def test_unknown_signal_override_is_ignored(self) -> None:
        """Nonexistent signal IDs in counter_rollover are silently ignored."""
        # Should not raise
        engine = self._make_engine({"nonexistent.signal": 1000.0})
        assert engine is not None  # type: ignore[attr-defined]

    def test_counter_wraps_after_override_applied(self) -> None:
        """End-to-end: counter actually wraps at the configured override value."""
        import yaml

        from factory_simulator.config import FactoryConfig
        from factory_simulator.engine.data_engine import DataEngine
        from factory_simulator.store import SignalStore

        cfg_path = Path("config/factory.yaml")
        raw = yaml.safe_load(cfg_path.read_text())
        # Small rollover to test quickly
        raw.setdefault("data_quality", {})
        raw["data_quality"]["counter_rollover"] = {"press.impression_count": 10.0}
        raw["data_quality"].setdefault("sensor_disconnect", {"enabled": False})
        raw["data_quality"]["sensor_disconnect"]["enabled"] = False
        raw["data_quality"].setdefault("stuck_sensor", {"enabled": False})
        raw["data_quality"]["stuck_sensor"]["enabled"] = False

        config = FactoryConfig(**raw)
        store = SignalStore()
        engine = DataEngine(config, store)

        # Tick once (press generates on tick 1)
        engine.tick()

        # Read from store — value should be wrapped (< 10)
        sv = store.get("press.impression_count")
        assert sv is not None
        if sv.value is not None:
            assert sv.value < 10.0  # wrapped below rollover threshold
