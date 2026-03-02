"""Simulation clock for the Collatr Factory Simulator.

Maintains deterministic simulated time independent of wall-clock speed.
At each tick, sim time advances by ``tick_interval_ms * time_scale``
milliseconds.  Signal models use sim_time and dt -- never wall time.

PRD Reference: Section 4.1 (Principles 4-5), Section 4.4 (Time Compression)
CLAUDE.md Rule 6: Simulated Time Invariant
"""

from __future__ import annotations

from datetime import UTC, datetime


class SimulationClock:
    """Deterministic simulation clock.

    Parameters
    ----------
    tick_interval_ms:
        Base tick interval in milliseconds.
    time_scale:
        Multiplier for time compression (1.0 = real-time, 10.0 = 10x).
    start_time:
        Optional ISO-8601 start time string.  If *None*, defaults to
        ``2024-01-15T06:00:00+00:00`` (a Monday morning shift start).
    """

    def __init__(
        self,
        tick_interval_ms: int = 100,
        time_scale: float = 1.0,
        start_time: str | None = None,
    ) -> None:
        if tick_interval_ms <= 0:
            raise ValueError("tick_interval_ms must be positive")
        if time_scale <= 0:
            raise ValueError("time_scale must be positive")

        self._tick_interval_ms: int = tick_interval_ms
        self._time_scale: float = time_scale

        # Parse start_time or use default
        if start_time is not None:
            self._start_dt = datetime.fromisoformat(start_time)
            if self._start_dt.tzinfo is None:
                self._start_dt = self._start_dt.replace(tzinfo=UTC)
        else:
            self._start_dt = datetime(2024, 1, 15, 6, 0, 0, tzinfo=UTC)

        # Sim time in seconds since start
        self._sim_time: float = 0.0
        self._tick_count: int = 0

    # -- Properties -----------------------------------------------------------

    @property
    def tick_interval_ms(self) -> int:
        """Base tick interval in milliseconds."""
        return self._tick_interval_ms

    @property
    def time_scale(self) -> float:
        """Time compression multiplier."""
        return self._time_scale

    @property
    def sim_time(self) -> float:
        """Current simulated time in seconds since start."""
        return self._sim_time

    @property
    def dt(self) -> float:
        """Simulated time delta per tick in seconds.

        This is the value signal models use for their ``dt`` parameter.
        """
        return (self._tick_interval_ms / 1000.0) * self._time_scale

    @property
    def tick_count(self) -> int:
        """Number of ticks since start."""
        return self._tick_count

    # -- Methods --------------------------------------------------------------

    def tick(self) -> float:
        """Advance the clock by one tick.

        Returns the new sim_time in seconds.
        """
        self._sim_time += self.dt
        self._tick_count += 1
        return self._sim_time

    def elapsed_seconds(self) -> float:
        """Simulated elapsed time in seconds since start."""
        return self._sim_time

    def sim_datetime(self) -> datetime:
        """Current simulated wall-clock as a timezone-aware datetime."""
        from datetime import timedelta

        return self._start_dt + timedelta(seconds=self._sim_time)

    def sim_time_iso(self) -> str:
        """Current simulated time as an ISO-8601 string."""
        return self.sim_datetime().isoformat()

    def reset(self) -> None:
        """Reset the clock to zero."""
        self._sim_time = 0.0
        self._tick_count = 0

    @classmethod
    def from_config(cls, config: object) -> SimulationClock:
        """Create a clock from a ``SimulationConfig`` object.

        Accepts any object with ``tick_interval_ms``, ``time_scale``,
        and ``start_time`` attributes (duck-typed to avoid circular imports).
        """
        return cls(
            tick_interval_ms=getattr(config, "tick_interval_ms", 100),
            time_scale=getattr(config, "time_scale", 1.0),
            start_time=getattr(config, "start_time", None),
        )
