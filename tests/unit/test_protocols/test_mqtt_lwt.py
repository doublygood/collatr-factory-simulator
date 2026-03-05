"""Tests for profile-specific LWT topic resolution (task 6e.2 / Y26).

PRD Section 3.3: LWT topic should be profile-specific so that packaging
and F&B instances publish offline messages to distinct MQTT topics.

When MqttProtocolConfig.lwt_topic is empty (the new default), resolve_lwt_topic()
auto-generates: {topic_prefix}/{line_id}/status.
When lwt_topic is explicitly set, it is used as-is for backward compatibility.
"""

from __future__ import annotations

from pathlib import Path

from factory_simulator.config import MqttProtocolConfig, load_config
from factory_simulator.protocols.mqtt_publisher import resolve_lwt_topic

_PACKAGING_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"
_FOODBEV_CONFIG_PATH = (
    Path(__file__).resolve().parents[3] / "config" / "factory-foodbev.yaml"
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_lwt_topic_auto_generated_packaging() -> None:
    """Empty lwt_topic → {topic_prefix}/{line_id}/status for packaging."""
    cfg = MqttProtocolConfig(
        topic_prefix="collatr/factory",
        line_id="packaging1",
        lwt_topic="",
    )
    assert resolve_lwt_topic(cfg) == "collatr/factory/packaging1/status"


def test_lwt_topic_auto_generated_foodbev() -> None:
    """Empty lwt_topic → {topic_prefix}/{line_id}/status for F&B."""
    cfg = MqttProtocolConfig(
        topic_prefix="collatr/factory",
        line_id="foodbev1",
        lwt_topic="",
    )
    assert resolve_lwt_topic(cfg) == "collatr/factory/foodbev1/status"


def test_lwt_topic_explicit_preserved() -> None:
    """Explicit lwt_topic is used as-is (backward compat)."""
    cfg = MqttProtocolConfig(
        topic_prefix="collatr/factory",
        line_id="packaging1",
        lwt_topic="custom/override/topic",
    )
    assert resolve_lwt_topic(cfg) == "custom/override/topic"


def test_both_configs_produce_different_lwt_topics() -> None:
    """Packaging and F&B YAML configs resolve to different LWT topics."""
    pkg_config = load_config(_PACKAGING_CONFIG_PATH)
    fnb_config = load_config(_FOODBEV_CONFIG_PATH)

    pkg_topic = resolve_lwt_topic(pkg_config.protocols.mqtt)
    fnb_topic = resolve_lwt_topic(fnb_config.protocols.mqtt)

    assert pkg_topic != fnb_topic, "Profiles must produce different LWT topics"
    assert "packaging1" in pkg_topic
    assert "foodbev1" in fnb_topic


def test_packaging_config_lwt_topic_default_empty() -> None:
    """Packaging YAML config no longer sets an explicit lwt_topic."""
    config = load_config(_PACKAGING_CONFIG_PATH)
    assert config.protocols.mqtt.lwt_topic == ""


def test_foodbev_config_lwt_topic_default_empty() -> None:
    """F&B YAML config no longer sets an explicit lwt_topic."""
    config = load_config(_FOODBEV_CONFIG_PATH)
    assert config.protocols.mqtt.lwt_topic == ""
