"""Command-line interface for the Collatr Factory Simulator.

Usage
-----
Start simulator (default: collapsed, real-time, packaging):
    python -m factory_simulator run

Batch mode (7 days, 100x, CSV output):
    python -m factory_simulator run \\
        --batch-output ./output \\
        --batch-duration 7d \\
        --batch-format csv \\
        --time-scale 100 \\
        --seed 42

Evaluate detections against ground truth:
    python -m factory_simulator evaluate \\
        --ground-truth output/ground_truth.jsonl \\
        --detections output/detections.csv

Print version:
    python -m factory_simulator version

PRD Reference: Appendix F (Phase 5 — CLI and Productisation)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from factory_simulator.config import FactoryConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


def parse_duration(duration: str) -> float:
    """Parse a human-readable duration string into seconds.

    Supported suffixes:
    - ``d`` — days  (``7d`` → 604800.0)
    - ``h`` — hours (``24h`` → 86400.0)
    - ``m`` — minutes (``30m`` → 1800.0)
    - ``s`` — seconds (``60s`` → 60.0)
    - bare number → interpreted as seconds (``3600`` → 3600.0)

    Raises
    ------
    ValueError
        If the string cannot be parsed.
    """
    s = duration.strip()
    if not s:
        raise ValueError(f"Empty duration string: {duration!r}")
    multipliers = {"d": 86400.0, "h": 3600.0, "m": 60.0, "s": 1.0}
    suffix = s[-1]
    if suffix in multipliers:
        try:
            return float(s[:-1]) * multipliers[suffix]
        except ValueError:
            raise ValueError(f"Invalid duration: {duration!r}") from None
    try:
        return float(s)
    except ValueError:
        raise ValueError(f"Invalid duration: {duration!r}") from None


# ---------------------------------------------------------------------------
# Profile → bundled config path
# ---------------------------------------------------------------------------


def _default_config_path(profile: str) -> Path:
    """Return the bundled config file path for the given profile.

    Parameters
    ----------
    profile:
        ``"packaging"`` (default) or ``"foodbev"``.
    """
    config_dir = Path(__file__).resolve().parent.parent.parent / "config"
    if profile == "foodbev":
        return config_dir / "factory-foodbev.yaml"
    return config_dir / "factory.yaml"


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def _setup_logging(level: str) -> None:
    """Configure the root logger to the given level."""
    numeric = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(name)-30s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )


# ---------------------------------------------------------------------------
# Version helpers
# ---------------------------------------------------------------------------


def _version_string() -> str:
    from factory_simulator import __version__

    return f"factory-simulator {__version__}"


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Build and return the top-level argument parser."""
    parser = argparse.ArgumentParser(
        prog="factory-simulator",
        description="Collatr Factory Simulator — industrial protocol signal generation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=_version_string(),
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    # Note: subparsers.required left as False so --version/-V still works alone.

    _add_run_subcommand(subparsers)
    _add_evaluate_subcommand(subparsers)
    _add_version_subcommand(subparsers)

    return parser


def _add_run_subcommand(subparsers: Any) -> None:
    """Register the 'run' subcommand parser."""
    p = subparsers.add_parser(
        "run",
        help="Start the factory simulator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--config",
        "-c",
        metavar="FILE",
        help="Path to YAML config file (default: bundled profile config)",
    )
    p.add_argument(
        "--profile",
        choices=["packaging", "foodbev"],
        default="packaging",
        help="Factory profile: packaging (default) or foodbev",
    )
    p.add_argument(
        "--seed",
        type=int,
        metavar="N",
        help="Random seed for reproducible runs (Rule 13)",
    )
    p.add_argument(
        "--time-scale",
        type=float,
        metavar="X",
        dest="time_scale",
        help="Simulation speed multiplier (1.0 = real-time, 100 = batch)",
    )
    p.add_argument(
        "--batch-output",
        metavar="DIR",
        dest="batch_output",
        help="Output directory for batch signal files; enables batch mode",
    )
    p.add_argument(
        "--batch-duration",
        metavar="DURATION",
        dest="batch_duration",
        help="Simulated duration for batch mode (e.g. 7d, 24h, 3600)",
    )
    p.add_argument(
        "--batch-format",
        choices=["csv", "parquet"],
        default="csv",
        dest="batch_format",
        help="Batch output format: csv (default) or parquet",
    )
    p.add_argument(
        "--network-mode",
        choices=["collapsed", "realistic"],
        default="collapsed",
        dest="network_mode",
        help="Network topology: collapsed (default) or realistic",
    )
    p.add_argument(
        "--ground-truth-path",
        metavar="FILE",
        dest="ground_truth_path",
        help=(
            "Ground truth JSONL output path "
            "(default: <batch-output>/ground_truth.jsonl in batch mode, "
            "./ground_truth.jsonl otherwise)"
        ),
    )
    p.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warn", "warning", "error", "critical"],
        dest="log_level",
        help="Log level (default: info)",
    )


def _add_evaluate_subcommand(subparsers: Any) -> None:
    """Register the 'evaluate' subcommand parser."""
    p = subparsers.add_parser(
        "evaluate",
        help="Evaluate anomaly detection results against ground truth",
    )
    p.add_argument(
        "--ground-truth",
        required=True,
        metavar="FILE",
        dest="ground_truth",
        help=(
            "Ground truth JSONL file produced by the simulator; "
            "comma-separated list for multi-seed mode"
        ),
    )
    p.add_argument(
        "--detections",
        required=True,
        metavar="FILE",
        help=(
            "Detection alerts CSV from the anomaly detector under test; "
            "comma-separated list for multi-seed mode"
        ),
    )
    p.add_argument(
        "--pre-margin",
        type=float,
        default=30.0,
        metavar="SECONDS",
        dest="pre_margin",
        help="Pre-event tolerance window in seconds (default: 30)",
    )
    p.add_argument(
        "--post-margin",
        type=float,
        default=60.0,
        metavar="SECONDS",
        dest="post_margin",
        help="Post-event tolerance window in seconds (default: 60)",
    )
    p.add_argument(
        "--output",
        metavar="FILE",
        help="Write the text report to this file in addition to stdout",
    )


def _add_version_subcommand(subparsers: Any) -> None:
    """Register the 'version' subcommand parser."""
    subparsers.add_parser("version", help="Print version and exit")


# ---------------------------------------------------------------------------
# 'version' subcommand handler
# ---------------------------------------------------------------------------


def version_command() -> int:
    """Print version string and return 0."""
    print(_version_string())
    return 0


# ---------------------------------------------------------------------------
# 'evaluate' subcommand handler (delegates to evaluation.cli)
# ---------------------------------------------------------------------------


def evaluate_command(args: argparse.Namespace) -> int:
    """Handle the 'evaluate' subcommand. Returns exit code (0 / 1)."""
    from factory_simulator.evaluation.cli import evaluate_command as _eval_cmd

    return _eval_cmd(args)


# ---------------------------------------------------------------------------
# Config assembly helpers
# ---------------------------------------------------------------------------


def _load_config(args: argparse.Namespace) -> FactoryConfig:
    """Load and patch a FactoryConfig from CLI arguments."""
    from factory_simulator.config import BatchOutputConfig, NetworkConfig, load_config

    config_path_str: str | None = getattr(args, "config", None)
    profile: str = getattr(args, "profile", "packaging")

    config_path = Path(config_path_str) if config_path_str else _default_config_path(profile)
    config = load_config(config_path)

    # Override simulation parameters
    seed: int | None = getattr(args, "seed", None)
    if seed is not None:
        config.simulation.random_seed = seed

    time_scale: float | None = getattr(args, "time_scale", None)
    if time_scale is not None:
        config.simulation.time_scale = time_scale

    log_level: str = getattr(args, "log_level", "info")
    config.simulation.log_level = log_level

    # Network mode override
    network_mode: str = getattr(args, "network_mode", "collapsed")
    if network_mode == "realistic":
        if config.network is None:
            config.network = NetworkConfig(mode="realistic")
        else:
            # model_copy preserves all other fields
            config.network = config.network.model_copy(update={"mode": "realistic"})

    # Batch output
    batch_output_dir: str | None = getattr(args, "batch_output", None)
    batch_duration_str: str | None = getattr(args, "batch_duration", None)
    batch_format: str = getattr(args, "batch_format", "csv")

    if batch_output_dir is not None:
        config.batch_output = BatchOutputConfig(
            format=batch_format,  # type: ignore[arg-type]
            path=str(batch_output_dir),
            event_driven_signals=config.batch_output.event_driven_signals,
        )

    if batch_duration_str is not None:
        config.simulation.sim_duration_s = parse_duration(batch_duration_str)

    return config


# ---------------------------------------------------------------------------
# 'run' subcommand — async helpers
# ---------------------------------------------------------------------------


async def _run_batch(engine: Any, sim_duration_s: float | None) -> int:
    """Run engine at high speed without live protocol servers.

    Called in batch mode (``--batch-output`` specified or ``time_scale >= 50``
    with a finite duration).  Runs ticks as fast as possible.  Stops when
    ``sim_duration_s`` is reached (or the process is interrupted).
    """
    try:
        while True:
            sim_time: float = engine.tick()
            if sim_duration_s is not None and sim_time >= sim_duration_s:
                logger.info("Batch run complete: simulated %.1fs", sim_time)
                break
            # Yield control to the event loop between ticks so Ctrl-C is responsive
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        pass
    finally:
        if engine.batch_writer is not None:
            engine.batch_writer.close()
    return 0


async def _run_realtime(config: FactoryConfig, engine: Any) -> int:
    """Run engine with live Modbus / OPC-UA / MQTT protocol servers.

    Starts enabled protocol servers as concurrent asyncio tasks, then runs
    the engine until cancelled (Ctrl-C / SIGINT).  Servers are shut down
    in reverse start order on exit.  A lightweight health server is also
    started on port 8080 for Docker health checks.
    """
    from factory_simulator.health.server import HealthServer

    servers: list[Any] = []
    tasks: list[asyncio.Task[None]] = []

    health = HealthServer(port=8080, store=engine.store)
    health.update(profile=config.factory.name)
    health_task: asyncio.Task[None] = asyncio.create_task(health.start())
    await asyncio.sleep(0.05)  # allow health server to bind

    try:
        if config.protocols.modbus.enabled:
            for srv in engine.create_modbus_servers():
                task: asyncio.Task[None] = asyncio.create_task(srv.start())
                tasks.append(task)
                servers.append(srv)
                await asyncio.sleep(0.05)  # allow server to bind
            health.update(modbus="up")

        if config.protocols.opcua.enabled:
            for srv in engine.create_opcua_servers():
                task = asyncio.create_task(srv.start())
                tasks.append(task)
                servers.append(srv)
                await asyncio.sleep(0.05)
            health.update(opcua="up")

        if config.protocols.mqtt.enabled:
            for mqtt in engine.create_mqtt_publishers():
                task = asyncio.create_task(mqtt.start())
                tasks.append(task)
                servers.append(mqtt)
            health.update(mqtt="up")

        health.update(status="running")

        logger.info(
            "Simulator running: profile=%s time_scale=%.1fx",
            config.factory.name,
            config.simulation.time_scale,
        )
        await engine.run()

    except asyncio.CancelledError:
        pass
    finally:
        health.update(status="stopping")
        engine.stop()
        for srv in reversed(servers):
            with contextlib.suppress(Exception):
                await srv.stop()
        for task in tasks:
            if not task.done():
                task.cancel()
        health_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await health_task

    return 0


async def _async_run(args: argparse.Namespace) -> int:
    """Load config, build components, and dispatch to batch or real-time mode."""
    from factory_simulator.engine.data_engine import DataEngine
    from factory_simulator.engine.ground_truth import GroundTruthLogger
    from factory_simulator.output.writer import BatchWriter
    from factory_simulator.store import SignalStore

    config = _load_config(args)

    _setup_logging(config.simulation.log_level)

    # Network topology: pass the NetworkConfig (not full FactoryConfig)
    topology = None
    if config.network is not None:
        from factory_simulator.topology import NetworkTopologyManager

        profile_str = getattr(args, "profile", "packaging")
        from typing import Literal

        topo_profile: Literal["packaging", "food_bev"] = (
            "food_bev" if profile_str == "foodbev" else "packaging"
        )
        topology = NetworkTopologyManager(config.network, profile=topo_profile)

    # Batch writer
    batch_writer: BatchWriter | None = None
    if config.batch_output.format != "none":
        from factory_simulator.config import BatchOutputConfig
        from factory_simulator.output.writer import CsvWriter, ParquetWriter

        out_dir = Path(config.batch_output.path)
        out_dir.mkdir(parents=True, exist_ok=True)
        batch_cfg = BatchOutputConfig(
            format=config.batch_output.format,
            path=str(out_dir),
            buffer_size=config.batch_output.buffer_size,
            event_driven_signals=config.batch_output.event_driven_signals,
        )
        if config.batch_output.format == "parquet":
            batch_writer = ParquetWriter(out_dir, batch_cfg)
        else:
            batch_writer = CsvWriter(out_dir, batch_cfg)

    # Ground truth logger — always created so scenario events are recorded.
    # Path resolution: explicit arg > batch-output dir > cwd.
    gt_path_override: str | None = getattr(args, "ground_truth_path", None)
    if gt_path_override is not None:
        gt_path = Path(gt_path_override)
    elif config.batch_output.format != "none":
        gt_path = Path(config.batch_output.path) / "ground_truth.jsonl"
    else:
        gt_path = Path("ground_truth.jsonl")

    ground_truth = GroundTruthLogger(gt_path)
    ground_truth.open()
    ground_truth.write_header(config)

    store = SignalStore()
    engine = DataEngine(
        config, store,
        topology=topology,
        batch_writer=batch_writer,
        ground_truth=ground_truth,
    )

    sim_duration_s = config.simulation.sim_duration_s
    is_batch_mode = config.batch_output.format != "none"

    try:
        if is_batch_mode or sim_duration_s is not None:
            return await _run_batch(engine, sim_duration_s)
        else:
            return await _run_realtime(config, engine)
    finally:
        ground_truth.close()


# ---------------------------------------------------------------------------
# 'run' subcommand handler (sync entry point)
# ---------------------------------------------------------------------------


def run_command(args: argparse.Namespace) -> int:
    """Execute the 'run' subcommand. Returns exit code."""
    try:
        return asyncio.run(_async_run(args))
    except KeyboardInterrupt:
        print("\nSimulator stopped.", file=sys.stderr)
        return 0


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the appropriate subcommand.

    Parameters
    ----------
    argv:
        Argument list.  When *None*, uses ``sys.argv[1:]``.

    Returns
    -------
    int
        Exit code (0 = success, non-zero = error).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    command: str | None = args.command

    if command == "run":
        return run_command(args)
    elif command == "evaluate":
        return evaluate_command(args)
    elif command == "version":
        return version_command()
    else:
        parser.print_help()
        return 0
