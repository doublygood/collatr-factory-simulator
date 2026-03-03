"""Unit tests for the CipGenerator (PRD 2b.8).

Tests verify:
- All 5 signals are produced with correct IDs
- Initial state is Idle (0), all signals at idle values
- force_state transitions to the requested phase
- Auto-progression: each phase advances to the next after its duration
- Pre-rinse → Caustic → Intermediate → Acid → Final → Idle
- wash_temp tracks phase-specific setpoints via first-order lag
- flow_rate is 0 in Idle, >0 in active phases
- conductivity rises in caustic, decays in rinse phases
- cycle_time_elapsed increments during active cycle, resets in Idle
- final_rinse_passed flag set correctly based on conductivity
- Signal IDs are fully-qualified
- 5 SignalValues returned per tick
- Signal values respect min/max clamps
- Determinism (same seed → same output)
- _parse_state accepts valid names, raises on invalid

Task 3.10
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.cip import (
    _CONDUCTIVITY_TARGETS,
    _FLOW_TARGETS,
    _PHASE_DURATIONS,
    _TEMP_TARGETS,
    STATE_ACID,
    STATE_CAUSTIC,
    STATE_FINAL_RINSE,
    STATE_IDLE,
    STATE_INTERMEDIATE,
    STATE_PRE_RINSE,
    CipGenerator,
    _parse_state,
)
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cip_config(
    *,
    temp_noise: float = 0.0,
    flow_noise: float = 0.0,
    cond_noise: float = 0.0,
) -> EquipmentConfig:
    """Create a minimal CIP config for testing."""
    signals: dict[str, SignalConfig] = {}

    signals["state"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=5.0,
        units="enum",
        opcua_node="FoodBevLine.CIP1.State",
        opcua_type="UInt16",
        params={"states": ["idle", "pre_rinse", "caustic_wash",
                           "intermediate_rinse", "acid_wash", "final_rinse"],
                "initial_state": "idle"},
    )
    signals["wash_temp"] = SignalConfig(
        model="first_order_lag",
        noise_sigma=temp_noise,
        sample_rate_ms=5000,
        min_clamp=15.0,
        max_clamp=85.0,
        units="C",
        modbus_hr=[1500, 1501],
        modbus_type="float32",
        params={"tau": 60.0, "initial_value": 20.0},
    )
    signals["flow_rate"] = SignalConfig(
        model="steady_state",
        noise_sigma=flow_noise,
        sample_rate_ms=5000,
        min_clamp=0.0,
        max_clamp=100.0,
        units="L/min",
        modbus_hr=[1502, 1503],
        modbus_type="float32",
        params={"target": 0.0},
    )
    signals["conductivity"] = SignalConfig(
        model="steady_state",
        noise_sigma=cond_noise,
        sample_rate_ms=10000,
        min_clamp=0.0,
        max_clamp=200.0,
        units="mS/cm",
        modbus_hr=[1504, 1505],
        modbus_type="float32",
        params={"target": 0.0},
    )
    signals["cycle_time_elapsed"] = SignalConfig(
        model="counter",
        sample_rate_ms=5000,
        min_clamp=0.0,
        max_clamp=7200.0,
        units="s",
        modbus_hr=[1506, 1507],
        modbus_type="uint32",
        params={"rate": 1.0, "rollover": 7200},
    )

    return EquipmentConfig(
        type="cip_skid",
        enabled=True,
        signals=signals,
    )


def _make_gen(
    *,
    temp_noise: float = 0.0,
    flow_noise: float = 0.0,
    cond_noise: float = 0.0,
    seed: int = 42,
) -> CipGenerator:
    """Create a CipGenerator with optional noise."""
    cfg = _make_cip_config(temp_noise=temp_noise, flow_noise=flow_noise,
                           cond_noise=cond_noise)
    rng = np.random.default_rng(seed)
    return CipGenerator("cip", cfg, rng)


def _empty_store() -> SignalStore:
    return SignalStore()


def _tick(gen: CipGenerator, dt: float = 1.0, n: int = 1) -> list[SignalValue]:
    """Run n ticks, returning the result of the last one."""
    store = _empty_store()
    result: list[SignalValue] = []
    t = 0.0
    for _ in range(n):
        t += dt
        result = gen.generate(t, dt, store)
        for sv in result:
            store.set(sv.signal_id, sv.value, sv.timestamp, sv.quality)
    return result


def _get_sv(result: list[SignalValue], name: str) -> SignalValue:
    """Extract a specific signal from a result list by partial ID match."""
    for sv in result:
        if sv.signal_id.endswith(f".{name}"):
            return sv
    raise KeyError(f"Signal '{name}' not found in result")


# ---------------------------------------------------------------------------
# Signal identity tests
# ---------------------------------------------------------------------------


def test_signal_ids_count() -> None:
    gen = _make_gen()
    assert len(gen.get_signal_ids()) == 5


def test_signal_ids_fully_qualified() -> None:
    gen = _make_gen()
    ids = gen.get_signal_ids()
    assert all(sid.startswith("cip.") for sid in ids)


def test_signal_ids_content() -> None:
    gen = _make_gen()
    ids = set(gen.get_signal_ids())
    expected = {
        "cip.state",
        "cip.wash_temp",
        "cip.flow_rate",
        "cip.conductivity",
        "cip.cycle_time_elapsed",
    }
    assert ids == expected


def test_generate_returns_five_signals() -> None:
    gen = _make_gen()
    result = _tick(gen)
    assert len(result) == 5


def test_output_signal_ids_match() -> None:
    gen = _make_gen()
    result = _tick(gen)
    ids = {sv.signal_id for sv in result}
    assert ids == {
        "cip.state",
        "cip.wash_temp",
        "cip.flow_rate",
        "cip.conductivity",
        "cip.cycle_time_elapsed",
    }


# ---------------------------------------------------------------------------
# Initial state tests
# ---------------------------------------------------------------------------


def test_initial_state_is_idle() -> None:
    gen = _make_gen()
    assert gen.state == STATE_IDLE


def test_initial_state_signal_is_zero() -> None:
    gen = _make_gen()
    result = _tick(gen)
    sv = _get_sv(result, "state")
    assert sv.value == pytest.approx(0.0)


def test_initial_flow_rate_is_zero() -> None:
    """Flow rate should be 0 in Idle."""
    gen = _make_gen()
    assert gen.flow_rate == pytest.approx(0.0)


def test_initial_cycle_time_is_zero() -> None:
    """cycle_time_elapsed should be 0 in Idle."""
    gen = _make_gen()
    result = _tick(gen)
    sv = _get_sv(result, "cycle_time_elapsed")
    assert sv.value == pytest.approx(0.0)


def test_initial_conductivity_near_zero() -> None:
    """Conductivity starts near 0 (Idle setpoint)."""
    gen = _make_gen()
    assert gen.conductivity == pytest.approx(0.0, abs=0.1)


# ---------------------------------------------------------------------------
# force_state tests
# ---------------------------------------------------------------------------


def test_force_state_pre_rinse() -> None:
    gen = _make_gen()
    gen.force_state("Pre_rinse")
    assert gen.state == STATE_PRE_RINSE


def test_force_state_caustic() -> None:
    gen = _make_gen()
    gen.force_state("Caustic")
    assert gen.state == STATE_CAUSTIC


def test_force_state_case_insensitive() -> None:
    gen = _make_gen()
    gen.force_state("caustic_wash")
    assert gen.state == STATE_CAUSTIC


def test_force_state_intermediate() -> None:
    gen = _make_gen()
    gen.force_state("Intermediate")
    assert gen.state == STATE_INTERMEDIATE


def test_force_state_acid() -> None:
    gen = _make_gen()
    gen.force_state("Acid")
    assert gen.state == STATE_ACID


def test_force_state_final_rinse() -> None:
    gen = _make_gen()
    gen.force_state("Final_rinse")
    assert gen.state == STATE_FINAL_RINSE


def test_force_state_idle() -> None:
    gen = _make_gen()
    gen.force_state("Pre_rinse")
    gen.force_state("Idle")
    assert gen.state == STATE_IDLE


def test_force_same_state_is_noop() -> None:
    """force_state to current state should not reset timers."""
    gen = _make_gen()
    gen.force_state("Pre_rinse")
    _tick(gen, dt=1.0, n=10)
    elapsed_before = gen.cycle_time_elapsed
    gen.force_state("Pre_rinse")
    assert gen.cycle_time_elapsed == pytest.approx(elapsed_before)


# ---------------------------------------------------------------------------
# Auto-progression tests
# ---------------------------------------------------------------------------


def test_auto_advances_from_pre_rinse_to_caustic() -> None:
    """Pre-rinse should auto-advance to Caustic after phase duration."""
    gen = _make_gen()
    gen.force_state("Pre_rinse")
    duration = _PHASE_DURATIONS[STATE_PRE_RINSE]
    # Run just past the phase duration
    _tick(gen, dt=1.0, n=int(duration) + 2)
    assert gen.state == STATE_CAUSTIC


def test_auto_advances_from_caustic_to_intermediate() -> None:
    """Caustic should auto-advance to Intermediate after phase duration."""
    gen = _make_gen()
    gen.force_state("Caustic")
    duration = _PHASE_DURATIONS[STATE_CAUSTIC]
    _tick(gen, dt=1.0, n=int(duration) + 2)
    assert gen.state == STATE_INTERMEDIATE


def test_auto_advances_from_acid_to_final_rinse() -> None:
    """Acid should auto-advance to Final_rinse after phase duration."""
    gen = _make_gen()
    gen.force_state("Acid")
    duration = _PHASE_DURATIONS[STATE_ACID]
    _tick(gen, dt=1.0, n=int(duration) + 2)
    assert gen.state == STATE_FINAL_RINSE


def test_auto_advances_from_final_rinse_to_idle() -> None:
    """Final rinse should auto-advance to Idle after phase duration."""
    gen = _make_gen()
    gen.force_state("Final_rinse")
    duration = _PHASE_DURATIONS[STATE_FINAL_RINSE]
    _tick(gen, dt=1.0, n=int(duration) + 2)
    assert gen.state == STATE_IDLE


# ---------------------------------------------------------------------------
# Signal behaviour tests
# ---------------------------------------------------------------------------


def test_flow_rate_positive_during_pre_rinse() -> None:
    """Flow rate should be positive once in an active phase."""
    gen = _make_gen()
    gen.force_state("Pre_rinse")
    # Run enough ticks for flow rate to ramp up (tau=15 s, run 5*tau=75 s)
    _tick(gen, dt=1.0, n=75)
    assert gen.flow_rate > 10.0


def test_flow_rate_approaches_target_in_caustic() -> None:
    """Flow rate should approach the Caustic target."""
    gen = _make_gen()
    gen.force_state("Caustic")
    # 5 time constants (15 s each)
    _tick(gen, dt=1.0, n=75)
    assert gen.flow_rate == pytest.approx(_FLOW_TARGETS[STATE_CAUSTIC], rel=0.1)


def test_flow_rate_returns_to_zero_in_idle() -> None:
    """After cycle completes, flow rate should return toward 0."""
    gen = _make_gen()
    gen.force_state("Pre_rinse")
    _tick(gen, dt=1.0, n=50)
    # Force back to Idle
    gen.force_state("Idle")
    # Run enough for flow to decay (5 * tau = 75 s)
    _tick(gen, dt=1.0, n=75)
    assert gen.flow_rate < 5.0


def test_wash_temp_approaches_caustic_target() -> None:
    """Wash temp should approach 75°C in Caustic phase."""
    gen = _make_gen()
    gen.force_state("Caustic")
    # 5 time constants (90 s each) = 450 s
    _tick(gen, dt=1.0, n=450)
    assert gen.wash_temp == pytest.approx(_TEMP_TARGETS[STATE_CAUSTIC], rel=0.05)


def test_wash_temp_drops_in_pre_rinse() -> None:
    """Wash temp target in pre-rinse (45°C) is below caustic (75°C)."""
    assert _TEMP_TARGETS[STATE_PRE_RINSE] < _TEMP_TARGETS[STATE_CAUSTIC]


def test_conductivity_rises_in_caustic() -> None:
    """Conductivity should rise significantly in Caustic phase."""
    gen = _make_gen()
    gen.force_state("Caustic")
    initial = gen.conductivity
    # Run 5 * tau_rise = 300 s
    _tick(gen, dt=1.0, n=300)
    assert gen.conductivity > initial + 50.0


def test_conductivity_approaches_caustic_target() -> None:
    """Conductivity should approach ~120 mS/cm in Caustic."""
    gen = _make_gen()
    gen.force_state("Caustic")
    # 5 * 60 = 300 s
    _tick(gen, dt=1.0, n=300)
    assert gen.conductivity == pytest.approx(
        _CONDUCTIVITY_TARGETS[STATE_CAUSTIC], rel=0.1
    )


def test_conductivity_decays_in_intermediate_rinse() -> None:
    """After caustic, conductivity should decay in intermediate rinse."""
    gen = _make_gen()
    gen.force_state("Caustic")
    # Build up conductivity in caustic
    _tick(gen, dt=1.0, n=300)
    assert gen.conductivity > 100.0

    # Switch to intermediate rinse
    gen.force_state("Intermediate")
    cond_after_caustic = gen.conductivity
    _tick(gen, dt=1.0, n=300)
    # Should have decayed significantly
    assert gen.conductivity < cond_after_caustic - 20.0


def test_conductivity_not_negative() -> None:
    """Conductivity must never go below 0."""
    gen = _make_gen()
    store = _empty_store()
    gen.force_state("Final_rinse")
    t = 0.0
    for _ in range(300):
        t += 1.0
        for sv in gen.generate(t, 1.0, store):
            if sv.signal_id == "cip.conductivity":
                assert sv.value >= 0.0


# ---------------------------------------------------------------------------
# cycle_time_elapsed tests
# ---------------------------------------------------------------------------


def test_cycle_time_increments_during_active_phase() -> None:
    """cycle_time_elapsed should increase during an active phase."""
    gen = _make_gen()
    gen.force_state("Pre_rinse")
    _tick(gen, dt=1.0, n=10)
    assert gen.cycle_time_elapsed == pytest.approx(10.0, abs=0.5)


def test_cycle_time_zero_when_idle() -> None:
    """cycle_time_elapsed signal should be 0 in Idle."""
    gen = _make_gen()
    result = _tick(gen, dt=1.0, n=5)
    sv = _get_sv(result, "cycle_time_elapsed")
    assert sv.value == pytest.approx(0.0)


def test_cycle_time_reset_after_cycle() -> None:
    """After returning to Idle, cycle_time_elapsed should reset to 0."""
    gen = _make_gen()
    gen.force_state("Pre_rinse")
    _tick(gen, dt=1.0, n=10)
    gen.force_state("Idle")
    result = _tick(gen, dt=1.0, n=1)
    sv = _get_sv(result, "cycle_time_elapsed")
    assert sv.value == pytest.approx(0.0)


def test_cycle_time_signal_within_clamp() -> None:
    """cycle_time_elapsed signal must stay within 0-7200."""
    gen = _make_gen()
    gen.force_state("Pre_rinse")
    store = _empty_store()
    t = 0.0
    for _ in range(1000):
        t += 1.0
        for sv in gen.generate(t, 1.0, store):
            if sv.signal_id == "cip.cycle_time_elapsed":
                assert 0.0 <= sv.value <= 7200.0


# ---------------------------------------------------------------------------
# final_rinse_passed tests
# ---------------------------------------------------------------------------


def test_final_rinse_pass_flag_when_conductivity_low() -> None:
    """When conductivity is already low, final_rinse_passed should be True."""
    gen = _make_gen()
    # Conductivity starts at 0, well below threshold
    gen.force_state("Final_rinse")
    duration = _PHASE_DURATIONS[STATE_FINAL_RINSE]
    _tick(gen, dt=1.0, n=int(duration) + 2)
    # Should have auto-advanced to Idle with low conductivity
    assert gen.state == STATE_IDLE
    assert gen.final_rinse_passed is True


def test_final_rinse_fail_flag_when_conductivity_high() -> None:
    """When conductivity is high at end of final rinse, flag should be False."""
    gen = _make_gen()
    # Manually set high conductivity before final rinse
    gen._conductivity = 50.0  # above threshold of 5 mS/cm
    gen.force_state("Final_rinse")
    # Run a very short final rinse so conductivity stays high
    _PHASE_DURATIONS[STATE_FINAL_RINSE]
    # Override phase duration temporarily via internal tick
    # Inject a single tick with the duration just expired
    gen._phase_elapsed = _PHASE_DURATIONS[STATE_FINAL_RINSE] - 0.1
    _tick(gen, dt=1.0, n=1)
    # Should have transitioned to Idle with high conductivity → fail
    assert gen.state == STATE_IDLE
    assert gen.final_rinse_passed is False


# ---------------------------------------------------------------------------
# Signal bounds tests
# ---------------------------------------------------------------------------


def test_wash_temp_within_clamp() -> None:
    """wash_temp must stay within 15-85°C config clamps."""
    gen = _make_gen(temp_noise=0.5)
    store = _empty_store()
    gen.force_state("Caustic")
    t = 0.0
    for _ in range(200):
        t += 1.0
        for sv in gen.generate(t, 1.0, store):
            if sv.signal_id == "cip.wash_temp":
                assert 15.0 <= sv.value <= 85.0


def test_flow_rate_within_clamp() -> None:
    """flow_rate must stay within 0-100 L/min."""
    gen = _make_gen(flow_noise=2.0)
    store = _empty_store()
    gen.force_state("Caustic")
    t = 0.0
    for _ in range(200):
        t += 1.0
        for sv in gen.generate(t, 1.0, store):
            if sv.signal_id == "cip.flow_rate":
                assert 0.0 <= sv.value <= 100.0


def test_conductivity_within_clamp() -> None:
    """conductivity must stay within 0-200 mS/cm."""
    gen = _make_gen(cond_noise=1.0)
    store = _empty_store()
    gen.force_state("Caustic")
    t = 0.0
    for _ in range(300):
        t += 1.0
        for sv in gen.generate(t, 1.0, store):
            if sv.signal_id == "cip.conductivity":
                assert 0.0 <= sv.value <= 200.0


def test_state_signal_within_0_5() -> None:
    """state signal must always be in 0-5 range."""
    gen = _make_gen()
    store = _empty_store()
    gen.force_state("Pre_rinse")
    t = 0.0
    for _ in range(10):
        t += 1.0
        for sv in gen.generate(t, 1.0, store):
            if sv.signal_id == "cip.state":
                assert 0.0 <= sv.value <= 5.0


# ---------------------------------------------------------------------------
# Protocol mapping tests
# ---------------------------------------------------------------------------


def test_protocol_mappings_hr_signals() -> None:
    """HR-mapped signals should have Modbus mappings."""
    gen = _make_gen()
    mappings = gen.get_protocol_mappings()
    hr_signals = [
        "cip.wash_temp",
        "cip.flow_rate",
        "cip.conductivity",
        "cip.cycle_time_elapsed",
    ]
    for sig_id in hr_signals:
        assert sig_id in mappings, f"Missing mapping for {sig_id}"
        assert mappings[sig_id].modbus is not None, f"No Modbus mapping for {sig_id}"


def test_state_signal_has_opcua_mapping() -> None:
    """cip.state should have OPC-UA mapping (not Modbus)."""
    gen = _make_gen()
    mappings = gen.get_protocol_mappings()
    assert "cip.state" in mappings
    assert mappings["cip.state"].opcua is not None
    assert mappings["cip.state"].modbus is None


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism() -> None:
    """Same seed → identical output."""
    gen1 = _make_gen(seed=7, temp_noise=0.3)
    gen2 = _make_gen(seed=7, temp_noise=0.3)

    gen1.force_state("Caustic")
    gen2.force_state("Caustic")

    result1 = _tick(gen1, dt=1.0, n=50)
    result2 = _tick(gen2, dt=1.0, n=50)

    assert len(result1) == len(result2)
    for sv1, sv2 in zip(result1, result2, strict=False):
        assert sv1.signal_id == sv2.signal_id
        assert sv1.value == pytest.approx(sv2.value)


def test_different_seeds_different_output_with_noise() -> None:
    """Different seeds → different output when noise > 0."""
    gen1 = _make_gen(seed=1, temp_noise=0.5)
    gen2 = _make_gen(seed=2, temp_noise=0.5)

    gen1.force_state("Caustic")
    gen2.force_state("Caustic")

    result1 = _tick(gen1, dt=1.0, n=10)
    result2 = _tick(gen2, dt=1.0, n=10)

    vals1 = {sv.signal_id: sv.value for sv in result1}
    vals2 = {sv.signal_id: sv.value for sv in result2}
    any_diff = any(
        abs(vals1[k] - vals2[k]) > 1e-9
        for k in vals1
        if k == "cip.wash_temp"
    )
    assert any_diff


# ---------------------------------------------------------------------------
# _parse_state tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,expected", [
    ("Idle", STATE_IDLE),
    ("idle", STATE_IDLE),
    ("Pre_rinse", STATE_PRE_RINSE),
    ("pre_rinse", STATE_PRE_RINSE),
    ("Caustic", STATE_CAUSTIC),
    ("caustic_wash", STATE_CAUSTIC),
    ("Intermediate", STATE_INTERMEDIATE),
    ("intermediate_rinse", STATE_INTERMEDIATE),
    ("Acid", STATE_ACID),
    ("acid_wash", STATE_ACID),
    ("Final_rinse", STATE_FINAL_RINSE),
    ("final", STATE_FINAL_RINSE),
])
def test_parse_state_valid(name: str, expected: int) -> None:
    assert _parse_state(name) == expected


def test_parse_state_invalid() -> None:
    with pytest.raises(ValueError, match="Unknown CIP state"):
        _parse_state("unknown_phase")
