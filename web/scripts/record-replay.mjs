/**
 * Record the replay UI as a GIF.
 *
 * VHS records terminals; this is a browser, so it is a Playwright script that
 * captures frames and hands them to ffmpeg. Needs the compose stack up
 * (`make serve`) and a finished run (`npm run e2e:setup`).
 *
 * The recording is staged rather than just pressing play. At 1000x an
 * eight-hour shift takes half a minute and a four-minute breakdown flashes past
 * in a frame, so the GIF runs the line at speed to show boards flowing, then
 * drops to 100x over a real placer failure -- which is the sequence the whole
 * project is about: the placer goes down, the conveyor behind it fills, and the
 * printer turns amber because it has nowhere to put the board it just finished.
 *
 * Usage: npm run record -- --out ../demo/replay.gif
 */

import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync, readdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

import { chromium } from "@playwright/test";

const OUT = process.argv.includes("--out")
  ? process.argv[process.argv.indexOf("--out") + 1]
  : "../demo/replay.gif";
const WEB = process.env.SMTSIM_WEB_URL ?? "http://localhost:8080";
const API = process.env.SMTSIM_API_URL ?? "http://localhost:8000";
const FPS = 12;

/** Find a placer breakdown long enough to watch, and when it starts. */
async function findFailure(runId) {
  let after = 0;
  const failures = [];
  for (;;) {
    const response = await fetch(`${API}/runs/${runId}/events?after=${after}&limit=1000`);
    const page = await response.json();
    for (const row of page.items) {
      if (row.station !== "pick_and_place") continue;
      if (row.event === "station_failed") failures.push({ from: row.t, to: null });
      if (row.event === "station_repaired" && failures.length > 0) {
        const open = failures[failures.length - 1];
        if (open.to === null) open.to = row.t;
      }
    }
    if (page.next_after === null) break;
    after = page.next_after;
  }
  const usable = failures.filter((f) => f.to !== null && f.to - f.from > 300);
  return usable.sort((a, b) => b.to - b.from - (a.to - a.from))[0] ?? null;
}

const frames = mkdtempSync(join(tmpdir(), "smtsim-frames-"));
const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: 1180, height: 760 },
  deviceScaleFactor: 2,
});

await page.goto(WEB, { waitUntil: "networkidle" });
await page.waitForFunction(
  () => document.querySelector('[data-testid="load-status"]')?.textContent?.includes("complete"),
  { timeout: 60_000 },
);

const runId = await page.evaluate(() =>
  document.querySelector(".run.is-selected")?.getAttribute("data-testid")?.replace("run-", ""),
);
const failure = await findFailure(runId);
console.log(failure ? `placer breakdown at t=${failure.from.toFixed(0)}s` : "no long breakdown found");

// Clip to the part that moves: the line, the controls, the legend, the
// timeline. The summary tables are in the README as text already.
const stage = await page.locator("section.stage").boundingBox();
const statusLine = await page.locator('[data-testid="load-status"]').boundingBox();
const clip = {
  x: stage.x,
  y: stage.y,
  width: stage.width,
  height: statusLine.y + statusLine.height - stage.y + 8,
};

const shoot = async (count) => {
  for (let frame = 0; frame < count; frame += 1) {
    await page.screenshot({
      path: join(frames, `f${String(readdirSync(frames).length).padStart(4, "0")}.png`),
      clip,
    });
    await page.waitForTimeout(1000 / FPS);
  }
};

const setSpeed = (multiplier) =>
  page.getByRole("button", { name: `${multiplier}×`, exact: true }).click();

const seekTo = (seconds) =>
  page.evaluate((target) => {
    const scrub = document.querySelector('[data-testid="scrub"]');
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
    setter.call(scrub, String(target));
    scrub.dispatchEvent(new Event("input", { bubbles: true }));
    scrub.dispatchEvent(new Event("change", { bubbles: true }));
  }, seconds);

// 1. The line running at speed.
await setSpeed(1000);
await page.getByTestId("play-toggle").click();
await shoot(FPS * 9);
await page.getByTestId("play-toggle").click();

// 2. The same run, slowed down over a real breakdown.
if (failure !== null) {
  await seekTo(Math.max(0, failure.from - 90));
  await setSpeed(100);
  await page.waitForTimeout(300);
  await page.getByTestId("play-toggle").click();
  await shoot(FPS * 13);
  await page.getByTestId("play-toggle").click();
}

await browser.close();
console.log(`captured ${readdirSync(frames).length} frames`);

execFileSync(
  "ffmpeg",
  [
    "-y",
    "-framerate", String(FPS),
    "-i", join(frames, "f%04d.png"),
    "-vf",
    `fps=${FPS},scale=1000:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4`,
    "-loop", "0",
    OUT,
  ],
  { stdio: ["ignore", "ignore", "inherit"] },
);

rmSync(frames, { recursive: true, force: true });
console.log(`wrote ${OUT}`);
