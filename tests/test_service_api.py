"""The HTTP and WebSocket API, against a real Postgres."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest

from service_fixtures import requires_database, run_alembic, wait_for_status
from smtsim.config import DEFAULT_LINE, load_line_config

pytestmark = [requires_database, pytest.mark.service]

REPO_ROOT = Path(__file__).resolve().parent.parent
BASELINE = load_line_config(REPO_ROOT / "configs" / "baseline.toml").to_dict()
DEFAULT_CONFIG = DEFAULT_LINE.to_dict()


# --- health -----------------------------------------------------------------


def test_healthz_reports_the_database(client) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] is True
    assert body["in_flight"] == 0


# --- run lifecycle ----------------------------------------------------------


def test_a_run_goes_from_accepted_to_summarised(client) -> None:
    response = client.post(
        "/runs",
        json={"config": BASELINE, "minutes": 480, "seed": 42, "warmup_minutes": 30},
    )
    assert response.status_code == 202
    run_id = response.json()["id"]
    assert response.json()["status"] == "queued"

    body = wait_for_status(client, f"/runs/{run_id}", {"succeeded", "failed"})
    assert body["status"] == "succeeded", body.get("error")
    assert body["error"] is None
    assert body["started_at"] is not None and body["finished_at"] is not None
    assert body["event_count"] > 1000

    summary = body["summary"]
    assert summary["boards_completed"] > 300
    assert summary["bottleneck"] == "pick_and_place"
    assert summary["warmup_seconds"] == pytest.approx(1800.0)


def test_the_summary_carries_everything_the_cli_prints(client) -> None:
    """Stage 4 renders from this; it must not have to recompute anything."""
    run_id = client.post(
        "/runs", json={"config": BASELINE, "minutes": 480, "seed": 42, "warmup_minutes": 30}
    ).json()["id"]
    summary = wait_for_status(client, f"/runs/{run_id}", {"succeeded"})["summary"]

    for key in ("throughput_per_hour", "mean_cycle_time_minutes", "p95_cycle_time_minutes"):
        assert key in summary

    stations = {station["name"]: station for station in summary["stations"]}
    assert set(stations) == {"solder_paste_printer", "pick_and_place", "spi", "reflow_oven"}

    for station in stations.values():
        four = (
            station["working_fraction"]
            + station["blocked_fraction"]
            + station["starved_fraction"]
            + station["down_fraction"]
        )
        assert four == pytest.approx(1.0, abs=1e-9), "the four time accounts must survive the API"
        for key in ("availability", "utilisation_uptime", "failures", "observed_mtbf_seconds"):
            assert key in station

    assert stations["solder_paste_printer"]["blocked_fraction"] > 0.0
    assert stations["pick_and_place"]["failures"] >= 0


def test_listing_runs_paginates_and_filters(client) -> None:
    for seed in range(4):
        client.post("/runs", json={"config": DEFAULT_CONFIG, "minutes": 30, "seed": seed})

    listing = client.get("/runs", params={"limit": 2}).json()
    assert listing["total"] == 4
    assert len(listing["items"]) == 2
    assert listing["limit"] == 2

    second = client.get("/runs", params={"limit": 2, "offset": 2}).json()
    first_ids = {item["id"] for item in listing["items"]}
    assert first_ids & {item["id"] for item in second["items"]} == set()

    for item in listing["items"]:
        wait_for_status(client, f"/runs/{item['id']}", {"succeeded", "failed"})
    for item in second["items"]:
        wait_for_status(client, f"/runs/{item['id']}", {"succeeded", "failed"})

    succeeded = client.get("/runs", params={"status": "succeeded"}).json()
    assert succeeded["total"] == 4
    assert client.get("/runs", params={"status": "queued"}).json()["total"] == 0


def test_an_unknown_status_filter_is_rejected(client) -> None:
    assert client.get("/runs", params={"status": "elsewhere"}).status_code == 422


def test_deleting_a_run_removes_its_events(client) -> None:
    run_id = client.post(
        "/runs", json={"config": DEFAULT_CONFIG, "minutes": 60, "seed": 5}
    ).json()["id"]
    wait_for_status(client, f"/runs/{run_id}", {"succeeded"})

    assert client.get(f"/runs/{run_id}/events").json()["items"]
    assert client.delete(f"/runs/{run_id}").status_code == 204
    assert client.get(f"/runs/{run_id}").status_code == 404
    assert client.get(f"/runs/{run_id}/events").status_code == 404
    assert client.delete(f"/runs/{run_id}").status_code == 404


# --- events -----------------------------------------------------------------


def test_events_paginate_by_sequence(client) -> None:
    run_id = client.post(
        "/runs", json={"config": DEFAULT_CONFIG, "minutes": 120, "seed": 3}
    ).json()["id"]
    total = wait_for_status(client, f"/runs/{run_id}", {"succeeded"})["event_count"]

    collected: list[dict] = []
    after = 0
    while True:
        page = client.get(f"/runs/{run_id}/events", params={"after": after, "limit": 200}).json()
        collected.extend(page["items"])
        if page["next_after"] is None:
            break
        after = page["next_after"]

    assert len(collected) == total
    assert [event["seq"] for event in collected] == list(range(1, total + 1))
    assert collected[0]["event"] == "run_started"
    assert collected[0]["detail"]["seed"] == 3
    assert collected[-1]["event"] == "run_finished"
    times = [event["t"] for event in collected]
    assert times == sorted(times)


def test_the_stored_stream_keeps_the_header_events(client) -> None:
    """`stats.summarise` reads run_started for capacity, horizon and warm-up."""
    from smtsim.events import Event
    from smtsim.stats import summarise

    run_id = client.post(
        "/runs", json={"config": BASELINE, "minutes": 240, "seed": 11, "warmup_minutes": 20}
    ).json()["id"]
    stored = wait_for_status(client, f"/runs/{run_id}", {"succeeded"})["summary"]

    page = client.get(f"/runs/{run_id}/events", params={"limit": 1000}).json()
    events = []
    after = 0
    while True:
        page = client.get(f"/runs/{run_id}/events", params={"after": after, "limit": 1000}).json()
        events.extend(
            Event.from_dict({k: v for k, v in item.items() if k != "seq"})
            for item in page["items"]
        )
        if page["next_after"] is None:
            break
        after = page["next_after"]

    recomputed = summarise(events)
    assert recomputed.to_dict() == stored, (
        "re-summarising the stored stream must reproduce the stored summary, which "
        "only works if the run_started header survived persistence"
    )


def test_a_run_can_skip_storing_its_events(client) -> None:
    run_id = client.post(
        "/runs",
        json={"config": DEFAULT_CONFIG, "minutes": 60, "seed": 9, "store_events": False},
    ).json()["id"]
    body = wait_for_status(client, f"/runs/{run_id}", {"succeeded"})

    assert body["summary"] is not None
    assert body["event_count"] == 0
    assert client.get(f"/runs/{run_id}/events").json()["items"] == []


# --- validation and 404s ----------------------------------------------------


def test_a_zero_buffer_is_rejected_with_the_validators_own_message(client) -> None:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config["stations"][1]["input_buffer"] = 0

    response = client.post("/runs", json={"config": config, "minutes": 60})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"][-1] == "config"
    assert "input_buffer must be >= 1" in detail[0]["msg"]
    assert "pick_and_place" in detail[0]["msg"]


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda c: c.pop("stations"), "stations"),
        (lambda c: c["stations"][0].update(capacity=0), "capacity must be >= 1"),
        (lambda c: c["stations"][0]["service_time"].update(kind="wishful"), "unknown distribution"),
        (lambda c: c["stations"][1].update(name=c["stations"][0]["name"]), "unique"),
        (
            lambda c: c["arrivals"].__setitem__(
                "interarrival", {"kind": "constant", "seconds": 0.0}
            ),
            "positive",
        ),
    ],
)
def test_bad_configurations_are_rejected_with_a_useful_message(client, mutate, fragment) -> None:
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    mutate(config)

    response = client.post("/runs", json={"config": config, "minutes": 60})

    assert response.status_code == 422
    assert fragment in json.dumps(response.json()["detail"])


def test_a_warmup_longer_than_the_run_is_rejected(client) -> None:
    response = client.post(
        "/runs", json={"config": DEFAULT_CONFIG, "minutes": 60, "warmup_minutes": 60}
    )
    assert response.status_code == 422


def test_unknown_ids_are_404(client) -> None:
    missing = uuid4()
    assert client.get(f"/runs/{missing}").status_code == 404
    assert client.get(f"/runs/{missing}/events").status_code == 404
    assert client.delete(f"/runs/{missing}").status_code == 404
    assert client.get(f"/comparisons/{missing}").status_code == 404


def test_a_malformed_uuid_is_422(client) -> None:
    assert client.get("/runs/not-a-uuid").status_code == 422


# --- failure recording ------------------------------------------------------


def test_a_run_that_fails_mid_simulation_records_the_error(client, monkeypatch) -> None:
    """A lost exception would leave the run in 'running' forever."""
    import smtsim_service.jobs as jobs

    def explode(events):
        raise RuntimeError("summariser exploded on purpose")

    monkeypatch.setattr(jobs, "summarise", explode)

    run_id = client.post(
        "/runs", json={"config": DEFAULT_CONFIG, "minutes": 60, "seed": 1}
    ).json()["id"]
    body = wait_for_status(client, f"/runs/{run_id}", {"succeeded", "failed"})

    assert body["status"] == "failed"
    assert "summariser exploded on purpose" in body["error"]
    assert body["finished_at"] is not None
    assert body["summary"] is None


def test_a_failed_run_still_closes_its_stream(client, monkeypatch) -> None:
    import smtsim_service.jobs as jobs

    def explode(events):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs, "summarise", explode)
    run_id = client.post(
        "/runs", json={"config": DEFAULT_CONFIG, "minutes": 5000, "seed": 1}
    ).json()["id"]

    with client.websocket_connect(f"/runs/{run_id}/stream") as socket:
        while True:
            frame = socket.receive_json()
            if frame["type"] == "end":
                assert frame["status"] == "failed"
                assert "boom" in frame["error"]
                break


# --- websockets -------------------------------------------------------------


def test_streaming_a_live_run(client) -> None:
    """Events arrive while the simulation is still going.

    The run is long enough that it is reliably still in flight when the socket
    connects; a 480-minute shift finishes in about a tenth of a second.
    """
    run_id = client.post(
        "/runs", json={"config": BASELINE, "minutes": 5000, "seed": 4}
    ).json()["id"]

    received: list[dict] = []
    with client.websocket_connect(f"/runs/{run_id}/stream") as socket:
        start = socket.receive_json()
        assert start["type"] == "start"
        assert start["mode"] == "live"

        while True:
            frame = socket.receive_json()
            if frame["type"] == "events":
                received.extend(frame["events"])
            elif frame["type"] == "end":
                assert frame["status"] == "succeeded"
                break

    assert len(received) > 1000
    assert {event["event"] for event in received} >= {"board_arrived", "service_started"}
    times = [event["t"] for event in received]
    assert times == sorted(times)


def test_the_websocket_batches_rather_than_sending_one_frame_per_event(client) -> None:
    run_id = client.post(
        "/runs", json={"config": BASELINE, "minutes": 5000, "seed": 6}
    ).json()["id"]

    sizes: list[int] = []
    with client.websocket_connect(f"/runs/{run_id}/stream") as socket:
        assert socket.receive_json()["type"] == "start"
        while True:
            frame = socket.receive_json()
            if frame["type"] == "events":
                sizes.append(len(frame["events"]))
            elif frame["type"] == "end":
                break

    events = sum(sizes)
    assert events > 1000
    # Deliberately not a frames-to-events ratio. The time-based flush is doing
    # its job when the producer is slow, so that ratio measures how loaded the
    # machine is rather than whether batching works. What must be true is that
    # frames carry many events when there are many to carry.
    assert max(sizes) >= 50, f"largest frame held {max(sizes)} events; batching is not happening"
    print(f"\n  {len(sizes)} frames, {events} events, largest {max(sizes)}")


def test_streaming_a_finished_run_replays_it(client) -> None:
    """Stage 4 gets one code path whether the run is live or long over."""
    run_id = client.post(
        "/runs", json={"config": DEFAULT_CONFIG, "minutes": 120, "seed": 8}
    ).json()["id"]
    total = wait_for_status(client, f"/runs/{run_id}", {"succeeded"})["event_count"]

    received: list[dict] = []
    with client.websocket_connect(f"/runs/{run_id}/stream") as socket:
        start = socket.receive_json()
        assert start["mode"] == "replay"
        while True:
            frame = socket.receive_json()
            if frame["type"] == "events":
                received.extend(frame["events"])
            elif frame["type"] == "end":
                assert frame["status"] == "succeeded"
                break

    assert len(received) == total
    assert received[0]["event"] == "run_started"
    assert received[-1]["event"] == "run_finished"


def test_a_client_that_falls_behind_is_disconnected_rather_than_shortchanged(
    settings, clean_tables
) -> None:
    """The stated backpressure policy, exercised.

    Every event mutates line state, so a consumer given an incomplete stream
    draws a silently wrong picture. The service closes the socket with 1013
    instead and points at the lossless REST endpoint.
    """
    from starlette.testclient import TestClient, WebSocketDisconnect

    from smtsim_service.app import create_app

    cramped = settings.model_copy(update={"ws_queue_maxsize": 4, "ws_batch_interval_ms": 400})

    with TestClient(create_app(cramped)) as slow_client:
        run_id = slow_client.post(
            "/runs", json={"config": BASELINE, "minutes": 5000, "seed": 12}
        ).json()["id"]

        with pytest.raises(WebSocketDisconnect) as caught:
            with slow_client.websocket_connect(f"/runs/{run_id}/stream") as socket:
                assert socket.receive_json()["type"] == "start"
                while True:
                    socket.receive_json()

    assert caught.value.code == 1013
    assert "events" in (caught.value.reason or "")


def test_streaming_an_unknown_run_is_refused(client) -> None:
    from starlette.testclient import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/runs/{uuid4()}/stream") as socket:
            socket.receive_json()

    assert caught.value.code == 4404


# --- comparisons ------------------------------------------------------------


def test_a_comparison_runs_and_reports_a_paired_interval(client) -> None:
    tight = load_line_config(REPO_ROOT / "configs" / "tight_buffers.toml").to_dict()

    response = client.post(
        "/comparisons",
        json={
            "baseline": tight,
            "variant": BASELINE,
            "seeds": 6,
            "minutes": 480,
            "warmup_minutes": 30,
        },
    )
    assert response.status_code == 202
    comparison_id = response.json()["id"]

    body = wait_for_status(
        client, f"/comparisons/{comparison_id}", {"succeeded", "failed"}, timeout=120
    )
    assert body["status"] == "succeeded", body.get("error")

    summary = body["summary"]
    assert summary["seeds"] == list(range(1, 7))
    metrics = {metric["metric"]: metric for metric in summary["metrics"]}
    assert set(metrics) == {"throughput_per_hour", "mean_cycle_time_minutes"}
    interval = metrics["mean_cycle_time_minutes"]["interval"]
    assert interval["n"] == 6
    assert interval["low"] < interval["mean_difference"] < interval["high"]
    assert "excludes_zero" in interval

    assert len(body["runs"]) == 12
    assert {link["role"] for link in body["runs"]} == {"baseline", "variant"}
    assert all(link["status"] == "succeeded" for link in body["runs"])


def test_a_comparison_needs_at_least_two_seeds(client) -> None:
    response = client.post(
        "/comparisons", json={"baseline": DEFAULT_CONFIG, "variant": DEFAULT_CONFIG, "seeds": 1}
    )
    assert response.status_code == 422


def test_a_comparison_rejects_a_bad_variant(client) -> None:
    broken = json.loads(json.dumps(DEFAULT_CONFIG))
    broken["stations"][0]["capacity"] = 0

    response = client.post(
        "/comparisons", json={"baseline": DEFAULT_CONFIG, "variant": broken, "seeds": 4}
    )
    assert response.status_code == 422
    assert "variant" in json.dumps(response.json()["detail"])


# --- migrations -------------------------------------------------------------


def test_migrations_go_up_and_down_cleanly(migrated_database: str) -> None:
    assert run_alembic("downgrade", "base").returncode == 0
    up = run_alembic("upgrade", "head")
    assert up.returncode == 0, up.stderr

    import psycopg

    with psycopg.connect(migrated_database) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
    assert {"runs", "run_events", "comparisons", "comparison_runs"} <= tables


def test_the_service_never_creates_tables_itself() -> None:
    """Schema changes go through Alembic, never through create_all."""
    service = REPO_ROOT / "src" / "smtsim_service"
    for module in service.rglob("*.py"):
        source = module.read_text(encoding="utf-8")
        assert "create_all" not in source, f"{module.name} creates tables outside a migration"
        assert "CREATE TABLE" not in source.upper(), f"{module.name} has DDL in it"


# --- the proof ---------------------------------------------------------------


def test_a_run_through_the_api_is_byte_identical_to_the_same_run_through_the_cli(
    client, tmp_path: Path
) -> None:
    """The point of the whole stage.

    If the simulation core really is untouched by adding a web layer, then the
    same configuration and seed must produce the same events whether they went
    to a file through the CLI or through a thread, a queue, a COPY and Postgres.
    Board-level events are compared byte for byte against the CLI's own JSONL
    output; the two header events carry a `detail` object and are compared as
    parsed JSON, because their key order is the one thing persistence does not
    promise to preserve.
    """
    config_path = REPO_ROOT / "configs" / "baseline.toml"
    log_path = tmp_path / "cli.jsonl"
    cli = subprocess.run(
        [
            sys.executable, "-m", "smtsim.cli", "run",
            "--minutes", "240", "--seed", "77", "--warmup", "20",
            "--config", str(config_path), "--out", str(log_path), "--quiet",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert cli.returncode == 0, cli.stderr

    run_id = client.post(
        "/runs",
        json={"config": BASELINE, "minutes": 240, "seed": 77, "warmup_minutes": 20},
    ).json()["id"]
    wait_for_status(client, f"/runs/{run_id}", {"succeeded"})

    api_events: list[dict] = []
    after = 0
    while True:
        page = client.get(f"/runs/{run_id}/events", params={"after": after, "limit": 1000}).json()
        api_events.extend(page["items"])
        if page["next_after"] is None:
            break
        after = page["next_after"]

    cli_lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(api_events) == len(cli_lines)

    headers = 0
    for line, stored in zip(cli_lines, api_events, strict=True):
        from_cli = json.loads(line)
        rebuilt = {
            "t": stored["t"],
            "event": stored["event"],
            "board": stored["board"],
            "station": stored["station"],
        }
        if stored["detail"] is not None:
            headers += 1
            assert stored["detail"] == from_cli["detail"]
        else:
            assert "detail" not in from_cli
            assert json.dumps(rebuilt, separators=(",", ":")) == line, (
                "an event travelled through a thread, a COPY and Postgres and came "
                "back different from the one the CLI wrote"
            )
        assert rebuilt["t"] == from_cli["t"]

    assert headers == 2, "expected exactly run_started and run_finished to carry detail"
