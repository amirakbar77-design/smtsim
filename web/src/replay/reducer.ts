/**
 * The replay reducer: (LineState, SimEvent) -> LineState.
 *
 * This is the frontend's equivalent of the simulation core, and it is written
 * with the same discipline: no React, no DOM, no fetch, no clock. It is a pure
 * function over plain data, so it can be tested by feeding it a real event log
 * and comparing the result against the API's own summary -- two independent
 * paths to the same number.
 *
 * It is also *immutable with structural sharing*: applying an event returns a
 * new top-level object that reuses every station it did not touch. That is what
 * makes keyframes almost free (see keyframes.ts) -- retaining an old state
 * costs only the parts that have changed since, not a copy of everything.
 *
 * The reducer knows nothing about the shape of the line beyond what the log
 * tells it. Station order, capacities and conveyor lengths all arrive in the
 * `run_started` header, and every later event names its own station, so the
 * reducer never needs to know the routing.
 */

import type { LineState, SimEvent, StationShape, StationState } from "./types";

export const EMPTY_STATE: LineState = {
  time: 0,
  cursor: 0,
  stations: [],
  boardsArrived: 0,
  boardsCompleted: 0,
  horizon: 0,
  lineName: "",
};

export function initialState(shapes: readonly StationShape[] = []): LineState {
  return { ...EMPTY_STATE, stations: shapes.map(blankStation) };
}

function blankStation(shape: StationShape): StationState {
  return {
    name: shape.name,
    capacity: shape.capacity,
    bufferCapacity: shape.bufferCapacity,
    queue: [],
    working: [],
    suspended: [],
    holding: [],
    blocked: 0,
    down: false,
    failures: 0,
  };
}

/** Pull the line's shape out of a `run_started` detail block. */
export function shapesFromHeader(detail: Record<string, unknown> | null | undefined): {
  shapes: StationShape[];
  horizon: number;
  lineName: string;
} {
  const line = (detail?.["line"] ?? {}) as Record<string, unknown>;
  const rawStations = (line["stations"] ?? []) as Record<string, unknown>[];
  return {
    shapes: rawStations.map((station) => ({
      name: String(station["name"]),
      capacity: Number(station["capacity"] ?? 1),
      bufferCapacity:
        station["input_buffer"] === undefined || station["input_buffer"] === null
          ? null
          : Number(station["input_buffer"]),
    })),
    horizon: Number(detail?.["horizon_seconds"] ?? 0),
    lineName: String(line["name"] ?? ""),
  };
}

function indexOfStation(state: LineState, name: string | null): number {
  if (name === null) return -1;
  return state.stations.findIndex((station) => station.name === name);
}

/** Replace one station, sharing every other one by reference. */
function withStation(
  state: LineState,
  index: number,
  change: Partial<StationState>,
): readonly StationState[] {
  const next = state.stations.slice();
  const current = next[index];
  if (current === undefined) return state.stations;
  next[index] = { ...current, ...change };
  return next;
}

function without(boards: readonly number[], board: number): number[] {
  const index = boards.indexOf(board);
  if (index < 0) return boards.slice();
  return [...boards.slice(0, index), ...boards.slice(index + 1)];
}

/**
 * Find and remove a board from whichever station is holding it.
 *
 * A board leaves a station either because it was blocked and space appeared, or
 * because it finished and the next conveyor had room -- and in the second case
 * the log emits no departure event at all, only the arrival at the next
 * station. So departure is inferred from arrival, which is why this searches
 * rather than being told. There are four stations; the search is not the
 * expensive part of anything.
 */
function releaseHeldBoard(stations: StationState[], board: number): void {
  for (let index = 0; index < stations.length; index += 1) {
    const station = stations[index];
    if (station !== undefined && station.holding.includes(board)) {
      stations[index] = { ...station, holding: without(station.holding, board) };
      return;
    }
  }
}

export function reduce(state: LineState, event: SimEvent): LineState {
  const time = Math.max(state.time, event.t);
  const cursor = state.cursor + 1;
  const index = indexOfStation(state, event.station);
  const station = index >= 0 ? state.stations[index] : undefined;

  switch (event.event) {
    case "run_started": {
      const { shapes, horizon, lineName } = shapesFromHeader(event.detail);
      return {
        ...initialState(shapes),
        time,
        cursor,
        horizon,
        lineName,
      };
    }

    case "board_arrived":
      return { ...state, time, cursor, boardsArrived: state.boardsArrived + 1 };

    case "queue_entered": {
      if (station === undefined || event.board === null) return { ...state, time, cursor };
      const stations = state.stations.slice();
      // The board has just left wherever it was; the arrival is the departure.
      releaseHeldBoard(stations, event.board);
      const target = stations[index];
      if (target !== undefined) {
        stations[index] = { ...target, queue: [...target.queue, event.board] };
      }
      return { ...state, time, cursor, stations };
    }

    case "service_started": {
      if (station === undefined || event.board === null) return { ...state, time, cursor };
      return {
        ...state,
        time,
        cursor,
        stations: withStation(state, index, {
          queue: without(station.queue, event.board),
          working: [...station.working, event.board],
        }),
      };
    }

    case "service_interrupted": {
      if (station === undefined || event.board === null) return { ...state, time, cursor };
      // The board stays in the machine; it just stops being worked on.
      return {
        ...state,
        time,
        cursor,
        stations: withStation(state, index, {
          working: without(station.working, event.board),
          suspended: [...station.suspended, event.board],
        }),
      };
    }

    case "service_resumed": {
      if (station === undefined || event.board === null) return { ...state, time, cursor };
      return {
        ...state,
        time,
        cursor,
        stations: withStation(state, index, {
          suspended: without(station.suspended, event.board),
          working: [...station.working, event.board],
        }),
      };
    }

    case "service_finished": {
      if (station === undefined || event.board === null) return { ...state, time, cursor };
      return {
        ...state,
        time,
        cursor,
        stations: withStation(state, index, {
          working: without(station.working, event.board),
          holding: [...station.holding, event.board],
        }),
      };
    }

    case "transfer_blocked": {
      if (station === undefined) return { ...state, time, cursor };
      return {
        ...state,
        time,
        cursor,
        stations: withStation(state, index, { blocked: station.blocked + 1 }),
      };
    }

    case "transfer_unblocked": {
      if (station === undefined) return { ...state, time, cursor };
      return {
        ...state,
        time,
        cursor,
        stations: withStation(state, index, { blocked: Math.max(0, station.blocked - 1) }),
      };
    }

    case "station_failed": {
      if (station === undefined) return { ...state, time, cursor };
      return {
        ...state,
        time,
        cursor,
        stations: withStation(state, index, { down: true, failures: station.failures + 1 }),
      };
    }

    case "station_repaired": {
      if (station === undefined) return { ...state, time, cursor };
      return { ...state, time, cursor, stations: withStation(state, index, { down: false }) };
    }

    case "board_completed": {
      const stations = state.stations.slice();
      if (event.board !== null) releaseHeldBoard(stations, event.board);
      return {
        ...state,
        time,
        cursor,
        stations,
        boardsCompleted: state.boardsCompleted + 1,
      };
    }

    case "run_finished":
      return { ...state, time, cursor };

    default:
      return { ...state, time, cursor };
  }
}

/** Apply a slice of the log. Used by playback and by seeking alike. */
export function reduceRange(
  state: LineState,
  events: readonly SimEvent[],
  from: number,
  to: number,
): LineState {
  let next = state;
  for (let index = from; index < to; index += 1) {
    const event = events[index];
    if (event === undefined) break;
    next = reduce(next, event);
  }
  return next;
}
