# Appendix C: Full MQTT Topic Map

## Packaging Profile Topics

Topic prefix: `collatr/factory/demo/line3/`

### Coder Topics

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `collatr/factory/demo/line3/coder/state` | coder.state | 1 | Yes | Event-driven |
| `collatr/factory/demo/line3/coder/prints_total` | coder.prints_total | 1 | Yes | Event-driven |
| `collatr/factory/demo/line3/coder/ink_level` | coder.ink_level | 0 | Yes | 60s |
| `collatr/factory/demo/line3/coder/printhead_temp` | coder.printhead_temp | 0 | Yes | 30s |
| `collatr/factory/demo/line3/coder/ink_pump_speed` | coder.ink_pump_speed | 0 | Yes | 5s |
| `collatr/factory/demo/line3/coder/ink_pressure` | coder.ink_pressure | 0 | Yes | 5s |
| `collatr/factory/demo/line3/coder/ink_viscosity_actual` | coder.ink_viscosity_actual | 0 | Yes | 30s |
| `collatr/factory/demo/line3/coder/supply_voltage` | coder.supply_voltage | 0 | Yes | 60s |
| `collatr/factory/demo/line3/coder/ink_consumption_ml` | coder.ink_consumption_ml | 0 | Yes | 60s |
| `collatr/factory/demo/line3/coder/nozzle_health` | coder.nozzle_health | 1 | Yes | Event-driven |
| `collatr/factory/demo/line3/coder/gutter_fault` | coder.gutter_fault | 1 | Yes | Event-driven |

### Environmental Topics

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `collatr/factory/demo/line3/env/ambient_temp` | env.ambient_temp | 0 | Yes | 60s |
| `collatr/factory/demo/line3/env/ambient_humidity` | env.ambient_humidity | 0 | Yes | 60s |

### Vibration Topics

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `collatr/factory/demo/line3/vibration/main_drive_x` | vibration.main_drive_x | 0 | No | 1s |
| `collatr/factory/demo/line3/vibration/main_drive_y` | vibration.main_drive_y | 0 | No | 1s |
| `collatr/factory/demo/line3/vibration/main_drive_z` | vibration.main_drive_z | 0 | No | 1s |

### Batch Vibration Topic (Alternative)

For high-frequency vibration data, an alternative batch topic publishes all three axes in one message:

```
collatr/factory/demo/line3/vibration/main_drive
```

```json
{
  "timestamp": "2026-03-01T14:30:00.000Z",
  "x": 4.2,
  "y": 3.8,
  "z": 5.1,
  "unit": "mm/s",
  "quality": "good"
}
```

This reduces MQTT message count by 3x for vibration data at the cost of a non-standard payload structure. Both the per-axis and batch formats are published simultaneously by default. The per-axis topics can be disabled via configuration.

---

## F&B Profile Topics

Topic prefix: `collatr/factory/demo/foodbev1/`

The F&B profile publishes coder and environmental topics using the same signal definitions as the packaging profile (shared equipment). The F&B profile does **not** have vibration MQTT topics because the F&B line has no vibration monitoring equipment group.

### Coder Topics (Shared Equipment)

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `collatr/factory/demo/foodbev1/coder/state` | coder.state | 1 | Yes | Event-driven |
| `collatr/factory/demo/foodbev1/coder/prints_total` | coder.prints_total | 1 | Yes | Event-driven |
| `collatr/factory/demo/foodbev1/coder/ink_level` | coder.ink_level | 0 | Yes | 60s |
| `collatr/factory/demo/foodbev1/coder/printhead_temp` | coder.printhead_temp | 0 | Yes | 30s |
| `collatr/factory/demo/foodbev1/coder/ink_pump_speed` | coder.ink_pump_speed | 0 | Yes | 5s |
| `collatr/factory/demo/foodbev1/coder/ink_pressure` | coder.ink_pressure | 0 | Yes | 5s |
| `collatr/factory/demo/foodbev1/coder/ink_viscosity_actual` | coder.ink_viscosity_actual | 0 | Yes | 30s |
| `collatr/factory/demo/foodbev1/coder/supply_voltage` | coder.supply_voltage | 0 | Yes | 60s |
| `collatr/factory/demo/foodbev1/coder/ink_consumption_ml` | coder.ink_consumption_ml | 0 | Yes | 60s |
| `collatr/factory/demo/foodbev1/coder/nozzle_health` | coder.nozzle_health | 1 | Yes | Event-driven |
| `collatr/factory/demo/foodbev1/coder/gutter_fault` | coder.gutter_fault | 1 | Yes | Event-driven |

### Environmental Topics (Shared Equipment)

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `collatr/factory/demo/foodbev1/env/ambient_temp` | env.ambient_temp | 0 | Yes | 60s |
| `collatr/factory/demo/foodbev1/env/ambient_humidity` | env.ambient_humidity | 0 | Yes | 60s |

> **No vibration topics for F&B.** The F&B line does not include a vibration monitoring equipment group. The packaging profile's vibration topics (`collatr/factory/demo/line3/vibration/*`) are exclusive to the packaging profile.

---

## JSON Payload Schema

All topics across both profiles use the same JSON payload format:

```json
{
  "timestamp": "2026-03-01T14:30:00.000Z",
  "value": 42.7,
  "unit": "C",
  "quality": "good"
}
```

Field types:
- `timestamp`: string (ISO 8601 with milliseconds, UTC)
- `value`: number (float64 JSON number, no string encoding)
- `unit`: string (engineering unit abbreviation)
- `quality`: string, one of: `"good"`, `"uncertain"`, `"bad"`

## Topic Summary

| Profile | Topic Group | Count | Retain | Notes |
|---------|------------|-------|--------|-------|
| Packaging | Coder | 11 | Yes | All 11 coder signals |
| Packaging | Environmental | 2 | Yes | Slow-changing ambient sensors |
| Packaging | Vibration (per-axis) | 3 | No | High-frequency 1s signals, no retain |
| Packaging | Vibration (batch) | 1 | No | Alternative combined payload |
| **Packaging total** | | **17** | | |
| F&B | Coder | 11 | Yes | Same signals, `foodbev1/` prefix |
| F&B | Environmental | 2 | Yes | Same signals, `foodbev1/` prefix |
| **F&B total** | | **13** | | |

Retain is set to `Yes` for all topics except high-frequency vibration signals (1s rate), which use `No` to avoid stale retained messages filling the broker. Event-driven topics use `Retain = Yes` so new subscribers immediately receive the last known state.
