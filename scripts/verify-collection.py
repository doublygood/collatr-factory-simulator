#!/usr/bin/env python3
"""Verify CollatrEdge collection from the Factory Simulator.

Reads the JSONL metrics file produced by CollatrEdge's file output plugin
and checks signal coverage, value ranges, data continuity, and
cross-protocol consistency for BOTH packaging and F&B profiles.

Usage:
    # Packaging (default)
    python3 scripts/verify-collection.py --data-dir ./data/factory-sim-packaging
    python3 scripts/verify-collection.py --data-dir ./data/factory-sim-packaging --tier medium

    # F&B
    python3 scripts/verify-collection.py --data-dir ./data/factory-sim-foodbev --profile foodbev

    # F&B from JSONL
    python3 scripts/verify-collection.py --metrics-file ./data/metrics.jsonl --profile foodbev --tier medium
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

# ---------------------------------------------------------------------------
# PRD signal definitions: name -> (min, max, units)
# ---------------------------------------------------------------------------

PACKAGING_SIGNALS: dict[str, tuple[float, float, str]] = {
    # Press (21 signals)
    "press.line_speed": (0, 400, "m/min"),
    "press.web_tension": (0, 600, "N"),
    "press.registration_error_x": (-0.5, 0.5, "mm"),
    "press.registration_error_y": (-0.5, 0.5, "mm"),
    "press.ink_viscosity": (10, 65, "seconds"),
    "press.ink_temperature": (10, 45, "C"),
    "press.dryer_temp_zone_1": (15, 130, "C"),
    "press.dryer_temp_zone_2": (15, 130, "C"),
    "press.dryer_temp_zone_3": (15, 130, "C"),
    "press.dryer_setpoint_zone_1": (40, 120, "C"),
    "press.dryer_setpoint_zone_2": (40, 120, "C"),
    "press.dryer_setpoint_zone_3": (40, 120, "C"),
    "press.impression_count": (0, 1e10, "count"),
    "press.good_count": (0, 1e10, "count"),
    "press.waste_count": (0, 1e6, "count"),
    "press.machine_state": (0, 5, "enum"),
    "press.fault_code": (0, 1000, "code"),
    "press.main_drive_current": (0, 300, "A"),
    "press.main_drive_speed": (0, 4000, "RPM"),
    "press.nip_pressure": (0, 12, "bar"),
    "press.unwind_diameter": (0, 1600, "mm"),
    "press.rewind_diameter": (0, 1600, "mm"),
    # Laminator (5 signals)
    "laminator.nip_temp": (15, 100, "C"),
    "laminator.nip_pressure": (0, 10, "bar"),
    "laminator.tunnel_temp": (15, 120, "C"),
    "laminator.web_speed": (0, 400, "m/min"),
    "laminator.adhesive_weight": (0.5, 6, "g/m2"),
    # Slitter (3 signals)
    "slitter.speed": (0, 800, "m/min"),
    "slitter.web_tension": (0, 200, "N"),
    "slitter.reel_count": (0, 1e6, "count"),
    # Coder (11 signals)
    "coder.state": (0, 4, "enum"),
    "coder.prints_total": (0, 1e10, "count"),
    "coder.ink_level": (0, 100, "%"),
    "coder.printhead_temp": (20, 55, "C"),
    "coder.ink_pump_speed": (0, 550, "RPM"),
    "coder.ink_pressure": (0, 950, "mbar"),
    "coder.ink_viscosity_actual": (1, 18, "cP"),
    "coder.supply_voltage": (20, 28, "V"),
    "coder.ink_consumption_ml": (0, 1e6, "ml"),
    "coder.nozzle_health": (0, 100, "%"),
    "coder.gutter_fault": (0, 1, "bool"),
    # Environment (2 signals)
    "env.ambient_temp": (10, 40, "C"),
    "env.ambient_humidity": (20, 90, "%RH"),
    # Energy (2 signals)
    "energy.line_power": (0, 300, "kW"),
    "energy.cumulative_kwh": (0, 1e7, "kWh"),
    # Vibration (3 signals)
    "vibration.main_drive_x": (0, 50, "mm/s"),
    "vibration.main_drive_y": (0, 50, "mm/s"),
    "vibration.main_drive_z": (0, 50, "mm/s"),
}

FOODBEV_SIGNALS: dict[str, tuple[float, float, str]] = {
    # Mixer (8 signals)
    "mixer.speed": (0, 3000, "RPM"),
    "mixer.torque": (0, 100, "%"),
    "mixer.batch_temp": (-5, 95, "C"),
    "mixer.batch_weight": (0, 2000, "kg"),
    "mixer.state": (0, 5, "enum"),
    "mixer.batch_id": (0, 0, "string"),  # string signal
    "mixer.mix_time_elapsed": (0, 3600, "s"),
    "mixer.lid_closed": (0, 1, "bool"),
    # Oven (13 signals)
    "oven.zone_1_temp": (15, 280, "C"),
    "oven.zone_2_temp": (15, 280, "C"),
    "oven.zone_3_temp": (15, 280, "C"),
    "oven.zone_1_setpoint": (80, 280, "C"),
    "oven.zone_2_setpoint": (80, 280, "C"),
    "oven.zone_3_setpoint": (80, 280, "C"),
    "oven.belt_speed": (0, 10, "m/min"),
    "oven.product_core_temp": (-5, 95, "C"),
    "oven.humidity_zone_2": (20, 95, "%RH"),
    "oven.state": (0, 4, "enum"),
    "oven.zone_1_output_power": (0, 100, "%"),
    "oven.zone_2_output_power": (0, 100, "%"),
    "oven.zone_3_output_power": (0, 100, "%"),
    # Filler (8 signals)
    "filler.line_speed": (0, 120, "packs/min"),
    "filler.fill_weight": (0, 1000, "g"),
    "filler.fill_target": (100, 800, "g"),
    "filler.fill_deviation": (-50, 50, "g"),
    "filler.packs_produced": (0, 1e8, "count"),
    "filler.reject_count": (0, 1e6, "count"),
    "filler.state": (0, 4, "enum"),
    "filler.hopper_level": (0, 100, "%"),
    # Sealer (6 signals)
    "sealer.seal_temp": (100, 250, "C"),
    "sealer.seal_pressure": (0, 10, "bar"),
    "sealer.seal_dwell": (0, 5, "s"),
    "sealer.gas_co2_pct": (0, 100, "%"),
    "sealer.gas_n2_pct": (0, 100, "%"),
    "sealer.vacuum_level": (-1, 0.1, "bar"),
    # QC (6 signals)
    "qc.actual_weight": (0, 1000, "g"),
    "qc.overweight_count": (0, 1e6, "count"),
    "qc.underweight_count": (0, 1e6, "count"),
    "qc.metal_detect_trips": (0, 1000, "count"),
    "qc.throughput": (0, 200, "items/min"),
    "qc.reject_total": (0, 1e6, "count"),
    # Chiller (7 signals)
    "chiller.room_temp": (-5, 25, "C"),
    "chiller.setpoint": (-5, 15, "C"),
    "chiller.compressor_state": (0, 1, "bool"),
    "chiller.suction_pressure": (0, 30, "bar"),
    "chiller.discharge_pressure": (0, 30, "bar"),
    "chiller.defrost_active": (0, 1, "bool"),
    "chiller.door_open": (0, 1, "bool"),
    # CIP (5 signals)
    "cip.state": (0, 5, "enum"),
    "cip.wash_temp": (15, 90, "C"),
    "cip.flow_rate": (0, 200, "L/min"),
    "cip.conductivity": (0, 200, "mS/cm"),
    "cip.cycle_time_elapsed": (0, 7200, "s"),
    # Coder (11 signals — shared with packaging)
    "coder.state": (0, 4, "enum"),
    "coder.prints_total": (0, 1e10, "count"),
    "coder.ink_level": (0, 100, "%"),
    "coder.printhead_temp": (20, 55, "C"),
    "coder.ink_pump_speed": (0, 550, "RPM"),
    "coder.ink_pressure": (0, 950, "mbar"),
    "coder.ink_viscosity_actual": (1, 18, "cP"),
    "coder.supply_voltage": (20, 28, "V"),
    "coder.ink_consumption_ml": (0, 1e6, "ml"),
    "coder.nozzle_health": (0, 100, "%"),
    "coder.gutter_fault": (0, 1, "bool"),
    # Environment (2 signals — shared)
    "env.ambient_temp": (5, 30, "C"),
    "env.ambient_humidity": (20, 90, "%RH"),
    # Energy (2 signals — shared, higher base for F&B)
    "energy.line_power": (0, 300, "kW"),
    "energy.cumulative_kwh": (0, 1e7, "kWh"),
}

# ---------------------------------------------------------------------------
# Per-protocol signal sets (packaging)
# ---------------------------------------------------------------------------

PACKAGING_MODBUS_SIGNALS = {
    "press.line_speed", "press.web_tension", "press.ink_viscosity",
    "press.ink_temperature", "press.dryer_temp_zone_1", "press.dryer_temp_zone_2",
    "press.dryer_temp_zone_3", "press.dryer_setpoint_zone_1",
    "press.dryer_setpoint_zone_2", "press.dryer_setpoint_zone_3",
    "press.impression_count", "press.good_count", "press.waste_count",
    "press.machine_state", "press.fault_code", "press.main_drive_current",
    "press.main_drive_speed", "press.nip_pressure", "press.unwind_diameter",
    "press.rewind_diameter", "laminator.nip_temp", "laminator.nip_pressure",
    "laminator.tunnel_temp", "laminator.web_speed", "laminator.adhesive_weight",
    "slitter.speed", "slitter.web_tension", "slitter.reel_count",
    "energy.line_power", "energy.cumulative_kwh",
}

PACKAGING_OPCUA_SIGNALS = set(PACKAGING_SIGNALS.keys())

PACKAGING_MQTT_SIGNALS = {
    "coder.state", "coder.prints_total", "coder.ink_level",
    "coder.printhead_temp", "coder.ink_pump_speed", "coder.ink_pressure",
    "coder.ink_viscosity_actual", "coder.supply_voltage",
    "coder.ink_consumption_ml", "coder.nozzle_health", "coder.gutter_fault",
    "env.ambient_temp", "env.ambient_humidity",
    "vibration.main_drive_x", "vibration.main_drive_y", "vibration.main_drive_z",
}

# ---------------------------------------------------------------------------
# Per-protocol signal sets (F&B)
# ---------------------------------------------------------------------------

FOODBEV_MODBUS_SIGNALS = {
    "mixer.speed", "mixer.torque", "mixer.batch_temp", "mixer.batch_weight",
    "mixer.mix_time_elapsed", "mixer.lid_closed",
    "oven.zone_1_temp", "oven.zone_2_temp", "oven.zone_3_temp",
    "oven.zone_1_setpoint", "oven.zone_2_setpoint", "oven.zone_3_setpoint",
    "oven.belt_speed", "oven.product_core_temp", "oven.humidity_zone_2",
    "oven.zone_1_output_power", "oven.zone_2_output_power", "oven.zone_3_output_power",
    "filler.hopper_level",
    "sealer.seal_temp", "sealer.seal_pressure", "sealer.seal_dwell",
    "sealer.gas_co2_pct", "sealer.gas_n2_pct", "sealer.vacuum_level",
    "chiller.room_temp", "chiller.setpoint", "chiller.compressor_state",
    "chiller.suction_pressure", "chiller.discharge_pressure",
    "chiller.defrost_active", "chiller.door_open",
    "cip.wash_temp", "cip.flow_rate", "cip.conductivity", "cip.cycle_time_elapsed",
    "energy.line_power", "energy.cumulative_kwh",
}

FOODBEV_OPCUA_SIGNALS = {
    "mixer.state", "mixer.batch_id",
    "oven.state",
    "filler.line_speed", "filler.fill_weight", "filler.fill_target",
    "filler.fill_deviation", "filler.packs_produced", "filler.reject_count",
    "filler.state",
    "qc.actual_weight", "qc.overweight_count", "qc.underweight_count",
    "qc.metal_detect_trips", "qc.throughput", "qc.reject_total",
    "cip.state",
    "energy.line_power", "energy.cumulative_kwh",
}

FOODBEV_MQTT_SIGNALS = {
    "coder.state", "coder.prints_total", "coder.ink_level",
    "coder.printhead_temp", "coder.ink_pump_speed", "coder.ink_pressure",
    "coder.ink_viscosity_actual", "coder.supply_voltage",
    "coder.ink_consumption_ml", "coder.nozzle_health", "coder.gutter_fault",
    "env.ambient_temp", "env.ambient_humidity",
}

# ---------------------------------------------------------------------------
# Monotonic counters
# ---------------------------------------------------------------------------

PACKAGING_MONOTONIC = {
    "press.impression_count", "press.good_count", "press.waste_count",
    "coder.prints_total", "coder.ink_consumption_ml",
    "energy.cumulative_kwh", "slitter.reel_count",
}

FOODBEV_MONOTONIC = {
    "filler.packs_produced", "filler.reject_count",
    "qc.overweight_count", "qc.underweight_count",
    "qc.metal_detect_trips", "qc.reject_total",
    "coder.prints_total", "coder.ink_consumption_ml",
    "energy.cumulative_kwh",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_metrics(path: Path) -> list[dict]:
    """Load JSONL metrics file."""
    metrics: list[dict] = []
    with open(path) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                metrics.append(json.loads(line))
            except json.JSONDecodeError as e:
                print(f"  WARNING: invalid JSON on line {line_num}: {e}")
    return metrics


def extract_signal_name(metric: dict) -> str | None:
    """Extract the signal name from a metric record."""
    if "name" in metric:
        return str(metric["name"])
    fields = metric.get("fields", {})
    if len(fields) == 1:
        return next(iter(fields.keys()))
    if "measurement" in metric:
        return str(metric["measurement"])
    return None


def extract_value(metric: dict) -> float | None:
    """Extract numeric value from a metric record."""
    if "value" in metric:
        v = metric["value"]
        if isinstance(v, (int, float)):
            return float(v)
    fields = metric.get("fields", {})
    for v in fields.values():
        if isinstance(v, (int, float)):
            return float(v)
    return None


def extract_timestamp(metric: dict) -> float | None:
    """Extract timestamp as float seconds."""
    ts = metric.get("timestamp")
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        if ts > 1e15:
            return ts / 1e9
        if ts > 1e12:
            return ts / 1e3
        return float(ts)
    return None


# ---------------------------------------------------------------------------
# Check functions
# ---------------------------------------------------------------------------


def check_signal_coverage(
    signal_data: dict[str, list[dict]],
    signal_defs: dict[str, tuple[float, float, str]],
    profile_name: str,
) -> tuple[int, int]:
    """Check that all signals were collected."""
    passes = 0
    fails = 0

    print(f"\n=== {profile_name} Signal Coverage ===")
    missing = set(signal_defs.keys()) - set(signal_data.keys())
    if missing:
        print(f"  FAIL: {len(missing)} signals missing:")
        for s in sorted(missing):
            print(f"    - {s}")
        fails += len(missing)
    else:
        print(f"  PASS: All {len(signal_defs)} signals collected")
        passes += 1

    for name in sorted(signal_defs.keys()):
        count = len(signal_data.get(name, []))
        if count == 0 and name not in missing:
            print(f"  FAIL: {name} has 0 data points")
            fails += 1
        elif count > 0:
            passes += 1

    return passes, fails


def check_value_ranges(
    signal_data: dict[str, list[dict]],
    signal_defs: dict[str, tuple[float, float, str]],
    profile_name: str,
) -> tuple[int, int]:
    """Check all values are within PRD-specified ranges."""
    passes = 0
    fails = 0

    print(f"\n=== {profile_name} Value Ranges ===")
    for name, (vmin, vmax, units) in sorted(signal_defs.items()):
        if units == "string":
            continue  # Skip string signals

        values = [extract_value(m) for m in signal_data.get(name, [])]
        values = [v for v in values if v is not None]

        if not values:
            continue

        bad = [v for v in values if math.isnan(v) or math.isinf(v)]
        if bad:
            print(f"  FAIL: {name} has {len(bad)} NaN/Inf values")
            fails += 1
            continue

        actual_min = min(values)
        actual_max = max(values)

        if actual_min < vmin or actual_max > vmax:
            print(
                f"  FAIL: {name} out of range "
                f"[{actual_min:.2f}, {actual_max:.2f}] "
                f"vs expected [{vmin}, {vmax}] {units}"
            )
            fails += 1
        else:
            passes += 1

    if fails == 0:
        print(f"  PASS: All signals within expected ranges")

    return passes, fails


def check_monotonic(
    signal_data: dict[str, list[dict]],
    monotonic_signals: set[str],
    profile_name: str,
) -> tuple[int, int]:
    """Check monotonic counters don't decrease."""
    passes = 0
    fails = 0

    print(f"\n=== {profile_name} Monotonic Counters ===")
    for name in sorted(monotonic_signals):
        metrics = signal_data.get(name, [])
        values = [extract_value(m) for m in metrics]
        values = [v for v in values if v is not None]

        if len(values) < 2:
            continue

        decreases = sum(
            1 for i in range(1, len(values)) if values[i] < values[i - 1]
        )
        if decreases > 0:
            print(
                f"  WARN: {name} decreased {decreases} times "
                f"(may be counter reset on job/shift change)"
            )
        else:
            print(f"  PASS: {name} monotonically non-decreasing ({len(values)} points)")
            passes += 1

    return passes, fails


def check_state_transitions(
    signal_data: dict[str, list[dict]],
    state_signal: str,
    profile_name: str,
    tier: str,
) -> tuple[int, int]:
    """Check that state transitions occurred (medium/full tiers)."""
    passes = 0
    fails = 0

    print(f"\n=== {profile_name} State Transitions ===")
    states = [extract_value(m) for m in signal_data.get(state_signal, [])]
    states = [int(s) for s in states if s is not None]

    if not states:
        print(f"  WARN: No data for {state_signal}")
        return passes, fails

    unique_states = set(states)
    print(f"  Observed states for {state_signal}: {sorted(unique_states)}")

    transitions = sum(1 for i in range(1, len(states)) if states[i] != states[i - 1])
    print(f"  State transitions: {transitions}")

    if tier in ("medium", "full"):
        if transitions < 1:
            print("  FAIL: No state transitions observed (expected at least 1)")
            fails += 1
        else:
            print(f"  PASS: {transitions} state transitions")
            passes += 1

        if 2 not in unique_states:
            print(f"  WARN: Running state (2) never observed for {state_signal}")
    else:
        passes += 1

    return passes, fails


def check_cross_protocol(
    signal_data: dict[str, list[dict]],
    modbus_signals: set[str],
    opcua_signals: set[str],
    mqtt_signals: set[str],
    profile_name: str,
) -> tuple[int, int]:
    """Basic cross-protocol presence check."""
    passes = 0
    fails = 0

    print(f"\n=== {profile_name} Cross-Protocol Coverage ===")

    for label, signals in [
        ("Modbus", modbus_signals),
        ("OPC-UA", opcua_signals),
        ("MQTT", mqtt_signals),
    ]:
        found = [s for s in signals if signal_data.get(s)]
        if found:
            print(f"  PASS: {label} data present ({len(found)}/{len(signals)} signals)")
            passes += 1
        else:
            print(f"  FAIL: No {label} data found")
            fails += 1

    return passes, fails


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify CollatrEdge collection from Factory Simulator"
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        help="Directory containing CollatrEdge output data",
    )
    parser.add_argument(
        "--metrics-file",
        type=Path,
        help="Path to metrics.jsonl file (alternative to --data-dir)",
    )
    parser.add_argument(
        "--profile",
        choices=["packaging", "foodbev"],
        default="packaging",
        help="Factory profile to verify (default: packaging)",
    )
    parser.add_argument(
        "--tier",
        choices=["smoke", "medium", "full"],
        default="smoke",
        help="Test tier (default: smoke)",
    )
    args = parser.parse_args()

    # Find metrics file
    if args.metrics_file:
        metrics_path = args.metrics_file
    elif args.data_dir:
        metrics_path = args.data_dir / "metrics.jsonl"
    else:
        parser.error("Specify --data-dir or --metrics-file")
        return 1

    if not metrics_path.exists():
        print(f"ERROR: Metrics file not found: {metrics_path}")
        print("Did CollatrEdge write output? Check the file output config.")
        return 1

    # Select profile definitions
    if args.profile == "foodbev":
        signal_defs = FOODBEV_SIGNALS
        modbus_sigs = FOODBEV_MODBUS_SIGNALS
        opcua_sigs = FOODBEV_OPCUA_SIGNALS
        mqtt_sigs = FOODBEV_MQTT_SIGNALS
        monotonic_sigs = FOODBEV_MONOTONIC
        state_signal = "mixer.state"
        profile_name = "F&B"
    else:
        signal_defs = PACKAGING_SIGNALS
        modbus_sigs = PACKAGING_MODBUS_SIGNALS
        opcua_sigs = PACKAGING_OPCUA_SIGNALS
        mqtt_sigs = PACKAGING_MQTT_SIGNALS
        monotonic_sigs = PACKAGING_MONOTONIC
        state_signal = "press.machine_state"
        profile_name = "Packaging"

    print(f"Loading metrics from: {metrics_path}")
    metrics = load_metrics(metrics_path)
    print(f"Loaded {len(metrics)} metric records")
    print(f"Profile: {args.profile} ({profile_name})")
    print(f"Test tier: {args.tier}")

    if not metrics:
        print("ERROR: No metrics found. Is CollatrEdge collecting data?")
        return 1

    # Group by signal name
    signal_data: dict[str, list[dict]] = defaultdict(list)
    unmatched = 0

    # Build a set of all known signal name prefixes for matching
    known_prefixes = set()
    for sig in signal_defs:
        prefix = sig.split(".")[0]
        known_prefixes.add(prefix)

    for m in metrics:
        name = extract_signal_name(m)
        if name and name in signal_defs:
            signal_data[name].append(m)
        elif name and name.endswith("_ir"):
            pass  # IR duplicates: skip
        elif name:
            prefix = name.split(".")[0] if "." in name else ""
            if prefix in known_prefixes:
                signal_data[name].append(m)
            else:
                unmatched += 1
        else:
            unmatched += 1

    print(f"Matched {len(signal_data)} unique signals, {unmatched} unmatched records")

    # Run checks
    total_pass = 0
    total_fail = 0

    for check_fn in [
        lambda: check_signal_coverage(signal_data, signal_defs, profile_name),
        lambda: check_value_ranges(signal_data, signal_defs, profile_name),
        lambda: check_monotonic(signal_data, monotonic_sigs, profile_name),
        lambda: check_state_transitions(signal_data, state_signal, profile_name, args.tier),
        lambda: check_cross_protocol(signal_data, modbus_sigs, opcua_sigs, mqtt_sigs, profile_name),
    ]:
        p, f = check_fn()
        total_pass += p
        total_fail += f

    # Summary
    print("\n" + "=" * 60)
    print(f"RESULTS: {total_pass} passed, {total_fail} failed")
    print("=" * 60)

    if total_fail > 0:
        print("\nVERDICT: FAIL")
        return 1
    else:
        print("\nVERDICT: PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
