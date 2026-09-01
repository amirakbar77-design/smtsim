/**
 * Starting a run.
 *
 * The configuration is prefilled from the worked example the API advertises in
 * its own OpenAPI document, which the service derives from the simulation's
 * BASELINE_LINE. A config literal here would be a second definition of the
 * line and would drift the first time a station gained a field.
 *
 * 422 messages are shown exactly as the service produced them. They come from
 * the same validators the CLI uses -- "station 'pick_and_place' input_buffer
 * must be >= 1, or omitted for unbounded" -- and rewording them here would only
 * make them worse.
 */

import { useEffect, useState } from "react";

import { ApiValidationError, createRun, fetchExampleConfig } from "../api/client";

interface Props {
  readonly onStarted: (runId: string) => void;
}

export function NewRunForm({ onStarted }: Props) {
  const [configText, setConfigText] = useState("");
  const [minutes, setMinutes] = useState(480);
  const [seed, setSeed] = useState(42);
  const [warmup, setWarmup] = useState(30);
  const [problems, setProblems] = useState<readonly string[]>([]);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    void fetchExampleConfig()
      .then((example) => setConfigText(JSON.stringify(example, null, 2)))
      .catch((caught: unknown) =>
        setProblems([caught instanceof Error ? caught.message : String(caught)]),
      );
  }, []);

  const submit = async (submitEvent: React.FormEvent) => {
    submitEvent.preventDefault();
    setProblems([]);
    setBusy(true);
    try {
      const config = JSON.parse(configText) as Record<string, unknown>;
      const created = await createRun({
        config,
        minutes,
        seed,
        warmup_minutes: warmup,
      });
      onStarted(created.id);
    } catch (caught) {
      if (caught instanceof ApiValidationError) {
        setProblems(
          caught.problems.map((problem) => `${problem.loc.join(".")}: ${problem.msg}`),
        );
      } else if (caught instanceof SyntaxError) {
        setProblems([`the configuration is not valid JSON: ${caught.message}`]);
      } else {
        setProblems([caught instanceof Error ? caught.message : String(caught)]);
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="new-run" onSubmit={(formEvent) => void submit(formEvent)}>
      <div className="fields">
        <label>
          minutes
          <input
            type="number"
            min={1}
            value={minutes}
            onChange={(changeEvent) => setMinutes(Number(changeEvent.target.value))}
          />
        </label>
        <label>
          seed
          <input
            type="number"
            min={0}
            value={seed}
            onChange={(changeEvent) => setSeed(Number(changeEvent.target.value))}
          />
        </label>
        <label>
          warm-up
          <input
            type="number"
            min={0}
            value={warmup}
            onChange={(changeEvent) => setWarmup(Number(changeEvent.target.value))}
          />
        </label>
        <button type="submit" disabled={busy || configText === ""} data-testid="start-run">
          {busy ? "Starting…" : "Start run"}
        </button>
      </div>

      <button type="button" className="disclosure" onClick={() => setOpen(!open)}>
        {open ? "Hide" : "Edit"} line configuration
      </button>

      {open && (
        <textarea
          className="config-editor"
          spellCheck={false}
          rows={18}
          value={configText}
          onChange={(changeEvent) => setConfigText(changeEvent.target.value)}
          aria-label="Line configuration, as JSON"
        />
      )}

      {problems.length > 0 && (
        <ul className="problems" data-testid="problems">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
    </form>
  );
}
