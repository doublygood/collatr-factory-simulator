"""Tests for PressGenerator -- 21 signals, state machine cascade.

Verifies:
- All 21 signal IDs are produced.
- State transitions cascade correctly (speed, counters, dryers).
- Running state increments counters and ramps speed.
- Fault state zeroes speed immediately.
- Deterministic output with same seed.

PRD Reference: Section 2.2 (Press equipment)
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.press import (
    STATE_IDLE,
    PressGenerator,
)
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_press_config() -> EquipmentConfig:
    """Build a minimal press EquipmentConfig for testing.

    Mirrors the structure in config/factory.yaml but uses shorter
    timers for test speed.
    """
    signals: dict[str, SignalConfig] = {}

    # --- line_speed ---
    signals["line_speed"] = SignalConfig(
        model="ramp",
        noise_sigma=0.5,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=400.0,
        units="m/min",
        modbus_hr=[100, 101],
        modbus_type="float32",
        params={"ramp_duration_s": 10.0},  # short for tests
    )

    # --- web_tension ---
    signals["web_tension"] = SignalConfig(
        model="correlated_follower",
        parent="press.line_speed",
        transform="linear",
        noise_sigma=5.0,
        sample_rate_ms=500,
        min_clamp=0.0,
        max_clamp=500.0,
        params={"base": 80.0, "factor": 0.5},
    )

    # --- registration_error_x ---
    signals["registration_error_x"] = SignalConfig(
        model="random_walk",
        noise_sigma=0.02,
        sample_rate_ms=500,
        min_clamp=-0.5,
        max_clamp=0.5,
        params={"center": 0.0, "drift_rate": 0.01, "reversion_rate": 0.1},
    )

    # --- registration_error_y ---
    signals["registration_error_y"] = SignalConfig(
        model="random_walk",
        noise_sigma=0.02,
        sample_rate_ms=500,
        min_clamp=-0.5,
        max_clamp=0.5,
        params={"center": 0.0, "drift_rate": 0.01, "reversion_rate": 0.1},
    )

    # --- ink_viscosity ---
    signals["ink_viscosity"] = SignalConfig(
        model="steady_state",
        noise_sigma=1.5,
        sample_rate_ms=30000,
        min_clamp=15.0,
        max_clamp=60.0,
        params={"target": 28.0},
    )

    # --- ink_temperature ---
    signals["ink_temperature"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.5,
        sample_rate_ms=10000,
        min_clamp=18.0,
        max_clamp=35.0,
        params={"target": 25.0},
    )

    # --- dryer_temp_zone_1 ---
    signals["dryer_temp_zone_1"] = SignalConfig(
        model="first_order_lag",
        noise_sigma=0.8,
        noise_type="ar1",
        noise_phi=0.7,
        sample_rate_ms=5000,
        min_clamp=20.0,
        max_clamp=150.0,
        params={"tau": 10.0, "initial_value": 20.0},  # short tau for tests
    )

    # --- dryer_temp_zone_2 ---
    signals["dryer_temp_zone_2"] = SignalConfig(
        model="first_order_lag",
        noise_sigma=0.8,
        noise_type="ar1",
        noise_phi=0.7,
        sample_rate_ms=5000,
        min_clamp=20.0,
        max_clamp=150.0,
        params={"tau": 10.0, "initial_value": 20.0},
    )

    # --- dryer_temp_zone_3 ---
    signals["dryer_temp_zone_3"] = SignalConfig(
        model="first_order_lag",
        noise_sigma=0.8,
        noise_type="ar1",
        noise_phi=0.7,
        sample_rate_ms=5000,
        min_clamp=20.0,
        max_clamp=150.0,
        params={"tau": 10.0, "initial_value": 20.0},
    )

    # --- dryer_setpoint_zone_1 ---
    signals["dryer_setpoint_zone_1"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        min_clamp=40.0,
        max_clamp=120.0,
        params={"target": 75.0},
    )

    # --- dryer_setpoint_zone_2 ---
    signals["dryer_setpoint_zone_2"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        min_clamp=40.0,
        max_clamp=120.0,
        params={"target": 80.0},
    )

    # --- dryer_setpoint_zone_3 ---
    signals["dryer_setpoint_zone_3"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        min_clamp=40.0,
        max_clamp=120.0,
        params={"target": 85.0},
    )

    # --- impression_count ---
    signals["impression_count"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=999999999.0,
        params={"rate": 1.0, "rollover": 999999999},
    )

    # --- good_count ---
    signals["good_count"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=999999999.0,
        params={"rate": 0.97, "rollover": 999999999},
    )

    # --- waste_count ---
    signals["waste_count"] = SignalConfig(
        model="counter",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=99999.0,
        params={"rate": 0.03, "rollover": 99999},
    )

    # --- machine_state ---
    signals["machine_state"] = SignalConfig(
        model="state_machine",
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=5.0,
        modbus_hr=[210],
        modbus_type="uint16",
        opcua_node="PackagingLine.Press1.State",
        opcua_type="UInt16",
        params={
            "states": ["off", "setup", "running", "idle", "fault", "maintenance"],
            "initial_state": "idle",
        },
    )

    # --- fault_code (scenario-managed, defaults to 0) ---
    signals["fault_code"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=999.0,
        modbus_hr=[211],
        modbus_type="uint16",
        params={"target": 0.0},
    )

    # --- main_drive_current ---
    signals["main_drive_current"] = SignalConfig(
        model="correlated_follower",
        parent="press.line_speed",
        transform="linear",
        noise_sigma=2.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=200.0,
        params={"base": 15.0, "factor": 0.35},
    )

    # --- main_drive_speed ---
    signals["main_drive_speed"] = SignalConfig(
        model="correlated_follower",
        parent="press.line_speed",
        transform="linear",
        noise_sigma=5.0,
        sample_rate_ms=1000,
        min_clamp=0.0,
        max_clamp=3000.0,
        params={"base": 0.0, "factor": 7.5},
    )

    # --- nip_pressure ---
    signals["nip_pressure"] = SignalConfig(
        model="steady_state",
        noise_sigma=0.2,
        sample_rate_ms=5000,
        min_clamp=0.0,
        max_clamp=10.0,
        params={"target": 4.5},
    )

    # --- unwind_diameter ---
    signals["unwind_diameter"] = SignalConfig(
        model="depletion",
        noise_sigma=1.0,
        sample_rate_ms=10000,
        min_clamp=50.0,
        max_clamp=1500.0,
        params={
            "initial_value": 1200.0,
            "consumption_rate": 0.1,
            "refill_threshold": 100.0,
            "refill_value": 1200.0,
        },
    )

    # --- rewind_diameter ---
    signals["rewind_diameter"] = SignalConfig(
        model="counter",
        noise_sigma=1.0,
        sample_rate_ms=10000,
        min_clamp=50.0,
        max_clamp=1500.0,
        params={"rate": 0.1, "initial_value": 76.0, "rollover": 1500},
    )

    return EquipmentConfig(
        enabled=True,
        type="flexographic_press",
        signals=signals,
        target_speed=200,
        speed_range=[50, 400],
    )


@pytest.fixture
def press_config() -> EquipmentConfig:
    return _make_press_config()


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(42)


@pytest.fixture
def store() -> SignalStore:
    return SignalStore()


@pytest.fixture
def press(press_config: EquipmentConfig, rng: np.random.Generator) -> PressGenerator:
    return PressGenerator("press", press_config, rng)


# ---------------------------------------------------------------------------
# Signal ID completeness
# ---------------------------------------------------------------------------


EXPECTED_SIGNAL_IDS = sorted([
    "press.line_speed",
    "press.web_tension",
    "press.registration_error_x",
    "press.registration_error_y",
    "press.ink_viscosity",
    "press.ink_temperature",
    "press.dryer_temp_zone_1",
    "press.dryer_temp_zone_2",
    "press.dryer_temp_zone_3",
    "press.dryer_setpoint_zone_1",
    "press.dryer_setpoint_zone_2",
    "press.dryer_setpoint_zone_3",
    "press.impression_count",
    "press.good_count",
    "press.waste_count",
    "press.machine_state",
    "press.fault_code",
    "press.main_drive_current",
    "press.main_drive_speed",
    "press.nip_pressure",
    "press.unwind_diameter",
    "press.rewind_diameter",
])


class TestSignalIds:
    """All 22 press signal IDs are produced."""

    def test_get_signal_ids_count(self, press: PressGenerator) -> None:
        ids = press.get_signal_ids()
        assert len(ids) == 22, f"Expected 22 signal IDs, got {len(ids)}"

    def test_get_signal_ids_complete(self, press: PressGenerator) -> None:
        ids = sorted(press.get_signal_ids())
        assert ids == EXPECTED_SIGNAL_IDS

    def test_generate_produces_all_signals(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        results = press.generate(0.0, 0.1, store)
        produced_ids = sorted(sv.signal_id for sv in results)
        assert produced_ids == EXPECTED_SIGNAL_IDS

    def test_generate_produces_signal_values(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        results = press.generate(0.0, 0.1, store)
        for sv in results:
            assert sv.quality == "good"
            assert sv.timestamp == 0.0
            assert isinstance(sv.value, float | int)


# ---------------------------------------------------------------------------
# State cascade
# ---------------------------------------------------------------------------


class TestStateCascade:
    """State transitions cascade to speed, counters, dryers."""

    def test_initial_state_is_idle(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        results = press.generate(0.0, 0.1, store)
        state_sv = _find_signal(results, "press.machine_state")
        assert int(state_sv.value) == STATE_IDLE

    def test_idle_speed_is_zero(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        results = press.generate(0.0, 0.1, store)
        speed_sv = _find_signal(results, "press.line_speed")
        # In idle, speed should be 0 (clamped)
        assert speed_sv.value == pytest.approx(0.0, abs=1.0)

    def test_force_running_starts_ramp(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """Forcing Running state starts speed ramp toward target."""
        press.state_machine.force_state("Running")

        # Run several ticks to let ramp progress
        sim_time = 0.0
        dt = 0.1
        speed = 0.0
        for _ in range(100):  # 10 seconds of simulation
            sim_time += dt
            results = press.generate(sim_time, dt, store)
            speed_sv = _find_signal(results, "press.line_speed")
            speed = speed_sv.value

        # Speed should be increasing (ramp in progress)
        assert speed > 0.0, "Speed should increase when Running"

    def test_running_to_target_speed(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """After full ramp duration, speed should reach target."""
        press.state_machine.force_state("Running")

        # Ramp duration is 10s in test config
        sim_time = 0.0
        dt = 0.1
        for _ in range(200):  # 20 seconds (well past 10s ramp)
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        speed_sv = _find_signal(results, "press.line_speed")
        # Should be near target speed (200 m/min) ± noise
        assert speed_sv.value == pytest.approx(200.0, abs=5.0)

    def test_fault_zeroes_speed(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """Fault state ramps speed to zero quickly."""
        # First get to Running with some speed
        press.state_machine.force_state("Running")
        sim_time = 0.0
        dt = 0.1
        for _ in range(150):  # 15s, past ramp
            sim_time += dt
            press.generate(sim_time, dt, store)

        # Now force Fault
        press.state_machine.force_state("Fault")

        # Run for 30+ seconds (fault ramp-down duration)
        for _ in range(400):
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        speed_sv = _find_signal(results, "press.line_speed")
        assert speed_sv.value == pytest.approx(0.0, abs=1.0), \
            "Speed should be zero after Fault ramp-down"


# ---------------------------------------------------------------------------
# Counters
# ---------------------------------------------------------------------------


class TestCounters:
    """Counters increment only when Running (speed > 0)."""

    def test_counters_zero_when_idle(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """In Idle state (speed=0), counters do not increment."""
        sim_time = 0.0
        dt = 0.1
        for _ in range(10):
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        imp = _find_signal(results, "press.impression_count")
        assert imp.value == pytest.approx(0.0, abs=0.01)

    def test_counters_increment_when_running(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """In Running state, counters increment with speed."""
        press.state_machine.force_state("Running")

        sim_time = 0.0
        dt = 0.1
        for _ in range(200):  # 20s, past ramp
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        imp = _find_signal(results, "press.impression_count")
        good = _find_signal(results, "press.good_count")
        waste = _find_signal(results, "press.waste_count")

        assert imp.value > 0.0, "Impression count should increment when Running"
        assert good.value > 0.0, "Good count should increment when Running"
        assert waste.value > 0.0, "Waste count should increment when Running"

        # Good count should be ~97% of impression count
        assert good.value < imp.value, "Good count should be less than impressions"

    def test_counters_freeze_on_fault(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """After Fault, counters stop incrementing (speed=0)."""
        press.state_machine.force_state("Running")
        sim_time = 0.0
        dt = 0.1

        # Run to accumulate some counts
        for _ in range(150):
            sim_time += dt
            press.generate(sim_time, dt, store)

        # Force Fault and wait for speed to drop
        press.state_machine.force_state("Fault")
        for _ in range(400):
            sim_time += dt
            press.generate(sim_time, dt, store)

        # Record count after fault ramp-down
        results = press.generate(sim_time + dt, dt, store)
        imp_after_fault = _find_signal(results, "press.impression_count").value

        # Run more ticks -- count should not change
        for _ in range(50):
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        imp_later = _find_signal(results, "press.impression_count").value
        assert imp_later == pytest.approx(imp_after_fault, abs=0.01), \
            "Counters should freeze when speed is zero"


# ---------------------------------------------------------------------------
# Correlated followers
# ---------------------------------------------------------------------------


class TestCorrelatedFollowers:
    """Correlated followers track line speed."""

    def test_web_tension_at_zero_speed(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """At zero speed, web_tension should be near base value."""
        results = press.generate(0.0, 0.1, store)
        tension = _find_signal(results, "press.web_tension")
        # base=80, factor=0.5, speed=0 -> tension ~80 ± noise
        assert tension.value == pytest.approx(80.0, abs=20.0)

    def test_drive_current_scales_with_speed(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """Drive current = base + factor * speed."""
        press.state_machine.force_state("Running")

        sim_time = 0.0
        dt = 0.1
        for _ in range(200):
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        current = _find_signal(results, "press.main_drive_current")
        # base=15, factor=0.35, speed~200 -> current ~85 ± noise
        assert current.value > 30.0, "Drive current should increase with speed"

    def test_drive_speed_scales_with_speed(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """Drive RPM = factor * speed."""
        press.state_machine.force_state("Running")

        sim_time = 0.0
        dt = 0.1
        for _ in range(200):
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        rpm = _find_signal(results, "press.main_drive_speed")
        # base=0, factor=7.5, speed~200 -> RPM ~1500 ± noise
        assert rpm.value > 100.0, "Drive RPM should scale with speed"


# ---------------------------------------------------------------------------
# Dryer temperatures
# ---------------------------------------------------------------------------


class TestDryerTemperatures:
    """Dryer temps track setpoints when Running, cool when Off."""

    def test_dryers_heat_when_running(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """Dryer temps should approach setpoints when Running."""
        press.state_machine.force_state("Running")

        sim_time = 0.0
        dt = 0.1
        # Run for 100s (10x tau=10s, should be very close)
        for _ in range(1000):
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        t1 = _find_signal(results, "press.dryer_temp_zone_1").value
        t2 = _find_signal(results, "press.dryer_temp_zone_2").value
        t3 = _find_signal(results, "press.dryer_temp_zone_3").value

        # Setpoints: 75, 80, 85 (with noise)
        assert t1 == pytest.approx(75.0, abs=5.0)
        assert t2 == pytest.approx(80.0, abs=5.0)
        assert t3 == pytest.approx(85.0, abs=5.0)

    def test_dryers_cool_when_off(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """Dryer temps should approach ambient when Off."""
        # First heat up
        press.state_machine.force_state("Running")
        sim_time = 0.0
        dt = 0.1
        for _ in range(1000):
            sim_time += dt
            press.generate(sim_time, dt, store)

        # Now go Off
        press.state_machine.force_state("Off")
        for _ in range(1000):  # 100s, many tau
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        t1 = _find_signal(results, "press.dryer_temp_zone_1").value
        # Should have cooled back toward 20C (ambient)
        assert t1 < 30.0, "Dryer should cool toward ambient when Off"


# ---------------------------------------------------------------------------
# Depletion
# ---------------------------------------------------------------------------


class TestDepletion:
    """Unwind diameter depletes when running."""

    def test_unwind_depletes_when_running(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        press.state_machine.force_state("Running")

        sim_time = 0.0
        dt = 0.1
        for _ in range(200):
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        unwind = _find_signal(results, "press.unwind_diameter")
        # Initial 1200mm, should have depleted
        assert unwind.value < 1200.0, "Unwind should deplete when running"


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed produces identical output."""

    def test_deterministic_with_same_seed(self, press_config: EquipmentConfig) -> None:
        store1 = SignalStore()
        store2 = SignalStore()

        gen1 = PressGenerator("press", press_config, np.random.default_rng(99))
        gen2 = PressGenerator("press", press_config, np.random.default_rng(99))

        gen1.state_machine.force_state("Running")
        gen2.state_machine.force_state("Running")

        sim_time = 0.0
        dt = 0.1
        for _ in range(50):
            sim_time += dt
            r1 = gen1.generate(sim_time, dt, store1)
            r2 = gen2.generate(sim_time, dt, store2)

        # All signal values should be identical
        for sv1, sv2 in zip(r1, r2, strict=True):
            assert sv1.signal_id == sv2.signal_id
            assert sv1.value == sv2.value, (
                f"{sv1.signal_id}: {sv1.value} != {sv2.value}"
            )


# ---------------------------------------------------------------------------
# Protocol mappings
# ---------------------------------------------------------------------------


class TestProtocolMappings:
    """Protocol mappings are extracted from config."""

    def test_mappings_count(self, press: PressGenerator) -> None:
        mappings = press.get_protocol_mappings()
        assert len(mappings) == 22

    def test_line_speed_has_modbus(self, press: PressGenerator) -> None:
        mappings = press.get_protocol_mappings()
        m = mappings["press.line_speed"]
        assert m.modbus is not None
        assert m.modbus.address == [100, 101]
        assert m.modbus.register_type == "float32"

    def test_machine_state_has_modbus(self, press: PressGenerator) -> None:
        mappings = press.get_protocol_mappings()
        m = mappings["press.machine_state"]
        assert m.modbus is not None
        assert m.modbus.address == [210]
        assert m.modbus.register_type == "uint16"


# ---------------------------------------------------------------------------
# Clamping
# ---------------------------------------------------------------------------


class TestClamping:
    """Signal values respect physical bounds."""

    def test_all_signals_within_bounds(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        """Run many ticks and verify all signals stay within bounds."""
        press.state_machine.force_state("Running")

        sim_time = 0.0
        dt = 0.1
        for _ in range(200):
            sim_time += dt
            results = press.generate(sim_time, dt, store)

        for sv in results:
            sig_name = sv.signal_id.split(".", 1)[1]
            sig_cfg = press._signal_configs.get(sig_name)
            if sig_cfg is not None and sig_cfg.min_clamp is not None:
                assert sv.value >= sig_cfg.min_clamp, (
                    f"{sv.signal_id} = {sv.value} < min {sig_cfg.min_clamp}"
                )
            if sig_cfg is not None and sig_cfg.max_clamp is not None:
                assert sv.value <= sig_cfg.max_clamp, (
                    f"{sv.signal_id} = {sv.value} > max {sig_cfg.max_clamp}"
                )


# ---------------------------------------------------------------------------
# Nip pressure state-dependent
# ---------------------------------------------------------------------------


class TestNipPressure:
    """Nip pressure is active when Running/Setup/Idle, zero when Off."""

    def test_nip_pressure_zero_when_off(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        press.state_machine.force_state("Off")
        results = press.generate(0.1, 0.1, store)
        nip = _find_signal(results, "press.nip_pressure")
        assert nip.value == pytest.approx(0.0, abs=0.01)

    def test_nip_pressure_active_when_running(
        self, press: PressGenerator, store: SignalStore,
    ) -> None:
        press.state_machine.force_state("Running")
        sim_time = 0.0
        dt = 0.1
        for _ in range(10):
            sim_time += dt
            results = press.generate(sim_time, dt, store)
        nip = _find_signal(results, "press.nip_pressure")
        assert nip.value > 0.0


# ---------------------------------------------------------------------------
# Dryer zone Cholesky correlation (PRD 4.3.1)
# ---------------------------------------------------------------------------


class TestDryerZoneCholesky:
    """Dryer zone noise is correlated via Cholesky pipeline (PRD 4.3.1)."""

    def test_dryer_zones_positively_correlated(self, press_config: EquipmentConfig) -> None:
        """Dryer zone noise residuals should be positively correlated.

        Run many ticks in Running state, extract residuals (value - setpoint),
        and verify that Pearson correlations are positive for adjacent zones.
        """
        store = SignalStore()
        gen = PressGenerator("press", press_config, np.random.default_rng(12345))
        gen.state_machine.force_state("Running")

        # Collect dryer temps over many ticks after steady-state is reached
        sim_time = 0.0
        dt = 0.1
        # Warm up: let lag models reach setpoints (tau=10s, run 200s)
        for _ in range(2000):
            sim_time += dt
            gen.generate(sim_time, dt, store)

        # Collect residuals
        residuals_1: list[float] = []
        residuals_2: list[float] = []
        residuals_3: list[float] = []
        for _ in range(5000):
            sim_time += dt
            results = gen.generate(sim_time, dt, store)
            t1 = _find_signal(results, "press.dryer_temp_zone_1").value
            t2 = _find_signal(results, "press.dryer_temp_zone_2").value
            t3 = _find_signal(results, "press.dryer_temp_zone_3").value
            # Setpoints: 75, 80, 85 from test config
            residuals_1.append(t1 - 75.0)
            residuals_2.append(t2 - 80.0)
            residuals_3.append(t3 - 85.0)

        # Compute sample Pearson correlations
        r12 = np.corrcoef(residuals_1, residuals_2)[0, 1]
        r13 = np.corrcoef(residuals_1, residuals_3)[0, 1]
        r23 = np.corrcoef(residuals_2, residuals_3)[0, 1]

        # PRD matrix: r12=0.1, r13=0.02, r23=0.1
        # Correlations should be positive (exact values vary due to clamping/lag)
        assert r12 > 0.0, f"Zone 1-2 correlation should be positive, got {r12:.4f}"
        assert r23 > 0.0, f"Zone 2-3 correlation should be positive, got {r23:.4f}"
        # Zone 1-3 has very weak correlation (0.02), may not be detectable
        # Just verify it's not strongly negative
        assert r13 > -0.1, f"Zone 1-3 correlation should not be negative, got {r13:.4f}"

        # Zone 1-2 and 2-3 should be stronger than 1-3
        assert r12 > r13, (
            f"Zone 1-2 ({r12:.4f}) should be more correlated than 1-3 ({r13:.4f})"
        )

    def test_custom_correlation_matrix(self) -> None:
        """Custom dryer_zone_correlation_matrix overrides PRD default."""
        custom_matrix = [
            [1.0, 0.5, 0.3],
            [0.5, 1.0, 0.5],
            [0.3, 0.5, 1.0],
        ]
        config = _make_press_config()
        # Inject custom matrix via model extras
        config_with_matrix = EquipmentConfig(
            enabled=True,
            type="flexographic_press",
            signals=config.signals,
            target_speed=200,
            speed_range=[50, 400],
            dryer_zone_correlation_matrix=custom_matrix,
        )
        gen = PressGenerator("press", config_with_matrix, np.random.default_rng(42))
        store = SignalStore()
        gen.state_machine.force_state("Running")

        # Warm up
        sim_time = 0.0
        dt = 0.1
        for _ in range(2000):
            sim_time += dt
            gen.generate(sim_time, dt, store)

        # Collect residuals
        residuals_1: list[float] = []
        residuals_2: list[float] = []
        for _ in range(5000):
            sim_time += dt
            results = gen.generate(sim_time, dt, store)
            t1 = _find_signal(results, "press.dryer_temp_zone_1").value
            t2 = _find_signal(results, "press.dryer_temp_zone_2").value
            residuals_1.append(t1 - 75.0)
            residuals_2.append(t2 - 80.0)

        r12 = np.corrcoef(residuals_1, residuals_2)[0, 1]
        # With stronger correlation matrix (0.5), expect higher correlation
        assert r12 > 0.1, f"Custom matrix correlation should be stronger, got {r12:.4f}"

    def test_dryer_noise_not_double_applied(self, press_config: EquipmentConfig) -> None:
        """Lag models should not have internal noise (avoid double-noising).

        Verify that the FirstOrderLagModel instances for dryer temps
        have noise=None — all noise is applied externally via Cholesky.
        """
        gen = PressGenerator("press", press_config, np.random.default_rng(42))
        assert gen._dryer_temp_1._noise is None
        assert gen._dryer_temp_2._noise is None
        assert gen._dryer_temp_3._noise is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    """Find a SignalValue by signal_id in a results list."""
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise AssertionError(f"Signal {signal_id} not found in results")
