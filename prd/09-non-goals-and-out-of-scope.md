# Non-Goals and Out of Scope

## 9.1 What This Is Not

**Not a replay of actual customer data.** The simulator generates original synthetic data. No rows from the reference database are included. No data from Site A, Site B, or any CIJ vendor customer site is embedded in the simulator or its configuration. The reference data informed the models. The models produce new data.

**Not a digital twin.** A digital twin models a specific physical asset with bidirectional data flow. The simulator models a generic packaging line. It does not represent any specific factory. It does not receive commands from a real control system. Data flows one direction: out.

**Not intended for production monitoring.** The simulator is a development and testing tool. It does not connect to real equipment. It does not process real production data. It does not generate alerts or reports for factory operators.

**Not a general-purpose protocol simulator.** The simulator serves a specific set of signals for a packaging line. It is not a configurable OPC-UA server for arbitrary data, a Modbus slave emulator for testing register maps, or an MQTT broker for general use. Those tools exist (Microsoft OPC PLC, oitc/modbus-server, Mosquitto).

## 9.2 Phase 2 and Beyond

The following items are explicitly deferred:

**Food and beverage overlay.** Add oven temperature zones, fill weight signals, cold room monitoring, and CIP cycle simulation. This extends the simulator to address the food manufacturing prospect list (Compleat Food Group, Warburtons, etc.). The research identified a complete data gap for food manufacturing in public datasets. All signals must be synthesized.

**CNC machine cell.** Add spindle speed, spindle load, feed rate, axis positions, and tool wear signals. The CNC datasets from the Round 2 research (Hannover, Bosch, MU-TCM) provide reference patterns. This addresses the automotive and aerospace prospect list (Mettis Aerospace, Sertec, ASG Group).

**Pharma tablet press.** Add compression force, turret speed, tablet weight, and cleanroom environmental monitoring. The Lek Pharmaceuticals tablet compression dataset provides direct reference data. This addresses the pharmaceutical prospect list (Sterling Pharma Solutions, Almac Group).

**Sparkplug B support.** Add protobuf-encoded MQTT payloads in the Sparkplug B namespace. This is a protocol feature, not a factory feature.

**Historical data access.** Add OPC-UA Historical Access (HA) support so clients can query past values. The current design serves only current values via subscriptions and polling.

**Multi-line simulation.** Run two or more packaging lines simultaneously with shared environmental conditions but independent production schedules.

**EtherNet/IP support.** Add Allen-Bradley native protocol. This is relevant for food and beverage sites using Rockwell PLCs. The customer profiles research showed Allen-Bradley CompactLogix using CDAB byte order.

**MTConnect support.** Add MTConnect agent for CNC machine data. This is relevant for the CNC machine cell phase.

**Web dashboard.** Add a browser-based UI showing real-time signal values, machine state, and scenario status. The health check endpoint provides raw data. A dashboard adds visualization.
