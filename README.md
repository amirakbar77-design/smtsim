# smtsim — a discrete-event simulation of an SMT assembly line

A simulated surface-mount technology (SMT) line: bare printed circuit boards are
fed in at one end, pass through four machines in sequence, and come out the other
end populated and soldered. The simulation answers the questions a process
engineer actually asks — *how many boards per hour does this line produce, which
machine is the constraint, how long does a board spend waiting rather than being
worked on* — without needing the real line.

![smtsim demo](demo/demo.gif)

## The line

```
              ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  boards ───▶ │  solder  │──▶│  pick &  │──▶│   SPI    │──▶│  reflow  │──▶ done
              │  paste   │   │  place   │   │  paste   │   │   oven   │
              │ printer  │   │          │   │ inspect  │   │ (tunnel) │
              └──────────┘   └──────────┘   └──────────┘   └──────────┘
   capacity        1              1              1              6
   cycle time    ~25 s          ~52 s          ~19 s          240 s
```

The first three stations hold one board at a time. The reflow oven is a
conveyorised tunnel: boards enter one after another and several are inside at
once, each taking a fixed time to traverse.

Pick-and-place is deliberately the slowest single-board step, and boards are fed
in slightly faster than it can clear them. Nothing in the code says "form a
queue" — the queue in front of the placer is simply what happens when work
arrives faster than a machine can absorb it. A typical eight-hour shift:

| station | capacity | utilisation | max queue | mean wait |
|---|---|---|---|---|
| solder_paste_printer | 1 | 46.0 % | 1 | 0.2 s |
| **pick_and_place** | 1 | **94.0 %** | **7** | **83.3 s** |
| spi | 1 | 34.0 % | 1 | 0.0 s |
| reflow_oven | 6 | 71.5 % | 1 | 0.0 s |

513 boards completed, 64.1 boards/hour, mean cycle time 6.96 min, p95 9.45 min.
The gap between the mean and the p95 is the queue in front of the placer showing
up in the numbers.

## Why discrete-event simulation rather than a tick loop

The obvious way to write this is a loop that advances a clock by a fixed step —
one second, say — and asks every machine what it is doing. That approach has
three problems this one avoids.

**It spends nearly all of its time doing nothing.** An eight-hour shift at
one-second ticks is 28,800 iterations, and in the overwhelming majority of them
no machine changes state. A discrete-event simulation jumps the clock straight to
the time of the next thing that actually happens. This model executes roughly
6,700 events for the same shift and finishes in about 0.1 s. That difference is
what makes it practical to sweep hundreds of configurations later.

**The tick size silently becomes part of the model.** With a one-second tick, a
placement taking 52.4 s and one taking 52.6 s are the same event. Every duration
gets quantised, and the quantisation error accumulates into the throughput
figure. Here durations are real numbers and the clock lands exactly on them, so
the answer does not depend on a resolution someone picked arbitrarily.

**Concurrency has to be hand-rolled.** In a tick loop, "this board is waiting for
the placer, which is busy until t=340" is state you maintain yourself, in
parallel, for every board and every machine — and that bookkeeping is where the
bugs live. SimPy expresses the same thing as a coroutine per board that simply
*yields* until the resource it asked for is free. Each board's journey reads as
straight-line code:

```python
for station in self.stations:
    yield from station.visit(self.env, board_id, self.rng, self.sink)
```

and a station's turn at a machine reads as:

```python
sink.emit(Event(env.now, EventType.QUEUE_ENTERED, board_id, self.name))
with self.resource.request() as request:
    yield request                                     # wait if the machine is busy
    sink.emit(Event(env.now, EventType.SERVICE_STARTED, board_id, self.name))
    yield env.timeout(self.config.service_time.sample(rng))
    sink.emit(Event(env.now, EventType.SERVICE_FINISHED, board_id, self.name))
```

There is no queue data structure anywhere in this repository. `simpy.Resource`
holds the pending requests, and the queue statistics in the summary table are
*reconstructed after the fact* from the gap between `queue_entered` and
`service_started` in the log.

## Architecture

```
src/smtsim/
  config.py     frozen dataclasses for the line, its stations and their
                service-time distributions; loads TOML/JSON/YAML or uses defaults
  events.py     the Event record, the EventSink protocol, and the sinks
                (null, in-memory, JSONL) — the only module that knows about files
  stations.py   one station's process: queue, seize, serve, release
  line.py       builds stations into a line, drives arrivals, runs the clock
  stats.py      pure reduction of an event stream to summary metrics
  cli.py        argument parsing, progress display, tables — all of the I/O
```

The dependency arrows only ever point one way: `cli` → `line`/`stats` →
`stations` → `config`/`events`. Nothing lower in that list imports anything
higher.

### The simulation core does no I/O, and that is the point

`line.py` and `stations.py` never open a file, never print, never read the wall
clock. They are handed an object with an `emit(event)` method and call it. Three
things follow from that.

*The core is testable without a filesystem.* Every test in `tests/` runs the real
model against an in-memory `ListSink`; only the determinism tests touch disk, and
only because the property under test is about bytes on disk.

*A different consumer can be dropped in without changing the model.* Today the
CLI injects a `JsonlSink` that writes lines to a file. In stage 3 a FastAPI
service will inject a sink that pushes each event onto a WebSocket and batches
inserts into Postgres. `line.py` will not change — it does not know the
difference, because `EventSink` is a one-method protocol:

```python
class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...
```

*Progress reporting stays outside too.* The rich progress bar is not printed by
the simulation; the CLI passes an `on_progress` callback that the model calls
with the current simulated time. The callback draws no random numbers and mutates
no state, so a run with the progress display produces exactly the same log as one
without — there is a test that asserts precisely this
(`test_progress_hook_does_not_perturb_the_log`).

### The event log is the interface, not a debug artefact

`run` writes the log and then **reads it back** to build its summary table. It
does not keep a parallel set of counters. The consequence is that
`smtsim stats run.jsonl` and the table printed at the end of `smtsim run` are
produced by the same function over the same bytes, and cannot drift apart.

This is also why timestamps are written at full float precision rather than
rounded to something prettier. Python's float repr round-trips exactly, so the
log is a lossless record of the run; rounding to microseconds made
`stats`-from-file disagree with `stats`-in-memory in the ninth decimal place, and
a test caught it.

### Determinism

The requirement is that a seed reproduces a run byte for byte, and it holds:

```
$ smtsim run --seed 42 --out a.jsonl && smtsim run --seed 42 --out b.jsonl
$ cmp a.jsonl b.jsonl && echo identical
identical
```

It holds because exactly one `random.Random(seed)` instance exists per run,
created in `Line.build`, and it is threaded explicitly into every `sample()`
call. No module-level RNG, no `random.random()`, no implicit global state — a
test seeds the global `random` module, runs a full simulation, and asserts the
global stream is undisturbed. SimPy's event queue breaks timestamp ties by
insertion order, so the event sequence is fixed too.

Determinism is not a nicety here. It is what makes a what-if comparison in stage
2 meaningful: if two runs differ, the difference has to come from the change
being tested and not from the sampler.

### Modelling decisions worth knowing about

These are simplifications, and you should know they are there before trusting a
number:

- **The reflow oven is modelled as six parallel slots with a fixed 240 s dwell,
  not as a physical conveyor.** A real tunnel constrains the *spacing* between
  boards as well as the count inside it. With capacity six and a four-minute
  dwell, the model permits one board every 40 s, which is well clear of the
  placer's 52 s and so does not distort the bottleneck. Push arrivals hard enough
  and this approximation would start to matter.
- **Buffers between stations are unbounded.** A board that has finished printing
  releases the printer immediately, even if the placer is backed up. Real lines
  have short conveyor buffers, and when they fill, the upstream machine *blocks*
  and stops working. Adding finite buffers is on the roadmap; until then the
  model slightly overstates upstream availability.
- **There is no warm-up exclusion.** Metrics cover the whole run from an empty
  line, so short runs read a little optimistically. At 480 minutes the fill
  transient is a rounding error; at 30 minutes it is not.
- **Utilisation counts a service still in progress at the horizon** as busy up to
  the horizon, and divides by `capacity × horizon` so multi-slot stations are
  measured on the same scale as single-slot ones.
- **There are no machine breakdowns yet.** See below.

## Installing and running

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # or: uv sync --extra yaml, for YAML config support
```

```bash
# simulate an eight-hour shift and write the event log
uv run smtsim run --minutes 480 --seed 42 --out runs/run1.jsonl

# rebuild the same summary from the saved log, without simulating again
uv run smtsim stats runs/run1.jsonl
```

`run` shows a live progress bar while simulating and then prints the summary.
Be aware that a 480-minute shift simulates in about a tenth of a second, so on
a default run the bar is on screen only briefly; it earns its keep on the long
multi-day runs that stage 2's what-if sweeps will need.

Other options:

| flag | meaning |
|---|---|
| `--minutes` | length of the simulated shift (default 480) |
| `--seed` | RNG seed; same seed, same log (default 42) |
| `--out` | where to write the JSONL log (default `runs/run1.jsonl`) |
| `--config` | a line configuration file; omit for the built-in defaults |
| `--quiet` | skip the progress display |

`make install`, `make test`, `make run`, `make stats` and `make demo` wrap the
common commands.

## Configuring a line

`line.example.toml` reproduces the built-in defaults and is the place to start.
Every station takes a `capacity` and a `service_time` drawn from one of three
distributions:

| kind | parameters | use it for |
|---|---|---|
| `lognormal` | `mean`, `cv` | machine cycles — right-skewed, occasionally slow, never negative |
| `triangular` | `low`, `mode`, `high` | a bounded estimate from an operator: best, typical, worst |
| `constant` | `seconds` | conveyor transit, where the belt speed fixes the time |

Lognormal is the default for machine work because real cycle times have a hard
floor, no hard ceiling, and a tail of slow cycles — feeder jams, a vision retry.
Parameterising it by mean and coefficient of variation rather than by the
underlying μ and σ means you can set it from numbers a line engineer can read off
a machine log; the conversion happens in `LogNormal.sample`.

**A note on YAML.** The brief asked for YAML config and for no runtime
dependencies beyond SimPy and rich, and those two requirements are in tension:
YAML needs PyYAML, whereas TOML and JSON parse with the standard library on
Python 3.12. Rather than quietly add the dependency, the loader dispatches on
file extension and supports all three, with PyYAML as an optional extra
(`uv sync --extra yaml`). If a `.yaml` file is passed without the extra
installed, the error message says exactly that. TOML is the recommended default;
nothing is lost, since the two formats express the same document.

## The event log

One JSON object per line, in simulated-time order:

```json
{"t":57.75432757403026,"event":"board_arrived","board":1,"station":null}
{"t":57.75432757403026,"event":"queue_entered","board":1,"station":"solder_paste_printer"}
{"t":57.75432757403026,"event":"service_started","board":1,"station":"solder_paste_printer"}
{"t":87.56666738640195,"event":"service_finished","board":1,"station":"solder_paste_printer"}
```

| field | meaning |
|---|---|
| `t` | simulated time in seconds since the start of the run |
| `event` | one of the seven event types below |
| `board` | board id, counting from 1, or `null` for whole-run events |
| `station` | station name, or `null` where it does not apply |

Event types: `board_arrived`, `queue_entered`, `service_started`,
`service_finished`, `board_completed`, plus `run_started` and `run_finished`,
which bracket the run and carry a `detail` object with the seed, the horizon and
the full line configuration. Those two are what make a log self-describing: they
are why `smtsim stats` can compute utilisation — which needs each station's
capacity and the length of the run — from the file alone. A log with the header
stripped still summarises; it just falls back to assuming single-slot stations
and infers the horizon from the last timestamp.

Because the format is line-oriented JSON, `jq` works on it directly:

```bash
jq -r 'select(.event=="board_completed") | .t' runs/run1.jsonl | tail -1
```

## Tests

```bash
uv run pytest
```

41 tests. The ones that matter are properties rather than golden values:

- **Determinism** — the same seed writes byte-identical files; different seeds do
  not; the progress hook does not perturb the log; the global `random` module is
  never touched.
- **Conservation** — boards arrived equals boards completed plus boards still on
  the line, checked both from the raw event set and from the computed stats.
- **Monotonicity** — timestamps never decrease.
- **Bottleneck** — pick-and-place has strictly the highest utilisation of the
  four, across several seeds.
- **Causality** — no board's `service_started` precedes its `queue_entered`, no
  board is served without having queued, and every completed board visits all
  four stations in line order.
- **Capacity** — no station is ever serving more boards than its capacity, which
  tests that the resource is doing the work rather than the station code.
- **Stats arithmetic** — utilisation, waits, percentiles and queue depths are
  checked against a twelve-line log worked out by hand, so the metric definitions
  are pinned independently of the simulation.

## Extension point: machine breakdowns

Deliberately not implemented yet. Two comment blocks mark where it goes:

- `config.py` — where a `failures: FailureConfig | None` field joins `LineConfig`,
  giving each station an MTBF/MTTR pair.
- `stations.py` — where the single `yield env.timeout(service_time)` becomes a
  loop that consumes remaining work while a separate availability process
  interrupts it, then waits for repair before resuming.

The reason it is one `yield` in one method today is so that stage 2 is a change
to that block and nothing else. Boards, arrivals, the event schema, the stats
reduction and the CLI all stay as they are; the log gains two event types
(`station_failed`, `station_repaired`) and the stats gain availability alongside
utilisation.

## Roadmap

**Stage 1 — the line (this repo).** Four stations, variable service times, queues
that emerge from timing, a deterministic event log, a CLI.

**Stage 2 — breakdowns and what-if comparison.** MTBF/MTTR per station via the
extension point above, then a `smtsim compare` that runs two configurations
across a set of seeds and reports the difference in throughput and cycle time
with a confidence interval — the point being to distinguish a real improvement
from sampler noise, which is only possible because runs are reproducible. Finite
inter-station buffers, so that a blocked placer starves the printer, belong here
too.

**Stage 3 — FastAPI and Postgres.** A service that accepts a line configuration,
runs the simulation, and streams events. The event sink becomes a database
writer and a WebSocket publisher; the simulation core is untouched, which is the
bet the I/O-free design is making.

**Stage 4 — web replay UI.** Scrub through a stored run and watch boards move
along the line, with queue depths building and draining. The event log already
contains everything this needs; it is a rendering problem, not a simulation one.

## Regenerating the demo

The GIF is recorded with [VHS](https://github.com/charmbracelet/vhs) from
`demo/demo.tape`:

```bash
brew install vhs
make demo
```

## Licence

MIT.
