Read CLAUDE.md for project rules and conventions.

You are implementing Phase 6e (Protocol Polish) of the Collatr Factory Simulator.

## CONTEXT

Phases 0-5 are complete (feature-complete simulator). Phase 6a fixed all RED issues and high-priority YELLOWs (Y1-Y3). Phase 6b fixed robustness YELLOWs (Y4-Y8). Phase 6c fixed validation and protocol polish YELLOWs (Y9-Y15). Phase 6d fixed maintenance, test coverage, and CI YELLOWs (Y16-Y24, Y27).

Current state:
- **All protocols**: Modbus, OPC-UA, MQTT fully implemented with EngineeringUnits, MinimumSamplingInterval, Cholesky correlation, profile-aware exceptions
- **All generators tested**: dedicated test files for all 15 generators
- **Shared time_utils**: REFERENCE_EPOCH_TS and sim-time converters centralised
- **CI**: Python 3.12 + 3.13 matrix, expanded integration tests
- **Config**: health port configurable, dead fields removed, clamp/drift validators fixed
- **Error handling**: server startup verification, narrow exception suppression, MQTT retry, SIGTERM handler
- 3100+ tests passing, ruff + mypy clean

**Phase 6e addresses the final 2 YELLOW issues (Y25-Y26). This completes the full code review remediation.**

The full review reports are in:
- `plans/review-protocol-fidelity.md` (primary reference for both tasks)
- `plans/consolidated-review-action-plan.md`

The Phase 6e plan with detailed per-task instructions is in `plans/phase-6e-protocol-polish.md`. Read it.

## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read `plans/phase-6e-protocol-polish.md` for the full plan
2. Read `plans/phase-6e-tasks.json` to find the **first** task with `"passes": false`
3. Check `depends_on` — if any dependency has `"passes": false`, skip to the next eligible task
4. Read the relevant review file for full context on the issue (the `review_ref` field tells you which)
5. Read the relevant source files before changing anything
6. Implement ONLY that single task's fix
7. Run the new/modified test file alone first: `ruff check src tests && pytest tests/path/to/test.py -v --tb=short`
8. Run ALL tests: `ruff check src tests && mypy src && pytest` — ALL must pass
9. Update `plans/phase-6e-tasks.json`: set `"passes": true` for your completed task
10. Update `plans/phase-6e-progress.md` with what you fixed and any decisions
11. Commit: `phase-6e: <what> (task 6e.X)`
12. Do NOT push. Pushing is handled externally.
13. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## PHASE-SPECIFIC NOTES

### OPC-UA Inactive Profile Nodes (Task 6e.1)

PRD Section 3.2.1: "Nodes for the inactive profile report StatusCode.BadNotReadable and have AccessLevel set to 0."

**This is the most substantial task.** In collapsed mode, only the active profile's OPC-UA nodes exist. The other profile's nodes should be present but inaccessible.

**Architecture — how OPC-UA servers are created:**

`DataEngine.create_opcua_servers()` in `data_engine.py` (line 291) handles both modes:
- **Collapsed mode** (line 311): `OpcuaServer(self._config, self._store)` — single server, full node tree.
- **Realistic mode** (lines 315-325): one `OpcuaServer` per endpoint with `endpoint=` and `clock_drift=`.

**How to wire the inactive config:**

1. Add `inactive_config: FactoryConfig | None = None` parameter to `OpcuaServer.__init__()`. Store as `self._inactive_config`.

2. In `create_opcua_servers()`, for collapsed mode, pass the inactive config:
   ```python
   return [OpcuaServer(self._config, self._store, inactive_config=inactive_cfg)]
   ```

3. The `DataEngine` needs to receive the inactive config. Add it as a constructor parameter:
   ```python
   def __init__(self, config, store, *, topology=None, batch_writer=None,
                ground_truth=None, inactive_config=None):
   ```

4. In `cli.py` `_async_run()`, determine and load the inactive config:
   ```python
   # Determine inactive profile config for OPC-UA dual-profile node tree
   profile = getattr(args, "profile", "packaging")
   inactive_profile = "foodbev" if profile == "packaging" else "packaging"
   inactive_config_path = _default_config_path(inactive_profile)
   inactive_config = None
   if inactive_config_path.exists():
       inactive_config = load_config(inactive_config_path)
   ```
   Pass `inactive_config` to the `DataEngine` constructor.

5. `_default_config_path()` already maps profile strings to YAML paths (cli.py line 85). Both YAML configs ship in the `config/` directory.

**In `_build_node_tree()` — creating inactive nodes:**

After the main active-node loop, if `self._inactive_config` is not None:
```python
# -- Inactive profile nodes (PRD 3.2.1) ---------------------------------
if self._inactive_config is not None and not self._node_tree_root:
    await self._build_inactive_nodes(ns, folder_cache)
```

Create a separate method `_build_inactive_nodes(self, ns, folder_cache)` that:
1. Iterates `self._inactive_config.equipment` → signals with `opcua_node`.
2. Creates folder hierarchy (reusing `folder_cache` from the active build).
3. Creates variable nodes with same data type mapping.
4. Sets `AccessLevel = 0`:
   ```python
   await var_node.write_attribute(
       ua.AttributeIds.AccessLevel,
       ua.DataValue(ua.Variant(0, ua.VariantType.Byte)),
   )
   ```
5. Writes initial value with `BadNotReadable` status:
   ```python
   await var_node.write_value(
       ua.DataValue(
           ua.Variant(init_val, vtype),
           StatusCode_=ua.StatusCode(ua.status_codes.StatusCodes.BadNotReadable),
       )
   )
   ```
6. Adds EURange, EngineeringUnits, MinimumSamplingInterval (same as active nodes).
7. Does NOT add to `self._nodes`, `self._node_to_signal`, etc. — no sync.

**Guard:** Skip `_build_inactive_nodes` when `self._node_tree_root` is set (realistic mode — each server is already scoped to its own subtree).

**asyncua StatusCode API:** Check what asyncua uses. It might be:
- `ua.StatusCodes.BadNotReadable` (check `asyncua.ua.status_codes`)
- Or construct directly: `ua.StatusCode(0x803E0000)` (BadNotReadable numeric value)

**Tests:**
- `test_inactive_profile_nodes_exist` — create OpcuaServer with packaging config active + F&B as inactive_config. Start server. Browse for `FoodBevLine.*` nodes. Assert they exist.
- `test_inactive_profile_access_level_zero` — read AccessLevel of an inactive node. Assert == 0.
- `test_inactive_profile_status_bad` — read value of inactive node. Assert StatusCode is bad.
- `test_inactive_nodes_not_synced` — set store values, run sync loop, verify inactive node values unchanged.
- `test_no_inactive_when_none` — OpcuaServer with `inactive_config=None` creates no inactive nodes.
- `test_realistic_mode_no_inactive` — with `endpoint=` set (realistic), inactive nodes not built even if inactive_config provided.

### Profile-Specific LWT Topic (Task 6e.2)

Both profiles use `lwt_topic: "collatr/factory/status"`. Should be profile-specific.

**Current code:**

`MqttProtocolConfig` (config.py line 179):
```python
lwt_topic: str = "collatr/factory/status"
```

`MqttPublisher._create_client()` (mqtt_publisher.py line 459):
```python
client.will_set(
    self._mqtt_cfg.lwt_topic,
    payload=self._mqtt_cfg.lwt_payload,
    qos=1,
    retain=True,
)
```

**Fix:**

1. In `config.py`, change the default:
   ```python
   lwt_topic: str = ""  # Empty = auto-generated: {topic_prefix}/{line_id}/status
   ```

2. In `MqttPublisher._create_client()`, resolve the LWT topic before `will_set()`:
   ```python
   lwt_topic = self._mqtt_cfg.lwt_topic
   if not lwt_topic:
       lwt_topic = f"{self._mqtt_cfg.topic_prefix}/{self._mqtt_cfg.line_id}/status"
   client.will_set(lwt_topic, ...)
   ```

3. Update YAML configs — remove the explicit `lwt_topic` lines from both `factory.yaml` (line 52) and `factory-foodbev.yaml` (line 53). The auto-generation will produce:
   - Packaging: `collatr/factory/packaging1/status`
   - F&B: `collatr/factory/foodbev1/status`

4. Also publish the online status message to the same resolved topic. Check if there's a startup publish of `{"status": "online"}` — if so, it should use the same resolved topic.

**Tests:**
- `test_lwt_topic_auto_generated` — MqttProtocolConfig with empty lwt_topic + line_id="packaging1" → resolved topic is `collatr/factory/packaging1/status`.
- `test_lwt_topic_explicit` — MqttProtocolConfig with lwt_topic="custom/topic" → uses "custom/topic" as-is.
- `test_lwt_topic_foodbev` — with line_id="foodbev1" and empty lwt_topic → `collatr/factory/foodbev1/status`.
- `test_both_configs_different_lwt` — load both YAML configs, resolve LWT topics, verify they differ.

## STOPPING RULES

**After completing ONE task:** Output `TASK_COMPLETE` and stop immediately.
Do not look for the next task. Do not start another task.

**If a test cannot pass after 3 genuine attempts:** STOP. Document the issue in `plans/phase-6e-progress.md`. Output `TASK_BLOCKED: <reason>` and stop.

**Dependency check:** If the first `"passes": false` task has unsatisfied dependencies, find the next task whose dependencies are all satisfied. If NO tasks are eligible, output `PHASE_BLOCKED: waiting on <task IDs>` and stop.

## COMPLETION

When ALL tasks in the task JSON have `"passes": true`:
1. Push all commits.
2. Output: PHASE_COMPLETE

**Phase 6e is the final remediation phase.** After completion, all 27 YELLOW issues from the code review have been resolved. The 21 GREEN issues are documented in the review files and deferred.
