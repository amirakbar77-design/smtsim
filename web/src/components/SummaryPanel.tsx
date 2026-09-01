/**
 * The summary, rendered exactly as the API returns it.
 *
 * Nothing here is computed. The service already produced every number the CLI
 * prints -- the four time accounts, availability, observed MTBF and MTTR -- and
 * recomputing any of it in the browser would be a second implementation of
 * stats.py waiting to disagree with the first.
 */

import { MODE_ORDER, MODE_STYLES } from "./palette";
import type { RunSummaryBody } from "../api/types";

const FRACTION_KEYS = {
  working: "working_fraction",
  blocked: "blocked_fraction",
  starved: "starved_fraction",
  down: "down_fraction",
} as const;

function percent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function minutes(seconds: number | null): string {
  return seconds === null ? "—" : `${(seconds / 60).toFixed(1)} min`;
}

export function SummaryPanel({ summary }: { summary: RunSummaryBody }) {
  return (
    <div className="summary">
      <div className="summary-headline">
        <Figure label="boards completed" value={String(summary.boards_completed)} />
        <Figure label="throughput" value={`${summary.throughput_per_hour.toFixed(1)} /h`} />
        <Figure label="mean cycle time" value={`${summary.mean_cycle_time_minutes.toFixed(2)} min`} />
        <Figure label="p95 cycle time" value={`${summary.p95_cycle_time_minutes.toFixed(2)} min`} />
        <Figure label="bottleneck" value={summary.bottleneck ?? "—"} />
      </div>

      <table className="summary-table">
        <caption>
          Time accounts — these four partition each station&rsquo;s capacity and sum to 100%
        </caption>
        <thead>
          <tr>
            <th scope="col">station</th>
            <th scope="col">cap</th>
            {MODE_ORDER.map((mode) => (
              <th scope="col" key={mode}>
                <span className="swatch" style={{ background: MODE_STYLES[mode].stroke }} />
                {mode}
              </th>
            ))}
            <th scope="col">max q</th>
            <th scope="col">wait</th>
          </tr>
        </thead>
        <tbody>
          {summary.stations.map((station) => (
            <tr key={station.name}>
              <th scope="row">{station.name.replace(/_/g, " ")}</th>
              <td>{station.capacity}</td>
              {MODE_ORDER.map((mode) => (
                <td key={mode}>{percent(station[FRACTION_KEYS[mode]])}</td>
              ))}
              <td>{station.max_queue_length}</td>
              <td>{station.mean_wait_seconds.toFixed(1)} s</td>
            </tr>
          ))}
        </tbody>
      </table>

      {summary.has_failures && (
        <table className="summary-table">
          <caption>Reliability</caption>
          <thead>
            <tr>
              <th scope="col">station</th>
              <th scope="col">availability</th>
              <th scope="col">util (uptime)</th>
              <th scope="col">failures</th>
              <th scope="col">MTBF obs</th>
              <th scope="col">MTTR obs</th>
            </tr>
          </thead>
          <tbody>
            {summary.stations
              .filter((station) => station.failures > 0 || station.downtime_seconds > 0)
              .map((station) => (
                <tr key={station.name}>
                  <th scope="row">{station.name.replace(/_/g, " ")}</th>
                  <td>{percent(station.availability)}</td>
                  <td>{percent(station.utilisation_uptime)}</td>
                  <td>{station.failures}</td>
                  <td>{minutes(station.observed_mtbf_seconds)}</td>
                  <td>{minutes(station.observed_mttr_seconds)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function Figure({ label, value }: { label: string; value: string }) {
  return (
    <div className="figure">
      <span className="figure-value">{value}</span>
      <span className="figure-label">{label}</span>
    </div>
  );
}
