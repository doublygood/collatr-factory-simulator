"""String Generator signal model.

Produces formatted identifier strings (not numeric values).  Used for
batch IDs in the F&B profile.

The string is assembled from a template with dynamic components:

    batch_id = template.format(
        date=sim_date,
        line=line_id,
        seq=batch_sequence_number,
    )

Default template: ``"{date:%y%m%d}-{line}-{seq:03d}"``
Example output: ``"260302-L1-007"`` (2 March 2026, Line 1, batch 7).

The sequence number increments each time a new batch starts (via
:meth:`new_batch`).  The sequence resets to 1 at each simulated
midnight (based on ``reset_at`` time of day).

This model does **not** extend :class:`SignalModel` because it
produces ``str`` values, not ``float``.  The equipment generator
handles it separately from numeric signal models.

PRD Reference: Section 4.2.14
CLAUDE.md Rule 6: uses sim_time, never wall clock.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta


class StringGeneratorModel:
    """Formatted string identifier generator.

    Parameters
    ----------
    template:
        Python format string with ``{date}``, ``{line}``, and ``{seq}``
        placeholders.  Default: ``"{date:%y%m%d}-{line}-{seq:03d}"``.
    line_id:
        Line identifier string (default ``"L1"``).
    reset_at:
        Time of day (``"HH:MM"``) to reset the sequence counter
        (default ``"00:00"``).
    start_time:
        Simulation start datetime.  Used to track midnight crossings.
        If *None*, defaults to ``2024-01-15T06:00:00+00:00``.
    """

    def __init__(
        self,
        *,
        template: str = "{date:%y%m%d}-{line}-{seq:03d}",
        line_id: str = "L1",
        reset_at: str = "00:00",
        start_time: str | datetime | None = None,
    ) -> None:
        self._template = template
        self._line_id = line_id

        # Parse reset time
        parts = reset_at.split(":")
        if len(parts) != 2:
            raise ValueError(f"reset_at must be 'HH:MM', got '{reset_at}'")
        self._reset_hour = int(parts[0])
        self._reset_minute = int(parts[1])
        self._reset_time = time(self._reset_hour, self._reset_minute)

        # Parse start time
        if isinstance(start_time, datetime):
            self._start_dt = start_time
        elif isinstance(start_time, str):
            self._start_dt = datetime.fromisoformat(start_time)
        else:
            self._start_dt = datetime(2024, 1, 15, 6, 0, 0, tzinfo=UTC)

        if self._start_dt.tzinfo is None:
            self._start_dt = self._start_dt.replace(tzinfo=UTC)

        # Sequence state
        self._sequence: int = 0
        self._current_value: str = ""

        # Initialize the last-seen reset boundary based on the start time.
        # This ensures that crossing the reset boundary after start_time
        # is correctly detected as a new day.
        self._last_reset_date: datetime = self._compute_reset_boundary(
            self._start_dt
        )

    # -- Properties -----------------------------------------------------------

    @property
    def template(self) -> str:
        """Format template string."""
        return self._template

    @property
    def line_id(self) -> str:
        """Line identifier."""
        return self._line_id

    @property
    def sequence(self) -> int:
        """Current batch sequence number."""
        return self._sequence

    @property
    def value(self) -> str:
        """Current formatted string value."""
        return self._current_value

    # -- Methods --------------------------------------------------------------

    def new_batch(self) -> None:
        """Signal that a new batch has started.

        Increments the sequence counter.  Called by the equipment
        generator when the mixer state machine transitions to a new
        batch.
        """
        self._sequence += 1

    def generate(self, sim_time: float, dt: float) -> str:
        """Produce the current batch ID string.

        Checks for midnight crossing and resets the sequence if needed,
        then formats the template with current values.

        Parameters
        ----------
        sim_time:
            Current simulated time in seconds since start.
        dt:
            Simulated time delta for this tick in seconds (unused
            but kept for interface consistency).

        Returns
        -------
        str
            The formatted batch identifier string.
        """
        sim_dt = self._start_dt + timedelta(seconds=sim_time)

        # Check if we need to reset the sequence at the reset time
        self._check_reset(sim_dt)

        # Format the string
        self._current_value = self._template.format(
            date=sim_dt,
            line=self._line_id,
            seq=self._sequence,
        )
        return self._current_value

    def _compute_reset_boundary(self, dt: datetime) -> datetime:
        """Compute the most recent reset boundary at or before *dt*."""
        reset_dt = dt.replace(
            hour=self._reset_hour,
            minute=self._reset_minute,
            second=0,
            microsecond=0,
        )
        if dt.time() < self._reset_time:
            reset_dt -= timedelta(days=1)
        return reset_dt

    def _check_reset(self, sim_dt: datetime) -> None:
        """Reset sequence counter at the configured time of day.

        Resets once per day when the simulated time crosses the
        ``reset_at`` boundary.
        """
        reset_dt = self._compute_reset_boundary(sim_dt)

        if reset_dt > self._last_reset_date:
            self._sequence = 0
            self._last_reset_date = reset_dt

    def reset(self) -> None:
        """Reset sequence counter and value to initial state."""
        self._sequence = 0
        self._last_reset_date = self._compute_reset_boundary(self._start_dt)
        self._current_value = ""
