# Round 2 Consolidated Issues

**Date:** 2 March 2026
**Sources:** Automation Engineer (review-round2-automation-engineer-2026-03-02.md), Data Scientist (review-round2-data-scientist-2026-03-02.md)
**PRD state at review:** 22 files, 5,112 lines, all 32 Round 1 items resolved

---

## High (6)

| # | Source | Issue | File(s) | Status |
|---|--------|-------|---------|--------|
| H1 | Auto | Oven setpoint register addresses: Section 3 says HR 1120-1125, Appendix A says HR 1110-1115 | `03-protocol-endpoints.md`, `appendix-a-modbus-register-map.md` | RESOLVED |
| H2 | Auto | MQTT topic prefix mismatch: Section 3 uses `packaging1`, Appendix C uses `line3` | `03-protocol-endpoints.md`, `appendix-c-mqtt-topic-map.md` | RESOLVED |
| H3 | Auto | Success criteria lists wrong Modbus slave assignments vs Section 3a.3 topology | `11-success-criteria.md` | RESOLVED |
| H4 | Data | Thermal diffusion initial condition: first-term Fourier gives T(0)=37C not 4C. Need 3+ terms or clamp | `04-data-generation-engine.md` | RESOLVED |
| H5 | Data | Evaluation protocol needs tolerance windows for early/late detections | `12-evaluation-protocol.md` | RESOLVED |
| H6 | Data | Cross-run statistical significance: need N=10 seeds, report mean and std dev | `12-evaluation-protocol.md` | RESOLVED |

## Medium (9)

| # | Source | Issue | File(s) | Status |
|---|--------|-------|---------|--------|
| M1 | Auto | Blast chiller vs cold room naming inconsistency | `02b-factory-layout-food-and-beverage.md`, `appendix-e-project-structure.md` | OPEN |
| M2 | Auto | Laminator has drying oven signal but described as solvent-free | `02-simulated-factory-layout.md` | OPEN |
| M3 | Auto | Mixer speed: equipment says 1000-3000 RPM, scenario config says 30-120 RPM | `02b-factory-layout-food-and-beverage.md`, `appendix-d-configuration-reference.md` | OPEN |
| M4 | Auto | OPC-UA Energy node: Section 3 says top-level peer, Appendix B says under profile tree | `03-protocol-endpoints.md`, `appendix-b-opcua-node-tree.md` | OPEN |
| M5 | Auto | CIP conductivity threshold: config says 50 uS/cm (=0.05 mS/cm), text says "below 5 mS/cm" | `appendix-d-configuration-reference.md` | OPEN |
| M6 | Data | Mixing matrix produces ~2x inflated correlations, need Cholesky decomposition | `04-data-generation-engine.md` | OPEN |
| M7 | Data | Peer correlation + sigma ordering unspecified (generate, mix, then scale) | `04-data-generation-engine.md` | OPEN |
| M8 | Data | Severity weighting in evaluation (web break vs micro-stop weighted equally) | `12-evaluation-protocol.md` | OPEN |
| M9 | Data | Detection latency targets not defined per scenario type | `12-evaluation-protocol.md` | OPEN |

## Low (12)

| # | Source | Issue | File(s) | Status |
|---|--------|-------|---------|--------|
| L1 | Auto | Signal count in table 2.11: `press.line_speed` protocol assignment inconsistent | `02-simulated-factory-layout.md` | OPEN |
| L2 | Auto | F&B network diagram: CollatrEdge and QC station both at .50 | `03a-network-topology.md` | OPEN |
| L3 | Auto | F&B input register list in Section 3 shorter than Appendix A | `03-protocol-endpoints.md` | OPEN |
| L4 | Auto | Config naming: Section 6 `drift_degrees` vs Appendix D `max_drift_c` | `06-configuration.md` | OPEN |
| L5 | Auto | No material splice scenario | `05-scenario-system.md` | OPEN |
| L6 | Auto | Checkweigher missing TNE thresholds (TN/28, WELMEC 6.7) | `02b-factory-layout-food-and-beverage.md` | OPEN |
| L7 | Data | Second-order response must reset t on setpoint change (implied, not stated) | `04-data-generation-engine.md` | OPEN |
| L8 | Data | Student-t variance 29% higher than sigma at df=5 (document or correct) | `04-data-generation-engine.md` | OPEN |
| L9 | Data | AR(1) state after connection gap: specify "continue internally" | `04-data-generation-engine.md` | OPEN |
| L10 | Data | Ground truth log should include noise parameters in header record | `04-data-generation-engine.md` | OPEN |
| L11 | Data | No random baseline defined in evaluation protocol | `12-evaluation-protocol.md` | OPEN |
| L12 | Data | 1/f noise absent (spectral analysis tell, Phase 2) | `04-data-generation-engine.md` | OPEN |
