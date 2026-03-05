"""Tests for batch output writers (CsvWriter and ParquetWriter).

Validates:
- CSV column order: timestamp, signal_id, value, quality
- Parquet output readable by pyarrow
- Event-driven signals written only on value change
- Buffer flushes at configured size
- Correct row count for a given number of ticks and signals
- NaN and Inf values are never written to output

PRD Reference: Appendix F (Phase 5 — batch output)
"""

from __future__ import annotations

import csv
import math
import pathlib

import pytest

from factory_simulator.config import BatchOutputConfig
from factory_simulator.output.writer import CsvWriter, ParquetWriter
from factory_simulator.store import SignalStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(*signals: tuple[str, float | str, str]) -> SignalStore:
    """Return a SignalStore pre-populated with given (id, value, quality) tuples."""
    store = SignalStore()
    for sig_id, value, quality in signals:
        store.set(sig_id, value, timestamp=1.0, quality=quality)
    return store


def _csv_config(tmp_path: pathlib.Path, **kwargs: object) -> BatchOutputConfig:
    return BatchOutputConfig(format="csv", path=str(tmp_path), **kwargs)  # type: ignore[arg-type]


def _read_csv_rows(path: pathlib.Path) -> list[dict[str, str]]:
    with open(path / "signals.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# ---------------------------------------------------------------------------
# CsvWriter — column order
# ---------------------------------------------------------------------------


class TestCsvColumnOrder:
    def test_header_columns_in_order(self, tmp_path: pathlib.Path) -> None:
        config = _csv_config(tmp_path, buffer_size=1000)
        store = _make_store(("press.line_speed", 120.0, "good"))
        writer = CsvWriter(tmp_path, config)
        writer.write_tick(0.1, store)
        writer.close()

        with open(tmp_path / "signals.csv", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            assert list(reader.fieldnames or []) == [
                "timestamp",
                "signal_id",
                "value",
                "quality",
            ]

    def test_row_values_in_correct_columns(self, tmp_path: pathlib.Path) -> None:
        config = _csv_config(tmp_path, buffer_size=1000)
        store = _make_store(("press.line_speed", 120.5, "good"))
        writer = CsvWriter(tmp_path, config)
        writer.write_tick(10.0, store)
        writer.close()

        rows = _read_csv_rows(tmp_path)
        assert len(rows) == 1
        row = rows[0]
        assert row["timestamp"] == "10.0"
        assert row["signal_id"] == "press.line_speed"
        assert float(row["value"]) == pytest.approx(120.5)
        assert row["quality"] == "good"

    def test_quality_bad_preserved(self, tmp_path: pathlib.Path) -> None:
        config = _csv_config(tmp_path, buffer_size=1000)
        store = _make_store(("s1", 0.0, "bad"))
        writer = CsvWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()

        rows = _read_csv_rows(tmp_path)
        assert rows[0]["quality"] == "bad"

    def test_string_value_preserved(self, tmp_path: pathlib.Path) -> None:
        config = _csv_config(tmp_path, buffer_size=1000)
        store = _make_store(("mixer.batch_id", "B-20240115-001", "good"))
        writer = CsvWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()

        rows = _read_csv_rows(tmp_path)
        assert rows[0]["value"] == "B-20240115-001"


# ---------------------------------------------------------------------------
# CsvWriter — event-driven signals
# ---------------------------------------------------------------------------


class TestCsvEventDriven:
    def test_event_driven_only_on_first_occurrence(
        self, tmp_path: pathlib.Path
    ) -> None:
        config = _csv_config(
            tmp_path,
            buffer_size=1000,
            event_driven_signals=["press.machine_state"],
        )
        writer = CsvWriter(tmp_path, config)

        # Write the same state value three times — only the first should appear.
        for t in [1.0, 2.0, 3.0]:
            store = _make_store(("press.machine_state", "Running", "good"))
            writer.write_tick(t, store)

        writer.close()

        rows = _read_csv_rows(tmp_path)
        state_rows = [r for r in rows if r["signal_id"] == "press.machine_state"]
        assert len(state_rows) == 1
        assert state_rows[0]["value"] == "Running"

    def test_event_driven_written_on_state_change(
        self, tmp_path: pathlib.Path
    ) -> None:
        config = _csv_config(
            tmp_path,
            buffer_size=1000,
            event_driven_signals=["press.machine_state"],
        )
        writer = CsvWriter(tmp_path, config)

        states = ["Idle", "Running", "Running", "Fault", "Fault", "Idle"]
        for t, state in enumerate(states, start=1):
            store = _make_store(("press.machine_state", state, "good"))
            writer.write_tick(float(t), store)

        writer.close()

        rows = _read_csv_rows(tmp_path)
        state_rows = [r for r in rows if r["signal_id"] == "press.machine_state"]
        # Idle→Running→Fault→Idle = 4 distinct transitions
        assert len(state_rows) == 4
        assert [r["value"] for r in state_rows] == [
            "Idle",
            "Running",
            "Fault",
            "Idle",
        ]

    def test_continuous_signals_written_every_tick(
        self, tmp_path: pathlib.Path
    ) -> None:
        config = _csv_config(
            tmp_path,
            buffer_size=1000,
            event_driven_signals=["press.machine_state"],
        )
        writer = CsvWriter(tmp_path, config)

        n_ticks = 5
        for t in range(n_ticks):
            store = SignalStore()
            store.set("press.line_speed", float(t), timestamp=float(t))
            store.set("press.machine_state", "Running", timestamp=float(t))
            writer.write_tick(float(t), store)

        writer.close()

        rows = _read_csv_rows(tmp_path)
        speed_rows = [r for r in rows if r["signal_id"] == "press.line_speed"]
        # Continuous signal: one row per tick
        assert len(speed_rows) == n_ticks


# ---------------------------------------------------------------------------
# CsvWriter — buffer flushing
# ---------------------------------------------------------------------------


class TestCsvBufferFlush:
    def test_buffer_flushes_at_configured_size(self, tmp_path: pathlib.Path) -> None:
        """With buffer_size=5 and 3 signals, 2 ticks (6 rows) triggers a flush."""
        config = _csv_config(tmp_path, buffer_size=5)
        writer = CsvWriter(tmp_path, config)

        store = _make_store(
            ("s1", 1.0, "good"),
            ("s2", 2.0, "good"),
            ("s3", 3.0, "good"),
        )
        # 2 ticks x 3 signals = 6 rows -> crosses buffer_size=5 -> flush triggered
        writer.write_tick(1.0, store)
        writer.write_tick(2.0, store)

        # At this point the buffer should have been flushed; close flushes remainder.
        writer.close()

        rows = _read_csv_rows(tmp_path)
        assert len(rows) == 6

    def test_remaining_buffer_written_on_close(
        self, tmp_path: pathlib.Path
    ) -> None:
        """Rows below buffer_size are flushed by close()."""
        config = _csv_config(tmp_path, buffer_size=1000)
        writer = CsvWriter(tmp_path, config)

        store = _make_store(("s1", 42.0, "good"))
        writer.write_tick(1.0, store)
        writer.close()

        rows = _read_csv_rows(tmp_path)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# CsvWriter — row count
# ---------------------------------------------------------------------------


class TestCsvRowCount:
    def test_row_count_for_n_ticks_and_m_signals(
        self, tmp_path: pathlib.Path
    ) -> None:
        config = _csv_config(tmp_path, buffer_size=10000)
        writer = CsvWriter(tmp_path, config)

        n_signals = 5
        n_ticks = 100
        store = _make_store(*[(f"sig_{i}", float(i), "good") for i in range(n_signals)])

        for t in range(n_ticks):
            writer.write_tick(float(t) * 0.1, store)

        writer.close()

        rows = _read_csv_rows(tmp_path)
        assert len(rows) == n_ticks * n_signals

    def test_empty_store_produces_only_header(self, tmp_path: pathlib.Path) -> None:
        config = _csv_config(tmp_path, buffer_size=1000)
        writer = CsvWriter(tmp_path, config)
        writer.write_tick(1.0, SignalStore())
        writer.close()

        with open(tmp_path / "signals.csv", encoding="utf-8") as f:
            lines = f.readlines()
        # Only the header row
        assert len(lines) == 1


# ---------------------------------------------------------------------------
# CsvWriter — NaN / Inf filtering
# ---------------------------------------------------------------------------


class TestCsvNoNanInf:
    def test_nan_not_written(self, tmp_path: pathlib.Path) -> None:
        config = _csv_config(tmp_path, buffer_size=1000)
        writer = CsvWriter(tmp_path, config)

        store = SignalStore()
        store.set("s_nan", math.nan, timestamp=1.0)
        store.set("s_good", 42.0, timestamp=1.0)
        writer.write_tick(1.0, store)
        writer.close()

        rows = _read_csv_rows(tmp_path)
        signal_ids = {r["signal_id"] for r in rows}
        assert "s_nan" not in signal_ids
        assert "s_good" in signal_ids

    def test_inf_not_written(self, tmp_path: pathlib.Path) -> None:
        config = _csv_config(tmp_path, buffer_size=1000)
        writer = CsvWriter(tmp_path, config)

        store = SignalStore()
        store.set("s_inf", math.inf, timestamp=1.0)
        store.set("s_neginf", -math.inf, timestamp=1.0)
        store.set("s_good", 1.0, timestamp=1.0)
        writer.write_tick(1.0, store)
        writer.close()

        rows = _read_csv_rows(tmp_path)
        signal_ids = {r["signal_id"] for r in rows}
        assert "s_inf" not in signal_ids
        assert "s_neginf" not in signal_ids
        assert "s_good" in signal_ids

    def test_no_nan_in_written_values(self, tmp_path: pathlib.Path) -> None:
        config = _csv_config(tmp_path, buffer_size=1000)
        writer = CsvWriter(tmp_path, config)

        store = _make_store(("s1", 1.0, "good"), ("s2", 2.0, "good"))
        writer.write_tick(1.0, store)
        writer.close()

        rows = _read_csv_rows(tmp_path)
        for row in rows:
            val = float(row["value"])
            assert not math.isnan(val), f"NaN in output for {row['signal_id']}"
            assert not math.isinf(val), f"Inf in output for {row['signal_id']}"


# ---------------------------------------------------------------------------
# CsvWriter — DataEngine integration
# ---------------------------------------------------------------------------


class TestCsvDataEngineIntegration:
    def test_data_engine_calls_write_tick(self, tmp_path: pathlib.Path) -> None:
        """DataEngine.tick() calls BatchWriter.write_tick()."""
        from unittest.mock import MagicMock

        from factory_simulator.config import load_config
        from factory_simulator.engine.data_engine import DataEngine
        from factory_simulator.output.writer import BatchWriter
        from factory_simulator.store import SignalStore

        config = load_config(
            pathlib.Path(__file__).parent.parent.parent / "config" / "factory.yaml"
        )
        store = SignalStore()
        mock_writer: BatchWriter = MagicMock(spec=BatchWriter)
        engine = DataEngine(config, store, batch_writer=mock_writer)

        engine.tick()

        mock_writer.write_tick.assert_called_once()  # type: ignore[attr-defined]
        call_args = mock_writer.write_tick.call_args  # type: ignore[attr-defined]
        # First positional arg is sim_time (float)
        assert isinstance(call_args[0][0], float)
        # Second positional arg is the store
        assert call_args[0][1] is store


# ---------------------------------------------------------------------------
# CsvWriter — idempotent close and write-after-close behaviour
# ---------------------------------------------------------------------------


class TestCsvIdempotentClose:
    def test_double_close_no_exception(self, tmp_path: pathlib.Path) -> None:
        """Calling close() twice must not raise."""
        config = _csv_config(tmp_path, buffer_size=1000)
        store = _make_store(("s1", 1.0, "good"))
        writer = CsvWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()
        writer.close()  # must be a no-op

    def test_write_tick_after_close_raises(self, tmp_path: pathlib.Path) -> None:
        """write_tick() after close() must raise RuntimeError."""
        config = _csv_config(tmp_path, buffer_size=1000)
        store = _make_store(("s1", 1.0, "good"))
        writer = CsvWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()
        with pytest.raises(RuntimeError, match="closed"):
            writer.write_tick(2.0, store)

    def test_double_close_data_intact(self, tmp_path: pathlib.Path) -> None:
        """Data written before first close is preserved after double-close."""
        config = _csv_config(tmp_path, buffer_size=1000)
        store = _make_store(("s1", 42.0, "good"))
        writer = CsvWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()
        writer.close()

        rows = _read_csv_rows(tmp_path)
        assert len(rows) == 1
        assert float(rows[0]["value"]) == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# BatchOutputConfig validation
# ---------------------------------------------------------------------------


class TestBatchOutputConfig:
    def test_defaults(self) -> None:
        config = BatchOutputConfig()
        assert config.format == "none"
        assert config.path == "."
        assert config.buffer_size == 10000
        assert config.event_driven_signals == []

    def test_buffer_size_zero_rejected(self) -> None:
        with pytest.raises(ValueError, match="buffer_size must be positive"):
            BatchOutputConfig(buffer_size=0)

    def test_buffer_size_negative_rejected(self) -> None:
        with pytest.raises(ValueError, match="buffer_size must be positive"):
            BatchOutputConfig(buffer_size=-1)

    def test_format_csv(self) -> None:
        config = BatchOutputConfig(format="csv", path="/tmp/out")
        assert config.format == "csv"

    def test_format_parquet(self) -> None:
        config = BatchOutputConfig(format="parquet", path="/tmp/out")
        assert config.format == "parquet"

    def test_invalid_format_rejected(self) -> None:
        with pytest.raises(ValueError):
            BatchOutputConfig(format="excel")  # type: ignore[arg-type]

    def test_event_driven_signals(self) -> None:
        config = BatchOutputConfig(
            event_driven_signals=["press.machine_state", "press.fault_code"]
        )
        assert "press.machine_state" in config.event_driven_signals


# ---------------------------------------------------------------------------
# ParquetWriter tests (skip if pyarrow not available)
# ---------------------------------------------------------------------------


pyarrow = pytest.importorskip("pyarrow", reason="pyarrow not installed")


class TestParquetWriter:
    def test_parquet_readable_by_pyarrow(self, tmp_path: pathlib.Path) -> None:
        import pyarrow.parquet as pq

        config = BatchOutputConfig(
            format="parquet", path=str(tmp_path), buffer_size=1000
        )
        store = _make_store(
            ("press.line_speed", 120.0, "good"),
            ("press.tension", 200.0, "good"),
        )
        writer = ParquetWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()

        table = pq.read_table(str(tmp_path / "signals.parquet"))
        assert table is not None
        assert table.num_rows == 1

    def test_parquet_has_timestamp_column(self, tmp_path: pathlib.Path) -> None:
        import pyarrow.parquet as pq

        config = BatchOutputConfig(
            format="parquet", path=str(tmp_path), buffer_size=1000
        )
        store = _make_store(("s1", 1.0, "good"))
        writer = ParquetWriter(tmp_path, config)
        writer.write_tick(5.0, store)
        writer.close()

        table = pq.read_table(str(tmp_path / "signals.parquet"))
        assert "timestamp" in table.column_names

    def test_parquet_each_signal_is_a_column(self, tmp_path: pathlib.Path) -> None:
        import pyarrow.parquet as pq

        config = BatchOutputConfig(
            format="parquet", path=str(tmp_path), buffer_size=1000
        )
        store = _make_store(
            ("press.line_speed", 100.0, "good"),
            ("press.tension", 200.0, "good"),
            ("press.temperature", 60.0, "good"),
        )
        writer = ParquetWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()

        table = pq.read_table(str(tmp_path / "signals.parquet"))
        col_names = set(table.column_names)
        assert "press.line_speed" in col_names
        assert "press.tension" in col_names
        assert "press.temperature" in col_names

    def test_parquet_row_count(self, tmp_path: pathlib.Path) -> None:
        import pyarrow.parquet as pq

        config = BatchOutputConfig(
            format="parquet", path=str(tmp_path), buffer_size=1000
        )
        store = _make_store(("s1", 1.0, "good"), ("s2", 2.0, "good"))
        writer = ParquetWriter(tmp_path, config)

        n_ticks = 20
        for t in range(n_ticks):
            store.set("s1", float(t), timestamp=float(t))
            store.set("s2", float(t) * 2, timestamp=float(t))
            writer.write_tick(float(t), store)

        writer.close()

        table = pq.read_table(str(tmp_path / "signals.parquet"))
        # One row per tick in wide format
        assert table.num_rows == n_ticks

    def test_parquet_event_driven_changed_column(
        self, tmp_path: pathlib.Path
    ) -> None:
        import pyarrow.parquet as pq

        config = BatchOutputConfig(
            format="parquet",
            path=str(tmp_path),
            buffer_size=1000,
            event_driven_signals=["press.machine_state"],
        )
        writer = ParquetWriter(tmp_path, config)

        states = ["Idle", "Running", "Running", "Fault"]
        for t, state in enumerate(states, start=1):
            store = _make_store(("press.machine_state", state, "good"))
            writer.write_tick(float(t), store)

        writer.close()

        table = pq.read_table(str(tmp_path / "signals.parquet"))
        col_names = set(table.column_names)
        assert "press.machine_state" in col_names
        assert "press.machine_state_changed" in col_names

        changed_col = table.column("press.machine_state_changed").to_pylist()
        # Idle(new=True), Running(new=True), Running(no change=False), Fault(new=True)
        assert changed_col == [True, True, False, True]

    def test_parquet_no_nan_in_output(self, tmp_path: pathlib.Path) -> None:
        """NaN floats are stored as null (None) in Parquet, not as NaN."""
        import pyarrow.parquet as pq

        config = BatchOutputConfig(
            format="parquet", path=str(tmp_path), buffer_size=1000
        )
        store = SignalStore()
        store.set("s_nan", math.nan, timestamp=1.0)
        store.set("s_good", 42.0, timestamp=1.0)
        writer = ParquetWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()

        table = pq.read_table(str(tmp_path / "signals.parquet"))
        assert "s_nan" in table.column_names
        nan_col = table.column("s_nan").to_pylist()
        # Should be null (None) not NaN
        assert nan_col == [None]

    def test_parquet_buffer_flush(self, tmp_path: pathlib.Path) -> None:
        """With buffer_size=3 and 5 ticks, flush is triggered mid-run."""
        import pyarrow.parquet as pq

        config = BatchOutputConfig(
            format="parquet", path=str(tmp_path), buffer_size=3
        )
        store = _make_store(("s1", 1.0, "good"))
        writer = ParquetWriter(tmp_path, config)

        n_ticks = 5
        for t in range(n_ticks):
            writer.write_tick(float(t), store)

        writer.close()

        table = pq.read_table(str(tmp_path / "signals.parquet"))
        assert table.num_rows == n_ticks

    def test_parquet_no_file_if_no_ticks(self, tmp_path: pathlib.Path) -> None:
        """If write_tick is never called, no Parquet file is created."""
        config = BatchOutputConfig(
            format="parquet", path=str(tmp_path), buffer_size=1000
        )
        writer = ParquetWriter(tmp_path, config)
        writer.close()

        assert not (tmp_path / "signals.parquet").exists()

    def test_double_close_no_exception(self, tmp_path: pathlib.Path) -> None:
        """Calling close() twice must not raise."""
        import pyarrow.parquet as pq

        config = BatchOutputConfig(
            format="parquet", path=str(tmp_path), buffer_size=1000
        )
        store = _make_store(("s1", 1.0, "good"))
        writer = ParquetWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()
        writer.close()  # must be a no-op

        # Data still intact
        table = pq.read_table(str(tmp_path / "signals.parquet"))
        assert table.num_rows == 1

    def test_write_tick_after_close_raises(self, tmp_path: pathlib.Path) -> None:
        """write_tick() after close() must raise RuntimeError."""
        config = BatchOutputConfig(
            format="parquet", path=str(tmp_path), buffer_size=1000
        )
        store = _make_store(("s1", 1.0, "good"))
        writer = ParquetWriter(tmp_path, config)
        writer.write_tick(1.0, store)
        writer.close()
        with pytest.raises(RuntimeError, match="closed"):
            writer.write_tick(2.0, store)
