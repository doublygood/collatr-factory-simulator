# Collatr Factory Simulator: Development Workflow

## Overview

The Factory Simulator uses the same **multi-agent phased development workflow** proven during the CollatrEdge build (11 phases, 1003 tests, 134 PRD review issues resolved). The workflow is adapted for Python and the simulator's specific architecture.

### The Agents

| Agent | Environment | Role |
|---|---|---|
| **Claude Code** (local) | Lee's machine, interactive + headless | Implementation. Writes code, tests, reviews. Runs the Ralph Wiggum loop. |
| **Dex** (OpenClaw) | Cloud-hosted, persistent | Architecture, planning, independent review, quality gate. Writes phase plans and PROMPT files. Spawns sub-agents for code review. |

### Key Principle: Separation of Concerns

The agent that writes the code **never** reviews its own work as the final gate. Claude Code does an internal review (sub-agent within Claude Code), then Dex does an independent review from a completely fresh context. This two-layer review consistently catches issues the implementation agent missed during the CollatrEdge build.

---

## Phase Lifecycle

Each phase follows this lifecycle. The entire Factory Simulator (Phases 0-5) is built by repeating this cycle.

```
+-------------------------------------------------------------+
|                    PHASE N LIFECYCLE                         |
|                                                             |
|  1. PRD REVIEW & PLANNING          (Dex)                   |
|  2. PLAN + PROMPT CREATION         (Dex -> push to git)    |
|  3. IMPLEMENTATION LOOP            (Claude Code, headless)  |
|  4. INTERNAL CODE REVIEW           (Claude Code sub-agent)  |
|  5. INTERNAL FIX PASS              (Claude Code -> push)    |
|  6. INDEPENDENT REVIEW             (Dex sub-agent)          |
|  7. GO/NO-GO DECISION              (Dex)                    |
|  8. FINAL FIX PASS                 (Claude Code)            |
|  9. PHASE COMPLETE                 (both agents)            |
|                                                             |
|  ---- repeat for Phase N+1 ----                             |
+-------------------------------------------------------------+
```

---

## Step-by-Step

### Step 1: PRD Review & Planning (Dex)

**Who:** Dex
**Input:** PRD sections for this phase (in `prd/`), previous phase review artifacts, CLAUDE.md
**Output:** Phase plan document, task list JSON, progress file

Dex reads the relevant PRD sections, reviews the current codebase state (git log, test results, previous phase reviews), and writes a comprehensive implementation plan.

**Artifacts created:**
- `plans/phase-N-<name>.md` -- Detailed plan with module breakdown, test strategy, implementation notes, edge cases, risks, and acceptance criteria
- `plans/phase-N-tasks.json` -- Structured task list with ordered tasks, PRD references, implementation steps, and a `passes: false` flag per task
- `plans/phase-N-progress.md` -- Empty progress file that the implementation agent fills in as it works

**Quality bar:** The plan must be detailed enough that the implementation agent can work through tasks one-at-a-time without needing to make architectural decisions. Ambiguity in the plan causes implementation drift.

---

### Step 2: PROMPT Creation (Dex)

**Who:** Dex
**Input:** Phase plan
**Output:** Updated `PROMPT_build.md`

Dex updates `PROMPT_build.md`, the instruction file that drives Claude Code's headless loop. This file points to the phase plan, provides context about what exists, defines the single-task-per-session workflow, and includes completion signals.

Both the plan and PROMPT are committed and pushed to git so Claude Code picks them up.

---

### Step 3: Implementation Loop (Claude Code)

**Who:** Claude Code (local, headless via `ralph.sh`)
**Input:** `PROMPT_build.md`, phase plan, task list, CLAUDE.md
**Output:** Implemented code + tests, committed per-task

The Ralph Wiggum Loop (`ralph.sh`) runs Claude Code headlessly in a loop. Each iteration:

1. Claude Code reads `PROMPT_build.md`
2. Finds the first task with `passes: false` in the task JSON
3. Reads the relevant PRD sections
4. Implements the code and tests
5. Runs `pytest` -- **all** tests must pass (not just new ones)
6. Updates `passes: true` in the task JSON
7. Updates the progress file
8. Commits with format: `phase-N: <what> (task N.X)`
9. Outputs `TASK_COMPLETE` and STOPS immediately

**Key constraints:**
- **ONE TASK PER SESSION.** The agent completes exactly one task, commits, outputs `TASK_COMPLETE`, and stops. The loop script handles iteration by starting a fresh context.
- **All tests must pass.** No partial implementations.
- **Progress file must be updated.** Breadcrumbs for the next iteration and for reviewers.
- **3-attempt failure rule.** If a test cannot be fixed after 3 genuine attempts, STOP and document.
- **Internal review is part of the loop.** When all tasks pass, the PROMPT instructs the agent to spawn a code review sub-agent before declaring PHASE_COMPLETE.

---

### Step 4: Internal Code Review (Claude Code sub-agent)

**Who:** Claude Code spawns a sub-agent
**Input:** All source files changed in this phase, CLAUDE.md rules, PRD
**Output:** `plans/phase-N-review.md`

Review checks:
1. PRD compliance -- signal models match spec, register addresses match Appendix A, etc.
2. CLAUDE.md rules compliance
3. Error handling -- are exceptions handled? asyncio tasks awaited?
4. Test coverage of hard paths -- complex branches tested, not just happy paths
5. Concurrency correctness -- no await between signals within a tick, single-writer store
6. Config wiring -- are configurable values wired from Pydantic models, not hardcoded?

**Review output format:**
- Red: Must Fix -- blocks next phase
- Yellow: Should Fix -- should be addressed
- Green: Nice to Have -- suggestions
- PRD compliance table per module

---

### Step 5-9: Fix, Independent Review, GO/NO-GO, Final Fix, Complete

Same pattern as CollatrEdge. See the detailed steps in `repos/collatr-edge/plans/WORKFLOW.md` for the full description of each step. The key difference: independent review spawned by Dex verifies the work from completely fresh context.

---

## Technology-Specific Notes

### Python vs Bun

The CollatrEdge workflow used Bun + TypeScript. The Factory Simulator uses Python 3.12+ with asyncio. Key differences:

| Aspect | CollatrEdge (Bun) | Factory Simulator (Python) |
|---|---|---|
| Test runner | `bun test` | `pytest` with pytest-asyncio |
| Property-based testing | -- | Hypothesis |
| Linting | -- | ruff |
| Type checking | TypeScript compiler | mypy |
| CI command | `bun test` | `ruff check && mypy src && pytest` |
| Package manager | bun | pip + requirements.txt |
| Config validation | Zod | Pydantic |

### Docker Compose

The simulator runs as a Docker Compose stack (simulator + Mosquitto sidecar). Integration tests should test against the running compose stack. The `config/mosquitto.conf` file must exist before first `docker compose up`.

### asyncio Concurrency

All protocol servers (pymodbus, asyncua, paho-mqtt publisher) run in a single asyncio event loop. The engine updates all signals for one tick before yielding. No locks needed. This is the same single-writer pattern as CollatrEdge's Bun event loop, just in Python.

---

## Phases

The PRD (Appendix F) defines 6 phases over 13 weeks:

| Phase | Name | Weeks | Signals | Protocols |
|---|---|---|---|---|
| 0 | Validation Spikes | 2 days | -- | pymodbus, asyncua, Mosquitto |
| 1 | Core Engine + Modbus + Tests | 1-3 | 47 (packaging) | Modbus TCP |
| 2 | OPC-UA + MQTT + Scenarios | 4-5 | 47 (packaging) | All three |
| 3 | F&B Profile | 6-8 | 68 (F&B) | All three |
| 4 | Full Scenarios + Data Quality | 9-11 | Both profiles | All three |
| 5 | Topology + Evaluation + Polish | 12-13 | Both profiles | All three |

Each phase gets its own plan document, task JSON, progress file, and review artifacts.

---

## Artifacts Per Phase

| File | Created by | Purpose |
|---|---|---|
| `plans/phase-N-<name>.md` | Dex | Implementation plan |
| `plans/phase-N-tasks.json` | Dex | Structured task list with pass/fail |
| `plans/phase-N-progress.md` | Claude Code | Running log of what was built |
| `plans/phase-N-review.md` | Claude Code (sub-agent) | Internal code review |
| `plans/phase-N-independent-review.md` | Dex (sub-agent) | Independent review + GO/NO-GO |

---

## PROMPT_build.md Template

Every `PROMPT_build.md` **must** include these sections:

```markdown
## CRITICAL: ONE TASK PER SESSION

You MUST implement exactly ONE task per session, then STOP.

1. Read the phase plan
2. Find the **first** task with "passes": false in the task JSON
3. Implement ONLY that single task
4. Run tests: pytest -- ALL must pass
5. Update task JSON ("passes": true) and progress file
6. Commit: phase-N: <what> (task N.X)
7. Do NOT push
8. Output TASK_COMPLETE and STOP. Do NOT continue to the next task.

## STOPPING RULES

**After completing ONE task:** Output TASK_COMPLETE and stop immediately.
Do not look for the next task. Do not start another task.
The ralph.sh loop will call you again for the next iteration.

## COMPLETION

When ALL tasks in the task JSON have "passes": true:
1. Do NOT output PHASE_COMPLETE yet.
2. Spawn a sub-agent code review.
3. Write the review to plans/phase-N-review.md
4. Address all red Must Fix findings. Re-run pytest after each fix.
5. Commit fixes: phase-N: address code review findings
6. Push all commits.
7. THEN output: PHASE_COMPLETE
```

**Lessons from CollatrEdge that apply here:**
- ONE TASK PER SESSION needs forceful language and a separate STOPPING RULES section. Burying the stop signal in a numbered list does not work.
- The internal review step must be in the PROMPT. If omitted, it only happens when manually triggered.
- Each task should produce roughly one source module + one test module + one commit.
- Integration tests are separate tasks from implementation.

---

## Lessons Carried Forward from CollatrEdge

1. **One task per session prevents context bleed.** The agent stays focused and commits are atomic.
2. **Independent review consistently finds things internal review missed.** Different perspective, no implementation bias.
3. **Progress files bridge context windows.** When iteration 4 picks up from iteration 3, the progress file provides continuity.
4. **3-attempt failure rule prevents infinite loops.** Stop and document. Humans debug faster than agents going in circles.
5. **Review quality grading creates accountability.** Grade the reviewer, not just the code.
6. **Rules evolve based on real failures.** Add rules to CLAUDE.md when bugs are found. Each rule exists because of a real bug.
7. **Do not spawn parallel sub-agents pushing to the same repo.** Causes rebase conflicts.
8. **Never push from the local agent.** Token expiry causes push failures mid-loop. Push is handled by the loop script or manually.

---

## Quick Reference: Starting a New Phase

**Dex** (planning):
1. Pull latest from git
2. Run tests to confirm green baseline
3. Read PRD sections for the phase
4. Review previous phase's independent review for carried-forward items
5. Write `plans/phase-N-<name>.md`, `plans/phase-N-tasks.json`, `plans/phase-N-progress.md`
6. Update `PROMPT_build.md` for the new phase
7. Commit and push

**Lee** (running the local agent):
1. Pull latest (picks up Dex's plan + PROMPT)
2. Run `./ralph.sh 10` (or interactive: `claude` then follow PROMPT_build.md)
3. Monitor progress via git log or `plans/phase-N-progress.md`
4. When PHASE_COMPLETE: push to git, tell Dex to review

**Dex** (reviewing a completed phase):
1. Pull latest
2. Run tests to confirm green
3. Spawn sub-agent for independent review
4. Review findings, add own observations
5. Make GO/NO-GO decision
6. Commit review and push
7. If fixes needed: tell Lee, Claude Code fixes, re-verify
