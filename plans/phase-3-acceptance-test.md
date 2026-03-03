# Phase 3: Acceptance Test — Both Profiles

**Purpose:** Verify the factory simulator serves correct data across all three protocols for BOTH profiles (packaging 47 signals + F&B 68 signals). Phase 3 gate.

**When:** After Phase 3 implementation is complete.

**How:** Programmatic acceptance test (`scripts/acceptance_test.py`) — runs the simulator engine in-process, starts protocol servers, connects as a client, and verifies everything automatically. No Docker (except Mosquitto for MQTT). No CollatrEdge. No CLI.

---

## What This Test Covers

1. **Both profiles** — packaging (47 signals) and F&B (68 signals)
2. **Engine → Store** — DataEngine populates the SignalStore with all signals
3. **Modbus TCP** — all HR, IR, coils, and DI readable with correct encoding
4. **OPC-UA** — all node paths exist and return values in range
5. **MQTT** — all topics publish with correct JSON payloads (if broker available)
6. **Cross-protocol consistency** — same signal via Modbus HR matches OPC-UA
7. **F&B-specific features** — CDAB encoding, multi-slave UIDs 11-13, per-item filler, BatchId string
8. **Signal ranges** — every signal in the store is within PRD-specified bounds
9. **Ground truth** — GroundTruthLogger records scenario events
10. **Scenarios** — engine ticks trigger scenario state changes

---

## Prerequisites

### Software

```bash
# Python 3.12+ with the simulator installed in dev mode
cd repos/collatr-factory-simulator
pip install -e ".[dev]"

# Verify imports work
python3 -c "from factory_simulator.engine.data_engine import DataEngine; print('OK')"
```

### MQTT Broker (Optional)

MQTT tests are **optional**. If Mosquitto is not running, MQTT tests are skipped with a warning.

```bash
# Start Mosquitto (only needed for MQTT tests)
cd repos/collatr-factory-simulator
docker compose up -d

# Verify
docker compose ps
# mqtt-broker should be "healthy"
```

### No CollatrEdge Required

This test does NOT use CollatrEdge. It connects directly to the simulator's protocol servers using pymodbus, asyncua, and paho-mqtt.

### No Docker Required (except Mosquitto)

The simulator runs as a Python library in-process. No container needed.

---

## Running the Test

### Quick Run (Both Profiles)

```bash
cd repos/collatr-factory-simulator
python3 scripts/acceptance_test.py
```

### Packaging Only

```bash
python3 scripts/acceptance_test.py --profile packaging
```

### F&B Only

```bash
python3 scripts/acceptance_test.py --profile foodbev
```

### More Engine Ticks (for scenario coverage)

```bash
python3 scripts/acceptance_test.py --ticks 100
```

---

## What the Script Does

For each profile (packaging, foodbev):

1. **Loads the factory config** (`config/factory.yaml` or `config/factory-foodbev.yaml`)
2. **Creates DataEngine** + SignalStore + SimulationClock + GroundTruthLogger
3. **Runs N engine ticks** (default 30) to populate all signals
4. **Starts Modbus server** on a unique port (packaging: 15600, F&B: 15610)
5. **Starts OPC-UA server** on OS-assigned port
6. **Starts MQTT publisher** (if broker reachable) and subscribes to all topics
7. **Connects as a client** to each protocol
8. **Verifies:**
   - All signals present in the store and within expected ranges
   - All Modbus HR/IR/coils/DI readable with correct values
   - All OPC-UA nodes accessible and returning values
   - MQTT topics published with correct JSON schema (if broker available)
   - Cross-protocol consistency (Modbus vs OPC-UA for shared signals)
   - F&B-specific: CDAB word-swap, multi-slave UIDs, BatchId string type
   - Ground truth log contains scenario events
9. **Reports** PASS/FAIL per check with a final summary

---

## Pass/Fail Criteria

### Packaging Profile (47 signals)

| Check | Criteria |
|-------|----------|
| Signal presence | All 47 signals in the SignalStore |
| Value ranges | All numeric values within PRD-specified bounds |
| Modbus HR | 10+ key registers readable with correct decoding |
| Modbus IR | 6 Eurotherm-style int16×10 temperature registers |
| OPC-UA nodes | 12+ key PackagingLine nodes return values |
| MQTT topics | ≥8 coder + ≥2 env topics (if broker available) |
| Cross-protocol | energy.line_power Modbus ≈ OPC-UA within float32 precision |
| Ground truth | Log file exists with ≥1 record |

### F&B Profile (68 signals)

| Check | Criteria |
|-------|----------|
| Signal presence | All 68 signals in the SignalStore |
| Value ranges | All numeric values within PRD-specified bounds |
| Modbus HR | All 31 HR entries readable (CDAB for mixer, ABCD for rest) |
| CDAB encoding | mixer CDAB decode ≠ ABCD decode (word-swap verified) |
| Modbus IR | 10+ int16×10 entries on main UID + energy float32 |
| Multi-slave | UIDs 11, 12, 13 each respond with 3 IR entries |
| Modbus coils | Coils 100-102 readable (mixer lid, compressor, defrost) |
| Modbus DI | DI 100 readable (chiller door) |
| OPC-UA nodes | All 19 FoodBevLine nodes return values |
| FoodBevLine tree | Equipment folders: Mixer1, Oven1, Filler1, QC1, CIP1, Energy |
| BatchId | FoodBevLine.Mixer1.BatchId is a Python string |
| Filler weight | FillWeight > 0 (per-item generation active) |
| MQTT topics | ≥8 coder + ≥2 env, foodbev1 prefix, no vibration, no packaging1 |
| Cross-protocol | energy signals Modbus ≈ OPC-UA |
| Ground truth | Log file exists with scenario events |

### Overall

- **PASS:** Zero FAIL results. Warnings are acceptable.
- **FAIL:** Any FAIL result.

---

## Expected Output

```
============================================================
  Collatr Factory Simulator — Phase 3 Acceptance Test
============================================================

>>> Testing profile: packaging
    Config: .../config/factory.yaml
    Modbus port: 15600
    MQTT broker: available
    Running 30 engine ticks...
    Profile packaging complete.

>>> Testing profile: foodbev
    Config: .../config/factory-foodbev.yaml
    Modbus port: 15610
    MQTT broker: available
    Running 30 engine ticks...
    Profile foodbev complete.

============================================================
  FINAL RESULTS
============================================================
  PASS: All 47 signals within expected ranges
  PASS: HR 100 (press.line_speed) = 125.3
  ...
  PASS: CDAB word-swap active: CDAB=450.00, ABCD=12345.67
  ...
  PASS: Multi-slave UID 11 responds (zone PV = 160.0)
  ...

============================================================
  142 passed, 0 failed, 2 warnings
============================================================

  VERDICT: PASS
```

---

## Troubleshooting

### Import errors

```bash
pip install -e ".[dev]"
```

### Modbus connection refused

The script uses unique ports (15600/15610) to avoid conflicts. If something is already on those ports, kill it.

### OPC-UA timeout

asyncua takes 1-2 seconds to start. The script waits 800ms. If your machine is slow, increase the sleep in the script.

### MQTT tests skipped

Start Mosquitto: `docker compose up -d`. If you don't need MQTT tests, that's fine — they're optional.

### CDAB test shows WARN

If mixer.speed is exactly 0 after 30 ticks, the CDAB verification can't distinguish encoding. Increase `--ticks 100` or check that the mixer generator is producing non-zero values.

### Ground truth empty

With only 30 ticks (3 simulated seconds), not all scenarios will fire. This is expected. Use `--ticks 200` for more scenario coverage.

---

## Related Files

| File | Purpose |
|------|---------|
| `scripts/acceptance_test.py` | The acceptance test script |
| `scripts/verify-collection.py` | CollatrEdge collection verifier (both profiles) |
| `configs/collatr-edge-packaging.toml` | CollatrEdge config for packaging (future use) |
| `configs/collatr-edge-foodbev.toml` | CollatrEdge config for F&B (future use) |
| `config/factory.yaml` | Packaging profile config |
| `config/factory-foodbev.yaml` | F&B profile config |
