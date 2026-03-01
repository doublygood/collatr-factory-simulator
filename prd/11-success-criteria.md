# Success Criteria

## 11.1 Protocol Connectivity

CollatrEdge connects to the simulator via all three protocols and collects data continuously for 24 hours with zero configuration changes to CollatrEdge beyond specifying the endpoint addresses.

**Modbus TCP:** CollatrEdge reads holding registers, input registers, coils, and discrete inputs at configured poll intervals. All register addresses in the map return valid data. Float32 and uint32 values decode correctly with ABCD byte order. Int16 values in input registers decode correctly with x10 scaling.

**OPC-UA:** CollatrEdge browses the node tree, subscribes to all nodes under `PackagingLine`, and receives data change notifications. All node values update at their configured rates. Status codes are correctly propagated.

**MQTT:** CollatrEdge subscribes to `collatr/factory/#` and receives JSON messages on all 9 MQTT topics. Payloads parse correctly. QoS 0 and QoS 1 messages are both handled.

## 11.2 Data Realism

A packaging industry professional (or someone with equivalent domain knowledge) reviews 24 hours of simulator output in a time-series chart and cannot distinguish it from real factory data based on signal shapes, value ranges, noise characteristics, and correlation patterns.

Specific checks:
- Line speed shows realistic ramp-up profiles, not step functions.
- Dryer temperatures track setpoints with thermal lag, not instant response.
- Web tension fluctuates during speed changes and stabilizes during steady state.
- Counters increment smoothly during running and freeze during idle.
- Energy consumption correlates with machine state.
- Vibration levels increase when the machine is running.
- Environmental temperature follows a daily cycle.
- Ink level depletes over hours, not minutes.

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

At 100x speed, all 40 signals produce values at 100x their configured rate. The protocol servers can keep up. No data is dropped due to throughput limits. CollatrEdge collects data at the compressed rate.

## 11.6 Reproducibility

With the same random seed and configuration, two independent runs of the simulator produce byte-identical signal sequences for the first 1 million data points.
