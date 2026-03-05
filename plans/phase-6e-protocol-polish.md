# Phase 6e: Protocol Polish

**Scope:** Final YELLOW protocol issues Y25-Y26 from the three-reviewer code review.
**Depends on:** Phase 6d complete.

---

## Task 6e.1 — OPC-UA AccessLevel=0 for Inactive Profile Nodes

**Review ref:** Y25 (review-protocol-fidelity.md §3.9)

**Problem:** PRD Section 3.2.1 states: "Nodes for the inactive profile report StatusCode.BadNotReadable and have AccessLevel set to 0." Currently, in collapsed mode, only the active profile's nodes are created. The inactive profile's nodes don't exist in the address space at all, so an OPC-UA client browsing the server won't discover them.

**Scope:** This only applies to **collapsed mode** (single OPC-UA server serving all signals). In realistic mode, each OPC-UA server is scoped to its own equipment subtree and there is no concept of an inactive profile on that server.

**Fix:**

1. The `OpcuaServer` needs to know what the "other" profile's signals look like. Add an optional `inactive_config: FactoryConfig | None` parameter to `OpcuaServer.__init__()`.

2. In `_build_node_tree()`, after building all active nodes, if `inactive_config` is not None, iterate through its equipment/signals and create variable nodes for each signal that has an `opcua_node`:
   - Same folder hierarchy creation pattern (reuse `folder_cache`).
   - Set `AccessLevel` to 0 (no read, no write): use `await var_node.write_attribute(ua.AttributeIds.AccessLevel, ua.DataValue(ua.Variant(0, ua.VariantType.Byte)))`.
   - Write initial value with StatusCode `BadNotReadable`.
   - Add EURange, EngineeringUnits, MinimumSamplingInterval as for active nodes.
   - Do NOT add these nodes to `self._nodes` or `self._node_to_signal` — they must not participate in the sync loop.

3. In `cli.py` (or wherever collapsed-mode OPC-UA servers are created):
   - Determine which profile is active (packaging or F&B).
   - Load the other profile's YAML config.
   - Pass the inactive config to OpcuaServer as `inactive_config`.

   **Implementation detail:** The CLI already knows which config file is loaded. For the inactive config, either:
   - (a) Accept a `--inactive-config` CLI arg, or
   - (b) Use a convention: if active config is `factory.yaml`, inactive is `factory-foodbev.yaml` and vice versa, or
   - (c) Add an `inactive_config_path` field to the main config, or
   - (d) Load both configs at startup and determine active/inactive from the profile name.

   **Simplest approach:** Option (b) with a mapping in the code. Or option (d) if both configs are always present.

4. **In realistic mode:** Skip entirely — `inactive_config` stays `None`.

**Tests:**
- `test_inactive_profile_nodes_exist` — in collapsed mode with packaging active, verify `FoodBevLine.*` nodes exist in the address space.
- `test_inactive_profile_access_level_zero` — read AccessLevel attribute of an inactive node, verify it's 0.
- `test_inactive_profile_status_bad` — read an inactive node's value, verify StatusCode is BadNotReadable.
- `test_inactive_nodes_not_synced` — verify inactive nodes are not in the sync loop (value doesn't change after ticks).
- `test_realistic_mode_no_inactive_nodes` — in realistic mode, verify no inactive nodes are created.

**Files:** `src/factory_simulator/protocols/opcua_server.py`, `src/factory_simulator/cli.py`, `tests/unit/test_protocols/test_opcua.py`

---

## Task 6e.2 — Profile-Specific LWT Topic

**Review ref:** Y26 (review-protocol-fidelity.md §4.5)

**Problem:** Both profiles use `lwt_topic: "collatr/factory/status"`. If both run simultaneously (future dual-profile mode), their LWT messages would conflict on the same topic.

**Fix:**

1. Change the default `lwt_topic` in `MqttProtocolConfig` to empty string:
   ```python
   lwt_topic: str = ""  # Empty means auto-generated from topic_prefix/line_id/status
   ```

2. In the MQTT publisher startup (where `will_set()` is called), if `lwt_topic` is empty, generate it:
   ```python
   lwt_topic = f"{self._mqtt_cfg.topic_prefix}/{self._mqtt_cfg.line_id}/status"
   ```
   This produces `collatr/factory/packaging1/status` or `collatr/factory/foodbev1/status`.

3. If `lwt_topic` is explicitly set (non-empty) in config, use it as-is (backward compat).

4. Update both YAML config files — remove the explicit `lwt_topic` lines (let auto-generation kick in), or update them to profile-specific paths.

**Tests:**
- `test_lwt_topic_auto_generated` — when lwt_topic is empty, verify LWT topic includes line_id.
- `test_lwt_topic_explicit` — when lwt_topic is set explicitly, verify it's used as-is.
- `test_lwt_topic_differs_between_profiles` — packaging and F&B configs produce different LWT topics.

**Files:** `src/factory_simulator/config.py`, `src/factory_simulator/protocols/mqtt_publisher.py`, `config/factory.yaml`, `config/factory-foodbev.yaml`, `tests/unit/test_protocols/test_mqtt.py`

---

## Task 6e.3 — Validate All Fixes — Full Suite

**Depends on:** Tasks 6e.1-6e.2

**Steps:**
1. Run `ruff check src tests` — must be clean.
2. Run `mypy src` — must pass.
3. Run `pytest` — ALL tests must pass.
4. Run batch sim with both profiles.
5. Fix any failures.

**Files:** None (validation only).

---

## Dependencies

```
6e.1 (inactive profile nodes)  → independent
6e.2 (LWT topic)               → independent
6e.3 (validation)              → depends on 6e.1 + 6e.2
```

## Effort Estimate

- 6e.1: ~60 min (OPC-UA inactive profile nodes — significant work)
- 6e.2: ~20 min (LWT topic auto-generation)
- 6e.3: ~15 min (run suite)
- **Total: ~1.5 hours**

## Completion Note

Phase 6e completes the entire code review remediation. All 54 issues (6 RED, 27 YELLOW, 21 GREEN) will have been addressed:
- **6 RED:** Fixed in Phase 6a
- **27 YELLOW:** Fixed across Phases 6a-6e (Y1-Y27, minus Y24 fixed in 6a)
- **21 GREEN:** Noted, deferred (documented in review files)
