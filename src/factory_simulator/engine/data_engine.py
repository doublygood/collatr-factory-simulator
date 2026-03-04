"""Data engine -- the simulation heartbeat.

The DataEngine owns the simulation clock, signal store, and equipment
generators.  On each tick it advances the clock, runs generators whose
sample interval has elapsed, and writes results to the store.

**Tick atomicity (Rule 8):** ``tick()`` is synchronous.  All signal
updates for one tick complete before yielding control.  Protocol readers
never see a mix of old and new tick values.

**Single writer (Rule 9):** Only the engine writes to the store.

**Deterministic (Rule 13):** When a seed is configured, the engine
produces identical output on the same platform.

PRD Reference: Section 8.2 (Data Flow), Section 8.3 (Concurrency Model)
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import numpy as np

from factory_simulator.clock import SimulationClock
from factory_simulator.engine.data_quality import DataQualityInjector
from factory_simulator.engine.ground_truth import GroundTruthLogger
from factory_simulator.engine.scenario_engine import ScenarioEngine
from factory_simulator.generators.base import EquipmentGenerator
from factory_simulator.generators.checkweigher import CheckweigherGenerator
from factory_simulator.generators.chiller import ChillerGenerator
from factory_simulator.generators.cip import CipGenerator
from factory_simulator.generators.coder import CoderGenerator
from factory_simulator.generators.energy import EnergyGenerator
from factory_simulator.generators.environment import EnvironmentGenerator
from factory_simulator.generators.filler import FillerGenerator
from factory_simulator.generators.laminator import LaminatorGenerator
from factory_simulator.generators.mixer import MixerGenerator
from factory_simulator.generators.oven import OvenGenerator
from factory_simulator.generators.press import PressGenerator
from factory_simulator.generators.sealer import SealerGenerator
from factory_simulator.generators.slitter import SlitterGenerator
from factory_simulator.generators.vibration import VibrationGenerator
from factory_simulator.store import SignalStore

if TYPE_CHECKING:
    from factory_simulator.config import EquipmentConfig, FactoryConfig
    from factory_simulator.models.counter import CounterModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Generator registry -- maps equipment type strings to generator classes
# ---------------------------------------------------------------------------

_GENERATOR_REGISTRY: dict[str, type[EquipmentGenerator]] = {
    "flexographic_press": PressGenerator,
    "solvent_free_laminator": LaminatorGenerator,
    "slitter_rewinder": SlitterGenerator,
    "cij_printer": CoderGenerator,
    "iolink_sensor": EnvironmentGenerator,
    "power_meter": EnergyGenerator,
    "wireless_vibration": VibrationGenerator,
    "high_shear_mixer": MixerGenerator,
    "tunnel_oven": OvenGenerator,
    "gravimetric_filler": FillerGenerator,
    "tray_sealer": SealerGenerator,
    "checkweigher": CheckweigherGenerator,
    "cold_room": ChillerGenerator,
    "cip_skid": CipGenerator,
}


def _min_sample_interval_s(eq_cfg: EquipmentConfig, default_ms: int) -> float:
    """Return the fastest sample interval (seconds) of any signal in *eq_cfg*.

    Falls back to *default_ms* if no signal defines ``sample_rate_ms``.
    """
    rates = [
        s.sample_rate_ms
        for s in eq_cfg.signals.values()
        if s.sample_rate_ms is not None
    ]
    if not rates:
        return default_ms / 1000.0
    return min(rates) / 1000.0


# ---------------------------------------------------------------------------
# DataEngine
# ---------------------------------------------------------------------------


class DataEngine:
    """Central simulation engine.

    Parameters
    ----------
    config:
        Validated :class:`FactoryConfig`.
    store:
        Shared :class:`SignalStore` instance.
    clock:
        :class:`SimulationClock` instance.  If *None* one is created from
        *config.simulation*.
    """

    def __init__(
        self,
        config: FactoryConfig,
        store: SignalStore,
        clock: SimulationClock | None = None,
        ground_truth: GroundTruthLogger | None = None,
    ) -> None:
        self._config = config
        self._store = store
        self._clock = (
            clock if clock is not None
            else SimulationClock.from_config(config.simulation)
        )
        self._running = False
        self._ground_truth = ground_truth

        # Master RNG -- child rngs are spawned per generator (Rule 13)
        # Use SeedSequence hierarchy for proper statistical independence (Y1 fix)
        seed = config.simulation.random_seed
        self._root_ss = (
            np.random.SeedSequence(seed) if seed is not None
            else np.random.SeedSequence()
        )
        self._root_rng = np.random.default_rng(self._root_ss)

        # Generator bookkeeping
        self._generators: list[EquipmentGenerator] = []
        self._gen_intervals: list[float] = []   # min sample interval (s)
        self._gen_last_time: list[float] = []   # sim_time of last generation

        self._build_generators()

        # Precompute counter models per generator (PRD 10.4, Task 4.15)
        # Apply DataQualityConfig.counter_rollover overrides after generators build.
        self._gen_counter_models: list[dict[str, CounterModel]] = []
        flat_counter_map: dict[str, CounterModel] = {}
        for gen in self._generators:
            cm = gen.get_counter_models()
            self._gen_counter_models.append(cm)
            flat_counter_map.update(cm)
        for sig_id, rollover_val in config.data_quality.counter_rollover.items():
            if sig_id in flat_counter_map:
                flat_counter_map[sig_id].set_rollover_value(rollover_val)

        # Scenario engine (PRD 8.2 step 3: evaluated before generators)
        # Y1: Use SeedSequence.spawn for child RNG
        # Y3: Pass sim_duration_s from config when set
        scenario_rng = np.random.default_rng(self._root_ss.spawn(1)[0])
        self._scenario_engine = ScenarioEngine(
            scenarios_config=config.scenarios,
            shifts_config=config.shifts,
            rng=scenario_rng,
            sim_duration_s=(
                config.simulation.sim_duration_s
                if config.simulation.sim_duration_s is not None
                else 8 * 3600  # default: one shift
            ),
            ground_truth=ground_truth,
        )

        # Data quality injector (PRD 8.2: runs after generators, before protocol reads)
        # Collect all signal IDs from enabled generators
        all_signal_ids: list[str] = []
        for gen in self._generators:
            all_signal_ids.extend(gen.get_signal_ids())

        disconnect_rng = np.random.default_rng(self._root_ss.spawn(1)[0])
        stuck_rng = np.random.default_rng(self._root_ss.spawn(1)[0])
        self._data_quality = DataQualityInjector(
            config.data_quality, all_signal_ids, disconnect_rng, stuck_rng
        )

    # -- Properties -----------------------------------------------------------

    @property
    def clock(self) -> SimulationClock:
        """The simulation clock."""
        return self._clock

    @property
    def store(self) -> SignalStore:
        """The signal value store."""
        return self._store

    @property
    def generators(self) -> list[EquipmentGenerator]:
        """Ordered list of equipment generators."""
        return list(self._generators)

    @property
    def scenario_engine(self) -> ScenarioEngine:
        """The scenario engine."""
        return self._scenario_engine

    @property
    def ground_truth(self) -> GroundTruthLogger | None:
        """The ground truth event logger, if configured."""
        return self._ground_truth

    @property
    def data_quality(self) -> DataQualityInjector:
        """The data quality injector (sensor disconnect + stuck sensor)."""
        return self._data_quality

    @property
    def running(self) -> bool:
        """Whether the async run loop is active."""
        return self._running

    # -- Generator construction -----------------------------------------------

    def _build_generators(self) -> None:
        """Instantiate generators from config.equipment.

        Generators are ordered by the config dict iteration order, which
        should list primary equipment first (press) then dependents.
        """
        default_ms = self._config.simulation.tick_interval_ms

        for eq_id, eq_cfg in self._config.equipment.items():
            if not eq_cfg.enabled:
                logger.debug("Skipping disabled equipment: %s", eq_id)
                continue

            gen_class = _GENERATOR_REGISTRY.get(eq_cfg.type)
            if gen_class is None:
                logger.warning(
                    "No generator registered for equipment type %r (id=%s)",
                    eq_cfg.type,
                    eq_id,
                )
                continue

            # Spawn an isolated child RNG per generator (Rule 13, Y1 fix)
            child_rng = np.random.default_rng(self._root_ss.spawn(1)[0])

            gen = gen_class(eq_id, eq_cfg, child_rng)
            self._generators.append(gen)
            self._gen_intervals.append(
                _min_sample_interval_s(eq_cfg, default_ms)
            )
            # -inf ensures every generator runs on the first tick
            self._gen_last_time.append(-float("inf"))

            logger.info(
                "Registered generator: %s (%s) with %d signals",
                eq_id,
                eq_cfg.type,
                len(gen.get_signal_ids()),
            )

    # -- Tick -----------------------------------------------------------------

    def tick(self) -> float:
        """Advance the simulation by one tick.

        This method is **synchronous** -- no ``await`` between signal
        updates (Rule 8: engine atomicity).  All generators that are due
        to run produce their values, which are written to the store before
        this method returns.

        Returns
        -------
        float
            The new simulated time in seconds.
        """
        sim_time = self._clock.tick()
        dt = self._clock.dt

        # PRD 8.2 step 3: evaluate scenarios before generators
        self._scenario_engine.tick(sim_time, dt, self)

        for i, gen in enumerate(self._generators):
            interval = self._gen_intervals[i]
            if sim_time - self._gen_last_time[i] >= interval:
                results = gen.generate(sim_time, dt, self._store)
                for sv in results:
                    self._store.set(
                        sv.signal_id, sv.value, sv.timestamp, sv.quality,
                    )
                # Log counter rollover events (PRD 10.4, Task 4.15)
                if self._ground_truth is not None:
                    for sig_id, counter in self._gen_counter_models[i].items():
                        if counter.rollover_occurred:
                            self._ground_truth.log_counter_rollover(
                                sim_time,
                                sig_id,
                                counter.rollover_value or 0.0,
                                counter.value,
                            )
                self._gen_last_time[i] = sim_time

        # Post-generator injection: active scenarios that override store values
        # (e.g. contextual anomalies, PRD 5.16, Task 4.6)
        self._scenario_engine.post_gen_tick(sim_time, dt, self._store)

        # Data quality injection: runs after all generator and scenario writes,
        # before protocol servers read the store (PRD 8.2, Task 4.12)
        self._data_quality.tick(sim_time, self._store, self._ground_truth)

        return sim_time

    # -- Async run loop -------------------------------------------------------

    async def run(self) -> None:
        """Run the simulation loop until cancelled.

        Ticks the engine at ``tick_interval_ms`` wall-clock intervals.
        The simulated time advances by ``dt`` (= tick_interval_ms *
        time_scale) per tick.

        Stop by cancelling the task or calling :meth:`stop`.
        """
        sleep_s = self._clock.tick_interval_ms / 1000.0
        self._running = True
        logger.info(
            "DataEngine started: tick=%dms, time_scale=%.1fx, generators=%d",
            self._clock.tick_interval_ms,
            self._clock.time_scale,
            len(self._generators),
        )

        try:
            while self._running:
                self.tick()
                await asyncio.sleep(sleep_s)
        except asyncio.CancelledError:
            logger.info("DataEngine cancelled")
            raise
        finally:
            self._running = False
            logger.info("DataEngine stopped at sim_time=%.3fs", self._clock.sim_time)

    def stop(self) -> None:
        """Signal the run loop to stop after the current tick."""
        self._running = False

    # -- Introspection --------------------------------------------------------

    def signal_count(self) -> int:
        """Total number of signal IDs across all generators."""
        return sum(len(g.get_signal_ids()) for g in self._generators)
