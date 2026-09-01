/** Response shapes, mirroring the service's pydantic models. */

export type RunStatus = "queued" | "running" | "succeeded" | "failed";

export interface StationSummary {
  readonly name: string;
  readonly capacity: number;
  readonly working_fraction: number;
  readonly blocked_fraction: number;
  readonly starved_fraction: number;
  readonly down_fraction: number;
  readonly utilisation: number;
  readonly utilisation_uptime: number;
  readonly availability: number;
  readonly max_queue_length: number;
  readonly mean_wait_seconds: number;
  readonly mean_service_seconds: number;
  readonly failures: number;
  readonly downtime_seconds: number;
  readonly observed_mtbf_seconds: number | null;
  readonly observed_mttr_seconds: number | null;
}

export interface RunSummaryBody {
  readonly line_name: string;
  readonly seed: number | null;
  readonly horizon_seconds: number;
  readonly warmup_seconds: number;
  readonly window_seconds: number;
  readonly boards_arrived: number;
  readonly boards_completed: number;
  readonly boards_in_system: number;
  readonly throughput_per_hour: number;
  readonly mean_cycle_time_minutes: number;
  readonly p95_cycle_time_minutes: number;
  readonly has_failures: boolean;
  readonly bottleneck: string | null;
  readonly stations: readonly StationSummary[];
}

export interface RunListItem {
  readonly id: string;
  readonly status: RunStatus;
  readonly seed: number;
  readonly minutes: number;
  readonly warmup_minutes: number;
  readonly event_count: number;
  readonly stores_events: boolean;
  readonly error: string | null;
  readonly created_at: string;
  readonly started_at: string | null;
  readonly finished_at: string | null;
}

export interface RunDetail extends RunListItem {
  readonly config: Record<string, unknown>;
  readonly summary: RunSummaryBody | null;
}

export interface RunPage {
  readonly items: readonly RunListItem[];
  readonly total: number;
  readonly limit: number;
  readonly offset: number;
}

export interface EventPage {
  readonly run_id: string;
  readonly items: readonly EventRow[];
  readonly next_after: number | null;
  readonly limit: number;
}

export interface EventRow {
  readonly seq: number;
  readonly t: number;
  readonly event: string;
  readonly board: number | null;
  readonly station: string | null;
  readonly detail: Record<string, unknown> | null;
}

export interface NewRunRequest {
  readonly config: Record<string, unknown>;
  readonly minutes: number;
  readonly seed: number;
  readonly warmup_minutes: number;
}

/** A 422 body from FastAPI, kept in its own shape so messages survive intact. */
export interface ValidationProblem {
  readonly loc: readonly (string | number)[];
  readonly msg: string;
  readonly type: string;
}
