"""Configuration loading, validation and defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from smtsim.config import (
    BASELINE_LINE,
    DEFAULT_LINE,
    Constant,
    LogNormal,
    Triangular,
    distribution_from_dict,
    line_config_from_dict,
    load_line_config,
)

TOML_CONFIG = """
name = "test line"
seed = 7

[arrivals.interarrival]
kind = "constant"
seconds = 30.0

[[stations]]
name = "printer"
[stations.service_time]
kind = "lognormal"
mean = 20.0
cv = 0.2

[[stations]]
name = "oven"
capacity = 3
[stations.service_time]
kind = "triangular"
low = 100.0
mode = 110.0
high = 130.0
"""


def test_default_line_has_the_four_stations_in_process_order() -> None:
    assert [station.name for station in DEFAULT_LINE.stations] == [
        "solder_paste_printer",
        "pick_and_place",
        "spi",
        "reflow_oven",
    ]
    assert DEFAULT_LINE.station("reflow_oven").capacity > 1
    assert all(s.capacity == 1 for s in DEFAULT_LINE.stations if s.name != "reflow_oven")


def test_toml_config_round_trips_through_the_loader(tmp_path: Path) -> None:
    path = tmp_path / "line.toml"
    path.write_text(TOML_CONFIG)
    config = load_line_config(path)

    assert config.name == "test line"
    assert config.seed == 7
    assert config.arrivals.interarrival == Constant(seconds=30.0)
    assert config.station("printer").service_time == LogNormal(mean=20.0, cv=0.2)
    assert config.station("oven").capacity == 3
    assert config.station("oven").service_time == Triangular(low=100.0, mode=110.0, high=130.0)


def test_a_config_dict_survives_a_serialisation_round_trip() -> None:
    assert line_config_from_dict(DEFAULT_LINE.to_dict()) == DEFAULT_LINE


def test_with_seed_leaves_the_original_untouched() -> None:
    reseeded = DEFAULT_LINE.with_seed(99)

    assert reseeded.seed == 99
    assert DEFAULT_LINE.seed == 42
    assert reseeded.stations == DEFAULT_LINE.stations


@pytest.mark.parametrize(
    "spec",
    [
        {"kind": "nope"},
        {"mean": 1.0},
        {"kind": "lognormal", "mean": 1.0},
        {"kind": "lognormal", "mean": -1.0, "cv": 0.1},
        {"kind": "triangular", "low": 5.0, "mode": 1.0, "high": 9.0},
    ],
)
def test_bad_distribution_specs_are_rejected(spec: dict) -> None:
    with pytest.raises(ValueError):
        distribution_from_dict(spec)


def test_duplicate_station_names_are_rejected() -> None:
    data = DEFAULT_LINE.to_dict()
    data["stations"][1]["name"] = data["stations"][0]["name"]

    with pytest.raises(ValueError, match="unique"):
        line_config_from_dict(data)


def test_an_unsupported_config_format_is_reported_clearly(tmp_path: Path) -> None:
    path = tmp_path / "line.ini"
    path.write_text("nope")

    with pytest.raises(ValueError, match="unsupported config format"):
        load_line_config(path)


def test_yaml_config_loads_when_the_optional_extra_is_installed(tmp_path: Path) -> None:
    pytest.importorskip("yaml")
    path = tmp_path / "line.yaml"
    path.write_text(
        "name: yaml line\n"
        "arrivals:\n"
        "  interarrival: {kind: constant, seconds: 45.0}\n"
        "stations:\n"
        "  - name: printer\n"
        "    service_time: {kind: constant, seconds: 20.0}\n"
    )

    config = load_line_config(path)
    assert config.name == "yaml line"
    assert config.station("printer").service_time == Constant(seconds=20.0)


def test_the_baseline_constant_matches_its_config_file() -> None:
    """BASELINE_LINE and configs/baseline.toml must not drift apart.

    The constant exists so that nothing has to read a file at import time; the
    file exists so the line can be edited and compared without touching Python.
    Two definitions of one thing is exactly the situation this project refuses
    elsewhere, so it is pinned rather than trusted.
    """
    repo_root = Path(__file__).resolve().parent.parent
    from_file = load_line_config(repo_root / "configs" / "baseline.toml")

    assert from_file == BASELINE_LINE


def test_the_baseline_is_the_default_line_with_conveyors_and_breakdowns() -> None:
    assert [s.name for s in BASELINE_LINE.stations] == [s.name for s in DEFAULT_LINE.stations]
    assert BASELINE_LINE.arrivals == DEFAULT_LINE.arrivals

    for station in BASELINE_LINE.stations:
        default = DEFAULT_LINE.station(station.name)
        assert station.service_time == default.service_time
        assert station.capacity == default.capacity

    assert BASELINE_LINE.station("solder_paste_printer").input_buffer is None
    assert BASELINE_LINE.station("pick_and_place").input_buffer == 3
    assert BASELINE_LINE.station("spi").failures is None
    assert BASELINE_LINE.station("reflow_oven").failures is not None
