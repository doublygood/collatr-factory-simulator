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
    EquipmentConfig,
    ErrorInjectionConfig,
    FactoryInfo,
    ModbusProtocolConfig,
    MqttProtocolConfig,
    OpcuaProtocolConfig,
    ScenariosConfig,
    ShiftsConfig,
    SignalConfig,
    SimulationConfig,
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
        with pytest.raises(ValidationError, match="noise_df.*must be >= 3"):
            SignalConfig(model="steady_state", noise_type="student_t", noise_df=2.0)

    def test_student_t_df_valid(self) -> None:
        sig = SignalConfig(model="steady_state", noise_type="student_t", noise_df=5.0)
        assert sig.noise_df == 5.0

    def test_ar1_phi_range(self) -> None:
        with pytest.raises(ValidationError, match="noise_phi.*must be in"):
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
        with pytest.raises(ValidationError, match="min.*must be <= max"):
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
