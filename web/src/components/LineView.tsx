/**
 * The line, drawn from whatever the reducer currently holds.
 *
 * SVG rather than Canvas: a few dozen moving elements is comfortably within
 * SVG's range, and it keeps every board and station a real element you can
 * inspect, hit-test and style. Canvas would buy throughput this does not need
 * and cost inspectability this does.
 *
 * This component is presentational to a fault. It reads a LineState and draws
 * it; it holds no state, subscribes to nothing, and knows nothing about time.
 */

import { MODE_STYLES, BOARD_FILL, BOARD_STROKE } from "./palette";
import { stationMode } from "../replay/types";
import type { LineState, StationState } from "../replay/types";

const STATION_WIDTH = 132;
const STATION_HEIGHT = 84;
const CONVEYOR_WIDTH = 96;
const TOP = 40;
const LEFT = 104;
const BOARD = 11;

function stationX(index: number): number {
  return LEFT + index * (STATION_WIDTH + CONVEYOR_WIDTH);
}

/** Short labels; the full names are long and the shapes are not. */
function shortName(name: string): string {
  return name
    .split("_")
    .map((part) => (part === "and" ? "&" : part))
    .join(" ");
}

function BoardShape({ x, y, dim = false }: { x: number; y: number; dim?: boolean }) {
  return (
    <rect
      x={x}
      y={y}
      width={BOARD}
      height={BOARD}
      rx={2}
      fill={BOARD_FILL}
      stroke={BOARD_STROKE}
      strokeWidth={1}
      opacity={dim ? 0.45 : 1}
    />
  );
}

function Station({ station, index }: { station: StationState; index: number }) {
  const mode = stationMode(station);
  const style = MODE_STYLES[mode];
  const x = stationX(index);
  const inside = [...station.working, ...station.suspended, ...station.holding];

  return (
    <g data-testid={`station-${station.name}`} data-mode={mode}>
      <rect
        x={x}
        y={TOP}
        width={STATION_WIDTH}
        height={STATION_HEIGHT}
        rx={8}
        fill={style.fill}
        stroke={style.stroke}
        strokeWidth={2}
      />
      <text x={x + STATION_WIDTH / 2} y={TOP + 20} className="station-name">
        {shortName(station.name)}
      </text>
      <text x={x + STATION_WIDTH / 2} y={TOP + 36} className="station-mode">
        {style.label}
      </text>

      {/* One slot per unit of capacity, so a six-slot oven looks like one. */}
      {Array.from({ length: station.capacity }, (_, slot) => {
        const perRow = Math.min(station.capacity, 6);
        const column = slot % perRow;
        const row = Math.floor(slot / perRow);
        const slotX = x + 12 + column * ((STATION_WIDTH - 24) / perRow);
        const slotY = TOP + 48 + row * (BOARD + 4);
        const board = inside[slot];
        return (
          <g key={slot}>
            <rect
              x={slotX}
              y={slotY}
              width={BOARD}
              height={BOARD}
              rx={2}
              fill="none"
              stroke={style.stroke}
              strokeWidth={0.75}
              opacity={0.4}
            />
            {board !== undefined && (
              <BoardShape x={slotX} y={slotY} dim={station.suspended.includes(board)} />
            )}
          </g>
        );
      })}

    </g>
  );
}

/**
 * The conveyor feeding a station, drawn as slots so that full looks full.
 *
 * This is the element that makes blocking legible. When the placer stops, its
 * conveyor fills slot by slot, and the moment the last slot fills the printer
 * behind it turns amber -- the whole causal chain, visible without commentary.
 */
function Conveyor({ station, index }: { station: StationState; index: number }) {
  const x = stationX(index) - CONVEYOR_WIDTH;
  const midline = TOP + STATION_HEIGHT / 2;
  const capacity = station.bufferCapacity;
  const occupancy = station.queue.length;
  const full = capacity !== null && occupancy >= capacity;

  return (
    <g
      data-testid={`conveyor-${station.name}`}
      data-occupancy={occupancy}
      data-capacity={capacity ?? "unbounded"}
      data-full={full ? "true" : "false"}
    >
      <line
        x1={x + 4}
        y1={midline}
        x2={x + CONVEYOR_WIDTH - 4}
        y2={midline}
        stroke={full ? MODE_STYLES.blocked.stroke : "#3d445c"}
        strokeWidth={2}
      />
      {capacity === null ? (
        // The printer is fed from a magazine, not a conveyor: there is no
        // capacity to draw an occupancy against. Boards queue off-stage.
        <>
          <text x={x + CONVEYOR_WIDTH / 2} y={midline - 12} className="conveyor-label">
            magazine
          </text>
          {occupancy > 0 && (
            <text x={x + CONVEYOR_WIDTH / 2} y={midline + 22} className="conveyor-count">
              {occupancy} waiting
            </text>
          )}
        </>
      ) : (
        <>
          {Array.from({ length: capacity }, (_, slot) => {
            const slotX = x + 8 + slot * ((CONVEYOR_WIDTH - 16) / capacity);
            const filled = slot < occupancy;
            return (
              <rect
                key={slot}
                x={slotX}
                y={midline - BOARD / 2}
                width={BOARD}
                height={BOARD}
                rx={2}
                fill={filled ? BOARD_FILL : "none"}
                stroke={filled ? BOARD_STROKE : "#4a5168"}
                strokeWidth={1}
              />
            );
          })}
          <text
            x={x + CONVEYOR_WIDTH / 2}
            y={midline + 24}
            className={full ? "conveyor-count is-full" : "conveyor-count"}
          >
            {occupancy}/{capacity}
          </text>
        </>
      )}
    </g>
  );
}

export function LineView({ state }: { state: LineState }) {
  const stations = state.stations;
  const width = LEFT * 2 + stations.length * STATION_WIDTH + (stations.length - 1) * CONVEYOR_WIDTH;

  if (stations.length === 0) {
    return <p className="empty">Waiting for the run header…</p>;
  }

  return (
    <svg
      className="line-view"
      viewBox={`0 0 ${Math.max(width, 640)} ${TOP + STATION_HEIGHT + 56}`}
      role="img"
      aria-label="The assembly line"
    >
      <text x={8} y={TOP + 16} className="line-end">
        in
      </text>
      {stations.map((station, index) => (
        <Conveyor key={station.name} station={station} index={index} />
      ))}
      {stations.map((station, index) => (
        <Station key={station.name} station={station} index={index} />
      ))}
      <text
        x={stationX(stations.length - 1) + STATION_WIDTH + 20}
        y={TOP + STATION_HEIGHT / 2 + 4}
        className="line-end"
      >
        out
      </text>
    </svg>
  );
}
