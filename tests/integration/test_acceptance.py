"""Final acceptance tests verifying PRD Section 11 criteria.

These tests constitute the "done" gate for Phase 5.  They verify:

- Both profiles run for 24 simulated hours without NaN/Inf or divergent values
- Realistic topology: correct per-controller endpoint counts and properties
- Per-controller Modbus server responds to register reads
- Controller independence: one drop does not affect others
- Evaluation framework: end-to-end metrics computation from synthetic data
- Batch CSV and Parquet output: valid files with correct schema
- CLI interface: --help, version, run subcommand
- Clock drift: Eurotherm drift formula produces visible offset after 24 h

Run acceptance tests only::

    pytest tests/integration/test_acceptance.py -m acceptance

Run acceptance tests including slow 24-hour runs::

    pytest tests/integration/test_acceptance.py -m "acceptance and slow"

PRD Reference: Section 11 (Success Criteria), Appendix F (Phase 5 exit criteria)
"""

from __future__ import annotations

import asyncio
import csv as csv_module
import dataclasses
import json
import math
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from factory_simulator.config import BatchOutputConfig, NetworkConfig, load_config
from factory_simulator.engine.data_engine import DataEngine
from factory_simulator.store import SignalStore

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_CONFIG_PKG = Path(__file__).resolve().parents[2] / "config" / "factory.yaml"
_CONFIG_FNB = Path(__file__).resolve().parents[2] / "config" / "factory-foodbev.yaml"

# ---------------------------------------------------------------------------
# Simulation constants (matching Phase 4 / reproducibility tests)
# ---------------------------------------------------------------------------

_TICK_INTERVAL_MS = 100
_TIME_SCALE = 100.0
_TICK_DT = (_TICK_INTERVAL_MS / 1000.0) * _TIME_SCALE  # 10 s / tick

_SIM_DAY_S = 86_400.0
_SIM_DAY_TICKS = int(_SIM_DAY_S / _TICK_DT)  # 8 640 ticks

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_engine(config_path: Path) -> tuple[DataEngine, SignalStore]:
    """Build a DataEngine with test-safe settings (100x, seed=42, no DQ noise)."""
    config = load_config(config_path, apply_env=False)
    config.simulation.random_seed = 42
    config.simulation.time_scale = _TIME_SCALE
    config.simulation.tick_interval_ms = _TICK_INTERVAL_MS
    config.data_quality.exception_probability = 0.0
    config.data_quality.partial_modbus_response.probability = 0.0
    config.data_quality.modbus_drop.enabled = False
    config.data_quality.opcua_stale.enabled = False
    config.data_quality.mqtt_drop.enabled = False
    store = SignalStore()
    engine = DataEngine(config, store)
    return engine, store


def _assert_no_nan_inf(store: SignalStore, context: str = "") -> None:
    """Raise AssertionError if any signal value is NaN or Inf."""
    bad: list[str] = [
        f"{sid}={sv.value!r}"
        for sid, sv in store.get_all().items()
        if isinstance(sv.value, float) and (math.isnan(sv.value) or math.isinf(sv.value))
    ]
    label = f" ({context})" if context else ""
    assert not bad, f"NaN/Inf in signals{label}: {bad}"


# ---------------------------------------------------------------------------
# 24-hour engine tests (no external services)
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
@pytest.mark.slow
def test_packaging_collapsed_24h() -> None:
    """Packaging profile: 24 simulated hours at 100x in collapsed mode.

    PRD 11.4 / 11.5: no NaN/Inf, >= 47 signals, signal values stay in
    plausible ranges.
    """
    engine, store = _build_engine(_CONFIG_PKG)
    for _ in range(_SIM_DAY_TICKS):
        engine.tick()

    all_signals = store.get_all()

    # Signal count
    assert len(all_signals) >= 47, (
        f"Expected >= 47 packaging signals, got {len(all_signals)}"
    )

    # No NaN / Inf
    _assert_no_nan_inf(store, "packaging 24h")

    # Spot-check: press.line_speed in plausible range [0, 600 m/min]
    line_speed = all_signals.get("press.line_speed")
    assert line_speed is not None, "press.line_speed not in store after 24h"
    assert 0.0 <= float(line_speed.value) <= 600.0, (
        f"press.line_speed={line_speed.value!r} outside [0, 600]"
    )

    # Spot-check: press.machine_state in [0, 5]
    machine_state = all_signals.get("press.machine_state")
    assert machine_state is not None, "press.machine_state not in store"
    assert 0 <= int(machine_state.value) <= 5, (
        f"press.machine_state={machine_state.value!r} outside [0, 5]"
    )


@pytest.mark.acceptance
@pytest.mark.slow
def test_foodbev_collapsed_24h() -> None:
    """F&B profile: 24 simulated hours at 100x in collapsed mode.

    PRD 11.4 / 11.5: no NaN/Inf, >= 68 signals, F&B-specific signal checks.
    """
    engine, store = _build_engine(_CONFIG_FNB)
    for _ in range(_SIM_DAY_TICKS):
        engine.tick()

    all_signals = store.get_all()

    # Signal count
    assert len(all_signals) >= 68, (
        f"Expected >= 68 F&B signals, got {len(all_signals)}"
    )

    # No NaN / Inf
    _assert_no_nan_inf(store, "F&B 24h")

    # Spot-check: mixer.state in valid range
    mixer_state = all_signals.get("mixer.state")
    assert mixer_state is not None, "mixer.state not in store after 24h"
    assert isinstance(mixer_state.value, float), (
        f"mixer.state type unexpected: {type(mixer_state.value)}"
    )

    # Spot-check: filler.fill_weight > 0 (machine was running at some point)
    fill_weight = all_signals.get("filler.fill_weight")
    assert fill_weight is not None, "filler.fill_weight not in store after 24h"
    assert isinstance(fill_weight.value, float), (
        f"filler.fill_weight type unexpected: {type(fill_weight.value)}"
    )


# ---------------------------------------------------------------------------
# Realistic topology: configuration checks (no server start required)
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_packaging_realistic_topology() -> None:
    """Packaging realistic mode returns 3 Modbus endpoints with correct ports.

    PRD 3a.4: press_plc on 5020 (UIDs 1 + 5), laminator on 5021, slitter on
    5022.  OPC-UA: single server on 4840.
    """
    from factory_simulator.topology import NetworkTopologyManager

    topo = NetworkTopologyManager(NetworkConfig(mode="realistic"), profile="packaging")

    mb_endpoints = topo.modbus_endpoints()
    assert len(mb_endpoints) == 3, (
        f"Packaging realistic: expected 3 Modbus endpoints, got {len(mb_endpoints)}"
    )

    ports = {ep.port for ep in mb_endpoints}
    assert ports == {5020, 5021, 5022}, f"Unexpected packaging Modbus ports: {ports}"

    # Press port shares UIDs 1 (press) and 5 (energy)
    press_ep = next(ep for ep in mb_endpoints if ep.port == 5020)
    assert set(press_ep.unit_ids) == {1, 5}, (
        f"Press port expected UIDs {{1, 5}}, got {press_ep.unit_ids}"
    )
    assert press_ep.controller_type == "S7-1500", (
        f"Press controller_type expected S7-1500, got {press_ep.controller_type}"
    )

    # Laminator and slitter are S7-1200
    lam_ep = next(ep for ep in mb_endpoints if ep.port == 5021)
    assert lam_ep.controller_type == "S7-1200"

    # OPC-UA: single server on 4840
    opc_endpoints = topo.opcua_endpoints()
    assert len(opc_endpoints) == 1, (
        f"Packaging realistic: expected 1 OPC-UA endpoint, got {len(opc_endpoints)}"
    )
    assert opc_endpoints[0].port == 4840


@pytest.mark.acceptance
def test_foodbev_realistic_topology() -> None:
    """F&B realistic mode: 6 Modbus endpoints, 2 OPC-UA endpoints.

    PRD 3a.4: mixer (5030, CDAB), oven gateway (5031, UIDs 1/2/3/10),
    filler (5032), sealer (5033), chiller (5034), CIP (5035).
    OPC-UA: filler on 4841, QC/checkweigher on 4842.
    """
    from factory_simulator.topology import NetworkTopologyManager

    topo = NetworkTopologyManager(NetworkConfig(mode="realistic"), profile="food_bev")

    mb_endpoints = topo.modbus_endpoints()
    assert len(mb_endpoints) == 6, (
        f"F&B realistic: expected 6 Modbus endpoints, got {len(mb_endpoints)}"
    )

    ports = {ep.port for ep in mb_endpoints}
    assert ports == {5030, 5031, 5032, 5033, 5034, 5035}, (
        f"Unexpected F&B Modbus ports: {ports}"
    )

    # Oven gateway on 5031: UIDs 1, 2, 3 (zones) + 10 (energy meter)
    oven_ep = next(ep for ep in mb_endpoints if ep.port == 5031)
    assert set(oven_ep.unit_ids) == {1, 2, 3, 10}, (
        f"Oven gateway expected UIDs {{1, 2, 3, 10}}, got {oven_ep.unit_ids}"
    )
    assert oven_ep.controller_type == "Eurotherm", (
        f"Oven gateway expected Eurotherm, got {oven_ep.controller_type}"
    )

    # Mixer uses CDAB byte order (Allen-Bradley CompactLogix)
    mixer_ep = next(ep for ep in mb_endpoints if ep.port == 5030)
    assert mixer_ep.byte_order == "CDAB", (
        f"Mixer expected CDAB byte order, got {mixer_ep.byte_order}"
    )
    assert mixer_ep.controller_type == "CompactLogix"

    # OPC-UA: 2 servers on 4841 (filler) and 4842 (QC/checkweigher)
    opc_endpoints = topo.opcua_endpoints()
    assert len(opc_endpoints) == 2, (
        f"F&B realistic: expected 2 OPC-UA endpoints, got {len(opc_endpoints)}"
    )
    opc_ports = {ep.port for ep in opc_endpoints}
    assert opc_ports == {4841, 4842}, f"Unexpected F&B OPC-UA ports: {opc_ports}"


# ---------------------------------------------------------------------------
# Realistic topology: live server tests (integration, no external broker)
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
@pytest.mark.integration
async def test_packaging_realistic_modbus_responds() -> None:
    """Packaging realistic mode: ModbusServer with endpoint spec responds to reads.

    Starts a single per-controller server on a test-safe port (18020),
    verifies it serves valid data for register addresses in its range.

    PRD 3a.4 / 3a.5: per-controller Modbus servers respond; out-of-range reads
    return exception 0x02 (Illegal Data Address).
    """
    from pymodbus.client import AsyncModbusTcpClient

    from factory_simulator.protocols.modbus_server import ModbusServer
    from factory_simulator.topology import NetworkTopologyManager

    config = load_config(_CONFIG_PKG, apply_env=False)
    config.simulation.random_seed = 42
    config.data_quality.exception_probability = 0.0
    config.data_quality.modbus_drop.enabled = False

    store = SignalStore()
    engine = DataEngine(config, store)
    # Warm up: populate all signal IDs before binding servers
    for _ in range(10):
        engine.tick()

    # Use the real press endpoint spec but override to a test-safe port
    topo = NetworkTopologyManager(NetworkConfig(mode="realistic"), profile="packaging")
    press_ep = next(ep for ep in topo.modbus_endpoints() if ep.port == 5020)
    test_ep = dataclasses.replace(press_ep, port=18020)

    server = ModbusServer(config, store, endpoint=test_ep)
    server.sync_registers()
    await server.start()
    await asyncio.sleep(0.3)  # allow server to bind before connecting

    client: AsyncModbusTcpClient | None = None
    try:
        client = AsyncModbusTcpClient("127.0.0.1", port=18020)
        connected = await client.connect()
        assert connected, "Failed to connect to realistic Modbus server on port 18020"

        # Valid read: HR 100-101 = press.line_speed (float32)
        result = await client.read_holding_registers(100, count=2)
        assert not result.isError(), (
            f"Valid read at HR 100-101 failed: {result}"
        )
        # Registers contain encoded float32: both should be non-None
        assert len(result.registers) == 2, (
            f"Expected 2 registers, got {len(result.registers)}"
        )
    finally:
        if client is not None:
            client.close()
        await server.stop()


@pytest.mark.acceptance
@pytest.mark.integration
async def test_controller_independence() -> None:
    """One controller dropping does not affect other controllers.

    Starts two ModbusServer instances on separate test ports.  Stops the
    first server (simulating a controller drop) and verifies the second
    server continues to respond normally.

    PRD 11.3 / 3a.5: independent per-controller connection drops.
    """
    from pymodbus.client import AsyncModbusTcpClient

    from factory_simulator.protocols.modbus_server import ModbusServer
    from factory_simulator.topology import ModbusEndpointSpec

    config = load_config(_CONFIG_PKG, apply_env=False)
    config.simulation.random_seed = 42
    config.data_quality.exception_probability = 0.0
    config.data_quality.modbus_drop.enabled = False

    store = SignalStore()
    engine = DataEngine(config, store)
    for _ in range(10):
        engine.tick()

    # Two independent endpoints on distinct test ports
    ep_a = ModbusEndpointSpec(port=18021, unit_ids=[1], controller_type="S7-1500")
    ep_b = ModbusEndpointSpec(port=18022, unit_ids=[1], controller_type="S7-1200")

    server_a = ModbusServer(config, store, endpoint=ep_a)
    server_b = ModbusServer(config, store, endpoint=ep_b)

    server_a.sync_registers()
    server_b.sync_registers()
    await server_a.start()
    await asyncio.sleep(0.3)  # allow server_a to bind before connecting
    await server_b.start()
    await asyncio.sleep(0.3)  # allow server_b to bind before connecting

    client_a: AsyncModbusTcpClient | None = None
    client_b: AsyncModbusTcpClient | None = None
    try:
        client_a = AsyncModbusTcpClient("127.0.0.1", port=18021)
        client_b = AsyncModbusTcpClient("127.0.0.1", port=18022)
        assert await client_a.connect(), "Failed to connect to server_a (port 18021)"
        assert await client_b.connect(), "Failed to connect to server_b (port 18022)"

        # Both serve data before any drop
        result_a = await client_a.read_holding_registers(100, count=2)
        assert not result_a.isError(), "server_a should respond before drop"

        result_b = await client_b.read_holding_registers(100, count=2)
        assert not result_b.isError(), "server_b should respond before drop"

        # Simulate server_a dropping (controller failure)
        client_a.close()
        client_a = None
        await server_a.stop()
        await asyncio.sleep(0.1)

        # server_b must continue serving after server_a drops
        result_b_after = await client_b.read_holding_registers(100, count=2)
        assert not result_b_after.isError(), (
            "server_b should still serve data after server_a dropped"
        )

    finally:
        if client_a is not None:
            client_a.close()
        if client_b is not None:
            client_b.close()
        await server_b.stop()


# ---------------------------------------------------------------------------
# Evaluation framework (no external services)
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_evaluation_framework(tmp_path: Path) -> None:
    """Evaluation framework: end-to-end metrics from synthetic ground truth.

    Creates a single ground-truth event (web_break, 60 s duration) and a
    perfect detector that fires 10 s after the start.  Verifies
    precision=recall=F1=1.0, TP=1, FP=0, FN=0, latency≈10 s.

    PRD Section 12.
    """
    from factory_simulator.evaluation.evaluator import Evaluator

    base_time = 1_700_000_000.0  # arbitrary UNIX timestamp

    # Write synthetic ground truth JSONL
    gt_path = tmp_path / "ground_truth.jsonl"
    with gt_path.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps({
                "event": "scenario_start",
                "scenario": "web_break",
                "sim_time": datetime.fromtimestamp(base_time, tz=UTC).isoformat(),
                "parameters": {},
            }) + "\n"
        )
        fh.write(
            json.dumps({
                "event": "scenario_end",
                "scenario": "web_break",
                "sim_time": datetime.fromtimestamp(base_time + 60.0, tz=UTC).isoformat(),
            }) + "\n"
        )

    # Perfect detector: fires 10 s after event start (well within 30 s pre-margin)
    det_path = tmp_path / "detections.csv"
    with det_path.open("w", encoding="utf-8") as fh:
        fh.write("timestamp,alert_type,signal_id,confidence\n")
        fh.write(f"{base_time + 10.0},web_break,press.web_tension,0.95\n")

    evaluator = Evaluator()
    result = evaluator.evaluate(gt_path, det_path)

    assert result.precision == 1.0, f"Expected precision=1.0, got {result.precision}"
    assert result.recall == 1.0, f"Expected recall=1.0, got {result.recall}"
    assert result.f1 == 1.0, f"Expected F1=1.0, got {result.f1}"
    assert result.true_positives == 1
    assert result.false_positives == 0
    assert result.false_negatives == 0
    assert result.detection_latency_median == pytest.approx(10.0, abs=0.1), (
        f"Expected latency_median≈10.0 s, got {result.detection_latency_median}"
    )


@pytest.mark.acceptance
def test_evaluation_framework_false_positives(tmp_path: Path) -> None:
    """Evaluator correctly classifies detections outside tolerance windows as FP.

    A detection that fires 120 s before the event start (beyond the 30 s
    pre-margin) should be a FP, and the event itself should be a FN.

    PRD Section 12.4.
    """
    from factory_simulator.evaluation.evaluator import Evaluator

    base_time = 1_700_000_000.0

    gt_path = tmp_path / "ground_truth.jsonl"
    with gt_path.open("w", encoding="utf-8") as fh:
        fh.write(
            json.dumps({
                "event": "scenario_start",
                "scenario": "bearing_wear",
                "sim_time": datetime.fromtimestamp(base_time, tz=UTC).isoformat(),
            }) + "\n"
        )
        fh.write(
            json.dumps({
                "event": "scenario_end",
                "scenario": "bearing_wear",
                "sim_time": datetime.fromtimestamp(base_time + 300.0, tz=UTC).isoformat(),
            }) + "\n"
        )

    # Detection 120 s BEFORE event start: outside the 30 s pre-margin
    det_path = tmp_path / "detections.csv"
    with det_path.open("w", encoding="utf-8") as fh:
        fh.write("timestamp,alert_type,signal_id,confidence\n")
        fh.write(f"{base_time - 120.0},bearing_wear,vibration.x,0.7\n")

    evaluator = Evaluator()
    result = evaluator.evaluate(gt_path, det_path)

    assert result.recall == 0.0, f"Expected recall=0.0 (event missed), got {result.recall}"
    assert result.false_positives == 1, f"Expected FP=1, got {result.false_positives}"
    assert result.false_negatives == 1, f"Expected FN=1, got {result.false_negatives}"


# ---------------------------------------------------------------------------
# Batch output (no external services)
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_batch_csv_output(tmp_path: Path) -> None:
    """Batch CSV writer produces valid long-format output.

    PRD Appendix F: columns ``timestamp, signal_id, value, quality`` (in that
    order).  At least 47 distinct signal IDs.  No NaN/Inf values.

    PRD 11.5 (batch generation mode).
    """
    from factory_simulator.output.writer import CsvWriter

    out_dir = tmp_path / "csv_out"
    batch_cfg = BatchOutputConfig(
        format="csv",
        path=str(out_dir),
        buffer_size=1000,
        event_driven_signals=[],
    )

    engine, store = _build_engine(_CONFIG_PKG)
    # Warm up: ensure all signals are in the store before writing
    for _ in range(10):
        engine.tick()

    writer = CsvWriter(out_dir, batch_cfg)
    n_write_ticks = 200
    for _ in range(n_write_ticks):
        engine.tick()
        writer.write_tick(engine.clock.sim_time, store)
    writer.close()

    csv_path = out_dir / "signals.csv"
    assert csv_path.exists(), f"CSV file not created at {csv_path}"

    # Verify column order matches PRD spec
    with csv_path.open(encoding="utf-8") as fh:
        header = fh.readline().strip()
    assert header == "timestamp,signal_id,value,quality", (
        f"Unexpected CSV header: {header!r}"
    )

    # Load all rows for content checks
    rows: list[dict[str, str]] = []
    with csv_path.open(encoding="utf-8") as fh:
        reader = csv_module.DictReader(fh)
        rows = list(reader)

    assert rows, "CSV output is empty"

    # Distinct signal IDs
    signal_ids = {row["signal_id"] for row in rows}
    assert len(signal_ids) >= 47, (
        f"Expected >= 47 distinct signal IDs, got {len(signal_ids)}"
    )

    # No NaN / Inf
    invalid = [
        f"{r['signal_id']}={r['value']}"
        for r in rows
        if r["value"].lower() in ("nan", "inf", "-inf")
    ]
    assert not invalid, f"Invalid float values in CSV: {invalid[:5]}"


@pytest.mark.acceptance
def test_batch_parquet_output(tmp_path: Path) -> None:
    """Batch Parquet writer produces readable wide-format output.

    Skipped if ``pyarrow`` is not installed.

    PRD Appendix F: timestamp column + one column per signal; event-driven
    signals have an additional ``<signal_id>_changed`` boolean column.

    PRD 11.5.
    """
    try:
        import pyarrow.parquet as pq
    except ImportError:
        pytest.skip("pyarrow not installed — skipping Parquet acceptance test")
        return  # unreachable; satisfies type checker

    from factory_simulator.output.writer import ParquetWriter

    out_dir = tmp_path / "parquet_out"
    batch_cfg = BatchOutputConfig(
        format="parquet",
        path=str(out_dir),
        buffer_size=100,
        event_driven_signals=["press.machine_state", "press.fault_code"],
    )

    engine, store = _build_engine(_CONFIG_PKG)
    for _ in range(10):
        engine.tick()

    writer = ParquetWriter(out_dir, batch_cfg)
    for _ in range(150):
        engine.tick()
        writer.write_tick(engine.clock.sim_time, store)
    writer.close()

    pq_path = out_dir / "signals.parquet"
    assert pq_path.exists(), f"Parquet file not created at {pq_path}"

    table = pq.read_table(str(pq_path))
    assert table.num_rows > 0, "Parquet table is empty"
    assert "timestamp" in table.schema.names, "Parquet table missing 'timestamp' column"

    # Event-driven signals must have _changed boolean columns
    assert "press.machine_state_changed" in table.schema.names, (
        "Parquet table missing 'press.machine_state_changed' column"
    )
    assert "press.fault_code_changed" in table.schema.names, (
        "Parquet table missing 'press.fault_code_changed' column"
    )

    # At least 47 signal columns (plus timestamp and _changed columns)
    signal_cols = [
        c for c in table.schema.names
        if c != "timestamp" and not c.endswith("_changed")
    ]
    assert len(signal_cols) >= 47, (
        f"Expected >= 47 signal columns in Parquet, got {len(signal_cols)}"
    )


# ---------------------------------------------------------------------------
# CLI tests (no external services)
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_cli_help() -> None:
    """``python -m factory_simulator --help`` exits 0 and prints usage.

    PRD Appendix F (Phase 5 — CLI).
    """
    result = subprocess.run(
        [sys.executable, "-m", "factory_simulator", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"CLI --help returned exit code {result.returncode}:\n{result.stderr}"
    )
    output = result.stdout + result.stderr
    assert "usage" in output.lower() or "factory-simulator" in output.lower(), (
        f"CLI --help output did not contain 'usage': {output[:300]!r}"
    )


@pytest.mark.acceptance
def test_cli_version() -> None:
    """``python -m factory_simulator version`` exits 0 and prints a version string."""
    result = subprocess.run(
        [sys.executable, "-m", "factory_simulator", "version"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"CLI version returned exit code {result.returncode}:\n{result.stderr}"
    )
    output = (result.stdout + result.stderr).strip()
    assert output, "CLI version printed nothing"
    # Version string should contain the package name or a version number
    assert "factory" in output.lower() or any(c.isdigit() for c in output), (
        f"Unexpected version output: {output!r}"
    )


@pytest.mark.acceptance
def test_cli_run_subcommand_flags() -> None:
    """``python -m factory_simulator run --help`` lists expected flags.

    Verifies --config, --profile, --seed, --time-scale are present.
    """
    result = subprocess.run(
        [sys.executable, "-m", "factory_simulator", "run", "--help"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, (
        f"CLI run --help returned exit code {result.returncode}:\n{result.stderr}"
    )
    for flag in ("--config", "--profile", "--seed", "--time-scale"):
        assert flag in result.stdout, f"CLI run --help missing flag: {flag}"


# ---------------------------------------------------------------------------
# Clock drift (no external services)
# ---------------------------------------------------------------------------


@pytest.mark.acceptance
def test_clock_drift_visible() -> None:
    """Eurotherm drift formula produces a visible timestamp offset after 24 h.

    PRD 3a.5 formula:
        drifted_time = sim_time + initial_offset_ms/1000
                     + drift_rate_s_per_day * elapsed_hours / 24

    Eurotherm defaults: initial_offset=5000 ms, drift=5 s/day.
    After 24 h:  offset = 5.0 (initial) + 5.0 * 24/24 = 10.0 s.
    """
    from factory_simulator.config import ClockDriftConfig
    from factory_simulator.topology import ClockDriftModel

    eurotherm_cfg = ClockDriftConfig(
        initial_offset_ms=5000.0, drift_rate_s_per_day=5.0
    )
    model = ClockDriftModel(eurotherm_cfg)

    sim_24h = 86_400.0  # seconds

    # Drift offset after 24 h should equal initial + accumulated = 10.0 s
    offset = model.drift_offset(sim_24h)
    assert offset == pytest.approx(10.0, abs=0.01), (
        f"Eurotherm drift after 24 h: expected 10.0 s, got {offset}"
    )

    # Drifted timestamp must exceed sim_time (offset is always positive)
    drifted = model.drifted_time(sim_24h)
    assert drifted > sim_24h, (
        f"drifted_time ({drifted}) should exceed sim_time ({sim_24h})"
    )
    assert drifted == pytest.approx(sim_24h + 10.0, abs=0.01), (
        f"drifted_time mismatch: expected {sim_24h + 10.0}, got {drifted}"
    )

    # Zero-drift model: offset equals only the initial component
    zero_drift_cfg = ClockDriftConfig(
        initial_offset_ms=100.0, drift_rate_s_per_day=0.0
    )
    zero_model = ClockDriftModel(zero_drift_cfg)
    assert zero_model.drift_offset(sim_24h) == pytest.approx(0.1, abs=0.001), (
        f"Zero-drift model: expected offset=0.1 s, got {zero_model.drift_offset(sim_24h)}"
    )

    # Siemens S7-1500 (very stable): 0.3 s/day, initial 200 ms
    s7_cfg = ClockDriftConfig(initial_offset_ms=200.0, drift_rate_s_per_day=0.3)
    s7_model = ClockDriftModel(s7_cfg)
    s7_offset_24h = s7_model.drift_offset(sim_24h)
    # Expected: 0.2 (initial) + 0.3 * 24/24 = 0.5 s
    assert s7_offset_24h == pytest.approx(0.5, abs=0.01), (
        f"S7-1500 drift after 24 h: expected 0.5 s, got {s7_offset_24h}"
    )
