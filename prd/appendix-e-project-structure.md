# Appendix E: Project Structure

```
collatr-factory-simulator/
  config/
    factory.yaml              # Main configuration
    mosquitto.conf            # Mosquitto broker configuration
    scenarios/
      packaging-line.yaml     # Default packaging line scenarios
      food-bev-line.yaml      # Default F&B line scenarios
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
      press.py                # Flexographic press signals (packaging)
      laminator.py            # Laminator signals (packaging)
      slitter.py              # Slitter signals (packaging)
      coder.py                # Coding and marking signals (shared)
      environment.py          # Environmental sensors (shared)
      energy.py               # Energy monitoring (shared)
      vibration.py            # Vibration monitoring (packaging)
      mixer.py                # Batch mixer signals (F&B)
      oven.py                 # Multi-zone oven signals (F&B)
      filler.py               # Filler/portioner signals (F&B)
      sealer.py               # MAP sealer signals (F&B)
      cold_room.py            # Cold room signals (F&B)
      cip.py                  # Clean-in-place signals (F&B)
    
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
      thermal_diffusion.py    # Oven thermal model (Fourier series)
      bang_bang.py             # Bang-bang hysteresis (chiller)
      string_generator.py     # String value generator (batch IDs)
    
    protocols/
      __init__.py
      modbus_server.py        # Modbus TCP server adapter
      opcua_server.py         # OPC-UA server adapter
      mqtt_publisher.py       # MQTT publish client (to external Mosquitto)
    
    scenarios/
      __init__.py
      job_changeover.py       # Packaging
      web_break.py            # Packaging
      dryer_drift.py          # Packaging
      bearing_wear.py         # Packaging
      ink_excursion.py        # Packaging
      registration_drift.py   # Packaging
      unplanned_stop.py       # Packaging
      shift_change.py         # Shared
      cold_start.py           # Shared
      coder_depletion.py      # Shared
      batch_cycle.py          # F&B
      oven_excursion.py       # F&B
      fill_weight_drift.py    # F&B
      seal_failure.py         # F&B
      chiller_door.py         # F&B
      cip_cycle.py            # F&B
      cold_chain_break.py     # F&B (compressor failure)
      micro_stop.py           # Shared (speed dips)
      material_splice.py      # Packaging (web splice)
    
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
