"""Configuration loading and validation for the Collatr Factory Simulator.

Pydantic v2 models for the entire config schema. Loads YAML config files,
validates against the schema, and applies environment variable overrides.

PRD Reference: Section 6 (Configuration)
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Factory metadata
# ---------------------------------------------------------------------------

class FactoryInfo(BaseModel):
    """Top-level factory identification."""

    name: str = "Demo Packaging Factory"
    site_id: str = "demo"
    timezone: str = "Europe/London"


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

class SimulationConfig(BaseModel):
    """Simulation engine settings (PRD 6.2 simulation block)."""

    time_scale: float = 1.0
    random_seed: int | None = None
    tick_interval_ms: int = 100
    start_time: str | None = None
    log_level: str = "info"
    sim_duration_s: float | None = None

    @field_validator("time_scale")
    @classmethod
    def _time_scale_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("time_scale must be positive")
        return v

    @field_validator("tick_interval_ms")
    @classmethod
    def _tick_interval_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("tick_interval_ms must be positive")
        return v

    @field_validator("log_level")
    @classmethod
    def _valid_log_level(cls, v: str) -> str:
        allowed = {"debug", "info", "warn", "warning", "error", "critical"}
        if v.lower() not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return v.lower()


# ---------------------------------------------------------------------------
# Protocol configs
# ---------------------------------------------------------------------------

class ErrorInjectionConfig(BaseModel):
    """Modbus error injection settings (PRD 6.2 protocols.modbus.error_injection)."""

    exception_probability: float = 0.001
    timeout_probability: float = 0.0005
    response_delay_ms: list[int] = Field(default_factory=lambda: [0, 50])

    @field_validator("exception_probability", "timeout_probability")
    @classmethod
    def _probability_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("probability must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _delay_range_valid(self) -> ErrorInjectionConfig:
        if len(self.response_delay_ms) != 2:
            raise ValueError("response_delay_ms must be a [min, max] pair")
        if self.response_delay_ms[0] > self.response_delay_ms[1]:
            raise ValueError("response_delay_ms min must be <= max")
        if self.response_delay_ms[0] < 0:
            raise ValueError("response_delay_ms values must be non-negative")
        return self


class ModbusProtocolConfig(BaseModel):
    """Modbus TCP server settings (PRD 6.2 protocols.modbus)."""

    enabled: bool = True
    bind_address: str = "0.0.0.0"
    port: int = 502
    unit_id: int = 1
    byte_order: str = "ABCD"
    error_injection: ErrorInjectionConfig = Field(default_factory=ErrorInjectionConfig)

    @field_validator("byte_order")
    @classmethod
    def _valid_byte_order(cls, v: str) -> str:
        if v not in ("ABCD", "CDAB"):
            raise ValueError("byte_order must be 'ABCD' or 'CDAB'")
        return v

    @field_validator("port")
    @classmethod
    def _valid_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return v


class OpcuaUserConfig(BaseModel):
    """OPC-UA user credentials."""

    username: str
    password: str


class OpcuaProtocolConfig(BaseModel):
    """OPC-UA server settings (PRD 6.2 protocols.opcua)."""

    enabled: bool = True
    bind_address: str = "0.0.0.0"
    port: int = 4840
    server_name: str = "Collatr Factory Simulator"
    namespace_uri: str = "urn:collatr:factory-simulator"
    security_mode: str = "None"
    anonymous_access: bool = True
    users: list[OpcuaUserConfig] = Field(default_factory=list)

    @field_validator("port")
    @classmethod
    def _valid_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return v

    @field_validator("security_mode")
    @classmethod
    def _valid_security_mode(cls, v: str) -> str:
        allowed = {"None", "Sign", "SignAndEncrypt"}
        if v not in allowed:
            raise ValueError(f"security_mode must be one of {sorted(allowed)}")
        return v


class MqttProtocolConfig(BaseModel):
    """MQTT publisher settings (PRD 6.2 protocols.mqtt)."""

    enabled: bool = True
    broker_host: str = "mqtt-broker"
    broker_port: int = 1883
    topic_prefix: str = "collatr/factory"
    line_id: str = "packaging1"
    sparkplug_b: bool = False
    retain: bool = True
    client_id: str = "factory-simulator"
    username: str | None = None
    password: str | None = None
    qos_default: int = 1
    buffer_limit: int = 1000
    buffer_overflow: str = "drop_oldest"
    lwt_topic: str = "collatr/factory/status"
    lwt_payload: str = '{"status": "offline"}'
    vibration_per_axis_enabled: bool = True

    @field_validator("broker_port")
    @classmethod
    def _valid_port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("broker_port must be between 1 and 65535")
        return v

    @field_validator("qos_default")
    @classmethod
    def _valid_qos(cls, v: int) -> int:
        if v not in (0, 1, 2):
            raise ValueError("qos_default must be 0, 1, or 2")
        return v

    @field_validator("buffer_overflow")
    @classmethod
    def _valid_overflow(cls, v: str) -> str:
        allowed = {"drop_oldest", "drop_newest"}
        if v not in allowed:
            raise ValueError(f"buffer_overflow must be one of {sorted(allowed)}")
        return v


class ProtocolsConfig(BaseModel):
    """Container for all protocol configurations."""

    modbus: ModbusProtocolConfig = Field(default_factory=ModbusProtocolConfig)
    opcua: OpcuaProtocolConfig = Field(default_factory=OpcuaProtocolConfig)
    mqtt: MqttProtocolConfig = Field(default_factory=MqttProtocolConfig)


# ---------------------------------------------------------------------------
# Signal and equipment configs
# ---------------------------------------------------------------------------

class SignalConfig(BaseModel):
    """Configuration for a single signal within an equipment group.

    Common fields are typed explicitly. Model-specific parameters go in
    the ``params`` dict. Extra fields from YAML are captured via
    ``extra="allow"`` for forward compatibility.

    PRD Reference: Section 6.2 equipment.*.signals.*
    """

    model_config = ConfigDict(extra="allow")

    # Signal model type
    model: str

    # Noise configuration
    noise_sigma: float = 0.0
    noise_type: str = "gaussian"
    noise_df: float | None = None  # Student-t degrees of freedom
    noise_phi: float | None = None  # AR(1) autocorrelation coefficient

    # Speed-dependent sigma (PRD 4.2.11):
    # effective_sigma = sigma_base + sigma_scale * |parent_value|
    sigma_base: float | None = None
    sigma_scale: float = 0.0
    sigma_parent: str | None = None  # Parent signal ID for speed-dependent sigma

    # Timing
    sample_rate_ms: int | None = None

    # Physical bounds
    min_clamp: float | None = None
    max_clamp: float | None = None
    units: str | None = None
    resolution: float | None = None

    # Protocol: Modbus
    modbus_hr: list[int] | None = None
    modbus_ir: list[int] | None = None
    modbus_type: str | None = None
    modbus_writable: bool = False
    modbus_byte_order: str = "ABCD"    # "ABCD" (default) or "CDAB" (Allen-Bradley)
    modbus_coil: int | None = None     # Coil address for binary signals (F&B)
    modbus_di: int | None = None       # Discrete input address for binary signals (F&B)
    modbus_slave_id: int | None = None # Secondary slave UID (F&B oven zones, task 3.13)
    # IR address on secondary slave — used alongside modbus_slave_id
    modbus_slave_ir: list[int] | None = None

    # Protocol: OPC-UA
    opcua_node: str | None = None
    opcua_type: str | None = None

    # Protocol: MQTT
    mqtt_topic: str | None = None

    # Correlated follower fields
    parent: str | None = None
    transform: str | None = None

    # Model-specific parameters
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("noise_sigma")
    @classmethod
    def _sigma_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("noise_sigma must be non-negative")
        return v

    @field_validator("noise_type")
    @classmethod
    def _valid_noise_type(cls, v: str) -> str:
        allowed = {"gaussian", "student_t", "ar1"}
        if v not in allowed:
            raise ValueError(f"noise_type must be one of {sorted(allowed)}")
        return v

    @field_validator("noise_df")
    @classmethod
    def _df_minimum(cls, v: float | None) -> float | None:
        if v is not None and v < 3:
            raise ValueError("noise_df (Student-t degrees of freedom) must be >= 3")
        return v

    @field_validator("noise_phi")
    @classmethod
    def _phi_range(cls, v: float | None) -> float | None:
        if v is not None and not -1.0 < v < 1.0:
            raise ValueError("noise_phi (AR(1) coefficient) must be in (-1, 1)")
        return v

    @model_validator(mode="after")
    def _clamp_order(self) -> SignalConfig:
        if (
            self.min_clamp is not None
            and self.max_clamp is not None
            and self.min_clamp > self.max_clamp
        ):
            raise ValueError(
                f"min_clamp ({self.min_clamp}) must be <= max_clamp ({self.max_clamp})"
            )
        return self


class EquipmentConfig(BaseModel):
    """Configuration for one equipment group.

    Common fields are typed. Equipment-specific fields (e.g. target_speed,
    schedule_offset_hours) are captured via ``extra="allow"``.

    PRD Reference: Section 6.2 equipment.*
    """

    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    type: str = ""
    signals: dict[str, SignalConfig] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scenario configs
# ---------------------------------------------------------------------------

class JobChangoverConfig(BaseModel):
    """PRD 5.2: Job changeover scenario."""

    enabled: bool = True
    frequency_per_shift: list[int] = Field(default_factory=lambda: [3, 6])
    duration_seconds: list[int] = Field(default_factory=lambda: [600, 1800])
    speed_change_probability: float = 0.3
    counter_reset_probability: float = 0.7

    @field_validator("speed_change_probability", "counter_reset_probability")
    @classmethod
    def _probability_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("probability must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _ranges_valid(self) -> JobChangoverConfig:
        _validate_range_pair(self.frequency_per_shift, "frequency_per_shift")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class WebBreakConfig(BaseModel):
    """PRD 5.3: Web break scenario."""

    enabled: bool = True
    frequency_per_week: list[int] = Field(default_factory=lambda: [1, 2])
    recovery_seconds: list[int] = Field(default_factory=lambda: [900, 3600])

    @model_validator(mode="after")
    def _ranges_valid(self) -> WebBreakConfig:
        _validate_range_pair(self.frequency_per_week, "frequency_per_week")
        _validate_range_pair(self.recovery_seconds, "recovery_seconds")
        return self


class DryerDriftConfig(BaseModel):
    """PRD 5.4: Dryer temperature drift scenario."""

    enabled: bool = True
    frequency_per_shift: list[int] = Field(default_factory=lambda: [1, 2])
    max_drift_c: list[float] = Field(default_factory=lambda: [5.0, 15.0])
    duration_seconds: list[int] = Field(default_factory=lambda: [1800, 7200])

    @model_validator(mode="after")
    def _ranges_valid(self) -> DryerDriftConfig:
        _validate_range_pair(self.frequency_per_shift, "frequency_per_shift")
        _validate_range_pair(self.max_drift_c, "max_drift_c")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class BearingWearConfig(BaseModel):
    """PRD 5.5: Motor bearing wear scenario."""

    enabled: bool = True
    start_after_hours: float = 48.0
    duration_hours: float = 336.0
    base_rate: list[float] = Field(default_factory=lambda: [0.001, 0.005])
    acceleration_k: list[float] = Field(default_factory=lambda: [0.005, 0.01])
    warning_threshold: float = 15.0
    alarm_threshold: float = 25.0
    current_increase_percent: list[float] = Field(default_factory=lambda: [1.0, 5.0])
    culminate_in_failure: bool = False
    failure_vibration: list[float] = Field(default_factory=lambda: [40.0, 50.0])

    @field_validator("start_after_hours", "duration_hours")
    @classmethod
    def _positive_hours(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("hours must be positive")
        return v

    @field_validator("warning_threshold", "alarm_threshold")
    @classmethod
    def _positive_threshold(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("threshold must be positive")
        return v

    @model_validator(mode="after")
    def _ranges_valid(self) -> BearingWearConfig:
        _validate_range_pair(self.base_rate, "base_rate")
        _validate_range_pair(self.acceleration_k, "acceleration_k")
        _validate_range_pair(self.current_increase_percent, "current_increase_percent")
        _validate_range_pair(self.failure_vibration, "failure_vibration")
        return self


class InkViscosityExcursionConfig(BaseModel):
    """PRD 5.6: Ink viscosity excursion scenario."""

    enabled: bool = True
    frequency_per_shift: list[int] = Field(default_factory=lambda: [2, 3])
    duration_seconds: list[int] = Field(default_factory=lambda: [300, 1800])

    @model_validator(mode="after")
    def _ranges_valid(self) -> InkViscosityExcursionConfig:
        _validate_range_pair(self.frequency_per_shift, "frequency_per_shift")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class RegistrationDriftConfig(BaseModel):
    """PRD 5.7: Registration drift scenario."""

    enabled: bool = True
    frequency_per_shift: list[int] = Field(default_factory=lambda: [1, 3])
    duration_seconds: list[int] = Field(default_factory=lambda: [120, 600])

    @model_validator(mode="after")
    def _ranges_valid(self) -> RegistrationDriftConfig:
        _validate_range_pair(self.frequency_per_shift, "frequency_per_shift")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class UnplannedStopConfig(BaseModel):
    """PRD 5.8: Unplanned stop scenario."""

    enabled: bool = True
    frequency_per_shift: list[int] = Field(default_factory=lambda: [1, 2])
    duration_seconds: list[int] = Field(default_factory=lambda: [300, 3600])

    @model_validator(mode="after")
    def _ranges_valid(self) -> UnplannedStopConfig:
        _validate_range_pair(self.frequency_per_shift, "frequency_per_shift")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class ShiftChangeConfig(BaseModel):
    """PRD 5.9: Shift change scenario."""

    enabled: bool = True
    times: list[str] = Field(default_factory=lambda: ["06:00", "14:00", "22:00"])
    changeover_seconds: list[int] = Field(default_factory=lambda: [300, 900])
    night_shift_speed_factor: float = 0.9
    weekend_enabled: bool = False

    @model_validator(mode="after")
    def _ranges_valid(self) -> ShiftChangeConfig:
        _validate_range_pair(self.changeover_seconds, "changeover_seconds")
        return self

    @field_validator("night_shift_speed_factor")
    @classmethod
    def _speed_factor_range(cls, v: float) -> float:
        if not 0.0 < v <= 2.0:
            raise ValueError("night_shift_speed_factor must be in (0, 2]")
        return v


class ColdStartSpikeConfig(BaseModel):
    """PRD 5.10: Cold start spike scenario."""

    enabled: bool = True
    idle_threshold_minutes: float = 30.0
    spike_duration_seconds: list[float] = Field(default_factory=lambda: [2.0, 5.0])
    spike_magnitude: list[float] = Field(default_factory=lambda: [1.5, 2.0])

    @model_validator(mode="after")
    def _ranges_valid(self) -> ColdStartSpikeConfig:
        _validate_range_pair(self.spike_duration_seconds, "spike_duration_seconds")
        _validate_range_pair(self.spike_magnitude, "spike_magnitude")
        return self


class CoderDepletionConfig(BaseModel):
    """PRD 5.12: Coder consumable depletion scenario."""

    enabled: bool = True
    low_ink_threshold: float = 10.0
    empty_threshold: float = 2.0
    recovery_duration_seconds: list[float] = Field(
        default_factory=lambda: [300.0, 1800.0]
    )

    @model_validator(mode="after")
    def _ranges_valid(self) -> CoderDepletionConfig:
        _validate_range_pair(self.recovery_duration_seconds, "recovery_duration_seconds")
        return self


class MaterialSpliceConfig(BaseModel):
    """PRD 5.13a: Material splice scenario."""

    enabled: bool = True
    trigger_diameter_mm: float = 150.0
    splice_duration_seconds: list[float] = Field(
        default_factory=lambda: [10.0, 30.0]
    )

    @model_validator(mode="after")
    def _ranges_valid(self) -> MaterialSpliceConfig:
        _validate_range_pair(self.splice_duration_seconds, "splice_duration_seconds")
        return self


# ---------------------------------------------------------------------------
# F&B scenario configs (PRD 5.14)
# ---------------------------------------------------------------------------


class BatchCycleConfig(BaseModel):
    """PRD 5.14.1: Mixer batch cycle scenario."""

    enabled: bool = True
    frequency_per_shift: list[int] = Field(default_factory=lambda: [8, 16])
    batch_duration_seconds: list[int] = Field(default_factory=lambda: [1200, 2700])

    @model_validator(mode="after")
    def _ranges_valid(self) -> BatchCycleConfig:
        _validate_range_pair(self.frequency_per_shift, "frequency_per_shift")
        _validate_range_pair(self.batch_duration_seconds, "batch_duration_seconds")
        return self


class OvenThermalExcursionConfig(BaseModel):
    """PRD 5.14.2: Oven thermal excursion scenario."""

    enabled: bool = True
    frequency_per_shift: list[int] = Field(default_factory=lambda: [1, 2])
    duration_seconds: list[int] = Field(default_factory=lambda: [1800, 5400])
    max_drift_c: list[float] = Field(default_factory=lambda: [3.0, 10.0])

    @model_validator(mode="after")
    def _ranges_valid(self) -> OvenThermalExcursionConfig:
        _validate_range_pair(self.frequency_per_shift, "frequency_per_shift")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        _validate_range_pair(self.max_drift_c, "max_drift_c")
        return self


class FillWeightDriftConfig(BaseModel):
    """PRD 5.14.3: Fill weight drift scenario."""

    enabled: bool = True
    frequency_per_shift: list[int] = Field(default_factory=lambda: [1, 3])
    duration_seconds: list[int] = Field(default_factory=lambda: [600, 3600])
    drift_rate: list[float] = Field(default_factory=lambda: [0.05, 0.2])

    @model_validator(mode="after")
    def _ranges_valid(self) -> FillWeightDriftConfig:
        _validate_range_pair(self.frequency_per_shift, "frequency_per_shift")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        _validate_range_pair(self.drift_rate, "drift_rate")
        return self


class SealIntegrityFailureConfig(BaseModel):
    """PRD 5.14.4: Seal integrity failure scenario."""

    enabled: bool = True
    frequency_per_week: list[int] = Field(default_factory=lambda: [1, 2])
    duration_seconds: list[int] = Field(default_factory=lambda: [300, 1800])

    @model_validator(mode="after")
    def _ranges_valid(self) -> SealIntegrityFailureConfig:
        _validate_range_pair(self.frequency_per_week, "frequency_per_week")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class ChillerDoorAlarmConfig(BaseModel):
    """PRD 5.14.5: Chiller door alarm scenario."""

    enabled: bool = True
    frequency_per_week: list[int] = Field(default_factory=lambda: [1, 3])
    duration_seconds: list[int] = Field(default_factory=lambda: [300, 1200])

    @model_validator(mode="after")
    def _ranges_valid(self) -> ChillerDoorAlarmConfig:
        _validate_range_pair(self.frequency_per_week, "frequency_per_week")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class CipCycleConfig(BaseModel):
    """PRD 5.14.6: CIP (Clean-in-Place) cycle scenario."""

    enabled: bool = True
    frequency_per_day: list[int] = Field(default_factory=lambda: [1, 3])
    cycle_duration_seconds: list[int] = Field(default_factory=lambda: [1800, 3600])

    @model_validator(mode="after")
    def _ranges_valid(self) -> CipCycleConfig:
        _validate_range_pair(self.frequency_per_day, "frequency_per_day")
        _validate_range_pair(self.cycle_duration_seconds, "cycle_duration_seconds")
        return self


class ColdChainBreakConfig(BaseModel):
    """PRD 5.14.7: Cold chain break scenario."""

    enabled: bool = True
    frequency_per_month: list[int] = Field(default_factory=lambda: [1, 2])
    duration_seconds: list[int] = Field(default_factory=lambda: [1800, 7200])

    @model_validator(mode="after")
    def _ranges_valid(self) -> ColdChainBreakConfig:
        _validate_range_pair(self.frequency_per_month, "frequency_per_month")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


# ---------------------------------------------------------------------------
# Phase 4 scenario configs: micro-stop, contextual anomaly, intermittent fault
# ---------------------------------------------------------------------------


class MicroStopConfig(BaseModel):
    """PRD 5.15: Micro-stop scenario (brief speed dips, no state change)."""

    enabled: bool = True
    frequency_per_shift: list[int] = Field(default_factory=lambda: [10, 50])
    duration_seconds: list[float] = Field(default_factory=lambda: [5.0, 30.0])
    speed_drop_percent: list[float] = Field(default_factory=lambda: [30.0, 80.0])
    ramp_down_seconds: list[float] = Field(default_factory=lambda: [2.0, 5.0])
    ramp_up_seconds: list[float] = Field(default_factory=lambda: [5.0, 15.0])

    @model_validator(mode="after")
    def _ranges_valid(self) -> MicroStopConfig:
        _validate_range_pair(self.frequency_per_shift, "frequency_per_shift")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        _validate_range_pair(self.speed_drop_percent, "speed_drop_percent")
        _validate_range_pair(self.ramp_down_seconds, "ramp_down_seconds")
        _validate_range_pair(self.ramp_up_seconds, "ramp_up_seconds")
        return self


class HeaterStuckConfig(BaseModel):
    """Contextual anomaly type: heater stuck on during idle/maintenance."""

    probability: float = 0.3
    duration_seconds: list[float] = Field(default_factory=lambda: [300.0, 3600.0])

    @field_validator("probability")
    @classmethod
    def _prob_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("probability must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _ranges_valid(self) -> HeaterStuckConfig:
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class PressureBleedConfig(BaseModel):
    """Contextual anomaly type: pressure bleed during idle."""

    probability: float = 0.2
    duration_seconds: list[float] = Field(default_factory=lambda: [600.0, 7200.0])

    @field_validator("probability")
    @classmethod
    def _prob_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("probability must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _ranges_valid(self) -> PressureBleedConfig:
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class CounterFalseTriggerConfig(BaseModel):
    """Contextual anomaly type: counter incrementing when machine is off."""

    probability: float = 0.2
    duration_seconds: list[float] = Field(default_factory=lambda: [60.0, 600.0])
    increment_rate: float = 0.1

    @field_validator("probability")
    @classmethod
    def _prob_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("probability must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _ranges_valid(self) -> CounterFalseTriggerConfig:
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class HotDuringMaintenanceConfig(BaseModel):
    """Contextual anomaly type: temperature readings during maintenance state."""

    probability: float = 0.15
    duration_seconds: list[float] = Field(default_factory=lambda: [1800.0, 7200.0])

    @field_validator("probability")
    @classmethod
    def _prob_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("probability must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _ranges_valid(self) -> HotDuringMaintenanceConfig:
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class VibrationDuringOffConfig(BaseModel):
    """Contextual anomaly type: vibration signal active when machine is off."""

    probability: float = 0.15
    duration_seconds: list[float] = Field(default_factory=lambda: [300.0, 1800.0])

    @field_validator("probability")
    @classmethod
    def _prob_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("probability must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _ranges_valid(self) -> VibrationDuringOffConfig:
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class ContextualAnomalyTypesConfig(BaseModel):
    """Container for all 5 contextual anomaly type configurations (PRD 5.16)."""

    heater_stuck: HeaterStuckConfig = Field(default_factory=HeaterStuckConfig)
    pressure_bleed: PressureBleedConfig = Field(default_factory=PressureBleedConfig)
    counter_false_trigger: CounterFalseTriggerConfig = Field(
        default_factory=CounterFalseTriggerConfig
    )
    hot_during_maintenance: HotDuringMaintenanceConfig = Field(
        default_factory=HotDuringMaintenanceConfig
    )
    vibration_during_off: VibrationDuringOffConfig = Field(
        default_factory=VibrationDuringOffConfig
    )


class ContextualAnomalyConfig(BaseModel):
    """PRD 5.16: Contextual anomaly scenario."""

    enabled: bool = True
    frequency_per_week: list[int] = Field(default_factory=lambda: [2, 5])
    types: ContextualAnomalyTypesConfig = Field(
        default_factory=ContextualAnomalyTypesConfig
    )

    @model_validator(mode="after")
    def _ranges_valid(self) -> ContextualAnomalyConfig:
        _validate_range_pair(self.frequency_per_week, "frequency_per_week")
        return self


class BearingIntermittentConfig(BaseModel):
    """Intermittent fault subtype: bearing vibration (PRD 5.17)."""

    enabled: bool = True
    start_after_hours: float = 24.0
    phase1_duration_hours: list[float] = Field(default_factory=lambda: [168.0, 336.0])
    phase1_frequency_per_day: list[float] = Field(default_factory=lambda: [1.0, 3.0])
    phase1_spike_duration_s: list[float] = Field(default_factory=lambda: [10.0, 60.0])
    phase2_duration_hours: list[float] = Field(default_factory=lambda: [48.0, 168.0])
    phase2_frequency_per_day: list[float] = Field(default_factory=lambda: [5.0, 20.0])
    phase2_spike_duration_s: list[float] = Field(default_factory=lambda: [30.0, 300.0])
    phase3_transition: bool = True
    affected_signals: list[str] = Field(
        default_factory=lambda: [
            "vibration.main_drive_x",
            "vibration.main_drive_y",
            "vibration.main_drive_z",
        ]
    )
    spike_magnitude: list[float] = Field(default_factory=lambda: [15.0, 25.0])

    @model_validator(mode="after")
    def _ranges_valid(self) -> BearingIntermittentConfig:
        _validate_range_pair(self.phase1_duration_hours, "phase1_duration_hours")
        _validate_range_pair(self.phase1_frequency_per_day, "phase1_frequency_per_day")
        _validate_range_pair(self.phase1_spike_duration_s, "phase1_spike_duration_s")
        _validate_range_pair(self.phase2_duration_hours, "phase2_duration_hours")
        _validate_range_pair(self.phase2_frequency_per_day, "phase2_frequency_per_day")
        _validate_range_pair(self.phase2_spike_duration_s, "phase2_spike_duration_s")
        _validate_range_pair(self.spike_magnitude, "spike_magnitude")
        return self


class ElectricalIntermittentConfig(BaseModel):
    """Intermittent fault subtype: electrical current spikes (PRD 5.17)."""

    enabled: bool = True
    start_after_hours: float = 48.0
    phase1_duration_hours: list[float] = Field(default_factory=lambda: [72.0, 168.0])
    phase1_frequency_per_day: list[float] = Field(default_factory=lambda: [1.0, 2.0])
    phase1_spike_duration_s: list[float] = Field(default_factory=lambda: [1.0, 10.0])
    phase2_duration_hours: list[float] = Field(default_factory=lambda: [24.0, 72.0])
    phase2_frequency_per_day: list[float] = Field(default_factory=lambda: [5.0, 15.0])
    phase2_spike_duration_s: list[float] = Field(default_factory=lambda: [2.0, 30.0])
    phase3_transition: bool = True
    affected_signals: list[str] = Field(
        default_factory=lambda: ["press.main_drive_current"]
    )
    spike_magnitude_pct: list[float] = Field(default_factory=lambda: [20.0, 50.0])

    @model_validator(mode="after")
    def _ranges_valid(self) -> ElectricalIntermittentConfig:
        _validate_range_pair(self.phase1_duration_hours, "phase1_duration_hours")
        _validate_range_pair(self.phase1_frequency_per_day, "phase1_frequency_per_day")
        _validate_range_pair(self.phase1_spike_duration_s, "phase1_spike_duration_s")
        _validate_range_pair(self.phase2_duration_hours, "phase2_duration_hours")
        _validate_range_pair(self.phase2_frequency_per_day, "phase2_frequency_per_day")
        _validate_range_pair(self.phase2_spike_duration_s, "phase2_spike_duration_s")
        _validate_range_pair(self.spike_magnitude_pct, "spike_magnitude_pct")
        return self


class SensorIntermittentConfig(BaseModel):
    """Intermittent fault subtype: sensor sentinel value spikes (PRD 5.17)."""

    enabled: bool = False
    start_after_hours: float = 24.0
    phase1_duration_hours: list[float] = Field(default_factory=lambda: [48.0, 168.0])
    phase1_frequency_per_day: list[float] = Field(default_factory=lambda: [1.0, 3.0])
    phase1_spike_duration_s: list[float] = Field(default_factory=lambda: [1.0, 5.0])
    phase2_duration_hours: list[float] = Field(default_factory=lambda: [24.0, 72.0])
    phase2_frequency_per_day: list[float] = Field(default_factory=lambda: [5.0, 15.0])
    phase2_spike_duration_s: list[float] = Field(default_factory=lambda: [2.0, 10.0])
    phase3_transition: bool = True
    affected_signals: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ranges_valid(self) -> SensorIntermittentConfig:
        _validate_range_pair(self.phase1_duration_hours, "phase1_duration_hours")
        _validate_range_pair(self.phase1_frequency_per_day, "phase1_frequency_per_day")
        _validate_range_pair(self.phase1_spike_duration_s, "phase1_spike_duration_s")
        _validate_range_pair(self.phase2_duration_hours, "phase2_duration_hours")
        _validate_range_pair(self.phase2_frequency_per_day, "phase2_frequency_per_day")
        _validate_range_pair(self.phase2_spike_duration_s, "phase2_spike_duration_s")
        return self


class PneumaticIntermittentConfig(BaseModel):
    """Intermittent fault subtype: pneumatic pressure drops (PRD 5.17)."""

    enabled: bool = True
    start_after_hours: float = 72.0
    phase1_duration_hours: list[float] = Field(default_factory=lambda: [168.0, 504.0])
    phase1_frequency_per_day: list[float] = Field(default_factory=lambda: [1.0, 2.0])
    phase1_spike_duration_s: list[float] = Field(default_factory=lambda: [2.0, 30.0])
    phase2_duration_hours: list[float] = Field(default_factory=lambda: [48.0, 168.0])
    phase2_frequency_per_day: list[float] = Field(default_factory=lambda: [3.0, 10.0])
    phase2_spike_duration_s: list[float] = Field(default_factory=lambda: [5.0, 60.0])
    phase3_transition: bool = False
    affected_signals: list[str] = Field(
        default_factory=lambda: ["coder.ink_pressure"]
    )

    @model_validator(mode="after")
    def _ranges_valid(self) -> PneumaticIntermittentConfig:
        _validate_range_pair(self.phase1_duration_hours, "phase1_duration_hours")
        _validate_range_pair(self.phase1_frequency_per_day, "phase1_frequency_per_day")
        _validate_range_pair(self.phase1_spike_duration_s, "phase1_spike_duration_s")
        _validate_range_pair(self.phase2_duration_hours, "phase2_duration_hours")
        _validate_range_pair(self.phase2_frequency_per_day, "phase2_frequency_per_day")
        _validate_range_pair(self.phase2_spike_duration_s, "phase2_spike_duration_s")
        return self


class IntermittentFaultFaultsConfig(BaseModel):
    """Container for all 4 intermittent fault subtype configurations (PRD 5.17)."""

    bearing_intermittent: BearingIntermittentConfig = Field(
        default_factory=BearingIntermittentConfig
    )
    electrical_intermittent: ElectricalIntermittentConfig = Field(
        default_factory=ElectricalIntermittentConfig
    )
    sensor_intermittent: SensorIntermittentConfig = Field(
        default_factory=SensorIntermittentConfig
    )
    pneumatic_intermittent: PneumaticIntermittentConfig = Field(
        default_factory=PneumaticIntermittentConfig
    )


class IntermittentFaultConfig(BaseModel):
    """PRD 5.17: Intermittent fault scenario (three-phase progression)."""

    enabled: bool = True
    faults: IntermittentFaultFaultsConfig = Field(
        default_factory=IntermittentFaultFaultsConfig
    )


# ---------------------------------------------------------------------------
# Data quality injection configs (PRD Section 10)
# ---------------------------------------------------------------------------


class CommDropConfig(BaseModel):
    """Configuration for a single protocol communication drop (PRD 10.2)."""

    enabled: bool = True
    frequency_per_hour: list[float] = Field(default_factory=lambda: [1.0, 2.0])
    duration_seconds: list[float] = Field(default_factory=lambda: [1.0, 10.0])

    @model_validator(mode="after")
    def _ranges_valid(self) -> CommDropConfig:
        _validate_range_pair(self.frequency_per_hour, "frequency_per_hour")
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class NoiseConfig(BaseModel):
    """Global noise calibration settings (PRD 10.3)."""

    enabled: bool = True
    global_sigma_multiplier: float = 1.0

    @field_validator("global_sigma_multiplier")
    @classmethod
    def _multiplier_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("global_sigma_multiplier must be positive")
        return v


class SensorDisconnectSentinelConfig(BaseModel):
    """Default sentinel values per signal type (PRD 10.9)."""

    temperature: float = 6553.5   # Siemens wire break convention
    pressure: float = 0.0          # 4-20 mA open circuit
    voltage: float = -32768.0      # int16 min, open circuit


class SensorDisconnectConfig(BaseModel):
    """Sensor disconnect injection config (PRD 10.9)."""

    enabled: bool = True
    frequency_per_24h_per_signal: list[float] = Field(
        default_factory=lambda: [0.0, 1.0]
    )
    duration_seconds: list[float] = Field(default_factory=lambda: [30.0, 300.0])
    sentinel_defaults: SensorDisconnectSentinelConfig = Field(
        default_factory=SensorDisconnectSentinelConfig
    )
    per_signal_overrides: dict[str, float] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _ranges_valid(self) -> SensorDisconnectConfig:
        _validate_range_pair(
            self.frequency_per_24h_per_signal, "frequency_per_24h_per_signal"
        )
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class StuckSensorConfig(BaseModel):
    """Stuck sensor (frozen value) injection config (PRD 10.10)."""

    enabled: bool = True
    frequency_per_week_per_signal: list[float] = Field(
        default_factory=lambda: [0.0, 2.0]
    )
    duration_seconds: list[float] = Field(default_factory=lambda: [300.0, 14400.0])

    @model_validator(mode="after")
    def _ranges_valid(self) -> StuckSensorConfig:
        _validate_range_pair(
            self.frequency_per_week_per_signal, "frequency_per_week_per_signal"
        )
        _validate_range_pair(self.duration_seconds, "duration_seconds")
        return self


class PartialModbusResponseConfig(BaseModel):
    """Partial Modbus response injection config (PRD 10.11)."""

    enabled: bool = True
    probability: float = 0.0001

    @field_validator("probability")
    @classmethod
    def _prob_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("probability must be between 0.0 and 1.0")
        return v


class DataQualityConfig(BaseModel):
    """Root data quality injection configuration (PRD Section 10, Appendix D)."""

    # Communication drops per protocol
    modbus_drop: CommDropConfig = Field(
        default_factory=lambda: CommDropConfig(
            enabled=True,
            frequency_per_hour=[1.0, 2.0],
            duration_seconds=[1.0, 10.0],
        )
    )
    opcua_stale: CommDropConfig = Field(
        default_factory=lambda: CommDropConfig(
            enabled=True,
            frequency_per_hour=[1.0, 2.0],
            duration_seconds=[5.0, 30.0],
        )
    )
    mqtt_drop: CommDropConfig = Field(
        default_factory=lambda: CommDropConfig(
            enabled=True,
            frequency_per_hour=[1.0, 2.0],
            duration_seconds=[5.0, 30.0],
        )
    )

    # Noise calibration
    noise: NoiseConfig = Field(default_factory=NoiseConfig)

    # Duplicate timestamps (PRD 10.5)
    duplicate_probability: float = 0.0001

    # Modbus exception / partial response (PRD 10.6, 10.11)
    exception_probability: float = 0.001
    timeout_probability: float = 0.0005
    response_delay_ms: list[int] = Field(default_factory=lambda: [0, 50])
    partial_modbus_response: PartialModbusResponseConfig = Field(
        default_factory=PartialModbusResponseConfig
    )

    # Counter rollover overrides (PRD 10.4)
    counter_rollover: dict[str, float] = Field(
        default_factory=lambda: {
            "press.impression_count": 4294967295.0,
            "press.good_count": 4294967295.0,
            "press.waste_count": 4294967295.0,
            "coder.prints_total": 4294967295.0,
            "energy.cumulative_kwh": 999999.0,
        }
    )

    # Sensor events (PRD 10.9, 10.10)
    sensor_disconnect: SensorDisconnectConfig = Field(
        default_factory=SensorDisconnectConfig
    )
    stuck_sensor: StuckSensorConfig = Field(default_factory=StuckSensorConfig)

    # Timezone offset for MQTT timestamps (PRD 10.7)
    mqtt_timestamp_offset_hours: float = 0.0

    @field_validator("duplicate_probability", "exception_probability", "timeout_probability")
    @classmethod
    def _prob_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("probability must be between 0.0 and 1.0")
        return v

    @model_validator(mode="after")
    def _delay_range_valid(self) -> DataQualityConfig:
        _validate_range_pair(self.response_delay_ms, "response_delay_ms")
        return self


class ScenariosConfig(BaseModel):
    """Container for all scenario configurations."""

    # Packaging scenarios
    job_changeover: JobChangoverConfig = Field(default_factory=JobChangoverConfig)
    web_break: WebBreakConfig = Field(default_factory=WebBreakConfig)
    dryer_drift: DryerDriftConfig = Field(default_factory=DryerDriftConfig)
    bearing_wear: BearingWearConfig = Field(default_factory=BearingWearConfig)
    ink_viscosity_excursion: InkViscosityExcursionConfig = Field(
        default_factory=InkViscosityExcursionConfig
    )
    registration_drift: RegistrationDriftConfig = Field(
        default_factory=RegistrationDriftConfig
    )
    unplanned_stop: UnplannedStopConfig = Field(default_factory=UnplannedStopConfig)
    shift_change: ShiftChangeConfig = Field(default_factory=ShiftChangeConfig)
    cold_start_spike: ColdStartSpikeConfig = Field(default_factory=ColdStartSpikeConfig)
    coder_depletion: CoderDepletionConfig = Field(default_factory=CoderDepletionConfig)
    material_splice: MaterialSpliceConfig = Field(default_factory=MaterialSpliceConfig)

    # Phase 4 advanced scenarios — optional, None when not configured
    micro_stop: MicroStopConfig | None = None
    contextual_anomaly: ContextualAnomalyConfig | None = None
    intermittent_fault: IntermittentFaultConfig | None = None

    # F&B scenarios (PRD 5.14) — optional, None when using packaging profile
    batch_cycle: BatchCycleConfig | None = None
    oven_thermal_excursion: OvenThermalExcursionConfig | None = None
    fill_weight_drift: FillWeightDriftConfig | None = None
    seal_integrity_failure: SealIntegrityFailureConfig | None = None
    chiller_door_alarm: ChillerDoorAlarmConfig | None = None
    cip_cycle: CipCycleConfig | None = None
    cold_chain_break: ColdChainBreakConfig | None = None


# ---------------------------------------------------------------------------
# Shift config
# ---------------------------------------------------------------------------

class ShiftOperatorConfig(BaseModel):
    """Per-shift operator behaviour biases (PRD 6.2 shifts.operators)."""

    speed_bias: float = 1.0
    waste_rate_bias: float = 1.0

    @field_validator("speed_bias", "waste_rate_bias")
    @classmethod
    def _positive_bias(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("bias must be positive")
        return v


class ShiftsConfig(BaseModel):
    """Shift pattern configuration (PRD 6.2 shifts block)."""

    pattern: str = "3x8"
    day_start: str = "06:00"
    operators: dict[str, ShiftOperatorConfig] = Field(default_factory=lambda: {
        "morning": ShiftOperatorConfig(speed_bias=1.0, waste_rate_bias=1.0),
        "afternoon": ShiftOperatorConfig(speed_bias=0.95, waste_rate_bias=1.05),
        "night": ShiftOperatorConfig(speed_bias=0.90, waste_rate_bias=1.10),
    })


# ---------------------------------------------------------------------------
# Evaluation framework config (PRD Section 12)
# ---------------------------------------------------------------------------


class EvaluationConfig(BaseModel):
    """Evaluation framework configuration (PRD Section 12.4).

    Controls tolerance windows, severity weights, and random baseline seed
    used by the ``Evaluator`` class in ``factory_simulator.evaluation``.
    """

    pre_margin_seconds: float = 30.0
    """Detections within [start - pre_margin, start] count as TP."""

    post_margin_seconds: float = 60.0
    """Detections within [end, end + post_margin] count as TP."""

    severity_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "web_break": 10.0,
            "unplanned_stop": 5.0,
            "seal_integrity_failure": 8.0,
            "cold_chain_break": 10.0,
            "bearing_wear": 8.0,
            "dryer_drift": 3.0,
            "oven_excursion": 3.0,
            "fill_weight_drift": 3.0,
            "ink_viscosity_excursion": 2.0,
            "registration_drift": 2.0,
            "contextual_anomaly": 5.0,
            "intermittent_fault": 4.0,
            "micro_stop": 1.0,
            "sensor_disconnect": 2.0,
            "stuck_sensor": 3.0,
        }
    )

    seeds: int = 1
    """Number of independent seeds for multi-seed evaluation (≥1)."""

    latency_targets: dict[str, float] = Field(
        default_factory=lambda: {
            "web_break": 2.0,
            "unplanned_stop": 10.0,
            "seal_integrity_failure": 60.0,
            "cold_chain_break": 300.0,
            "bearing_wear": 86400.0,
            "dryer_drift": 900.0,
            "fill_weight_drift": 600.0,
            "contextual_anomaly": 300.0,
            "intermittent_fault": 172800.0,
        }
    )

    @field_validator("pre_margin_seconds", "post_margin_seconds")
    @classmethod
    def _non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("margin must be non-negative")
        return v

    @field_validator("seeds")
    @classmethod
    def _seeds_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("seeds must be >= 1")
        return v


# ---------------------------------------------------------------------------
# Batch output config (PRD Appendix F, Phase 5)
# ---------------------------------------------------------------------------


class BatchOutputConfig(BaseModel):
    """Batch output configuration for offline analysis runs.

    When ``format`` is not ``"none"``, the :class:`~factory_simulator.output.writer.BatchWriter`
    writes all signal values to disk after each engine tick.

    PRD Reference: Appendix F (Phase 5 — batch output)
    """

    format: Literal["csv", "parquet", "none"] = "none"
    """Output format: csv (long format), parquet (wide format), or none (disabled)."""

    path: str = "."
    """Output directory path."""

    buffer_size: int = 10000
    """Number of rows to buffer in memory before flushing to disk."""

    event_driven_signals: list[str] = Field(default_factory=list)
    """Signal IDs written only when their value changes (e.g. machine_state, fault_code)."""

    @field_validator("buffer_size")
    @classmethod
    def _buffer_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("buffer_size must be positive")
        return v


# ---------------------------------------------------------------------------
# Network topology configs (PRD 3a)
# ---------------------------------------------------------------------------


class ClockDriftConfig(BaseModel):
    """Per-controller clock drift parameters (PRD 3a.5).

    Formula: drifted_time = sim_time + initial_offset_ms/1000
             + drift_rate_s_per_day * elapsed_hours / 24
    """

    initial_offset_ms: float = 0.0
    drift_rate_s_per_day: float = 0.0

    @field_validator("initial_offset_ms", "drift_rate_s_per_day")
    @classmethod
    def _must_be_finite(cls, v: float) -> float:
        if not math.isfinite(v):
            raise ValueError("value must be finite (not NaN or Inf)")
        return v


class ScanCycleConfig(BaseModel):
    """Per-controller scan cycle parameters (PRD 3a.8).

    Formula: actual_cycle = cycle_ms * (1.0 + uniform(0, jitter_pct))
    """

    cycle_ms: float = 10.0
    jitter_pct: float = 0.05

    @field_validator("cycle_ms")
    @classmethod
    def _cycle_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("cycle_ms must be positive")
        return v

    @field_validator("jitter_pct")
    @classmethod
    def _jitter_range(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("jitter_pct must be between 0.0 and 1.0")
        return v


class ConnectionLimitConfig(BaseModel):
    """Per-controller connection limit parameters (PRD 3a.5)."""

    max_connections: int = 16
    response_timeout_ms_typical: float = 50.0
    response_timeout_ms_max: float = 200.0

    @field_validator("max_connections")
    @classmethod
    def _conn_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("max_connections must be positive")
        return v

    @field_validator("response_timeout_ms_typical", "response_timeout_ms_max")
    @classmethod
    def _timeout_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("timeout must be non-negative")
        return v


class ConnectionDropConfig(BaseModel):
    """Per-controller connection drop parameters (PRD 3a.5)."""

    mtbf_hours_min: float = 72.0
    mtbf_hours_max: float = 168.0
    reconnection_delay_s_min: float = 1.0
    reconnection_delay_s_max: float = 3.0

    @field_validator(
        "mtbf_hours_min",
        "mtbf_hours_max",
        "reconnection_delay_s_min",
        "reconnection_delay_s_max",
    )
    @classmethod
    def _positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("value must be positive")
        return v

    @model_validator(mode="after")
    def _ranges_valid(self) -> ConnectionDropConfig:
        if self.mtbf_hours_min > self.mtbf_hours_max:
            raise ValueError("mtbf_hours_min must be <= mtbf_hours_max")
        if self.reconnection_delay_s_min > self.reconnection_delay_s_max:
            raise ValueError(
                "reconnection_delay_s_min must be <= reconnection_delay_s_max"
            )
        return self


class NetworkConfig(BaseModel):
    """Network topology configuration (PRD 3a.4).

    mode="collapsed" (default): single port per protocol, current behaviour.
    mode="realistic": per-controller ports per PRD 3a.4 table.
    """

    mode: Literal["collapsed", "realistic"] = "collapsed"
    clock_drift: dict[str, ClockDriftConfig] = Field(default_factory=dict)
    scan_cycle: dict[str, ScanCycleConfig] = Field(default_factory=dict)
    connection_limits: dict[str, ConnectionLimitConfig] = Field(default_factory=dict)
    connection_drops: dict[str, ConnectionDropConfig] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------

class FactoryConfig(BaseModel):
    """Root configuration model for the Collatr Factory Simulator.

    This is the top-level object returned by :func:`load_config`.
    """

    factory: FactoryInfo = Field(default_factory=FactoryInfo)
    simulation: SimulationConfig = Field(default_factory=SimulationConfig)
    protocols: ProtocolsConfig = Field(default_factory=ProtocolsConfig)
    equipment: dict[str, EquipmentConfig] = Field(default_factory=dict)
    scenarios: ScenariosConfig = Field(default_factory=ScenariosConfig)
    shifts: ShiftsConfig = Field(default_factory=ShiftsConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    batch_output: BatchOutputConfig = Field(default_factory=BatchOutputConfig)
    evaluation: EvaluationConfig = Field(default_factory=EvaluationConfig)
    network: NetworkConfig | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_range_pair(pair: list[int] | list[float], name: str) -> None:
    """Validate that a [min, max] pair is well-formed."""
    if len(pair) != 2:
        raise ValueError(f"{name} must be a [min, max] pair")
    if pair[0] > pair[1]:
        raise ValueError(f"{name} min ({pair[0]}) must be <= max ({pair[1]})")


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply environment variable overrides to the raw config dict.

    PRD Reference: Section 6.4 Environment Variables
    """
    env_map: list[tuple[str, list[str], type]] = [
        # SIM_ prefixed
        ("SIM_TIME_SCALE", ["simulation", "time_scale"], float),
        ("SIM_RANDOM_SEED", ["simulation", "random_seed"], int),
        ("SIM_LOG_LEVEL", ["simulation", "log_level"], str),
        # Protocol overrides (no SIM_ prefix per PRD table)
        ("MODBUS_ENABLED", ["protocols", "modbus", "enabled"], bool),
        ("MODBUS_PORT", ["protocols", "modbus", "port"], int),
        ("MODBUS_BYTE_ORDER", ["protocols", "modbus", "byte_order"], str),
        ("OPCUA_ENABLED", ["protocols", "opcua", "enabled"], bool),
        ("OPCUA_PORT", ["protocols", "opcua", "port"], int),
        ("MQTT_ENABLED", ["protocols", "mqtt", "enabled"], bool),
        ("MQTT_BROKER_HOST", ["protocols", "mqtt", "broker_host"], str),
        ("MQTT_BROKER_PORT", ["protocols", "mqtt", "broker_port"], int),
        ("MQTT_TOPIC_PREFIX", ["protocols", "mqtt", "topic_prefix"], str),
        ("SIM_NETWORK_MODE", ["network", "mode"], str),
    ]

    for env_var, path, convert in env_map:
        raw = os.environ.get(env_var)
        if raw is None or raw == "":
            continue

        # Convert to the target type
        if convert is bool:
            value: Any = raw.lower() in ("true", "1", "yes")
        elif convert is int:
            value = int(raw)
        elif convert is float:
            value = float(raw)
        else:
            value = raw

        # Walk the path and set the value
        node = data
        for key in path[:-1]:
            node = node.setdefault(key, {})
        node[path[-1]] = value

    return data


def load_config(
    path: str | Path | None = None,
    *,
    apply_env: bool = True,
) -> FactoryConfig:
    """Load, validate, and return a :class:`FactoryConfig`.

    Parameters
    ----------
    path:
        Path to the YAML configuration file. If *None*, uses the
        ``SIM_CONFIG_PATH`` env var or falls back to
        ``config/factory.yaml``.
    apply_env:
        Whether to apply environment variable overrides after loading
        the YAML file. Defaults to *True*.

    Raises
    ------
    FileNotFoundError
        If the config file does not exist.
    pydantic.ValidationError
        If the configuration fails validation.
    """
    if path is None:
        path = os.environ.get("SIM_CONFIG_PATH", "config/factory.yaml")
    path = Path(path)

    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {path}")

    with path.open() as fh:
        data: dict[str, Any] = yaml.safe_load(fh) or {}

    if apply_env:
        data = _apply_env_overrides(data)

    return FactoryConfig.model_validate(data)
