# Phase 6e: Protocol Polish — Progress

## Status: COMPLETE

## Tasks
- [x] 6e.1: OPC-UA AccessLevel=0 for Inactive Profile Nodes (Y25)
- [x] 6e.2: Profile-Specific LWT Topic (Y26)
- [x] 6e.3: Validate All Fixes — Full Suite

## Task 6e.1 Notes

**What was done:**
- Added `inactive_config: FactoryConfig | None = None` parameter to `OpcuaServer.__init__()`.
- Added `_build_inactive_nodes(ns, folder_cache)` method: iterates inactive profile's equipment signals, creates OPC-UA variable nodes with `AccessLevel=0` and initial value with `StatusCode.BadNotReadable`. Also adds EURange, EngineeringUnits, and MinimumSamplingInterval. Does NOT add to `self._nodes`/`self._node_to_signal` — sync loop never touches them.
- Guard: `_build_inactive_nodes` only called when `self._node_tree_root == ""` (collapsed mode); realistic mode skips it.
- Added `inactive_config` parameter to `DataEngine.__init__()`, stored as `self._inactive_config`, passed to `OpcuaServer` in collapsed mode `create_opcua_servers()`.
- Updated `cli.py _async_run()` to determine inactive profile from `--profile` arg, load its config, pass to `DataEngine`.
- Tests: 11 tests in `tests/unit/test_protocols/test_opcua_inactive.py` covering: node existence, AccessLevel=0, BadNotReadable status, no sync (not in `server.nodes`), no inactive when `inactive_config=None`, realistic mode skips, both profiles have different OPC-UA roots.
- Full suite: 3160 passed.

## Task 6e.2 Notes

**What was done:**
- Changed `MqttProtocolConfig.lwt_topic` default from `"collatr/factory/status"` to `""` (empty).
- Added `resolve_lwt_topic(mqtt_cfg)` pure function to `mqtt_publisher.py`: returns explicit topic if set, else auto-generates `{topic_prefix}/{line_id}/status`.
- Updated `_create_client()` to call `resolve_lwt_topic(self._mqtt_cfg)` instead of using `lwt_topic` directly.
- Removed explicit `lwt_topic: "collatr/factory/status"` lines from both `config/factory.yaml` and `config/factory-foodbev.yaml`; auto-generation now produces profile-specific topics (`collatr/factory/packaging1/status`, `collatr/factory/foodbev1/status`).
- Added `MqttProtocolConfig` to the `TYPE_CHECKING` import block for the type hint on `resolve_lwt_topic`.
- 6 tests in `tests/unit/test_protocols/test_mqtt_lwt.py` covering auto-generation, explicit override, both configs differing, and YAML configs having empty defaults.
- Full suite: 3166 passed.

## Task 6e.3 Notes

**What was done:**
- Ran `ruff check src tests && mypy src && pytest --tb=short -q`: **3166 passed**, 10 warnings, 4m00s. No regressions.
- Ran batch sim with packaging profile (10s): 7 generators, 48 signals, exit 0, signals.csv + ground_truth.jsonl written.
- Ran batch sim with F&B profile (10s): 10 generators, 68 signals, exit 0, signals.csv + ground_truth.jsonl written.

## Notes

Both tasks are independent. 6e.3 depends on both.

Phase 6e completes all YELLOW issue remediation from the three-reviewer code review.
All 27 YELLOW issues (Y1–Y27) resolved across phases 6a–6e.
