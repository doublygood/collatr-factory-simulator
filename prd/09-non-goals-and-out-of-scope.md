# Non-Goals and Out of Scope

## 9.1 What This Is Not

**Not a replay of actual customer data.** The simulator generates original synthetic data. No rows from the reference database are included. No data from Site A, Site B, or any CIJ vendor customer site is embedded in the simulator or its configuration. The reference data informed the models. The models produce new data.

**Not a digital twin (yet).** A digital twin models a specific physical asset with bidirectional data flow. The simulator models a generic packaging line. It does not represent any specific factory. It does not receive commands from a real control system. Data flows one direction: out. However, the simulation clock architecture and physics-based signal models lay groundwork for evolution toward digital twin and predictive modelling capabilities. See Section 9.4 for the longer-term vision.

**Not intended for production monitoring.** The simulator is a development and testing tool. It does not connect to real equipment. It does not process real production data. It does not generate alerts or reports for factory operators.

**Not a general-purpose protocol simulator.** The simulator serves a specific set of signals for a packaging line. It is not a configurable OPC-UA server for arbitrary data, a Modbus slave emulator for testing register maps, or an MQTT broker for general use. Those tools exist (Microsoft OPC PLC, oitc/modbus-server, Mosquitto).

## 9.2 Phase 2 and Beyond

The following items are explicitly deferred:

**CNC machine cell.** Add spindle speed, spindle load, feed rate, axis positions, and tool wear signals. The CNC datasets from the Round 2 research (Hannover, Bosch, MU-TCM) provide reference patterns. This addresses the automotive and aerospace prospect list (Mettis Aerospace, Sertec, ASG Group).

**Pharma tablet press.** Add compression force, turret speed, tablet weight, and cleanroom environmental monitoring. The Lek Pharmaceuticals tablet compression dataset provides direct reference data. This addresses the pharmaceutical prospect list (Sterling Pharma Solutions, Almac Group).

**Sparkplug B support.** Add protobuf-encoded MQTT payloads in the Sparkplug B namespace. This is a protocol feature, not a factory feature.

**Historical data access.** Add OPC-UA Historical Access (HA) support so clients can query past values. The current design serves only current values via subscriptions and polling.

**Multi-line simulation.** Run two or more packaging lines simultaneously with shared environmental conditions but independent production schedules.

**EtherNet/IP support.** Add Allen-Bradley native CIP protocol. The F&B profile already simulates a Rockwell CompactLogix PLC for the mixer, but accesses it via Modbus TCP. This is how most third-party integrators work in practice. EtherNet/IP (CIP) is the native protocol and would exercise CollatrEdge's native Rockwell driver when that is built.

**MTConnect support.** Add MTConnect agent for CNC machine data. This is relevant for the CNC machine cell phase.

**Web dashboard.** Add a browser-based UI showing real-time signal values, machine state, and scenario status. The health check endpoint provides raw data. A dashboard adds visualization.

## 9.4 Future Direction: Digital Twin and Predictive Modelling

The simulator's architecture has properties that extend beyond test data generation.

The simulation clock decouples simulated time from wall-clock time. Run a factory at 100x or 1000x in batch mode to project months of bearing degradation in minutes. Run it at 1000x to model a year of shift patterns overnight. This is the core capability of a discrete-event simulation engine.

The signal models encode real physics: thermal diffusion, exponential degradation, Ornstein-Uhlenbeck processes, correlated cascades, bang-bang control loops. These are not curve-fitting approximations. They are parametric models that respond to input changes. Change a dryer setpoint and the temperature model produces a physically plausible transient. Change the production schedule and the energy model responds.

The scenario system already models operational decisions: recipe changes, maintenance windows, shift patterns, CIP scheduling. These are the inputs to a "what if" tool.

A post-MVP evolution path:

1. **Predictive maintenance modelling.** Feed the bearing degradation model (Section 5.5) with real vibration baselines from a customer site. Fast-forward to predict when the bearing reaches warning threshold. Compare against actual maintenance schedules to quantify the cost of run-to-failure vs condition-based maintenance.

2. **Schedule optimisation.** Model the impact of changeover frequency on OEE. Run 100 simulated weeks with 4 changeovers per shift vs 6. Quantify the throughput and waste difference.

3. **Site-specific digital twin.** Replace generic signal parameters with values calibrated from a real factory's CollatrEdge data. The simulator becomes a model of that specific site. Run scenarios that have not happened yet: "What if the oven fails during a production run?" "What if we add a second filler?"

4. **Training data generation.** Generate labelled training data for machine learning models. The ground truth log provides exact labels. Generate 10,000 hours of simulated data with known fault patterns. Train anomaly detection models. Validate against real data.

This evolution is not in MVP scope. The MVP is a test tool for CollatrEdge integration. But the architecture does not need to change to support these use cases. The simulation clock, signal models, scenario engine, and ground truth logging all carry forward. The investment in physics-based models pays compound returns.

## 9.3 Items Promoted to Phase 1

**Food and beverage profile.** Originally listed as a Phase 2 item, the F&B profile was promoted to Phase 1 scope. The full F&B layout is defined in `02b-factory-layout-food-and-beverage.md`. It covers a chilled ready meal line with mixer, oven, filler, sealer, chiller, and CIP equipment (65 signals total). Protocol mappings for Modbus, OPC-UA, and MQTT are complete.
