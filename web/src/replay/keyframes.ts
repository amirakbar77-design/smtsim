/**
 * Seeking, including backwards.
 *
 * The reducer only moves forward -- there is no inverse of "a board finished at
 * the placer" -- so seeking backwards means starting from an earlier state and
 * replaying. Keeping every state would be simplest and costs too much; keeping
 * only the first would make a scrub to the end of a long run replay the whole
 * log.
 *
 * The compromise is keyframes: retain the state every N events, and seek by
 * finding the latest keyframe at or before the target and replaying forward
 * from there. Worst-case replay work is N events, which bounds seek latency
 * independently of run length.
 *
 * What makes this cheap here is that the reducer is immutable and shares
 * structure. A keyframe is not a snapshot that has to be copied -- it is a
 * reference to a state object that already exists, and its incremental cost is
 * only the station objects that changed since the previous keyframe. Retaining
 * one is a pointer.
 */

import { initialState, reduceRange } from "./reducer";
import type { LineState, SimEvent } from "./types";

/**
 * Events between keyframes. Measured, not guessed -- see tests/keyframe-bench.ts
 * and the README table.
 *
 * The headline of the measurement is that the 480-minute fixture (7,322 events)
 * does not need keyframes at all: seeking the whole log from scratch takes
 * 0.6 ms. They begin to matter around 100,000 events, and at 293,000 -- a
 * multi-day horizon -- seeking with no keyframes costs 23 ms worst case and
 * 11.6 ms mean, which is a dropped frame on every scrub.
 *
 * 1,000 is the choice: at that same 293,000-event log it seeks in 0.15 ms worst
 * case and 0.04 ms mean while retaining 1,461 objects, on the order of 100 KB.
 * Halving it to 500 doubles what is retained and buys no latency anyone can
 * perceive; raising it to 50,000 still fits inside a frame but gives up the
 * headroom for nothing, since the memory it saves was never the problem.
 */
export const DEFAULT_KEYFRAME_INTERVAL = 1000;

export class ReplayTimeline {
  private readonly events: SimEvent[] = [];
  private readonly keyframes: LineState[] = [];
  private readonly interval: number;

  constructor(initial: LineState, interval: number = DEFAULT_KEYFRAME_INTERVAL) {
    this.interval = Math.max(1, interval);
    this.keyframes.push(initial);
  }

  get length(): number {
    return this.events.length;
  }

  get keyframeCount(): number {
    return this.keyframes.length;
  }

  get intervalSize(): number {
    return this.interval;
  }

  /** Simulated time of the last event loaded. The scrub bar's right-hand end. */
  get loadedUntil(): number {
    return this.events.length === 0 ? 0 : (this.events[this.events.length - 1]?.t ?? 0);
  }

  eventAt(index: number): SimEvent | undefined {
    return this.events[index];
  }

  slice(from: number, to: number): SimEvent[] {
    return this.events.slice(from, to);
  }

  /**
   * Append events and build any keyframes they complete.
   *
   * Called as batches arrive from the WebSocket. Building keyframes on append
   * rather than on demand keeps the cost off the seek path, where it would be
   * felt.
   */
  append(batch: readonly SimEvent[]): void {
    for (const event of batch) {
      this.events.push(event);
      if (this.events.length % this.interval === 0) {
        this.keyframes.push(this.stateAtCursor(this.events.length));
      }
    }
  }

  /** The latest keyframe at or before `cursor`. */
  private keyframeBefore(cursor: number): LineState {
    const index = Math.min(Math.floor(cursor / this.interval), this.keyframes.length - 1);
    return this.keyframes[Math.max(0, index)] ?? (this.keyframes[0] as LineState);
  }

  /** State after applying exactly `cursor` events. */
  stateAtCursor(cursor: number): LineState {
    const target = Math.max(0, Math.min(cursor, this.events.length));
    const keyframe = this.keyframeBefore(target);
    if (keyframe.cursor === target) return keyframe;
    return reduceRange(keyframe, this.events, keyframe.cursor, target);
  }

  /** How many events fall at or before `time`. Binary search over a sorted log. */
  cursorAtTime(time: number): number {
    let low = 0;
    let high = this.events.length;
    while (low < high) {
      const middle = (low + high) >>> 1;
      const event = this.events[middle];
      if (event !== undefined && event.t <= time) low = middle + 1;
      else high = middle;
    }
    return low;
  }

  /** State as of simulated time `time`, seeking in either direction. */
  stateAtTime(time: number): LineState {
    return this.stateAtCursor(this.cursorAtTime(time));
  }

  /**
   * Advance an already-current state to `time` without seeking.
   *
   * The playback loop's hot path: while playing forward the previous state is
   * already correct up to its own cursor, so there is nothing to replay.
   */
  advance(from: LineState, time: number): LineState {
    const target = this.cursorAtTime(time);
    if (target <= from.cursor) return from;
    return reduceRange(from, this.events, from.cursor, target);
  }
}

/**
 * Build a timeline from a complete log. Used by tests and by the REST fallback.
 *
 * `run_started` is appended like any other event rather than being consumed to
 * seed the state. It is tempting to special-case it -- it is the event that
 * creates the stations -- but doing so puts every cursor one out of step with
 * the log's own indices, which makes seeking agree with itself and disagree
 * with playing forward from the beginning.
 */
export function timelineFrom(
  events: readonly SimEvent[],
  interval: number = DEFAULT_KEYFRAME_INTERVAL,
): ReplayTimeline {
  const timeline = new ReplayTimeline(initialState(), interval);
  timeline.append(events);
  return timeline;
}
