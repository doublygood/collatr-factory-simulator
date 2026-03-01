# Appendix E: Project Structure

```
collatr-factory-simulator/
  config/
    factory.yaml              # Main configuration
    scenarios/
      packaging-line.yaml     # Default packaging line scenarios
      bearing-failure.yaml    # Long-duration bearing failure scenario
      stress-test.yaml        # High-rate stress test configuration
  
  src/
    __init__.py
    main.py                   # Entry point
    config.py                 # Configuration loading and validation
    clock.py                  # Simulation clock (time management)
    store.py                  # Signal value store
    
    engine/
      __init__.py
      data_engine.py          # Main generation loop
      scenario_engine.py      # Scenario scheduling and execution
      state_machine.py        # Equipment state machine logic
      correlation.py          # Cross-signal correlation model
    
    generators/
      __init__.py
      base.py                 # EquipmentGenerator ABC
      press.py                # Flexographic press signals
      laminator.py            # Laminator signals
      slitter.py              # Slitter signals
      coder.py                # Coding and marking signals
      environment.py          # Environmental sensors
      energy.py               # Energy monitoring
      vibration.py            # Vibration monitoring
    
    models/
      __init__.py
      steady_state.py         # Steady state with noise
      sinusoidal.py           # Sinusoidal with noise
      first_order_lag.py      # Setpoint tracking
      ramp.py                 # Ramp up/down
      random_walk.py          # Random walk with mean reversion
      counter.py              # Counter increment
      depletion.py            # Consumable depletion
      correlated.py           # Correlated follower
      state.py                # State machine
    
    protocols/
      __init__.py
      modbus_server.py        # Modbus TCP server adapter
      opcua_server.py         # OPC-UA server adapter
      mqtt_adapter.py         # MQTT broker/publisher adapter
    
    scenarios/
      __init__.py
      job_changeover.py
      web_break.py
      dryer_drift.py
      bearing_wear.py
      ink_excursion.py
      registration_drift.py
      unplanned_stop.py
      shift_change.py
      cold_start.py
      coder_depletion.py
    
    health/
      __init__.py
      server.py               # HTTP health check and status endpoint
  
  tests/
    test_config.py
    test_clock.py
    test_store.py
    test_generators/
      test_press.py
      test_laminator.py
      test_coder.py
    test_models/
      test_steady_state.py
      test_first_order_lag.py
      test_counter.py
      test_random_walk.py
    test_protocols/
      test_modbus.py
      test_opcua.py
      test_mqtt.py
    test_scenarios/
      test_web_break.py
      test_bearing_wear.py
      test_shift_change.py
    test_integration/
      test_full_run.py        # Spin up simulator, connect clients, verify data
  
  Dockerfile
  docker-compose.yaml
  requirements.txt
  requirements-dev.txt
  pyproject.toml
  README.md
  LICENSE
```
