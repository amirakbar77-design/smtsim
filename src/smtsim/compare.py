"""Paired what-if comparison of two line configurations.

Two configurations are run across the same set of seeds. Because each station
draws from its own named stream, seed *k* gives both runs identical randomness
everywhere the two configurations agree, so the pair of results for a seed
differ only by the change under test. The differences are therefore analysed
pairwise, which removes the seed-to-seed variation the two runs share.

This module orchestrates; it does not compute statistics (that is `stats.py`)
and it does not print (that is `cli.py`). It writes no files: like the rest of
the core it collects events into an in-memory sink and reduces them with the
same `summarise` that `smtsim stats` uses on a saved log.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from smtsim.config import SECONDS_PER_MINUTE, LineConfig
from smtsim.events import ListSink
from smtsim.line import simulate
from smtsim.stats import LineStats, PairedInterval, mean, paired_interval, summarise

RunHook = Callable[[int, int], None]

DEFAULT_SEEDS = 30


@dataclass(frozen=True, slots=True)
class Metric:
    """One reported quantity, and how to pull it out of a run's stats."""

    key: str
    label: str
    unit: str
    extract: Callable[[LineStats], float]
    higher_is_better: bool


METRICS: tuple[Metric, ...] = (
    Metric(
        key="throughput_per_hour",
        label="throughput",
        unit="boards/h",
        extract=lambda stats: stats.throughput_per_hour,
        higher_is_better=True,
    ),
    Metric(
        key="mean_cycle_time_minutes",
        label="mean cycle time",
        unit="min",
        extract=lambda stats: stats.mean_cycle_time_minutes,
        higher_is_better=False,
    ),
)


@dataclass(frozen=True, slots=True)
class SeedResult:
    """What one configuration produced under one seed."""

    seed: int
    values: dict[str, float]

    def to_dict(self) -> dict[str, Any]:
        return {"seed": self.seed, **self.values}


@dataclass(frozen=True, slots=True)
class MetricComparison:
    """One metric, compared across the two configurations."""

    key: str
    label: str
    unit: str
    higher_is_better: bool
    baseline_mean: float
    variant_mean: float
    interval: PairedInterval

    @property
    def is_improvement(self) -> bool:
        moved_up = self.interval.mean_difference > 0
        return moved_up if self.higher_is_better else not moved_up

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.key,
            "unit": self.unit,
            "baseline_mean": self.baseline_mean,
            "variant_mean": self.variant_mean,
            "higher_is_better": self.higher_is_better,
            "interval": self.interval.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Comparison:
    """The full result of a paired what-if run."""

    baseline_name: str
    variant_name: str
    seeds: tuple[int, ...]
    horizon_seconds: float
    warmup_seconds: float
    baseline_runs: tuple[SeedResult, ...]
    variant_runs: tuple[SeedResult, ...]
    metrics: tuple[MetricComparison, ...]

    def metric(self, key: str) -> MetricComparison:
        for metric in self.metrics:
            if metric.key == key:
                return metric
        raise KeyError(key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline_name,
            "variant": self.variant_name,
            "seeds": list(self.seeds),
            "minutes": self.horizon_seconds / SECONDS_PER_MINUTE,
            "warmup_minutes": self.warmup_seconds / SECONDS_PER_MINUTE,
            "metrics": [metric.to_dict() for metric in self.metrics],
            "per_seed": {
                "baseline": [run.to_dict() for run in self.baseline_runs],
                "variant": [run.to_dict() for run in self.variant_runs],
            },
        }


def seed_sequence(count: int, start: int = 1) -> tuple[int, ...]:
    """The seeds a comparison uses, so a run can be reproduced exactly."""
    if count < 2:
        raise ValueError("a paired comparison needs at least two seeds")
    return tuple(range(start, start + count))


def run_once(
    config: LineConfig,
    seed: int,
    horizon_seconds: float,
    warmup_seconds: float,
) -> SeedResult:
    """Simulate one configuration under one seed and extract the metrics."""
    sink = ListSink()
    simulate(horizon_seconds, config=config, sink=sink, seed=seed, warmup_seconds=warmup_seconds)
    stats = summarise(sink.events)
    return SeedResult(seed=seed, values={metric.key: metric.extract(stats) for metric in METRICS})


def compare(
    baseline: LineConfig,
    variant: LineConfig,
    seeds: Sequence[int],
    horizon_seconds: float,
    warmup_seconds: float = 0.0,
    confidence: float = 0.95,
    on_run: RunHook | None = None,
) -> Comparison:
    """Run both configurations across ``seeds`` and analyse the paired differences.

    ``on_run`` is called with ``(runs_finished, runs_total)`` after each
    simulation. Like the progress hook on `Line.run` it is pure observation:
    it draws no random numbers and touches no state.
    """
    if not seeds:
        raise ValueError("a comparison needs at least one seed")

    total = 2 * len(seeds)
    finished = 0
    baseline_runs: list[SeedResult] = []
    variant_runs: list[SeedResult] = []

    for seed in seeds:
        for config, results in ((baseline, baseline_runs), (variant, variant_runs)):
            results.append(run_once(config, seed, horizon_seconds, warmup_seconds))
            finished += 1
            if on_run is not None:
                on_run(finished, total)

    metrics = tuple(
        MetricComparison(
            key=metric.key,
            label=metric.label,
            unit=metric.unit,
            higher_is_better=metric.higher_is_better,
            baseline_mean=mean([run.values[metric.key] for run in baseline_runs]),
            variant_mean=mean([run.values[metric.key] for run in variant_runs]),
            interval=paired_interval(
                [run.values[metric.key] for run in baseline_runs],
                [run.values[metric.key] for run in variant_runs],
                confidence=confidence,
            ),
        )
        for metric in METRICS
    )

    return Comparison(
        baseline_name=baseline.name,
        variant_name=variant.name,
        seeds=tuple(seeds),
        horizon_seconds=horizon_seconds,
        warmup_seconds=warmup_seconds,
        baseline_runs=tuple(baseline_runs),
        variant_runs=tuple(variant_runs),
        metrics=metrics,
    )
