# Phase 0: Validation Spikes - Progress

## Status: In Progress

## Tasks
- [x] 0.1: Project scaffolding
- [ ] 0.2: Spike: Multi-server pymodbus
- [ ] 0.3: Spike: Mosquitto sidecar + paho-mqtt
- [ ] 0.4: Spike: asyncua multiple instances
- [ ] 0.5: Document spike results

## Task 0.1: Project Scaffolding

**Completed:** 2026-03-02

**Created:**
- `pyproject.toml` -- project metadata, pytest config (`asyncio_mode = "auto"`, `integration` marker), ruff config (py312, line-length 100, select E/W/F/I/UP/B/SIM/RUF), mypy config (strict, ignore missing imports for pymodbus/asyncua/paho/uvloop)
- `requirements.txt` -- production deps: pymodbus, asyncua, paho-mqtt, numpy, pydantic, pyyaml, uvloop (linux-only)
- `requirements-dev.txt` -- includes requirements.txt + pytest, pytest-asyncio, hypothesis, ruff, mypy
- `src/factory_simulator/__init__.py` -- package init with `__version__`
- `tests/` -- conftest.py + `__init__.py` in tests/, unit/, integration/, spikes/
- `.gitignore` -- Python, IDE, testing, Docker artifacts

**Verified:**
- `ruff check src tests` -- All checks passed
- `mypy src` -- Success: no issues found in 1 source file
- `pytest` -- discovers test directories (0 items collected, no errors)

**Decisions:**
- All tool config consolidated in `pyproject.toml` (no separate ruff.toml, mypy.ini, etc.)
- Using `src/factory_simulator/` layout per PRD appendix-e (not flat `src/` with `__init__.py`)
- uvloop dependency conditional on `sys_platform == "linux"` per PRD 7.5 platform note

## Notes

_(Updated by the implementation agent as work progresses)_
