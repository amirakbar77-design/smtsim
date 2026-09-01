/**
 * Seeking must be indistinguishable from having played there.
 *
 * The reducer only moves forward, so a backwards scrub replays from a keyframe.
 * That is only sound if replaying from a keyframe lands in exactly the state
 * playing forward would have reached -- otherwise the picture quietly diverges
 * from the log the further you scrub, which is the kind of bug nobody notices
 * until they are trying to explain a screenshot.
 */

import { describe, expect, it } from "vitest";

import events from "./fixtures/run-events.json";
import { ReplayTimeline, timelineFrom } from "../src/replay/keyframes";
import { initialState, reduceRange } from "../src/replay/reducer";
import type { LineState, SimEvent } from "../src/replay/types";

const LOG = events as unknown as SimEvent[];
const HORIZON = 480 * 60;

/** Play the log from the very beginning. The definition seeking must match. */
function byPlayingForward(cursor: number): LineState {
  return reduceRange(initialState(), LOG, 0, cursor);
}

const SAMPLE_TIMES = [
  0, 1, 57.7, 600, 1800, 5000, 9999.5, 14400, 20000, 25000, 28799, HORIZON, HORIZON * 2,
];

describe("seeking", () => {
  const timeline = timelineFrom(LOG);

  it.each(SAMPLE_TIMES)("lands where playing forward would, at t=%s", (time) => {
    const sought = timeline.stateAtTime(time);
    expect(sought).toEqual(byPlayingForward(timeline.cursorAtTime(time)));
  });

  it("is unaffected by where it was sought from", () => {
    const forwards = SAMPLE_TIMES.map((time) => timeline.stateAtTime(time));
    const backwards = [...SAMPLE_TIMES].reverse().map((time) => timeline.stateAtTime(time));

    expect(backwards.reverse()).toEqual(forwards);
  });

  it("gives the same answer however often it is asked", () => {
    expect(timeline.stateAtTime(12345)).toEqual(timeline.stateAtTime(12345));
  });

  it("agrees with itself at every keyframe boundary", () => {
    for (let cursor = 0; cursor <= LOG.length; cursor += 500) {
      expect(timeline.stateAtCursor(cursor)).toEqual(byPlayingForward(cursor));
    }
  });

  it.each([1, 7, 100, 500, 2000, 100000])(
    "gives identical states whatever the keyframe interval (%s)",
    (interval) => {
      const alternative = timelineFrom(LOG, interval);
      for (const time of SAMPLE_TIMES) {
        expect(alternative.stateAtTime(time)).toEqual(timeline.stateAtTime(time));
      }
    },
  );

  it("advancing during playback matches seeking to the same time", () => {
    let playing = timeline.stateAtTime(0);
    for (const time of [100, 500, 2000, 8000, 16000, 28800]) {
      playing = timeline.advance(playing, time);
      expect(playing).toEqual(timeline.stateAtTime(time));
    }
  });

  it("clamps to the ends rather than running off them", () => {
    expect(timeline.stateAtTime(-1000).cursor).toBe(0);
    expect(timeline.stateAtTime(1e9).cursor).toBe(LOG.length);
    expect(timeline.stateAtCursor(-5).cursor).toBe(0);
    expect(timeline.stateAtCursor(LOG.length + 999).cursor).toBe(LOG.length);
  });

  it("finds the right cursor for a time between two events", () => {
    const first = LOG[10] as SimEvent;
    const second = LOG[11] as SimEvent;
    if (second.t > first.t) {
      const between = (first.t + second.t) / 2;
      expect(timeline.cursorAtTime(between)).toBe(11);
    }
  });
});

describe("live loading", () => {
  it("grows its extent as batches arrive, and stays seekable throughout", () => {
    const live = new ReplayTimeline(initialState());
    const batchSize = 250;

    for (let start = 0; start < LOG.length; start += batchSize) {
      live.append(LOG.slice(start, start + batchSize));
      const loaded = live.loadedUntil;
      expect(live.stateAtTime(loaded)).toEqual(byPlayingForward(live.length));
    }

    expect(live.length).toBe(LOG.length);
    expect(live.stateAtTime(HORIZON)).toEqual(byPlayingForward(LOG.length));
  });

  it("reaches the same place whether loaded in one batch or many", () => {
    const oneBatch = new ReplayTimeline(initialState());
    oneBatch.append(LOG);

    const manyBatches = new ReplayTimeline(initialState());
    for (let start = 0; start < LOG.length; start += 37) {
      manyBatches.append(LOG.slice(start, start + 37));
    }

    expect(manyBatches.stateAtTime(HORIZON)).toEqual(oneBatch.stateAtTime(HORIZON));
  });
});
