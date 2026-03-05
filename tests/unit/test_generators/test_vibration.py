"""Unit tests for the VibrationGenerator (PRD 2.9).

Tests verify:
- Signal count (3 axes: main_drive_x, main_drive_y, main_drive_z)
- Non-zero vibration when press is running (speed > 1.0)
- Near-zero vibration when press is stopped
- Cholesky-correlated axes (sample correlations > 0)
- Cholesky matrix matches PRD 4.3.1 correlation matrix
- Determinism (same seed -> same output)
- Clamping respected
- Quality always "good"

Task 6d.11
"""

from __future__ import annotations

import numpy as np
import pytest

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.vibration import VibrationGenerator
from factory_simulator.store import SignalStore, SignalValue

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_vibration_config() -> EquipmentConfig:
    """Create a minimal vibration config for testing."""
    signals: dict[str, SignalConfig] = {}

    for axis, target in [("main_drive_x", 4.0), ("main_drive_y", 3.5), ("main_drive_z", 5.0)]:
        signals[axis] = SignalConfig(
            model="steady_state",
            noise_sigma=0.3,
            noise_type="student_t",
            noise_df=5,
            sigma_base=0.2,
            sigma_scale=0.015,
            sigma_parent="press.line_speed",
            sample_rate_ms=1000,
            min_clamp=0.0,
            max_clamp=50.0,
            units="mm/s",
            params={"target": target},
        )

    return EquipmentConfig(
        enabled=True,
        type="wireless_vibration",
        signals=signals,
    )


def _find_signal(results: list[SignalValue], signal_id: str) -> SignalValue:
    for sv in results:
        if sv.signal_id == signal_id:
            return sv
    raise KeyError(f"Signal {signal_id} not found in results")


def _run_ticks(
    gen: VibrationGenerator,
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
def vib(rng: np.random.Generator) -> VibrationGenerator:
    return VibrationGenerator("vibration", _make_vibration_config(), rng)


# ---------------------------------------------------------------------------
# Tests: signal IDs
# ---------------------------------------------------------------------------


class TestSignalIds:
    """Verify all 3 vibration signals are registered."""

    def test_signal_count(self, vib: VibrationGenerator) -> None:
        assert len(vib.get_signal_ids()) == 3

    def test_signal_names(self, vib: VibrationGenerator) -> None:
        ids = set(vib.get_signal_ids())
        expected = {
            "vibration.main_drive_x",
            "vibration.main_drive_y",
            "vibration.main_drive_z",
        }
        assert ids == expected


# ---------------------------------------------------------------------------
# Tests: running behaviour (press speed > 1.0)
# ---------------------------------------------------------------------------


class TestRunning:
    """Vibration should be at baseline level when press is running."""

    def test_nonzero_when_running(
        self, vib: VibrationGenerator, store: SignalStore,
    ) -> None:
        """All axes produce non-zero values when press speed > 1.0."""
        store.set("press.line_speed", 100.0, 0.0, "good")
        results = vib.generate(0.1, 0.1, store)

        for sv in results:
            assert sv.value > 0.0, f"{sv.signal_id} should be > 0 when running"

    def test_values_near_target(
        self, vib: VibrationGenerator, store: SignalStore,
    ) -> None:
        """Mean vibration should be near target values over many ticks."""
        store.set("press.line_speed", 100.0, 0.0, "good")
        results_list = _run_ticks(vib, store, n_ticks=200, dt=0.1)

        x_vals = [_find_signal(r, "vibration.main_drive_x").value for r in results_list]
        y_vals = [_find_signal(r, "vibration.main_drive_y").value for r in results_list]
        z_vals = [_find_signal(r, "vibration.main_drive_z").value for r in results_list]

        # Targets: x=4.0, y=3.5, z=5.0
        assert 2.0 < np.mean(x_vals) < 7.0, f"X mean unexpected: {np.mean(x_vals):.2f}"
        assert 1.5 < np.mean(y_vals) < 6.5, f"Y mean unexpected: {np.mean(y_vals):.2f}"
        assert 3.0 < np.mean(z_vals) < 8.0, f"Z mean unexpected: {np.mean(z_vals):.2f}"


# ---------------------------------------------------------------------------
# Tests: stopped behaviour (press speed <= 1.0)
# ---------------------------------------------------------------------------


class TestStopped:
    """Vibration should be near zero when press is stopped."""

    def test_near_zero_when_stopped(
        self, vib: VibrationGenerator, store: SignalStore,
    ) -> None:
        """All axes should be near zero (ambient floor) when stopped."""
        store.set("press.line_speed", 0.0, 0.0, "good")
        results_list = _run_ticks(vib, store, n_ticks=50, dt=0.1)

        for results in results_list:
            for sv in results:
                # Idle mean=0.2, std=0.05, clamped >= 0
                assert sv.value < 1.0, (
                    f"{sv.signal_id} should be near zero when stopped: {sv.value}"
                )

    def test_default_stopped(
        self, vib: VibrationGenerator, store: SignalStore,
    ) -> None:
        """When press.line_speed is not in store (defaults to 0), acts as stopped."""
        results = vib.generate(0.1, 0.1, store)
        for sv in results:
            assert sv.value < 1.0, (
                f"{sv.signal_id} should be near zero with no press speed: {sv.value}"
            )


# ---------------------------------------------------------------------------
# Tests: Cholesky correlation (PRD 4.3.1)
# ---------------------------------------------------------------------------


class TestCholeskyCorrelation:
    """Axes should be correlated via Cholesky decomposition."""

    def test_cholesky_matrix_matches_prd(self) -> None:
        """The PRD correlation matrix should decompose correctly."""
        expected = np.array([
            [1.0, 0.2, 0.15],
            [0.2, 1.0, 0.2],
            [0.15, 0.2, 1.0],
        ])
        assert np.array_equal(VibrationGenerator._PRD_CORRELATION_MATRIX, expected)

    def test_axes_positively_correlated(self, store: SignalStore) -> None:
        """Over many samples, all axis pairs should show positive correlation."""
        rng = np.random.default_rng(123)
        gen = VibrationGenerator("vibration", _make_vibration_config(), rng)
        store.set("press.line_speed", 100.0, 0.0, "good")

        results_list = _run_ticks(gen, store, n_ticks=2000, dt=0.1)

        x_vals = np.array([_find_signal(r, "vibration.main_drive_x").value for r in results_list])
        y_vals = np.array([_find_signal(r, "vibration.main_drive_y").value for r in results_list])
        z_vals = np.array([_find_signal(r, "vibration.main_drive_z").value for r in results_list])

        # Compute sample correlations
        corr_xy = np.corrcoef(x_vals, y_vals)[0, 1]
        corr_xz = np.corrcoef(x_vals, z_vals)[0, 1]
        corr_yz = np.corrcoef(y_vals, z_vals)[0, 1]

        # PRD specifies: X-Y=0.2, X-Z=0.15, Y-Z=0.2
        # With noise and clamping, sample correlations won't match exactly
        # but should be positive
        assert corr_xy > 0.0, f"X-Y correlation should be positive: {corr_xy:.3f}"
        assert corr_xz > 0.0, f"X-Z correlation should be positive: {corr_xz:.3f}"
        assert corr_yz > 0.0, f"Y-Z correlation should be positive: {corr_yz:.3f}"

    def test_custom_correlation_matrix(self, store: SignalStore) -> None:
        """Custom correlation matrix should be used when provided."""
        custom_matrix = [
            [1.0, 0.9, 0.9],
            [0.9, 1.0, 0.9],
            [0.9, 0.9, 1.0],
        ]
        cfg = _make_vibration_config()
        # EquipmentConfig has extra="allow", so we can pass custom attrs
        cfg_with_custom = EquipmentConfig(
            enabled=True,
            type="wireless_vibration",
            signals=cfg.signals,
            axis_correlation_matrix=custom_matrix,
        )
        rng = np.random.default_rng(77)
        gen = VibrationGenerator("vibration", cfg_with_custom, rng)
        store.set("press.line_speed", 100.0, 0.0, "good")

        results_list = _run_ticks(gen, store, n_ticks=2000, dt=0.1)

        x_vals = np.array([_find_signal(r, "vibration.main_drive_x").value for r in results_list])
        y_vals = np.array([_find_signal(r, "vibration.main_drive_y").value for r in results_list])

        corr_xy = np.corrcoef(x_vals, y_vals)[0, 1]
        # With high correlation matrix (0.9), sample correlation should be notably higher
        assert corr_xy > 0.3, f"High-correlation matrix should produce high corr: {corr_xy:.3f}"


# ---------------------------------------------------------------------------
# Tests: clamping
# ---------------------------------------------------------------------------


class TestClamping:
    """Signal values respect min/max clamp."""

    def test_values_within_bounds(
        self, vib: VibrationGenerator, store: SignalStore,
    ) -> None:
        """All values should be within [0, 50] mm/s."""
        store.set("press.line_speed", 100.0, 0.0, "good")
        results_list = _run_ticks(vib, store, n_ticks=200, dt=0.1)

        for results in results_list:
            for sv in results:
                assert 0.0 <= sv.value <= 50.0, (
                    f"{sv.signal_id} out of bounds: {sv.value}"
                )

    def test_non_negative_when_stopped(
        self, vib: VibrationGenerator, store: SignalStore,
    ) -> None:
        """Idle vibration should be >= 0 (clamped)."""
        store.set("press.line_speed", 0.0, 0.0, "good")
        results_list = _run_ticks(vib, store, n_ticks=100, dt=0.1)

        for results in results_list:
            for sv in results:
                assert sv.value >= 0.0, f"{sv.signal_id} negative: {sv.value}"


# ---------------------------------------------------------------------------
# Tests: quality
# ---------------------------------------------------------------------------


class TestQuality:
    """All signals should have quality='good'."""

    def test_all_good_quality(
        self, vib: VibrationGenerator, store: SignalStore,
    ) -> None:
        store.set("press.line_speed", 100.0, 0.0, "good")
        results = vib.generate(0.1, 0.1, store)
        for sv in results:
            assert sv.quality == "good"

    def test_good_quality_when_stopped(
        self, vib: VibrationGenerator, store: SignalStore,
    ) -> None:
        store.set("press.line_speed", 0.0, 0.0, "good")
        results = vib.generate(0.1, 0.1, store)
        for sv in results:
            assert sv.quality == "good"


# ---------------------------------------------------------------------------
# Tests: determinism (CLAUDE.md Rule 13)
# ---------------------------------------------------------------------------


class TestDeterminism:
    """Same seed -> identical output sequence."""

    def test_vibration_deterministic(self, store: SignalStore) -> None:
        cfg = _make_vibration_config()
        store.set("press.line_speed", 100.0, 0.0, "good")

        gen1 = VibrationGenerator("vibration", cfg, np.random.default_rng(99))
        gen2 = VibrationGenerator("vibration", cfg, np.random.default_rng(99))

        store1 = SignalStore()
        store2 = SignalStore()
        store1.set("press.line_speed", 100.0, 0.0, "good")
        store2.set("press.line_speed", 100.0, 0.0, "good")

        sim_time = 0.0
        dt = 0.1
        r1 = r2 = []
        for _ in range(50):
            sim_time += dt
            r1 = gen1.generate(sim_time, dt, store1)
            r2 = gen2.generate(sim_time, dt, store2)
            for sv in r1:
                store1.set(sv.signal_id, sv.value, sv.timestamp, sv.quality)
            for sv in r2:
                store2.set(sv.signal_id, sv.value, sv.timestamp, sv.quality)

        for sv1, sv2 in zip(r1, r2, strict=True):
            assert sv1.signal_id == sv2.signal_id
            assert sv1.value == sv2.value, (
                f"{sv1.signal_id}: {sv1.value} != {sv2.value}"
            )

    def test_different_seeds_differ(self, store: SignalStore) -> None:
        cfg = _make_vibration_config()

        gen1 = VibrationGenerator("vibration", cfg, np.random.default_rng(1))
        gen2 = VibrationGenerator("vibration", cfg, np.random.default_rng(2))

        store1 = SignalStore()
        store2 = SignalStore()
        store1.set("press.line_speed", 100.0, 0.0, "good")
        store2.set("press.line_speed", 100.0, 0.0, "good")

        r1 = gen1.generate(0.1, 0.1, store1)
        r2 = gen2.generate(0.1, 0.1, store2)

        # At least one value should differ with different seeds
        values_differ = any(
            sv1.value != sv2.value for sv1, sv2 in zip(r1, r2, strict=True)
        )
        assert values_differ, "Different seeds should produce different values"
