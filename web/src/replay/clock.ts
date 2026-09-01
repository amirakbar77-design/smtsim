/**
 * The playback clock.
 *
 * This is the half of the design that makes a replay watchable. The loader
 * delivers a whole shift in a tenth of a second; the clock decides what the
 * viewer sees, and at what rate, entirely independently of that.
 *
 * Each animation frame it computes how much *simulated* time should have passed
 * from how much *wall* time actually passed, times the speed multiplier, then
 * asks the timeline for the state at that moment and reports it once. Applying
 * two thousand events in a frame costs one callback, not two thousand -- which
 * is the difference between a smooth replay and a browser tab that stops
 * answering.
 */

import type { ReplayTimeline } from "./keyframes";
import type { LineState } from "./types";

/** Simulated seconds per wall-clock second. */
export const SPEEDS = [1, 10, 100, 1000, 5000] as const;
export type Speed = (typeof SPEEDS)[number];

/** A 480-minute shift takes about 29 seconds at this speed. */
export const DEFAULT_SPEED: Speed = 1000;

export interface ClockListener {
  (state: LineState, time: number, playing: boolean): void;
}

export class PlaybackClock {
  private timeline: ReplayTimeline;
  private readonly listener: ClockListener;
  private readonly raf: (callback: (now: number) => void) => number;
  private readonly cancelRaf: (handle: number) => void;

  private time = 0;
  private speed: Speed = DEFAULT_SPEED;
  private playing = false;
  private handle: number | null = null;
  private lastFrameAt = 0;
  private state: LineState;
  /** Follow the growing tail of a live run rather than a fixed time. */
  private following = false;

  constructor(
    timeline: ReplayTimeline,
    listener: ClockListener,
    scheduler?: {
      request: (callback: (now: number) => void) => number;
      cancel: (handle: number) => void;
    },
  ) {
    this.timeline = timeline;
    this.listener = listener;
    this.raf = scheduler?.request ?? ((callback) => requestAnimationFrame(callback));
    this.cancelRaf = scheduler?.cancel ?? ((handle) => cancelAnimationFrame(handle));
    this.state = timeline.stateAtCursor(0);
  }

  get currentTime(): number {
    return this.time;
  }

  get currentSpeed(): Speed {
    return this.speed;
  }

  get isPlaying(): boolean {
    return this.playing;
  }

  get isFollowing(): boolean {
    return this.following;
  }

  setTimeline(timeline: ReplayTimeline): void {
    this.timeline = timeline;
  }

  setSpeed(speed: Speed): void {
    this.speed = speed;
  }

  setFollowing(following: boolean): void {
    this.following = following;
    if (following) this.seek(this.timeline.loadedUntil);
  }

  play(): void {
    if (this.playing) return;
    // Restarting at the end starts again from the beginning, which is what
    // pressing play on a finished replay is asking for.
    if (this.time >= this.timeline.loadedUntil && !this.following) this.seek(0);
    this.playing = true;
    this.lastFrameAt = 0;
    this.schedule();
    this.emit();
  }

  pause(): void {
    if (!this.playing) return;
    this.playing = false;
    if (this.handle !== null) this.cancelRaf(this.handle);
    this.handle = null;
    this.emit();
  }

  toggle(): void {
    if (this.playing) this.pause();
    else this.play();
  }

  /** Jump to a moment, in either direction. */
  seek(time: number): void {
    this.time = Math.max(0, time);
    this.state = this.timeline.stateAtTime(this.time);
    this.emit();
  }

  /**
   * New events arrived.
   *
   * Following the tail is the single concession live mode gets. Otherwise
   * playback is untouched -- except that a paused clock re-derives its state,
   * because the timeline it was reading has just grown underneath it. Without
   * that, a freshly loaded run shows nothing at all until you press play: the
   * clock sits at t=0 holding the empty state it was constructed with, and the
   * `run_started` header that creates the stations has never been applied.
   */
  onLoaded(): void {
    if (this.following) {
      this.seek(this.timeline.loadedUntil);
    } else if (!this.playing) {
      this.state = this.timeline.stateAtTime(this.time);
      this.emit();
    }
  }

  dispose(): void {
    if (this.handle !== null) this.cancelRaf(this.handle);
    this.handle = null;
    this.playing = false;
  }

  private schedule(): void {
    this.handle = this.raf((now) => this.tick(now));
  }

  private tick(now: number): void {
    if (!this.playing) return;

    if (this.lastFrameAt === 0) this.lastFrameAt = now;
    const wallDelta = Math.max(0, now - this.lastFrameAt);
    this.lastFrameAt = now;

    // A tab that was backgrounded can hand back an enormous delta. Clamping it
    // means returning to the tab resumes rather than teleporting to the end.
    const simDelta = (Math.min(wallDelta, 250) / 1000) * this.speed;
    const target = this.time + simDelta;
    const end = this.timeline.loadedUntil;

    if (target >= end && !this.following) {
      this.time = end;
      this.state = this.timeline.advance(this.state, end);
      this.playing = false;
      this.handle = null;
      this.emit();
      return;
    }

    this.time = target;
    // `advance` is the cheap path: the state is already correct up to its own
    // cursor, so playing forward never touches a keyframe.
    this.state = this.timeline.advance(this.state, target);
    this.emit();
    this.schedule();
  }

  private emit(): void {
    this.listener(this.state, this.time, this.playing);
  }
}
