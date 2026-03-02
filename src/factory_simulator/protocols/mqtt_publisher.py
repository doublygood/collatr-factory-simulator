"""MQTT publisher adapter for the Collatr Factory Simulator.

Reads signal values from the SignalStore and publishes JSON payloads to
an MQTT broker using paho-mqtt 2.0 (CallbackAPIVersion.VERSION2).

The publisher runs paho's network loop in its own thread (loop_start())
and calls client.publish() from the asyncio event loop.  paho 2.x
publish() is thread-safe, so no synchronisation is needed between the
asyncio loop (writer) and the paho network thread (reader).

Features:
- Topic map built from signal configs (mqtt_topic field)
- QoS 1 for state/fault/counter signals; QoS 0 for analog/env/vibration
- Retain=True for all topics except vibration/* (PRD 3.3.8)
- Event-driven publish for state/prints_total/nozzle_health/gutter_fault
- Timed publish for all other signals (interval from sample_rate_ms)
- LWT on the configured status topic
- Client-side buffer: buffer_limit messages, drop oldest (PRD 3.3)
- JSON payload: {timestamp, value, unit, quality} (PRD 3.3.4)
- Batch vibration topic: combined x/y/z payload (PRD 3.3.6)

PRD Reference: Section 3.3, Appendix C (MQTT Topic Map)
CLAUDE.md Rule 9: No locks (single writer, asyncio single-threaded).
CLAUDE.md Rule 10: Configuration via Pydantic.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

if TYPE_CHECKING:
    from factory_simulator.config import FactoryConfig
    from factory_simulator.store import SignalStore, SignalValue

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# QoS and retain rules (PRD Section 3.3.5, 3.3.8)
# ---------------------------------------------------------------------------

# Relative topic suffixes that require QoS 1 (PRD 3.3.5)
_QOS1_SUFFIXES: frozenset[str] = frozenset({
    "coder/state",
    "coder/prints_total",
    "coder/nozzle_health",
    "coder/gutter_fault",
})

# Relative topic suffixes that are event-driven (publish on value change)
_EVENT_DRIVEN_SUFFIXES: frozenset[str] = frozenset({
    "coder/state",
    "coder/prints_total",
    "coder/nozzle_health",
    "coder/gutter_fault",
})

# Relative topic prefixes for topics published without retain (PRD 3.3.8)
_NO_RETAIN_PREFIXES: tuple[str, ...] = ("vibration/",)


# ---------------------------------------------------------------------------
# Topic entry
# ---------------------------------------------------------------------------


@dataclass
class TopicEntry:
    """Published MQTT topic configuration for one signal."""

    signal_id: str
    topic: str          # Full MQTT topic path
    qos: int            # MQTT QoS level (0 or 1)
    retain: bool        # MQTT retained message flag
    interval_s: float   # Publish interval in seconds; 0.0 = event-driven
    unit: str           # Engineering unit string for payload

    # Mutable scheduling state (excluded from equality comparison)
    last_published: float = field(default=0.0, compare=False)
    last_value: float | str | None = field(default=None, compare=False)


@dataclass
class BatchVibrationEntry:
    """Batch vibration topic config: publishes x/y/z axes in one message.

    PRD Section 3.3.6: combined payload ``{timestamp, x, y, z, unit, quality}``.
    """

    topic: str             # Full MQTT topic path (.../vibration/main_drive)
    qos: int               # Always 0 (vibration is loss-tolerant)
    retain: bool           # Always False (vibration/* not retained)
    interval_s: float      # Same as per-axis interval (typically 1.0 s)
    unit: str              # Engineering unit (typically "mm/s")
    x_signal_id: str       # SignalStore key for x-axis
    y_signal_id: str       # SignalStore key for y-axis
    z_signal_id: str       # SignalStore key for z-axis

    # Mutable scheduling state (excluded from equality comparison)
    last_published: float = field(default=0.0, compare=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Quality ranking: higher rank = worse quality
_QUALITY_RANK: dict[str, int] = {"good": 0, "uncertain": 1, "bad": 2}


def _worst_quality(qualities: list[str]) -> str:
    """Return the worst quality string from a list."""
    return max(qualities, key=lambda q: _QUALITY_RANK.get(q, 0))


def _qos_for_topic(relative: str) -> int:
    """Return MQTT QoS for a relative topic path (e.g. 'coder/state')."""
    return 1 if relative in _QOS1_SUFFIXES else 0


def _retain_for_topic(relative: str) -> bool:
    """Return True if the topic should be published with the retain flag."""
    return not any(relative.startswith(p) for p in _NO_RETAIN_PREFIXES)


def _is_event_driven(relative: str) -> bool:
    """Return True if the topic should publish on value change (not timer)."""
    return relative in _EVENT_DRIVEN_SUFFIXES


def make_payload(value: float | str, quality: str, unit: str) -> bytes:
    """Build a JSON payload per PRD Section 3.3.4.

    Parameters
    ----------
    value:
        Signal value. Numeric signals use float/int; boolean signals use 0/1.
    quality:
        Quality flag: ``'good'``, ``'uncertain'``, or ``'bad'``.
    unit:
        Engineering unit string (e.g. ``'C'``, ``'m/min'``).

    Returns
    -------
    bytes
        UTF-8 encoded JSON with fields: ``timestamp``, ``value``,
        ``unit``, ``quality``.
    """
    now = datetime.now(UTC)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    payload_dict = {
        "timestamp": ts,
        "value": value,
        "unit": unit,
        "quality": quality,
    }
    return json.dumps(payload_dict).encode()


def make_batch_vibration_payload(
    x: float, y: float, z: float, quality: str, unit: str
) -> bytes:
    """Build a batch vibration JSON payload per PRD Section 3.3.6.

    Parameters
    ----------
    x, y, z:
        Vibration values for each axis in engineering units.
    quality:
        Combined quality flag: ``'good'``, ``'uncertain'``, or ``'bad'``.
    unit:
        Engineering unit string (e.g. ``'mm/s'``).

    Returns
    -------
    bytes
        UTF-8 encoded JSON with fields: ``timestamp``, ``x``, ``y``, ``z``,
        ``unit``, ``quality``.
    """
    now = datetime.now(UTC)
    ts = now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
    payload_dict = {
        "timestamp": ts,
        "x": x,
        "y": y,
        "z": z,
        "unit": unit,
        "quality": quality,
    }
    return json.dumps(payload_dict).encode()


def build_batch_vibration_entry(config: FactoryConfig) -> BatchVibrationEntry | None:
    """Build a batch vibration entry for the packaging profile, if applicable.

    Scans all equipment signal configs for ``vibration/*_x``, ``vibration/*_y``,
    and ``vibration/*_z`` mqtt_topic groups.  Returns a :class:`BatchVibrationEntry`
    for the first complete group found, or ``None`` if no vibration signals exist.

    Parameters
    ----------
    config:
        Validated :class:`~factory_simulator.config.FactoryConfig`.

    Returns
    -------
    BatchVibrationEntry | None
        Batch entry ready for publishing, or ``None`` for profiles without
        vibration signals.
    """
    mqtt_cfg = config.protocols.mqtt
    site_id = config.factory.site_id
    line_id = mqtt_cfg.line_id
    prefix = f"{mqtt_cfg.topic_prefix}/{site_id}/{line_id}"

    # Collect vibration per-axis signals grouped by base topic
    # groups[base] = {axis: (signal_id, interval_s, unit)}
    groups: dict[str, dict[str, tuple[str, float, str]]] = {}

    for eq_id, eq_cfg in config.equipment.items():
        if not eq_cfg.enabled:
            continue
        for sig_name, sig_cfg in eq_cfg.signals.items():
            if sig_cfg.mqtt_topic is None:
                continue
            relative = sig_cfg.mqtt_topic
            if not relative.startswith("vibration/"):
                continue
            for axis in ("_x", "_y", "_z"):
                if relative.endswith(axis):
                    base = relative[: -len(axis)]  # e.g. "vibration/main_drive"
                    if base not in groups:
                        groups[base] = {}
                    sig_id = f"{eq_id}.{sig_name}"
                    interval_s = (sig_cfg.sample_rate_ms or 1000) / 1000.0
                    unit = sig_cfg.units or "mm/s"
                    groups[base][axis[1:]] = (sig_id, interval_s, unit)
                    break

    for base, axes in groups.items():
        if "x" in axes and "y" in axes and "z" in axes:
            x_sig_id, interval_s, unit = axes["x"]
            y_sig_id, _, _ = axes["y"]
            z_sig_id, _, _ = axes["z"]
            return BatchVibrationEntry(
                topic=f"{prefix}/{base}",
                qos=0,
                retain=False,
                interval_s=interval_s,
                unit=unit,
                x_signal_id=x_sig_id,
                y_signal_id=y_sig_id,
                z_signal_id=z_sig_id,
            )

    return None


def build_topic_map(config: FactoryConfig) -> list[TopicEntry]:
    """Build the list of TopicEntry objects from signal configs.

    Scans all equipment signal configs for ``mqtt_topic`` and creates a
    TopicEntry for each.  Topic paths, QoS, retain, and publish intervals
    are derived from the topic suffix and ``sample_rate_ms``.

    Parameters
    ----------
    config:
        Validated :class:`~factory_simulator.config.FactoryConfig`.

    Returns
    -------
    list[TopicEntry]
        One entry per signal that has ``mqtt_topic`` set.
    """
    mqtt_cfg = config.protocols.mqtt
    site_id = config.factory.site_id
    line_id = mqtt_cfg.line_id
    prefix = f"{mqtt_cfg.topic_prefix}/{site_id}/{line_id}"
    per_axis_enabled = mqtt_cfg.vibration_per_axis_enabled

    entries: list[TopicEntry] = []
    for eq_id, eq_cfg in config.equipment.items():
        if not eq_cfg.enabled:
            continue
        for sig_name, sig_cfg in eq_cfg.signals.items():
            if sig_cfg.mqtt_topic is None:
                continue

            signal_id = f"{eq_id}.{sig_name}"
            relative = sig_cfg.mqtt_topic

            # Skip per-axis vibration topics when disabled via config (PRD 3.3.6)
            if not per_axis_enabled and relative.startswith("vibration/"):
                continue

            topic = f"{prefix}/{relative}"

            qos = _qos_for_topic(relative)
            retain = _retain_for_topic(relative)
            event_driven = _is_event_driven(relative)

            # Publish interval: 0.0 for event-driven, sample_rate in seconds
            interval_s = (
                0.0
                if event_driven
                else (sig_cfg.sample_rate_ms or 1000) / 1000.0
            )

            entries.append(TopicEntry(
                signal_id=signal_id,
                topic=topic,
                qos=qos,
                retain=retain,
                interval_s=interval_s,
                unit=sig_cfg.units or "",
            ))

    logger.info("MQTT topic map built: %d topics", len(entries))
    return entries


# ---------------------------------------------------------------------------
# MqttPublisher
# ---------------------------------------------------------------------------


class MqttPublisher:
    """MQTT publisher adapter that reads from the SignalStore.

    Builds the topic map from factory configuration and periodically
    publishes signal values to the MQTT broker.  Runs paho's network loop
    in a background thread and drives publish scheduling from the asyncio
    event loop.

    Parameters
    ----------
    config:
        Validated :class:`~factory_simulator.config.FactoryConfig`.
    store:
        Shared :class:`~factory_simulator.store.SignalStore` instance.
    host:
        Broker hostname override (for testing).  Defaults to config value.
    port:
        Broker port override (for testing).  Defaults to config value.
    client:
        Injected paho.mqtt.Client (for unit testing).  If ``None``,
        a new client is created with LWT and buffer settings.
    """

    def __init__(
        self,
        config: FactoryConfig,
        store: SignalStore,
        *,
        host: str | None = None,
        port: int | None = None,
        client: mqtt.Client | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._mqtt_cfg = config.protocols.mqtt
        self._host = host or self._mqtt_cfg.broker_host
        self._port = port or self._mqtt_cfg.broker_port

        # Build topic map from signal configs
        self._topic_entries = build_topic_map(config)

        # Batch vibration entry (None for F&B profile, which has no vibration)
        self._batch_vib_entry: BatchVibrationEntry | None = build_batch_vibration_entry(config)

        # paho client (injected for unit testing, else created fresh)
        self._client: mqtt.Client = client or self._create_client()

        # Async task handle
        self._publish_task: asyncio.Task[None] | None = None

    # -- Properties -----------------------------------------------------------

    @property
    def topic_entries(self) -> list[TopicEntry]:
        """The topic map (for testing and introspection)."""
        return self._topic_entries

    @property
    def batch_vibration_entry(self) -> BatchVibrationEntry | None:
        """The batch vibration entry, or None if the profile has no vibration."""
        return self._batch_vib_entry

    # -- Client setup ---------------------------------------------------------

    def _create_client(self) -> mqtt.Client:
        """Create and configure a paho-mqtt client with LWT and buffer."""
        client: mqtt.Client = mqtt.Client(
            callback_api_version=CallbackAPIVersion.VERSION2,
            client_id=self._mqtt_cfg.client_id,
            protocol=mqtt.MQTTv311,
        )
        # Last Will and Testament (PRD 3.3)
        client.will_set(
            self._mqtt_cfg.lwt_topic,
            payload=self._mqtt_cfg.lwt_payload,
            qos=1,
            retain=True,
        )
        # Client-side buffer: 1000 messages, drop oldest (PRD 3.3)
        client.max_queued_messages_set(self._mqtt_cfg.buffer_limit)
        # Optional username/password authentication
        if self._mqtt_cfg.username:
            client.username_pw_set(
                self._mqtt_cfg.username,
                self._mqtt_cfg.password,
            )
        return client

    # -- Publish helpers ------------------------------------------------------

    def _publish_entry(self, entry: TopicEntry, sv: SignalValue) -> None:
        """Publish one signal value to its MQTT topic."""
        payload = make_payload(sv.value, sv.quality, entry.unit)
        self._client.publish(
            entry.topic,
            payload=payload,
            qos=entry.qos,
            retain=entry.retain,
        )

    def _publish_batch_vib(self, now: float) -> None:
        """Publish the batch vibration topic when due (PRD 3.3.6).

        Reads x, y, z signal values from the store.  Skips if any axis
        is missing.  Uses the worst quality across all three axes.

        Parameters
        ----------
        now:
            Current wall-clock time from ``time.monotonic()``.
        """
        entry = self._batch_vib_entry
        if entry is None:
            return
        if now - entry.last_published < entry.interval_s:
            return

        sv_x = self._store.get(entry.x_signal_id)
        sv_y = self._store.get(entry.y_signal_id)
        sv_z = self._store.get(entry.z_signal_id)
        if sv_x is None or sv_y is None or sv_z is None:
            return

        quality = _worst_quality([sv_x.quality, sv_y.quality, sv_z.quality])
        payload = make_batch_vibration_payload(
            float(sv_x.value), float(sv_y.value), float(sv_z.value),
            quality, entry.unit,
        )
        self._client.publish(
            entry.topic, payload=payload, qos=entry.qos, retain=entry.retain
        )
        entry.last_published = now

    def _publish_due(self, now: float) -> None:
        """Check all entries and publish those that are due.

        Parameters
        ----------
        now:
            Current wall-clock time from ``time.monotonic()``.
        """
        self._publish_batch_vib(now)

        for entry in self._topic_entries:
            sv = self._store.get(entry.signal_id)
            if sv is None:
                continue

            if entry.interval_s == 0.0:
                # Event-driven: publish when the value changes
                if sv.value != entry.last_value:
                    self._publish_entry(entry, sv)
                    entry.last_published = now
                    entry.last_value = sv.value
            else:
                # Timed: publish when the interval has elapsed
                if now - entry.last_published >= entry.interval_s:
                    self._publish_entry(entry, sv)
                    entry.last_published = now
                    entry.last_value = sv.value

    # -- Async lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Connect to broker and start the publish loop."""
        self._client.connect(self._host, self._port, keepalive=60)
        self._client.loop_start()
        self._publish_task = asyncio.create_task(self._publish_loop())
        logger.info(
            "MQTT publisher started, broker=%s:%d, topics=%d",
            self._host, self._port, len(self._topic_entries),
        )

    async def stop(self) -> None:
        """Stop the publish loop and disconnect from broker."""
        if self._publish_task is not None:
            self._publish_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._publish_task
            self._publish_task = None

        self._client.loop_stop()
        self._client.disconnect()
        logger.info("MQTT publisher stopped")

    async def _publish_loop(self) -> None:
        """Periodically check and publish due signal values."""
        try:
            while True:
                now = time.monotonic()
                self._publish_due(now)
                await asyncio.sleep(0.1)  # 100 ms scheduling granularity
        except asyncio.CancelledError:
            pass
