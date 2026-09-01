/**
 * The vocabulary of the event log, mirrored in TypeScript.
 *
 * These names are not invented here. They are the ones the simulation emits,
 * the CLI table prints and the API returns, and they mean the same thing in all
 * four places. See the README's "The event log".
 */

export type EventType =
  | "run_started"
  | "board_arrived"
  | "queue_entered"
  | "service_started"
  | "service_interrupted"
  | "service_resumed"
  | "service_finished"
  | "transfer_blocked"
  | "transfer_unblocked"
  | "board_completed"
  | "station_failed"
  | "station_repaired"
  | "run_finished";

/** One line of the event log, exactly as the API serves it. */
export interface SimEvent {
  readonly t: number;
  readonly event: EventType;
  readonly board: number | null;
  readonly station: string | null;
  readonly detail?: Record<string, unknown> | null;
}

/**
 * The four time accounts, and nothing else.
 *
 * A station is in exactly one of these at any instant. The names are
 * load-bearing: they are the columns of the CLI's station table and the keys of
 * the API's summary, and a fifth would mean the UI was telling a different story
 * from every other part of the project.
 */
export type StationMode = "working" | "blocked" | "starved" | "down";

export interface StationShape {
  readonly name: string;
  readonly capacity: number;
  /** Length of the conveyor feeding this station; null means unbounded. */
  readonly bufferCapacity: number | null;
}

export interface StationState {
  readonly name: string;
  readonly capacity: number;
  readonly bufferCapacity: number | null;
  /** Boards on the conveyor feeding this station, oldest first. */
  readonly queue: readonly number[];
  /** Boards under the head, being worked on. */
  readonly working: readonly number[];
  /**
   * Boards in the machine whose work is paused by a breakdown.
   *
   * A board caught by a failure is still physically inside the station -- it is
   * simply not being worked on -- and showing it is the whole point of drawing
   * a down machine. Without this list such a board belongs to no list at all
   * and disappears from the view, which is how it was found: the conservation
   * test came up one board short at a horizon that landed mid-repair.
   */
  readonly suspended: readonly number[];
  /** Boards that have finished here and not yet moved on. */
  readonly holding: readonly number[];
  /** How many of `holding` are formally blocked -- downstream had no room. */
  readonly blocked: number;
  readonly down: boolean;
  readonly failures: number;
}

export interface LineState {
  /** Simulated time, in seconds, of the last event applied. */
  readonly time: number;
  /** How many events have been applied. Doubles as the cursor into the log. */
  readonly cursor: number;
  readonly stations: readonly StationState[];
  readonly boardsArrived: number;
  readonly boardsCompleted: number;
  /** Horizon in seconds, from the run header. Zero until it is known. */
  readonly horizon: number;
  readonly lineName: string;
}

export function stationMode(station: StationState): StationMode {
  if (station.down) return "down";
  if (station.working.length > 0) return "working";
  if (station.blocked > 0 || station.holding.length > 0) return "blocked";
  return "starved";
}
