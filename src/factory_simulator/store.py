"""Signal value store for the Collatr Factory Simulator.

Central store for current signal values and metadata.  The engine is the
sole writer; protocol adapters are readers.  No locks are needed because
the simulator runs on a single asyncio event loop (Rule 9).

PRD Reference: Section 8.2 (Data Flow), Section 8.3 (Concurrency Model)
CLAUDE.md Rule 9: Single Writer, No Locks
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

# Valid quality flag values per PRD Section 8.4
QUALITY_FLAGS = frozenset({"good", "uncertain", "bad"})


@dataclass(slots=True)
class SignalValue:
    """A single signal's current value and metadata.

    Attributes
    ----------
    signal_id:
        Unique identifier (e.g. ``"press.line_speed"``).
    value:
        Current value -- float for numeric signals, str for string signals
        (e.g. ``mixer.batch_id`` in the F&B profile).
    timestamp:
        Simulated time in seconds when the value was last updated.
    quality:
        Data quality flag: ``"good"``, ``"uncertain"``, or ``"bad"``.
    """

    signal_id: str
    value: float | str
    timestamp: float
    quality: str = "good"


@dataclass
class SignalStore:
    """Central store for all signal values.

    Provides dict-like access to :class:`SignalValue` entries keyed by
    signal_id.  Designed for single-writer (engine), multiple-reader
    (protocol adapters) access within a single asyncio event loop.
    """

    _signals: dict[str, SignalValue] = field(default_factory=dict, repr=False)

    # -- Write interface (engine only) ----------------------------------------

    def set(
        self,
        signal_id: str,
        value: float | str,
        timestamp: float,
        quality: str = "good",
    ) -> None:
        """Create or update a signal value.

        Parameters
        ----------
        signal_id:
            Unique signal identifier.
        value:
            Numeric or string value.
        timestamp:
            Simulated time in seconds.
        quality:
            Data quality flag (``"good"``, ``"uncertain"``, ``"bad"``).

        Raises
        ------
        ValueError
            If *quality* is not a recognised flag.
        """
        if quality not in QUALITY_FLAGS:
            raise ValueError(
                f"quality must be one of {sorted(QUALITY_FLAGS)}, got {quality!r}"
            )

        existing = self._signals.get(signal_id)
        if existing is not None:
            existing.value = value
            existing.timestamp = timestamp
            existing.quality = quality
        else:
            self._signals[signal_id] = SignalValue(
                signal_id=signal_id,
                value=value,
                timestamp=timestamp,
                quality=quality,
            )

    # -- Read interface (protocol adapters) -----------------------------------

    def get(self, signal_id: str) -> SignalValue | None:
        """Return the current :class:`SignalValue` for *signal_id*, or *None*."""
        return self._signals.get(signal_id)

    def get_value(self, signal_id: str, default: float | str = 0.0) -> float | str:
        """Return just the value for *signal_id*, or *default* if absent."""
        entry = self._signals.get(signal_id)
        return entry.value if entry is not None else default

    def get_all(self) -> dict[str, SignalValue]:
        """Return a snapshot dict of all signal values.

        Protocol adapters use this for bulk reads.  Returns the internal
        dict directly (no copy) for performance -- callers must not mutate.
        """
        return self._signals

    def signal_ids(self) -> list[str]:
        """Return a sorted list of all registered signal IDs."""
        return sorted(self._signals.keys())

    # -- Convenience ----------------------------------------------------------

    def __len__(self) -> int:
        return len(self._signals)

    def __contains__(self, signal_id: str) -> bool:
        return signal_id in self._signals

    def __iter__(self) -> Iterator[str]:
        return iter(self._signals)

    def clear(self) -> None:
        """Remove all signal values."""
        self._signals.clear()
