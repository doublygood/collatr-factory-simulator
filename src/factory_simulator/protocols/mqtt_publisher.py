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
from typing import TYPE_CHECKING

import numpy as np
import paho.mqtt.client as mqtt
from paho.mqtt.enums import CallbackAPIVersion

from factory_simulator.protocols.comm_drop import CommDropScheduler
from factory_simulator.time_utils import sim_time_to_iso

if TYPE_CHECKING:
    from factory_simulator.config import FactoryConfig, MqttProtocolConfig
    from factory_simulator.store import SignalStore, SignalValue
    from factory_simulator.topology import ClockDriftModel

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


def resolve_lwt_topic(mqtt_cfg: MqttProtocolConfig) -> str:
    """Return the resolved LWT topic (PRD 3.3, Y26 fix).

    When ``mqtt_cfg.lwt_topic`` is empty (the default), the topic is
    auto-generated as ``{topic_prefix}/{line_id}/status``, making it
    profile-specific.  An explicit non-empty value is returned unchanged
    for backward compatibility.
    """
    if mqtt_cfg.lwt_topic:
        return mqtt_cfg.lwt_topic
    return f"{mqtt_cfg.topic_prefix}/{mqtt_cfg.line_id}/status"


def make_payload(
    value: float | str, quality: str, unit: str, sim_time: float,
    offset_hours: float = 0.0,
) -> bytes:
    """Build a JSON payload per PRD Section 3.3.4.

    Parameters
    ----------
    value:
        Signal value. Numeric signals use float/int; boolean signals use 0/1.
    quality:
        Quality flag: ``'good'``, ``'uncertain'``, or ``'bad'``.
    unit:
        Engineering unit string (e.g. ``'C'``, ``'m/min'``).
    sim_time:
        Simulated time in seconds (from SignalValue.timestamp).
        Converted to ISO 8601 using the reference epoch (Rule 6).
    offset_hours:
        Timezone offset applied to the timestamp (PRD 10.7).  Default 0.0
        (no offset).

    Returns
    -------
    bytes
        UTF-8 encoded JSON with fields: ``timestamp``, ``value``,
        ``unit``, ``quality``.
    """
    ts = sim_time_to_iso(sim_time, offset_hours * 3600.0)
    payload_dict = {
        "timestamp": ts,
        "value": value,
        "unit": unit,
        "quality": quality,
    }
    return json.dumps(payload_dict).encode()


def make_batch_vibration_payload(
    x: float, y: float, z: float, quality: str, unit: str, sim_time: float,
    offset_hours: float = 0.0,
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
    sim_time:
        Simulated time in seconds (from SignalValue.timestamp).
        Converted to ISO 8601 using the reference epoch (Rule 6).
    offset_hours:
        Timezone offset applied to the timestamp (PRD 10.7).  Default 0.0
        (no offset).

    Returns
    -------
    bytes
        UTF-8 encoded JSON with fields: ``timestamp``, ``x``, ``y``, ``z``,
        ``unit``, ``quality``.
    """
    ts = sim_time_to_iso(sim_time, offset_hours * 3600.0)
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
        comm_drop_rng: np.random.Generator | None = None,
        duplicate_rng: np.random.Generator | None = None,
        clock_drift: ClockDriftModel | None = None,
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
        # Visibility callbacks: log connect/disconnect events (paho v2 signatures).
        # Paho's loop_start() already handles mid-run reconnection automatically;
        # these callbacks are for observability only.
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

        # Async task handle
        self._publish_task: asyncio.Task[None] | None = None

        # Communication drop scheduler (PRD 10.2)
        _rng = comm_drop_rng if comm_drop_rng is not None else np.random.default_rng()
        self._drop_scheduler = CommDropScheduler(
            config.data_quality.mqtt_drop, _rng,
        )

        # Duplicate timestamp injection (PRD 10.5)
        self._dup_rng: np.random.Generator | None = duplicate_rng
        self._dup_prob: float = config.data_quality.duplicate_probability / 2.0

        # Timezone offset for MQTT timestamps (PRD 10.7)
        self._offset_hours: float = config.data_quality.mqtt_timestamp_offset_hours

        # Per-controller clock drift for MQTT timestamps (PRD 3a.5)
        self._clock_drift: ClockDriftModel | None = clock_drift

    # -- Properties -----------------------------------------------------------

    @property
    def topic_entries(self) -> list[TopicEntry]:
        """The topic map (for testing and introspection)."""
        return self._topic_entries

    @property
    def batch_vibration_entry(self) -> BatchVibrationEntry | None:
        """The batch vibration entry, or None if the profile has no vibration."""
        return self._batch_vib_entry

    @property
    def comm_drop_active(self) -> bool:
        """True if an MQTT communication drop is currently active (PRD 10.2)."""
        t = time.monotonic()
        self._drop_scheduler.update(t)
        return self._drop_scheduler.is_active(t)

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
            resolve_lwt_topic(self._mqtt_cfg),
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
        """Publish one signal value to its MQTT topic.

        Applies clock drift (PRD 3a.5) and timezone offset (PRD 10.7) to
        the timestamp.  Occasionally publishes the same message twice
        within 1 ms to simulate sensor gateway double-publish (PRD 10.5).
        """
        ts = sv.timestamp
        if self._clock_drift is not None:
            ts = self._clock_drift.drifted_time(ts)
        payload = make_payload(sv.value, sv.quality, entry.unit, ts,
                               self._offset_hours)
        self._client.publish(
            entry.topic,
            payload=payload,
            qos=entry.qos,
            retain=entry.retain,
        )
        # Duplicate publish (PRD 10.5): same payload, same topic, within 1 ms
        if self._dup_rng is not None and self._dup_rng.random() < self._dup_prob:
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
        # Use the most recent timestamp among the three axes (Rule 6)
        sim_time = max(sv_x.timestamp, sv_y.timestamp, sv_z.timestamp)
        # Apply clock drift (PRD 3a.5) if configured
        if self._clock_drift is not None:
            sim_time = self._clock_drift.drifted_time(sim_time)
        payload = make_batch_vibration_payload(
            float(sv_x.value), float(sv_y.value), float(sv_z.value),
            quality, entry.unit, sim_time, self._offset_hours,
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

    # -- Connection callbacks -------------------------------------------------

    def _on_connect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: object,
        reason_code: object,
        properties: object,
    ) -> None:
        """Log MQTT connection result (paho v2 on_connect callback).

        Called by paho after each connection attempt.  Registered for
        observability only; paho's loop_start() handles reconnection.

        Paho v2 signature: ``on_connect(client, userdata, connect_flags,
        reason_code, properties)``.
        """
        if getattr(reason_code, "is_failure", False):
            logger.error(
                "MQTT connection failed: %s (broker=%s:%d)",
                reason_code, self._host, self._port,
            )
        else:
            logger.info(
                "MQTT connected: %s (broker=%s:%d)",
                reason_code, self._host, self._port,
            )

    def _on_disconnect(
        self,
        client: mqtt.Client,
        userdata: object,
        flags: object,
        reason_code: object,
        properties: object,
    ) -> None:
        """Log MQTT disconnection (paho v2 on_disconnect callback).

        Called by paho when the connection drops.  Registered for
        observability only; paho's loop_start() handles reconnection.

        Paho v2 signature: ``on_disconnect(client, userdata, disconnect_flags,
        reason_code, properties)``.
        """
        logger.warning(
            "MQTT disconnected: %s (broker=%s:%d)",
            reason_code, self._host, self._port,
        )

    # -- Async lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Connect to broker and start the publish loop.

        Retries the initial ``connect()`` call up to 4 times with
        exponential backoff (delays: 1 s, 2 s, 4 s) to tolerate Docker
        Compose startup ordering where the Mosquitto sidecar may not be
        ready when the simulator starts.

        Paho's ``loop_start()`` handles reconnection for mid-run drops
        automatically; no additional reconnection logic is added here.

        Raises
        ------
        Exception
            If all 4 connection attempts fail, the last exception is re-raised.
        """
        _delays = (1.0, 2.0, 4.0)
        _max_attempts = len(_delays) + 1  # 4 attempts total
        last_exc: Exception | None = None

        for attempt in range(_max_attempts):
            try:
                self._client.connect(self._host, self._port, keepalive=60)
                break
            except Exception as exc:
                last_exc = exc
                if attempt < len(_delays):
                    delay = _delays[attempt]
                    logger.warning(
                        "MQTT connect attempt %d/%d failed (%s); retrying in %.0f s",
                        attempt + 1,
                        _max_attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
                else:
                    logger.error(
                        "MQTT connect failed after %d attempts: %s",
                        _max_attempts,
                        exc,
                    )
        else:
            raise last_exc  # type: ignore[misc]

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
        """Periodically check and publish due signal values.

        Skips publishing during an active communication drop (PRD 10.2):
        QoS 0 messages are silently dropped; QoS 1 messages resume
        delivery when the drop ends (paho re-queues them on reconnect,
        but here we simply skip the publish call during the drop window).
        """
        try:
            while True:
                now = time.monotonic()
                self._drop_scheduler.update(now)
                if not self._drop_scheduler.is_active(now):
                    self._publish_due(now)
                await asyncio.sleep(0.1)  # 100 ms scheduling granularity
        except asyncio.CancelledError:
            pass
