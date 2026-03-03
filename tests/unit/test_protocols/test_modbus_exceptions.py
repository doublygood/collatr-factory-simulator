"""Unit tests for Modbus exception and partial response injection (PRD 10.6, 10.11).

Tests :class:`ModbusExceptionInjector`, the updated :class:`FactoryDeviceContext`,
and the :class:`ModbusServer` state-transition tracking.

PRD Reference: Section 10.6 (Modbus Exceptions), Section 10.11 (Partial Responses)
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from pymodbus.datastore import ModbusSequentialDataBlock
from pymodbus.pdu.register_message import ExcCodes  # type: ignore[attr-defined]

from factory_simulator.config import PartialModbusResponseConfig, load_config
from factory_simulator.engine.ground_truth import GroundTruthLogger
from factory_simulator.protocols.modbus_server import (
    FactoryDeviceContext,
    ModbusExceptionInjector,
    ModbusServer,
)
from factory_simulator.store import SignalStore

_CONFIG_PATH = Path(__file__).resolve().parents[3] / "config" / "factory.yaml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_partial_cfg(
    enabled: bool = True, probability: float = 1.0
) -> PartialModbusResponseConfig:
    return PartialModbusResponseConfig(enabled=enabled, probability=probability)


def _make_injector(
    exception_prob: float = 0.0,
    partial_enabled: bool = True,
    partial_prob: float = 0.0,
    seed: int = 42,
) -> ModbusExceptionInjector:
    rng = np.random.default_rng(seed)
    partial_cfg = _make_partial_cfg(enabled=partial_enabled, probability=partial_prob)
    return ModbusExceptionInjector(rng, exception_prob, partial_cfg)


def _make_context(
    injector: ModbusExceptionInjector | None = None,
    transition_active: bool = False,
    unit_id: int = 1,
) -> FactoryDeviceContext:
    """Build a FactoryDeviceContext with a small HR data block."""
    hr = ModbusSequentialDataBlock(0, [100 + i for i in range(20)])  # type: ignore[no-untyped-call]
    ir = ModbusSequentialDataBlock(0, [0] * 20)  # type: ignore[no-untyped-call]
    co = ModbusSequentialDataBlock(0, [False] * 8)  # type: ignore[no-untyped-call]
    di = ModbusSequentialDataBlock(0, [False] * 8)  # type: ignore[no-untyped-call]
    return FactoryDeviceContext(
        float32_addresses=set(),
        exception_injector=injector,
        transition_active_fn=(lambda: transition_active) if injector else None,
        unit_id=unit_id,
        hr=hr,
        ir=ir,
        co=co,
        di=di,
    )


# ---------------------------------------------------------------------------
# ModbusExceptionInjector — exception 0x04
# ---------------------------------------------------------------------------


class TestExceptionInjector0x04:
    """check_exception_0x04 fires based on probability."""

    def test_never_fires_at_zero_probability(self) -> None:
        inj = _make_injector(exception_prob=0.0)
        results = [inj.check_exception_0x04() for _ in range(1000)]
        assert not any(results)
        assert inj.exception_0x04_count == 0

    def test_always_fires_at_probability_one(self) -> None:
        inj = _make_injector(exception_prob=1.0)
        assert inj.check_exception_0x04() is True
        assert inj.exception_0x04_count == 1

    def test_count_increments_on_each_hit(self) -> None:
        inj = _make_injector(exception_prob=1.0)
        for _ in range(5):
            inj.check_exception_0x04()
        assert inj.exception_0x04_count == 5

    def test_fires_at_expected_rate(self) -> None:
        """With prob=0.1 and N=1000 draws, count should be ~100 ± generous margin."""
        inj = _make_injector(exception_prob=0.1, seed=0)
        count = sum(inj.check_exception_0x04() for _ in range(1000))
        assert 50 <= count <= 200

    def test_deterministic_same_seed(self) -> None:
        inj_a = _make_injector(exception_prob=0.5, seed=7)
        inj_b = _make_injector(exception_prob=0.5, seed=7)
        seq_a = [inj_a.check_exception_0x04() for _ in range(20)]
        seq_b = [inj_b.check_exception_0x04() for _ in range(20)]
        assert seq_a == seq_b

    def test_different_seeds_give_different_sequences(self) -> None:
        inj_a = _make_injector(exception_prob=0.5, seed=1)
        inj_b = _make_injector(exception_prob=0.5, seed=2)
        seq_a = [inj_a.check_exception_0x04() for _ in range(50)]
        seq_b = [inj_b.check_exception_0x04() for _ in range(50)]
        assert seq_a != seq_b


# ---------------------------------------------------------------------------
# ModbusExceptionInjector — exception 0x06
# ---------------------------------------------------------------------------


class TestExceptionInjector0x06:
    """check_exception_0x06 fires deterministically on transition."""

    def test_fires_when_transition_active(self) -> None:
        inj = _make_injector()
        assert inj.check_exception_0x06(transition_active=True) is True
        assert inj.exception_0x06_count == 1

    def test_no_fire_outside_transition(self) -> None:
        inj = _make_injector()
        assert inj.check_exception_0x06(transition_active=False) is False
        assert inj.exception_0x06_count == 0

    def test_count_increments_per_transition(self) -> None:
        inj = _make_injector()
        for _ in range(3):
            inj.check_exception_0x06(transition_active=True)
        assert inj.exception_0x06_count == 3

    def test_no_rng_draw_for_0x06(self) -> None:
        """0x06 is deterministic — no random draw consumed."""
        rng = np.random.default_rng(99)
        state_before = rng.bit_generator.state
        inj = ModbusExceptionInjector(rng, 0.0, _make_partial_cfg(probability=0.0))
        inj.check_exception_0x06(transition_active=True)
        state_after = rng.bit_generator.state
        assert state_before["state"]["state"] == state_after["state"]["state"]


# ---------------------------------------------------------------------------
# ModbusExceptionInjector — partial responses
# ---------------------------------------------------------------------------


class TestExceptionInjectorPartial:
    """check_partial behaves per PRD 10.11."""

    def test_disabled_config_never_fires(self) -> None:
        inj = _make_injector(partial_enabled=False, partial_prob=1.0)
        results = [inj.check_partial(10) for _ in range(100)]
        assert all(r is None for r in results)
        assert inj.partial_response_count == 0

    def test_single_register_never_partial(self) -> None:
        inj = _make_injector(partial_enabled=True, partial_prob=1.0)
        results = [inj.check_partial(1) for _ in range(50)]
        assert all(r is None for r in results)

    def test_always_fires_at_probability_one(self) -> None:
        inj = _make_injector(partial_enabled=True, partial_prob=1.0)
        result = inj.check_partial(10)
        assert result is not None
        assert inj.partial_response_count == 1

    def test_returned_count_in_range(self) -> None:
        """Truncated count must be in [1, requested - 1]."""
        inj = _make_injector(partial_enabled=True, partial_prob=1.0, seed=0)
        for requested in [2, 5, 10, 50]:
            result = inj.check_partial(requested)
            assert result is not None, f"expected partial for requested={requested}"
            assert 1 <= result < requested, f"count={result} out of range for requested={requested}"

    def test_count_varies_across_draws(self) -> None:
        """With enough draws, the truncated count varies (not fixed)."""
        inj = _make_injector(partial_enabled=True, partial_prob=1.0, seed=42)
        counts = {inj.check_partial(10) for _ in range(30)}
        assert len(counts) > 1

    def test_count_never_fires_at_zero_prob(self) -> None:
        inj = _make_injector(partial_enabled=True, partial_prob=0.0)
        results = [inj.check_partial(5) for _ in range(200)]
        assert all(r is None for r in results)

    def test_deterministic_same_seed(self) -> None:
        inj_a = _make_injector(partial_enabled=True, partial_prob=0.5, seed=7)
        inj_b = _make_injector(partial_enabled=True, partial_prob=0.5, seed=7)
        seq_a = [inj_a.check_partial(8) for _ in range(20)]
        seq_b = [inj_b.check_partial(8) for _ in range(20)]
        assert seq_a == seq_b

    def test_record_partial_stores_event(self) -> None:
        inj = _make_injector()
        inj.record_partial(controller_id=1, address=100, requested=10, returned=4)
        assert len(inj.partial_events) == 1
        ev = inj.partial_events[0]
        assert ev["controller_id"] == 1
        assert ev["start_address"] == 100
        assert ev["requested_count"] == 10
        assert ev["returned_count"] == 4


# ---------------------------------------------------------------------------
# FactoryDeviceContext — exception injection
# ---------------------------------------------------------------------------


class TestFactoryDeviceContextExceptions:
    """FactoryDeviceContext injects exceptions via the injector."""

    def test_no_injector_normal_behavior(self) -> None:
        ctx = _make_context(injector=None)
        result = ctx.getValues(3, 0, 2)
        assert isinstance(result, list)
        assert len(result) == 2

    def test_returns_device_failure_on_0x04(self) -> None:
        inj = _make_injector(exception_prob=1.0)
        ctx = _make_context(injector=inj, transition_active=False)
        result = ctx.getValues(3, 0, 2)
        assert result == ExcCodes.DEVICE_FAILURE

    def test_returns_device_busy_during_transition(self) -> None:
        # prob=0 so 0x04 can't fire; transition makes 0x06 fire first
        inj = _make_injector(exception_prob=0.0)
        ctx = _make_context(injector=inj, transition_active=True)
        result = ctx.getValues(3, 0, 2)
        assert result == ExcCodes.DEVICE_BUSY

    def test_0x06_takes_priority_over_0x04(self) -> None:
        """Transition check is evaluated before random 0x04."""
        inj = _make_injector(exception_prob=1.0)  # both would fire
        ctx = _make_context(injector=inj, transition_active=True)
        result = ctx.getValues(3, 0, 2)
        # 0x06 should be returned (checked first)
        assert result == ExcCodes.DEVICE_BUSY

    def test_partial_response_shorter_than_requested(self) -> None:
        inj = _make_injector(partial_enabled=True, partial_prob=1.0)
        ctx = _make_context(injector=inj, transition_active=False)
        result = ctx.getValues(3, 0, 10)
        assert isinstance(result, list)
        assert 1 <= len(result) < 10

    def test_partial_not_injected_for_single_register(self) -> None:
        inj = _make_injector(partial_enabled=True, partial_prob=1.0)
        ctx = _make_context(injector=inj, transition_active=False)
        result = ctx.getValues(3, 0, 1)
        assert isinstance(result, list)
        assert len(result) == 1

    def test_partial_records_event_in_injector(self) -> None:
        inj = _make_injector(partial_enabled=True, partial_prob=1.0)
        ctx = _make_context(injector=inj, transition_active=False, unit_id=5)
        ctx.getValues(3, 10, 8)
        assert inj.partial_response_count == 1
        assert len(inj.partial_events) == 1
        ev = inj.partial_events[0]
        assert ev["controller_id"] == 5
        assert ev["start_address"] == 10
        assert ev["requested_count"] == 8

    def test_register_limit_still_enforced(self) -> None:
        """The 125-register limit takes precedence over injection."""
        inj = _make_injector(exception_prob=0.0, partial_prob=0.0)
        ctx = _make_context(injector=inj)
        result = ctx.getValues(3, 0, 126)
        assert result == ExcCodes.ILLEGAL_VALUE

    def test_no_injection_on_fc06_write(self) -> None:
        """Exception injection only applies to FC03/FC04, not writes."""
        inj = _make_injector(exception_prob=1.0)
        ctx = _make_context(injector=inj)
        # FC16 write should not be affected by exception injector
        result = ctx.setValues(16, 0, [42])
        # Should succeed (no FC06 rejection since address 0 not in float32_addresses)
        assert result is None

    def test_coil_read_not_injected(self) -> None:
        """FC01 (coil read) is not subject to exception injection."""
        inj = _make_injector(exception_prob=1.0)
        ctx = _make_context(injector=inj)
        result = ctx.getValues(1, 0, 4)
        # Should return values, not ExcCodes.DEVICE_FAILURE
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# ModbusServer — exception injector integration
# ---------------------------------------------------------------------------


class TestModbusServerExceptionInjector:
    """ModbusServer creates and exposes the exception injector."""

    def test_server_creates_exception_injector(self) -> None:
        config = load_config(_CONFIG_PATH)
        server = ModbusServer(config, SignalStore())
        assert isinstance(server.exception_injector, ModbusExceptionInjector)

    def test_exception_rng_parameter_used(self) -> None:
        """Passing exception_rng uses it (same seed → same injector state)."""
        config = load_config(_CONFIG_PATH)
        config.data_quality.exception_probability = 0.5
        config.data_quality.partial_modbus_response.probability = 0.5
        s1 = ModbusServer(config, SignalStore(), exception_rng=np.random.default_rng(42))
        s2 = ModbusServer(config, SignalStore(), exception_rng=np.random.default_rng(42))
        # Both injectors start from the same RNG state
        r1 = s1.exception_injector.check_exception_0x04()
        r2 = s2.exception_injector.check_exception_0x04()
        assert r1 == r2

    def test_transition_not_active_initially(self) -> None:
        config = load_config(_CONFIG_PATH)
        server = ModbusServer(config, SignalStore())
        # Transition timestamp is in the past — window has not opened
        assert not server._is_transition_active()

    def test_transition_detected_after_state_change(self) -> None:
        config = load_config(_CONFIG_PATH)
        store = SignalStore()
        server = ModbusServer(config, store)

        # Set initial state
        store.set("press.machine_state", 2.0, 0.0, "good")  # Running
        server.sync_registers()  # initialises _last_machine_state

        # Change state
        store.set("press.machine_state", 3.0, 1.0, "good")  # Idle
        server.sync_registers()  # should detect transition

        assert server._is_transition_active()

    def test_transition_window_expires(self) -> None:
        config = load_config(_CONFIG_PATH)
        server = ModbusServer(config, SignalStore())
        # Force a transition timestamp far in the past (2 seconds ago)
        server._transition_ts = __import__("time").monotonic() - 2.0
        assert not server._is_transition_active()

    def test_device_context_has_injector(self) -> None:
        config = load_config(_CONFIG_PATH)
        server = ModbusServer(config, SignalStore())
        assert server._device_context._exception_injector is server.exception_injector

    def test_0x04_fires_via_device_context(self) -> None:
        """With exception_probability=1.0, FC03 reads return DEVICE_FAILURE."""
        config = load_config(_CONFIG_PATH)
        config.data_quality.exception_probability = 1.0
        server = ModbusServer(config, SignalStore())
        result = server._device_context.getValues(3, 0, 2)
        assert result == ExcCodes.DEVICE_FAILURE

    def test_partial_fires_via_device_context(self) -> None:
        """With partial probability=1.0, multi-reg FC03 reads are truncated."""
        config = load_config(_CONFIG_PATH)
        config.data_quality.exception_probability = 0.0  # no 0x04 injection
        config.data_quality.partial_modbus_response.probability = 1.0
        server = ModbusServer(config, SignalStore())
        server.sync_registers()
        result = server._device_context.getValues(3, 0, 10)
        assert isinstance(result, list)
        assert 1 <= len(result) < 10


# ---------------------------------------------------------------------------
# GroundTruthLogger — log_partial_modbus_response
# ---------------------------------------------------------------------------


class TestGroundTruthLogPartialResponse:
    """GroundTruthLogger.log_partial_modbus_response records the event."""

    def test_log_partial_writes_jsonl(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        path = tmp_path / "gt.jsonl"
        gt = GroundTruthLogger(path)
        gt.open()
        gt.log_partial_modbus_response(
            sim_time=60.0,
            controller_id="1",
            start_address=100,
            requested_count=10,
            returned_count=4,
        )
        gt.close()

        lines = path.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["event"] == "partial_modbus_response"
        assert record["controller_id"] == "1"
        assert record["start_address"] == 100
        assert record["requested_count"] == 10
        assert record["returned_count"] == 4

    def test_log_partial_no_op_when_not_opened(self) -> None:
        """Logger silently discards events when file is not open."""
        gt = GroundTruthLogger("/tmp/non_existent_gt.jsonl")
        # Should not raise
        gt.log_partial_modbus_response(
            sim_time=0.0,
            controller_id="1",
            start_address=0,
            requested_count=5,
            returned_count=2,
        )
