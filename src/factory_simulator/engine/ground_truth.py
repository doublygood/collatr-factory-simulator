"""Ground truth event log -- JSONL sidecar for scenario and state events.

The GroundTruthLogger writes one JSON object per line to a configurable
output file.  The first line is a configuration header record.  All
subsequent lines are event records.

The logger is write-only and append-only.  It never reads the file.
Protocol adapters and generators push events through simple method calls.

PRD Reference: Section 4.7 (Ground Truth Event Log)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory_simulator.config import FactoryConfig

logger = logging.getLogger(__name__)

# Simulator version embedded in config header
_SIM_VERSION = "1.0.0"


class GroundTruthLogger:
    """Append-only JSONL event logger.

    Parameters
    ----------
    path:
        Output file path.  Parent directories are created if needed.
    """

    def __init__(self, path: str | Path = "output/ground_truth.jsonl") -> None:
        self._path = Path(path)
        self._fh: Any = None

    # -- Lifecycle -------------------------------------------------------------

    def open(self) -> None:
        """Open the output file for writing (truncate if exists)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("w", encoding="utf-8")
        logger.info("Ground truth log opened: %s", self._path)

    def close(self) -> None:
        """Flush and close the output file."""
        if self._fh is not None:
            self._fh.close()
            self._fh = None
            logger.info("Ground truth log closed: %s", self._path)

    # -- Header ----------------------------------------------------------------

    def write_header(self, config: FactoryConfig) -> None:
        """Write the config header record (first line).

        PRD 4.7: Contains simulator version, random seed, profile name,
        per-signal noise parameters, and active scenario list.
        """
        signals: dict[str, dict[str, Any]] = {}
        for eq_id, eq_cfg in config.equipment.items():
            for sig_id, sig_cfg in eq_cfg.signals.items():
                key = f"{eq_id}.{sig_id}"
                entry: dict[str, Any] = {
                    "noise": sig_cfg.noise_type,
                    "sigma": sig_cfg.noise_sigma,
                }
                if sig_cfg.noise_df is not None:
                    entry["df"] = sig_cfg.noise_df
                if sig_cfg.noise_phi is not None:
                    entry["phi"] = sig_cfg.noise_phi
                if sig_cfg.sigma_base is not None:
                    entry["sigma_base"] = sig_cfg.sigma_base
                if sig_cfg.sigma_scale != 0.0:
                    entry["sigma_scale"] = sig_cfg.sigma_scale
                signals[key] = entry

        # Collect enabled scenarios
        scenarios_list: list[str] = []
        scfg = config.scenarios
        if scfg.job_changeover.enabled:
            scenarios_list.append("job_changeover")
        if scfg.web_break.enabled:
            scenarios_list.append("web_break")
        if scfg.dryer_drift.enabled:
            scenarios_list.append("dryer_drift")
        if scfg.bearing_wear.enabled:
            scenarios_list.append("bearing_wear")
        if scfg.ink_viscosity_excursion.enabled:
            scenarios_list.append("ink_viscosity_excursion")
        if scfg.registration_drift.enabled:
            scenarios_list.append("registration_drift")
        if scfg.unplanned_stop.enabled:
            scenarios_list.append("unplanned_stop")
        if scfg.shift_change.enabled:
            scenarios_list.append("shift_change")
        if scfg.cold_start_spike.enabled:
            scenarios_list.append("cold_start_spike")
        if scfg.coder_depletion.enabled:
            scenarios_list.append("coder_depletion")
        if scfg.material_splice.enabled:
            scenarios_list.append("material_splice")

        header = {
            "event_type": "config",
            "sim_version": _SIM_VERSION,
            "seed": config.simulation.random_seed,
            "profile": config.factory.name,
            "signals": signals,
            "scenarios": scenarios_list,
        }
        self._write_line(header)

    # -- Event writers ---------------------------------------------------------

    def log_scenario_start(
        self,
        sim_time: float,
        scenario_name: str,
        affected_signals: list[str],
        parameters: dict[str, Any] | None = None,
    ) -> None:
        """Log a scenario_start event (PRD 4.7)."""
        record: dict[str, Any] = {
            "sim_time": self._format_time(sim_time),
            "event": "scenario_start",
            "scenario": scenario_name,
            "affected_signals": affected_signals,
        }
        if parameters:
            record["parameters"] = parameters
        self._write_line(record)

    def log_scenario_end(
        self,
        sim_time: float,
        scenario_name: str,
    ) -> None:
        """Log a scenario_end event (PRD 4.7)."""
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "scenario_end",
            "scenario": scenario_name,
        })

    def log_state_change(
        self,
        sim_time: float,
        signal: str,
        from_state: int | str,
        to_state: int | str,
    ) -> None:
        """Log a state_change event (PRD 4.7)."""
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "state_change",
            "signal": signal,
            "from": from_state,
            "to": to_state,
        })

    def log_signal_anomaly(
        self,
        sim_time: float,
        signal: str,
        anomaly_type: str,
        value: float,
        normal_range: list[float],
    ) -> None:
        """Log a signal_anomaly event (PRD 4.7)."""
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "signal_anomaly",
            "signal": signal,
            "anomaly_type": anomaly_type,
            "value": value,
            "normal_range": normal_range,
        })

    def log_contextual_anomaly(
        self,
        sim_time: float,
        anomaly_type: str,
        signal: str,
        injected_value: float,
        expected_state: int,
        actual_state: int,
    ) -> None:
        """Log a contextual_anomaly injection start (PRD 5.16, Task 4.6).

        Parameters record: event type, affected signal, injected value,
        expected state (where the value would be normal), and actual state
        (where it is anomalous), per PRD Section 5.16.
        """
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "contextual_anomaly",
            "anomaly_type": anomaly_type,
            "signal": signal,
            "injected_value": injected_value,
            "expected_state": expected_state,
            "actual_state": actual_state,
        })

    def log_data_quality(
        self,
        sim_time: float,
        protocol: str,
        duration: float,
        description: str | None = None,
    ) -> None:
        """Log a data_quality event (PRD 4.7)."""
        record: dict[str, Any] = {
            "sim_time": self._format_time(sim_time),
            "event": "data_quality",
            "protocol": protocol,
            "duration": duration,
        }
        if description:
            record["description"] = description
        self._write_line(record)

    def log_micro_stop(
        self,
        sim_time: float,
        duration: float,
        speed_reduction_pct: float,
    ) -> None:
        """Log a micro_stop event (PRD 4.7)."""
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "micro_stop",
            "duration": duration,
            "speed_reduction_pct": speed_reduction_pct,
        })

    def log_shift_change(
        self,
        sim_time: float,
        old_shift: str,
        new_shift: str,
    ) -> None:
        """Log a shift_change event (PRD 4.7)."""
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "shift_change",
            "old_shift": old_shift,
            "new_shift": new_shift,
        })

    def log_consumable(
        self,
        sim_time: float,
        signal: str,
        new_value: float,
        description: str | None = None,
    ) -> None:
        """Log a consumable event (ink refill, material splice, etc.)."""
        record: dict[str, Any] = {
            "sim_time": self._format_time(sim_time),
            "event": "consumable",
            "signal": signal,
            "new_value": new_value,
        }
        if description:
            record["description"] = description
        self._write_line(record)

    def log_sensor_disconnect(
        self,
        sim_time: float,
        signal: str,
        sentinel_value: float | str,
    ) -> None:
        """Log a sensor_disconnect event (PRD 4.7)."""
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "sensor_disconnect",
            "signal": signal,
            "sentinel_value": sentinel_value,
        })

    def log_stuck_sensor(
        self,
        sim_time: float,
        signal: str,
        frozen_value: float,
        duration: float,
    ) -> None:
        """Log a stuck_sensor event (PRD 4.7)."""
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "stuck_sensor",
            "signal": signal,
            "frozen_value": frozen_value,
            "duration": duration,
        })

    def log_intermittent_fault(
        self,
        sim_time: float,
        subtype: str,
        phase: int,
        affected_signals: list[str],
        magnitude: float,
        duration: float,
        permanent: bool,
        note: str | None = None,
    ) -> None:
        """Log an intermittent_fault event (PRD 5.17).

        Records each spike occurrence and phase transitions.  Fields:
        ``subtype`` (bearing/electrical/sensor/pneumatic), ``phase`` (1/2/3),
        ``affected_signals``, ``magnitude``, ``duration``, ``permanent``.
        """
        record: dict[str, object] = {
            "sim_time": self._format_time(sim_time),
            "event": "intermittent_fault",
            "subtype": subtype,
            "phase": phase,
            "affected_signals": affected_signals,
            "magnitude": magnitude,
            "duration": duration,
            "permanent": permanent,
        }
        if note is not None:
            record["note"] = note
        self._write_line(record)

    def log_partial_modbus_response(
        self,
        sim_time: float,
        controller_id: str,
        start_address: int,
        requested_count: int,
        returned_count: int,
    ) -> None:
        """Log a partial_modbus_response injection event (PRD 10.11, Task 4.10).

        Records controller ID, requested address range, returned count, and
        timestamp per the ground truth specification.
        """
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "partial_modbus_response",
            "controller_id": controller_id,
            "start_address": start_address,
            "requested_count": requested_count,
            "returned_count": returned_count,
        })

    def log_connection_drop(
        self,
        sim_time: float,
        controller_id: str,
        protocol: str,
        duration: float,
        affected_signals: list[str],
    ) -> None:
        """Log a connection_drop event (PRD 4.7 / 4.8)."""
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "connection_drop",
            "controller_id": controller_id,
            "protocol": protocol,
            "duration": duration,
            "affected_signals": affected_signals,
        })

    def log_counter_rollover(
        self,
        sim_time: float,
        signal_id: str,
        rollover_value: float,
        value_after: float,
    ) -> None:
        """Log a counter_rollover event (PRD 10.4, Task 4.15).

        Fired when a counter wraps from near ``rollover_value`` back to
        zero.  Engineers can use this to test CollatrEdge counter-wrap
        detection without waiting decades for a uint32 to overflow.
        """
        self._write_line({
            "sim_time": self._format_time(sim_time),
            "event": "counter_rollover",
            "signal_id": signal_id,
            "rollover_value": rollover_value,
            "value_after": value_after,
        })

    # -- Internals -------------------------------------------------------------

    def _write_line(self, record: dict[str, Any]) -> None:
        """Serialize and write one JSON line."""
        if self._fh is None:
            return
        line = json.dumps(record, separators=(",", ":"))
        self._fh.write(line + "\n")
        self._fh.flush()

    @staticmethod
    def _format_time(sim_time: float) -> str:
        """Convert sim_time to ISO 8601 string.

        Always treats sim_time as seconds from the reference epoch
        (2026-01-01T00:00:00Z).  All simulation times are relative
        offsets from simulation start.
        """
        # Simulation typically uses relative seconds from start.
        # Use a fixed reference epoch for consistent output.
        _REFERENCE_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)
        dt_obj = _REFERENCE_EPOCH.timestamp() + sim_time
        return datetime.fromtimestamp(dt_obj, tz=UTC).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
