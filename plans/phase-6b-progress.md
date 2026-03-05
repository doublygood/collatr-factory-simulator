# Phase 6b: Robustness — Progress

## Status: IN PROGRESS

## Tasks
- [x] 6b.1: MQTT Publisher Startup Retry and Disconnect Monitoring (Y4)
- [ ] 6b.2: CsvWriter Idempotent Close (Y5)
- [ ] 6b.3: SIGTERM Handler for Graceful Docker Shutdown (Y6)
- [ ] 6b.4: Profile-Aware 0x06 Device Busy Exception (Y7)
- [ ] 6b.5: Wire EvaluationConfig into FactoryConfig (Y8)
- [ ] 6b.6: Validate All Fixes — Full Suite

## Notes

Tasks 6b.1-6b.5 are all independent (no dependencies between them). Task 6b.6 depends on all others.

---

## Task 6b.1: MQTT Publisher Startup Retry (DONE)

**Files changed:**
- `src/factory_simulator/protocols/mqtt_publisher.py`
- `tests/unit/test_protocols/test_mqtt.py`

**What was done:**
1. Added `_on_connect` and `_on_disconnect` methods to `MqttPublisher` with paho v2 signatures `(client, userdata, flags, reason_code, properties)`. Both are registered on `self._client` in `__init__` immediately after the client is assigned.
2. Modified `start()` to retry the initial `connect()` up to 3 times with exponential backoff (delays 1 s, 2 s, 4 s). Logs WARNING on each retry and ERROR if all fail. If all 3 fail, the last exception is re-raised. Paho's `loop_start()` handles mid-run reconnection — no additional logic added.
3. Added 6 new tests: retry succeeds on second attempt, raises after all 3 fail, succeeds on third attempt, callbacks are callable, callbacks are registered on the real paho client.

**Decisions:**
- Callbacks registered in `__init__` (not `_create_client`) so they're always applied regardless of whether the client is injected or created.
- Used `getattr(reason_code, "is_failure", False)` to avoid hard dependency on paho `ReasonCode` type.
- `# type: ignore[assignment]` not needed — mypy accepts the assignment without it.
- Test for callback registration used `_ClientSpy` (plain object) + `==` comparison (bound method equality, not identity, since Python creates new bound method objects on each attribute access).
