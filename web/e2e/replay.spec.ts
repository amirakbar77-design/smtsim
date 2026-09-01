/**
 * The smoke test: does pressing play actually move anything?
 *
 * Everything about the reducer, seeking and keyframes is tested without a
 * browser, because none of it needs one. What needs a browser is the claim
 * that the clock drives a render loop and that the render loop draws boards --
 * so that is all this asserts, and it asserts it against the real API.
 */

import { expect, test } from "@playwright/test";

test("a finished run replays: the clock advances and boards move", async ({ page }) => {
  const pageErrors: string[] = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));

  await page.goto("/");

  // The newest run is selected automatically; wait for its log to load.
  const status = page.getByTestId("load-status");
  await expect(status).toContainText("complete", { timeout: 60_000 });
  await expect(status).toContainText("events loaded");

  // The line is drawn, with the four stations the log described.
  await expect(page.getByTestId("station-solder_paste_printer")).toBeVisible();
  await expect(page.getByTestId("station-pick_and_place")).toBeVisible();
  await expect(page.getByTestId("station-spi")).toBeVisible();
  await expect(page.getByTestId("station-reflow_oven")).toBeVisible();

  const clock = page.getByTestId("clock");
  const boards = page.getByTestId("boards-completed");
  await expect(clock).toHaveText("00:00:00");
  await expect(boards).toHaveText("0 boards");

  await page.getByTestId("play-toggle").click();
  await expect(page.getByTestId("play-toggle")).toHaveText("Pause");

  // The clock advances...
  await expect(clock).not.toHaveText("00:00:00", { timeout: 10_000 });

  // ...and boards actually come off the end of the line.
  await expect(async () => {
    const text = await boards.innerText();
    expect(Number.parseInt(text, 10)).toBeGreaterThan(0);
  }).toPass({ timeout: 20_000 });

  // Stations take on modes from the four-account vocabulary, and no other.
  const modes = await page.locator("[data-mode]").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-mode")),
  );
  expect(modes.length).toBe(4);
  for (const mode of modes) {
    expect(["working", "blocked", "starved", "down"]).toContain(mode);
  }

  // Pausing stops the clock.
  await page.getByTestId("play-toggle").click();
  await expect(page.getByTestId("play-toggle")).toHaveText("Play");
  const paused = await clock.innerText();
  await page.waitForTimeout(700);
  await expect(clock).toHaveText(paused);

  expect(pageErrors).toEqual([]);
});

test("the timeline seeks, forwards and backwards", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("load-status")).toContainText("complete", { timeout: 60_000 });

  const timeline = page.locator(".timeline");
  const boards = page.getByTestId("boards-completed");
  const clock = page.getByTestId("clock");

  const box = await timeline.boundingBox();
  if (box === null) throw new Error("the timeline has no box");

  const seekTo = async (fraction: number) => {
    await timeline.click({ position: { x: box.width * fraction, y: box.height / 2 } });
  };

  await seekTo(0.85);
  await expect(clock).not.toHaveText("00:00:00");
  const late = Number.parseInt(await boards.innerText(), 10);
  expect(late).toBeGreaterThan(0);

  // The reducer only moves forward, so this is the keyframe replay path.
  await seekTo(0.2);
  const early = Number.parseInt(await boards.innerText(), 10);
  expect(early).toBeLessThan(late);

  // And forward again lands back where it was: seeking is a function of time,
  // not of how you got there.
  await seekTo(0.85);
  expect(Number.parseInt(await boards.innerText(), 10)).toBe(late);
});

test("a conveyor fills to its capacity and no further", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("load-status")).toContainText("complete", { timeout: 60_000 });

  const conveyor = page.getByTestId("conveyor-pick_and_place");
  const capacity = Number(await conveyor.getAttribute("data-capacity"));
  expect(capacity).toBeGreaterThan(0);

  await page.getByTestId("play-toggle").click();
  let seenFull = false;
  for (let sample = 0; sample < 60; sample += 1) {
    const occupancy = Number(await conveyor.getAttribute("data-occupancy"));
    expect(occupancy).toBeLessThanOrEqual(capacity);
    if (occupancy === capacity) seenFull = true;
    await page.waitForTimeout(120);
  }
  expect(seenFull).toBe(true);
});
