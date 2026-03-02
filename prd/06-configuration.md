# Configuration

## 6.1 Configuration File Format

The simulator uses YAML configuration files. YAML was chosen over TOML for its better support of nested structures, arrays of objects, and multi-line strings. The configuration is hierarchical: factory > equipment > signals > parameters.

## 6.2 Main Configuration File

File: `config/factory.yaml`

```yaml
# Collatr Factory Simulator Configuration
# Version: 1.0

factory:
  name: "Demo Packaging Factory"
  site_id: "demo"
  timezone: "Europe/London"

simulation:
  time_scale: 1.0          # 1.0 = real-time, 10.0 = 10x speed
  random_seed: null         # null = time-based, integer = deterministic
  tick_interval_ms: 100     # Internal engine tick rate
  start_time: null          # null = now, ISO8601 = specific start

protocols:
  modbus:
    enabled: true
    bind_address: "0.0.0.0"
    port: 502
    unit_id: 1
    byte_order: "ABCD"     # ABCD (big-endian) or CDAB (word-swapped)
    error_injection:
      exception_probability: 0.001
      timeout_probability: 0.0005
      response_delay_ms: [0, 50]

  opcua:
    enabled: true
    bind_address: "0.0.0.0"
    port: 4840
    server_name: "Collatr Factory Simulator"
    namespace_uri: "urn:collatr:factory-simulator"
    security_mode: "None"   # None, Sign, SignAndEncrypt
    anonymous_access: true
    users:
      - username: "collatr"
        password: "collatr123"

  mqtt:
    enabled: true
    broker_host: "mqtt-broker"  # Mosquitto sidecar hostname in Docker network
    broker_port: 1883
    topic_prefix: "collatr/factory"
    sparkplug_b: false      # Phase 2
    retain: true
    client_id: "factory-simulator"
    username: null
    password: null
    qos_default: 1
    buffer_limit: 1000      # Max buffered messages during connection loss
    buffer_overflow: "drop_oldest"  # drop_oldest or drop_newest

equipment:
  press:
    enabled: true
    type: "flexographic_press"
    model: "CI-8"
    target_speed: 200       # m/min, normal operating speed
    speed_range: [50, 400]
    signals:
      line_speed:
        model: "ramp"
        noise_sigma: 0.5
        modbus_hr: [100, 101]
        modbus_type: "float32"
        opcua_node: "PackagingLine.Press1.LineSpeed"
        opcua_type: "Double"
      web_tension:
        model: "correlated_follower"
        parent: "press.line_speed"
        transform: "linear"
        params:
          base: 80
          factor: 0.5
          sigma: 5.0
        opcua_node: "PackagingLine.Press1.WebTension"
        opcua_type: "Double"
        sample_rate_ms: 500
      # ... (remaining signals follow same pattern)

  laminator:
    enabled: true
    type: "solvent_free_laminator"
    signals:
      # ... signal definitions

  slitter:
    enabled: true
    type: "slitter_rewinder"
    signals:
      # ... signal definitions

  coder:
    enabled: true
    type: "cij_printer"
    signals:
      # ... signal definitions

  environment:
    enabled: true
    type: "iolink_sensor"
    signals:
      # ... signal definitions

  energy:
    enabled: true
    type: "power_meter"
    signals:
      # ... signal definitions

  vibration:
    enabled: true
    type: "wireless_vibration"
    signals:
      # ... signal definitions

scenarios:
  job_changeover:
    enabled: true
    frequency_per_shift: [3, 6]
    duration_seconds: [600, 1800]
    speed_change_probability: 0.3
    counter_reset_probability: 0.7

  web_break:
    enabled: true
    frequency_per_week: [1, 2]
    recovery_seconds: [900, 3600]

  dryer_drift:
    enabled: true
    frequency_per_shift: [1, 2]
    max_drift_c: [5, 15]
    duration_seconds: [1800, 7200]

  bearing_wear:
    enabled: true
    start_after_hours: 48
    duration_hours: 336
    culminate_in_failure: false

  ink_viscosity_excursion:
    enabled: true
    frequency_per_shift: [2, 3]
    duration_seconds: [300, 1800]

  registration_drift:
    enabled: true
    frequency_per_shift: [1, 3]
    duration_seconds: [120, 600]

  unplanned_stop:
    enabled: true
    frequency_per_shift: [1, 2]
    duration_seconds: [300, 3600]

  shift_change:
    enabled: true
    times: ["06:00", "14:00", "22:00"]
    changeover_seconds: [300, 900]
    night_shift_speed_factor: 0.9
    weekend_enabled: false

  cold_start_spike:
    enabled: true
    idle_threshold_minutes: 30
    spike_duration_seconds: [2, 5]
    spike_magnitude: [1.5, 2.0]

shifts:
  pattern: "3x8"           # 3 shifts of 8 hours
  day_start: "06:00"
  operators:
    morning:
      speed_bias: 1.0
      waste_rate_bias: 1.0
    afternoon:
      speed_bias: 0.95
      waste_rate_bias: 1.05
    night:
      speed_bias: 0.90
      waste_rate_bias: 1.10
```

## 6.3 Docker Compose Deployment

File: `docker-compose.yaml`

```yaml
version: "3.8"

services:
  mqtt-broker:
    image: eclipse-mosquitto:2
    container_name: mqtt-broker
    ports:
      - "1883:1883"     # MQTT
    volumes:
      - ./config/mosquitto.conf:/mosquitto/config/mosquitto.conf:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "mosquitto_sub", "-t", "$$SYS/#", "-C", "1", "-W", "3"]
      interval: 30s
      timeout: 10s
      retries: 3

  factory-simulator:
    build:
      context: .
      dockerfile: Dockerfile
    image: collatr/factory-simulator:latest
    container_name: factory-simulator
    ports:
      - "502:502"       # Modbus TCP
      - "4840:4840"     # OPC-UA
      - "8080:8080"     # Web dashboard / health check
    volumes:
      - ./config:/app/config:ro
    environment:
      - SIM_TIME_SCALE=1.0
      - SIM_RANDOM_SEED=
      - SIM_LOG_LEVEL=info
      - MODBUS_ENABLED=true
      - MODBUS_PORT=502
      - OPCUA_ENABLED=true
      - OPCUA_PORT=4840
      - MQTT_ENABLED=true
      - MQTT_BROKER_HOST=mqtt-broker
      - MQTT_BROKER_PORT=1883
    depends_on:
      mqtt-broker:
        condition: service_healthy
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/health"]
      interval: 30s
      timeout: 10s
      retries: 3
```

## 6.4 Environment Variables

Environment variables override configuration file values. All environment variables are prefixed with `SIM_`.

| Variable | Default | Description |
|----------|---------|-------------|
| `SIM_TIME_SCALE` | `1.0` | Simulation speed multiplier |
| `SIM_RANDOM_SEED` | (empty) | Random seed for deterministic runs |
| `SIM_LOG_LEVEL` | `info` | Log level: debug, info, warn, error |
| `SIM_CONFIG_PATH` | `/app/config/factory.yaml` | Path to main config file |
| `MODBUS_ENABLED` | `true` | Enable Modbus TCP server |
| `MODBUS_PORT` | `502` | Modbus TCP port |
| `MODBUS_BYTE_ORDER` | `ABCD` | Register byte order |
| `OPCUA_ENABLED` | `true` | Enable OPC-UA server |
| `OPCUA_PORT` | `4840` | OPC-UA port |
| `MQTT_ENABLED` | `true` | Enable MQTT publishing |
| `MQTT_BROKER_HOST` | `mqtt-broker` | MQTT broker hostname |
| `MQTT_BROKER_PORT` | `1883` | MQTT broker port |
| `MQTT_TOPIC_PREFIX` | `collatr/factory` | MQTT topic prefix |

## 6.5 Quick Start

```bash
# Start with defaults (all protocols, real-time, random seed)
docker compose up -d

# Start at 10x speed with deterministic seed
SIM_TIME_SCALE=10 SIM_RANDOM_SEED=42 docker compose up -d

# Start with only Modbus enabled
OPCUA_ENABLED=false MQTT_ENABLED=false docker compose up -d

# Verify Modbus is serving data
modbus read -a localhost -p 502 -t hr -s 100 -c 4

# Verify OPC-UA is serving data
# (use any OPC-UA client, e.g., UaExpert)
# Connect to opc.tcp://localhost:4840

# Verify MQTT is publishing
mosquitto_sub -h localhost -t "collatr/factory/#" -v
```
