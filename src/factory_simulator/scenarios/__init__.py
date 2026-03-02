"""Scenario system for the Collatr Factory Simulator.

Scenarios are time-bounded events that override normal signal generation.
They inject anomalies, operational events, and degradation patterns into
the data stream.

PRD Reference: Section 5.1 (Overview), Section 5.13 (Scheduling)
"""

from factory_simulator.scenarios.base import Scenario, ScenarioPhase

__all__ = ["Scenario", "ScenarioPhase"]
