# BUG: Modbus FC16 setpoint writes silently discarded

**Severity:** HIGH -- PRD requirement violated, integration test failing
**Found:** Phase 2.1 review
**Root cause:** Unidirectional register sync overwrites client writes

## Summary

FC16 writes to writable setpoint registers (e.g. dryer zone setpoints HR 140-145) are accepted by the Modbus server but immediately overwritten on the next sync cycle (every 50ms). The sync loop only flows Store -> Registers and never checks whether a client has written a new value to a writable register.

This means **setpoint write-back does not work**. The PRD explicitly requires it:

> **PRD 3.1.7 Setpoint write handling.** Client writes to setpoint registers (e.g. dryer zone setpoints, oven zone setpoints) update the signal model's target setpoint. The process variable then tracks the new setpoint via its configured dynamics. If the scenario engine is also driving setpoints, the last writer wins. **This is essential for the LLM agent demo use case** where the agent must change setpoints and observe the effect.

## Impact

- `test_fc16_write_and_readback` fails: writes 92.5 to HR 140-141, reads back 75.0 (the store value)
- The LLM agent demo use case (agent writes setpoints and observes effects) is broken
- The `HoldingRegisterEntry.writable` flag is captured from config but never used

## Reproduction

```python
# Write setpoint via FC16
hi, lo = encode_float32_abcd(92.5)
await client.write_registers(140, [hi, lo])

# Read back (sync loop has overwritten with store value)
result = await client.read_holding_registers(140, count=2)
# FAILS: readback is 75.0 (store value), not 92.5 (written value)
```

## Data flow (broken)

```
1. FC16 writes 92.5 to register block
2. _update_loop fires (50ms)
3. _sync_holding_registers reads store (75.0)
4. Overwrites register with 75.0
5. Client reads back 75.0
```

## Fix

Implement bidirectional sync for writable registers, matching the existing OPC-UA server pattern (`opcua_server.py:_sync_values`):

1. Track last-synced register values for writable entries (`_last_hr_sync`)
2. Before store->register sync, compare current register values with tracked values
3. If register changed (client wrote), propagate register -> store
4. Then proceed with normal store -> register sync (which now writes the client value back)

## How this was missed

The `HoldingRegisterEntry.writable` field was added during register map construction but no code path ever reads it. The integration test `test_fc16_write_and_readback` was written to catch this, but the test itself was allowed through review with a failing status. The Phase 2 review (R3) identified missing auto-scheduling but did not catch the setpoint write-back gap.
