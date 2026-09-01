/**
 * The timeline strip: where things went wrong, over the whole run.
 *
 * A featureless scrub bar tells you nothing about where to look. This one is
 * drawn from the log itself -- one lane per station, marked where it was down
 * and where it was blocked -- so the interesting moments are visible before you
 * scrub to them, and clicking one seeks there.
 *
 * The bands are computed once per loaded log rather than per frame; they change
 * only when more events arrive.
 */

import { useMemo } from "react";

import { MODE_STYLES } from "./palette";
import type { SimEvent } from "../replay/types";

const LANE_HEIGHT = 16;
const LANE_GAP = 4;
const LABEL_WIDTH = 132;

export interface Band {
  readonly from: number;
  readonly to: number;
  readonly kind: "down" | "blocked";
}

export interface Lane {
  readonly station: string;
  readonly bands: readonly Band[];
}

/**
 * Reduce the log to intervals worth drawing.
 *
 * Deliberately not part of the replay reducer: that one answers "what is true
 * now", this one answers "what happened across the whole run", and they have no
 * reason to share a representation.
 */
export function bandsFrom(events: readonly SimEvent[], stations: readonly string[]): Lane[] {
  const openDown = new Map<string, number>();
  const blockedDepth = new Map<string, number>();
  const openBlock = new Map<string, number>();
  const lanes = new Map<string, Band[]>(stations.map((name) => [name, []]));

  for (const event of events) {
    const station = event.station;
    if (station === null) continue;
    const bands = lanes.get(station);
    if (bands === undefined) continue;

    switch (event.event) {
      case "station_failed":
        openDown.set(station, event.t);
        break;
      case "station_repaired": {
        const from = openDown.get(station);
        if (from !== undefined) bands.push({ from, to: event.t, kind: "down" });
        openDown.delete(station);
        break;
      }
      case "transfer_blocked": {
        const depth = (blockedDepth.get(station) ?? 0) + 1;
        blockedDepth.set(station, depth);
        if (depth === 1) openBlock.set(station, event.t);
        break;
      }
      case "transfer_unblocked": {
        const depth = Math.max(0, (blockedDepth.get(station) ?? 0) - 1);
        blockedDepth.set(station, depth);
        if (depth === 0) {
          const from = openBlock.get(station);
          if (from !== undefined) bands.push({ from, to: event.t, kind: "blocked" });
          openBlock.delete(station);
        }
        break;
      }
      default:
        break;
    }
  }

  // Anything still open when the log ends ran to the horizon.
  const end = events.length > 0 ? (events[events.length - 1]?.t ?? 0) : 0;
  for (const [station, from] of openDown) lanes.get(station)?.push({ from, to: end, kind: "down" });
  for (const [station, from] of openBlock)
    lanes.get(station)?.push({ from, to: end, kind: "blocked" });

  return stations.map((name) => ({ station: name, bands: lanes.get(name) ?? [] }));
}

interface Props {
  readonly events: readonly SimEvent[];
  readonly stations: readonly string[];
  readonly extent: number;
  readonly time: number;
  readonly onSeek: (time: number) => void;
}

export function Timeline({ events, stations, extent, time, onSeek }: Props) {
  const lanes = useMemo(() => bandsFrom(events, stations), [events, stations]);
  const width = 1000;
  const height = stations.length * (LANE_HEIGHT + LANE_GAP) + 20;
  const span = Math.max(extent, 1);

  const seekFromClick = (clientX: number, target: SVGSVGElement) => {
    const box = target.getBoundingClientRect();
    const fraction = (clientX - box.left) / box.width;
    const scaled = (fraction * width - LABEL_WIDTH) / (width - LABEL_WIDTH);
    onSeek(Math.max(0, Math.min(1, scaled)) * span);
  };

  return (
    <svg
      className="timeline"
      viewBox={`0 0 ${width} ${height}`}
      role="slider"
      aria-label="Run timeline: click to seek"
      aria-valuemin={0}
      aria-valuemax={span}
      aria-valuenow={time}
      tabIndex={0}
      onClick={(clickEvent) => seekFromClick(clickEvent.clientX, clickEvent.currentTarget)}
    >
      {lanes.map((lane, index) => {
        const y = index * (LANE_HEIGHT + LANE_GAP);
        return (
          <g key={lane.station} data-testid={`lane-${lane.station}`}>
            <text x={LABEL_WIDTH - 8} y={y + LANE_HEIGHT - 2} className="lane-label">
              {lane.station.replace(/_/g, " ")}
            </text>
            <rect
              x={LABEL_WIDTH}
              y={y}
              width={width - LABEL_WIDTH}
              height={LANE_HEIGHT}
              fill="#20242f"
              rx={2}
            />
            {lane.bands.map((band, bandIndex) => {
              const x = LABEL_WIDTH + (band.from / span) * (width - LABEL_WIDTH);
              const bandWidth = Math.max(
                1.5,
                ((band.to - band.from) / span) * (width - LABEL_WIDTH),
              );
              return (
                <rect
                  key={bandIndex}
                  x={x}
                  y={y}
                  width={bandWidth}
                  height={LANE_HEIGHT}
                  fill={MODE_STYLES[band.kind].stroke}
                  opacity={band.kind === "down" ? 0.95 : 0.7}
                  rx={1}
                />
              );
            })}
          </g>
        );
      })}
      <line
        className="playhead"
        x1={LABEL_WIDTH + (Math.min(time, span) / span) * (width - LABEL_WIDTH)}
        y1={0}
        x2={LABEL_WIDTH + (Math.min(time, span) / span) * (width - LABEL_WIDTH)}
        y2={stations.length * (LANE_HEIGHT + LANE_GAP)}
        stroke="#f4f6ff"
        strokeWidth={1.5}
      />
      <text x={LABEL_WIDTH} y={height - 4} className="lane-legend">
        red: under repair · amber: blocked, holding a finished board · click to seek
      </text>
    </svg>
  );
}
