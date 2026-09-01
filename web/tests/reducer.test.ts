/**
 * The reducer, checked against the API's own summary.
 *
 * The fixture is a real event log from a real run -- 7,322 events from a
 * 480-minute shift of configs/baseline.toml -- together with the summary the
 * Python `stats.summarise` computed from the same events. Two independent
 * implementations, in two languages, reducing the same log; if they agree on
 * the numbers, the TypeScript one is reading the event vocabulary correctly.
 *
 * This is the frontend's equivalent of the byte-identity test in the service
 * suite, and it works for the same reason: two paths to one number.
 */

import { describe, expect, it } from "vitest";

import events from "./fixtures/run-events.json";
import summary from "./fixtures/run-summary.json";
import { DEFAULT_KEYFRAME_INTERVAL, timelineFrom } from "../src/replay/keyframes";
import { reduce, reduceRange, initialState } from "../src/replay/reducer";
import { stationMode } from "../src/replay/types";
import type { LineState, SimEvent, StationMode } from "../src/replay/types";

const LOG = events as unknown as SimEvent[];

function playAll(): LineState {
  return reduceRange(initialState(), LOG, 0, LOG.length);
}

describe("the reducer against a real run", () => {
  it("agrees with the API on boards completed", () => {
    expect(playAll().boardsCompleted).toBe(summary.boards_completed);
  });

  it("agrees with the API on boards arrived", () => {
    expect(playAll().boardsArrived).toBe(summary.boards_arrived);
  });

  it("learns the line's shape from the run header alone", () => {
    const state = playAll();
    const expected = summary.stations.map((station) => station.name);

    expect(state.stations.map((station) => station.name)).toEqual(expected);
    expect(state.lineName).toBe(summary.line_name);
    expect(state.horizon).toBe(summary.horizon_seconds);

    for (const station of summary.stations) {
      const actual = state.stations.find((candidate) => candidate.name === station.name);
      expect(actual?.capacity).toBe(station.capacity);
    }
  });

  it("agrees with the API on how many times each station failed", () => {
    const state = playAll();
    for (const station of summary.stations) {
      const actual = state.stations.find((candidate) => candidate.name === station.name);
      expect(actual?.failures).toBe(station.failures);
    }
  });

  it("leaves every station in a defensible mode at the horizon", () => {
    const state = playAll();
    for (const station of state.stations) {
      const mode: StationMode = stationMode(station);
      expect(["working", "blocked", "starved", "down"]).toContain(mode);
      if (station.down) expect(mode).toBe("down");
    }
  });

  it("conserves boards: arrived equals completed plus those still on the line", () => {
    const state = playAll();
    const onLine = state.stations.reduce(
      (total, station) =>
        total +
        station.queue.length +
        station.working.length +
        station.suspended.length +
        station.holding.length,
      0,
    );

    expect(state.boardsArrived).toBe(state.boardsCompleted + onLine);
  });

  it("never holds more boards at a station than its capacity", () => {
    let state = initialState();
    for (const event of LOG) {
      state = reduce(state, event);
      for (const station of state.stations) {
        expect(
          station.working.length + station.suspended.length + station.holding.length,
        ).toBeLessThanOrEqual(station.capacity);
      }
    }
  });

  it("never queues more boards than the conveyor holds", () => {
    let state = initialState();
    for (const event of LOG) {
      state = reduce(state, event);
      for (const station of state.stations) {
        if (station.bufferCapacity !== null) {
          expect(station.queue.length).toBeLessThanOrEqual(station.bufferCapacity);
        }
      }
    }
  });

  it("sees blocking where the API reports it, and nowhere else", () => {
    let sawBlocked = new Set<string>();
    let state = initialState();
    for (const event of LOG) {
      state = reduce(state, event);
      for (const station of state.stations) {
        if (station.blocked > 0) sawBlocked.add(station.name);
      }
    }

    const reportedBlocked = new Set(
      summary.stations.filter((station) => station.blocked_fraction > 0).map((s) => s.name),
    );
    expect(sawBlocked).toEqual(reportedBlocked);
  });

  it("sees a station down exactly while the API says it was", () => {
    let state = initialState();
    const downSeen = new Map<string, number>();
    for (const event of LOG) {
      const previous = state;
      state = reduce(state, event);
      for (let index = 0; index < state.stations.length; index += 1) {
        const now = state.stations[index];
        const before = previous.stations[index];
        if (now !== undefined && before !== undefined && now.down && !before.down) {
          downSeen.set(now.name, (downSeen.get(now.name) ?? 0) + 1);
        }
      }
    }

    for (const station of summary.stations) {
      expect(downSeen.get(station.name) ?? 0).toBe(station.failures);
    }
  });

  it("does not mutate the state it is given", () => {
    const before = initialState([
      { name: "a", capacity: 1, bufferCapacity: 2 },
    ]);
    const snapshot = JSON.stringify(before);
    reduce(before, { t: 1, event: "board_arrived", board: 1, station: null });

    expect(JSON.stringify(before)).toBe(snapshot);
  });

  it("shares structure, so one event copies only the station it touched", () => {
    const start = reduce(initialState(), LOG[0] as SimEvent);
    const printer = start.stations.findIndex((s) => s.name === "solder_paste_printer");
    const next = reduce(start, {
      t: 1,
      event: "queue_entered",
      board: 1,
      station: "solder_paste_printer",
    });

    expect(next.stations[printer]).not.toBe(start.stations[printer]);
    for (let index = 0; index < start.stations.length; index += 1) {
      if (index !== printer) expect(next.stations[index]).toBe(start.stations[index]);
    }
  });

  it("keeps a board visible while its station is under repair", () => {
    // The case that found the gap: a run whose horizon lands mid-repair leaves
    // a board suspended in the placer, and it must still be somewhere.
    let state = initialState();
    let sawSuspended = false;
    for (const event of LOG) {
      state = reduce(state, event);
      if (state.stations.some((station) => station.suspended.length > 0)) sawSuspended = true;
    }

    expect(sawSuspended).toBe(true);
    const stranded = state.stations.reduce((n, s) => n + s.suspended.length, 0);
    expect(stranded).toBeGreaterThan(0);
  });

  it("ignores events for stations it has never heard of", () => {
    const state = reduce(initialState(), {
      t: 5,
      event: "service_started",
      board: 1,
      station: "not_a_station",
    });

    expect(state.cursor).toBe(1);
    expect(state.time).toBe(5);
    expect(state.stations).toHaveLength(0);
  });
});

describe("the fixture itself", () => {
  it("is a real log, not a toy", () => {
    expect(LOG.length).toBeGreaterThan(5000);
    expect(LOG[0]?.event).toBe("run_started");
    expect(LOG[LOG.length - 1]?.event).toBe("run_finished");
    expect(timelineFrom(LOG).keyframeCount).toBeGreaterThan(
      LOG.length / DEFAULT_KEYFRAME_INTERVAL - 1,
    );
  });

  it("has timestamps that never decrease", () => {
    let previous = 0;
    for (const event of LOG) {
      expect(event.t).toBeGreaterThanOrEqual(previous);
      previous = event.t;
    }
  });
});
