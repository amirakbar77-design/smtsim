/**
 * The REST fallback, which exists for the documented 1013 case.
 *
 * The bug this pins: paging until a short page comes back only means we have
 * caught up with what has been *persisted*, not that the run is over. The first
 * version stopped there and reported a 335,000-event run as complete at 40,000.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { pageEverything } from "../src/api/stream";
import type { SimEvent } from "../src/replay/types";

interface Row {
  seq: number;
  t: number;
  event: string;
  board: number | null;
  station: string | null;
  detail: null;
}

function rows(from: number, count: number): Row[] {
  return Array.from({ length: count }, (_, index) => ({
    seq: from + index,
    t: from + index,
    event: "board_arrived",
    board: from + index,
    station: null,
    detail: null,
  }));
}

/** A fake service whose run keeps producing events after the first catch-up. */
function fakeApi(options: { total: number; revealed: number; finishesAfter: number }) {
  let statusChecks = 0;
  let revealed = options.revealed;

  return vi.fn(async (url: string) => {
    if (url.includes("/events")) {
      const after = Number(new URL(url, "http://x").searchParams.get("after"));
      const limit = Number(new URL(url, "http://x").searchParams.get("limit"));
      const available = Math.max(0, revealed - after);
      const items = rows(after + 1, Math.min(limit, available));
      return {
        ok: true,
        status: 200,
        json: async () => ({
          run_id: "r",
          items,
          next_after: items.length === limit ? items[items.length - 1]?.seq : null,
          limit,
        }),
      };
    }
    statusChecks += 1;
    // The run keeps going for a while, revealing more each time it is asked.
    if (statusChecks >= options.finishesAfter) revealed = options.total;
    else revealed = Math.min(options.total, revealed + 500);
    return {
      ok: true,
      status: 200,
      json: async () => ({
        id: "r",
        status: statusChecks >= options.finishesAfter ? "succeeded" : "running",
        error: null,
      }),
    };
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("pageEverything", () => {
  it("keeps paging until the run is actually finished", async () => {
    vi.stubGlobal("fetch", fakeApi({ total: 3000, revealed: 900, finishesAfter: 3 }));

    const received: SimEvent[] = [];
    let ended: string | null = null;
    await pageEverything(
      "r",
      {
        onEvents: (batch) => received.push(...batch),
        onEnd: (status) => {
          ended = status;
        },
        onError: (message) => {
          throw new Error(message);
        },
      },
      500,
      0,
    );

    expect(received).toHaveLength(3000);
    expect(ended).toBe("succeeded");
    expect(received[0]?.board).toBe(1);
    expect(received[received.length - 1]?.board).toBe(3000);
  });

  it("stops immediately when the run has already finished", async () => {
    vi.stubGlobal("fetch", fakeApi({ total: 1200, revealed: 1200, finishesAfter: 1 }));

    const received: SimEvent[] = [];
    await pageEverything(
      "r",
      { onEvents: (b) => received.push(...b), onEnd: () => undefined, onError: () => undefined },
      500,
      0,
    );

    expect(received).toHaveLength(1200);
  });

  it("reports an error rather than looping forever", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => ({ ok: false, status: 500, statusText: "boom", text: async () => "boom" })),
    );

    let message: string | null = null;
    await pageEverything(
      "r",
      { onEvents: () => undefined, onEnd: () => undefined, onError: (m) => (message = m) },
      500,
      0,
    );

    expect(message).toContain("boom");
  });
});
