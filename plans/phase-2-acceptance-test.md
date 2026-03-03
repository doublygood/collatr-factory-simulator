# Phase 2: Acceptance Test Procedure

**Purpose:** Verify the factory simulator serves correct data to CollatrEdge across all three protocols.
**When:** After Phase 2 implementation tasks are complete. Phase 2 gate.
**Where:** Dev machine (Lee's machine). Not CI.
**Who:** Lee, or Claude Code on the dev machine.

## Prerequisites

1. Factory simulator repo checked out and built: `docker compose build`
2. CollatrEdge binary available on the dev machine
3. CollatrEdge config: `configs/collatr-edge-packaging.toml` (in this repo)
4. Verification script: `scripts/verify-collection.py` (in this repo)

## Test Tiers

| Tier | Simulated Duration | Real Duration (at 10x) | Purpose |
|------|-------------------|----------------------|---------|
| Smoke | 10 minutes | 1 minute | Does it connect? Does data flow? |
| Medium | 1 hour | 6 minutes | Do scenarios fire? State transitions? |
| Full | 24 hours | 2.4 hours | PRD exit criterion |

Run Smoke first. If it passes, run Medium. Run Full when you want the sign-off.

## Procedure

### Step 1: Start the Simulator

```bash
cd repos/collatr-factory-simulator

# Start at 10x speed with a fixed seed for reproducibility
SIM_TIME_SCALE=10 SIM_RANDOM_SEED=42 docker compose up -d

# Verify both containers are healthy
docker compose ps
# mqtt-broker should be "healthy"
# factory-simulator should be "healthy"

# Quick sanity check: read a Modbus register
# (requires modbus-cli or pymodbus installed)
python3 -c "
import asyncio
from pymodbus.client import AsyncModbusTcpClient
async def check():
    c = AsyncModbusTcpClient('localhost', port=502)
    await c.connect()
    r = await c.read_holding_registers(210, count=1, slave=1)
    print(f'Machine state: {r.registers[0]}')
    c.close()
asyncio.run(check())
"
```

### Step 2: Start CollatrEdge

```bash
# From the dev machine, not inside Docker
# Adjust the path to your CollatrEdge binary

collatr-edge --config repos/collatr-factory-simulator/configs/collatr-edge-packaging.toml

# Or if running the Bun source directly:
cd repos/collatr-edge
bun run src/main.ts --config ../collatr-factory-simulator/configs/collatr-edge-packaging.toml
```

Watch the logs for:
- "Connected to Modbus TCP at localhost:502"
- "Connected to OPC-UA at opc.tcp://localhost:4840"
- "Connected to MQTT broker at tcp://localhost:1883"
- No persistent errors after initial connection

### Step 3: Let It Run

| Tier | Wait Time |
|------|-----------|
| Smoke | 1 minute |
| Medium | 6 minutes |
| Full | 2 hours 24 minutes |

During the run, you can check the web UI at `http://localhost:8080` to see data flowing.

### Step 4: Stop and Verify

```bash
# Stop CollatrEdge (Ctrl+C or kill)

# Run the verification script
python3 repos/collatr-factory-simulator/scripts/verify-collection.py \
  --data-dir ./data/factory-sim-packaging \
  --tier smoke   # or: medium, full

# Stop the simulator
cd repos/collatr-factory-simulator
docker compose down
```

## What the Verification Script Checks

### Signal Coverage
- All 47 packaging signal IDs present in collected data
- No unexpected signal IDs (simulator shouldn't produce signals outside the profile)
- Modbus, OPC-UA, and MQTT all contributed data points

### Value Ranges
For each signal, values are within PRD-specified min/max bounds:

| Signal | Min | Max | Units |
|--------|-----|-----|-------|
| press.line_speed | 0 | 400 | m/min |
| press.web_tension | 0 | 500 | N |
| press.dryer_temp_zone_* | 20 | 120 | C |
| press.machine_state | 0 | 5 | enum |
| press.main_drive_current | 0 | 200 | A |
| energy.line_power | 0 | 200 | kW |
| vibration.main_drive_* | 0 | 50 | mm/s |
| ... (all 47 signals) | | | |

### Data Continuity
- No gaps longer than 2x the expected sample interval for each signal
- Counters (impression_count, good_count, waste_count) are monotonically non-decreasing within a job
- Cumulative_kwh is monotonically non-decreasing

### Cross-Protocol Consistency
- For signals served on multiple protocols (press.line_speed on Modbus + OPC-UA, press.dryer_temp_zone_1 on Modbus HR + IR + OPC-UA), values agree within float32 precision
- Machine state is consistent: when Modbus HR 210 = 2 (Running), OPC-UA Press1.State = 2

### Scenario Evidence (Medium and Full tiers only)

**Phase 1 scenarios (all tiers above Smoke):**
- At least one state transition occurred (machine_state changed)
- At least one job changeover (speed ramped to 0 and back)
- Counters incremented during Running state
- At 10x speed over 1 simulated hour, expect 1-2 shift changes and 3-6 job changeovers

**Phase 2 scenarios — Medium tier (1 simulated hour at 10x):**
- DryerDrift: likely 1-2 instances (frequency_per_shift [1, 2]); dryer_temp_zone_* deviates from setpoint
- InkExcursion: likely 2-3 instances (frequency_per_shift [2, 3]); ink_viscosity spikes
- RegistrationDrift: likely 1-3 instances (frequency_per_shift [1, 3]); registration_error_x/y drift
- WebBreak: unlikely (frequency_per_week [1, 2]); may not fire in 1 hour
- ColdStart: possible if a shift change causes an idle period followed by restart
- CoderDepletion: 1 monitoring instance active; may not trigger in 1 hour (depends on ink consumption rate)
- MaterialSplice: 1 monitoring instance active; may trigger if unwind_diameter reaches threshold

**Phase 2 scenarios — Full tier (24 simulated hours at 10x):**
- DryerDrift: multiple instances expected (3-6 per day)
- InkExcursion: multiple instances expected (6-9 per day)
- RegistrationDrift: multiple instances expected (3-9 per day)
- WebBreak: at least 1 expected (1-2 per week frequency, 24h is ~14% of a week)
- ColdStart: at least 1 triggered (monitors idle-to-active transitions after shift changes)
- CoderDepletion: at least 1 full depletion-refill cycle expected (~24h cycle)
- MaterialSplice: multiple splice events expected (one per ~3h, so 6-8 in 24h)

### Error Check
- No NaN or Infinity values in any signal
- No Modbus exception responses in CollatrEdge logs (beyond intentional error injection)
- No OPC-UA Bad status codes on active nodes
- No MQTT disconnect/reconnect cycles

## Pass/Fail Criteria

### Smoke (10 simulated minutes)
- PASS: All 47 signals collected, all three protocols connected, values in range, no errors
- FAIL: Any protocol failed to connect, missing signals, values out of range

### Medium (1 simulated hour)
- PASS: Smoke criteria + at least one state transition + counters incremented + cross-protocol consistency
- FAIL: Smoke failures, or no state transitions, or cross-protocol mismatch

### Full (24 simulated hours)
- PASS: Medium criteria + all 10 scenario types (3 Phase 1 + 7 Phase 2) fired at least once + no protocol crashes + memory stable
- FAIL: Medium failures, or protocol server crashed, or memory grew unbounded, or any Phase 2 scenario type missing from ground truth log

## Troubleshooting

**CollatrEdge can't connect to Modbus:**
- Check simulator is running: `docker compose ps`
- Check port is exposed: `docker compose port factory-simulator 502`
- Try direct: `nc -zv localhost 502`

**CollatrEdge can't connect to OPC-UA:**
- OPC-UA server takes 2-5 seconds to start. Wait and retry.
- Check: `docker compose logs factory-simulator | grep OPC`

**CollatrEdge can't connect to MQTT:**
- Check Mosquitto is healthy: `docker compose ps mqtt-broker`
- Try direct: `mosquitto_sub -h localhost -t "collatr/factory/#" -C 1 -W 5`

**Missing signals:**
- Check CollatrEdge config matches simulator profile
- Check the simulator is in packaging mode (default)
- Check signal sample rates: slow signals (60s) need longer collection time

**Values out of range:**
- Check simulator seed: `SIM_RANDOM_SEED=42` for reproducible runs
- Check time scale: `SIM_TIME_SCALE=10` for 10x speed
- Some signals are 0 during Idle/Off states. This is correct.
