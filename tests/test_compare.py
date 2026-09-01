"""Paired comparison: the statistics, and the pairing that justifies them."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from smtsim.cli import main
from smtsim.compare import compare, seed_sequence
from smtsim.config import SECONDS_PER_MINUTE, load_line_config
from smtsim.stats import (
    mean,
    paired_interval,
    regularised_incomplete_beta,
    sample_stdev,
    student_t_cdf,
    student_t_ppf,
)

BASELINE = Path("configs/baseline.toml")
TWO_PLACERS = Path("configs/two_placers.toml")
SHIFT = 480 * SECONDS_PER_MINUTE


# --- the statistics, checked against values that can be looked up ----------


@pytest.mark.parametrize(
    ("df", "expected"),
    [(1, 12.7062), (2, 4.3027), (5, 2.5706), (10, 2.2281), (29, 2.0452), (100, 1.9840)],
)
def test_t_quantiles_match_a_published_table(df: int, expected: float) -> None:
    assert student_t_ppf(0.975, df) == pytest.approx(expected, abs=5e-5)


def test_the_t_distribution_approaches_the_normal_for_large_df() -> None:
    assert student_t_ppf(0.975, 100000) == pytest.approx(1.959964, abs=1e-4)


def test_the_t_cdf_is_a_distribution_function() -> None:
    assert student_t_cdf(0.0, 10) == pytest.approx(0.5)
    assert student_t_cdf(-2.2281, 10) == pytest.approx(0.025, abs=1e-5)
    assert student_t_cdf(2.2281, 10) == pytest.approx(0.975, abs=1e-5)
    assert student_t_cdf(-40.0, 3) < student_t_cdf(-1.0, 3) < student_t_cdf(1.0, 3)


def test_the_cdf_and_quantile_are_inverses() -> None:
    for probability in (0.01, 0.25, 0.5, 0.75, 0.99):
        assert student_t_cdf(student_t_ppf(probability, 7), 7) == pytest.approx(
            probability, abs=1e-9
        )


def test_the_incomplete_beta_matches_known_values() -> None:
    assert regularised_incomplete_beta(1.0, 1.0, 0.3) == pytest.approx(0.3)
    assert regularised_incomplete_beta(2.0, 3.0, 0.5) == pytest.approx(0.6875)
    assert regularised_incomplete_beta(0.5, 0.5, 0.5) == pytest.approx(0.5)
    assert regularised_incomplete_beta(3.0, 2.0, 0.0) == 0.0
    assert regularised_incomplete_beta(3.0, 2.0, 1.0) == 1.0


def test_mean_and_stdev_match_hand_arithmetic() -> None:
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]

    assert mean(values) == pytest.approx(5.0)
    assert sample_stdev(values) == pytest.approx(math.sqrt(32.0 / 7.0))


def test_a_paired_interval_matches_an_example_computed_by_hand() -> None:
    """baseline [1,2,3,4], variant [2,4,4,6] -> differences [1,2,1,2].

    mean 1.5; sample sd sqrt(1/3) = 0.5773503; standard error 0.2886751;
    t(0.975, df=3) = 3.182446; margin 0.918687. So the interval is
    [0.581313, 2.418687].
    """
    interval = paired_interval([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 4.0, 6.0])

    assert interval.n == 4
    assert interval.mean_difference == pytest.approx(1.5)
    assert interval.standard_error == pytest.approx(0.2886751, abs=1e-6)
    assert interval.low == pytest.approx(0.581313, abs=1e-5)
    assert interval.high == pytest.approx(2.418687, abs=1e-5)
    assert interval.excludes_zero


def test_an_interval_that_straddles_zero_is_reported_as_such() -> None:
    interval = paired_interval([10.0, 11.0, 12.0, 13.0], [11.0, 10.0, 13.0, 12.0])

    assert not interval.excludes_zero
    assert interval.low < 0 < interval.high


def test_identical_samples_give_a_zero_width_interval_at_zero() -> None:
    interval = paired_interval([1.0, 2.0, 3.0], [1.0, 2.0, 3.0])

    assert interval.mean_difference == 0.0
    assert interval.low == 0.0 and interval.high == 0.0
    assert not interval.excludes_zero


def test_a_constant_shift_is_detected_with_no_uncertainty() -> None:
    interval = paired_interval([1.0, 5.0, 9.0], [3.0, 7.0, 11.0])

    assert interval.mean_difference == pytest.approx(2.0)
    assert interval.low == pytest.approx(2.0)
    assert interval.excludes_zero


def test_the_interval_narrows_as_pairs_are_added() -> None:
    small = paired_interval([1.0, 2.0, 3.0, 4.0], [2.0, 4.0, 3.0, 6.0])
    large = paired_interval([1.0, 2.0, 3.0, 4.0] * 10, [2.0, 4.0, 3.0, 6.0] * 10)

    assert large.high - large.low < small.high - small.low
    assert large.mean_difference == pytest.approx(small.mean_difference)


@pytest.mark.parametrize(
    ("baseline", "variant"),
    [([1.0], [2.0]), ([1.0, 2.0], [3.0])],
)
def test_degenerate_paired_samples_are_rejected(baseline, variant) -> None:
    with pytest.raises(ValueError):
        paired_interval(baseline, variant)


# --- the comparison itself -------------------------------------------------


def test_a_configuration_compared_against_itself_shows_no_difference() -> None:
    """The strongest check on the pairing: identical configs must cancel exactly.

    Without common random numbers the two arms would draw different samples and
    this would only be approximately zero.
    """
    config = load_line_config(BASELINE)
    result = compare(config, config, seed_sequence(5), SHIFT, warmup_seconds=1800.0)

    for metric in result.metrics:
        assert metric.interval.mean_difference == 0.0
        assert metric.baseline_mean == pytest.approx(metric.variant_mean)
        assert not metric.interval.excludes_zero


def test_a_second_placement_head_shortens_cycle_time() -> None:
    result = compare(
        load_line_config(BASELINE),
        load_line_config(TWO_PLACERS),
        seed_sequence(12),
        SHIFT,
        warmup_seconds=1800.0,
    )
    cycle_time = result.metric("mean_cycle_time_minutes")

    assert cycle_time.interval.mean_difference < 0
    assert cycle_time.interval.excludes_zero
    assert cycle_time.is_improvement


def test_the_comparison_is_reproducible() -> None:
    baseline, variant = load_line_config(BASELINE), load_line_config(TWO_PLACERS)
    first = compare(baseline, variant, seed_sequence(4), SHIFT)
    second = compare(baseline, variant, seed_sequence(4), SHIFT)

    assert first.to_dict() == second.to_dict()


def test_every_seed_is_run_by_both_configurations() -> None:
    result = compare(
        load_line_config(BASELINE), load_line_config(TWO_PLACERS), seed_sequence(6), SHIFT
    )
    seeds = tuple(range(1, 7))

    assert result.seeds == seeds
    assert tuple(run.seed for run in result.baseline_runs) == seeds
    assert tuple(run.seed for run in result.variant_runs) == seeds


def test_the_run_hook_observes_every_run_without_changing_the_result() -> None:
    baseline, variant = load_line_config(BASELINE), load_line_config(TWO_PLACERS)
    seen: list[tuple[int, int]] = []
    without = compare(baseline, variant, seed_sequence(3), SHIFT)
    with_hook = compare(
        baseline, variant, seed_sequence(3), SHIFT, on_run=lambda done, total: seen.append((done, total))
    )

    assert seen == [(1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]
    assert without.to_dict() == with_hook.to_dict()


def test_a_comparison_needs_at_least_two_seeds() -> None:
    with pytest.raises(ValueError):
        seed_sequence(1)


# --- the command line ------------------------------------------------------


def test_the_compare_command_writes_json_and_prints_a_summary(tmp_path: Path, capsys) -> None:
    out = tmp_path / "comparison.json"
    code = main(
        ["compare", str(BASELINE), str(TWO_PLACERS), "--seeds", "5", "--minutes", "120",
         "--warmup", "20", "--out", str(out), "--quiet"]
    )
    captured = capsys.readouterr().out

    assert code == 0
    assert "Paired difference" in captured
    assert "95% CI" in captured

    payload = json.loads(out.read_text())
    assert payload["seeds"] == [1, 2, 3, 4, 5]
    assert payload["minutes"] == pytest.approx(120.0)
    assert payload["warmup_minutes"] == pytest.approx(20.0)
    assert {metric["metric"] for metric in payload["metrics"]} == {
        "throughput_per_hour",
        "mean_cycle_time_minutes",
    }
    assert len(payload["per_seed"]["baseline"]) == 5
    assert all("excludes_zero" in metric["interval"] for metric in payload["metrics"])


def test_a_separated_result_still_disclaims_commercial_significance(capsys) -> None:
    """The wording matters: statistical separation is not business value."""
    main(
        ["compare", str(BASELINE), str(TWO_PLACERS), "--seeds", "12", "--minutes", "480",
         "--warmup", "30", "--quiet"]
    )
    captured = capsys.readouterr().out

    assert "clear of zero" in captured
    assert "worth paying for" in captured


def test_an_unseparated_result_is_not_reported_as_no_difference(capsys) -> None:
    """Spanning zero means these runs did not separate them, not that they match."""
    main(
        ["compare", str(BASELINE), str(BASELINE), "--seeds", "5", "--minutes", "120", "--quiet"]
    )
    captured = capsys.readouterr().out

    assert "spans zero" in captured
    assert "not evidence they are the same" in captured


def test_verbose_shows_the_per_seed_table(tmp_path: Path, capsys) -> None:
    main(
        ["compare", str(BASELINE), str(TWO_PLACERS), "--seeds", "3", "--minutes", "60",
         "--verbose", "--quiet"]
    )
    captured = capsys.readouterr().out

    assert "Per seed" in captured

    capsys.readouterr()
    main(["compare", str(BASELINE), str(TWO_PLACERS), "--seeds", "3", "--minutes", "60", "--quiet"])
    assert "Per seed" not in capsys.readouterr().out


def test_one_seed_is_rejected_by_the_command(capsys) -> None:
    assert main(["compare", str(BASELINE), str(TWO_PLACERS), "--seeds", "1", "--quiet"]) == 2
