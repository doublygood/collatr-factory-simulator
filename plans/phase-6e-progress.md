# Phase 6e: Protocol Polish — Progress

## Status: IN PROGRESS

## Tasks
- [x] 6e.1: OPC-UA AccessLevel=0 for Inactive Profile Nodes (Y25)
- [ ] 6e.2: Profile-Specific LWT Topic (Y26)
- [ ] 6e.3: Validate All Fixes — Full Suite

## Task 6e.1 Notes

**What was done:**
- Added `inactive_config: FactoryConfig | None = None` parameter to `OpcuaServer.__init__()`.
- Added `_build_inactive_nodes(ns, folder_cache)` method: iterates inactive profile's equipment signals, creates OPC-UA variable nodes with `AccessLevel=0` and initial value with `StatusCode.BadNotReadable`. Also adds EURange, EngineeringUnits, and MinimumSamplingInterval. Does NOT add to `self._nodes`/`self._node_to_signal` — sync loop never touches them.
- Guard: `_build_inactive_nodes` only called when `self._node_tree_root == ""` (collapsed mode); realistic mode skips it.
- Added `inactive_config` parameter to `DataEngine.__init__()`, stored as `self._inactive_config`, passed to `OpcuaServer` in collapsed mode `create_opcua_servers()`.
- Updated `cli.py _async_run()` to determine inactive profile from `--profile` arg, load its config, pass to `DataEngine`.
- Tests: 11 tests in `tests/unit/test_protocols/test_opcua_inactive.py` covering: node existence, AccessLevel=0, BadNotReadable status, no sync (not in `server.nodes`), no inactive when `inactive_config=None`, realistic mode skips, both profiles have different OPC-UA roots.
- Full suite: 3160 passed.

## Notes

Both tasks are independent. 6e.3 depends on both.

Phase 6e completes all YELLOW issue remediation from the three-reviewer code review.
