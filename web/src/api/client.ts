/**
 * The REST client.
 *
 * Everything is served under /api, which the Vite dev server and the production
 * nginx both proxy to the service on the same origin. The browser therefore
 * never makes a cross-origin request and the service needs no CORS
 * configuration at all -- see the README.
 */

import type {
  EventPage,
  NewRunRequest,
  RunDetail,
  RunPage,
  RunStatus,
  ValidationProblem,
} from "./types";

export const API_BASE = "/api";

/** A 422 from the service, carrying the messages its own validators produced. */
export class ApiValidationError extends Error {
  readonly problems: readonly ValidationProblem[];

  constructor(problems: readonly ValidationProblem[]) {
    super(problems.map((problem) => problem.msg).join("; ") || "invalid request");
    this.name = "ApiValidationError";
    this.problems = problems;
  }
}

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });

  if (response.status === 422) {
    const body = (await response.json()) as { detail?: ValidationProblem[] };
    throw new ApiValidationError(body.detail ?? []);
  }
  if (!response.ok) {
    const text = await response.text();
    throw new ApiError(response.status, text || response.statusText);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function listRuns(status?: RunStatus, limit = 50): Promise<RunPage> {
  const query = new URLSearchParams({ limit: String(limit) });
  if (status) query.set("status", status);
  return request<RunPage>(`/runs?${query.toString()}`);
}

export function getRun(id: string): Promise<RunDetail> {
  return request<RunDetail>(`/runs/${id}`);
}

export function deleteRun(id: string): Promise<void> {
  return request<void>(`/runs/${id}`, { method: "DELETE" });
}

export function createRun(body: NewRunRequest): Promise<{ id: string; status: string }> {
  return request<{ id: string; status: string }>("/runs", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getEvents(id: string, after: number, limit = 1000): Promise<EventPage> {
  return request<EventPage>(`/runs/${id}/events?after=${after}&limit=${limit}`);
}

/**
 * The worked example the API already derives from the simulation's own config.
 *
 * Read out of the OpenAPI document rather than written down here. A config
 * literal in the frontend would be a second definition of the line, and it
 * would drift from the backend's the first time a station gained a field.
 */
export async function fetchExampleConfig(): Promise<Record<string, unknown>> {
  const spec = (await request<Record<string, unknown>>("/openapi.json")) as {
    components?: {
      schemas?: {
        RunRequest?: { properties?: { config?: { example?: unknown; default?: unknown } } };
      };
    };
  };
  const field = spec.components?.schemas?.RunRequest?.properties?.config;
  const example = field?.example ?? field?.default;
  if (example === undefined || example === null || typeof example !== "object") {
    throw new ApiError(500, "the API did not advertise an example configuration");
  }
  return example as Record<string, unknown>;
}
