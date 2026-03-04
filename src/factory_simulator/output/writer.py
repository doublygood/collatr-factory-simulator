"""Batch output writers for the Collatr Factory Simulator.

Provides CSV (long format) and Parquet (wide format) batch writers for
offline analysis of simulation runs.

CSV format
----------
Long format: one row per signal per tick.
Columns (in order): timestamp, signal_id, value, quality.
Continuous signals are written at every tick. Event-driven signals
(listed in ``BatchOutputConfig.event_driven_signals``) are written only
when their value changes from the previously recorded value.

Parquet format
--------------
Wide format: one row per tick, one column per signal.
Event-driven signals include an additional ``<signal_id>_changed``
boolean column that is True on ticks where the value changed.
Requires ``pyarrow`` (optional dependency).

PRD Reference: Appendix F (Phase 5 — batch output)
"""

from __future__ import annotations

import csv
import io
import math
import pathlib
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory_simulator.config import BatchOutputConfig
    from factory_simulator.store import SignalStore


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BatchWriter(ABC):
    """Abstract base class for batch output writers.

    Implementations buffer tick data in memory and flush to disk
    periodically (at ``config.buffer_size`` rows) and on ``close()``.
    """

    @abstractmethod
    def write_tick(self, sim_time: float, store: SignalStore) -> None:
        """Write all signal values for one simulation tick to the output.

        Parameters
        ----------
        sim_time:
            Simulated time in seconds for this tick.
        store:
            Current signal value store.
        """

    @abstractmethod
    def close(self) -> None:
        """Flush any buffered data and close the output file(s)."""


# ---------------------------------------------------------------------------
# CSV writer
# ---------------------------------------------------------------------------


class CsvWriter(BatchWriter):
    """Writes signals to a CSV file in long format.

    CSV columns (in order): timestamp, signal_id, value, quality.

    Continuous signals are written at every tick. Event-driven signals
    (``config.event_driven_signals``) are written only when their value
    changes from the previously recorded value.

    Values that are NaN or Inf are silently dropped (never written).

    Rows are buffered in memory and flushed to disk when the buffer
    reaches ``config.buffer_size`` rows (default 10,000) or when
    ``close()`` is called.
    """

    _CSV_COLUMNS: tuple[str, ...] = ("timestamp", "signal_id", "value", "quality")

    def __init__(
        self,
        path: pathlib.Path,
        config: BatchOutputConfig,
    ) -> None:
        self._config = config
        self._event_driven: frozenset[str] = frozenset(config.event_driven_signals)
        self._last_event_values: dict[str, float | str] = {}
        self._buffer: list[tuple[float, str, float | str, str]] = []
        self._flush_size: int = config.buffer_size

        path.mkdir(parents=True, exist_ok=True)
        self._file_path = path / "signals.csv"
        # File kept open across calls — context manager not applicable here.
        self._file: io.TextIOWrapper = open(  # noqa: SIM115
            self._file_path, "w", newline="", encoding="utf-8"
        )
        self._csv_writer: Any = csv.writer(self._file)
        self._csv_writer.writerow(self._CSV_COLUMNS)

    def write_tick(self, sim_time: float, store: SignalStore) -> None:
        """Buffer one tick's worth of signal values.

        Flushes the buffer to disk automatically when ``buffer_size`` is
        reached.
        """
        for sv in store.get_all().values():
            # Drop NaN / Inf (Rule: no invalid floats in batch output)
            if isinstance(sv.value, float) and (
                math.isnan(sv.value) or math.isinf(sv.value)
            ):
                continue

            if sv.signal_id in self._event_driven:
                last = self._last_event_values.get(sv.signal_id)
                if last is not None and sv.value == last:
                    continue  # no change — skip this signal for this tick
                self._last_event_values[sv.signal_id] = sv.value

            self._buffer.append((sim_time, sv.signal_id, sv.value, sv.quality))

        if len(self._buffer) >= self._flush_size:
            self._flush()

    def _flush(self) -> None:
        """Write buffered rows to the CSV file and clear the buffer."""
        self._csv_writer.writerows(self._buffer)
        self._file.flush()
        self._buffer.clear()

    def close(self) -> None:
        """Flush any remaining buffered rows and close the output file."""
        if self._buffer:
            self._flush()
        self._file.close()


# ---------------------------------------------------------------------------
# Parquet writer
# ---------------------------------------------------------------------------


class ParquetWriter(BatchWriter):
    """Writes signals to a Parquet file in wide (columnar) format.

    Schema: ``timestamp`` column followed by one column per signal.
    Event-driven signals also have a ``<signal_id>_changed`` boolean column
    that is True on ticks where the value changed from the previous tick.

    NaN and Inf values are stored as null (``None``) in Parquet rather
    than being dropped, preserving row alignment.

    Rows are buffered in memory and flushed to disk when the buffer
    reaches ``config.buffer_size`` rows or when ``close()`` is called.
    Multiple flush calls append to the same Parquet file using
    ``pyarrow.parquet.ParquetWriter``.

    Requires ``pyarrow``::

        pip install pyarrow

    Raises
    ------
    ImportError
        If ``pyarrow`` is not installed when the writer is constructed.
    """

    def __init__(
        self,
        path: pathlib.Path,
        config: BatchOutputConfig,
    ) -> None:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            raise ImportError(
                "pyarrow is required for Parquet output. "
                "Install it with: pip install pyarrow"
            ) from exc

        self._pa: Any = pa
        self._pq: Any = pq
        self._config = config
        self._event_driven: frozenset[str] = frozenset(config.event_driven_signals)
        self._last_event_values: dict[str, float | str] = {}
        self._buffer: list[dict[str, float | str | bool | None]] = []
        self._flush_size: int = config.buffer_size

        path.mkdir(parents=True, exist_ok=True)
        self._file_path = path / "signals.parquet"
        # Opened on first flush (schema determined from first batch).
        self._pq_writer: Any = None

    def write_tick(self, sim_time: float, store: SignalStore) -> None:
        """Buffer one tick's worth of signal values.

        Flushes the buffer to disk automatically when ``buffer_size`` is
        reached.
        """
        row: dict[str, float | str | bool | None] = {"timestamp": sim_time}

        for sv in store.get_all().values():
            if isinstance(sv.value, float) and (
                math.isnan(sv.value) or math.isinf(sv.value)
            ):
                # Store null rather than NaN/Inf to preserve row alignment.
                row[sv.signal_id] = None
            else:
                row[sv.signal_id] = sv.value

            if sv.signal_id in self._event_driven:
                last = self._last_event_values.get(sv.signal_id)
                changed = last is None or sv.value != last
                row[f"{sv.signal_id}_changed"] = changed
                self._last_event_values[sv.signal_id] = sv.value

        self._buffer.append(row)

        if len(self._buffer) >= self._flush_size:
            self._flush()

    def _flush(self) -> None:
        """Write buffered rows to the Parquet file and clear the buffer."""
        if not self._buffer:
            return

        # Collect all column names preserving insertion order from first row.
        all_keys: list[str] = []
        seen: set[str] = set()
        for row in self._buffer:
            for k in row:
                if k not in seen:
                    all_keys.append(k)
                    seen.add(k)

        # Build per-column arrays (fill missing keys with None).
        columns: dict[str, list[float | str | bool | None]] = {k: [] for k in all_keys}
        for row in self._buffer:
            for k in all_keys:
                columns[k].append(row.get(k))

        table = self._pa.table(columns)

        if self._pq_writer is None:
            self._pq_writer = self._pq.ParquetWriter(
                str(self._file_path), schema=table.schema
            )

        self._pq_writer.write_table(table)
        self._buffer.clear()

    def close(self) -> None:
        """Flush any remaining buffered rows and close the Parquet file."""
        self._flush()
        if self._pq_writer is not None:
            self._pq_writer.close()
