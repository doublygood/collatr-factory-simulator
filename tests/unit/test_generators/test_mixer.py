"""Unit tests for the MixerGenerator (PRD 2b.2).

Tests verify:
- State machine transitions and cascade effects
- Speed ramp to target during Mixing, hold during Holding
- Torque correlates with speed
- Batch temperature tracks setpoint via first-order lag
- Batch weight ramps during Loading/Discharging
- Batch ID string generation
- Mix time elapsed counter
- Lid closed state
- Determinism (same seed → same output)

Task 3.4
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.mixer import (
    STATE_OFF,
    MixerGenerator,
)
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mixer_config(
    *,
    target_speed: float = 2000.0,
    speed_range: list[float] | None = None,
) -> EquipmentConfig:
    """Create a minimal mixer config for testing."""
    if speed_range is None:
        speed_range = [0.0, 3000.0]

    signals: dict[str, SignalConfig] = {}

    signals["speed"] = SignalConfig(
        model="ramp",
        noise_sigma=10.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=3000.0,
        units="RPM",
        params={"ramp_duration_s": 30},
    )
    signals["torque"] = SignalConfig(
        model="correlated_follower",
        noise_sigma=1.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=100.0,
        units="%",
        params={"base": 5.0, "factor": 0.03},
    )
    signals["batch_temp"] = SignalConfig(
        model="first_order_lag",
        noise_sigma=0.3,
        noise_type="ar1",
        noise_phi=0.7,
        sample_rate_ms=5000,
        min_clamp=-5.0,
        max_clamp=95.0,
        units="C",
        params={"tau": 300.0, "initial_value": 4.0},
    )
    signals["batch_weight"] = SignalConfig(
        model="ramp",
        noise_sigma=2.0,
        sample_rate_ms=5000,
        min_clamp=0.0,
        max_clamp=2000.0,
        units="kg",
        params={"ramp_duration_s": 60},
    )
    signals["state"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=5.0,
        units="enum",
        params={
            "states": ["off", "loading", "mixing", "holding",
                       "discharging", "cip"],
            "initial_state": "off",
        },
    )
    signals["batch_id"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=0.0,
        units="",
        params={"target": 0.0},
    )
    signals["mix_time_elapsed"] = SignalConfig(
        model="counter",
        sample_rate_ms=5000,
        min_clamp=0.0,
        max_clamp=3600.0,
        units="s",
        params={"rate": 1.0, "rollover": 3600},
    )
    signals["lid_closed"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=1.0,
        units="bool",
        params={"states": ["open", "closed"], "initial_state": "closed"},
    )

    return EquipmentConfig(
        enabled=True,
        type="high_shear_mixer",
        signals=signals,
        target_speed=target_speed,
        speed_range=speed_range,
    )


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise KeyError(f"Signal {signal_id} not found in results")


def _run_ticks(
    gen: MixerGenerator,
    store: SignalStore,
    *,
    n_ticks: int,
    dt: float = 0.1,
    start_time: float = 0.0,
) -> list[list[SignalValue]]:
    """Run generator for n_ticks, return list of result lists."""
    all_results: list[list[SignalValue]] = []
    sim_time = start_time
    for _ in range(n_ticks):
        sim_time += dt
        results = gen.generate(sim_time, dt, store)
        for sv in results:
            store.set(sv.signal_id, sv.value, sv.timestamp, sv.quality)
        all_results.append(results)
    return all_results


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)


@pytest.fixture
def store() -> SignalStore:
    return SignalStore()


@pytest.fixture
def mixer(rng: np.random.Generator) -> MixerGenerator:
    return MixerGenerator("mixer", _make_mixer_config(), rng)


# ---------------------------------------------------------------------------
# Tests: signal IDs
# ---------------------------------------------------------------------------


class TestSignalIds:
    """Verify all 8 mixer signals are registered."""

    def test_signal_count(self, mixer: MixerGenerator) -> None:
        assert len(mixer.get_signal_ids()) == 8

    def test_signal_names(self, mixer: MixerGenerator) -> None:
        ids = set(mixer.get_signal_ids())
        expected = {
            "mixer.speed", "mixer.torque", "mixer.batch_temp",
            "mixer.batch_weight", "mixer.state", "mixer.batch_id",
            "mixer.mix_time_elapsed", "mixer.lid_closed",
        }
        assert ids == expected


# ---------------------------------------------------------------------------
# Tests: initial state
# ---------------------------------------------------------------------------


class TestInitialState:
    """Mixer starts in Off state with appropriate initial values."""

    def test_initial_state_off(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        results = mixer.generate(0.1, 0.1, store)
        state_sv = _find_signal(results, "mixer.state")
        assert int(state_sv.value) == STATE_OFF

    def test_initial_speed_zero(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        results = mixer.generate(0.1, 0.1, store)
        speed_sv = _find_signal(results, "mixer.speed")
        assert speed_sv.value == 0.0

    def test_lid_initially_closed(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        results = mixer.generate(0.1, 0.1, store)
        lid_sv = _find_signal(results, "mixer.lid_closed")
        assert lid_sv.value == 1.0


# ---------------------------------------------------------------------------
# Tests: state transitions and cascade
# ---------------------------------------------------------------------------


class TestStateTransitions:
    """State transitions trigger correct cascade effects."""

    def test_loading_starts_weight_ramp(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Force to Loading state, weight should increase."""
        # First tick in Off
        mixer.generate(0.1, 0.1, store)

        # Transition to Loading
        mixer.state_machine.force_state("Loading")

        # Run several ticks
        results_list = _run_ticks(mixer, store, n_ticks=50, dt=0.1, start_time=0.1)

        # Weight should be increasing
        weights = [
            _find_signal(r, "mixer.batch_weight").value
            for r in results_list
        ]
        # Check that weight at end > weight at start
        assert weights[-1] > weights[0], (
            f"Weight should increase during Loading: {weights[0]:.1f} → {weights[-1]:.1f}"
        )

    def test_mixing_ramps_speed(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Force to Mixing state, speed should ramp toward target."""
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Loading")
        _run_ticks(mixer, store, n_ticks=10, dt=0.1, start_time=0.1)

        mixer.state_machine.force_state("Mixing")
        results_list = _run_ticks(mixer, store, n_ticks=100, dt=0.1, start_time=1.1)

        speeds = [
            _find_signal(r, "mixer.speed").value
            for r in results_list
        ]
        # Speed should increase over time
        assert speeds[-1] > speeds[0], (
            f"Speed should ramp up during Mixing: {speeds[0]:.0f} → {speeds[-1]:.0f}"
        )

    def test_holding_drops_speed(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Force to Holding after Mixing, speed should drop."""
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")
        # Run enough for speed to ramp up
        _run_ticks(mixer, store, n_ticks=350, dt=0.1, start_time=0.1)

        # Check speed is high
        speed_before = _find_signal(
            mixer.generate(35.2, 0.1, store), "mixer.speed",
        ).value
        assert speed_before > 500.0, f"Speed should be high before holding: {speed_before}"

        # Transition to Holding
        mixer.state_machine.force_state("Holding")
        results_list = _run_ticks(mixer, store, n_ticks=200, dt=0.1, start_time=35.3)

        speeds = [
            _find_signal(r, "mixer.speed").value
            for r in results_list
        ]
        # Speed should decrease
        assert speeds[-1] < speed_before, (
            f"Speed should drop during Holding: {speed_before:.0f} → {speeds[-1]:.0f}"
        )

    def test_discharging_weight_decreases(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Weight decreases during Discharging."""
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Loading")
        # Load for a while to build up weight
        _run_ticks(mixer, store, n_ticks=500, dt=0.1, start_time=0.1)

        weight_before = _find_signal(
            mixer.generate(50.2, 0.1, store), "mixer.batch_weight",
        ).value

        mixer.state_machine.force_state("Discharging")
        results_list = _run_ticks(mixer, store, n_ticks=200, dt=0.1, start_time=50.3)

        weights = [
            _find_signal(r, "mixer.batch_weight").value
            for r in results_list
        ]
        assert weights[-1] < weight_before, (
            f"Weight should decrease during Discharging: {weight_before:.1f} → {weights[-1]:.1f}"
        )

    def test_off_resets_speed_to_zero(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Transitioning to Off ramps speed down to zero."""
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")
        _run_ticks(mixer, store, n_ticks=350, dt=0.1, start_time=0.1)

        mixer.state_machine.force_state("Off")
        results_list = _run_ticks(mixer, store, n_ticks=200, dt=0.1, start_time=35.1)

        final_speed = _find_signal(results_list[-1], "mixer.speed").value
        assert final_speed == 0.0, f"Speed should be 0 when Off: {final_speed}"

    def test_cip_state(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """CIP state ramps speed down."""
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")
        _run_ticks(mixer, store, n_ticks=50, dt=0.1, start_time=0.1)

        mixer.state_machine.force_state("Cip")
        results_list = _run_ticks(mixer, store, n_ticks=200, dt=0.1, start_time=5.1)

        final_speed = _find_signal(results_list[-1], "mixer.speed").value
        assert final_speed == 0.0, f"Speed should be 0 during CIP: {final_speed}"


# ---------------------------------------------------------------------------
# Tests: torque correlates with speed
# ---------------------------------------------------------------------------


class TestTorqueCorrelation:
    """Torque should follow speed via correlated follower."""

    def test_torque_increases_with_speed(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """When speed increases, torque should increase."""
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")

        # Run to let speed ramp up
        results_list = _run_ticks(mixer, store, n_ticks=350, dt=0.1, start_time=0.1)

        # Torque at start vs end
        torque_start = _find_signal(results_list[0], "mixer.torque").value
        torque_end = _find_signal(results_list[-1], "mixer.torque").value

        assert torque_end > torque_start, (
            f"Torque should increase with speed: {torque_start:.1f} → {torque_end:.1f}"
        )

    def test_torque_zero_when_off(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """When Off and speed=0, torque should be near base value."""
        results = mixer.generate(0.1, 0.1, store)
        torque = _find_signal(results, "mixer.torque").value
        # base=5.0, gain=0.03, speed=0 → torque ≈ 5.0 + noise
        assert torque < 15.0, f"Torque should be low when Off: {torque}"


# ---------------------------------------------------------------------------
# Tests: batch temperature
# ---------------------------------------------------------------------------


class TestBatchTemperature:
    """Batch temperature tracks setpoint via first-order lag."""

    def test_temp_starts_at_initial(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Initial batch temp should be near the configured initial value."""
        results = mixer.generate(0.1, 0.1, store)
        temp = _find_signal(results, "mixer.batch_temp").value
        # initial_value = 4.0 ± noise
        assert -5.0 <= temp <= 15.0, f"Initial temp unexpected: {temp}"

    def test_temp_rises_during_mixing(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """During Mixing, temp should rise toward cooking setpoint."""
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")

        results_list = _run_ticks(mixer, store, n_ticks=500, dt=0.1, start_time=0.1)

        temps = [
            _find_signal(r, "mixer.batch_temp").value
            for r in results_list
        ]
        # Temperature should increase
        assert temps[-1] > temps[0], (
            f"Temp should rise during Mixing: {temps[0]:.1f} → {temps[-1]:.1f}"
        )


# ---------------------------------------------------------------------------
# Tests: batch ID string
# ---------------------------------------------------------------------------


class TestBatchId:
    """Batch ID is a formatted string."""

    def test_batch_id_is_string(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """batch_id should produce a string value."""
        results = mixer.generate(0.1, 0.1, store)
        batch_id = _find_signal(results, "mixer.batch_id")
        assert isinstance(batch_id.value, str)

    def test_batch_id_increments_on_loading(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Each Loading transition should increment batch sequence."""
        mixer.generate(0.1, 0.1, store)
        seq_before = mixer.batch_id_model.sequence

        mixer.state_machine.force_state("Loading")
        mixer.generate(0.2, 0.1, store)

        assert mixer.batch_id_model.sequence == seq_before + 1


# ---------------------------------------------------------------------------
# Tests: mix time elapsed
# ---------------------------------------------------------------------------


class TestMixTimeElapsed:
    """Mix time counter increments only during active mixing."""

    def test_mix_time_zero_when_off(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Mix time should not increment when Off."""
        results_list = _run_ticks(mixer, store, n_ticks=10, dt=0.1)
        mix_time = _find_signal(results_list[-1], "mixer.mix_time_elapsed").value
        assert mix_time == 0.0

    def test_mix_time_increments_during_mixing(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Mix time should increment during Mixing state."""
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")

        results_list = _run_ticks(mixer, store, n_ticks=100, dt=0.1, start_time=0.1)
        mix_time = _find_signal(results_list[-1], "mixer.mix_time_elapsed").value
        # 100 ticks * 0.1s * rate=1.0 * speed=1.0 = 10.0
        assert mix_time > 5.0, f"Mix time should increment: {mix_time}"

    def test_mix_time_resets_on_loading(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        """Mix time should reset when entering Loading (new batch)."""
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")
        _run_ticks(mixer, store, n_ticks=50, dt=0.1, start_time=0.1)

        mixer.state_machine.force_state("Loading")
        results_list = _run_ticks(mixer, store, n_ticks=10, dt=0.1, start_time=5.1)
        mix_time = _find_signal(results_list[-1], "mixer.mix_time_elapsed").value
        # Should have reset to 0 (not incrementing during Loading)
        assert mix_time == 0.0


# ---------------------------------------------------------------------------
# Tests: all signals present on every tick
# ---------------------------------------------------------------------------


class TestAllSignals:
    """Every tick produces exactly 8 signals."""

    def test_signal_count_per_tick(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        results = mixer.generate(0.1, 0.1, store)
        # batch_id is a string, the rest are numeric
        assert len(results) == 8

    def test_all_signals_have_quality_good(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        results = mixer.generate(0.1, 0.1, store)
        for sv in results:
            assert sv.quality == "good"


# ---------------------------------------------------------------------------
# Tests: bounds respected
# ---------------------------------------------------------------------------


class TestBounds:
    """Signal values respect min/max clamp."""

    def test_speed_within_bounds(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")
        results_list = _run_ticks(mixer, store, n_ticks=500, dt=0.1, start_time=0.1)
        for r in results_list:
            speed = _find_signal(r, "mixer.speed").value
            assert 0.0 <= speed <= 3000.0, f"Speed out of bounds: {speed}"

    def test_torque_within_bounds(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")
        results_list = _run_ticks(mixer, store, n_ticks=500, dt=0.1, start_time=0.1)
        for r in results_list:
            torque = _find_signal(r, "mixer.torque").value
            assert 0.0 <= torque <= 100.0, f"Torque out of bounds: {torque}"

    def test_batch_temp_within_bounds(
        self, mixer: MixerGenerator, store: SignalStore,
    ) -> None:
        mixer.generate(0.1, 0.1, store)
        mixer.state_machine.force_state("Mixing")
        results_list = _run_ticks(mixer, store, n_ticks=500, dt=0.1, start_time=0.1)
        for r in results_list:
            temp = _find_signal(r, "mixer.batch_temp").value
            assert -5.0 <= temp <= 95.0, f"Batch temp out of bounds: {temp}"


# ---------------------------------------------------------------------------
# Tests: determinism (CLAUDE.md Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed → identical output sequence."""

    def test_mixer_deterministic(self, store: SignalStore) -> None:
        cfg = _make_mixer_config()
        gen1 = MixerGenerator("mixer", cfg, np.random.default_rng(99))
        gen2 = MixerGenerator("mixer", cfg, np.random.default_rng(99))

        sim_time = 0.0
        dt = 0.1
        for _ in range(50):
            sim_time += dt
            r1 = gen1.generate(sim_time, dt, store)
            r2 = gen2.generate(sim_time, dt, store)

        for sv1, sv2 in zip(r1, r2, strict=True):
            assert sv1.signal_id == sv2.signal_id
            assert sv1.value == sv2.value, (
                f"{sv1.signal_id}: {sv1.value} != {sv2.value}"
            )


# ---------------------------------------------------------------------------
# Tests: protocol mappings
# ---------------------------------------------------------------------------


class TestProtocolMappings:
    """Protocol mappings are derived from config."""

    def test_modbus_mappings_from_config(self) -> None:
        """Modbus signals should have modbus mappings when configured."""
        signals: dict[str, SignalConfig] = {}
        signals["speed"] = SignalConfig(
            model="ramp",
            noise_sigma=10.0,
            min_clamp=0.0,
            max_clamp=3000.0,
            modbus_hr=[1000, 1001],
            modbus_type="float32",
            params={"ramp_duration_s": 30},
        )
        signals["state"] = SignalConfig(
            model="state_machine",
            min_clamp=0.0,
            max_clamp=5.0,
            opcua_node="FoodBevLine.Mixer1.State",
            opcua_type="UInt16",
            params={
                "states": ["off", "loading", "mixing", "holding",
                           "discharging", "cip"],
                "initial_state": "off",
            },
        )
        # Need all 8 signal names for get_signal_ids
        for name in ["torque", "batch_temp", "batch_weight",
                      "batch_id", "mix_time_elapsed", "lid_closed"]:
            signals[name] = SignalConfig(
                model="steady_state",
                min_clamp=0.0,
                max_clamp=100.0,
                params={"target": 0.0},
            )

        cfg = EquipmentConfig(
            enabled=True,
            type="high_shear_mixer",
            signals=signals,
            target_speed=2000.0,
        )
        gen = MixerGenerator("mixer", cfg, np.random.default_rng(42))
        mappings = gen.get_protocol_mappings()

        assert "mixer.speed" in mappings
        assert mappings["mixer.speed"].modbus is not None
        assert mappings["mixer.speed"].modbus.address == [1000, 1001]

        assert "mixer.state" in mappings
        assert mappings["mixer.state"].opcua is not None
        assert mappings["mixer.state"].opcua.node_id == "FoodBevLine.Mixer1.State"
