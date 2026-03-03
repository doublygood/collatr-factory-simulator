"""Checkweigher and metal detection (QC) equipment generator.

The checkweigher verifies every pack is within weight tolerance.
The metal detector scans for contaminants.  Combined, they produce 6 signals.

Per-item generation (PRD 4.6):
- ``actual_weight`` updates on each item arrival by reading
  ``filler.fill_weight`` from the store and adding a tray+lid weight offset.
- ``overweight_count`` increments when actual_weight exceeds the upper limit.
- ``underweight_count`` increments when actual_weight falls below the lower limit.
- ``metal_detect_trips`` increments with a rare per-item Bernoulli probability.
- ``reject_total`` is the running total of all reject types.
- ``throughput`` mirrors ``filler.line_speed`` from the store.
- Between item arrivals all per-item values hold their last value.

Weight thresholds (from equipment config):
- ``overweight_threshold_g``: actual > fill_target + tray_weight + threshold → overweight
- ``underweight_threshold_g``: actual < fill_target + tray_weight - threshold → underweight

PRD Reference: Section 2b.6 (Checkweigher and Metal Detection), Section 4.6
CLAUDE.md Rule 6: All models use sim_time, never wall clock.
CLAUDE.md Rule 9: No locks (single-threaded asyncio).
CLAUDE.md Rule 12: No global state.
CLAUDE.md Rule 13: numpy.random.Generator with SeedSequence.
"""

from __future__ import annotations

import numpy as np

from factory_simulator.config import EquipmentConfig, SignalConfig
from factory_simulator.generators.base import EquipmentGenerator
from factory_simulator.models.base import clamp
from factory_simulator.models.noise import NoiseGenerator
from factory_simulator.store import SignalStore, SignalValue

# Filler state that means "Running" (PRD 2b.4)
_FILLER_RUNNING_STATE = 2

# Default configuration values
_DEFAULT_TRAY_WEIGHT_G = 10.0
_DEFAULT_OVERWEIGHT_THRESHOLD_G = 30.0
_DEFAULT_UNDERWEIGHT_THRESHOLD_G = 15.0
# Metal detector trips: < 1 per 1000 packs (PRD 2b.6)
_DEFAULT_METAL_DETECT_PROB = 0.001

# Counter rollover limits (PRD 2b.6 range bounds)
_OVERWEIGHT_MAX = 9999.0
_UNDERWEIGHT_MAX = 9999.0
_METAL_TRIPS_MAX = 99.0
_REJECT_TOTAL_MAX = 9999.0


def _float_extra(extras: dict[str, object], key: str, default: float) -> float:
    raw = extras.get(key, default)
    if raw is None:
        return default
    return float(raw)  # type: ignore[arg-type]


class CheckweigherGenerator(EquipmentGenerator):
    """Checkweigher and metal detection QC generator -- 6 signals.

    Signals
    -------
    actual_weight       Measured pack weight (OPC-UA, per-item)
    overweight_count    Packs above upper weight limit (OPC-UA, counter)
    underweight_count   Packs below lower weight limit (OPC-UA, counter)
    metal_detect_trips  Metal detection rejects (OPC-UA, counter)
    throughput          Items checked per minute (OPC-UA)
    reject_total        Total QC rejects all causes (OPC-UA, counter)

    Parameters
    ----------
    equipment_id:
        Equipment prefix, typically ``"qc"``.
    config:
        QC equipment config from YAML.
    rng:
        numpy random Generator (from SeedSequence).
    """

    def __init__(
        self,
        equipment_id: str,
        config: EquipmentConfig,
        rng: np.random.Generator,
    ) -> None:
        super().__init__(equipment_id, config, rng)

        extras = config.model_extra or {}
        self._tray_weight: float = _float_extra(
            extras, "tray_weight_g", _DEFAULT_TRAY_WEIGHT_G
        )
        self._overweight_threshold: float = _float_extra(
            extras, "overweight_threshold_g", _DEFAULT_OVERWEIGHT_THRESHOLD_G
        )
        self._underweight_threshold: float = _float_extra(
            extras, "underweight_threshold_g", _DEFAULT_UNDERWEIGHT_THRESHOLD_G
        )
        self._metal_detect_prob: float = _float_extra(
            extras, "metal_detect_prob", _DEFAULT_METAL_DETECT_PROB
        )

        # Per-item timing state
        self._time_since_last_item: float = 0.0

        # Initial actual_weight: read from signal config target if available
        self._last_actual_weight: float = 410.0  # fill_target(400) + tray(10) typical
        actual_weight_cfg = config.signals.get("actual_weight")
        if actual_weight_cfg is not None and actual_weight_cfg.params:
            target = actual_weight_cfg.params.get("target")
            if target is not None:
                self._last_actual_weight = float(target)

        # Discrete counters (cumulative per-item increments, never per-tick floats)
        self._overweight_count: float = 0.0
        self._underweight_count: float = 0.0
        self._metal_detect_trips: float = 0.0
        self._reject_total: float = 0.0

        # Optional noise for actual_weight and throughput
        self._actual_weight_noise: NoiseGenerator | None = (
            self._make_noise(actual_weight_cfg)
            if actual_weight_cfg is not None and actual_weight_cfg.noise_sigma > 0.0
            else None
        )
        throughput_cfg = config.signals.get("throughput")
        self._throughput_noise: NoiseGenerator | None = (
            self._make_noise(throughput_cfg)
            if throughput_cfg is not None and throughput_cfg.noise_sigma > 0.0
            else None
        )
        self._throughput_cfg: SignalConfig | None = throughput_cfg
        self._actual_weight_cfg: SignalConfig | None = actual_weight_cfg

    # -- Public properties (for scenarios and tests) --------------------------

    @property
    def overweight_count(self) -> float:
        """Current overweight pack count."""
        return self._overweight_count

    @property
    def underweight_count(self) -> float:
        """Current underweight pack count."""
        return self._underweight_count

    @property
    def metal_detect_trips(self) -> float:
        """Current metal detection trip count."""
        return self._metal_detect_trips

    @property
    def reject_total(self) -> float:
        """Total QC reject count (all causes)."""
        return self._reject_total

    @property
    def last_actual_weight(self) -> float:
        """Last measured actual pack weight (g)."""
        return self._last_actual_weight

    @property
    def tray_weight(self) -> float:
        """Tray + lid weight offset (g)."""
        return self._tray_weight

    @property
    def overweight_threshold(self) -> float:
        """Overweight threshold above fill_target+tray (g)."""
        return self._overweight_threshold

    @property
    def underweight_threshold(self) -> float:
        """Underweight threshold below fill_target+tray (g)."""
        return self._underweight_threshold

    # -- EquipmentGenerator interface -----------------------------------------

    def get_signal_ids(self) -> list[str]:
        """Return all 6 QC signal IDs."""
        return [self._signal_id(name) for name in self._signal_configs]

    def generate(
        self,
        sim_time: float,
        dt: float,
        store: SignalStore,
    ) -> list[SignalValue]:
        """Generate all QC signals for one tick.

        Generation order:
        1. Read filler state and speed from store.
        2. Compute weight thresholds using filler.fill_target from store.
        3. Per-item logic: advance timer, draw new weight on item arrival.
        4. Throughput mirrors filler line_speed.
        5. Build and return SignalValue list.
        """
        # --- 1. Read filler state and speed from store ---
        filler_state = int(store.get_value("filler.state", 0))
        is_running = filler_state == _FILLER_RUNNING_STATE

        line_speed = float(store.get_value("filler.line_speed", 0.0))

        # --- 2. Weight thresholds from filler.fill_target ---
        fill_target = float(
            store.get_value("filler.fill_target", self._last_actual_weight - self._tray_weight)
        )
        nominal_actual = fill_target + self._tray_weight
        overweight_limit = nominal_actual + self._overweight_threshold
        underweight_limit = nominal_actual - self._underweight_threshold

        # --- 3. Per-item logic ---
        if is_running and line_speed > 0.0:
            item_interval = 60.0 / line_speed
            self._time_since_last_item += dt

            if self._time_since_last_item >= item_interval:
                # Carry remainder so timing stays accurate across ticks
                self._time_since_last_item -= item_interval

                # Read latest fill weight from filler
                fill_weight = float(store.get_value("filler.fill_weight", fill_target))

                # Compute actual weight: fill + tray + measurement noise
                raw_actual = fill_weight + self._tray_weight
                if self._actual_weight_noise is not None:
                    raw_actual += self._actual_weight_noise.sample()

                # Apply clamp from signal config
                if self._actual_weight_cfg is not None:
                    raw_actual = clamp(
                        raw_actual,
                        self._actual_weight_cfg.min_clamp,
                        self._actual_weight_cfg.max_clamp,
                    )
                self._last_actual_weight = raw_actual

                # Overweight / underweight classification
                if raw_actual > overweight_limit:
                    self._overweight_count = min(
                        self._overweight_count + 1.0, _OVERWEIGHT_MAX
                    )
                    self._reject_total = min(
                        self._reject_total + 1.0, _REJECT_TOTAL_MAX
                    )
                elif raw_actual < underweight_limit:
                    self._underweight_count = min(
                        self._underweight_count + 1.0, _UNDERWEIGHT_MAX
                    )
                    self._reject_total = min(
                        self._reject_total + 1.0, _REJECT_TOTAL_MAX
                    )

                # Metal detection: rare per-item Bernoulli (PRD 2b.6: < 1 per 1000)
                if self._rng.random() < self._metal_detect_prob:
                    self._metal_detect_trips = min(
                        self._metal_detect_trips + 1.0, _METAL_TRIPS_MAX
                    )
                    self._reject_total = min(
                        self._reject_total + 1.0, _REJECT_TOTAL_MAX
                    )
        else:
            # Not running: reset item timer, hold all per-item values
            self._time_since_last_item = 0.0

        # --- 4. Throughput mirrors filler line_speed ---
        # Only clamp to the signal bounds when running; 0.0 is valid when inactive.
        if is_running and line_speed > 0.0:
            throughput = line_speed
            if self._throughput_noise is not None:
                throughput += self._throughput_noise.sample()
            if self._throughput_cfg is not None:
                throughput = clamp(
                    throughput,
                    self._throughput_cfg.min_clamp,
                    self._throughput_cfg.max_clamp,
                )
        else:
            throughput = 0.0

        # --- 5. Build results ---
        return [
            self._make_sv("actual_weight", self._last_actual_weight, sim_time),
            self._make_sv("overweight_count", self._overweight_count, sim_time),
            self._make_sv("underweight_count", self._underweight_count, sim_time),
            self._make_sv("metal_detect_trips", self._metal_detect_trips, sim_time),
            self._make_sv("throughput", throughput, sim_time),
            self._make_sv("reject_total", self._reject_total, sim_time),
        ]

    # -- Helper ---------------------------------------------------------------

    def _make_sv(
        self,
        signal_name: str,
        value: float,
        sim_time: float,
    ) -> SignalValue:
        """Create a SignalValue with fully qualified signal ID."""
        return SignalValue(
            signal_id=self._signal_id(signal_name),
            value=value,
            timestamp=sim_time,
            quality="good",
        )
