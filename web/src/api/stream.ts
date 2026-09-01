/**
 * Loading a run's events. One code path for a run that is still going and one
 * that finished last week.
 *
 * `WS /runs/{id}/stream` decides which: it sends live events for a running run
 * and replays the stored ones for a finished run, and says which in its opening
 * frame. The only thing this module does differently between the two is report
 * the mode; the buffer, the reducer and the clock never find out.
 *
 * **The WebSocket is a loader, not a clock.** Events arrive as fast as the
 * network allows -- a 480-minute shift lands in about a tenth of a second --
 * and go straight into the timeline. What the viewer sees is decided entirely
 * by the playback clock, which is why speed is independent of arrival rate.
 */

import { getEvents } from "./client";
import type { SimEvent } from "../replay/types";

export type StreamMode = "live" | "replay";

/**
 * The service closes with 1013 when a client cannot keep up with a live run.
 * That is documented behaviour, not a fault: every event mutates line state, so
 * the service disconnects rather than hand over a stream with holes in it. The
 * lossless path is the paginated REST endpoint, which is what we fall back to.
 */
export const WS_TRY_AGAIN_LATER = 1013;

export interface StreamCallbacks {
  onOpen(mode: StreamMode): void;
  onEvents(events: SimEvent[]): void;
  onEnd(status: string, error: string | null): void;
  /** Called when the socket gave up and the REST fallback took over. */
  onFellBack(reason: string): void;
  onError(message: string): void;
}

interface OpeningFrame {
  type: "start";
  mode: StreamMode;
}

interface EventsFrame {
  type: "events";
  events: SimEvent[];
}

interface EndFrame {
  type: "end";
  status: string;
  error: string | null;
}

type Frame = OpeningFrame | EventsFrame | EndFrame;

export interface StreamHandle {
  close(): void;
}

function socketUrl(runId: string): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/runs/${runId}/stream`;
}

export function openRunStream(runId: string, callbacks: StreamCallbacks): StreamHandle {
  let closedByUs = false;
  let fellBack = false;
  const socket = new WebSocket(socketUrl(runId));

  socket.onmessage = (message: MessageEvent<string>) => {
    const frame = JSON.parse(message.data) as Frame;
    switch (frame.type) {
      case "start":
        callbacks.onOpen(frame.mode);
        break;
      case "events":
        callbacks.onEvents(frame.events);
        break;
      case "end":
        callbacks.onEnd(frame.status, frame.error);
        break;
    }
  };

  socket.onerror = () => {
    if (!closedByUs && !fellBack) callbacks.onError("the event stream could not be reached");
  };

  socket.onclose = (close: CloseEvent) => {
    if (closedByUs || fellBack) return;
    if (close.code === WS_TRY_AGAIN_LATER) {
      fellBack = true;
      callbacks.onFellBack(
        close.reason ||
          "the live stream outran this browser, so the run is being loaded from storage instead",
      );
      void pageEverything(runId, callbacks);
    }
  };

  return {
    close() {
      closedByUs = true;
      socket.close();
    },
  };
}

/**
 * Load the whole log through the REST endpoint.
 *
 * Deliberately restarts from the beginning rather than resuming where the
 * socket stopped. Frames carry no sequence number, and in live mode the socket
 * starts wherever the run had got to, so there is no reliable way to line up
 * what arrived with what to ask for next. Reloading a few thousand rows is
 * cheap; a stream stitched together at the wrong offset is not.
 */
export async function pageEverything(
  runId: string,
  callbacks: Pick<StreamCallbacks, "onEvents" | "onEnd" | "onError">,
  pageSize = 1000,
): Promise<void> {
  try {
    let after = 0;
    for (;;) {
      const page = await getEvents(runId, after, pageSize);
      callbacks.onEvents(
        page.items.map((row) => ({
          t: row.t,
          event: row.event as SimEvent["event"],
          board: row.board,
          station: row.station,
          detail: row.detail,
        })),
      );
      if (page.next_after === null) break;
      after = page.next_after;
    }
    callbacks.onEnd("succeeded", null);
  } catch (error) {
    callbacks.onError(error instanceof Error ? error.message : String(error));
  }
}
