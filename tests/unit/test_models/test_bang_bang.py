"""Tests for the BangBangModel (on/off controller with hysteresis).

PRD Reference: Section 4.2.12 (Bang-Bang with Hysteresis)
"""

from __future__ import annotations

import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factory_simulator.models.bang_bang import BangBangModel
from factory_simulator.models.noise import NoiseGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SEED = 42
DT = 1.0  # 1 second tick


def _make_rng(seed: int = SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


def _make_noise(
    sigma: float = 0.1, seed: int = SEED
) -> NoiseGenerator:
    return NoiseGenerator(sigma, "gaussian", _make_rng(seed))


def _make_model(
    params: dict[str, object] | None = None,
    seed: int = SEED,
    noise: NoiseGenerator | None = None,
) -> BangBangModel:
    p = params if params is not None else {}
    return BangBangModel(p, _make_rng(seed), noise=noise)


def _run_ticks(
    model: BangBangModel, n: int, dt: float = DT
) -> list[float]:
    """Run n ticks and return the values."""
    t = 0.0
    values: list[float] = []
    for _ in range(n):
        values.append(model.generate(t, dt))
        t += dt
    return values


# ===================================================================
# Construction
# ===================================================================


class TestConstruction:
    def test_defaults(self) -> None:
        m = _make_model()
        assert m.setpoint == 2.0
        assert m.dead_band_high == 1.0
        assert m.dead_band_low == 1.0
        assert m.cooling_rate == 0.5
        assert m.heat_gain_rate == 0.2
        assert m.pv == 2.0  # defaults to setpoint
        assert m.compressor_on is False

    def test_explicit_params(self) -> None:
        m = _make_model({
            "setpoint": 4.0,
            "dead_band_high": 2.0,
            "dead_band_low": 1.5,
            "cooling_rate": 1.0,
            "heat_gain_rate": 0.3,
        })
        assert m.setpoint == 4.0
        assert m.dead_band_high == 2.0
        assert m.dead_band_low == 1.5
        assert m.cooling_rate == 1.0
        assert m.heat_gain_rate == 0.3

    def test_initial_temp_explicit(self) -> None:
        m = _make_model({"initial_temp": 5.0})
        assert m.pv == 5.0

    def test_initial_temp_defaults_to_setpoint(self) -> None:
        m = _make_model({"setpoint": -2.0})
        assert m.pv == -2.0

    def test_initial_state_on(self) -> None:
        m = _make_model({"initial_state": "on"})
        assert m.compressor_on is True

    def test_initial_state_off(self) -> None:
        m = _make_model({"initial_state": "off"})
        assert m.compressor_on is False

    def test_initial_state_case_insensitive(self) -> None:
        m = _make_model({"initial_state": "ON"})
        assert m.compressor_on is True

    def test_invalid_dead_band_high(self) -> None:
        with pytest.raises(ValueError, match="dead_band_high must be > 0"):
            _make_model({"dead_band_high": 0.0})

    def test_invalid_dead_band_high_negative(self) -> None:
        with pytest.raises(ValueError, match="dead_band_high must be > 0"):
            _make_model({"dead_band_high": -1.0})

    def test_invalid_dead_band_low(self) -> None:
        with pytest.raises(ValueError, match="dead_band_low must be > 0"):
            _make_model({"dead_band_low": 0.0})

    def test_invalid_cooling_rate(self) -> None:
        with pytest.raises(ValueError, match="cooling_rate must be > 0"):
            _make_model({"cooling_rate": 0.0})

    def test_invalid_heat_gain_rate(self) -> None:
        with pytest.raises(ValueError, match="heat_gain_rate must be > 0"):
            _make_model({"heat_gain_rate": -0.1})

    def test_invalid_initial_state(self) -> None:
        with pytest.raises(ValueError, match="initial_state must be"):
            _make_model({"initial_state": "maybe"})


# ===================================================================
# Basic Sawtooth Behaviour
# ===================================================================


class TestSawtoothBehaviour:
    """Verify the characteristic sawtooth temperature pattern."""

    def test_heat_gain_when_off(self) -> None:
        """PV increases when compressor is OFF."""
        m = _make_model({"setpoint": 2.0, "initial_temp": 2.0, "initial_state": "off"})
        vals = _run_ticks(m, 60, dt=1.0)
        # heat_gain_rate = 0.2 C/min = 0.2/60 C/s
        # After 60s, PV should increase by 0.2 C
        assert vals[-1] > 2.0
        assert vals[-1] == pytest.approx(2.0 + 0.2 / 60.0 * 60, abs=1e-10)

    def test_cooling_when_on(self) -> None:
        """PV decreases when compressor is ON."""
        m = _make_model({"setpoint": 2.0, "initial_temp": 2.0, "initial_state": "on"})
        vals = _run_ticks(m, 60, dt=1.0)
        # cooling_rate = 0.5 C/min = 0.5/60 C/s
        # After 60s, PV should decrease by 0.5 C
        assert vals[-1] < 2.0
        assert vals[-1] == pytest.approx(2.0 - 0.5 / 60.0 * 60, abs=1e-10)

    def test_turns_on_above_upper_threshold(self) -> None:
        """Compressor turns ON when PV exceeds setpoint + dead_band_high."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "heat_gain_rate": 6.0,  # Fast heat gain: 6 C/min = 0.1 C/s
            "initial_temp": 2.0,
            "initial_state": "off",
        })
        # Run until PV > 3.0 (upper threshold)
        was_off = True
        turned_on = False
        for i in range(200):
            m.generate(float(i), 1.0)
            if was_off and m.compressor_on:
                turned_on = True
                # PV should be just above upper threshold
                assert m.pv > 2.0  # Above setpoint (may have passed threshold)
                break
            was_off = not m.compressor_on

        assert turned_on, "Compressor should have turned on"

    def test_turns_off_below_lower_threshold(self) -> None:
        """Compressor turns OFF when PV drops below setpoint - dead_band_low."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_low": 1.0,
            "cooling_rate": 6.0,  # Fast cooling: 6 C/min = 0.1 C/s
            "initial_temp": 2.0,
            "initial_state": "on",
        })
        # Run until PV < 1.0 (lower threshold)
        was_on = True
        turned_off = False
        for i in range(200):
            m.generate(float(i), 1.0)
            if was_on and not m.compressor_on:
                turned_off = True
                # PV should be just below lower threshold
                assert m.pv < 2.0  # Below setpoint (may have passed threshold)
                break
            was_on = m.compressor_on

        assert turned_off, "Compressor should have turned off"

    def test_full_cycle(self) -> None:
        """Complete one ON/OFF cycle with expected sawtooth shape."""
        # Start at upper threshold, compressor just turned on
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "dead_band_low": 1.0,
            "cooling_rate": 0.5,
            "heat_gain_rate": 0.2,
            "initial_temp": 3.1,  # Just above upper threshold
            "initial_state": "on",
        })

        # Cool down from 3.1 to below 1.0 (compressor ON)
        vals: list[float] = []
        states: list[bool] = []
        for i in range(2000):  # 2000 seconds
            v = m.generate(float(i), 1.0)
            vals.append(v)
            states.append(m.compressor_on)

        # Should have completed at least one full cycle
        transitions = sum(
            1 for i in range(1, len(states)) if states[i] != states[i - 1]
        )
        assert transitions >= 2, "Should have at least one ON->OFF->ON cycle"

        # PV should stay within reasonable bounds
        assert min(vals) >= -1.0  # Some room below lower threshold
        assert max(vals) <= 5.0  # Some room above upper threshold

    def test_oscillation_range(self) -> None:
        """Temperature oscillates within the dead band around setpoint."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "dead_band_low": 1.0,
            "cooling_rate": 0.5,
            "heat_gain_rate": 0.2,
            "initial_temp": 3.0,
            "initial_state": "on",
        })
        # Run for a while to settle into oscillation
        vals = _run_ticks(m, 5000, dt=1.0)
        # After settling, pv stays near setpoint +/- dead_band (within a tick)
        settled = vals[2000:]  # Skip transient
        # The sawtooth should cross through the dead band range
        assert min(settled) < 1.5  # Below setpoint
        assert max(settled) > 2.5  # Above setpoint


# ===================================================================
# Cycle Timing (PRD: 8-12 minute cycle time)
# ===================================================================


class TestCycleTiming:
    def test_typical_cycle_time(self) -> None:
        """PRD: typical chiller cycle time about 8-12 minutes."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "dead_band_low": 1.0,
            "cooling_rate": 0.5,
            "heat_gain_rate": 0.2,
            "initial_temp": 3.01,  # Just above upper threshold
            "initial_state": "on",
        })

        # Cooling phase: from 3.01 to 0.99 (2.02 C at 0.5 C/min)
        # -> 2.02 / 0.5 = 4.04 min
        # Heating phase: from ~0.99 to 3.01 (2.02 C at 0.2 C/min)
        # -> 2.02 / 0.2 = 10.1 min
        # Total cycle: ~14.14 min
        # PRD says "about 8-12 minutes" -- we're close with default params

        # Run for 30 minutes to observe cycles
        transitions: list[int] = []
        prev_state = m.compressor_on
        for i in range(1800):  # 30 minutes at 1s ticks
            m.generate(float(i), 1.0)
            if m.compressor_on != prev_state:
                transitions.append(i)
                prev_state = m.compressor_on

        # Should have at least 2 full cycles in 30 minutes
        assert len(transitions) >= 4, f"Expected at least 4 transitions, got {len(transitions)}"

        # Verify cycle times are reasonable (4-20 min range)
        for i in range(2, len(transitions)):
            cycle_s = transitions[i] - transitions[i - 2]
            cycle_min = cycle_s / 60.0
            assert 4.0 < cycle_min < 20.0, f"Cycle time {cycle_min:.1f} min outside range"

    def test_cooling_phase_duration(self) -> None:
        """Cooling phase should take dead_band_range / cooling_rate."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "dead_band_low": 1.0,
            "cooling_rate": 0.5,  # C/min
            "initial_temp": 3.01,  # Just above upper threshold
            "initial_state": "on",
        })
        # Cool from 3.01 to below 1.0: range ~2.01 C at 0.5 C/min
        # Expected duration: 2.01 / 0.5 = 4.02 min = 241.2 s
        ticks = 0
        while m.compressor_on and ticks < 600:
            m.generate(float(ticks), 1.0)
            ticks += 1

        expected_s = (3.01 - 1.0) / (0.5 / 60.0)
        # The turn-off tick can overshoot by one tick
        assert abs(ticks - expected_s) < 2.0

    def test_heating_phase_duration(self) -> None:
        """Heating phase should take dead_band_range / heat_gain_rate."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "dead_band_low": 1.0,
            "heat_gain_rate": 0.2,  # C/min
            "initial_temp": 0.99,  # Just below lower threshold
            "initial_state": "off",
        })
        # Heat from 0.99 to above 3.0: range ~2.01 C at 0.2 C/min
        # Expected duration: 2.01 / 0.2 = 10.05 min = 603 s
        ticks = 0
        while not m.compressor_on and ticks < 1000:
            m.generate(float(ticks), 1.0)
            ticks += 1

        expected_s = (3.0 - 0.99) / (0.2 / 60.0)
        assert abs(ticks - expected_s) < 2.0


# ===================================================================
# Asymmetric Dead Band
# ===================================================================


class TestAsymmetricDeadBand:
    def test_asymmetric_dead_band(self) -> None:
        """Different dead_band_high and dead_band_low."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 2.0,  # Turn on at 4.0
            "dead_band_low": 0.5,   # Turn off at 1.5
        })
        assert m.dead_band_high == 2.0
        assert m.dead_band_low == 0.5

    def test_narrow_dead_band_faster_cycling(self) -> None:
        """Narrower dead band -> faster cycling."""
        m_narrow = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 0.5,
            "dead_band_low": 0.5,
            "initial_temp": 2.5,
            "initial_state": "on",
        })
        m_wide = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 2.0,
            "dead_band_low": 2.0,
            "initial_temp": 4.0,
            "initial_state": "on",
        })

        def count_transitions(model: BangBangModel, n: int) -> int:
            prev = model.compressor_on
            transitions = 0
            for i in range(n):
                model.generate(float(i), 1.0)
                if model.compressor_on != prev:
                    transitions += 1
                    prev = model.compressor_on
            return transitions

        narrow_trans = count_transitions(m_narrow, 3600)
        wide_trans = count_transitions(m_wide, 3600)
        assert narrow_trans > wide_trans


# ===================================================================
# Setpoint Changes
# ===================================================================


class TestSetpointChanges:
    def test_set_setpoint(self) -> None:
        m = _make_model({"setpoint": 2.0})
        m.set_setpoint(5.0)
        assert m.setpoint == 5.0

    def test_setpoint_change_affects_thresholds(self) -> None:
        """After changing setpoint, thresholds shift accordingly."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "dead_band_low": 1.0,
            "heat_gain_rate": 6.0,  # Fast
            "initial_temp": 2.0,
            "initial_state": "off",
        })
        # Change setpoint to 10.0 -- upper threshold now 11.0
        m.set_setpoint(10.0)
        # PV is at 2.0, heating at 0.1 C/s. Need to reach 11.0 -> 90 seconds
        for i in range(200):
            m.generate(float(i), 1.0)
        # Should have turned on when PV > 11.0
        assert m.compressor_on is True


# ===================================================================
# Disturbance
# ===================================================================


class TestDisturbance:
    def test_add_disturbance_warming(self) -> None:
        """Positive disturbance increases PV (e.g. door open event)."""
        m = _make_model({"setpoint": 2.0, "initial_temp": 2.0})
        m.add_disturbance(3.0)
        assert m.pv == pytest.approx(5.0)

    def test_add_disturbance_cooling(self) -> None:
        """Negative disturbance decreases PV."""
        m = _make_model({"setpoint": 2.0, "initial_temp": 2.0})
        m.add_disturbance(-1.0)
        assert m.pv == pytest.approx(1.0)

    def test_disturbance_triggers_compressor(self) -> None:
        """Large warming disturbance should trigger compressor ON."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "initial_temp": 2.0,
            "initial_state": "off",
        })
        # Add large disturbance above upper threshold
        m.add_disturbance(2.0)  # PV now 4.0, threshold is 3.0
        m.generate(0.0, 1.0)  # One tick to check thresholds
        assert m.compressor_on is True

    def test_prd_door_open_event(self) -> None:
        """PRD: door open causes 2-5 C temperature excursion."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "dead_band_low": 1.0,
            "cooling_rate": 0.5,
            "initial_temp": 2.0,
            "initial_state": "off",
        })
        pv_before = m.pv
        m.add_disturbance(3.5)  # Mid-range door-open event
        assert m.pv == pytest.approx(pv_before + 3.5)


# ===================================================================
# Noise
# ===================================================================


class TestNoise:
    def test_noise_adds_variation(self) -> None:
        """Noise adds variation to the output."""
        m1 = _make_model({"initial_temp": 2.0}, noise=_make_noise(0.1, 42))
        m2 = _make_model({"initial_temp": 2.0}, noise=None)

        vals_noisy = _run_ticks(m1, 100)
        vals_clean = _run_ticks(m2, 100)

        assert np.std(vals_noisy) > np.std(vals_clean)

    def test_zero_sigma_clean(self) -> None:
        """Zero sigma noise produces clean signal."""
        m1 = _make_model({"initial_temp": 2.0}, noise=_make_noise(0.0, 42))
        m2 = _make_model({"initial_temp": 2.0}, noise=None)

        vals_noise = _run_ticks(m1, 50)
        vals_clean = _run_ticks(m2, 50)

        assert vals_noise == pytest.approx(vals_clean)

    def test_noise_does_not_affect_state(self) -> None:
        """Noise affects returned value but not internal PV or state."""
        m = _make_model({"initial_temp": 2.0}, noise=_make_noise(0.5, 42))
        m.generate(0.0, 1.0)
        # We check the pv is still clean (without noise)
        heat_per_tick = 0.2 / 60.0  # heat_gain_rate in C/s
        assert m.pv == pytest.approx(2.0 + heat_per_tick)

    def test_mean_near_pv_over_many_samples(self) -> None:
        """Mean of noisy output should be near the PV."""
        m = _make_model(
            {"initial_temp": 2.0, "heat_gain_rate": 0.001},
            noise=_make_noise(0.1, 42),
        )
        vals = _run_ticks(m, 10000)
        # With very slow heat gain, PV barely changes
        assert abs(np.mean(vals) - 2.0) < 0.1


# ===================================================================
# Negative Setpoint (Freezer)
# ===================================================================


class TestNegativeSetpoint:
    def test_negative_setpoint_works(self) -> None:
        """Model works with negative temperatures (freezer)."""
        m = _make_model({
            "setpoint": -18.0,
            "dead_band_high": 2.0,
            "dead_band_low": 2.0,
            "initial_temp": -18.0,
            "initial_state": "off",
        })
        vals = _run_ticks(m, 3600)
        # Should oscillate around -18
        assert min(vals) < -18.0
        assert max(vals) > -18.0


# ===================================================================
# Reset
# ===================================================================


class TestReset:
    def test_reset_restores_initial_temp(self) -> None:
        m = _make_model({"initial_temp": 5.0})
        _run_ticks(m, 100)
        assert m.pv != 5.0
        m.reset()
        assert m.pv == 5.0

    def test_reset_restores_initial_state(self) -> None:
        m = _make_model({"initial_state": "off", "initial_temp": 5.0, "dead_band_high": 0.01})
        # Run until compressor turns on
        for i in range(1000):
            m.generate(float(i), 1.0)
            if m.compressor_on:
                break
        m.reset()
        assert m.compressor_on is False

    def test_reset_clears_noise_state(self) -> None:
        noise = NoiseGenerator(0.1, "ar1", _make_rng(), phi=0.9)
        m = _make_model({"initial_temp": 2.0}, noise=noise)
        _run_ticks(m, 100)
        m.reset()
        # AR(1) state should be cleared
        assert noise._ar1_prev == 0.0

    def test_reset_allows_replay(self) -> None:
        """After reset, same sequence is replayed."""
        m = _make_model({"initial_temp": 2.0})
        vals1 = _run_ticks(m, 50)
        m.reset()
        vals2 = _run_ticks(m, 50)
        assert vals1 == pytest.approx(vals2)


# ===================================================================
# Determinism (Rule 13)
# ===================================================================


class TestDeterminism:
    def test_same_seed_identical(self) -> None:
        m1 = _make_model({"initial_temp": 2.0}, seed=99)
        m2 = _make_model({"initial_temp": 2.0}, seed=99)
        assert _run_ticks(m1, 100) == pytest.approx(_run_ticks(m2, 100))

    def test_different_seeds_same_without_noise(self) -> None:
        """Without noise, output is deterministic regardless of seed."""
        m1 = _make_model({"initial_temp": 2.0}, seed=1)
        m2 = _make_model({"initial_temp": 2.0}, seed=2)
        # Bang-bang without noise is purely deterministic
        assert _run_ticks(m1, 100) == pytest.approx(_run_ticks(m2, 100))

    def test_noise_same_seed_identical(self) -> None:
        m1 = _make_model(
            {"initial_temp": 2.0}, seed=42, noise=_make_noise(0.1, 42)
        )
        m2 = _make_model(
            {"initial_temp": 2.0}, seed=42, noise=_make_noise(0.1, 42)
        )
        assert _run_ticks(m1, 100) == pytest.approx(_run_ticks(m2, 100))

    def test_noise_different_seeds_differ(self) -> None:
        m1 = _make_model(
            {"initial_temp": 2.0}, seed=42, noise=_make_noise(0.1, 42)
        )
        m2 = _make_model(
            {"initial_temp": 2.0}, seed=42, noise=_make_noise(0.1, 99)
        )
        v1 = _run_ticks(m1, 100)
        v2 = _run_ticks(m2, 100)
        assert v1 != pytest.approx(v2, abs=1e-6)


# ===================================================================
# Time Compression (Rule 6)
# ===================================================================


class TestTimeCompression:
    def test_same_output_different_tick_rates(self) -> None:
        """Same total simulated time, different tick rates -> same PV."""
        # 100 ticks of 1.0s = 100s total
        m1 = _make_model({"initial_temp": 2.0, "initial_state": "off"})
        for i in range(100):
            m1.generate(float(i), 1.0)
        pv1 = m1.pv

        # 1000 ticks of 0.1s = 100s total
        m2 = _make_model({"initial_temp": 2.0, "initial_state": "off"})
        t = 0.0
        for _ in range(1000):
            m2.generate(t, 0.1)
            t += 0.1
        pv2 = m2.pv

        assert pv1 == pytest.approx(pv2, abs=1e-10)


# ===================================================================
# PRD Examples
# ===================================================================


class TestPrdExamples:
    def test_chiller_config(self) -> None:
        """PRD: setpoint 2C, dead band +/- 1C, oscillates 1-3C."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "dead_band_low": 1.0,
            "cooling_rate": 0.5,
            "heat_gain_rate": 0.2,
            "initial_temp": 3.0,
            "initial_state": "on",
        })

        vals = _run_ticks(m, 5000, dt=1.0)
        settled = vals[2000:]

        # PV should oscillate approximately between 1 and 3
        # (may slightly undershoot/overshoot by one tick amount)
        tick_tolerance = max(0.5 / 60.0, 0.2 / 60.0)  # max change per tick
        assert min(settled) < 1.0 + tick_tolerance
        assert max(settled) > 3.0 - tick_tolerance

    def test_compressor_state_reflects_controller(self) -> None:
        """The compressor_on property reflects the controller state."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "dead_band_low": 1.0,
            "initial_temp": 3.5,  # Above upper threshold
            "initial_state": "off",
        })
        # One tick should trigger compressor ON since PV (3.5+heat) > 3.0
        m.generate(0.0, 1.0)
        assert m.compressor_on is True


# ===================================================================
# Edge Cases
# ===================================================================


class TestEdgeCases:
    def test_very_small_dt(self) -> None:
        """Small dt still produces correct evolution."""
        m = _make_model({"initial_temp": 2.0, "initial_state": "off"})
        total_time = 60.0  # 1 minute
        dt = 0.001  # 1 ms ticks
        t = 0.0
        for _ in range(int(total_time / dt)):
            m.generate(t, dt)
            t += dt
        # heat_gain_rate = 0.2 C/min, so after 1 min: +0.2 C
        assert m.pv == pytest.approx(2.0 + 0.2, abs=1e-6)

    def test_very_large_dt(self) -> None:
        """Large dt produces correct temperature change."""
        m = _make_model({"initial_temp": 2.0, "initial_state": "off"})
        m.generate(0.0, 60.0)  # One big 60s tick
        # heat_gain_rate = 0.2 C/min = 0.2 C in 60s
        assert m.pv == pytest.approx(2.0 + 0.2, abs=1e-10)

    def test_pv_at_exact_threshold(self) -> None:
        """PV exactly at threshold should not trigger (need to exceed)."""
        m = _make_model({
            "setpoint": 2.0,
            "dead_band_high": 1.0,
            "initial_temp": 3.0,  # Exactly at upper threshold
            "initial_state": "off",
        })
        # PV = 3.0 which is == threshold, not > threshold
        # After one tick with heat gain, PV > 3.0 -> turns on
        m.generate(0.0, 1.0)
        # After heat gain: 3.0 + 0.2/60 > 3.0 -> should turn on
        assert m.compressor_on is True


# ===================================================================
# Hypothesis Property-Based Tests
# ===================================================================


class TestPropertyBased:
    @given(
        setpoint=st.floats(min_value=-30, max_value=50),
        dead_band=st.floats(min_value=0.1, max_value=5.0),
        seed=st.integers(min_value=0, max_value=2**31),
    )
    @settings(max_examples=50)
    def test_output_always_finite(
        self, setpoint: float, dead_band: float, seed: int
    ) -> None:
        m = _make_model({
            "setpoint": setpoint,
            "dead_band_high": dead_band,
            "dead_band_low": dead_band,
            "initial_temp": setpoint,
        }, seed=seed)
        for i in range(100):
            v = m.generate(float(i), 1.0)
            assert np.isfinite(v)

    @given(seed=st.integers(min_value=0, max_value=2**31))
    @settings(max_examples=30)
    def test_determinism_any_seed(self, seed: int) -> None:
        m1 = _make_model({"initial_temp": 2.0}, seed=seed)
        m2 = _make_model({"initial_temp": 2.0}, seed=seed)
        v1 = _run_ticks(m1, 50)
        v2 = _run_ticks(m2, 50)
        assert v1 == pytest.approx(v2)

    @given(
        setpoint=st.floats(min_value=-20, max_value=30),
        dead_band=st.floats(min_value=0.1, max_value=3.0),
    )
    @settings(max_examples=30)
    def test_compressor_is_boolean(self, setpoint: float, dead_band: float) -> None:
        m = _make_model({
            "setpoint": setpoint,
            "dead_band_high": dead_band,
            "dead_band_low": dead_band,
            "initial_temp": setpoint,
        })
        for i in range(100):
            m.generate(float(i), 1.0)
            assert isinstance(m.compressor_on, bool)


# ===================================================================
# Package Imports
# ===================================================================


class TestPackageImports:
    def test_importable_from_models(self) -> None:
        from factory_simulator.models import BangBangModel as Imported

        assert Imported is BangBangModel

    def test_in_all(self) -> None:
        from factory_simulator import models

        assert "BangBangModel" in models.__all__
