/**
 * Transport controls over simulated time.
 *
 * The speeds are multiples of real time: at 1x one simulated second takes one
 * wall second, so an eight-hour shift would take eight hours. 1000x is the
 * default because it brings that shift down to about half a minute, which is
 * roughly how long someone will watch.
 */

import { SPEEDS } from "../replay/clock";
import type { Speed } from "../replay/clock";

export function formatSimTime(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

interface Props {
  readonly playing: boolean;
  readonly speed: Speed;
  readonly time: number;
  readonly extent: number;
  readonly boardsCompleted: number;
  readonly live: boolean;
  readonly following: boolean;
  readonly onToggle: () => void;
  readonly onSpeed: (speed: Speed) => void;
  readonly onSeek: (time: number) => void;
  readonly onFollowing: (following: boolean) => void;
}

export function Controls({
  playing,
  speed,
  time,
  extent,
  boardsCompleted,
  live,
  following,
  onToggle,
  onSpeed,
  onSeek,
  onFollowing,
}: Props) {
  return (
    <div className="controls">
      <button type="button" onClick={onToggle} data-testid="play-toggle" className="play">
        {playing ? "Pause" : "Play"}
      </button>

      <input
        type="range"
        className="scrub"
        data-testid="scrub"
        min={0}
        max={Math.max(extent, 1)}
        step={Math.max(extent, 1) / 2000}
        value={Math.min(time, extent)}
        onChange={(changeEvent) => onSeek(Number(changeEvent.target.value))}
        aria-label="Seek within the run"
      />

      <span className="readout" data-testid="clock">
        {formatSimTime(time)}
      </span>
      <span className="readout dim">/ {formatSimTime(extent)}</span>

      <span className="readout" data-testid="boards-completed">
        {boardsCompleted} boards
      </span>

      <span className="speeds">
        {SPEEDS.map((option) => (
          <button
            type="button"
            key={option}
            className={option === speed ? "speed is-current" : "speed"}
            onClick={() => onSpeed(option)}
          >
            {option}&times;
          </button>
        ))}
      </span>

      {live && (
        <label className="follow">
          <input
            type="checkbox"
            checked={following}
            onChange={(changeEvent) => onFollowing(changeEvent.target.checked)}
          />
          follow the tail
        </label>
      )}
    </div>
  );
}
