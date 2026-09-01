"""The command line: exit codes, flags, and the run/stats agreement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smtsim.cli import main

CONFIG = Path("configs/baseline.toml")


def test_run_writes_a_log_and_reports_success(tmp_path: Path, capsys) -> None:
    out = tmp_path / "run.jsonl"
    code = main(["run", "--minutes", "60", "--seed", "1", "--out", str(out), "--quiet"])
    captured = capsys.readouterr().out

    assert code == 0
    assert out.exists()
    assert "boards completed" in captured
    assert "pick_and_place" in captured


def test_stats_reproduces_the_table_printed_by_run(tmp_path: Path, capsys) -> None:
    out = tmp_path / "run.jsonl"
    main(["run", "--minutes", "60", "--seed", "1", "--out", str(out), "--quiet"])
    from_run = capsys.readouterr().out.split("boards completed")[1]

    main(["stats", str(out)])
    from_stats = capsys.readouterr().out.split("boards completed")[1]

    assert from_run == from_stats


def test_stats_defaults_to_the_warmup_the_run_was_recorded_with(tmp_path: Path, capsys) -> None:
    out = tmp_path / "run.jsonl"
    main(["run", "--minutes", "120", "--warmup", "30", "--seed", "1", "--out", str(out), "--quiet"])
    from_run = capsys.readouterr().out.split("boards completed")[1]

    main(["stats", str(out)])
    assert capsys.readouterr().out.split("boards completed")[1] == from_run

    main(["stats", str(out), "--warmup", "0"])
    assert capsys.readouterr().out.split("boards completed")[1] != from_run


def test_the_warmup_is_recorded_in_the_log(tmp_path: Path) -> None:
    out = tmp_path / "run.jsonl"
    main(["run", "--minutes", "120", "--warmup", "30", "--seed", "1", "--out", str(out), "--quiet"])
    header = json.loads(out.read_text().splitlines()[0])

    assert header["event"] == "run_started"
    assert header["detail"]["warmup_seconds"] == pytest.approx(1800.0)


def test_a_warmup_longer_than_the_run_is_rejected(tmp_path: Path) -> None:
    out = tmp_path / "run.jsonl"
    assert main(["run", "--minutes", "10", "--warmup", "10", "--out", str(out), "--quiet"]) == 2


def test_running_a_config_with_breakdowns_shows_the_reliability_table(
    tmp_path: Path, capsys
) -> None:
    out = tmp_path / "run.jsonl"
    code = main(
        ["run", "--minutes", "480", "--seed", "3", "--config", str(CONFIG),
         "--out", str(out), "--quiet"]
    )
    captured = capsys.readouterr().out

    assert code == 0
    assert "Reliability" in captured
    assert "MTBF obs" in captured
    assert "spi" not in captured.split("Reliability")[1], "spi cannot fail, so it has no row"


def test_a_line_without_breakdowns_shows_no_reliability_table(tmp_path: Path, capsys) -> None:
    out = tmp_path / "run.jsonl"
    main(["run", "--minutes", "60", "--seed", "1", "--out", str(out), "--quiet"])

    assert "Reliability" not in capsys.readouterr().out


def test_stats_on_a_missing_log_fails_cleanly(tmp_path: Path, capsys) -> None:
    assert main(["stats", str(tmp_path / "nope.jsonl")]) == 1
    assert "no such event log" in capsys.readouterr().out


def test_a_broken_config_fails_cleanly(tmp_path: Path, capsys) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('name = "x"\n')

    assert main(["run", "--config", str(bad), "--out", str(tmp_path / "o.jsonl"), "--quiet"]) == 1
    assert "error:" in capsys.readouterr().out
