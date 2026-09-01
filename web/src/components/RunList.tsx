/**
 * The run list.
 *
 * Polls only while something is actually running. A list of finished runs does
 * not change on its own, and polling it would be a request every few seconds
 * for the rest of the tab's life.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { listRuns } from "../api/client";
import type { RunListItem } from "../api/types";

const POLL_MS = 1500;

export function useRunList(): {
  runs: readonly RunListItem[];
  error: string | null;
  refresh: () => void;
} {
  const [runs, setRuns] = useState<readonly RunListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  const load = useCallback(async () => {
    try {
      const page = await listRuns(undefined, 50);
      setRuns(page.items);
      setError(null);
      return page.items;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
      return [];
    }
  }, []);

  useEffect(() => {
    let cancelled = false;

    const cycle = async () => {
      const items = await load();
      if (cancelled) return;
      const busy = items.some((run) => run.status === "queued" || run.status === "running");
      // Only keep asking while there is something whose answer can change.
      if (busy) timer.current = window.setTimeout(cycle, POLL_MS);
    };

    void cycle();
    return () => {
      cancelled = true;
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, [load]);

  return { runs, error, refresh: () => void load() };
}

interface Props {
  readonly runs: readonly RunListItem[];
  readonly selected: string | null;
  readonly onSelect: (id: string) => void;
}

export function RunList({ runs, selected, onSelect }: Props) {
  if (runs.length === 0) {
    return <p className="empty">No runs yet. Start one below.</p>;
  }

  return (
    <ul className="run-list">
      {runs.map((run) => (
        <li key={run.id}>
          <button
            type="button"
            className={run.id === selected ? "run is-selected" : "run"}
            onClick={() => onSelect(run.id)}
            data-testid={`run-${run.id}`}
            data-status={run.status}
          >
            <span className={`status status-${run.status}`}>{run.status}</span>
            <span className="run-meta">
              {run.minutes.toFixed(0)} min · seed {run.seed}
              {run.warmup_minutes > 0 && ` · ${run.warmup_minutes.toFixed(0)} min warm-up`}
            </span>
            <span className="run-id">{run.id.slice(0, 8)}</span>
            {run.error !== null && <span className="run-error">{run.error}</span>}
          </button>
        </li>
      ))}
    </ul>
  );
}
