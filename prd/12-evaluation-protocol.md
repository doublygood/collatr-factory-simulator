# Evaluation Protocol

## 12.1 Purpose

This document defines a standardised protocol for evaluating anomaly detection algorithms against simulator output. The simulator produces labelled data with ground truth annotations (Section 4.7). This protocol describes how to generate evaluation datasets, pair clean and impaired runs, and compute detection metrics.

The goal is repeatable, comparable evaluation. Two engineers running the same configuration should produce the same dataset and reach the same scores.

## 12.2 Dataset Generation

A single evaluation dataset consists of three artefacts:

1. **Signal data.** The time series output from a simulator run. One value per signal per tick. Format: CSV or Parquet, one row per timestamp.
2. **Ground truth log.** The JSONL sidecar file described in Section 4.7. Every scenario event, state transition, and data quality injection is recorded with its simulated timestamp.
3. **Run manifest.** A YAML file capturing the full configuration, random seed, simulator version, and wall-clock start/end times. This enables exact reproduction.

To generate a dataset:

1. Choose a run configuration (see Section 12.5 for recommended configurations).
2. Set a fixed random seed in the configuration file.
3. Run the simulator. It produces signal data and the ground truth log.
4. Archive all three artefacts together. Name the archive with the seed and configuration variant.

The ground truth log is the single source of labels. Do not hand-label signal data. The simulator already knows every injected event.

## 12.3 Clean/Impaired Pairing

A paired evaluation generates two runs from the same base signal:

**Clean run.** All scenarios disabled. All data quality impairments disabled. The simulator produces normal operational data with natural variation, noise, and state transitions (job changeovers, shift changes). No injected anomalies. No communication drops. No sensor faults.

**Impaired run.** Scenarios and data quality impairments enabled per the chosen configuration. The simulator injects anomalies, faults, communication drops, and sensor failures on top of the same base signal evolution.

Both runs use the same random seed for base signal generation. The base signal models (steady state, ramp, random walk, correlated follower) produce identical trajectories in both runs. The difference is the injected layer: scenarios and data quality events.

This pairing enables ablation studies. Compare detector performance on clean data (false positive baseline) against impaired data (detection rate). The paired design isolates the effect of each impairment category. Run three pairs to separate the contributions:

1. Clean vs scenarios-only (no data quality impairments).
2. Clean vs impairments-only (no scenarios).
3. Clean vs full impaired (both scenarios and impairments).

**Configuration for clean run:**

```yaml
scenarios:
  job_changeover:
    enabled: true        # Normal operations, not an anomaly
  shift_change:
    enabled: true        # Normal operations
  # All other scenarios: enabled: false

data_quality:
  modbus_drop:
    enabled: false
  opcua_stale:
    enabled: false
  mqtt_drop:
    enabled: false
  sensor_disconnect:
    enabled: false
  stuck_sensor:
    enabled: false
  noise:
    enabled: true        # Noise is part of the base signal, not an impairment
```

Job changeovers and shift changes remain enabled in the clean run. They are normal operational events, not anomalies.

## 12.4 Evaluation Metrics

Evaluate at the **event level**, not the point level. Point-level metrics (per-sample precision/recall) inflate scores because anomaly events span many consecutive samples. A detector that fires once during a 600-sample event scores 1/600 recall at the point level. That is misleading.

### Event Matching

An event is a contiguous scenario from `scenario_start` to `scenario_end` in the ground truth log. Each event has a time window: `[start_time, end_time]`.

A **detection** is any alert produced by the anomaly detection system under test. Each detection has a timestamp.

An event counts as **detected** if at least one detection falls within the event window. Multiple detections within the same window do not count as multiple true positives. They count as one.

**Tolerance windows.** Detectors may fire before the annotated start (precursor signals) or after the annotated end (processing delay). Early detection is good behaviour and should not be penalised. Two configurable margins extend the matching window:

- **Pre-margin** (`pre_margin_seconds`, default 30). A detection within `[start - pre_margin, start]` counts as a true positive for that event.
- **Post-margin** (`post_margin_seconds`, default 60). A detection within `[end, end + post_margin]` counts as a true positive.

The effective matching window becomes `[start - pre_margin, end + post_margin]`. If two adjacent events have overlapping effective windows, a single detection is assigned to the nearest event by start time.

Detection latency is still measured from `scenario_start`. Early detections produce negative latency. Negative latency is desirable and should be reported as-is, not clamped to zero.

The NAB benchmark uses a sigmoidal scoring function for partial credit. Our approach is simpler: binary match within the tolerance window. This is adequate for the simulator's primary use case (demos and integration testing). A sigmoidal scoring function can be added later for research benchmarking.

A detection that falls outside all effective matching windows is a **false positive**.

An event with no detection inside its effective matching window is a **missed event** (false negative).

### Metrics

**Precision.** The fraction of detections that correspond to real events.

```
precision = true_positives / (true_positives + false_positives)
```

**Recall.** The fraction of real events that were detected.

```
recall = detected_events / total_events
```

**F1 score.** The harmonic mean of precision and recall.

```
F1 = 2 * precision * recall / (precision + recall)
```

**Detection latency.** For each detected event, the time from `scenario_start` to the first detection within the window. Report the median and 90th percentile across all detected events.

### Reporting

Report metrics per scenario type and overall. A detector may excel at web breaks (sudden, large magnitude) but miss dryer drift (gradual, small magnitude). Per-scenario breakdown reveals these patterns.

### Severity-Weighted Metrics (Supplementary)

Unweighted metrics treat all events equally. A web break (minutes of downtime, thousands of pounds) and a micro-stop (15 seconds, negligible cost) contribute equally to recall. This is adequate for basic evaluation but masks operational impact.

Each scenario type carries an optional severity weight (default 1.0). Weighted recall accounts for the operational cost of missed events:

```
weighted_recall = sum(weight_i for detected events) / sum(weight_i for all events)
```

Weighted F1 is the harmonic mean of precision and weighted recall.

Default severity weights:

| Scenario Type | Default Weight |
|---|---|
| web_break | 10.0 |
| unplanned_stop | 5.0 |
| seal_integrity_failure | 8.0 |
| cold_chain_break | 10.0 |
| bearing_wear (phase 3) | 8.0 |
| dryer_drift / oven_excursion | 3.0 |
| fill_weight_drift | 3.0 |
| ink_viscosity_excursion | 2.0 |
| registration_drift | 2.0 |
| contextual_anomaly | 5.0 |
| intermittent_fault | 4.0 |
| micro_stop | 1.0 |
| sensor_disconnect | 2.0 |
| stuck_sensor | 3.0 |

Weights are configurable in the evaluation configuration file. Unweighted metrics (weight = 1.0 for all) remain the primary reported metrics. Weighted metrics are supplementary. Report both.

### Latency Targets

Section 12.4 measures detection latency (median and 90th percentile) but does not define what counts as fast enough. The following per-scenario targets are based on operational consequence:

| Scenario Type | Target Latency | Rationale |
|---|---|---|
| web_break | < 2 seconds | Before the press fully stops |
| unplanned_stop | < 10 seconds | Before operator reaches the machine |
| seal_integrity_failure | < 60 seconds | Before a batch of defective packs accumulates |
| cold_chain_break | < 5 minutes | Before product temperature exceeds safe threshold |
| bearing_wear | < 24 hours before failure | Enough time to schedule maintenance |
| dryer_drift | < 15 minutes | Before waste accumulates significantly |
| fill_weight_drift | < 10 minutes | Before regulatory non-compliance |
| contextual_anomaly | < 5 minutes | Context-dependent, varies |
| intermittent_fault (phase 1) | < 48 hours of first occurrence | Early warning value |
| micro_stop | N/A (detection, not prediction) | Counted after the fact |

These are aspirational targets for a mature detection system. A first-generation detector is not expected to meet all targets. Report actual latency alongside targets for gap analysis.

### Statistical Significance

A single-seed result is not statistically significant. Random scenario placement changes with each seed. A detector scoring 0.85 F1 on one seed might score 0.72 on another.

**Internal evaluation (regression testing, development).** A single fixed seed is sufficient. The goal is deterministic comparison across code changes, not absolute scoring.

**Published benchmarking or comparative evaluation.** Run N=10 independent seeds. Use consecutive integers starting from a base (seeds 1 through 10). This is reproducible. Report mean and standard deviation of precision, recall, F1, and detection latency (median and p90).

**Significance test.** A result is statistically significant if the 95% confidence interval does not overlap between two detectors. Compute the interval as:

```
CI = mean +/- 1.96 * std / sqrt(N)
```

If the intervals for detector A and detector B do not overlap on a given metric, the difference is significant at p < 0.05.

**Trivial baseline.** Report the performance of a random detector that fires with probability p per tick, where p equals the anomaly density of the dataset. Anomaly density is the fraction of ticks that fall inside any ground truth event window. This random baseline provides a floor. Any useful detector must beat it. If a detector does not beat the random baseline, it has no predictive value.

## 12.5 Recommended Run Configurations

### Run A: Normal Operations (24 hours)

Simulates a typical production day. Three shifts. Job changeovers, shift changes, micro-stops. Low anomaly rate. Tests false positive rate under normal conditions.

| Parameter | Value |
|---|---|
| Duration | 24 simulated hours |
| Time compression | 10x (2.4 real hours) |
| Scenarios enabled | job_changeover, shift_change, micro_stop, dryer_drift (1 per shift), ink_viscosity_excursion |
| Data quality | Default (communication drops, noise, duplicate timestamps) |
| Seed | Any fixed value |
| Seeds | 1 (development), 10 (benchmarking) |
| pre_margin_seconds | 30 |
| post_margin_seconds | 60 |

### Run B: Heavy Anomaly (24 hours)

Simulates a bad day. Multiple faults, intermittent failures, and contextual anomalies. Tests detection rate under heavy load.

| Parameter | Value |
|---|---|
| Duration | 24 simulated hours |
| Time compression | 10x (2.4 real hours) |
| Scenarios enabled | All scenarios enabled. web_break frequency doubled. unplanned_stop frequency doubled. contextual_anomaly frequency tripled. |
| Data quality | All impairments enabled. sensor_disconnect frequency doubled. |
| Seed | Any fixed value |
| Seeds | 1 (development), 10 (benchmarking) |
| pre_margin_seconds | 30 |
| post_margin_seconds | 60 |

### Run C: Long-Term Degradation (7 days)

Simulates a full week including bearing wear progression and intermittent faults evolving toward permanent failure. Tests trend detection over long horizons.

| Parameter | Value |
|---|---|
| Duration | 7 simulated days |
| Time compression | 100x (1.68 real hours) |
| Scenarios enabled | bearing_wear (start at hour 0, culminate_in_failure: true), intermittent_fault (bearing and electrical), all normal operations |
| Data quality | Default |
| Seed | Any fixed value |
| Seeds | 1 (development), 10 (benchmarking) |
| pre_margin_seconds | 30 |
| post_margin_seconds | 60 |

## 12.6 Cross-References

- Ground truth event log format and event types: [Section 4.7](04-data-generation-engine.md)
- Scenario definitions and parameters: [Section 5](05-scenario-system.md)
- Data quality impairment definitions: [Section 10](10-data-quality-realism.md)
- Configuration reference for all parameters: [Appendix D](appendix-d-configuration-reference.md)
