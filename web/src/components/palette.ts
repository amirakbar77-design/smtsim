/**
 * The four time accounts, and their colours.
 *
 * These are the same four names the CLI's station table prints, the same four
 * the API's summary returns, and the same four that must sum to 100% of a
 * station's capacity over the measured window. There is deliberately no fifth.
 *
 * The colours are chosen so the story reads at a glance: green is producing,
 * grey is idle-but-fine, amber is the machine being held up by something
 * downstream, red is broken. Amber and red are the two that matter -- a viewer
 * should be able to watch the placer go red and the printer turn amber behind
 * it without being told that is what to look for.
 */

import type { StationMode } from "../replay/types";

export interface ModeStyle {
  readonly fill: string;
  readonly stroke: string;
  readonly label: string;
  readonly description: string;
}

export const MODE_STYLES: Record<StationMode, ModeStyle> = {
  working: {
    fill: "#1f6f43",
    stroke: "#35b06b",
    label: "working",
    description: "a board under the head, being processed",
  },
  blocked: {
    fill: "#8a5a12",
    stroke: "#e0a33c",
    label: "blocked",
    description: "finished a board and holding it — no room downstream",
  },
  starved: {
    fill: "#33384a",
    stroke: "#5b6480",
    label: "starved",
    description: "free capacity with no board to work on",
  },
  down: {
    fill: "#7c2532",
    stroke: "#e0566e",
    label: "down",
    description: "under repair",
  },
};

export const MODE_ORDER: readonly StationMode[] = ["working", "blocked", "starved", "down"];

export const BOARD_FILL = "#8fd3ff";
export const BOARD_STROKE = "#2b6f96";
