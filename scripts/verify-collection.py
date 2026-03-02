#!/usr/bin/env python3
"""Verify CollatrEdge collection from the Factory Simulator.

Reads the JSONL metrics file produced by CollatrEdge's file output plugin
and checks signal coverage, value ranges, data continuity, and
cross-protocol consistency.

Usage:
    python3 scripts/verify-collection.py --data-dir ./data/factory-sim-packaging
    python3 scripts/verify-collection.py --data-dir ./data/factory-sim-packaging --tier medium
    python3 scripts/verify-collection.py --metrics-file ./data/factory-sim-packaging/metrics.jsonl
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
# From PRD Section 2 and Appendix A
# ---------------------------------------------------------------------------

PACKAGING_SIGNALS: dict[str, tuple[float, float, str]] = {
    # Press (21 signals)
    "press.line_speed": (0, 400, "m/min"),
    "press.web_tension": (0, 600, "N"),  # 600 to allow web break spike
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
    "press.main_drive_current": (0, 300, "A"),  # 300 for cold start spike
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
    "energy.line_power": (0, 300, "kW"),  # 300 for cold start spike
    "energy.cumulative_kwh": (0, 1e7, "kWh"),
    # Vibration (3 signals)
    "vibration.main_drive_x": (0, 50, "mm/s"),
    "vibration.main_drive_y": (0, 50, "mm/s"),
    "vibration.main_drive_z": (0, 50, "mm/s"),
}

# Signals expected on each protocol
MODBUS_SIGNALS = {
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

OPCUA_SIGNALS = set(PACKAGING_SIGNALS.keys())  # All 47 on OPC-UA

MQTT_SIGNALS = {
    "coder.state", "coder.prints_total", "coder.ink_level",
    "coder.printhead_temp", "coder.ink_pump_speed", "coder.ink_pressure",
    "coder.ink_viscosity_actual", "coder.supply_voltage",
    "coder.ink_consumption_ml", "coder.nozzle_health", "coder.gutter_fault",
    "env.ambient_temp", "env.ambient_humidity",
    "vibration.main_drive_x", "vibration.main_drive_y", "vibration.main_drive_z",
}

# Monotonic counters (should not decrease within a job)
MONOTONIC_SIGNALS = {
    "press.impression_count", "press.good_count", "press.waste_count",
    "coder.prints_total", "coder.ink_consumption_ml",
    "energy.cumulative_kwh", "slitter.reel_count",
}


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
    """Extract the signal name from a metric record.

    CollatrEdge writes metrics with different structures depending on
    the input plugin. This function tries common patterns.
    """
    # Direct name field
    if "name" in metric:
        return str(metric["name"])

    # Fields dict with a single key
    fields = metric.get("fields", {})
    if len(fields) == 1:
        return next(iter(fields.keys()))

    # Measurement name
    if "measurement" in metric:
        return str(metric["measurement"])

    return None


def extract_value(metric: dict) -> float | None:
    """Extract numeric value from a metric record."""
    # Direct value field
    if "value" in metric:
        v = metric["value"]
        if isinstance(v, (int, float)):
            return float(v)

    # Fields dict
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
        # Nanoseconds or seconds
        if ts > 1e15:  # nanoseconds
            return ts / 1e9
        if ts > 1e12:  # milliseconds
            return ts / 1e3
        return float(ts)
    return None


def check_signal_coverage(
    signal_data: dict[str, list[dict]], tier: str
) -> tuple[int, int]:
    """Check that all 47 signals were collected."""
    passes = 0
    fails = 0

    print("\n=== Signal Coverage ===")
    missing = set(PACKAGING_SIGNALS.keys()) - set(signal_data.keys())
    if missing:
        print(f"  FAIL: {len(missing)} signals missing:")
        for s in sorted(missing):
            print(f"    - {s}")
        fails += len(missing)
    else:
        print(f"  PASS: All {len(PACKAGING_SIGNALS)} signals collected")
        passes += 1

    # Check data point counts
    for name in sorted(PACKAGING_SIGNALS.keys()):
        count = len(signal_data.get(name, []))
        if count == 0 and name not in missing:
            print(f"  FAIL: {name} has 0 data points")
            fails += 1
        elif count > 0:
            passes += 1

    return passes, fails


def check_value_ranges(
    signal_data: dict[str, list[dict]],
) -> tuple[int, int]:
    """Check all values are within PRD-specified ranges."""
    passes = 0
    fails = 0

    print("\n=== Value Ranges ===")
    for name, (vmin, vmax, units) in sorted(PACKAGING_SIGNALS.items()):
        values = [extract_value(m) for m in signal_data.get(name, [])]
        values = [v for v in values if v is not None]

        if not values:
            continue

        # Check for NaN/Inf
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
) -> tuple[int, int]:
    """Check monotonic counters don't decrease."""
    passes = 0
    fails = 0

    print("\n=== Monotonic Counters ===")
    for name in sorted(MONOTONIC_SIGNALS):
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
                f"(may be counter reset on job change)"
            )
            # Not a hard fail: counters can reset on job changeover
        else:
            print(f"  PASS: {name} monotonically non-decreasing ({len(values)} points)")
            passes += 1

    return passes, fails


def check_state_transitions(
    signal_data: dict[str, list[dict]], tier: str
) -> tuple[int, int]:
    """Check that state transitions occurred (medium/full tiers)."""
    passes = 0
    fails = 0

    print("\n=== State Transitions ===")
    states = [extract_value(m) for m in signal_data.get("press.machine_state", [])]
    states = [int(s) for s in states if s is not None]

    unique_states = set(states)
    print(f"  Observed states: {sorted(unique_states)}")

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
            print("  WARN: Running state (2) never observed")
    else:
        passes += 1  # Smoke tier: just report

    return passes, fails


def check_cross_protocol(
    signal_data: dict[str, list[dict]],
) -> tuple[int, int]:
    """Basic cross-protocol presence check."""
    passes = 0
    fails = 0

    print("\n=== Cross-Protocol Coverage ===")

    # Check each protocol contributed data
    # This is a simplified check: look for signals unique to each protocol
    modbus_only = {"press.nip_pressure", "press.ink_viscosity"}
    opcua_only = {"press.registration_error_x", "press.registration_error_y"}
    mqtt_only = {"vibration.main_drive_x", "coder.state", "env.ambient_temp"}

    for label, signals in [
        ("Modbus", modbus_only),
        ("OPC-UA", opcua_only),
        ("MQTT", mqtt_only),
    ]:
        found = [s for s in signals if signal_data.get(s)]
        if found:
            print(f"  PASS: {label} data present ({len(found)} indicator signals)")
            passes += 1
        else:
            print(f"  FAIL: No {label} data found")
            fails += 1

    return passes, fails


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

    print(f"Loading metrics from: {metrics_path}")
    metrics = load_metrics(metrics_path)
    print(f"Loaded {len(metrics)} metric records")

    if not metrics:
        print("ERROR: No metrics found. Is CollatrEdge collecting data?")
        return 1

    # Group by signal name
    signal_data: dict[str, list[dict]] = defaultdict(list)
    unmatched = 0
    for m in metrics:
        name = extract_signal_name(m)
        if name and name in PACKAGING_SIGNALS:
            signal_data[name].append(m)
        elif name and name.endswith("_ir"):
            # IR duplicates: skip (they're the same signal via input registers)
            pass
        elif name and name.startswith(("press.", "laminator.", "slitter.",
                                       "coder.", "env.", "energy.", "vibration.")):
            signal_data[name].append(m)
        else:
            unmatched += 1

    print(f"Matched {len(signal_data)} unique signals, {unmatched} unmatched records")
    print(f"Test tier: {args.tier}")

    # Run checks
    total_pass = 0
    total_fail = 0

    for check_fn in [
        lambda: check_signal_coverage(signal_data, args.tier),
        lambda: check_value_ranges(signal_data),
        lambda: check_monotonic(signal_data),
        lambda: check_state_transitions(signal_data, args.tier),
        lambda: check_cross_protocol(signal_data),
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
