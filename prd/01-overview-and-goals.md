# Overview and Goals

## 1.1 What This Is

The Collatr Factory Simulator is a standalone tool that generates synthetic industrial data and serves it over three live protocols: Modbus TCP, OPC-UA, and MQTT. CollatrEdge connects to the simulator the same way it connects to a real factory floor. No code changes. No special modes. The simulator looks and behaves like a packaging and printing factory running a production shift.

## 1.2 Why It Exists

CollatrEdge needs a test target. A real factory is unavailable for development. Public OPC-UA and Modbus servers exist but serve generic data with no industrial context. We need a data source that produces realistic packaging line signals with proper correlations, anomaly patterns, and protocol-specific quirks.

Three use cases drive this project:

**Integration testing.** Engineers connect CollatrEdge to the simulator and verify that data collection works across all three protocols. The simulator produces known patterns. Tests assert that CollatrEdge captures those patterns correctly. Regression tests run against the simulator in CI.

**Demonstrations.** Sales shows the simulator to prospects. The data looks real. Charts show a flexographic press running production. Anomalies appear on schedule. The prospect sees their factory reflected in the demo. This is more convincing than random sine waves.

**Development.** Engineers building new CollatrEdge features need a data source running on localhost. The simulator starts in one command. It produces 40 signals across three protocols. No cloud dependencies. No VPN to a customer site. No waiting for a real machine to produce interesting data.

## 1.3 The Reference Data Constraint

We have access to real CIJ vendor printing equipment data in a local reference database. This data comes from two sources:

**Public schema (VisionLog trial).** 14.8 million rows from AX350i continuous inkjet printers, R-Series vision inspection systems, and Balluff IOLink environmental sensors. Two customer sites (Site A and Site B). Ten months of data. 60-second polling intervals. Event-driven vision streams.

**Equipment telemetry.** 28.7 million metric data points from industrial digital presses. Print head temperatures, pneumatic system pressures, ink pump speeds, production counters. Sub-second sampling on some sensors.

This data is reference material only. We study it. We learn the ranges, distributions, correlations, noise characteristics, and anomaly shapes. We then build synthetic generators that produce original data with the same statistical properties.

**No actual proprietary reference data may be included in or distributed with the simulator.** No raw values. No sampled rows. No replay of real timeseries. The generators produce new data every time they run.

What we learn from the reference data:

- AX350i printer line speeds range from 0 to 638 units with binary printing/not-printing states
- R-Series vision inspection shows 85.6% fail rates during idle periods (no-read failures, not quality failures)
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
