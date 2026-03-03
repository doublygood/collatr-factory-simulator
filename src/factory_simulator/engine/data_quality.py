"""Sensor-level data quality injectors (PRD Sections 10.9, 10.10).

Implements two store-level injectors:

* :class:`SensorDisconnectInjector` — the signal jumps to a sentinel value
  and ``quality`` becomes ``"bad"`` for a configured duration.

* :class:`StuckSensorInjector` — the signal freezes at whatever value it held
  when the event started; ``quality`` remains ``"good"`` because the sensor
  believes it is working.

Both classes are designed to run **after** generators write and **before**
protocol servers read (PRD 8.2 ordering).  They use *simulation time* (not
wall-clock) for scheduling — sensor events are tied to the simulated factory
timeline, not the host machine clock.

PRD Reference: Section 10.9 (Sensor Disconnect), Section 10.10 (Stuck Sensor)
CLAUDE.md Rule 13: Reproducible when a seeded RNG is supplied.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from factory_simulator.config import SensorDisconnectConfig, StuckSensorConfig
    from factory_simulator.engine.ground_truth import GroundTruthLogger
    from factory_simulator.store import SignalStore

logger = logging.getLogger(__name__)

_SECONDS_PER_DAY: float = 86_400.0
_SECONDS_PER_WEEK: float = 7.0 * _SECONDS_PER_DAY


# ---------------------------------------------------------------------------
# Sentinel value resolution
# ---------------------------------------------------------------------------


def _sentinel_for_signal(sig_id: str, cfg: SensorDisconnectConfig) -> float:
    """Return the sentinel value for *sig_id*.

    Priority order:
    1. ``per_signal_overrides`` entry — explicit per-signal value.
    2. Name-based type detection: ``"temp"`` → temperature sentinel,
       ``"pressure"`` → pressure sentinel, ``"voltage"`` → voltage sentinel.
    3. Default: 0.0.
    """
    if sig_id in cfg.per_signal_overrides:
        return cfg.per_signal_overrides[sig_id]
    name = sig_id.lower()
    if "temp" in name:
        return cfg.sentinel_defaults.temperature
    if "pressure" in name:
        return cfg.sentinel_defaults.pressure
    if "voltage" in name:
        return cfg.sentinel_defaults.voltage
    return 0.0


# ---------------------------------------------------------------------------
# SensorDisconnectInjector
# ---------------------------------------------------------------------------


class SensorDisconnectInjector:
    """Poisson-scheduled sensor disconnect events per signal (PRD 10.9).

    During an active disconnect the affected signal is written with the
    configured sentinel value and ``quality="bad"``.  After the duration
    expires the injector stops overriding; the next generator write restores
    normal values.

    Scheduling uses Poisson inter-arrival times derived from
    ``cfg.frequency_per_24h_per_signal``.  Each signal has an independent
    schedule: the RNG is shared but draws are interleaved per-signal.

    Parameters
    ----------
    cfg:
        :class:`~factory_simulator.config.SensorDisconnectConfig`.
    signal_ids:
        Signal identifiers to manage.  Only listed signals can disconnect.
    rng:
        Numpy RNG for reproducible scheduling.
    """

    def __init__(
        self,
        cfg: SensorDisconnectConfig,
        signal_ids: list[str],
        rng: np.random.Generator,
    ) -> None:
        self._cfg = cfg
        self._signal_ids = list(signal_ids)
        self._rng = rng

        # Pre-compute sentinel value per signal (done once at construction)
        self._sentinels: dict[str, float] = {
            s: _sentinel_for_signal(s, cfg) for s in signal_ids
        }

        # Per-signal scheduler state (populated on first tick)
        self._next_event: dict[str, float] = {}  # sim_time of next start
        self._event_ends: dict[str, float] = {}  # sim_time of current end
        self._initialized = False

    # -- Internals ---------------------------------------------------------------

    def _schedule_next(self, sig_id: str, after_t: float) -> None:
        """Schedule the next disconnect for *sig_id* after *after_t*."""
        if not self._cfg.enabled:
            self._next_event[sig_id] = float("inf")
            return
        freq_min, freq_max = self._cfg.frequency_per_24h_per_signal
        mean_freq = (freq_min + freq_max) / 2.0
        if mean_freq <= 0.0:
            self._next_event[sig_id] = float("inf")
            return
        mean_interval_s = _SECONDS_PER_DAY / mean_freq
        self._next_event[sig_id] = after_t + float(
            self._rng.exponential(mean_interval_s)
        )

    def _initialize(self, sim_time: float) -> None:
        for sig_id in self._signal_ids:
            self._event_ends[sig_id] = -float("inf")
            self._schedule_next(sig_id, sim_time)
        self._initialized = True

    # -- Public ------------------------------------------------------------------

    def tick(
        self,
        sim_time: float,
        store: SignalStore,
        ground_truth: GroundTruthLogger | None = None,
    ) -> None:
        """Advance injector state and apply active disconnects to *store*.

        Call this AFTER generator writes, BEFORE protocol reads.
        On the first call the scheduler is initialised; the first event will
        not fire until at least one inter-arrival gap has elapsed.
        """
        if not self._initialized:
            self._initialize(sim_time)
            # Fall through — first event can't fire because _next_event > sim_time

        for sig_id in self._signal_ids:
            end_t = self._event_ends[sig_id]
            next_t = self._next_event[sig_id]

            # Start a new disconnect if its scheduled time has passed
            if sim_time >= next_t and sim_time >= end_t:
                dur_min, dur_max = self._cfg.duration_seconds
                duration = float(self._rng.uniform(dur_min, dur_max))
                self._event_ends[sig_id] = sim_time + duration
                self._schedule_next(sig_id, sim_time + duration)
                sentinel = self._sentinels[sig_id]
                logger.debug(
                    "sensor_disconnect start: %s sentinel=%.1f dur=%.1fs t=%.1f",
                    sig_id,
                    sentinel,
                    duration,
                    sim_time,
                )
                if ground_truth is not None:
                    ground_truth.log_sensor_disconnect(sim_time, sig_id, sentinel)

            # Override store value while disconnect is active
            if sim_time < self._event_ends[sig_id]:
                store.set(sig_id, self._sentinels[sig_id], sim_time, "bad")

    def is_active(self, sig_id: str, sim_time: float) -> bool:
        """Return ``True`` if *sig_id* is currently disconnected."""
        return sim_time < self._event_ends.get(sig_id, -float("inf"))

    @property
    def sentinels(self) -> dict[str, float]:
        """Sentinel value per signal (read-only copy)."""
        return dict(self._sentinels)


# ---------------------------------------------------------------------------
# StuckSensorInjector
# ---------------------------------------------------------------------------


class StuckSensorInjector:
    """Poisson-scheduled stuck-sensor (frozen-value) events per signal (PRD 10.10).

    At event start the signal's current store value is captured as the frozen
    value.  For the duration of the event that value is written back every
    tick with ``quality="good"`` — the sensor appears to be working normally.
    After the duration the injector stops overriding; the next generator write
    may produce a step change back to the physical value.

    If the signal is not yet in the store when a stuck event should start
    the event is deferred by re-scheduling from the current sim_time.

    Parameters
    ----------
    cfg:
        :class:`~factory_simulator.config.StuckSensorConfig`.
    signal_ids:
        Signal identifiers to manage.
    rng:
        Numpy RNG for reproducible scheduling.
    """

    def __init__(
        self,
        cfg: StuckSensorConfig,
        signal_ids: list[str],
        rng: np.random.Generator,
    ) -> None:
        self._cfg = cfg
        self._signal_ids = list(signal_ids)
        self._rng = rng

        # Per-signal scheduler state (populated on first tick)
        self._next_event: dict[str, float] = {}
        self._event_ends: dict[str, float] = {}
        self._frozen_value: dict[str, float | str] = {}
        self._frozen_duration: dict[str, float] = {}
        self._initialized = False

    # -- Internals ---------------------------------------------------------------

    def _schedule_next(self, sig_id: str, after_t: float) -> None:
        """Schedule the next stuck event for *sig_id* after *after_t*."""
        if not self._cfg.enabled:
            self._next_event[sig_id] = float("inf")
            return
        freq_min, freq_max = self._cfg.frequency_per_week_per_signal
        mean_freq = (freq_min + freq_max) / 2.0
        if mean_freq <= 0.0:
            self._next_event[sig_id] = float("inf")
            return
        mean_interval_s = _SECONDS_PER_WEEK / mean_freq
        self._next_event[sig_id] = after_t + float(
            self._rng.exponential(mean_interval_s)
        )

    def _initialize(self, sim_time: float) -> None:
        for sig_id in self._signal_ids:
            self._event_ends[sig_id] = -float("inf")
            self._schedule_next(sig_id, sim_time)
        self._initialized = True

    # -- Public ------------------------------------------------------------------

    def tick(
        self,
        sim_time: float,
        store: SignalStore,
        ground_truth: GroundTruthLogger | None = None,
    ) -> None:
        """Advance injector state and freeze stuck-sensor signals in *store*.

        Call this AFTER generator writes, BEFORE protocol reads.
        If the signal is absent from the store when an event is due, the event
        is deferred by re-scheduling from the current sim_time.
        """
        if not self._initialized:
            self._initialize(sim_time)
            # Fall through — first event can't fire because _next_event > sim_time

        for sig_id in self._signal_ids:
            end_t = self._event_ends[sig_id]
            next_t = self._next_event[sig_id]

            # Start a new stuck event if its scheduled time has passed
            if sim_time >= next_t and sim_time >= end_t:
                sv = store.get(sig_id)
                if sv is None:
                    # Signal not in store yet — defer until next opportunity
                    self._schedule_next(sig_id, sim_time)
                    continue

                dur_min, dur_max = self._cfg.duration_seconds
                duration = float(self._rng.uniform(dur_min, dur_max))
                self._event_ends[sig_id] = sim_time + duration
                self._frozen_value[sig_id] = sv.value
                self._frozen_duration[sig_id] = duration
                self._schedule_next(sig_id, sim_time + duration)

                # Ground truth value must be numeric
                gt_val: float = (
                    float(sv.value)
                    if isinstance(sv.value, int | float)
                    else 0.0
                )
                logger.debug(
                    "stuck_sensor start: %s frozen=%.3f dur=%.1fs t=%.1f",
                    sig_id,
                    gt_val,
                    duration,
                    sim_time,
                )
                if ground_truth is not None:
                    ground_truth.log_stuck_sensor(sim_time, sig_id, gt_val, duration)

            # Override store value while stuck
            if sim_time < self._event_ends[sig_id] and sig_id in self._frozen_value:
                store.set(sig_id, self._frozen_value[sig_id], sim_time, "good")

    def is_active(self, sig_id: str, sim_time: float) -> bool:
        """Return ``True`` if *sig_id* is currently stuck."""
        return sim_time < self._event_ends.get(sig_id, -float("inf"))

    def frozen_value_at(self, sig_id: str, sim_time: float) -> float | str | None:
        """Return the frozen value for *sig_id* if currently stuck, else ``None``."""
        if self.is_active(sig_id, sim_time):
            return self._frozen_value.get(sig_id)
        return None
