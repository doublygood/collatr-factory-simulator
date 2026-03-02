# Network Topology

This section defines the controller infrastructure and network layout that the simulator models. Real factories do not have a single PLC serving all signals on one IP address. Each equipment group has its own controller, often from a different vendor, with different protocols, addressing schemes, and communication characteristics. The simulator must replicate this fragmentation to test CollatrEdge under realistic integration loads.

## 3a.1 Design Principle

The simulator exposes multiple independent network endpoints. Each endpoint represents a distinct physical controller. CollatrEdge must discover, connect to, and poll each one separately, then correlate the data into a unified view of the production line.

This is the core integration challenge in manufacturing. A single packaging line might have eight controllers from four vendors. A food and beverage line might have ten. CollatrEdge must handle all of them concurrently, each with its own quirks: byte order, polling rate limits, connection timeouts, and failure modes.

## 3a.2 Packaging Line Controllers

The packaging line has seven independently addressable controllers:

| Controller | Vendor/Model | Protocol | IP:Port | Unit ID | Byte Order | Notes |
|---|---|---|---|---|---|---|
| Press PLC | Siemens S7-1500 | Modbus TCP | 10.0.1.10:502 | 1 | ABCD | Main press control, 27 registers |
| Laminator PLC | Siemens S7-1200 | Modbus TCP | 10.0.1.11:502 | 1 | ABCD | 5 registers |
| Slitter PLC | Siemens S7-1200 | Modbus TCP | 10.0.1.12:502 | 1 | ABCD | 3 registers |
| Press OPC-UA | Siemens S7-1500 | OPC-UA | 10.0.1.10:4840 | n/a | n/a | Same PLC, dual-stack |
| Coder | CIJ Printer | MQTT | broker:1883 | n/a | n/a | Publishes to broker, 11 topics |
| Energy Meter | Schneider PM5560 | Modbus TCP | 10.0.1.20:502 | 5 | ABCD | 2 registers, different unit ID |
| IOLink Master | ifm AL1350 | MQTT | broker:1883 | n/a | n/a | Env sensors, 2 topics |
| Vibration Gateway | SKF Enlight | MQTT | broker:1883 | n/a | n/a | Wireless sensors, 3+1 topics |

### Controller Details

**Press PLC (Siemens S7-1500).** The main press controller runs both a Modbus TCP server and an OPC-UA server on the same hardware. In real Siemens installations, the S7-1500 natively supports OPC-UA via firmware (TIA Portal enables it). Modbus TCP runs through a communication module or CP 1543-1. Both servers are always available. CollatrEdge can poll the same data via either protocol. The OPC-UA server provides richer metadata (engineering units, ranges, state enums) while Modbus provides raw register values.

The press PLC owns the largest register block: line speed, web tension, ink system, dryer zones (actuals and setpoints), production counters, machine state, fault codes, drive parameters, nip pressure, and reel diameters. It also owns the press coils (running, fault, e-stop, web break) and discrete inputs (guard door, material present, cycle complete).

**Dryer Zones.** The three dryer zones use Eurotherm 3504 temperature controllers. In many packaging installations, these are standalone instruments with their own Modbus addresses. However, in this factory the Eurotherm outputs are wired to the S7-1500 analog inputs, and the PLC re-serves them as holding registers. The input registers (FC04) provide the same temperature values in Eurotherm-native int16 x10 format. This dual representation is deliberate. It tests CollatrEdge's ability to handle the same physical signal available through two different register types with different data formats.

**Laminator PLC and Slitter PLC (Siemens S7-1200).** Smaller standalone controllers. Each machine was purchased as a unit with its own PLC. They have no OPC-UA capability (S7-1200 OPC-UA support is limited and often disabled). Modbus TCP only. Each listens on its own IP address with unit ID 1.

**Coder (CIJ Printer CIJ).** The coding and marking printer connects to the factory MQTT broker and publishes telemetry. It does not expose Modbus or OPC-UA. CIJ printers use MQTT natively for IoT connectivity. The coder publishes 11 topics covering operational state, print counts, ink system health, and fault events.

**Energy Meter (Schneider PM5560).** A DIN-rail power meter on the line's main electrical feed. It sits on the same Modbus TCP network as the PLCs but uses a different unit ID (5). In the real world, energy meters often share a Modbus RTU bus (RS-485) behind a serial-to-TCP gateway. The simulator models the TCP-accessible endpoint directly. Two holding registers: instantaneous power and cumulative energy.

**IOLink Master (ifm AL1350).** The environmental sensors (ambient temperature, humidity) connect via IOLink to an ifm master gateway. The gateway publishes JSON readings to the MQTT broker. No Modbus, no OPC-UA. Two topics on slow intervals (60s).

**Vibration Gateway (SKF Enlight).** Wireless vibration sensors on the press main drive report through an SKF gateway that publishes to MQTT. Three per-axis topics at 1s intervals plus an optional batch topic. No wired connection to any PLC.

### Packaging Line Network Diagram

```
                     Factory Floor Network (10.0.1.0/24)
    ┌────────────────────┬─────────────────────┬──────────────────┐
    │                    │                     │                  │
    │                    │                     │                  │
┌───┴───┐          ┌─────┴─────┐         ┌─────┴─────┐     ┌─────┴─────┐
│ Press  │          │ Laminator │         │  Slitter  │     │  Energy   │
│ S7-1500│          │  S7-1200  │         │  S7-1200  │     │  PM5560   │
│.10:502 │          │ .11:502   │         │ .12:502   │     │ .20:502   │
│.10:4840│          │ (Modbus   │         │ (Modbus   │     │ (Modbus   │
│(Modbus │          │  only)    │         │  only)    │     │  UID=5)   │
│+OPC-UA)│          └───────────┘         └───────────┘     └───────────┘
└────────┘
    │
    │  MQTT Broker (10.0.1.100:1883)
    │  ┌─────────────────────────────────────────────────┐
    │  │                                                 │
    ├──┤  ┌─────────┐  ┌──────────┐  ┌────────────────┐  │
    │  │  │ CIJ vendor  │  │ ifm      │  │ SKF Enlight    │  │
    │  │  │ Ax-Ser. │  │ AL1350   │  │ Vibration GW   │  │
    │  │  │ (Coder) │  │ (IOLink) │  │ (Wireless)     │  │
    │  │  │ 11 topic│  │ 2 topics │  │ 3+1 topics     │  │
    │  │  └─────────┘  └──────────┘  └────────────────┘  │
    │  └─────────────────────────────────────────────────┘
    │
┌───┴──────────┐
│ CollatrEdge  │
│ .50          │
│              │
│ 4x Modbus    │
│ 1x OPC-UA   │
│ 1x MQTT sub │
└──────────────┘
```

**CollatrEdge connection count for packaging line: 6** (4 Modbus TCP connections, 1 OPC-UA session, 1 MQTT subscription with 17 topic filters).

## 3a.3 F&B Line Controllers

The food and beverage line has ten independently addressable controllers:

| Controller | Vendor/Model | Protocol | IP:Port | Unit ID | Byte Order | Notes |
|---|---|---|---|---|---|---|
| Mixer PLC | Allen-Bradley CompactLogix | Modbus TCP | 10.0.2.10:502 | 1 | CDAB | Word-swapped, 5 HR |
| Oven Zone 1 | Eurotherm 3504 | Modbus TCP | 10.0.2.20:502 | 1 | ABCD | Standalone controller |
| Oven Zone 2 | Eurotherm 3504 | Modbus TCP | 10.0.2.20:502 | 2 | ABCD | Same gateway, different slave |
| Oven Zone 3 | Eurotherm 3504 | Modbus TCP | 10.0.2.20:502 | 3 | ABCD | Same gateway, different slave |
| Filler PLC | Siemens S7-1200 | Modbus TCP + OPC-UA | 10.0.2.30:502 / :4840 | 1 | ABCD | 1 HR, 7 OPC-UA nodes |
| Sealer PLC | Siemens S7-1200 | Modbus TCP | 10.0.2.31:502 | 1 | ABCD | 6 HR |
| Chiller | Danfoss AK-CC 550 | Modbus TCP | 10.0.2.40:502 | 1 | ABCD | 4 HR, 3 coils, 1 DI |
| CIP Controller | Siemens S7-1200 | Modbus TCP | 10.0.2.32:502 | 1 | ABCD | 4 HR |
| QC Station | Mettler Toledo | OPC-UA | 10.0.2.50:4840 | n/a | n/a | Checkweigher + metal detector |
| Coder | CIJ Printer | MQTT | broker:1883 | n/a | n/a | Same model as packaging, 11 topics |
| Energy Meter | Schneider PM5560 | Modbus TCP | 10.0.2.20:502 | 10 | ABCD | Shared gateway, UID=10 |

### Controller Details

**Mixer PLC (Allen-Bradley CompactLogix).** The significant difference from the packaging line: CDAB byte order. Allen-Bradley PLCs store 32-bit floats with the words swapped relative to Siemens convention. CollatrEdge must detect or be configured for this. A misconfigured byte order produces garbage values. This is one of the most common integration mistakes in manufacturing IT.

The mixer PLC owns speed, torque, batch temperature, batch weight, and elapsed mix time. It also owns the mixer.lid_closed coil (safety interlock). The CompactLogix communicates via Modbus TCP through an embedded TCP/IP module. Native EtherNet/IP is not simulated in this phase.

**Oven Zone Controllers (Eurotherm 3504 x3).** Unlike the packaging line where Eurotherm outputs feed into the PLC, the F&B oven uses standalone Eurotherm controllers. Each zone is a separate Modbus slave behind a single RS-485 to TCP gateway (Moxa NPort or equivalent). All three share the same IP address (10.0.2.20) but use different unit IDs (1, 2, 3). CollatrEdge must poll each slave separately.

Each zone controller serves: actual temperature, setpoint (writable), and status. The Eurotherm native data format is int16 with x10 scaling (so 185.3C is stored as 1853). The input registers serve this native format. The holding registers serve float32 for convenience.

The oven also has belt speed, product core temperature, and zone 2 humidity. These are wired to a small S7-1200 that shares the oven gateway IP but uses a different unit ID. In the simulator, these are served from the same address block for simplicity, but the multi-slave topology is preserved.

**Filler PLC (Siemens S7-1200 with OPC-UA).** The filler is the dual-protocol machine on the F&B line. Hopper level is on Modbus. The higher-value signals (line speed, fill weight, fill target, fill deviation, packs produced, reject count, state) are on OPC-UA only. This split is common in newer Siemens installations where the machine builder exposes some data via the older protocol for backward compatibility and the rest via OPC-UA.

**Sealer PLC (Siemens S7-1200).** Standalone controller for the tray sealer. Six holding registers covering seal temperature, pressure, dwell time, gas mix (CO2 and N2 percentages for MAP packaging), and vacuum level. Modbus only.

**Chiller Controller (Danfoss AK-CC 550).** A dedicated refrigeration controller. Modbus TCP accessible. Four holding registers (room temp, setpoint, suction pressure, discharge pressure), three coils (compressor state, defrost active, lid closed), and one discrete input (door open). The setpoint register is writable. This allows testing CollatrEdge's handling of writable registers (note: CollatrEdge only reads, never writes, but it must still correctly identify writable vs read-only registers).

**CIP Controller (Siemens S7-1200).** Clean-in-place system controller. Four holding registers: wash temperature, flow rate, conductivity, and cycle elapsed time. CIP operates between production batches. Most of the time these registers read zero or last-cycle values. The state machine drives the CIP cycle.

**QC Station (Mettler Toledo).** The checkweigher and metal detector combination. Mettler Toledo X-series machines expose data via OPC-UA natively. Six nodes: actual weight, overweight count, underweight count, metal detect trips, throughput, and reject total. No Modbus interface. CollatrEdge must connect to this as a separate OPC-UA server from the filler.

**Energy Meter.** Same Schneider PM5560 model as the packaging line. Shares the Modbus gateway at 10.0.2.20 with the oven Eurotherm controllers, using unit ID 10.

### F&B Line Network Diagram

```
                     Factory Floor Network (10.0.2.0/24)
    ┌──────────┬──────────┬──────────┬──────────┬──────────┐
    │          │          │          │          │          │
┌───┴───┐ ┌────┴────┐ ┌───┴───┐ ┌────┴────┐ ┌───┴───┐ ┌───┴───┐
│Mixer  │ │Oven GW  │ │Filler │ │ Sealer  │ │Chiller│ │ CIP   │
│A-B CL │ │Moxa     │ │S7-1200│ │ S7-1200 │ │Danfos │ │S7-1200│
│.10:502│ │.20:502  │ │.30:502│ │ .31:502 │ │.40:502│ │.32:502│
│CDAB   │ │UID 1,2,3│ │.30:484│ │         │ │       │ │       │
│       │ │UID 10   │ │(+OPCUA│ │         │ │       │ │       │
│       │ │(+Energy)│ │       │ │         │ │       │ │       │
└───────┘ └─────────┘ └───────┘ └─────────┘ └───────┘ └───────┘
                                     │
                              ┌──────┴──────┐
                              │ QC Station  │
                              │Mettler Tol. │
                              │ .50:4840    │
                              │ (OPC-UA)    │
                              └─────────────┘
    │
    │  MQTT Broker (10.0.2.100:1883)
    │  ┌─────────────────────────────┐
    │  │  ┌─────────┐  ┌──────────┐ │
    │  │  │ CIJ vendor  │  │ ifm      │ │
    │  │  │ Ax-Ser. │  │ AL1350   │ │
    │  │  │ (Coder) │  │ (IOLink) │ │
    │  │  │ 11 topic│  │ 2 topics │ │
    │  │  └─────────┘  └──────────┘ │
    │  └─────────────────────────────┘
    │
┌───┴──────────┐
│ CollatrEdge  │
│ .50          │
│              │
│ 7x Modbus    │
│  (inc. 3     │
│   multi-slave│
│   on .20)    │
│ 2x OPC-UA   │
│ 1x MQTT sub │
└──────────────┘
```

**CollatrEdge connection count for F&B line: 10** (7 Modbus TCP connections including multi-slave polling on the oven gateway, 2 OPC-UA sessions, 1 MQTT subscription with 13 topic filters).

## 3a.4 Simulator Implementation

The simulator does not actually bind dozens of IP addresses. It uses port multiplexing and unit ID routing to simulate the multi-controller topology on a single host.

### Port Mapping

| Simulated Endpoint | Simulator Binding | Routing |
|---|---|---|
| Press PLC Modbus (10.0.1.10:502) | 0.0.0.0:5020 | UID 1, packaging registers |
| Laminator PLC (10.0.1.11:502) | 0.0.0.0:5021 | UID 1, laminator registers |
| Slitter PLC (10.0.1.12:502) | 0.0.0.0:5022 | UID 1, slitter registers |
| Energy Meter (10.0.1.20:502 UID 5) | 0.0.0.0:5020 | UID 5, energy registers |
| Press OPC-UA (10.0.1.10:4840) | 0.0.0.0:4840 | PackagingLine tree |
| Mixer PLC (10.0.2.10:502) | 0.0.0.0:5030 | UID 1, CDAB byte order |
| Oven GW (10.0.2.20:502 UID 1-3) | 0.0.0.0:5031 | UID 1/2/3, Eurotherm |
| Oven GW Energy (10.0.2.20:502 UID 10) | 0.0.0.0:5031 | UID 10, energy registers |
| Filler Modbus (10.0.2.30:502) | 0.0.0.0:5032 | UID 1, filler HR |
| Filler OPC-UA (10.0.2.30:4840) | 0.0.0.0:4841 | FoodBevLine.Filler1 tree |
| Sealer PLC (10.0.2.31:502) | 0.0.0.0:5033 | UID 1, sealer registers |
| Chiller (10.0.2.40:502) | 0.0.0.0:5034 | UID 1, chiller registers |
| CIP PLC (10.0.2.32:502) | 0.0.0.0:5035 | UID 1, CIP registers |
| QC OPC-UA (10.0.2.50:4840) | 0.0.0.0:4842 | FoodBevLine.QC1 tree |
| MQTT Broker | 0.0.0.0:1883 | All MQTT topics, both profiles |

For development convenience, the simulator also supports a "collapsed" mode where all Modbus registers are served from a single port (0.0.0.0:502) with unit ID routing. This matches the current appendix register map layout. The multi-port "realistic" mode is the default for integration testing.

### Collapsed vs Realistic Mode

```yaml
# config/factory.yaml
network:
  mode: "realistic"    # "realistic" or "collapsed"
  
  # Collapsed mode: single port, unit ID routing
  # All registers on 0.0.0.0:502
  # OPC-UA on 0.0.0.0:4840
  # MQTT on 0.0.0.0:1883
  
  # Realistic mode: per-controller ports
  # Each controller on its own port
  # Multiple OPC-UA servers
  # Same MQTT broker for all publishers
```

**Collapsed mode** is simpler to set up and sufficient for protocol correctness testing. Use it when you want to verify that register values decode correctly and OPC-UA subscriptions work.

**Realistic mode** is the integration testing target. Use it when you want to test CollatrEdge's multi-connection management, connection pooling, failure isolation (one controller going down should not affect collection from others), and correlation of data from independent sources.

## 3a.5 Connection Behaviour

Each simulated controller has independent connection behaviour:

**Connection limits.** Real PLCs limit concurrent TCP connections. Siemens S7-1200 allows 3 Modbus TCP connections. S7-1500 allows 16. Eurotherm 3504 allows 1 (or 2 behind a gateway). The simulator enforces these limits per endpoint. If CollatrEdge opens too many connections to a single controller, the simulator rejects the excess. This tests CollatrEdge's connection pooling.

| Controller Type | Max Connections | Response Timeout |
|---|---|---|
| Siemens S7-1500 | 16 | 50ms typical, 200ms max |
| Siemens S7-1200 | 3 | 100ms typical, 500ms max |
| Allen-Bradley CompactLogix | 8 | 75ms typical, 300ms max |
| Eurotherm 3504 (via gateway) | 2 per slave | 150ms typical, 1000ms max |
| Danfoss AK-CC 550 | 2 | 200ms typical, 1000ms max |
| Schneider PM5560 | 4 | 50ms typical, 100ms max |

**Response latency.** Real Modbus devices do not respond instantly. The PLC scan cycle, communication module processing, and network latency all add delay. The simulator injects realistic response times per controller type. Eurotherm controllers behind a serial gateway are noticeably slower than direct TCP connections.

**Poll rate limits.** Polling a Siemens S7-1200 at 100ms intervals while it is also running a control program can cause the communication module to fall behind. The simulator models this: if the client polls faster than the controller's minimum response interval, responses are delayed or dropped. This tests CollatrEdge's adaptive polling logic.

**Connection drops.** Each controller endpoint can independently drop connections. The press PLC might stay connected for days. The oven gateway might drop every few hours (serial gateways are less reliable than direct TCP). The energy meter might drop during high-load periods. Each controller has its own mean time between failures (MTBF) and reconnection delay. For what happens to signal generation during a drop, see Section 4.8 (Signal Behaviour During Controller Connection Drops).

| Controller | Connection MTBF | Reconnection Delay |
|---|---|---|
| Siemens S7-1500 | 72+ hours | 1-3s |
| Siemens S7-1200 | 48+ hours | 2-5s |
| Allen-Bradley CompactLogix | 48+ hours | 2-5s |
| Eurotherm Gateway | 8-24 hours | 5-15s |
| Danfoss Chiller | 24-48 hours | 3-10s |
| Schneider PM5560 | 72+ hours | 1-2s |
| OPC-UA Sessions | 24+ hours | 5-10s |

**OPC-UA session management.** OPC-UA connections have sessions with timeouts. If CollatrEdge does not send a keep-alive within the session timeout, the server closes the session. Subscriptions are lost. CollatrEdge must reconnect and re-subscribe. The simulator enforces standard OPC-UA session management with configurable timeouts.

**Per-controller clock drift.** Each simulated controller has an independent clock offset. In a real factory, each PLC has its own clock. Clock drift between PLCs of 1-5 seconds is common. Between a Siemens PLC and a Eurotherm controller, drift of 10+ seconds is normal. The offset starts at zero and drifts at a configurable rate. The drift is linear (no NTP correction simulated).

```
controller_timestamp = sim_time + initial_offset + drift_rate * elapsed_hours
```

| Controller Type | Initial Offset | Drift Rate | Notes |
|---|---|---|---|
| Siemens S7-1500 | 0-500 ms | 0.1-0.5 s/day | Typically NTP-synced, small drift |
| Siemens S7-1200 | 0-2 s | 0.5-2.0 s/day | Often no NTP, moderate drift |
| Allen-Bradley CompactLogix | 0-1 s | 0.2-1.0 s/day | CIP Sync capable but often not configured |
| Eurotherm 3504 | 0-10 s | 2-10 s/day | Poor internal clock, notorious drifter |
| Danfoss AK-CC 550 | 0-5 s | 1-5 s/day | Refrigeration controller, no time sync |
| Schneider PM5560 | 0-1 s | 0.1-0.5 s/day | Power meter, usually well-synced |

The clock offset affects timestamps in OPC-UA SourceTimestamp and MQTT JSON timestamp fields. Modbus has no timestamps. CollatrEdge timestamps Modbus data on receipt. This tests CollatrEdge's time alignment logic when correlating data from multiple controllers.

The protocol adapters apply the controller's clock offset when generating timestamps. The ground truth event log uses the true simulation time, not the drifted time. This allows post-hoc evaluation of CollatrEdge's time correction.

See Appendix D for clock drift configuration parameters.

## 3a.6 Load Profile Summary

Total concurrent connections CollatrEdge must maintain:

| Metric | Packaging | F&B | Both (future) |
|---|---|---|---|
| Modbus TCP connections | 4 | 7 | 11 |
| OPC-UA sessions | 1 | 2 | 3 |
| MQTT subscriptions | 1 | 1 | 1 (shared broker) |
| Total connections | 6 | 10 | 15 |
| Unique controllers | 7 | 10 | 17 |
| Total signals polled | 47 | 65 | 112 |
| Modbus polls/second (estimated) | 12 | 18 | 30 |
| OPC-UA data changes/second | 8 | 12 | 20 |
| MQTT messages/second | 5 | 3 | 8 |

These numbers represent a single production line. A real factory with 3-5 lines would multiply the connection count proportionally. CollatrEdge's architecture should handle this without degradation.

## 3a.7 Cross-References

- Register addresses per controller: [Appendix A](appendix-a-modbus-register-map.md)
- OPC-UA node assignments per server: [Appendix B](appendix-b-opcua-node-tree.md)
- MQTT topic assignments per publisher: [Appendix C](appendix-c-mqtt-topic-map.md)
- Packaging equipment signals: [Section 2](02-simulated-factory-layout.md)
- F&B equipment signals: [Section 2b](02b-factory-layout-food-and-beverage.md)
- Protocol server configuration: [Section 3](03-protocol-endpoints.md)

## 3a.8 Scan Cycle Artefacts

Every PLC executes its control program in a fixed scan cycle. The cycle reads inputs, runs logic, and writes outputs. Register values update once per scan cycle. Between scans, the register values are stale.

**Scan cycle times by controller type:**

| Controller | Model | Scan Cycle | Notes |
|---|---|---|---|
| Press PLC | Siemens S7-1500 | 10 ms | Fast CPU, short cycle |
| Laminator PLC | Siemens S7-1200 | 20 ms | Smaller CPU, longer cycle |
| Slitter PLC | Siemens S7-1200 | 20 ms | Same hardware as laminator |
| Mixer PLC | Allen-Bradley CompactLogix | 15 ms | Typical for continuous task |
| Oven Zone 1-3 | Eurotherm 3504 | 100 ms | Standalone instrument, slow cycle |
| Filler PLC | Siemens S7-1200 | 20 ms | Standard S7-1200 cycle |
| Sealer PLC | Siemens S7-1200 | 20 ms | Standard S7-1200 cycle |
| Chiller | Danfoss AK-CC 550 | 100 ms | Refrigeration controller |
| CIP Controller | Siemens S7-1200 | 20 ms | Standard S7-1200 cycle |

**Stale reads.** When CollatrEdge polls faster than the scan cycle, consecutive reads return the same value. The PLC has not updated the register yet. This is correct behaviour, not a fault. A Modbus client polling an S7-1200 every 10 ms sees identical values on roughly half its reads. The simulator models this by quantising value updates to the scan cycle boundary. The underlying signal model generates continuously, but the register value snaps to the most recent scan cycle output.

```
register_value = last_scan_output
if sim_time >= next_scan_boundary:
    register_value = current_generated_value
    next_scan_boundary += scan_cycle_time
```

**Phase jitter.** Real scan cycles are not perfectly periodic. Interrupt handling, communication load, and program branching cause small variations. The simulator adds a phase jitter of 0-10% on each scan cycle. A 10 ms scan cycle varies between 10.0 ms and 11.0 ms per cycle. The jitter is drawn from a uniform distribution each cycle.

```
actual_cycle = scan_cycle * (1.0 + uniform(0, jitter_pct))
```

**Inter-signal skew.** Two signals on the same PLC that change at the same logical instant may appear 1-2 scan cycles apart from the client's perspective. The client polls registers sequentially. If the scan boundary falls between two register reads within the same Modbus transaction, the first register reflects the old scan and the second reflects the new scan. This is a real effect in Siemens S7 when reading large register blocks that span the scan boundary.

The simulator models this by assigning each register read within a multi-register request an independent chance of hitting the scan boundary. For signals on different PLCs, there is no phase relationship at all. The scan cycles of different controllers are free-running and unsynchronised.

**Impact on analytics.** Scan cycle artefacts affect sub-second correlation analysis. Two signals that change simultaneously in the physical process appear offset by up to one scan cycle in the collected data. At 100 ms scan cycles (Eurotherm), this offset is significant. At 10 ms scan cycles (S7-1500), it is negligible for most analytics. Detectors that compute cross-correlations at fine time resolution must account for scan cycle quantisation.

See Appendix D for scan cycle configuration parameters.
