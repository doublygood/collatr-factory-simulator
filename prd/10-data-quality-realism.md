# Data Quality Realism

## 10.1 Why Messy Data Matters

Real industrial data is messy. Sensors drift. Networks drop packets. PLCs restart. Timestamps have timezone bugs. Counters wrap. Duplicate rows appear. The reference data from the public schema demonstrated all of these issues. A simulator that produces clean, perfect data fails to test CollatrEdge's robustness.

The simulator produces intentionally imperfect data. The imperfections are configurable and documented so engineers know what to test for.

## 10.2 Communication Drops

The simulator periodically stops responding on one protocol for a configurable duration.

**Modbus drops:** The server stops responding to requests for 1-10 seconds. The client times out. When the server resumes, the next response contains the current value (not the value at the time of the request). Frequency: configurable, default 1-2 per hour.

**OPC-UA drops:** Node values freeze (stop updating) for 5-30 seconds. The status code changes to `UncertainLastUsableValue`. After the drop, values resume updating and status returns to `Good`. Frequency: configurable, default 1-2 per hour.

**MQTT drops:** The broker stops publishing to specific topics for 5-30 seconds. No messages are queued during the drop (QoS 0 topics). QoS 1 topics (coder state) are delivered when publishing resumes. Frequency: configurable, default 1-2 per hour.

The reference data showed one site agent having extended connectivity issues (97.9% error rate in December 2024 during initial setup). Another site agent had intermittent drops (0-11.6% error rate in typical months). Our simulator models the normal-operations case: brief drops, not extended outages.

## 10.3 Sensor Noise

Every analog signal includes noise. The noise magnitude (sigma) and distribution are configured per signal. Gaussian is the default distribution. Signals with heavy-tailed behaviour use Student-t. Signals with autocorrelated residuals use AR(1). See Section 4.2.11 for distribution definitions.

| Signal | Noise Sigma | Distribution | Rationale |
|--------|-------------|-------------|-----------|
| press.line_speed | 0.5 m/min | Gaussian | Encoder resolution + motor controller jitter |
| press.web_tension | 5.0 N | Gaussian | Load cell noise, typical for web handling |
| press.registration_error_x/y | 0.01 mm | Gaussian | Camera resolution limit |
| press.ink_viscosity | 0.5 s | Gaussian | Measurement method variability |
| press.ink_temperature | 0.2 C | Gaussian | Thermocouple noise |
| press.dryer_temp_zone_* | 0.3 C | AR(1), phi=0.7 | PID control loop autocorrelation |
| press.main_drive_current | 0.5 A | Student-t, df=8 | CT clamp resolution + load spikes |
| press.main_drive_speed | 2.0 RPM | Gaussian | Encoder resolution |
| press.nip_pressure | 0.05 bar | Gaussian | Pressure transducer noise |
| laminator.nip_temp | similar to press | AR(1), phi=0.7 | PID-controlled temperature |
| laminator.* (other) | similar to press | Gaussian | Same sensor types |
| coder.printhead_temp | 0.5 C | AR(1), phi=0.7 | PID-controlled, ref sigma=2.8C |
| coder.ink_pump_speed | 0.5 RPM | Gaussian | Pump motor encoder noise |
| coder.ink_pressure | 60 mbar | Student-t, df=6 | Pneumatic transient outliers |
| coder.ink_viscosity_actual | 0.3 cP | Gaussian | Viscosity sensor measurement noise |
| coder.supply_voltage | 0.1 V | Gaussian | PSU ripple and measurement noise |
| coder.ink_consumption_ml | 0.0 | n/a | Counter, no noise on accumulation |
| env.ambient_temp | 0.1 C | Gaussian | IOLink sensor resolution |
| env.ambient_humidity | 0.5 %RH | Gaussian | IOLink sensor resolution |
| energy.line_power | 0.2 kW | Gaussian | Power meter resolution |
| vibration.main_drive_* | 0.3 mm/s | Student-t, df=5 | Mechanical impulse outliers |

## 10.4 Counter Rollovers

The `press.impression_count`, `press.good_count`, and `energy.cumulative_kwh` counters are stored as uint32 in Modbus registers. At maximum value (4,294,967,295), the counter wraps to 0.

In normal operation at 200 m/min, the impression counter increments at roughly 200 counts per minute (assuming 1 impression per meter). It takes approximately 14,889 days (40.8 years) to wrap a uint32 counter at this rate. Counter wrap is unrealistic at normal speed.

However, the simulator supports a configurable `rollover_value` for testing purposes. Set `rollover_value: 10000` and the counter wraps at 10,000 instead of 4,294,967,295. This lets engineers test CollatrEdge's counter wrap detection in minutes instead of decades.

The reference data showed `FPGA_Head_PrintedTotal` wrapping at 999. This is an unusually low rollover value, likely a per-head counter with limited register width. The simulator's configurable rollover replicates this behaviour.

## 10.5 Duplicate Timestamps

The reference data contained a severe duplicate insertion bug at one customer site: 190x row duplication over 6 days. The simulator replicates a milder version.

At a configurable probability (default: 0.01%), a Modbus read returns the same value with the same internal timestamp as the previous read. This simulates a PLC that has not completed its scan cycle between two consecutive client reads. The value is not stale (it is legitimately the same) but the identical timestamps can confuse naive analytics that assume strictly monotonic timestamps.

For MQTT, the simulator occasionally publishes two messages to the same topic within 1 millisecond (configurable probability, default: 0.005%). This simulates the edge case where a sensor gateway double-publishes.

## 10.6 Modbus Exception Responses

Real Modbus devices return exception responses for various reasons: register not implemented, device busy, slave device failure. The simulator injects exception responses at configurable probability.

| Exception Code | Name | When Injected |
|---------------|------|---------------|
| 0x01 | Illegal Function | Reading coils with FC03 (wrong function code) |
| 0x02 | Illegal Data Address | Reading beyond the implemented register range |
| 0x04 | Slave Device Failure | Random injection at configured probability |
| 0x06 | Slave Device Busy | During machine state transitions |

## 10.7 Timezone Issues

The reference data showed camera timestamps drifting between UTC, BST, and US Eastern timezone during the trial. This is a real problem in manufacturing. Many PLCs and industrial devices do not implement NTP and their clocks drift or are set to incorrect timezones.

The simulator's OPC-UA server timestamps are always in UTC (this is the OPC-UA specification requirement). The MQTT JSON payloads use ISO 8601 UTC timestamps. The Modbus protocol has no timestamps.

To test timezone handling, the MQTT adapter accepts a configuration option `timestamp_offset_hours` (default: 0). Setting this to 1 simulates a device reporting BST timestamps as if they were UTC. Setting it to -5 simulates the camera clock timezone drift issue. CollatrEdge must handle these correctly.

## 10.8 Stale and Missing Values

Some signals occasionally report stale values. The OPC-UA adapter marks these with `UncertainLastUsableValue` status. The MQTT adapter sets the quality field to `"uncertain"`.

Stale values occur when:
- A sensor communication drop prevents a fresh read.
- The PLC scan cycle is slower than the client polling rate (the same value is reported twice).
- A counter stops incrementing during idle periods (legitimately unchanged, not stale, but can look stale to a system that expects change).

The press counters are the primary example. When `press.machine_state` is Idle (3), `press.impression_count`, `press.good_count`, and `press.waste_count` do not increment. They report the same value every second. This is correct behaviour. CollatrEdge must distinguish between "counter is stale" and "counter is not incrementing because the machine is idle."

## 10.9 Sensor Disconnect Events

Real sensors fail. When a thermocouple disconnects from a PLC analog input, the PLC does not report zero. It reports a specific sentinel value determined by the input module configuration. The reference data from the CIJ vendor trial showed `Temperatur1` reporting 6553.5 when the sensor was disconnected. This is the most common pattern in Siemens installations.

Common sentinel patterns:

| Sentinel | Meaning | Source |
|---|---|---|
| 6553.5 | uint16 max / 10 | Siemens AI module, wire break on int16 x10 channel |
| -32768 | int16 min | Some PLCs report negative rail on open circuit |
| 9999.0 | Out-of-range high | Eurotherm convention for sensor break |
| 0.0 | Zero | 4-20 mA transmitter with broken wire (0 mA = open circuit) |
| 3.6 mA equivalent | NAMUR NE 43 low | NAMUR-compliant transmitters signal under-range |

The simulator injects sensor disconnect events at configurable probability and duration. During a disconnect:

1. The affected signal's value jumps to the configured sentinel value.
2. The OPC-UA status code changes to `BadSensorFailure`.
3. The MQTT quality field changes to `"bad"`.
4. The Modbus register holds the sentinel value (no exception, just the wrong number).
5. After the configured duration, the signal resumes normal generation.

Default frequency: 0-1 per 24 hours per signal (configurable). Default duration: 30 seconds to 5 minutes.

Each signal specifies which sentinel value to use. Temperature signals default to 6553.5 (Siemens convention). Pressure signals default to 0.0 (open-circuit 4-20 mA). The sentinel value is configurable per signal to match the PLC hardware in the simulated factory.

The ground truth event log (Section 4.7) records every sensor disconnect with signal name, sentinel value, start time, and duration.

## 10.10 Stuck Sensor (Frozen Value)

A stuck sensor is different from a sensor disconnect (Section 10.9). A disconnected sensor reports a sentinel value. A stuck sensor reports a plausible value that simply stops changing. The value freezes at whatever it was when the sensor stuck. The signal stops responding to state changes, setpoint changes, and noise. It holds one number.

Causes: failed ADC, frozen I/O module, software bug in PLC program, broken feedback loop.

Detection challenge: A frozen value within the normal range does not trigger threshold alarms. The value looks normal in isolation. Only the lack of variation gives it away. Statistical tests detect it: variance drops to zero and autocorrelation goes to 1.0. This tests CollatrEdge's ability to identify signals that should be changing but are not.

The simulator injects stuck sensor events as follows:

1. The engine records the current signal value at injection start.
2. The engine holds that value for the configured duration.
3. The OPC-UA status code remains `Good`. The sensor thinks it is working.
4. The MQTT quality field remains `"good"`.
5. The Modbus register holds the frozen value.
6. After the configured duration, the signal resumes normal generation. There may be a step change as the signal jumps from the frozen value to the current generated value.

Default frequency: 0 to 2 per week per signal (configurable). Default duration: 5 minutes to 4 hours.

The ground truth event log (Section 4.7) records stuck sensor events with the frozen value, start time, and duration.
