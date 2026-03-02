"""Simulation engine for the Collatr Factory Simulator.

The DataEngine owns the clock, signal store, and equipment generators.
It drives the simulation loop: advance clock, run generators, update store.

PRD Reference: Section 8.2 (Data Flow), Section 8.3 (Concurrency Model)
"""

from factory_simulator.engine.data_engine import DataEngine

__all__ = ["DataEngine"]
