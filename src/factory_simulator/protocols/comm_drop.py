"""Communication drop scheduler for protocol adapters (PRD Section 10.2).

Each protocol adapter (Modbus, OPC-UA, MQTT) owns one CommDropScheduler
instance.  Drops are scheduled using Poisson inter-arrival times, with
durations drawn uniformly from the configured range.

Wall-clock time (``time.monotonic()``) is used for scheduling — comm drops
are real network-level events, not simulation-time events.

PRD Reference: Section 10.2 (Communication Drops)
CLAUDE.md Rule 13: reproducible when seeded RNG is supplied.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from factory_simulator.config import CommDropConfig


class CommDropScheduler:
    """Poisson-scheduled communication drop state machine.

    Parameters
    ----------
    cfg:
        :class:`~factory_simulator.config.CommDropConfig` specifying
        ``enabled``, ``frequency_per_hour`` range, and
        ``duration_seconds`` range.
    rng:
        Numpy RNG for reproducible scheduling.  Pass
        ``np.random.default_rng()`` for non-deterministic operation.
    """

    def __init__(self, cfg: CommDropConfig, rng: np.random.Generator) -> None:
        self._cfg = cfg
        self._rng = rng
        self._drop_end: float = -1.0    # wall-clock end of current drop
        self._next_drop: float = float("inf")  # wall-clock start of next drop
        self._initialized: bool = False

    # -- Internal helpers -----------------------------------------------------

    def _schedule_next(self, after_t: float) -> None:
        """Schedule next drop start using Poisson inter-arrival time."""
        if not self._cfg.enabled:
            self._next_drop = float("inf")
            return
        freq_min, freq_max = self._cfg.frequency_per_hour
        mean_freq = (freq_min + freq_max) / 2.0
        mean_interval_s = 3600.0 / max(mean_freq, 1e-9)
        gap = float(self._rng.exponential(mean_interval_s))
        self._next_drop = after_t + gap

    # -- Public interface -----------------------------------------------------

    def update(self, t: float) -> None:
        """Advance scheduler to wall-clock time ``t``.

        Must be called before :meth:`is_active` to keep the state machine
        current.  Idempotent if called multiple times with the same ``t``.

        Parameters
        ----------
        t:
            Current wall-clock time in seconds (``time.monotonic()``).
        """
        if not self._initialized:
            self._initialized = True
            self._schedule_next(t)

        if t >= self._next_drop and t >= self._drop_end:
            # Start a new drop
            dur_min, dur_max = self._cfg.duration_seconds
            duration = float(self._rng.uniform(dur_min, dur_max))
            self._drop_end = t + duration
            self._schedule_next(self._drop_end)

    def is_active(self, t: float) -> bool:
        """Return ``True`` if a comm drop is active at wall-clock time ``t``.

        Parameters
        ----------
        t:
            Current wall-clock time in seconds (``time.monotonic()``).
        """
        return t < self._drop_end

    # -- Introspection (for testing) ------------------------------------------

    @property
    def next_drop_at(self) -> float:
        """Wall-clock time when the next drop will start."""
        return self._next_drop

    @property
    def drop_ends_at(self) -> float:
        """Wall-clock time when the current drop ends (-1 if none active)."""
        return self._drop_end
