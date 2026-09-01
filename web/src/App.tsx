/**
 * The app shell: pick a run, watch it, read its summary.
 *
 * Comparisons are listed nowhere and rendered nowhere. The API serves them and
 * the CLI reads them well; a paired-difference view is its own piece of work
 * and is out of scope for this stage.
 */

import { useCallback, useEffect, useState } from "react";

import { getRun } from "./api/client";
import type { RunDetail } from "./api/types";
import { Controls } from "./components/Controls";
import { LineView } from "./components/LineView";
import { MODE_ORDER, MODE_STYLES } from "./components/palette";
import { NewRunForm } from "./components/NewRunForm";
import { RunList, useRunList } from "./components/RunList";
import { SummaryPanel } from "./components/SummaryPanel";
import { Timeline } from "./components/Timeline";
import { useReplay } from "./replay/useReplay";

export function App() {
  const { runs, error: listError, refresh } = useRunList();
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const replay = useReplay(selected);

  // Select the newest run once, so the page is not empty on arrival.
  useEffect(() => {
    if (selected === null && runs.length > 0) setSelected(runs[0]?.id ?? null);
  }, [runs, selected]);

  useEffect(() => {
    if (selected === null) return;
    let cancelled = false;
    const load = () =>
      void getRun(selected)
        .then((body) => {
          if (!cancelled) setDetail(body);
        })
        .catch(() => undefined);
    load();
    // The summary only appears when the run finishes, so re-read once the
    // replay says the stream ended.
    if (replay.phase === "complete") load();
    return () => {
      cancelled = true;
    };
  }, [selected, replay.phase]);

  const onStarted = useCallback(
    (runId: string) => {
      setSelected(runId);
      refresh();
    },
    [refresh],
  );

  const stationNames = replay.state.stations.map((station) => station.name);

  return (
    <div className="app">
      <header>
        <h1>smtsim</h1>
        <p className="tagline">
          Replay of a simulated SMT assembly line. Boards enter at the left, pass through four
          machines with conveyors between them, and leave at the right.
        </p>
      </header>

      <main>
        <section className="stage">
          {replay.notice !== null && (
            <p className="notice" data-testid="fallback-notice">
              {replay.notice}
            </p>
          )}
          {replay.error !== null && (
            <p className="notice is-error" data-testid="stream-error">
              {replay.error}
            </p>
          )}

          <LineView state={replay.state} />

          <Controls
            playing={replay.playing}
            speed={replay.speed}
            time={replay.time}
            extent={replay.extent}
            boardsCompleted={replay.state.boardsCompleted}
            live={replay.mode === "live"}
            following={replay.following}
            onToggle={replay.toggle}
            onSpeed={replay.setSpeed}
            onSeek={replay.seek}
            onFollowing={replay.setFollowing}
          />

          <p className="legend">
            {MODE_ORDER.map((mode) => (
              <span key={mode} className="legend-item">
                <span className="swatch" style={{ background: MODE_STYLES[mode].stroke }} />
                <strong>{mode}</strong> — {MODE_STYLES[mode].description}
              </span>
            ))}
          </p>

          {stationNames.length > 0 && (
            <Timeline
              events={replay.events}
              stations={stationNames}
              extent={replay.extent}
              time={replay.time}
              onSeek={replay.seek}
            />
          )}

          <p className="status-line" data-testid="load-status">
            {replay.mode === null ? "connecting" : replay.mode} · {replay.phase} ·{" "}
            {replay.eventCount.toLocaleString()} events loaded
          </p>

          {detail?.summary != null && <SummaryPanel summary={detail.summary} />}
        </section>

        <aside>
          <h2>Runs</h2>
          {listError !== null && <p className="notice is-error">{listError}</p>}
          <RunList runs={runs} selected={selected} onSelect={setSelected} />
          <h2>New run</h2>
          <NewRunForm onStarted={onStarted} />
          <p className="caveat">
            This service has no authentication. Anyone who can reach the page can start runs and
            read or delete every run stored here.
          </p>
        </aside>
      </main>
    </div>
  );
}
