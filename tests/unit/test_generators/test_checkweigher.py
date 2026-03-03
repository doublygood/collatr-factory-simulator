"""Unit tests for the CheckweigherGenerator (PRD 2b.6).

Tests verify:
- All 6 signals are produced with correct IDs
- actual_weight mirrors filler.fill_weight + tray_weight offset
- actual_weight only updates on item arrivals (per-item generation)
- overweight_count increments when actual > fill_target + tray + threshold
- underweight_count increments when actual < fill_target + tray - threshold
- metal_detect_trips is rare (Bernoulli per item)
- reject_total accumulates from all reject types
- throughput mirrors filler.line_speed with optional noise
- No increments when filler is not Running
- No increments when line_speed is zero
- Graceful handling of missing store signals
- Determinism (same seed → same output)

Task 3.8
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.checkweigher import (
    _DEFAULT_METAL_DETECT_PROB,
    _DEFAULT_OVERWEIGHT_THRESHOLD_G,
    _DEFAULT_TRAY_WEIGHT_G,
    _DEFAULT_UNDERWEIGHT_THRESHOLD_G,
    CheckweigherGenerator,
)
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FILL_TARGET = 400.0
_TRAY_WEIGHT = 10.0
_OWT = 30.0   # overweight threshold
_UWT = 15.0   # underweight threshold


def _make_qc_config(
    *,
    tray_weight_g: float = _TRAY_WEIGHT,
    overweight_threshold_g: float = _OWT,
    underweight_threshold_g: float = _UWT,
    metal_detect_prob: float = _DEFAULT_METAL_DETECT_PROB,
    actual_weight_target: float = 410.0,
    actual_weight_noise: float = 0.5,
    throughput_noise: float = 0.3,
) -> EquipmentConfig:
    """Create a minimal checkweigher config for testing."""
    signals: dict[str, SignalConfig] = {}

    signals["actual_weight"] = SignalConfig(
        model="steady_state",
        noise_sigma=actual_weight_noise,
        sample_rate_ms=1000,
        min_clamp=100.0,
        max_clamp=1000.0,
        units="g",
        opcua_node="FoodBevLine.QC1.ActualWeight",
        opcua_type="Double",
        params={"target": actual_weight_target},
    )
    signals["overweight_count"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=9999.0,
        units="count",
        opcua_node="FoodBevLine.QC1.OverweightCount",
        opcua_type="UInt32",
        params={"rate": 0.001, "rollover": 9999},
    )
    signals["underweight_count"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=9999.0,
        units="count",
        opcua_node="FoodBevLine.QC1.UnderweightCount",
        opcua_type="UInt32",
        params={"rate": 0.0005, "rollover": 9999},
    )
    signals["metal_detect_trips"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=99.0,
        units="count",
        opcua_node="FoodBevLine.QC1.MetalDetectTrips",
        opcua_type="UInt32",
        params={"rate": 0.0001, "rollover": 99},
    )
    signals["throughput"] = SignalConfig(
        model="correlated_follower",
        noise_sigma=throughput_noise,
        sample_rate_ms=1000,
        min_clamp=10.0,
        max_clamp=120.0,
        units="items/min",
        opcua_node="FoodBevLine.QC1.Throughput",
        opcua_type="Double",
        params={"base": 0.0, "factor": 1.0},
    )
    signals["reject_total"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=9999.0,
        units="count",
        opcua_node="FoodBevLine.QC1.RejectTotal",
        opcua_type="UInt32",
        params={"rate": 0.002, "rollover": 9999},
    )

    return EquipmentConfig(
        enabled=True,
        type="checkweigher",
        signals=signals,
        tray_weight_g=tray_weight_g,
        overweight_threshold_g=overweight_threshold_g,
        underweight_threshold_g=underweight_threshold_g,
        metal_detect_prob=metal_detect_prob,
    )


def _make_store(
    filler_state: float = 2.0,
    filler_line_speed: float = 60.0,
    filler_fill_weight: float = 405.0,
    filler_fill_target: float = 400.0,
) -> SignalStore:
    """Create a store with typical filler signal values."""
    store = SignalStore()
    store.set("filler.state", filler_state, 0.0, "good")
    store.set("filler.line_speed", filler_line_speed, 0.0, "good")
    store.set("filler.fill_weight", filler_fill_weight, 0.0, "good")
    store.set("filler.fill_target", filler_fill_target, 0.0, "good")
    return store


def _make_qc(seed: int = 42) -> CheckweigherGenerator:
    rng = np.random.default_rng(seed)
    return CheckweigherGenerator("qc", _make_qc_config(), rng)


def _tick(
    gen: CheckweigherGenerator,
    store: SignalStore,
    n: int = 1,
    dt: float = 0.1,
) -> list[SignalValue]:
    """Run n ticks and return results from the last tick."""
    results: list[SignalValue] = []
    for i in range(n):
        results = gen.generate(float(i) * dt, dt, store)
    return results


def _sv_dict(results: list[SignalValue]) -> dict[str, float]:
    return {sv.signal_id: float(sv.value) for sv in results}


# ---------------------------------------------------------------------------
# Signal identity
# ---------------------------------------------------------------------------


def test_signal_count():
    gen = _make_qc()
    store = _make_store()
    results = gen.generate(0.0, 0.1, store)
    assert len(results) == 6


def test_signal_ids():
    gen = _make_qc()
    ids = gen.get_signal_ids()
    assert set(ids) == {
        "qc.actual_weight",
        "qc.overweight_count",
        "qc.underweight_count",
        "qc.metal_detect_trips",
        "qc.throughput",
        "qc.reject_total",
    }


def test_signal_ids_match_generate():
    gen = _make_qc()
    store = _make_store()
    results = gen.generate(0.0, 0.1, store)
    produced_ids = {sv.signal_id for sv in results}
    assert produced_ids == set(gen.get_signal_ids())


# ---------------------------------------------------------------------------
# actual_weight: per-item, mirrors fill_weight + tray
# ---------------------------------------------------------------------------


def test_actual_weight_mirrors_fill_weight_plus_tray():
    """actual_weight should be close to filler.fill_weight + tray_weight."""
    # Use zero noise to verify the offset precisely
    cfg = _make_qc_config(actual_weight_noise=0.0)
    rng = np.random.default_rng(1)
    gen = CheckweigherGenerator("qc", cfg, rng)

    fill_weight = 405.0
    store = _make_store(
        filler_state=2.0,
        filler_line_speed=60.0,  # 1 pack/s, item_interval = 1.0 s
        filler_fill_weight=fill_weight,
    )

    # dt=1.1 s triggers an item arrival (item_interval=1.0 s)
    results = gen.generate(0.0, 1.1, store)
    values = _sv_dict(results)
    assert values["qc.actual_weight"] == pytest.approx(
        fill_weight + _TRAY_WEIGHT, abs=0.01
    )


def test_actual_weight_held_between_items():
    """actual_weight holds its last value between item arrivals."""
    cfg = _make_qc_config(actual_weight_noise=0.0)
    rng = np.random.default_rng(2)
    gen = CheckweigherGenerator("qc", cfg, rng)
    initial_weight = gen.last_actual_weight

    store = _make_store(
        filler_state=2.0,
        filler_line_speed=60.0,  # item_interval = 1.0 s
        filler_fill_weight=405.0,
    )

    # Short dt (0.1s) -- not enough for an item to arrive
    results = gen.generate(0.0, 0.1, store)
    values = _sv_dict(results)
    # Should still be the initial default
    assert values["qc.actual_weight"] == pytest.approx(initial_weight, abs=0.01)


def test_actual_weight_respects_clamp():
    """actual_weight should stay within min/max bounds."""
    gen = _make_qc()
    store = _make_store(filler_fill_weight=405.0)
    for i in range(200):
        results = gen.generate(float(i) * 0.1, 0.1, store)
    values = _sv_dict(results)
    assert 100.0 <= values["qc.actual_weight"] <= 1000.0


# ---------------------------------------------------------------------------
# Overweight counter
# ---------------------------------------------------------------------------


def test_overweight_count_increments_on_heavy_item():
    """overweight_count increments when actual > fill_target + tray + threshold."""
    cfg = _make_qc_config(actual_weight_noise=0.0, overweight_threshold_g=_OWT)
    rng = np.random.default_rng(3)
    gen = CheckweigherGenerator("qc", cfg, rng)

    # fill_weight = fill_target + tray + overweight_threshold + 1 → definitely overweight
    heavy_fill = _FILL_TARGET + _OWT + 1.0   # actual = 445 + tray, limit = 440
    store = _make_store(
        filler_fill_weight=heavy_fill,
        filler_fill_target=_FILL_TARGET,
        filler_line_speed=60.0,
    )
    # Trigger one item (dt > 1.0 s)
    gen.generate(0.0, 1.1, store)
    assert gen.overweight_count == 1.0


def test_overweight_does_not_increment_on_normal_item():
    """overweight_count does not increment for a normal-weight item."""
    cfg = _make_qc_config(actual_weight_noise=0.0)
    rng = np.random.default_rng(4)
    gen = CheckweigherGenerator("qc", cfg, rng)

    # Normal weight: fill_target + small giveaway
    normal_fill = _FILL_TARGET + 5.0   # actual = 415, limit = 440 → within range
    store = _make_store(
        filler_fill_weight=normal_fill,
        filler_fill_target=_FILL_TARGET,
        filler_line_speed=60.0,
    )
    gen.generate(0.0, 1.1, store)
    assert gen.overweight_count == 0.0


# ---------------------------------------------------------------------------
# Underweight counter
# ---------------------------------------------------------------------------


def test_underweight_count_increments_on_light_item():
    """underweight_count increments when actual < fill_target + tray - threshold."""
    cfg = _make_qc_config(actual_weight_noise=0.0, underweight_threshold_g=_UWT)
    rng = np.random.default_rng(5)
    gen = CheckweigherGenerator("qc", cfg, rng)

    # fill_weight below underweight limit: fill_target - threshold - 1
    # underweight_limit = fill_target + tray - threshold = 400 + 10 - 15 = 395
    # So fill_weight must be < 395 - tray = 385
    light_fill = _FILL_TARGET - _UWT - 2.0   # actual = 393, limit = 395 → underweight
    store = _make_store(
        filler_fill_weight=light_fill,
        filler_fill_target=_FILL_TARGET,
        filler_line_speed=60.0,
    )
    gen.generate(0.0, 1.1, store)
    assert gen.underweight_count == 1.0


def test_underweight_does_not_increment_on_normal_item():
    """underweight_count does not increment for a normal-weight item."""
    cfg = _make_qc_config(actual_weight_noise=0.0)
    rng = np.random.default_rng(6)
    gen = CheckweigherGenerator("qc", cfg, rng)

    normal_fill = _FILL_TARGET + 5.0
    store = _make_store(
        filler_fill_weight=normal_fill,
        filler_fill_target=_FILL_TARGET,
        filler_line_speed=60.0,
    )
    gen.generate(0.0, 1.1, store)
    assert gen.underweight_count == 0.0


# ---------------------------------------------------------------------------
# Metal detection: rare Bernoulli per item
# ---------------------------------------------------------------------------


def test_metal_detect_trips_rare():
    """metal_detect_trips should be very rare (< 5% of packs over 1000 items)."""
    # Use high probability to confirm logic works, then test with default
    cfg = _make_qc_config(actual_weight_noise=0.0)
    rng = np.random.default_rng(7)
    gen = CheckweigherGenerator("qc", cfg, rng)

    # Run 1000 items at 1 ppm (dt=1.1s each)
    store = _make_store(filler_line_speed=60.0)
    for i in range(1000):
        gen.generate(float(i) * 1.1, 1.1, store)

    # With default probability 0.001, expect ~1 trip per 1000 packs
    # Allow generous range to avoid flakiness
    assert gen.metal_detect_trips < 30.0  # extremely unlikely to exceed this


def test_metal_detect_trips_always_zero_with_zero_prob():
    """With metal_detect_prob=0.0, trips should never occur."""
    cfg = _make_qc_config(actual_weight_noise=0.0, metal_detect_prob=0.0)
    rng = np.random.default_rng(8)
    gen = CheckweigherGenerator("qc", cfg, rng)

    store = _make_store(filler_line_speed=60.0)
    for i in range(500):
        gen.generate(float(i) * 1.1, 1.1, store)

    assert gen.metal_detect_trips == 0.0


def test_metal_detect_trips_always_fire_with_prob_1():
    """With metal_detect_prob=1.0, every item triggers a trip."""
    cfg = _make_qc_config(actual_weight_noise=0.0, metal_detect_prob=1.0)
    rng = np.random.default_rng(9)
    gen = CheckweigherGenerator("qc", cfg, rng)

    # Run exactly 5 items
    store = _make_store(filler_line_speed=60.0)
    for i in range(5):
        gen.generate(float(i) * 1.1, 1.1, store)

    assert gen.metal_detect_trips == 5.0


# ---------------------------------------------------------------------------
# reject_total accumulates from all reject types
# ---------------------------------------------------------------------------


def test_reject_total_accumulates_overweight_and_metal():
    """reject_total should include both overweight and metal detect rejects."""
    cfg = _make_qc_config(
        actual_weight_noise=0.0,
        metal_detect_prob=1.0,  # always trigger metal
    )
    rng = np.random.default_rng(10)
    gen = CheckweigherGenerator("qc", cfg, rng)

    heavy_fill = _FILL_TARGET + _OWT + 1.0  # overweight
    store = _make_store(
        filler_fill_weight=heavy_fill,
        filler_fill_target=_FILL_TARGET,
        filler_line_speed=60.0,
    )
    # One item: overweight + metal detect → reject_total should be 2
    gen.generate(0.0, 1.1, store)
    assert gen.overweight_count == 1.0
    assert gen.metal_detect_trips == 1.0
    assert gen.reject_total == 2.0


def test_reject_total_accumulates_underweight():
    """reject_total should include underweight rejects."""
    cfg = _make_qc_config(actual_weight_noise=0.0, metal_detect_prob=0.0)
    rng = np.random.default_rng(11)
    gen = CheckweigherGenerator("qc", cfg, rng)

    light_fill = _FILL_TARGET - _UWT - 2.0
    store = _make_store(
        filler_fill_weight=light_fill,
        filler_fill_target=_FILL_TARGET,
        filler_line_speed=60.0,
    )
    gen.generate(0.0, 1.1, store)
    assert gen.underweight_count == 1.0
    assert gen.reject_total == 1.0


# ---------------------------------------------------------------------------
# Throughput mirrors filler.line_speed
# ---------------------------------------------------------------------------


def test_throughput_zero_when_filler_off():
    """throughput should be 0 when filler is not Running."""
    cfg = _make_qc_config(throughput_noise=0.0)
    rng = np.random.default_rng(12)
    gen = CheckweigherGenerator("qc", cfg, rng)

    store = _make_store(filler_state=0.0, filler_line_speed=60.0)
    results = gen.generate(0.0, 0.1, store)
    values = _sv_dict(results)
    assert values["qc.throughput"] == pytest.approx(0.0, abs=0.01)


def test_throughput_nonzero_when_filler_running():
    """throughput should mirror line_speed when filler is Running."""
    cfg = _make_qc_config(throughput_noise=0.0)
    rng = np.random.default_rng(13)
    gen = CheckweigherGenerator("qc", cfg, rng)

    store = _make_store(filler_state=2.0, filler_line_speed=60.0)
    results = gen.generate(0.0, 0.1, store)
    values = _sv_dict(results)
    # Without noise: should equal line_speed, clamped to [10, 120]
    assert values["qc.throughput"] == pytest.approx(60.0, abs=0.01)


def test_throughput_respects_clamp():
    """throughput should stay within [10, 120]."""
    gen = _make_qc()
    store = _make_store(filler_state=2.0, filler_line_speed=60.0)
    for i in range(200):
        results = gen.generate(float(i) * 0.1, 0.1, store)
    values = _sv_dict(results)
    assert 10.0 <= values["qc.throughput"] <= 120.0


# ---------------------------------------------------------------------------
# No increments when filler is not Running
# ---------------------------------------------------------------------------


def test_no_item_arrivals_when_filler_off():
    """No per-item updates should occur when filler.state != Running."""
    cfg = _make_qc_config(actual_weight_noise=0.0, metal_detect_prob=1.0)
    rng = np.random.default_rng(14)
    gen = CheckweigherGenerator("qc", cfg, rng)

    store = _make_store(filler_state=0.0, filler_line_speed=60.0)
    for i in range(100):
        gen.generate(float(i) * 0.1, 0.1, store)

    assert gen.overweight_count == 0.0
    assert gen.underweight_count == 0.0
    assert gen.metal_detect_trips == 0.0
    assert gen.reject_total == 0.0


def test_no_item_arrivals_when_speed_zero():
    """No per-item updates should occur when line_speed is 0."""
    cfg = _make_qc_config(actual_weight_noise=0.0, metal_detect_prob=1.0)
    rng = np.random.default_rng(15)
    gen = CheckweigherGenerator("qc", cfg, rng)

    store = _make_store(filler_state=2.0, filler_line_speed=0.0)
    for i in range(100):
        gen.generate(float(i) * 0.1, 0.1, store)

    assert gen.metal_detect_trips == 0.0


# ---------------------------------------------------------------------------
# Graceful handling of missing store signals
# ---------------------------------------------------------------------------


def test_empty_store_produces_valid_output():
    """Generator should not raise when filler signals are absent from store."""
    gen = _make_qc()
    store = SignalStore()  # empty store
    results = gen.generate(0.0, 0.1, store)
    assert len(results) == 6
    for sv in results:
        assert isinstance(sv.value, float)


def test_missing_fill_target_falls_back():
    """When filler.fill_target is absent, generator uses last actual weight as fallback."""
    cfg = _make_qc_config(actual_weight_noise=0.0)
    rng = np.random.default_rng(16)
    gen = CheckweigherGenerator("qc", cfg, rng)

    # Provide filler state/speed/weight but NOT fill_target
    store = SignalStore()
    store.set("filler.state", 2.0, 0.0, "good")
    store.set("filler.line_speed", 60.0, 0.0, "good")
    store.set("filler.fill_weight", 405.0, 0.0, "good")

    # Should not raise
    results = gen.generate(0.0, 1.1, store)
    assert len(results) == 6


# ---------------------------------------------------------------------------
# Multiple item arrivals in one tick
# ---------------------------------------------------------------------------


def test_item_timer_carries_remainder():
    """Item timer carry-over means exactly correct counts over time."""
    cfg = _make_qc_config(actual_weight_noise=0.0, metal_detect_prob=0.0)
    rng = np.random.default_rng(17)
    gen = CheckweigherGenerator("qc", cfg, rng)

    # 60 ppm → 1 item/s. Run for 10 ticks of 1.0 s each.
    # Use heavy fill so overweight always increments.
    heavy_fill = _FILL_TARGET + _OWT + 1.0
    store = _make_store(
        filler_fill_weight=heavy_fill,
        filler_fill_target=_FILL_TARGET,
        filler_line_speed=60.0,
    )
    for i in range(10):
        gen.generate(float(i), 1.0, store)

    # At 1 item/s, 10 ticks x 1s = 10 items (first tick: timer += 1.0 >= 1.0 -> 1 item)
    assert gen.overweight_count == 10.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism():
    """Same seed → same output sequence."""
    store = _make_store()

    def run_ticks(seed: int) -> list[float]:
        rng = np.random.default_rng(seed)
        gen = CheckweigherGenerator("qc", _make_qc_config(), rng)
        vals = []
        for i in range(30):
            results = gen.generate(float(i) * 0.1, 0.1, store)
            vals.extend(float(sv.value) for sv in results)
        return vals

    run1 = run_ticks(99)
    run2 = run_ticks(99)
    assert run1 == run2


def test_different_seeds_differ():
    """Different seeds → different outputs."""
    store = _make_store()

    def run_ticks(seed: int) -> list[float]:
        rng = np.random.default_rng(seed)
        gen = CheckweigherGenerator("qc", _make_qc_config(), rng)
        vals = []
        for i in range(30):
            results = gen.generate(float(i) * 0.1, 0.1, store)
            vals.extend(float(sv.value) for sv in results)
        return vals

    run1 = run_ticks(1)
    run2 = run_ticks(2)
    assert run1 != run2


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


def test_default_tray_weight():
    """CheckweigherGenerator uses _DEFAULT_TRAY_WEIGHT_G when not in config."""
    # Config with no extras (use EquipmentConfig directly)
    signals: dict[str, SignalConfig] = {
        "actual_weight": SignalConfig(
            model="steady_state",
            noise_sigma=0.0,
            min_clamp=100.0,
            max_clamp=1000.0,
            params={"target": 410.0},
        ),
        "overweight_count": SignalConfig(model="counter", min_clamp=0.0, max_clamp=9999.0),
        "underweight_count": SignalConfig(model="counter", min_clamp=0.0, max_clamp=9999.0),
        "metal_detect_trips": SignalConfig(model="counter", min_clamp=0.0, max_clamp=99.0),
        "throughput": SignalConfig(
            model="steady_state", noise_sigma=0.0, min_clamp=10.0, max_clamp=120.0
        ),
        "reject_total": SignalConfig(model="counter", min_clamp=0.0, max_clamp=9999.0),
    }
    cfg = EquipmentConfig(enabled=True, type="checkweigher", signals=signals)
    rng = np.random.default_rng(0)
    gen = CheckweigherGenerator("qc", cfg, rng)
    assert gen.tray_weight == _DEFAULT_TRAY_WEIGHT_G
    assert gen.overweight_threshold == _DEFAULT_OVERWEIGHT_THRESHOLD_G
    assert gen.underweight_threshold == _DEFAULT_UNDERWEIGHT_THRESHOLD_G
