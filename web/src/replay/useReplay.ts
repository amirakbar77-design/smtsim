/**
 * Wiring the loader to the clock.
 *
 * This is where the loader-versus-clock distinction becomes concrete. The
 * stream fills a ReplayTimeline as fast as the network delivers; the clock
 * walks that timeline at whatever speed the viewer chose. Neither knows about
 * the other's rate.
 *
 * There is exactly one code path for a run that is still going and one that
 * finished last week. The socket decides which it is and says so in its opening
 * frame; all this hook does with the answer is record it, so that the scrub
 * bar's extent is understood to be growing and a follow-the-tail toggle
 * appears. Everything downstream -- the timeline, the reducer, the clock, every
 * component -- is identical in both cases.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { PlaybackClock, DEFAULT_SPEED } from "./clock";
import type { Speed } from "./clock";
import { ReplayTimeline } from "./keyframes";
import { initialState } from "./reducer";
import type { LineState, SimEvent } from "./types";
import { openRunStream } from "../api/stream";
import type { StreamMode } from "../api/stream";

export type LoadPhase = "connecting" | "loading" | "complete" | "error";

export interface ReplayHandle {
  readonly state: LineState;
  readonly time: number;
  readonly playing: boolean;
  readonly speed: Speed;
  readonly following: boolean;
  readonly mode: StreamMode | null;
  readonly phase: LoadPhase;
  readonly notice: string | null;
  readonly error: string | null;
  /** Every event loaded so far, for the timeline strip. */
  readonly events: readonly SimEvent[];
  /** The right-hand end of the scrub bar. Grows during a live run. */
  readonly extent: number;
  readonly eventCount: number;
  toggle(): void;
  setSpeed(speed: Speed): void;
  seek(time: number): void;
  setFollowing(following: boolean): void;
}

export function useReplay(runId: string | null): ReplayHandle {
  const [state, setState] = useState<LineState>(() => initialState());
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeedState] = useState<Speed>(DEFAULT_SPEED);
  const [following, setFollowingState] = useState(false);
  const [mode, setMode] = useState<StreamMode | null>(null);
  const [phase, setPhase] = useState<LoadPhase>("connecting");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<SimEvent[]>([]);
  const [extent, setExtent] = useState(0);

  const timelineRef = useRef<ReplayTimeline | null>(null);
  const clockRef = useRef<PlaybackClock | null>(null);

  useEffect(() => {
    if (runId === null) return;

    const timeline = new ReplayTimeline(initialState());
    // The clock reports once per animation frame, not once per event. Feeding
    // setState per event would be thousands of React renders in a tenth of a
    // second, which is the failure mode this whole design exists to avoid.
    const clock = new PlaybackClock(timeline, (nextState, nextTime, isPlaying) => {
      setState(nextState);
      setTime(nextTime);
      setPlaying(isPlaying);
    });

    timelineRef.current = timeline;
    clockRef.current = clock;

    setState(initialState());
    setTime(0);
    setPlaying(false);
    setEvents([]);
    setExtent(0);
    setMode(null);
    setNotice(null);
    setError(null);
    setPhase("connecting");
    setFollowingState(false);

    // Accumulate arrivals and flush on a frame rather than on every batch: the
    // socket can deliver hundreds of batches in the time React would like to
    // render once.
    let pending: SimEvent[] = [];
    let flushHandle: number | null = null;

    const flush = () => {
      flushHandle = null;
      if (pending.length === 0) return;
      const batch = pending;
      pending = [];
      timeline.append(batch);
      clock.onLoaded();
      setEvents(timeline.slice(0, timeline.length));
      setExtent(Math.max(timeline.loadedUntil, timeline.stateAtCursor(1).horizon));
    };

    const scheduleFlush = () => {
      if (flushHandle === null) flushHandle = requestAnimationFrame(flush);
    };

    const handle = openRunStream(runId, {
      onOpen(streamMode) {
        setMode(streamMode);
        setPhase("loading");
        if (streamMode === "live") {
          setFollowingState(true);
          clock.setFollowing(true);
        }
      },
      onEvents(batch) {
        pending.push(...batch);
        scheduleFlush();
      },
      onEnd(status, endError) {
        flush();
        setPhase(status === "failed" ? "error" : "complete");
        if (endError !== null) setError(endError);
      },
      onFellBack(reason) {
        // Documented behaviour, not a fault: see api/stream.ts. Everything
        // loaded so far is discarded and the lossless path starts from the top.
        pending = [];
        setNotice(reason);
        setMode("replay");
        setFollowingState(false);
        clock.setFollowing(false);
      },
      onError(message) {
        setError(message);
        setPhase("error");
      },
    });

    return () => {
      if (flushHandle !== null) cancelAnimationFrame(flushHandle);
      handle.close();
      clock.dispose();
      timelineRef.current = null;
      clockRef.current = null;
    };
  }, [runId]);

  const toggle = useCallback(() => clockRef.current?.toggle(), []);
  const seek = useCallback((target: number) => {
    clockRef.current?.setFollowing(false);
    setFollowingState(false);
    clockRef.current?.seek(target);
  }, []);
  const setSpeed = useCallback((next: Speed) => {
    setSpeedState(next);
    clockRef.current?.setSpeed(next);
  }, []);
  const setFollowing = useCallback((next: boolean) => {
    setFollowingState(next);
    clockRef.current?.setFollowing(next);
  }, []);

  return useMemo(
    () => ({
      state,
      time,
      playing,
      speed,
      following,
      mode,
      phase,
      notice,
      error,
      events,
      extent,
      eventCount: events.length,
      toggle,
      setSpeed,
      seek,
      setFollowing,
    }),
    [
      state, time, playing, speed, following, mode, phase, notice, error, events, extent,
      toggle, setSpeed, seek, setFollowing,
    ],
  );
}
