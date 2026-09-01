/**
 * Make sure the API has a finished run for the smoke test to replay.
 *
 * The UI selects the newest run on load, so the test needs a known, modest one
 * to be newest -- not whatever 20,000-minute experiment happened to run last.
 */

const API = process.env.SMTSIM_API_URL ?? "http://localhost:8000";

async function json(path, init) {
  const response = await fetch(`${API}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) throw new Error(`${path}: ${response.status} ${await response.text()}`);
  return response.json();
}

const spec = await json("/openapi.json");
const config = spec.components.schemas.RunRequest.properties.config.example;

const created = await json("/runs", {
  method: "POST",
  body: JSON.stringify({ config, minutes: 480, seed: 42, warmup_minutes: 30 }),
});

for (let attempt = 0; attempt < 120; attempt += 1) {
  const run = await json(`/runs/${created.id}`);
  if (run.status === "succeeded") {
    console.log(`seeded run ${created.id}: ${run.event_count} events`);
    process.exit(0);
  }
  if (run.status === "failed") throw new Error(`seed run failed: ${run.error}`);
  await new Promise((resolve) => setTimeout(resolve, 250));
}
throw new Error("seed run did not finish in time");
