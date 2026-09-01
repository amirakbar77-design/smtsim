#!/usr/bin/env python
"""Submit a run and watch its events arrive over the WebSocket.

A small reference client for the streaming endpoint -- the shape stage 4's
replay UI will take, minus the drawing. It deliberately does the naive thing a
browser would: connect, read frames, keep a running tally of what the line is
doing.

    python scripts/stream_run.py --config configs/baseline.toml --minutes 5000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smtsim.config import load_line_config  # noqa: E402


def post_run(base_url: str, config: dict, minutes: float, seed: int, warmup: float) -> str:
    body = json.dumps(
        {
            "config": config,
            "minutes": minutes,
            "seed": seed,
            "warmup_minutes": warmup,
        }
    ).encode()
    request = urllib.request.Request(
        f"{base_url}/runs", data=body, headers={"content-type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)["id"]


async def stream(ws_url: str, run_id: str) -> None:
    import websockets

    counts: Counter[str] = Counter()
    frames = 0
    started = time.monotonic()

    async with websockets.connect(f"{ws_url}/runs/{run_id}/stream", max_size=None) as socket:
        opening = json.loads(await socket.recv())
        print(f"  connected: {opening['mode']} stream for {run_id}")

        async for message in socket:
            frame = json.loads(message)
            if frame["type"] == "events":
                frames += 1
                counts.update(event["event"] for event in frame["events"])
                total = sum(counts.values())
                print(
                    f"\r  {total:>7,} events in {frames:>4} frames"
                    f"   boards done: {counts['board_completed']:>5,}"
                    f"   failures: {counts['station_failed']:>3}"
                    f"   blocks: {counts['transfer_blocked']:>5,}",
                    end="",
                    flush=True,
                )
            elif frame["type"] == "end":
                elapsed = time.monotonic() - started
                print(f"\n  {frame['status']} in {elapsed:.1f}s")
                return


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--config", type=Path, default=Path("configs/baseline.toml"))
    parser.add_argument("--minutes", type=float, default=5000.0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--warmup-minutes", type=float, default=30.0)
    args = parser.parse_args()

    config = load_line_config(args.config).to_dict()
    run_id = post_run(args.base_url, config, args.minutes, args.seed, args.warmup_minutes)
    print(f"  POST /runs -> 202 {run_id}")

    ws_url = args.base_url.replace("http://", "ws://").replace("https://", "wss://")
    asyncio.run(stream(ws_url, run_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
