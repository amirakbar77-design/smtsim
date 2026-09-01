import { defineConfig, devices } from "@playwright/test";

/**
 * One smoke test, against the real stack.
 *
 * It needs an API with at least one finished run, which `npm run e2e:setup`
 * arranges. The reducer and the seeking are covered exhaustively by Vitest
 * without a browser; what only a browser can tell us is whether pressing play
 * actually moves anything.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 90_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: process.env["CI"] ? "line" : "list",
  use: {
    baseURL: process.env["SMTSIM_WEB_URL"] ?? "http://localhost:5173",
    trace: "retain-on-failure",
    ...devices["Desktop Chrome"],
  },
});
