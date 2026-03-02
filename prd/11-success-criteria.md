# Success Criteria

## 11.1 Protocol Connectivity

CollatrEdge connects to the simulator via all three protocols and collects data continuously for 24 hours with zero configuration changes to CollatrEdge beyond specifying the endpoint addresses.

**Modbus TCP (packaging):** CollatrEdge reads holding registers, input registers, coils, and discrete inputs at configured poll intervals. All register addresses in the map return valid data. Float32 and uint32 values decode correctly with ABCD byte order. Int16 values in input registers decode correctly with x10 scaling.

**Modbus TCP (F&B):** CollatrEdge reads all F&B equipment across the network topology defined in Section 3a.3. Mixer at 10.0.2.10:502 UID 1 uses CDAB byte order (Allen-Bradley convention). Oven gateway at 10.0.2.20:502 serves three zone controllers at UID 1, 2, 3 and an energy meter at UID 10. Filler at 10.0.2.30:502 UID 1, sealer at 10.0.2.31:502 UID 1, chiller at 10.0.2.40:502 UID 1, and CIP at 10.0.2.32:502 UID 1 all use ABCD byte order. All F&B register addresses return valid data.

**OPC-UA:** CollatrEdge browses the node tree and subscribes to all nodes under both `PackagingLine` and `FoodBevLine` trees. All node values update at their configured rates. Status codes are correctly propagated.

**MQTT (packaging):** CollatrEdge subscribes to `collatr/factory/#` and receives JSON messages on all 17 packaging MQTT topics. Payloads parse correctly. QoS 0 and QoS 1 messages are both handled.

**MQTT (F&B):** CollatrEdge subscribes to `collatr/factory/demo/foodbev1/#` and receives JSON messages on all 13 F&B MQTT topics. Payloads parse correctly.

## 11.2 Data Realism

A packaging industry professional (or someone with equivalent domain knowledge) reviews 24 hours of simulator output in a time-series chart and cannot distinguish it from real factory data based on signal shapes, value ranges, noise characteristics, and correlation patterns.

Specific checks (packaging):
- Line speed shows realistic ramp-up profiles, not step functions.
- Dryer temperatures track setpoints with thermal lag, not instant response.
- Web tension fluctuates during speed changes and stabilizes during steady state.
- Counters increment smoothly during running and freeze during idle.
- Energy consumption correlates with machine state.
- Vibration levels increase when the machine is running.
- Environmental temperature follows a daily cycle.
- Ink level depletes over hours, not minutes.

Specific checks (F&B):
- Mixer batch cycles complete in 20-45 minutes with distinct phase transitions.
- Oven zones track setpoints independently with thermal coupling between adjacent zones.
- Fill weight distribution is Gaussian around target with realistic sigma (2-4g).
- CIP wash cycles follow the recipe curve: temperature, flow, and conductivity profiles match expected shapes.
- Chiller room temperature recovers with first-order dynamics after door close events.
- Seal temperature maintains minimum threshold during normal operation.

## 11.3 Anomaly Detection

Each scenario type produces a pattern that is detectable by a basic anomaly detection algorithm (3-sigma, CUSUM, or simple threshold).

| Scenario | Detection Method | Expected Signal |
|----------|-----------------|-----------------|
| Web break | Threshold on web tension | Spike > 600 N followed by drop to 0 |
| Dryer drift | CUSUM on (dryer_temp - setpoint) | Sustained positive deviation over 30+ minutes |
| Bearing wear | Trend on vibration RMS | Linear increase over days/weeks |
| Ink excursion | Threshold on ink viscosity | Value outside 18-45 range |
| Registration drift | Threshold on registration error | Value outside +/-0.3 mm |
| Cold start spike | Threshold on line power | Spike > 150% of running average |
| Shift change | Pattern in machine state | Regular 8-hour gaps in Running state |

## 11.4 Continuous Operation

The simulator runs for 7 consecutive days without:
- Memory leaks (RSS stays within 2x of initial).
- CPU runaway (stays below 20% of one core at 1x speed).
- Protocol server crashes or unhandled exceptions.
- Divergent signal values (no NaN, no infinity, no values outside configured ranges).
- Counter overflows (unless configured for rollover testing).

## 11.5 Time Compression

**Protocol serving mode (up to 10x).** At 10x speed, all signals in the active profile produce values at 10x their configured rate. The protocol servers keep up. CollatrEdge collects data at the compressed rate without gaps. A 24-hour simulation completes in 2.4 real hours.

**Batch generation mode (above 10x).** At 100x and above, protocol adapters are disabled. The engine writes signal data and ground truth to files. Success criterion: the output files contain the correct number of data points for the simulated duration, with no NaN, no infinity, and no values outside configured ranges. A 7-day simulation at 100x completes in under 2 real hours.

## 11.6 Reproducibility

With the same random seed and configuration, two independent runs of the simulator produce byte-identical signal sequences for the first 1 million data points.
