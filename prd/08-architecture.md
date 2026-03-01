# Architecture

## 8.1 Component Diagram

```
+--------------------------------------------------------------------+
|                     Collatr Factory Simulator                       |
|                                                                     |
|  +-------------------+     +-------------------+                    |
|  |   Configuration   |     |  Scenario Engine  |                    |
|  |   (YAML loader)   |     |  (event scheduler)|                    |
|  +--------+----------+     +--------+----------+                    |
|           |                          |                               |
|           v                          v                               |
|  +--------------------------------------------------+               |
|  |              Simulation Clock                     |               |
|  |   (manages sim time, tick rate, compression)      |               |
|  +---------------------------+----------------------+               |
|                              |                                      |
|                              v                                      |
|  +--------------------------------------------------+               |
|  |              Machine State Engine                 |               |
|  |   (state machine per equipment, transition logic) |               |
|  +---------------------------+----------------------+               |
|                              |                                      |
|                              v                                      |
|  +--------------------------------------------------+               |
|  |           Signal Generation Engine                |               |
|  |                                                    |               |
|  |  +----------+ +----------+ +----------+           |               |
|  |  | Press    | | Lam.     | | Slitter  |           |               |
|  |  | Generator| | Generator| | Generator|           |               |
|  |  +----------+ +----------+ +----------+           |               |
|  |  +----------+ +----------+ +----------+           |               |
|  |  | Coder    | | Env      | | Energy   |           |               |
|  |  | Generator| | Generator| | Generator|           |               |
|  |  +----------+ +----------+ +----------+           |               |
|  |  +----------+                                     |               |
|  |  | Vibration|    (correlation model links         |               |
|  |  | Generator|     generators together)            |               |
|  |  +----------+                                     |               |
|  +---------------------------+----------------------+               |
|                              |                                      |
|                              v                                      |
|  +--------------------------------------------------+               |
|  |              Signal Value Store                   |               |
|  |   (current value of all 40 signals + metadata)    |               |
|  +------+------------------+------------------+-----+               |
|         |                  |                  |                      |
|         v                  v                  v                      |
|  +-----------+    +-------------+    +------------+                 |
|  | Modbus    |    | OPC-UA      |    | MQTT       |                 |
|  | Adapter   |    | Adapter     |    | Adapter    |                 |
|  |           |    |             |    |            |                 |
|  | Reads from|    | Reads from  |    | Reads from |                 |
|  | store,    |    | store,      |    | store,     |                 |
|  | encodes   |    | updates     |    | publishes  |                 |
|  | registers |    | node values |    | messages   |                 |
|  +-----+-----+   +------+------+   +------+------+                 |
|        |                 |                 |                         |
+--------|-----------------|-----------------|-------------------------+
         |                 |                 |
         v                 v                 v
    Port 502          Port 4840         Port 1883
   (Modbus TCP)      (OPC-UA TCP)     (MQTT TCP)
```

## 8.2 Data Flow

1. **Configuration loads.** The YAML config is parsed into typed configuration objects. Signal definitions, protocol mappings, and scenario schedules are validated.

2. **Simulation clock starts.** The clock ticks at `tick_interval_ms` (default: 100ms). At each tick, the clock advances by `tick_interval_ms * time_scale` simulated milliseconds.

3. **Scenario engine evaluates.** The scenario engine checks if any scheduled scenario should start, advance, or end at the current simulation time. Active scenarios modify machine state or signal parameters.

4. **Machine state engine evaluates.** Each equipment group's state machine processes pending transitions. State changes cascade: press.machine_state changing to Running triggers coder.state changing to Printing.

5. **Signal generators produce values.** Each generator runs only if its sample interval has elapsed. A 1-second signal generates a new value every 1 simulated second. A 500ms signal generates every 500 simulated milliseconds. Generators read the current machine state and other signal values (for correlations) from the signal store.

6. **Signal store updates.** New values are written to the central signal store. Each value has: signal ID, timestamp, numeric value, quality flag.

7. **Protocol adapters read the store.** Each adapter runs independently.
   - **Modbus adapter:** On each client read request, the adapter reads the latest value from the store, encodes it according to the register map (float32, uint32, uint16, etc.), and returns it in the Modbus response.
   - **OPC-UA adapter:** At each tick, the adapter updates OPC-UA node values from the store. Subscribed clients receive data change notifications.
   - **MQTT adapter:** At each signal's publish interval, the adapter reads the store, formats a JSON payload, and publishes to the topic.

## 8.3 Concurrency Model

The simulator uses Python `asyncio` for concurrency. Each protocol server runs as an async task.

```python
async def main():
    config = load_config()
    store = SignalStore()
    clock = SimulationClock(config.simulation)
    
    engine = DataEngine(config, store, clock)
    
    tasks = [
        engine.run(),                           # Signal generation loop
        ModbusServer(config.protocols.modbus, store).run(),
        OpcuaServer(config.protocols.opcua, store).run(),
        MqttBroker(config.protocols.mqtt, store).run(),
        HealthServer(config).run(),             # HTTP health check
    ]
    
    async with asyncio.TaskGroup() as tg:
        for task in tasks:
            tg.create_task(task)
```

The signal store uses no locks. The engine is the sole writer. Protocol adapters are readers. In Python's asyncio single-threaded model, there are no race conditions. Values are eventually consistent within one tick (100ms).

## 8.4 Plugin Architecture

New equipment types can be added by implementing the `EquipmentGenerator` interface:

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class SignalValue:
    signal_id: str
    timestamp: float
    value: float
    quality: str  # "good", "uncertain", "bad"

class EquipmentGenerator(ABC):
    @abstractmethod
    def configure(self, config: dict) -> None:
        """Load equipment-specific configuration."""
        pass
    
    @abstractmethod
    def get_signal_ids(self) -> list[str]:
        """Return list of signal IDs this equipment produces."""
        pass
    
    @abstractmethod
    def generate(self, sim_time: float, machine_state: int, 
                 store: SignalStore) -> list[SignalValue]:
        """Generate new signal values for the current tick."""
        pass
    
    @abstractmethod
    def get_protocol_mappings(self) -> dict:
        """Return Modbus/OPC-UA/MQTT mappings for each signal."""
        pass
```

Adding a new equipment type requires:
1. Create a new generator class implementing `EquipmentGenerator`.
2. Add the equipment section to the YAML config.
3. Register the generator in the equipment factory.

No changes to protocol adapters or the simulation engine are needed.

## 8.5 Health Check and Monitoring

The simulator exposes an HTTP endpoint on port 8080:

```
GET /health -> 200 OK {"status": "running", "sim_time": "...", "signals": 40}
GET /metrics -> Prometheus metrics (optional)
GET /status -> Detailed status of all signals and their current values
```

The `/status` endpoint returns a JSON object with current values for all 40 signals. This is useful for debugging and for building a simple web dashboard.
