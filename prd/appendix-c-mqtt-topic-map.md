# Appendix C: Full MQTT Topic Map

## Plain JSON Topics

| Topic | Signal | QoS | Retain | Publish Rate |
|-------|--------|-----|--------|-------------|
| `collatr/factory/demo/line3/coder/state` | coder.state | 1 | Yes | Event-driven |
| `collatr/factory/demo/line3/coder/prints_total` | coder.prints_total | 1 | Yes | Event-driven |
| `collatr/factory/demo/line3/coder/ink_level` | coder.ink_level | 0 | Yes | 60s |
| `collatr/factory/demo/line3/coder/printhead_temp` | coder.printhead_temp | 0 | Yes | 30s |
| `collatr/factory/demo/line3/env/ambient_temp` | env.ambient_temp | 0 | Yes | 60s |
| `collatr/factory/demo/line3/env/ambient_humidity` | env.ambient_humidity | 0 | Yes | 60s |
| `collatr/factory/demo/line3/vibration/main_drive_x` | vibration.main_drive_x | 0 | No | 1s |
| `collatr/factory/demo/line3/vibration/main_drive_y` | vibration.main_drive_y | 0 | No | 1s |
| `collatr/factory/demo/line3/vibration/main_drive_z` | vibration.main_drive_z | 0 | No | 1s |

## JSON Payload Schema

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

## Batch Vibration Topic (Alternative)

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
