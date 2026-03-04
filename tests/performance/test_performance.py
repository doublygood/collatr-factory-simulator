"""Performance benchmarks for the Collatr Factory Simulator.

Measures engine throughput, memory stability, and realistic-mode overhead.
Results are written to ``performance-results.json`` in the project root for
tracking across commits.

These are **benchmarks, not strict functional tests**.  Assertions use
generous bounds that should never be hit in practice; they exist only to
catch catastrophic regressions (e.g. O(n²) leaks, order-of-magnitude
slowdowns).

Run benchmarks:
    pytest tests/performance/ -v

Skip benchmarks in normal test runs:
    pytest -m 'not performance'

PRD Reference: Appendix F (Phase 5 — performance profiling)
"""

from __future__ import annotations

import json
import time
import tracemalloc
from pathlib import Path

import numpy as np
import pytest

from factory_simulator.clock import SimulationClock
from factory_simulator.config import BatchOutputConfig, NetworkConfig, load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.output.writer import CsvWriter
from factory_simulator.store import SignalStore
from factory_simulator.topology import NetworkTopologyManager

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

_CONFIG_PKG = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
_CONFIG_FNB = Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"
_RESULTS_FILE = Path(__file__).resolve().parents[2] / "performance-results.json"

# 10x throughput test: 1 simulated hour at 10x compression.
# tick_interval=100ms, time_scale=10 → dt=1s/tick → 3600 ticks/hour.
_10X_TICK_INTERVAL_MS = 100
_10X_TIME_SCALE = 10.0
_10X_DT_S = (_10X_TICK_INTERVAL_MS / 1000.0) * _10X_TIME_SCALE  # 1.0 s/tick
_1H_S = 3_600.0
_10X_1H_TICKS = int(_1H_S / _10X_DT_S)  # 3600 ticks

# 100x batch test: 24 simulated hours at 100x compression.
# tick_interval=100ms, time_scale=100 → dt=10s/tick → 8640 ticks/24h.
_100X_TICK_INTERVAL_MS = 100
_100X_TIME_SCALE = 100.0
_100X_DT_S = (_100X_TICK_INTERVAL_MS / 1000.0) * _100X_TIME_SCALE  # 10.0 s/tick
_24H_S = 86_400.0
_100X_24H_TICKS = int(_24H_S / _100X_DT_S)  # 8640 ticks

# 7-day memory test (slow): 7 simulated days at 100x.
# 7 * 86400 / 10 = 60480 ticks ≈ 100 real seconds per profile.
_7DAY_S = 7.0 * _24H_S
_7DAY_TICKS = int(_7DAY_S / _100X_DT_S)  # 60480 ticks

# Realistic mode overhead: shorter run to limit wall time.
_OVERHEAD_TICKS = 500

# Target bounds (informational only — generous thresholds):
#   tick latency: < 100ms mean (PRD "10x serving" target)
#   24h batch wall time: < 15 minutes (900s)
#   realistic overhead: < 2x collapsed tick latency
#   7-day memory growth: < 5x initial peak (consistent with existing slow tests)
_TARGET_TICK_LATENCY_MS = 100.0
_TARGET_BATCH_WALL_S = 900.0
_TARGET_OVERHEAD_RATIO = 2.0
_TARGET_MEMORY_RATIO = 5.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _update_results(key: str, result: dict[str, object]) -> None:
    """Atomically load, update, and save ``performance-results.json``."""
    data: dict[str, object] = {}
    if _RESULTS_FILE.exists():
        try:
            data = json.loads(_RESULTS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
    data[key] = result
    _RESULTS_FILE.write_text(
        json.dumps(data, indent=2, default=str), encoding="utf-8"
    )


def _make_engine(
    config_path: Path,
    time_scale: float,
    seed: int = 42,
    topology: NetworkTopologyManager | None = None,
) -> DataEngine:
    """Create a plain DataEngine (no protocol servers, no GT logger)."""
    config = load_config(config_path, apply_env=False)
    config.simulation.random_seed = seed
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = time_scale

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    return DataEngine(config, store, clock, topology=topology)


def _run_ticks(engine: DataEngine, n: int) -> None:
    """Advance engine by n ticks with no asyncio sleep."""
    for _ in range(n):
        engine.tick()


def _measure_ticks(engine: DataEngine, n: int) -> list[float]:
    """Advance engine by n ticks; return per-tick wall times in seconds."""
    latencies: list[float] = []
    for _ in range(n):
        t0 = time.perf_counter()
        engine.tick()
        latencies.append(time.perf_counter() - t0)
    return latencies


def _stats(values: list[float]) -> dict[str, float]:
    """Compute summary statistics (mean, p95, p99) in milliseconds."""
    arr = np.array(values) * 1000.0  # convert to ms
    return {
        "mean_ms": float(round(float(np.mean(arr)), 3)),
        "p95_ms": float(round(float(np.percentile(arr, 95)), 3)),
        "p99_ms": float(round(float(np.percentile(arr, 99)), 3)),
    }


# ---------------------------------------------------------------------------
# 10x throughput: 1-hour simulation per profile
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_packaging_10x_throughput() -> None:
    """1-hour packaging simulation at 10x: record tick latency statistics.

    Target (informational): mean tick latency < 100 ms so that the engine
    can comfortably serve all three protocols between ticks when running
    at 10x time compression.

    PRD Appendix F: 10x protocol serving tick latency < 100ms target.
    """
    engine = _make_engine(_CONFIG_PKG, _10X_TIME_SCALE)
    latencies = _measure_ticks(engine, _10X_1H_TICKS)
    stats = _stats(latencies)

    result: dict[str, object] = {
        "profile": "packaging",
        "time_scale": _10X_TIME_SCALE,
        "sim_duration_h": 1,
        "ticks": _10X_1H_TICKS,
        **stats,
    }
    _update_results("packaging_10x_throughput", result)

    # Generous bound: catch O(n²) regressions, not minor slowdowns.
    assert stats["mean_ms"] < _TARGET_TICK_LATENCY_MS, (
        f"Packaging 10x mean tick latency {stats['mean_ms']:.1f}ms "
        f"exceeds {_TARGET_TICK_LATENCY_MS}ms target"
    )


@pytest.mark.performance
def test_foodbev_10x_throughput() -> None:
    """1-hour F&B simulation at 10x: record tick latency statistics.

    F&B has more signals (68) and generators (10) than packaging (47/7).
    This test verifies the heavier profile still meets the 100ms target.
    """
    engine = _make_engine(_CONFIG_FNB, _10X_TIME_SCALE)
    latencies = _measure_ticks(engine, _10X_1H_TICKS)
    stats = _stats(latencies)

    result: dict[str, object] = {
        "profile": "food_bev",
        "time_scale": _10X_TIME_SCALE,
        "sim_duration_h": 1,
        "ticks": _10X_1H_TICKS,
        **stats,
    }
    _update_results("foodbev_10x_throughput", result)

    assert stats["mean_ms"] < _TARGET_TICK_LATENCY_MS, (
        f"F&B 10x mean tick latency {stats['mean_ms']:.1f}ms "
        f"exceeds {_TARGET_TICK_LATENCY_MS}ms target"
    )


# ---------------------------------------------------------------------------
# 100x batch: 24-hour simulation per profile with CSV writer
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_packaging_100x_batch(tmp_path: Path) -> None:
    """24-hour packaging batch at 100x: measure wall time and throughput.

    Exercises the full DataEngine → CsvWriter pipeline at maximum time
    compression.  Target: 24-hour sim completes in < 15 minutes wall time.
    """
    config = load_config(_CONFIG_PKG, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = _100X_TICK_INTERVAL_MS
    config.simulation.time_scale = _100X_TIME_SCALE

    batch_cfg = BatchOutputConfig(format="csv", path=str(tmp_path), buffer_size=10_000)
    writer = CsvWriter(tmp_path, batch_cfg)

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock, batch_writer=writer)

    t0 = time.perf_counter()
    _run_ticks(engine, _100X_24H_TICKS)
    writer.close()
    wall_s = time.perf_counter() - t0

    ticks_per_sec = _100X_24H_TICKS / max(wall_s, 1e-9)

    result: dict[str, object] = {
        "profile": "packaging",
        "time_scale": _100X_TIME_SCALE,
        "sim_duration_h": 24,
        "ticks": _100X_24H_TICKS,
        "wall_time_s": round(wall_s, 2),
        "ticks_per_second": round(ticks_per_sec, 1),
    }
    _update_results("packaging_100x_batch", result)

    assert wall_s < _TARGET_BATCH_WALL_S, (
        f"Packaging 24h batch took {wall_s:.1f}s "
        f"(target < {_TARGET_BATCH_WALL_S}s / 15 min)"
    )


@pytest.mark.performance
def test_foodbev_100x_batch(tmp_path: Path) -> None:
    """24-hour F&B batch at 100x: measure wall time and throughput.

    F&B has more signals and writes more CSV rows per tick than packaging.
    """
    config = load_config(_CONFIG_FNB, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = _100X_TICK_INTERVAL_MS
    config.simulation.time_scale = _100X_TIME_SCALE

    batch_cfg = BatchOutputConfig(format="csv", path=str(tmp_path), buffer_size=10_000)
    writer = CsvWriter(tmp_path, batch_cfg)

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock, batch_writer=writer)

    t0 = time.perf_counter()
    _run_ticks(engine, _100X_24H_TICKS)
    writer.close()
    wall_s = time.perf_counter() - t0

    ticks_per_sec = _100X_24H_TICKS / max(wall_s, 1e-9)

    result: dict[str, object] = {
        "profile": "food_bev",
        "time_scale": _100X_TIME_SCALE,
        "sim_duration_h": 24,
        "ticks": _100X_24H_TICKS,
        "wall_time_s": round(wall_s, 2),
        "ticks_per_second": round(ticks_per_sec, 1),
    }
    _update_results("foodbev_100x_batch", result)

    assert wall_s < _TARGET_BATCH_WALL_S, (
        f"F&B 24h batch took {wall_s:.1f}s "
        f"(target < {_TARGET_BATCH_WALL_S}s / 15 min)"
    )


# ---------------------------------------------------------------------------
# Realistic mode overhead: packaging 10x, collapsed vs realistic topology
# ---------------------------------------------------------------------------


@pytest.mark.performance
def test_realistic_mode_10x() -> None:
    """Packaging 10x: compare engine tick latency in collapsed vs realistic mode.

    In realistic mode the ``NetworkTopologyManager`` is wired into the
    ``DataEngine`` constructor.  This test verifies that the topology
    configuration does not add significant overhead to the engine tick loop
    (which does not itself invoke the topology; protocol-server sync runs
    separately in asyncio tasks).

    Target: realistic-mode tick latency < 2x collapsed-mode tick latency.
    """
    # Collapsed mode baseline
    engine_collapsed = _make_engine(_CONFIG_PKG, _10X_TIME_SCALE)
    latencies_collapsed = _measure_ticks(engine_collapsed, _OVERHEAD_TICKS)
    stats_collapsed = _stats(latencies_collapsed)

    # Realistic mode: NetworkTopologyManager passed to DataEngine.
    config = load_config(_CONFIG_PKG, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = 100
    config.simulation.time_scale = _10X_TIME_SCALE
    if config.network is None:
        config.network = NetworkConfig(mode="realistic")
    else:
        config.network.mode = "realistic"
    topology = NetworkTopologyManager(config.network, profile="packaging")

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine_realistic = DataEngine(config, store, clock, topology=topology)
    latencies_realistic = _measure_ticks(engine_realistic, _OVERHEAD_TICKS)
    stats_realistic = _stats(latencies_realistic)

    collapsed_mean = stats_collapsed["mean_ms"]
    realistic_mean = stats_realistic["mean_ms"]
    overhead_ratio = realistic_mean / max(collapsed_mean, 0.001)

    result: dict[str, object] = {
        "profile": "packaging",
        "time_scale": _10X_TIME_SCALE,
        "ticks": _OVERHEAD_TICKS,
        "collapsed_mean_ms": collapsed_mean,
        "realistic_mean_ms": realistic_mean,
        "overhead_ratio": round(overhead_ratio, 3),
    }
    _update_results("realistic_mode_10x_overhead", result)

    assert overhead_ratio < _TARGET_OVERHEAD_RATIO, (
        f"Realistic mode tick overhead {overhead_ratio:.2f}x "
        f"exceeds {_TARGET_OVERHEAD_RATIO:.1f}x target"
    )


# ---------------------------------------------------------------------------
# 7-day memory stability (slow mark: ~100s wall time per profile)
# ---------------------------------------------------------------------------


@pytest.mark.performance
@pytest.mark.slow
def test_memory_7day() -> None:
    """Packaging 7-day run at 100x: verify Python heap growth < 5x initial peak.

    Uses ``tracemalloc`` (Python-level allocation tracking) to detect
    unbounded memory growth that would prevent long production runs.

    The 5x threshold (vs 2x for the 1-day integration test) accounts for
    bounded linear accumulation over 60 480 ticks.  Exponential growth
    (memory leaks) will exceed 5x; bounded growth will not.

    Echoes the existing ``TestLongRunIntegration.test_packaging_7day_memory_stable``
    from the reproducibility suite, but also records results to
    ``performance-results.json``.

    PRD Appendix F: 7-day batch run without memory leaks.
    """
    config = load_config(_CONFIG_PKG, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.tick_interval_ms = _100X_TICK_INTERVAL_MS
    config.simulation.time_scale = _100X_TIME_SCALE
    config.simulation.sim_duration_s = _7DAY_S

    store = SignalStore()
    clock = SimulationClock.from_config(config.simulation)
    engine = DataEngine(config, store, clock)

    tracemalloc.start()
    _run_ticks(engine, 100)  # warmup: let initial allocations settle
    _, initial_peak = tracemalloc.get_traced_memory()

    _run_ticks(engine, _7DAY_TICKS - 100)
    _, final_peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    ratio = final_peak / max(initial_peak, 1)
    initial_kb = initial_peak // 1024
    final_kb = final_peak // 1024

    result: dict[str, object] = {
        "profile": "packaging",
        "time_scale": _100X_TIME_SCALE,
        "sim_duration_days": 7,
        "ticks": _7DAY_TICKS,
        "initial_peak_kb": initial_kb,
        "final_peak_kb": final_kb,
        "growth_ratio": round(ratio, 3),
    }
    _update_results("memory_7day_packaging", result)

    assert initial_peak > 0, "tracemalloc returned zero initial peak"
    assert ratio < _TARGET_MEMORY_RATIO, (
        f"Packaging 7-day memory grew {ratio:.2f}x initial peak "
        f"({initial_kb} KiB → {final_kb} KiB). "
        "Possible unbounded accumulation in engine tick loop."
    )
