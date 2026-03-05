"""Tests for configuration loading and validation.

Task 1.1: Configuration Models
PRD Reference: Section 6
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from factory_simulator.config import (
    BatchCycleConfig,
    BearingIntermittentConfig,
    BearingWearConfig,
    ChillerDoorAlarmConfig,
    CipCycleConfig,
    ColdChainBreakConfig,
    CommDropConfig,
    ContextualAnomalyConfig,
    DataQualityConfig,
    ElectricalIntermittentConfig,
    EquipmentConfig,
    ErrorInjectionConfig,
    FactoryConfig,
    FactoryInfo,
    FillWeightDriftConfig,
    IntermittentFaultConfig,
    MicroStopConfig,
    ModbusProtocolConfig,
    MqttProtocolConfig,
    NoiseConfig,
    OpcuaProtocolConfig,
    OvenThermalExcursionConfig,
    PartialModbusResponseConfig,
    PneumaticIntermittentConfig,
    ScenariosConfig,
    SealIntegrityFailureConfig,
    SensorDisconnectConfig,
    ShiftsConfig,
    SignalConfig,
    SimulationConfig,
    StuckSensorConfig,
    load_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(tmp_path: Path, data: dict[str, Any], name: str = "test.yaml") -> Path:
    """Write a dict to a YAML file and return its path."""
    p = tmp_path / name
    p.write_text(yaml.dump(data, default_flow_style=False))
    return p


def _minimal_config() -> dict[str, Any]:
    """Return a minimal valid config dict."""
    return {
        "factory": {"name": "Test Factory", "site_id": "test"},
        "simulation": {"time_scale": 1.0, "tick_interval_ms": 100},
    }


# ===================================================================
# FactoryInfo
# ===================================================================


class TestFactoryInfo:
    def test_defaults(self) -> None:
        info = FactoryInfo()
        assert info.name == "Demo Packaging Factory"
        assert info.site_id == "demo"
        assert info.timezone == "Europe/London"

    def test_custom_values(self) -> None:
        info = FactoryInfo(name="My Factory", site_id="mf01", timezone="US/Eastern")
        assert info.name == "My Factory"
        assert info.site_id == "mf01"


# ===================================================================
# SimulationConfig
# ===================================================================


class TestSimulationConfig:
    def test_defaults(self) -> None:
        cfg = SimulationConfig()
        assert cfg.time_scale == 1.0
        assert cfg.random_seed is None
        assert cfg.tick_interval_ms == 100
        assert cfg.start_time is None
        assert cfg.log_level == "info"
        assert cfg.health_port == 8080

    def test_valid_time_scale(self) -> None:
        cfg = SimulationConfig(time_scale=10.0)
        assert cfg.time_scale == 10.0

    def test_negative_time_scale_rejected(self) -> None:
        with pytest.raises(ValidationError, match="time_scale must be positive"):
            SimulationConfig(time_scale=-1.0)

    def test_zero_time_scale_rejected(self) -> None:
        with pytest.raises(ValidationError, match="time_scale must be positive"):
            SimulationConfig(time_scale=0.0)

    def test_negative_tick_interval_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tick_interval_ms must be positive"):
            SimulationConfig(tick_interval_ms=-10)

    def test_zero_tick_interval_rejected(self) -> None:
        with pytest.raises(ValidationError, match="tick_interval_ms must be positive"):
            SimulationConfig(tick_interval_ms=0)

    def test_invalid_log_level_rejected(self) -> None:
        with pytest.raises(ValidationError, match="log_level must be one of"):
            SimulationConfig(log_level="verbose")

    def test_log_level_normalised(self) -> None:
        cfg = SimulationConfig(log_level="DEBUG")
        assert cfg.log_level == "debug"

    def test_random_seed_integer(self) -> None:
        cfg = SimulationConfig(random_seed=42)
        assert cfg.random_seed == 42

    def test_custom_health_port(self) -> None:
        cfg = SimulationConfig(health_port=9090)
        assert cfg.health_port == 9090

    def test_health_port_zero_valid(self) -> None:
        cfg = SimulationConfig(health_port=0)
        assert cfg.health_port == 0

    def test_health_port_max_valid(self) -> None:
        cfg = SimulationConfig(health_port=65535)
        assert cfg.health_port == 65535

    def test_health_port_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="health_port must be between 0 and 65535"):
            SimulationConfig(health_port=-1)

    def test_health_port_too_large_rejected(self) -> None:
        with pytest.raises(ValidationError, match="health_port must be between 0 and 65535"):
            SimulationConfig(health_port=65536)


# ===================================================================
# ModbusProtocolConfig
# ===================================================================


class TestModbusProtocolConfig:
    def test_defaults(self) -> None:
        cfg = ModbusProtocolConfig()
        assert cfg.enabled is True
        assert cfg.port == 502
        assert cfg.byte_order == "ABCD"

    def test_invalid_byte_order(self) -> None:
        with pytest.raises(ValidationError, match="byte_order must be"):
            ModbusProtocolConfig(byte_order="DCBA")

    def test_cdab_accepted(self) -> None:
        cfg = ModbusProtocolConfig(byte_order="CDAB")
        assert cfg.byte_order == "CDAB"

    def test_invalid_port(self) -> None:
        with pytest.raises(ValidationError, match="port must be between"):
            ModbusProtocolConfig(port=0)

    def test_port_upper_bound(self) -> None:
        with pytest.raises(ValidationError, match="port must be between"):
            ModbusProtocolConfig(port=70000)


# ===================================================================
# ErrorInjectionConfig
# ===================================================================


class TestErrorInjectionConfig:
    def test_defaults(self) -> None:
        cfg = ErrorInjectionConfig()
        assert cfg.exception_probability == 0.001
        assert cfg.response_delay_ms == [0, 50]

    def test_invalid_probability(self) -> None:
        with pytest.raises(ValidationError, match="probability must be between"):
            ErrorInjectionConfig(exception_probability=1.5)

    def test_negative_probability(self) -> None:
        with pytest.raises(ValidationError, match="probability must be between"):
            ErrorInjectionConfig(timeout_probability=-0.1)

    def test_delay_must_be_pair(self) -> None:
        with pytest.raises(ValidationError, match="must be a \\[min, max\\] pair"):
            ErrorInjectionConfig(response_delay_ms=[0, 50, 100])

    def test_delay_min_gt_max_rejected(self) -> None:
        with pytest.raises(ValidationError, match="min must be <= max"):
            ErrorInjectionConfig(response_delay_ms=[100, 50])

    def test_negative_delay_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be non-negative"):
            ErrorInjectionConfig(response_delay_ms=[-1, 50])


# ===================================================================
# OpcuaProtocolConfig
# ===================================================================


class TestOpcuaProtocolConfig:
    def test_defaults(self) -> None:
        cfg = OpcuaProtocolConfig()
        assert cfg.port == 4840
        assert cfg.security_mode == "None"

    def test_invalid_security_mode(self) -> None:
        with pytest.raises(ValidationError, match="security_mode must be one of"):
            OpcuaProtocolConfig(security_mode="Encrypt")


# ===================================================================
# MqttProtocolConfig
# ===================================================================


class TestMqttProtocolConfig:
    def test_defaults(self) -> None:
        cfg = MqttProtocolConfig()
        assert cfg.broker_host == "mqtt-broker"
        assert cfg.qos_default == 1

    def test_invalid_qos(self) -> None:
        with pytest.raises(ValidationError, match="qos_default must be"):
            MqttProtocolConfig(qos_default=3)

    def test_invalid_overflow(self) -> None:
        with pytest.raises(ValidationError, match="buffer_overflow must be"):
            MqttProtocolConfig(buffer_overflow="block")


# ===================================================================
# SignalConfig
# ===================================================================


class TestSignalConfig:
    def test_minimal(self) -> None:
        sig = SignalConfig(model="steady_state")
        assert sig.model == "steady_state"
        assert sig.noise_sigma == 0.0

    def test_negative_sigma_rejected(self) -> None:
        with pytest.raises(ValidationError, match="noise_sigma must be non-negative"):
            SignalConfig(model="steady_state", noise_sigma=-0.5)

    def test_invalid_noise_type_rejected(self) -> None:
        with pytest.raises(ValidationError, match="noise_type must be one of"):
            SignalConfig(model="steady_state", noise_type="poisson")

    def test_student_t_df_too_low(self) -> None:
        with pytest.raises(ValidationError, match=r"noise_df.*must be >= 3"):
            SignalConfig(model="steady_state", noise_type="student_t", noise_df=2.0)

    def test_student_t_df_valid(self) -> None:
        sig = SignalConfig(model="steady_state", noise_type="student_t", noise_df=5.0)
        assert sig.noise_df == 5.0

    def test_ar1_phi_range(self) -> None:
        with pytest.raises(ValidationError, match=r"noise_phi.*must be in"):
            SignalConfig(model="steady_state", noise_type="ar1", noise_phi=1.0)

    def test_ar1_phi_valid(self) -> None:
        sig = SignalConfig(model="steady_state", noise_type="ar1", noise_phi=0.8)
        assert sig.noise_phi == 0.8

    def test_params_dict(self) -> None:
        sig = SignalConfig(model="ramp", params={"ramp_duration_s": 180})
        assert sig.params["ramp_duration_s"] == 180

    def test_protocol_mappings(self) -> None:
        sig = SignalConfig(
            model="steady_state",
            modbus_hr=[100, 101],
            modbus_type="float32",
            opcua_node="PackagingLine.Press1.LineSpeed",
            opcua_type="Double",
        )
        assert sig.modbus_hr == [100, 101]
        assert sig.opcua_node == "PackagingLine.Press1.LineSpeed"

    def test_extra_fields_allowed(self) -> None:
        sig = SignalConfig(model="steady_state", custom_field="custom_value")
        assert sig.model_extra is not None
        assert sig.model_extra.get("custom_field") == "custom_value"

    # -- min_clamp / max_clamp ordering (Y9) --

    def test_clamp_order_valid(self) -> None:
        sig = SignalConfig(model="steady_state", min_clamp=0.0, max_clamp=100.0)
        assert sig.min_clamp == 0.0
        assert sig.max_clamp == 100.0

    def test_clamp_equal(self) -> None:
        sig = SignalConfig(model="steady_state", min_clamp=50.0, max_clamp=50.0)
        assert sig.min_clamp == sig.max_clamp == 50.0

    def test_clamp_reversed_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min_clamp.*must be <= max_clamp"):
            SignalConfig(model="steady_state", min_clamp=100.0, max_clamp=50.0)

    def test_clamp_one_sided_min_only(self) -> None:
        sig = SignalConfig(model="steady_state", min_clamp=0.0)
        assert sig.min_clamp == 0.0
        assert sig.max_clamp is None

    def test_clamp_one_sided_max_only(self) -> None:
        sig = SignalConfig(model="steady_state", max_clamp=200.0)
        assert sig.min_clamp is None
        assert sig.max_clamp == 200.0

    def test_clamp_neither(self) -> None:
        sig = SignalConfig(model="steady_state")
        assert sig.min_clamp is None
        assert sig.max_clamp is None


# ===================================================================
# EquipmentConfig
# ===================================================================


class TestEquipmentConfig:
    def test_minimal(self) -> None:
        eq = EquipmentConfig(type="press")
        assert eq.type == "press"
        assert eq.enabled is True
        assert eq.signals == {}

    def test_with_signals(self) -> None:
        eq = EquipmentConfig(
            type="press",
            signals={"speed": SignalConfig(model="ramp")},
        )
        assert "speed" in eq.signals
        assert eq.signals["speed"].model == "ramp"

    def test_extra_fields_for_equipment_params(self) -> None:
        eq = EquipmentConfig(type="press", target_speed=200, speed_range=[50, 400])
        assert eq.model_extra is not None
        assert eq.model_extra.get("target_speed") == 200
        assert eq.model_extra.get("speed_range") == [50, 400]


# ===================================================================
# ScenariosConfig
# ===================================================================


class TestScenariosConfig:
    def test_defaults(self) -> None:
        cfg = ScenariosConfig()
        assert cfg.job_changeover.enabled is True
        assert cfg.bearing_wear.duration_hours == 336.0

    def test_inverted_range_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            ScenariosConfig(
                job_changeover={"enabled": True, "frequency_per_shift": [6, 3]}  # type: ignore[arg-type]
            )

    def test_scenario_probability_validation(self) -> None:
        with pytest.raises(ValidationError, match="probability must be between"):
            ScenariosConfig(
                job_changeover={  # type: ignore[arg-type]
                    "enabled": True,
                    "speed_change_probability": 2.0,
                }
            )

    def test_bearing_wear_zero_hours_rejected(self) -> None:
        with pytest.raises(ValidationError, match="hours must be positive"):
            ScenariosConfig(
                bearing_wear={"enabled": True, "duration_hours": 0}  # type: ignore[arg-type]
            )

    def test_fnb_scenarios_none_by_default(self) -> None:
        """F&B scenario configs are None by default (packaging profile)."""
        cfg = ScenariosConfig()
        assert cfg.batch_cycle is None
        assert cfg.oven_thermal_excursion is None
        assert cfg.fill_weight_drift is None
        assert cfg.seal_integrity_failure is None
        assert cfg.chiller_door_alarm is None
        assert cfg.cip_cycle is None
        assert cfg.cold_chain_break is None

    def test_fnb_scenarios_enabled(self) -> None:
        """F&B scenario configs can be set explicitly."""
        cfg = ScenariosConfig(
            batch_cycle=BatchCycleConfig(),
            oven_thermal_excursion=OvenThermalExcursionConfig(),
            fill_weight_drift=FillWeightDriftConfig(),
            seal_integrity_failure=SealIntegrityFailureConfig(),
            chiller_door_alarm=ChillerDoorAlarmConfig(),
            cip_cycle=CipCycleConfig(),
            cold_chain_break=ColdChainBreakConfig(),
        )
        assert cfg.batch_cycle is not None
        assert cfg.batch_cycle.enabled is True
        assert cfg.oven_thermal_excursion is not None
        assert cfg.cold_chain_break is not None


# ===================================================================
# F&B Scenario Config Models (PRD 5.14)
# ===================================================================


class TestBatchCycleConfig:
    def test_defaults(self) -> None:
        cfg = BatchCycleConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_shift == [8, 16]
        assert cfg.batch_duration_seconds == [1200, 2700]

    def test_custom_values(self) -> None:
        cfg = BatchCycleConfig(
            frequency_per_shift=[10, 12],
            batch_duration_seconds=[1500, 2400],
        )
        assert cfg.frequency_per_shift == [10, 12]
        assert cfg.batch_duration_seconds == [1500, 2400]

    def test_inverted_frequency_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            BatchCycleConfig(frequency_per_shift=[16, 8])

    def test_inverted_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            BatchCycleConfig(batch_duration_seconds=[2700, 1200])

    def test_wrong_length_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must be a \\[min, max\\] pair"):
            BatchCycleConfig(frequency_per_shift=[8])


class TestOvenThermalExcursionConfig:
    def test_defaults(self) -> None:
        cfg = OvenThermalExcursionConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_shift == [1, 2]
        assert cfg.duration_seconds == [1800, 5400]
        assert cfg.max_drift_c == [3.0, 10.0]

    def test_inverted_drift_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            OvenThermalExcursionConfig(max_drift_c=[10.0, 3.0])

    def test_inverted_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            OvenThermalExcursionConfig(duration_seconds=[5400, 1800])


class TestFillWeightDriftConfig:
    def test_defaults(self) -> None:
        cfg = FillWeightDriftConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_shift == [1, 3]
        assert cfg.duration_seconds == [600, 3600]
        assert cfg.drift_rate == [0.05, 0.2]

    def test_inverted_drift_rate_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            FillWeightDriftConfig(drift_rate=[0.2, 0.05])

    def test_custom_values(self) -> None:
        cfg = FillWeightDriftConfig(drift_rate=[0.1, 0.15])
        assert cfg.drift_rate == [0.1, 0.15]


class TestSealIntegrityFailureConfig:
    def test_defaults(self) -> None:
        cfg = SealIntegrityFailureConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_week == [1, 2]
        assert cfg.duration_seconds == [300, 1800]

    def test_inverted_frequency_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            SealIntegrityFailureConfig(frequency_per_week=[2, 1])


class TestChillerDoorAlarmConfig:
    def test_defaults(self) -> None:
        cfg = ChillerDoorAlarmConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_week == [1, 3]
        assert cfg.duration_seconds == [300, 1200]

    def test_inverted_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            ChillerDoorAlarmConfig(duration_seconds=[1200, 300])


class TestCipCycleConfig:
    def test_defaults(self) -> None:
        cfg = CipCycleConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_day == [1, 3]
        assert cfg.cycle_duration_seconds == [1800, 3600]

    def test_inverted_cycle_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            CipCycleConfig(cycle_duration_seconds=[3600, 1800])

    def test_custom_values(self) -> None:
        cfg = CipCycleConfig(frequency_per_day=[2, 4], cycle_duration_seconds=[2400, 3000])
        assert cfg.frequency_per_day == [2, 4]


class TestColdChainBreakConfig:
    def test_defaults(self) -> None:
        cfg = ColdChainBreakConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_month == [1, 2]
        assert cfg.duration_seconds == [1800, 7200]

    def test_inverted_frequency_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            ColdChainBreakConfig(frequency_per_month=[2, 1])

    def test_disabled(self) -> None:
        cfg = ColdChainBreakConfig(enabled=False)
        assert cfg.enabled is False


# ===================================================================
# ShiftsConfig
# ===================================================================


class TestShiftsConfig:
    def test_defaults(self) -> None:
        cfg = ShiftsConfig()
        assert cfg.pattern == "3x8"
        assert "morning" in cfg.operators
        assert cfg.operators["morning"].speed_bias == 1.0

    def test_custom_operators(self) -> None:
        cfg = ShiftsConfig(
            operators={
                "day": {"speed_bias": 1.0, "waste_rate_bias": 1.0},  # type: ignore[dict-item]
                "night": {"speed_bias": 0.85, "waste_rate_bias": 1.15},  # type: ignore[dict-item]
            }
        )
        assert cfg.operators["night"].speed_bias == 0.85


# ===================================================================
# Full config loading
# ===================================================================


class TestLoadConfig:
    def test_load_default_factory_yaml(self) -> None:
        """The default config/factory.yaml should load without errors."""
        cfg = load_config("config/factory.yaml", apply_env=False)
        assert cfg.factory.name == "Demo Packaging Factory"
        assert cfg.simulation.time_scale == 1.0

    def test_all_48_signals_present(self) -> None:
        """Verify all 48 packaging signals are defined in the default config."""
        cfg = load_config("config/factory.yaml", apply_env=False)
        total_signals = sum(len(eq.signals) for eq in cfg.equipment.values())
        assert total_signals == 48, f"Expected 48 signals, got {total_signals}"

    def test_equipment_groups(self) -> None:
        """Verify all 7 equipment groups are present."""
        cfg = load_config("config/factory.yaml", apply_env=False)
        expected = {"press", "laminator", "slitter", "coder", "environment", "energy", "vibration"}
        assert set(cfg.equipment.keys()) == expected

    def test_signal_counts_per_equipment(self) -> None:
        """PRD 2.2-2.9 signal counts."""
        cfg = load_config("config/factory.yaml", apply_env=False)
        counts = {name: len(eq.signals) for name, eq in cfg.equipment.items()}
        assert counts["press"] == 22
        assert counts["laminator"] == 5
        assert counts["slitter"] == 3
        assert counts["coder"] == 11
        assert counts["environment"] == 2
        assert counts["energy"] == 2
        assert counts["vibration"] == 3

    def test_load_minimal_config(self, tmp_path: Path) -> None:
        """A minimal config should load with defaults filled in."""
        p = _write_yaml(tmp_path, _minimal_config())
        cfg = load_config(p, apply_env=False)
        assert cfg.factory.name == "Test Factory"
        assert cfg.protocols.modbus.port == 502  # default

    def test_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="not found"):
            load_config(tmp_path / "nonexistent.yaml")

    def test_empty_yaml(self, tmp_path: Path) -> None:
        """An empty YAML file should produce a config with all defaults."""
        p = tmp_path / "empty.yaml"
        p.write_text("")
        cfg = load_config(p, apply_env=False)
        assert cfg.factory.name == "Demo Packaging Factory"

    def test_invalid_config_rejected(self, tmp_path: Path) -> None:
        data = {"simulation": {"time_scale": -5.0}}
        p = _write_yaml(tmp_path, data)
        with pytest.raises(ValidationError, match="time_scale must be positive"):
            load_config(p, apply_env=False)


# ===================================================================
# Environment variable overrides
# ===================================================================


class TestEnvOverrides:
    def test_sim_time_scale(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write_yaml(tmp_path, _minimal_config())
        monkeypatch.setenv("SIM_TIME_SCALE", "10.0")
        cfg = load_config(p, apply_env=True)
        assert cfg.simulation.time_scale == 10.0

    def test_sim_random_seed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write_yaml(tmp_path, _minimal_config())
        monkeypatch.setenv("SIM_RANDOM_SEED", "42")
        cfg = load_config(p, apply_env=True)
        assert cfg.simulation.random_seed == 42

    def test_sim_log_level(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write_yaml(tmp_path, _minimal_config())
        monkeypatch.setenv("SIM_LOG_LEVEL", "debug")
        cfg = load_config(p, apply_env=True)
        assert cfg.simulation.log_level == "debug"

    def test_sim_health_port(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write_yaml(tmp_path, _minimal_config())
        monkeypatch.setenv("SIM_HEALTH_PORT", "9090")
        cfg = load_config(p, apply_env=True)
        assert cfg.simulation.health_port == 9090

    def test_modbus_enabled_false(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write_yaml(tmp_path, _minimal_config())
        monkeypatch.setenv("MODBUS_ENABLED", "false")
        cfg = load_config(p, apply_env=True)
        assert cfg.protocols.modbus.enabled is False

    def test_modbus_port(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write_yaml(tmp_path, _minimal_config())
        monkeypatch.setenv("MODBUS_PORT", "5020")
        cfg = load_config(p, apply_env=True)
        assert cfg.protocols.modbus.port == 5020

    def test_mqtt_broker_host(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write_yaml(tmp_path, _minimal_config())
        monkeypatch.setenv("MQTT_BROKER_HOST", "localhost")
        cfg = load_config(p, apply_env=True)
        assert cfg.protocols.mqtt.broker_host == "localhost"

    def test_empty_env_var_ignored(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        p = _write_yaml(tmp_path, _minimal_config())
        monkeypatch.setenv("SIM_RANDOM_SEED", "")
        cfg = load_config(p, apply_env=True)
        assert cfg.simulation.random_seed is None

    def test_env_overrides_file_values(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        data = _minimal_config()
        data["simulation"]["time_scale"] = 1.0
        p = _write_yaml(tmp_path, data)
        monkeypatch.setenv("SIM_TIME_SCALE", "5.0")
        cfg = load_config(p, apply_env=True)
        assert cfg.simulation.time_scale == 5.0

    def test_sim_config_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """SIM_CONFIG_PATH selects the config file when no path is given."""
        data = _minimal_config()
        data["factory"]["name"] = "Path Test"
        p = _write_yaml(tmp_path, data, name="custom.yaml")
        monkeypatch.setenv("SIM_CONFIG_PATH", str(p))
        cfg = load_config(apply_env=False)
        assert cfg.factory.name == "Path Test"


# ===================================================================
# Modbus register address coverage
# ===================================================================


class TestModbusRegisterCoverage:
    """Verify that the config contains expected Modbus register addresses
    matching Appendix A of the PRD."""

    def test_press_registers(self) -> None:
        cfg = load_config("config/factory.yaml", apply_env=False)
        press = cfg.equipment["press"]
        assert press.signals["line_speed"].modbus_hr == [100, 101]
        assert press.signals["web_tension"].modbus_hr == [102, 103]
        assert press.signals["ink_viscosity"].modbus_hr == [110, 111]
        assert press.signals["impression_count"].modbus_hr == [200, 201]
        assert press.signals["machine_state"].modbus_hr == [210]
        assert press.signals["main_drive_current"].modbus_hr == [300, 301]
        assert press.signals["nip_pressure"].modbus_hr == [310, 311]
        assert press.signals["unwind_diameter"].modbus_hr == [320, 321]
        assert press.signals["rewind_diameter"].modbus_hr == [322, 323]

    def test_dryer_setpoint_writable(self) -> None:
        cfg = load_config("config/factory.yaml", apply_env=False)
        press = cfg.equipment["press"]
        assert press.signals["dryer_setpoint_zone_1"].modbus_writable is True
        assert press.signals["dryer_setpoint_zone_2"].modbus_writable is True
        assert press.signals["dryer_setpoint_zone_3"].modbus_writable is True
        assert press.signals["line_speed"].modbus_writable is False

    def test_laminator_registers(self) -> None:
        cfg = load_config("config/factory.yaml", apply_env=False)
        lam = cfg.equipment["laminator"]
        assert lam.signals["nip_temp"].modbus_hr == [400, 401]
        assert lam.signals["adhesive_weight"].modbus_hr == [408, 409]

    def test_slitter_registers(self) -> None:
        cfg = load_config("config/factory.yaml", apply_env=False)
        slt = cfg.equipment["slitter"]
        assert slt.signals["speed"].modbus_hr == [500, 501]
        assert slt.signals["reel_count"].modbus_hr == [510, 511]

    def test_energy_registers(self) -> None:
        cfg = load_config("config/factory.yaml", apply_env=False)
        eng = cfg.equipment["energy"]
        assert eng.signals["line_power"].modbus_hr == [600, 601]
        assert eng.signals["cumulative_kwh"].modbus_hr == [602, 603]

    def test_input_register_presence(self) -> None:
        """Dryer temp zones should have input register mappings (int16 x10)."""
        cfg = load_config("config/factory.yaml", apply_env=False)
        press = cfg.equipment["press"]
        assert press.signals["dryer_temp_zone_1"].modbus_ir == [0]
        assert press.signals["dryer_temp_zone_2"].modbus_ir == [1]
        assert press.signals["dryer_temp_zone_3"].modbus_ir == [2]


# ===================================================================
# F&B Config Loading (Task 3.2)
# ===================================================================


class TestFnbConfigLoading:
    """Tests for loading and validating config/factory-foodbev.yaml."""

    def test_load_foodbev_yaml(self) -> None:
        """The F&B config should load without errors."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert cfg.factory.name == "Demo F&B Factory"
        assert cfg.factory.site_id == "demo"

    def test_68_signals_total(self) -> None:
        """Verify all 68 F&B signals are defined (PRD 2b.14)."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        total = sum(len(eq.signals) for eq in cfg.equipment.values())
        assert total == 68, f"Expected 68 signals, got {total}"

    def test_equipment_groups(self) -> None:
        """Verify all 10 equipment groups are present."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        expected = {
            "mixer", "oven", "filler", "sealer", "qc",
            "chiller", "cip", "coder", "environment", "energy",
        }
        assert set(cfg.equipment.keys()) == expected

    def test_signal_counts_per_equipment(self) -> None:
        """PRD 2b signal counts per equipment group."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        counts = {name: len(eq.signals) for name, eq in cfg.equipment.items()}
        assert counts["mixer"] == 8
        assert counts["oven"] == 13
        assert counts["filler"] == 8
        assert counts["sealer"] == 6
        assert counts["qc"] == 6
        assert counts["chiller"] == 7
        assert counts["cip"] == 5
        assert counts["coder"] == 11
        assert counts["environment"] == 2
        assert counts["energy"] == 2

    def test_equipment_types(self) -> None:
        """Verify equipment type strings match generator registry expectations."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert cfg.equipment["mixer"].type == "high_shear_mixer"
        assert cfg.equipment["oven"].type == "tunnel_oven"
        assert cfg.equipment["filler"].type == "gravimetric_filler"
        assert cfg.equipment["sealer"].type == "tray_sealer"
        assert cfg.equipment["qc"].type == "checkweigher"
        assert cfg.equipment["chiller"].type == "cold_room"
        assert cfg.equipment["cip"].type == "cip_skid"
        assert cfg.equipment["coder"].type == "cij_printer"
        assert cfg.equipment["energy"].type == "power_meter"
        assert cfg.equipment["environment"].type == "iolink_sensor"

    def test_mqtt_line_id_foodbev1(self) -> None:
        """MQTT line_id must be 'foodbev1' for F&B profile."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert cfg.protocols.mqtt.line_id == "foodbev1"

    def test_mixer_cdab_byte_order(self) -> None:
        """Mixer Modbus signals use CDAB byte order (Allen-Bradley)."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        mixer = cfg.equipment["mixer"]
        cdab_signals = ["speed", "torque", "batch_temp", "batch_weight", "mix_time_elapsed"]
        for sig_name in cdab_signals:
            sig = mixer.signals[sig_name]
            assert sig.modbus_hr is not None, f"mixer.{sig_name} should have modbus_hr"
            assert sig.modbus_byte_order == "CDAB", (
                f"mixer.{sig_name} should be CDAB, got {sig.modbus_byte_order}"
            )

    def test_mixer_modbus_addresses(self) -> None:
        """Mixer HR addresses per Appendix A."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        mixer = cfg.equipment["mixer"]
        assert mixer.signals["speed"].modbus_hr == [1000, 1001]
        assert mixer.signals["torque"].modbus_hr == [1002, 1003]
        assert mixer.signals["batch_temp"].modbus_hr == [1004, 1005]
        assert mixer.signals["batch_weight"].modbus_hr == [1006, 1007]
        assert mixer.signals["mix_time_elapsed"].modbus_hr == [1010, 1011]

    def test_oven_modbus_addresses(self) -> None:
        """Oven HR and IR addresses per Appendix A."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        oven = cfg.equipment["oven"]
        assert oven.signals["zone_1_temp"].modbus_hr == [1100, 1101]
        assert oven.signals["zone_2_temp"].modbus_hr == [1102, 1103]
        assert oven.signals["zone_3_temp"].modbus_hr == [1104, 1105]
        assert oven.signals["zone_1_setpoint"].modbus_hr == [1110, 1111]
        assert oven.signals["belt_speed"].modbus_hr == [1120, 1121]
        assert oven.signals["product_core_temp"].modbus_hr == [1122, 1123]
        assert oven.signals["humidity_zone_2"].modbus_hr == [1124, 1125]
        # IR addresses
        assert oven.signals["zone_1_temp"].modbus_ir == [100]
        assert oven.signals["zone_2_temp"].modbus_ir == [101]
        assert oven.signals["zone_3_temp"].modbus_ir == [102]
        assert oven.signals["product_core_temp"].modbus_ir == [106]

    def test_oven_setpoints_writable(self) -> None:
        """Oven zone setpoints must be writable per Appendix A."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        oven = cfg.equipment["oven"]
        for z in [1, 2, 3]:
            sig = oven.signals[f"zone_{z}_setpoint"]
            assert sig.modbus_writable is True, f"oven.zone_{z}_setpoint should be writable"

    def test_oven_output_power_multi_slave(self) -> None:
        """Oven output power signals map to multi-slave UIDs 11-13 (PRD 3.1.6)."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        oven = cfg.equipment["oven"]
        for zone, uid in [(1, 11), (2, 12), (3, 13)]:
            sig = oven.signals[f"zone_{zone}_output_power"]
            assert sig.modbus_slave_id == uid, f"zone_{zone}_output_power slave_id should be {uid}"
            assert sig.modbus_ir == [2]

    def test_filler_opcua_nodes(self) -> None:
        """Filler signals use OPC-UA with FoodBevLine prefix (Appendix B)."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        filler = cfg.equipment["filler"]
        assert filler.signals["line_speed"].opcua_node == "FoodBevLine.Filler1.LineSpeed"
        assert filler.signals["fill_weight"].opcua_node == "FoodBevLine.Filler1.FillWeight"
        assert filler.signals["state"].opcua_node == "FoodBevLine.Filler1.State"
        assert filler.signals["packs_produced"].opcua_node == "FoodBevLine.Filler1.PacksProduced"

    def test_filler_hopper_level_modbus_only(self) -> None:
        """Filler hopper_level is the only filler signal on Modbus HR."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        filler = cfg.equipment["filler"]
        assert filler.signals["hopper_level"].modbus_hr == [1200, 1201]
        # All other filler signals should not have modbus_hr
        for sig_name in ["line_speed", "fill_weight", "fill_target",
                         "fill_deviation", "packs_produced", "reject_count", "state"]:
            assert filler.signals[sig_name].modbus_hr is None

    def test_qc_opcua_nodes(self) -> None:
        """QC signals use OPC-UA with FoodBevLine.QC1 prefix (Appendix B)."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        qc = cfg.equipment["qc"]
        assert qc.signals["actual_weight"].opcua_node == "FoodBevLine.QC1.ActualWeight"
        assert qc.signals["reject_total"].opcua_node == "FoodBevLine.QC1.RejectTotal"
        assert qc.signals["throughput"].opcua_node == "FoodBevLine.QC1.Throughput"

    def test_chiller_modbus_addresses(self) -> None:
        """Chiller HR, IR, coil, and DI addresses per Appendix A."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        chiller = cfg.equipment["chiller"]
        assert chiller.signals["room_temp"].modbus_hr == [1400, 1401]
        assert chiller.signals["room_temp"].modbus_ir == [110]
        assert chiller.signals["setpoint"].modbus_hr == [1402, 1403]
        assert chiller.signals["setpoint"].modbus_writable is True
        assert chiller.signals["suction_pressure"].modbus_hr == [1404, 1405]
        assert chiller.signals["discharge_pressure"].modbus_hr == [1406, 1407]
        # Coils
        compressor = chiller.signals["compressor_state"]
        assert compressor.modbus_coil == 101
        defrost = chiller.signals["defrost_active"]
        assert defrost.modbus_coil == 102
        # Discrete input
        door = chiller.signals["door_open"]
        assert door.modbus_di == 100

    def test_cip_modbus_addresses(self) -> None:
        """CIP Modbus HR and IR addresses per Appendix A."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        cip = cfg.equipment["cip"]
        assert cip.signals["wash_temp"].modbus_hr == [1500, 1501]
        assert cip.signals["wash_temp"].modbus_ir == [115]
        assert cip.signals["flow_rate"].modbus_hr == [1502, 1503]
        assert cip.signals["conductivity"].modbus_hr == [1504, 1505]
        assert cip.signals["cycle_time_elapsed"].modbus_hr == [1506, 1507]

    def test_energy_shared_registers(self) -> None:
        """Energy registers shared at HR 600-603 + F&B IR 120-121."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        energy = cfg.equipment["energy"]
        assert energy.signals["line_power"].modbus_hr == [600, 601]
        assert energy.signals["line_power"].modbus_ir == [120, 121]
        assert energy.signals["cumulative_kwh"].modbus_hr == [602, 603]
        # OPC-UA nodes under FoodBevLine
        assert energy.signals["line_power"].opcua_node == "FoodBevLine.Energy.LinePower"
        assert energy.signals["cumulative_kwh"].opcua_node == "FoodBevLine.Energy.CumulativeKwh"

    def test_coder_mqtt_topics(self) -> None:
        """Coder MQTT topics for F&B (same topic paths, foodbev1 line_id)."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        coder = cfg.equipment["coder"]
        assert coder.signals["state"].mqtt_topic == "coder/state"
        assert coder.signals["prints_total"].mqtt_topic == "coder/prints_total"
        assert coder.signals["nozzle_health"].mqtt_topic == "coder/nozzle_health"
        assert coder.signals["gutter_fault"].mqtt_topic == "coder/gutter_fault"

    def test_environment_mqtt_topics(self) -> None:
        """Environment MQTT topics for F&B."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        env = cfg.equipment["environment"]
        assert env.signals["ambient_temp"].mqtt_topic == "env/ambient_temp"
        assert env.signals["ambient_humidity"].mqtt_topic == "env/ambient_humidity"

    def test_no_vibration_equipment(self) -> None:
        """F&B profile has no vibration equipment group."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert "vibration" not in cfg.equipment

    def test_fnb_scenarios_enabled(self) -> None:
        """F&B scenario configs should be present and enabled."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert cfg.scenarios.batch_cycle is not None
        assert cfg.scenarios.batch_cycle.enabled is True
        assert cfg.scenarios.oven_thermal_excursion is not None
        assert cfg.scenarios.oven_thermal_excursion.enabled is True
        assert cfg.scenarios.fill_weight_drift is not None
        assert cfg.scenarios.fill_weight_drift.enabled is True
        assert cfg.scenarios.seal_integrity_failure is not None
        assert cfg.scenarios.seal_integrity_failure.enabled is True
        assert cfg.scenarios.chiller_door_alarm is not None
        assert cfg.scenarios.chiller_door_alarm.enabled is True
        assert cfg.scenarios.cip_cycle is not None
        assert cfg.scenarios.cip_cycle.enabled is True
        assert cfg.scenarios.cold_chain_break is not None
        assert cfg.scenarios.cold_chain_break.enabled is True

    def test_packaging_scenarios_disabled(self) -> None:
        """Most packaging scenarios should be disabled for F&B profile."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert cfg.scenarios.job_changeover.enabled is False
        assert cfg.scenarios.web_break.enabled is False
        assert cfg.scenarios.dryer_drift.enabled is False
        assert cfg.scenarios.bearing_wear.enabled is False

    def test_coder_coupling_config(self) -> None:
        """Coder should have coupling config for F&B (follows filler)."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        coder = cfg.equipment["coder"]
        assert coder.model_extra is not None
        assert coder.model_extra.get("coupling_state_signal") == "filler.state"
        assert coder.model_extra.get("coupling_speed_signal") == "filler.line_speed"
        assert coder.model_extra.get("coupling_running_state") == 2

    def test_energy_follows_filler(self) -> None:
        """Energy line_power should follow filler.line_speed for F&B."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        energy = cfg.equipment["energy"]
        assert energy.signals["line_power"].parent == "filler.line_speed"

    def test_mixer_coil_address(self) -> None:
        """Mixer lid_closed should map to coil 100."""
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        lid = cfg.equipment["mixer"].signals["lid_closed"]
        assert lid.modbus_coil == 100


# ===================================================================
# Phase 4 Config Models (Task 4.3)
# ===================================================================


class TestBearingWearConfigUpdated:
    """Updated BearingWearConfig with Phase 4 fields."""

    def test_new_defaults(self) -> None:
        cfg = BearingWearConfig()
        assert cfg.base_rate == [0.001, 0.005]
        assert cfg.acceleration_k == [0.005, 0.01]
        assert cfg.warning_threshold == 15.0
        assert cfg.alarm_threshold == 25.0
        assert cfg.current_increase_percent == [1.0, 5.0]
        assert cfg.failure_vibration == [40.0, 50.0]

    def test_inverted_base_rate_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            BearingWearConfig(base_rate=[0.005, 0.001])

    def test_zero_warning_threshold_rejected(self) -> None:
        with pytest.raises(ValidationError, match="threshold must be positive"):
            BearingWearConfig(warning_threshold=0.0)

    def test_inverted_failure_vibration_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            BearingWearConfig(failure_vibration=[50.0, 40.0])

    def test_yaml_has_new_fields(self) -> None:
        """factory.yaml bearing_wear section should include Phase 4 fields."""
        cfg = load_config("config/factory.yaml", apply_env=False)
        bw = cfg.scenarios.bearing_wear
        assert bw.base_rate == [0.001, 0.005]
        assert bw.acceleration_k == [0.005, 0.01]
        assert bw.warning_threshold == 15.0
        assert bw.alarm_threshold == 25.0


class TestMicroStopConfig:
    def test_defaults(self) -> None:
        cfg = MicroStopConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_shift == [10, 50]
        assert cfg.duration_seconds == [5.0, 30.0]
        assert cfg.speed_drop_percent == [30.0, 80.0]
        assert cfg.ramp_down_seconds == [2.0, 5.0]
        assert cfg.ramp_up_seconds == [5.0, 15.0]

    def test_inverted_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            MicroStopConfig(duration_seconds=[30.0, 5.0])

    def test_inverted_speed_drop_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            MicroStopConfig(speed_drop_percent=[80.0, 30.0])

    def test_yaml_has_micro_stop(self) -> None:
        cfg = load_config("config/factory.yaml", apply_env=False)
        assert cfg.scenarios.micro_stop is not None
        assert cfg.scenarios.micro_stop.enabled is True
        assert cfg.scenarios.micro_stop.frequency_per_shift == [10, 50]

    def test_foodbev_micro_stop_disabled(self) -> None:
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert cfg.scenarios.micro_stop is not None
        assert cfg.scenarios.micro_stop.enabled is False

    def test_micro_stop_none_by_default(self) -> None:
        cfg = ScenariosConfig()
        assert cfg.micro_stop is None


class TestContextualAnomalyConfig:
    def test_defaults(self) -> None:
        cfg = ContextualAnomalyConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_week == [2, 5]
        assert cfg.types.heater_stuck.probability == 0.3
        assert cfg.types.pressure_bleed.probability == 0.2
        assert cfg.types.counter_false_trigger.increment_rate == 0.1
        assert cfg.types.hot_during_maintenance.probability == 0.15
        assert cfg.types.vibration_during_off.probability == 0.15

    def test_inverted_frequency_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            ContextualAnomalyConfig(frequency_per_week=[5, 2])

    def test_invalid_probability_rejected(self) -> None:
        with pytest.raises(ValidationError, match="probability must be between"):
            ContextualAnomalyConfig(
                types={"heater_stuck": {"probability": 1.5, "duration_seconds": [300.0, 3600.0]}}  # type: ignore[arg-type]
            )

    def test_yaml_has_contextual_anomaly(self) -> None:
        cfg = load_config("config/factory.yaml", apply_env=False)
        assert cfg.scenarios.contextual_anomaly is not None
        assert cfg.scenarios.contextual_anomaly.enabled is True
        assert cfg.scenarios.contextual_anomaly.frequency_per_week == [2, 5]

    def test_foodbev_contextual_anomaly_disabled(self) -> None:
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert cfg.scenarios.contextual_anomaly is not None
        assert cfg.scenarios.contextual_anomaly.enabled is False

    def test_contextual_anomaly_none_by_default(self) -> None:
        cfg = ScenariosConfig()
        assert cfg.contextual_anomaly is None


class TestIntermittentFaultConfig:
    def test_defaults(self) -> None:
        cfg = IntermittentFaultConfig()
        assert cfg.enabled is True
        faults = cfg.faults
        assert faults.bearing_intermittent.enabled is True
        assert faults.bearing_intermittent.phase1_duration_hours == [168.0, 336.0]
        assert faults.electrical_intermittent.enabled is True
        assert faults.sensor_intermittent.enabled is False
        assert faults.pneumatic_intermittent.phase3_transition is False
        assert faults.pneumatic_intermittent.affected_signals == ["coder.ink_pressure"]

    def test_bearing_inverted_spike_magnitude_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            BearingIntermittentConfig(spike_magnitude=[25.0, 15.0])

    def test_electrical_inverted_magnitude_pct_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            ElectricalIntermittentConfig(spike_magnitude_pct=[50.0, 20.0])

    def test_pneumatic_phase3_false_by_default(self) -> None:
        cfg = PneumaticIntermittentConfig()
        assert cfg.phase3_transition is False

    def test_yaml_has_intermittent_fault(self) -> None:
        cfg = load_config("config/factory.yaml", apply_env=False)
        assert cfg.scenarios.intermittent_fault is not None
        assert cfg.scenarios.intermittent_fault.enabled is True
        faults = cfg.scenarios.intermittent_fault.faults
        assert faults.bearing_intermittent.enabled is True
        assert faults.sensor_intermittent.enabled is False
        assert faults.pneumatic_intermittent.phase3_transition is False

    def test_foodbev_intermittent_fault_disabled(self) -> None:
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        assert cfg.scenarios.intermittent_fault is not None
        assert cfg.scenarios.intermittent_fault.enabled is False

    def test_intermittent_fault_none_by_default(self) -> None:
        cfg = ScenariosConfig()
        assert cfg.intermittent_fault is None


class TestCommDropConfig:
    def test_defaults(self) -> None:
        cfg = CommDropConfig()
        assert cfg.enabled is True
        assert cfg.frequency_per_hour == [1.0, 2.0]
        assert cfg.duration_seconds == [1.0, 10.0]

    def test_inverted_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            CommDropConfig(duration_seconds=[10.0, 1.0])

    def test_inverted_frequency_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            CommDropConfig(frequency_per_hour=[2.0, 1.0])


class TestDataQualityConfig:
    def test_defaults(self) -> None:
        cfg = DataQualityConfig()
        assert cfg.modbus_drop.enabled is True
        assert cfg.modbus_drop.duration_seconds == [1.0, 10.0]
        assert cfg.opcua_stale.duration_seconds == [5.0, 30.0]
        assert cfg.mqtt_drop.duration_seconds == [5.0, 30.0]
        assert cfg.noise.global_sigma_multiplier == 1.0
        assert cfg.duplicate_probability == 0.0001
        assert cfg.exception_probability == 0.001
        assert cfg.partial_modbus_response.probability == 0.0001
        assert cfg.sensor_disconnect.enabled is True
        assert cfg.sensor_disconnect.sentinel_defaults.temperature == 6553.5
        assert cfg.sensor_disconnect.sentinel_defaults.pressure == 0.0
        assert cfg.sensor_disconnect.sentinel_defaults.voltage == -32768.0
        assert cfg.stuck_sensor.enabled is True
        assert cfg.stuck_sensor.duration_seconds == [300.0, 14400.0]
        assert cfg.mqtt_timestamp_offset_hours == 0.0
        assert "press.impression_count" in cfg.counter_rollover

    def test_invalid_duplicate_probability(self) -> None:
        with pytest.raises(ValidationError, match="probability must be between"):
            DataQualityConfig(duplicate_probability=1.5)

    def test_invalid_exception_probability(self) -> None:
        with pytest.raises(ValidationError, match="probability must be between"):
            DataQualityConfig(exception_probability=-0.1)

    def test_inverted_response_delay_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            DataQualityConfig(response_delay_ms=[50, 0])

    def test_partial_modbus_probability_validated(self) -> None:
        with pytest.raises(ValidationError, match="probability must be between"):
            PartialModbusResponseConfig(probability=2.0)

    def test_sensor_disconnect_inverted_duration_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            SensorDisconnectConfig(duration_seconds=[300.0, 30.0])

    def test_stuck_sensor_inverted_frequency_rejected(self) -> None:
        with pytest.raises(ValidationError, match=r"min.*must be <= max"):
            StuckSensorConfig(frequency_per_week_per_signal=[2.0, 0.0])

    def test_noise_zero_multiplier_rejected(self) -> None:
        with pytest.raises(ValidationError, match="global_sigma_multiplier must be positive"):
            NoiseConfig(global_sigma_multiplier=0.0)

    def test_yaml_has_data_quality(self) -> None:
        cfg = load_config("config/factory.yaml", apply_env=False)
        dq = cfg.data_quality
        assert dq.modbus_drop.enabled is True
        assert dq.modbus_drop.duration_seconds == [1.0, 10.0]
        assert dq.opcua_stale.duration_seconds == [5.0, 30.0]
        assert dq.sensor_disconnect.sentinel_defaults.temperature == 6553.5
        assert dq.mqtt_timestamp_offset_hours == 0.0

    def test_foodbev_yaml_has_data_quality(self) -> None:
        cfg = load_config("config/factory-foodbev.yaml", apply_env=False)
        dq = cfg.data_quality
        assert dq.sensor_disconnect.enabled is True
        assert dq.stuck_sensor.enabled is True


class TestNoiseCalibrationPackaging:
    """Verify packaging profile noise params match PRD Section 10.3 table.

    PRD Reference: Section 10.3 Sensor Noise table.
    """

    @pytest.fixture(scope="class")
    def cfg(self) -> FactoryConfig:
        return load_config("config/factory.yaml", apply_env=False)

    # -- Press signals -------------------------------------------------------

    def test_press_line_speed_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["press"].signals["line_speed"]
        assert sig.noise_sigma == pytest.approx(0.5)
        assert sig.noise_type == "gaussian"

    def test_press_web_tension_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["press"].signals["web_tension"]
        assert sig.noise_sigma == pytest.approx(5.0)
        assert sig.noise_type == "gaussian"

    def test_press_registration_error_x_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["press"].signals["registration_error_x"]
        assert sig.noise_sigma == pytest.approx(0.01)  # PRD 10.3: camera resolution

    def test_press_registration_error_y_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["press"].signals["registration_error_y"]
        assert sig.noise_sigma == pytest.approx(0.01)

    def test_press_ink_viscosity_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["press"].signals["ink_viscosity"]
        assert sig.noise_sigma == pytest.approx(0.5)  # PRD 10.3: measurement variability

    def test_press_ink_temperature_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["press"].signals["ink_temperature"]
        assert sig.noise_sigma == pytest.approx(0.2)  # PRD 10.3: thermocouple noise

    def test_press_dryer_zone_noise(self, cfg: FactoryConfig) -> None:
        """All three dryer zones: 0.3 C, AR(1), phi=0.7 per PRD 10.3."""
        press_sigs = cfg.equipment["press"].signals
        for zone in ("dryer_temp_zone_1", "dryer_temp_zone_2", "dryer_temp_zone_3"):
            sig = press_sigs[zone]
            assert sig.noise_sigma == pytest.approx(0.3), f"{zone} sigma"
            assert sig.noise_type == "ar1", f"{zone} type"
            assert sig.noise_phi == pytest.approx(0.7), f"{zone} phi"

    def test_press_main_drive_current_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["press"].signals["main_drive_current"]
        assert sig.noise_sigma == pytest.approx(0.5)   # PRD 10.3: CT clamp noise
        assert sig.noise_type == "student_t"
        assert sig.noise_df == pytest.approx(8.0)

    def test_press_main_drive_speed_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["press"].signals["main_drive_speed"]
        assert sig.noise_sigma == pytest.approx(2.0)   # PRD 10.3: encoder resolution
        assert sig.noise_type == "gaussian"

    def test_press_nip_pressure_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["press"].signals["nip_pressure"]
        assert sig.noise_sigma == pytest.approx(0.05)  # PRD 10.3: transducer noise

    # -- Laminator signals ---------------------------------------------------

    def test_laminator_nip_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["laminator"].signals["nip_temp"]
        assert sig.noise_sigma == pytest.approx(0.3)   # PRD 10.3: similar to press dryer
        assert sig.noise_type == "ar1"
        assert sig.noise_phi == pytest.approx(0.7)

    def test_laminator_nip_pressure_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["laminator"].signals["nip_pressure"]
        assert sig.noise_sigma == pytest.approx(0.05)  # PRD 10.3: similar to press.nip_pressure

    def test_laminator_tunnel_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["laminator"].signals["tunnel_temp"]
        assert sig.noise_sigma == pytest.approx(0.3)   # PRD 10.3: laminator.*(other) Gaussian
        assert sig.noise_type == "gaussian"            # PRD 10.3: not AR(1) per table

    def test_laminator_web_speed_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["laminator"].signals["web_speed"]
        assert sig.noise_sigma == pytest.approx(0.5)   # PRD 10.3: similar to press.line_speed

    # -- Coder signals -------------------------------------------------------

    def test_coder_printhead_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["printhead_temp"]
        assert sig.noise_sigma == pytest.approx(0.5)   # PRD 10.3: PID-controlled
        assert sig.noise_type == "ar1"
        assert sig.noise_phi == pytest.approx(0.7)

    def test_coder_ink_pump_speed_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["ink_pump_speed"]
        assert sig.noise_sigma == pytest.approx(0.5)   # PRD 10.3: pump encoder noise

    def test_coder_ink_pressure_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["ink_pressure"]
        assert sig.noise_sigma == pytest.approx(60.0)  # PRD 10.3: pneumatic transients
        assert sig.noise_type == "student_t"
        assert sig.noise_df == pytest.approx(6.0)

    def test_coder_ink_viscosity_actual_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["ink_viscosity_actual"]
        assert sig.noise_sigma == pytest.approx(0.3)   # PRD 10.3: viscosity sensor noise

    def test_coder_supply_voltage_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["supply_voltage"]
        assert sig.noise_sigma == pytest.approx(0.1)   # PRD 10.3: PSU ripple

    # -- Environment signals -------------------------------------------------

    def test_env_ambient_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["environment"].signals["ambient_temp"]
        assert sig.noise_sigma == pytest.approx(0.1)   # PRD 10.3: IOLink resolution

    def test_env_ambient_humidity_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["environment"].signals["ambient_humidity"]
        assert sig.noise_sigma == pytest.approx(0.5)   # PRD 10.3: IOLink resolution

    # -- Energy signals ------------------------------------------------------

    def test_energy_line_power_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["energy"].signals["line_power"]
        assert sig.noise_sigma == pytest.approx(0.2)   # PRD 10.3: power meter resolution

    # -- Vibration signals ---------------------------------------------------

    def test_vibration_noise(self, cfg: FactoryConfig) -> None:
        """All three vibration axes: 0.3 mm/s, Student-t df=5 per PRD 10.3."""
        vib_sigs = cfg.equipment["vibration"].signals
        for axis in ("main_drive_x", "main_drive_y", "main_drive_z"):
            sig = vib_sigs[axis]
            assert sig.noise_sigma == pytest.approx(0.3), f"{axis} sigma"
            assert sig.noise_type == "student_t", f"{axis} type"
            assert sig.noise_df == pytest.approx(5.0), f"{axis} df"


class TestNoiseCalibrationFoodBev:
    """Verify F&B profile noise params match PRD Section 10.3 analogues.

    Mapping rules (per PROMPT_build.md task 4.14):
      - PID-controlled temps  → AR(1), phi=0.7
      - Load/torque signals   → Student-t, df=8
      - Motor/encoder speeds  → Gaussian
      - Pressure transducers  → Gaussian (similar to press.nip_pressure)
      - Other analogue        → Gaussian

    PRD Reference: Section 10.3 Sensor Noise table.
    """

    @pytest.fixture(scope="class")
    def cfg(self) -> FactoryConfig:
        return load_config("config/factory-foodbev.yaml", apply_env=False)

    # -- Mixer signals -------------------------------------------------------

    def test_mixer_speed_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["mixer"].signals["speed"]
        assert sig.noise_sigma == pytest.approx(5.0)   # encoder, ~0.17% of 3000 RPM
        assert sig.noise_type == "gaussian"

    def test_mixer_torque_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["mixer"].signals["torque"]
        assert sig.noise_sigma == pytest.approx(0.5)   # load/torque → Student-t df=8
        assert sig.noise_type == "student_t"
        assert sig.noise_df == pytest.approx(8.0)

    def test_mixer_batch_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["mixer"].signals["batch_temp"]
        assert sig.noise_sigma == pytest.approx(0.3)   # PID-controlled → AR(1)
        assert sig.noise_type == "ar1"
        assert sig.noise_phi == pytest.approx(0.7)

    def test_mixer_batch_weight_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["mixer"].signals["batch_weight"]
        assert sig.noise_sigma == pytest.approx(0.5)   # load cell, Gaussian
        assert sig.noise_type == "gaussian"

    # -- Oven signals --------------------------------------------------------

    def test_oven_zone_temps_noise(self, cfg: FactoryConfig) -> None:
        """All three oven zones: PID-controlled → AR(1), phi=0.7, 0.3 C."""
        oven_sigs = cfg.equipment["oven"].signals
        for zone in ("zone_1_temp", "zone_2_temp", "zone_3_temp"):
            sig = oven_sigs[zone]
            assert sig.noise_sigma == pytest.approx(0.3), f"{zone} sigma"
            assert sig.noise_type == "ar1", f"{zone} type"
            assert sig.noise_phi == pytest.approx(0.7), f"{zone} phi"

    def test_oven_product_core_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["oven"].signals["product_core_temp"]
        assert sig.noise_sigma == pytest.approx(0.3)   # thermocouple probe, Gaussian
        assert sig.noise_type == "gaussian"

    def test_oven_humidity_zone_2_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["oven"].signals["humidity_zone_2"]
        assert sig.noise_sigma == pytest.approx(0.5)   # similar to env.ambient_humidity
        assert sig.noise_type == "gaussian"

    def test_oven_output_power_noise(self, cfg: FactoryConfig) -> None:
        """All three zone output powers: Gaussian, 0.5%."""
        oven_sigs = cfg.equipment["oven"].signals
        for z in ("zone_1_output_power", "zone_2_output_power", "zone_3_output_power"):
            sig = oven_sigs[z]
            assert sig.noise_sigma == pytest.approx(0.5), f"{z} sigma"
            assert sig.noise_type == "gaussian", f"{z} type"

    # -- Filler signals ------------------------------------------------------

    def test_filler_fill_weight_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["filler"].signals["fill_weight"]
        assert sig.noise_sigma == pytest.approx(1.0)   # load cell → Student-t df=8
        assert sig.noise_type == "student_t"
        assert sig.noise_df == pytest.approx(8.0)

    def test_filler_hopper_level_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["filler"].signals["hopper_level"]
        assert sig.noise_sigma == pytest.approx(0.5)   # proximity sensor, Gaussian
        assert sig.noise_type == "gaussian"

    # -- Sealer signals ------------------------------------------------------

    def test_sealer_seal_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["sealer"].signals["seal_temp"]
        assert sig.noise_sigma == pytest.approx(0.5)   # PID-controlled → AR(1)
        assert sig.noise_type == "ar1"
        assert sig.noise_phi == pytest.approx(0.7)

    def test_sealer_seal_pressure_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["sealer"].signals["seal_pressure"]
        assert sig.noise_sigma == pytest.approx(0.05)  # similar to press.nip_pressure
        assert sig.noise_type == "gaussian"

    def test_sealer_gas_co2_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["sealer"].signals["gas_co2_pct"]
        assert sig.noise_sigma == pytest.approx(0.3)   # gas analyser, Gaussian
        assert sig.noise_type == "gaussian"

    def test_sealer_gas_n2_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["sealer"].signals["gas_n2_pct"]
        assert sig.noise_sigma == pytest.approx(0.3)
        assert sig.noise_type == "gaussian"

    def test_sealer_vacuum_level_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["sealer"].signals["vacuum_level"]
        assert sig.noise_sigma == pytest.approx(0.01)  # vacuum transducer, Gaussian
        assert sig.noise_type == "gaussian"

    # -- QC signals ----------------------------------------------------------

    def test_qc_actual_weight_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["qc"].signals["actual_weight"]
        assert sig.noise_sigma == pytest.approx(1.0)   # checkweigher load cell, Gaussian
        assert sig.noise_type == "gaussian"

    def test_qc_throughput_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["qc"].signals["throughput"]
        assert sig.noise_sigma == pytest.approx(0.2)   # similar to press.line_speed
        assert sig.noise_type == "gaussian"

    # -- Chiller signals -----------------------------------------------------

    def test_chiller_room_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["chiller"].signals["room_temp"]
        assert sig.noise_sigma == pytest.approx(0.1)   # thermistor, similar to env.ambient_temp
        assert sig.noise_type == "gaussian"

    def test_chiller_suction_pressure_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["chiller"].signals["suction_pressure"]
        assert sig.noise_sigma == pytest.approx(0.05)  # similar to press.nip_pressure
        assert sig.noise_type == "gaussian"

    def test_chiller_discharge_pressure_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["chiller"].signals["discharge_pressure"]
        assert sig.noise_sigma == pytest.approx(0.1)   # higher pressure range, Gaussian
        assert sig.noise_type == "gaussian"

    # -- CIP signals ---------------------------------------------------------

    def test_cip_wash_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["cip"].signals["wash_temp"]
        assert sig.noise_sigma == pytest.approx(0.5)   # PID-controlled → AR(1)
        assert sig.noise_type == "ar1"
        assert sig.noise_phi == pytest.approx(0.7)

    def test_cip_flow_rate_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["cip"].signals["flow_rate"]
        assert sig.noise_sigma == pytest.approx(0.5)   # flow meter, Gaussian
        assert sig.noise_type == "gaussian"

    def test_cip_conductivity_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["cip"].signals["conductivity"]
        assert sig.noise_sigma == pytest.approx(0.3)   # conductivity sensor, Gaussian
        assert sig.noise_type == "gaussian"

    # -- Coder signals (shared equipment, same calibration as packaging) -----

    def test_coder_printhead_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["printhead_temp"]
        assert sig.noise_sigma == pytest.approx(0.5)   # PID-controlled, same as packaging
        assert sig.noise_type == "ar1"
        assert sig.noise_phi == pytest.approx(0.7)

    def test_coder_ink_pump_speed_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["ink_pump_speed"]
        assert sig.noise_sigma == pytest.approx(0.5)   # pump encoder, same as packaging
        assert sig.noise_type == "gaussian"

    def test_coder_ink_pressure_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["ink_pressure"]
        assert sig.noise_sigma == pytest.approx(60.0)  # pneumatic transients, same as packaging
        assert sig.noise_type == "student_t"
        assert sig.noise_df == pytest.approx(6.0)

    def test_coder_ink_viscosity_actual_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["ink_viscosity_actual"]
        assert sig.noise_sigma == pytest.approx(0.3)   # viscosity sensor, same as packaging
        assert sig.noise_type == "gaussian"

    def test_coder_supply_voltage_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["coder"].signals["supply_voltage"]
        assert sig.noise_sigma == pytest.approx(0.1)   # PSU ripple, same as packaging
        assert sig.noise_type == "gaussian"

    # -- Environment signals (shared, same calibration as packaging) ---------

    def test_env_ambient_temp_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["environment"].signals["ambient_temp"]
        assert sig.noise_sigma == pytest.approx(0.1)   # IOLink resolution
        assert sig.noise_type == "gaussian"

    def test_env_ambient_humidity_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["environment"].signals["ambient_humidity"]
        assert sig.noise_sigma == pytest.approx(0.5)   # IOLink resolution
        assert sig.noise_type == "gaussian"

    # -- Energy signals ------------------------------------------------------

    def test_energy_line_power_noise(self, cfg: FactoryConfig) -> None:
        sig = cfg.equipment["energy"].signals["line_power"]
        assert sig.noise_sigma == pytest.approx(0.2)   # power meter resolution
        assert sig.noise_type == "gaussian"
