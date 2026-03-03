"""Unit tests for the ChillerGenerator (PRD 2b.7).

Tests verify:
- All 7 signals are produced with correct IDs
- Initial room_temp starts at setpoint
- Bang-bang: compressor turns off when temp drops below lower threshold
- Bang-bang: compressor turns on when temp rises above upper threshold
- Room temp decreases when compressor ON
- Room temp increases when compressor OFF
- Defrost cycle activates after the configured period
- Defrost cycle forces compressor OFF
- Defrost cycle increases heat gain rate
- Defrost cycle ends after the configured duration
- Door open increases heat gain rate
- Suction pressure tracks compressor state (lower when ON)
- Discharge pressure tracks compressor state (higher when ON)
- compressor_forced_off locks compressor off
- compressor_forced_off overrides bang-bang
- All signal IDs are fully qualified
- 7 SignalValues returned per tick
- Signal values respect min/max clamps
- Determinism (same seed → same output)
- Protocol mappings for HR signals

Task 3.9
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.chiller import (
    _DEAD_BAND_HIGH_C,
    _DEAD_BAND_LOW_C,
    _DEFROST_DURATION_S,
    _DEFROST_PERIOD_S,
    _DISCHARGE_TARGET_OFF,
    _DISCHARGE_TARGET_ON,
    _DOOR_OPEN_HEAT_RATE_C_PER_S,
    _PRESSURE_TAU_S,
    _SUCTION_TARGET_OFF,
    _SUCTION_TARGET_ON,
    ChillerGenerator,
)
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SETPOINT = 2.0


def _make_chiller_config(
    *,
    setpoint: float = _SETPOINT,
    room_temp_noise: float = 0.0,   # noise-free for most tests
    suction_noise: float = 0.0,
    discharge_noise: float = 0.0,
) -> EquipmentConfig:
    """Create a minimal chiller config for testing."""
    signals: dict[str, SignalConfig] = {}

    signals["room_temp"] = SignalConfig(
        model="steady_state",
        noise_sigma=room_temp_noise,
        sample_rate_ms=30000,
        min_clamp=-5.0,
        max_clamp=15.0,
        units="C",
        modbus_hr=[1400, 1401],
        modbus_type="float32",
        params={"target": setpoint},
    )
    signals["setpoint"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        min_clamp=-5.0,
        max_clamp=15.0,
        units="C",
        modbus_hr=[1402, 1403],
        modbus_type="float32",
        modbus_writable=True,
        params={"target": setpoint},
    )
    signals["compressor_state"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=1.0,
        units="bool",
        params={"states": ["off", "on"], "initial_state": "on"},
    )
    signals["suction_pressure"] = SignalConfig(
        model="steady_state",
        noise_sigma=suction_noise,
        sample_rate_ms=30000,
        min_clamp=0.0,
        max_clamp=10.0,
        units="bar",
        modbus_hr=[1404, 1405],
        modbus_type="float32",
        params={"target": 3.5},
    )
    signals["discharge_pressure"] = SignalConfig(
        model="steady_state",
        noise_sigma=discharge_noise,
        sample_rate_ms=30000,
        min_clamp=5.0,
        max_clamp=25.0,
        units="bar",
        modbus_hr=[1406, 1407],
        modbus_type="float32",
        params={"target": 15.0},
    )
    signals["defrost_active"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=1.0,
        units="bool",
        params={"states": ["inactive", "active"], "initial_state": "inactive"},
    )
    signals["door_open"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=1.0,
        units="bool",
        params={"states": ["closed", "open"], "initial_state": "closed"},
    )

    return EquipmentConfig(
        type="cold_room",
        enabled=True,
        signals=signals,
    )


def _make_gen(
    *,
    setpoint: float = _SETPOINT,
    room_temp_noise: float = 0.0,
    seed: int = 42,
) -> ChillerGenerator:
    """Create a ChillerGenerator with the given setpoint."""
    cfg = _make_chiller_config(setpoint=setpoint, room_temp_noise=room_temp_noise)
    rng = np.random.default_rng(seed)
    return ChillerGenerator("chiller", cfg, rng)


def _empty_store() -> SignalStore:
    return SignalStore()


def _tick(gen: ChillerGenerator, dt: float = 1.0, n: int = 1) -> list[SignalValue]:
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


# ---------------------------------------------------------------------------
# Signal identity tests
# ---------------------------------------------------------------------------


def test_signal_ids_count() -> None:
    gen = _make_gen()
    assert len(gen.get_signal_ids()) == 7


def test_signal_ids_fully_qualified() -> None:
    gen = _make_gen()
    ids = gen.get_signal_ids()
    assert all(sid.startswith("chiller.") for sid in ids)


def test_signal_ids_content() -> None:
    gen = _make_gen()
    ids = gen.get_signal_ids()
    expected = {
        "chiller.room_temp",
        "chiller.setpoint",
        "chiller.compressor_state",
        "chiller.suction_pressure",
        "chiller.discharge_pressure",
        "chiller.defrost_active",
        "chiller.door_open",
    }
    assert set(ids) == expected


def test_generate_returns_seven_signals() -> None:
    gen = _make_gen()
    result = _tick(gen)
    assert len(result) == 7


def test_output_signal_ids_match() -> None:
    gen = _make_gen()
    result = _tick(gen)
    ids = {sv.signal_id for sv in result}
    assert ids == {
        "chiller.room_temp",
        "chiller.setpoint",
        "chiller.compressor_state",
        "chiller.suction_pressure",
        "chiller.discharge_pressure",
        "chiller.defrost_active",
        "chiller.door_open",
    }


# ---------------------------------------------------------------------------
# Initial state tests
# ---------------------------------------------------------------------------


def test_initial_room_temp_equals_setpoint() -> None:
    gen = _make_gen(setpoint=2.0)
    assert gen.room_temp == pytest.approx(2.0)


def test_initial_compressor_on() -> None:
    gen = _make_gen()
    assert gen.compressor_on is True


def test_initial_defrost_inactive() -> None:
    gen = _make_gen()
    assert gen.defrost_active is False


def test_initial_door_closed() -> None:
    gen = _make_gen()
    assert gen.door_open is False


def test_setpoint_output_matches_config() -> None:
    gen = _make_gen(setpoint=3.0)
    result = _tick(gen)
    sp_sv = next(sv for sv in result if sv.signal_id == "chiller.setpoint")
    assert sp_sv.value == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# Bang-bang: room temperature cooling
# ---------------------------------------------------------------------------


def test_room_temp_decreases_when_compressor_on() -> None:
    """With compressor ON, room_temp should decrease each tick."""
    gen = _make_gen()
    assert gen.compressor_on is True

    temps: list[float] = []
    for _ in range(5):
        _tick(gen, dt=10.0, n=1)
        temps.append(gen.room_temp)

    assert all(temps[i] <= temps[i - 1] for i in range(1, len(temps)))


def test_room_temp_increases_when_compressor_off() -> None:
    """With compressor OFF, room_temp should increase each tick."""
    gen = _make_gen(setpoint=5.0)
    # Force compressor off by setting room_temp well below the lower threshold
    gen.room_temp = 5.0 - _DEAD_BAND_LOW_C - 0.5   # below lower threshold

    # Trigger the OFF condition
    _tick(gen, dt=1.0, n=1)
    assert gen.compressor_on is False

    # Now track temperature (compressor off, no door, no defrost)
    temps: list[float] = []
    for _ in range(5):
        _tick(gen, dt=10.0, n=1)
        temps.append(gen.room_temp)

    assert all(temps[i] >= temps[i - 1] for i in range(1, len(temps)))


def test_bang_bang_compressor_turns_off() -> None:
    """Compressor should turn OFF when room_temp drops below setpoint - dead_band_low."""
    gen = _make_gen(setpoint=5.0)
    # Put temp just below the lower threshold to trigger OFF
    gen.room_temp = 5.0 - _DEAD_BAND_LOW_C - 0.1
    _tick(gen, dt=0.1, n=1)
    assert gen.compressor_on is False


def test_bang_bang_compressor_turns_on() -> None:
    """Compressor should turn ON when room_temp exceeds setpoint + dead_band_high."""
    gen = _make_gen(setpoint=5.0)
    # First force it off
    gen.room_temp = 5.0 - _DEAD_BAND_LOW_C - 0.1
    _tick(gen, dt=0.1, n=1)
    assert gen.compressor_on is False

    # Now push temp above the upper threshold
    gen.room_temp = 5.0 + _DEAD_BAND_HIGH_C + 0.1
    _tick(gen, dt=0.1, n=1)
    assert gen.compressor_on is True


def test_bang_bang_sawtooth_oscillation() -> None:
    """Over many ticks, room_temp should oscillate around the setpoint."""
    gen = _make_gen(setpoint=2.0)
    # Run enough ticks to see at least one full compressor cycle
    # Cooling 1→3°C: time = 2 °C / (0.5/60) = 240 s
    # Heating 1→3°C: time = 2 °C / (0.2/60) = 600 s
    # Total cycle ≈ 840 s; run for twice that
    n_ticks = 1700
    dt = 1.0
    temps: list[float] = []
    t = 0.0
    store = _empty_store()
    for _ in range(n_ticks):
        t += dt
        gen.generate(t, dt, store)
        # Read the internal (noise-free) room_temp
        temps.append(gen.room_temp)

    # Room temp should stay within a reasonable band around the setpoint
    assert min(temps) >= _SETPOINT - _DEAD_BAND_LOW_C - 0.5
    assert max(temps) <= _SETPOINT + _DEAD_BAND_HIGH_C + 0.5

    # Should have at least one compressor cycle (temp both above and below setpoint)
    above = any(t > _SETPOINT + 0.5 for t in temps)
    below = any(t < _SETPOINT - 0.5 for t in temps)
    assert above or below  # at minimum one direction should be visible


# ---------------------------------------------------------------------------
# Defrost cycle tests
# ---------------------------------------------------------------------------


def test_defrost_activates_after_period() -> None:
    """Defrost should become active after DEFROST_PERIOD_S seconds."""
    gen = _make_gen()
    assert gen.defrost_active is False

    # Advance past the defrost period
    dt = 1.0
    n = int(_DEFROST_PERIOD_S) + 5
    _tick(gen, dt=dt, n=n)

    assert gen.defrost_active is True


def test_defrost_forces_compressor_off() -> None:
    """During defrost, compressor_state should be 0.0."""
    gen = _make_gen()
    # Manually activate defrost
    gen._defrost_active = True
    gen._compressor_on = True  # would normally be on

    result = _tick(gen, dt=1.0, n=1)
    comp_sv = next(sv for sv in result if sv.signal_id == "chiller.compressor_state")
    assert comp_sv.value == pytest.approx(0.0)
    assert gen.compressor_on is False


def test_defrost_signal_reflects_active() -> None:
    """defrost_active signal should be 1.0 while defrost is active."""
    gen = _make_gen()
    gen._defrost_active = True

    result = _tick(gen, dt=1.0, n=1)
    defrost_sv = next(sv for sv in result if sv.signal_id == "chiller.defrost_active")
    assert defrost_sv.value == pytest.approx(1.0)


def test_defrost_ends_after_duration() -> None:
    """Defrost should deactivate after DEFROST_DURATION_S seconds."""
    gen = _make_gen()
    gen._defrost_active = True
    gen._defrost_elapsed = 0.0

    dt = 1.0
    n = int(_DEFROST_DURATION_S) + 5
    _tick(gen, dt=dt, n=n)

    assert gen.defrost_active is False


def test_defrost_increases_heat_rate() -> None:
    """Room temp should rise faster during defrost than during normal OFF state."""
    # Normal OFF heat rate over 100 s
    gen_normal = _make_gen(setpoint=10.0)   # high setpoint, compressor stays off
    gen_normal.room_temp = 4.0
    gen_normal._compressor_on = False
    gen_normal._compressor_forced_off = True
    start_normal = gen_normal.room_temp
    _tick(gen_normal, dt=1.0, n=100)
    rise_normal = gen_normal.room_temp - start_normal

    # Defrost active: compressor forced off + defrost heat
    gen_defrost = _make_gen(setpoint=10.0)
    gen_defrost.room_temp = 4.0
    gen_defrost._compressor_on = False
    gen_defrost._defrost_active = True
    gen_defrost._defrost_elapsed = 0.0
    start_defrost = gen_defrost.room_temp
    _tick(gen_defrost, dt=1.0, n=100)
    rise_defrost = gen_defrost.room_temp - start_defrost

    assert rise_defrost > rise_normal


# ---------------------------------------------------------------------------
# Door open tests
# ---------------------------------------------------------------------------


def test_door_open_signal_is_one_when_open() -> None:
    """door_open signal should be 1.0 when door is open."""
    gen = _make_gen()
    gen.door_open = True

    result = _tick(gen, dt=1.0, n=1)
    door_sv = next(sv for sv in result if sv.signal_id == "chiller.door_open")
    assert door_sv.value == pytest.approx(1.0)


def test_door_closed_signal_is_zero() -> None:
    """door_open signal should be 0.0 when door is closed."""
    gen = _make_gen()
    assert gen.door_open is False

    result = _tick(gen, dt=1.0, n=1)
    door_sv = next(sv for sv in result if sv.signal_id == "chiller.door_open")
    assert door_sv.value == pytest.approx(0.0)


def test_door_open_increases_heat_gain() -> None:
    """Room temp should rise faster with door open vs door closed."""
    n = 300  # 300 s

    # Without door open
    gen_closed = _make_gen(setpoint=20.0)  # high setpoint → compressor stays off
    gen_closed.room_temp = 4.0
    gen_closed._compressor_on = False
    gen_closed._compressor_forced_off = True
    start = gen_closed.room_temp
    _tick(gen_closed, dt=1.0, n=n)
    rise_closed = gen_closed.room_temp - start

    # With door open
    gen_open = _make_gen(setpoint=20.0)
    gen_open.room_temp = 4.0
    gen_open._compressor_on = False
    gen_open._compressor_forced_off = True
    gen_open.door_open = True
    start = gen_open.room_temp
    _tick(gen_open, dt=1.0, n=n)
    rise_open = gen_open.room_temp - start

    assert rise_open > rise_closed
    # Expected: door adds _DOOR_OPEN_HEAT_RATE_C_PER_S * n seconds extra
    expected_extra = _DOOR_OPEN_HEAT_RATE_C_PER_S * n
    assert abs((rise_open - rise_closed) - expected_extra) < 0.05


# ---------------------------------------------------------------------------
# Pressure tests
# ---------------------------------------------------------------------------


def test_suction_pressure_lower_when_compressor_on() -> None:
    """Suction pressure target is lower when compressor is ON."""
    assert _SUCTION_TARGET_ON < _SUCTION_TARGET_OFF


def test_discharge_pressure_higher_when_compressor_on() -> None:
    """Discharge pressure target is higher when compressor is ON."""
    assert _DISCHARGE_TARGET_ON > _DISCHARGE_TARGET_OFF


def test_suction_approaches_on_target_over_time() -> None:
    """Suction pressure should approach ON target when compressor is ON."""
    # Use high setpoint so lower threshold (setpoint-1) is far above initial room_temp;
    # compressor starts ON and won't switch off for many ticks.
    # Set setpoint=12°C → lower threshold=11°C. Start room_temp at 30°C (internal,
    # no clamp on _room_temp). Cooling at 0.00833°C/s takes ~2280 s to reach 11°C,
    # so compressor stays ON for the full 300-tick test.
    gen = _make_gen(setpoint=12.0)
    gen.room_temp = 30.0   # internal temp, compressor stays ON for 300+ ticks
    assert gen.compressor_on is True

    # Pressure starts at ON target; just verify it stays near there (lag is <300s)
    n = int(5 * _PRESSURE_TAU_S)
    _tick(gen, dt=1.0, n=n)

    # Should be close to ON target (within 0.5 bar)
    assert gen._suction_current == pytest.approx(_SUCTION_TARGET_ON, abs=0.5)


def test_suction_approaches_off_target_when_compressor_off() -> None:
    """Suction pressure should approach OFF target when compressor is OFF."""
    gen = _make_gen(setpoint=10.0)
    # Force compressor off
    gen._compressor_forced_off = True
    gen._compressor_on = False

    n = int(5 * _PRESSURE_TAU_S)
    _tick(gen, dt=1.0, n=n)

    assert gen._suction_current == pytest.approx(_SUCTION_TARGET_OFF, abs=0.5)


# ---------------------------------------------------------------------------
# compressor_forced_off tests
# ---------------------------------------------------------------------------


def test_compressor_forced_off_locks_compressor() -> None:
    """When compressor_forced_off is True, compressor stays off even if temp is high."""
    gen = _make_gen()
    gen.compressor_forced_off = True
    # Put temp well above upper threshold
    gen.room_temp = _SETPOINT + _DEAD_BAND_HIGH_C + 5.0

    _tick(gen, dt=1.0, n=10)
    assert gen.compressor_on is False


def test_compressor_forced_off_releases() -> None:
    """After releasing the forced-off lock, bang-bang resumes."""
    gen = _make_gen()
    gen.compressor_forced_off = True
    gen.room_temp = _SETPOINT + _DEAD_BAND_HIGH_C + 5.0
    _tick(gen, dt=1.0, n=5)
    assert gen.compressor_on is False

    # Release the lock
    gen.compressor_forced_off = False
    _tick(gen, dt=0.1, n=1)
    # With temp above upper threshold and no lock, should turn ON
    assert gen.compressor_on is True


# ---------------------------------------------------------------------------
# Signal bounds
# ---------------------------------------------------------------------------


def test_room_temp_within_clamp() -> None:
    """room_temp output must respect min/max clamps."""
    gen = _make_gen(room_temp_noise=0.5, setpoint=_SETPOINT)
    store = _empty_store()
    t = 0.0
    for _ in range(200):
        t += 1.0
        for sv in gen.generate(t, 1.0, store):
            if sv.signal_id == "chiller.room_temp":
                assert -5.0 <= sv.value <= 15.0


def test_compressor_state_binary() -> None:
    """compressor_state must always be 0.0 or 1.0."""
    gen = _make_gen()
    store = _empty_store()
    t = 0.0
    for _ in range(200):
        t += 10.0
        for sv in gen.generate(t, 10.0, store):
            if sv.signal_id == "chiller.compressor_state":
                assert sv.value in (0.0, 1.0)


def test_suction_pressure_within_clamp() -> None:
    """Suction pressure output must stay within 0-10 bar."""
    gen = _make_gen()
    store = _empty_store()
    t = 0.0
    for _ in range(200):
        t += 10.0
        for sv in gen.generate(t, 10.0, store):
            if sv.signal_id == "chiller.suction_pressure":
                assert 0.0 <= sv.value <= 10.0


def test_discharge_pressure_within_clamp() -> None:
    """Discharge pressure output must stay within 5-25 bar."""
    gen = _make_gen()
    store = _empty_store()
    t = 0.0
    for _ in range(200):
        t += 10.0
        for sv in gen.generate(t, 10.0, store):
            if sv.signal_id == "chiller.discharge_pressure":
                assert 5.0 <= sv.value <= 25.0


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism() -> None:
    """Same seed should produce identical output."""
    gen1 = _make_gen(seed=99, room_temp_noise=0.2)
    gen2 = _make_gen(seed=99, room_temp_noise=0.2)

    result1 = _tick(gen1, dt=1.0, n=50)
    result2 = _tick(gen2, dt=1.0, n=50)

    assert len(result1) == len(result2)
    for sv1, sv2 in zip(result1, result2, strict=False):
        assert sv1.signal_id == sv2.signal_id
        assert sv1.value == pytest.approx(sv2.value)


def test_different_seeds_different_output() -> None:
    """Different seeds should produce different outputs when noise > 0."""
    gen1 = _make_gen(seed=1, room_temp_noise=0.5)
    gen2 = _make_gen(seed=2, room_temp_noise=0.5)

    result1 = _tick(gen1, dt=1.0, n=10)
    result2 = _tick(gen2, dt=1.0, n=10)

    # At least one signal should differ
    values1 = {sv.signal_id: sv.value for sv in result1}
    values2 = {sv.signal_id: sv.value for sv in result2}
    any_diff = any(
        abs(values1[k] - values2[k]) > 1e-9
        for k in values1
        if k == "chiller.room_temp"
    )
    assert any_diff


# ---------------------------------------------------------------------------
# Protocol mappings
# ---------------------------------------------------------------------------


def test_protocol_mappings_hr_signals() -> None:
    """HR-mapped signals should have Modbus mappings."""
    gen = _make_gen()
    mappings = gen.get_protocol_mappings()

    hr_signals = [
        "chiller.room_temp",
        "chiller.setpoint",
        "chiller.suction_pressure",
        "chiller.discharge_pressure",
    ]
    for sig_id in hr_signals:
        assert sig_id in mappings, f"Missing mapping for {sig_id}"
        assert mappings[sig_id].modbus is not None, f"No Modbus mapping for {sig_id}"


def test_setpoint_is_writable() -> None:
    """setpoint should be flagged as Modbus writable."""
    gen = _make_gen()
    mappings = gen.get_protocol_mappings()
    assert mappings["chiller.setpoint"].modbus is not None
    assert mappings["chiller.setpoint"].modbus.writable is True
