/**
 * Not a test: the measurement behind DEFAULT_KEYFRAME_INTERVAL.
 *
 * Run with `npm run bench`. Kept in the repository because the README quotes
 * its numbers and they should be reproducible.
 */

import { readFileSync } from "node:fs";

import { ReplayTimeline } from "../src/replay/keyframes";
import { initialState } from "../src/replay/reducer";
import type { SimEvent } from "../src/replay/types";

const LOG = JSON.parse(
  readFileSync(new URL("./fixtures/run-events.json", import.meta.url), "utf8"),
) as SimEvent[];

const HORIZON = 480 * 60;
const SEEK_SAMPLES = 300;

/**
 * A rough but honest proxy for what the keyframes cost.
 *
 * Because the reducer shares structure, keyframes are references into one
 * graph: what they retain is the station objects that changed since the
 * previous keyframe, not a copy of the whole state. Counting distinct objects
 * reachable from the keyframes measures that, where sizeof(state) x count
 * would overstate it several times over.
 */
function retainedObjects(timeline: ReplayTimeline): number {
  const seen = new Set<object>();
  for (let index = 0; index < timeline.keyframeCount; index += 1) {
    const state = timeline.stateAtCursor(index * timeline.intervalSize);
    if (seen.has(state)) continue;
    seen.add(state);
    for (const station of state.stations) seen.add(station);
  }
  return seen.size;
}

/**
 * A longer log, synthesised by repeating the real one with offset timestamps.
 *
 * Seek cost depends on how many events have to be replayed, not on whether the
 * boards are plausible, so repetition is a fair way to ask what happens at ten
 * or forty times the length. The 480-minute fixture is simply too short for the
 * keyframe interval to do any work.
 */
function repeated(times: number): SimEvent[] {
  const out: SimEvent[] = [];
  for (let copy = 0; copy < times; copy += 1) {
    const offset = copy * HORIZON;
    for (const event of LOG) out.push({ ...event, t: event.t + offset });
  }
  return out;
}

for (const copies of [1, 10, 40]) {
  const log = copies === 1 ? LOG : repeated(copies);
  const span = HORIZON * copies;
  console.log(`\n${log.length.toLocaleString()} events (${copies}x the 480-minute fixture)`);
  console.log("interval  keyframes   worst seek   mean seek   objects retained");
  console.log("--------  ---------  -----------  ----------  ----------------");

  for (const interval of [50, 250, 500, 1000, 5000, 50000, Number.MAX_SAFE_INTEGER]) {
    const timeline = new ReplayTimeline(initialState(), interval);
    timeline.append(log);

    // Warm up, then measure; the first seeks pay for JIT rather than for work.
    for (let sample = 0; sample < 30; sample += 1) timeline.stateAtTime((span * sample) / 30);

    const times: number[] = [];
    for (let sample = 0; sample < SEEK_SAMPLES; sample += 1) {
      const started = performance.now();
      timeline.stateAtTime((span * sample) / SEEK_SAMPLES);
      times.push(performance.now() - started);
    }

    const worst = Math.max(...times);
    const mean = times.reduce((a, b) => a + b, 0) / times.length;
    const label = interval === Number.MAX_SAFE_INTEGER ? "none" : String(interval);
    console.log(
      `${label.padStart(8)}  ${String(timeline.keyframeCount).padStart(9)}  ` +
        `${worst.toFixed(2).padStart(9)}ms  ${mean.toFixed(3).padStart(8)}ms  ` +
        `${String(retainedObjects(timeline)).padStart(16)}`,
    );
  }
}
