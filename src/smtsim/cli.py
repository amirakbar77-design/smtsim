"""Command-line entry point.

This module owns all of the program's I/O: argument parsing, the event log file,
the progress display and the summary tables. The simulation core underneath it
does none of those things.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from smtsim import __version__
from smtsim.config import DEFAULT_LINE, SECONDS_PER_MINUTE, LineConfig, load_line_config
from smtsim.events import open_jsonl, read_jsonl
from smtsim.line import Line
from smtsim.stats import LineStats, summarise

DEFAULT_MINUTES = 480.0
DEFAULT_SEED = 42


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="smtsim",
        description="Discrete-event simulation of an SMT PCB assembly line.",
    )
    parser.add_argument("--version", action="version", version=f"smtsim {__version__}")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="simulate a line and write an event log")
    run.add_argument(
        "--minutes",
        type=float,
        default=DEFAULT_MINUTES,
        help=f"length of the simulated shift in minutes (default: {DEFAULT_MINUTES:g})",
    )
    run.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help=f"random seed; the same seed always produces the same log (default: {DEFAULT_SEED})",
    )
    run.add_argument(
        "--out",
        type=Path,
        default=Path("runs/run1.jsonl"),
        help="path to write the JSONL event log to (default: runs/run1.jsonl)",
    )
    run.add_argument(
        "--config",
        type=Path,
        default=None,
        help="line configuration file (.toml, .json, or .yaml with the 'yaml' extra)",
    )
    run.add_argument("--quiet", action="store_true", help="suppress the progress display")

    stats = subcommands.add_parser("stats", help="summarise a saved event log")
    stats.add_argument("log", type=Path, help="path to a JSONL event log")

    return parser


def render_summary(console: Console, stats: LineStats) -> None:
    """Print the line and per-station tables."""
    line_table = Table(title=f"{stats.line_name} — {stats.horizon_minutes:.0f} min shift")
    line_table.add_column("metric", style="cyan", no_wrap=True)
    line_table.add_column("value", justify="right", style="bold")

    line_table.add_row("boards completed", f"{stats.boards_completed}")
    line_table.add_row("throughput", f"{stats.throughput_per_hour:.1f} boards/hour")
    line_table.add_row("mean cycle time", f"{stats.mean_cycle_time_minutes:.2f} min")
    line_table.add_row("p95 cycle time", f"{stats.p95_cycle_time_minutes:.2f} min")
    line_table.add_row("boards arrived", f"{stats.boards_arrived}")
    line_table.add_row("still in system", f"{stats.boards_in_system}")
    if stats.seed is not None:
        line_table.add_row("seed", f"{stats.seed}")

    station_table = Table(title="Stations")
    station_table.add_column("station", style="cyan", no_wrap=True)
    station_table.add_column("cap", justify="right")
    station_table.add_column("utilisation", justify="right")
    station_table.add_column("max queue", justify="right")
    station_table.add_column("wait", justify="right")
    station_table.add_column("service", justify="right")

    bottleneck = stats.bottleneck
    for station in stats.stations:
        is_bottleneck = bottleneck is not None and station.name == bottleneck.name
        style = "bold red" if is_bottleneck else None
        label = f"{station.name} ←" if is_bottleneck else station.name
        station_table.add_row(
            label,
            f"{station.capacity}",
            f"{station.utilisation:.1%}",
            f"{station.max_queue_length}",
            f"{station.mean_wait_seconds:.1f} s",
            f"{station.mean_service_seconds:.1f} s",
            style=style,
        )

    console.print(line_table)
    console.print(station_table)
    if bottleneck is not None:
        console.print(
            f"[dim]Bottleneck: [/dim][bold red]{bottleneck.name}[/bold red]"
            f"[dim] at {bottleneck.utilisation:.1%} utilisation.[/dim]"
        )


def load_config(path: Path | None) -> LineConfig:
    return DEFAULT_LINE if path is None else load_line_config(path)


def command_run(args: argparse.Namespace, console: Console) -> int:
    if args.minutes <= 0:
        console.print("[red]--minutes must be positive[/red]")
        return 2

    config = load_config(args.config).with_seed(args.seed)
    horizon = args.minutes * SECONDS_PER_MINUTE

    with open_jsonl(args.out) as sink:
        line = Line.build(config, sink)
        if args.quiet:
            line.run(horizon)
        else:
            columns = (
                TextColumn("[bold blue]{task.description}"),
                BarColumn(),
                TaskProgressColumn(),
                TextColumn("[dim]{task.fields[clock]}[/dim]"),
                TimeElapsedColumn(),
            )
            with Progress(*columns, console=console, transient=False) as progress:
                task = progress.add_task(
                    f"simulating {config.name}",
                    total=horizon,
                    clock="0 / %d min" % args.minutes,
                )

                def on_progress(now: float) -> None:
                    progress.update(
                        task,
                        completed=now,
                        clock=f"{now / SECONDS_PER_MINUTE:.0f} / {args.minutes:.0f} min",
                    )

                line.run(horizon, on_progress=on_progress)

    console.print(f"[dim]Wrote event log to[/dim] {args.out}")
    render_summary(console, summarise(read_jsonl(args.out)))
    return 0


def command_stats(args: argparse.Namespace, console: Console) -> int:
    if not args.log.exists():
        console.print(f"[red]no such event log:[/red] {args.log}")
        return 1
    render_summary(console, summarise(read_jsonl(args.log)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()

    try:
        if args.command == "run":
            return command_run(args, console)
        if args.command == "stats":
            return command_stats(args, console)
    except (ValueError, OSError) as exc:
        console.print(f"[red]error:[/red] {exc}")
        return 1

    parser.error(f"unknown command {args.command!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
