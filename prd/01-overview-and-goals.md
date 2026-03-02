# Overview and Goals

## 1.1 What This Is

The Collatr Factory Simulator is a standalone tool that generates synthetic industrial data and serves it over three live protocols: Modbus TCP, OPC-UA, and MQTT. CollatrEdge connects to the simulator the same way it connects to a real factory floor. No code changes. No special modes. The simulator looks and behaves like a packaging and printing factory running real production patterns across multiple time horizons: within-shift variability, shift-to-shift handover effects, daily production cycles, weekly maintenance windows, and seasonal demand fluctuations.

## 1.2 Why It Exists

CollatrEdge needs a test target. A real factory is unavailable for development. Public OPC-UA and Modbus servers exist but serve generic data with no industrial context. We need a data source that produces realistic packaging line signals with proper correlations, anomaly patterns, and protocol-specific quirks.

Three use cases drive this project:

**Integration testing.** Engineers connect CollatrEdge to the simulator and verify that data collection works across all three protocols. The simulator produces known patterns. Tests assert that CollatrEdge captures those patterns correctly. Regression tests run against the simulator in CI.

**Demonstrations.** Sales shows the simulator to prospects. The data looks real. Charts show a flexographic press running production. Anomalies appear on schedule. The prospect sees their factory reflected in the demo. This is more convincing than random sine waves.

**Development.** Engineers building new CollatrEdge features need a data source running on localhost. The simulator starts in one command. It produces 47 signals across three protocols. No cloud dependencies. No VPN to a customer site. No waiting for a real machine to produce interesting data.

**LLM agent demo.** The simulator provides the live data backend for a conversational AI agent that prospects can interact with on the Collatr website. The agent queries factory data, calculates KPIs, identifies anomalies, and answers natural language questions about production performance. Progrow.ai demonstrated that a public chat demo is an effective lead generation tool. Their agent was limited to OEE and downtime analysis with shift-level granularity, generic machine names, and obviously simulated data. Our simulator produces richer data: sub-minute sensor streams, realistic machine identities, correlated anomaly patterns, and multiple protocol sources. This gives the Collatr agent access to raw sensor data for genuine root cause analysis, predictive maintenance insights, energy monitoring, and quality correlations that Progrow's agent cannot perform. The simulator must produce data compelling enough that a prospect interacting with the agent sees their own factory reflected in the demo. See `research/research-progrow-competitor-analysis.md` for the full competitive analysis.

## 1.3 The Reference Data Constraint

We have access to real CIJ vendor printing equipment data in a local reference database. This data comes from two sources:

**Public schema (VisionLog trial).** 14.8 million rows from AX350i continuous inkjet printers, R-Series vision inspection systems, and Balluff IOLink environmental sensors. Two customer sites (Site A and Site B). Ten months of data. 60-second polling intervals. Event-driven vision streams.

**Equipment telemetry.** 28.7 million metric data points from industrial digital presses. Print head temperatures, pneumatic system pressures, ink pump speeds, production counters. Sub-second sampling on some sensors.

This data is reference material only. We study it. We learn the ranges, distributions, correlations, noise characteristics, and anomaly shapes. We then build synthetic generators that produce original data with the same statistical properties.

**No actual proprietary reference data may be included in or distributed with the simulator.** No raw values. No sampled rows. No replay of real timeseries. The generators produce new data every time they run.

What we learn from the reference data:

- AX350i printer line speeds range from 0 to 638 units with binary printing/not-printing states
- R-Series vision inspection shows 85.6% fail rates during idle periods in the reference data. This is not representative of normal operation. Line operators were not adequately trained on R-Series use and the camera was sometimes not pointing at the line. The "read" is triggered by an optical sensor on the line firing the R-Series camera, so misalignment produces no-read failures rather than quality failures. The simulator should model a properly operated vision system with realistic failure rates (low single-digit percent during production, higher during startup and changeover) rather than replicating this pathological dataset
- IOLink BCM0002 sensors report humidity (15-80%), contact temperature (20-40C), vibration RMS (0-50 mm/s), and barometric pressure (990-1030 hPa)
- IOLink BNI0042 static charge sensors report 0-5 kV charged potential
- Digital press print head temperatures cluster at 41-42C with 1C standard deviation
- Pneumatic fill tank levels are the highest-volume signal with cyclic fill/drain patterns
- Ink pump speeds are bimodal: 0 RPM (idle) or 200-500 RPM (active)
- Lung pressure sits at 830-840 mbar during normal operation with 60 mbar standard deviation
- Counter values wrap at specific thresholds (PrintedTotal wraps at 999)
- Temperature sensor Temperatur1 reports 6553.5 (uint16 max / 10) when disconnected, a classic sensor fault pattern
- Main board temperatures are stored in tenths of degrees (divide by 10)
- The customer site had a 6-day duplicate insertion bug producing 190x row duplication
- Camera clock timezones drifted from UTC to BST to US Eastern during the trial

These patterns inform our synthetic generators without including the data itself.

### Additional Reference Data Sources

The reference database is available for direct study when needed. Engineers can connect to the local database instance to query distributions, correlations, and anomaly signatures in the raw data.

Beyond the private reference data, publicly available industrial datasets should be studied and incorporated into the simulator's statistical models. CNC machining datasets have good public coverage. Pharmaceutical tablet press data is moderately available. Packaging/printing and food and beverage sectors have almost zero public datasets, which makes our private reference data and custom simulators especially valuable. Any public factory machine data that helps calibrate realistic signal behaviour should be catalogued and its statistical properties extracted. See `research/research-real-world-industrial-datasets.md` and `research/research-targeted-datasets-round2.md` for existing catalogues.
