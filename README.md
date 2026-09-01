# smtsim — a discrete-event simulation of an SMT assembly line

A simulated surface-mount technology (SMT) line: bare printed circuit boards are
fed in at one end, pass through four machines in sequence, and come out the other
end populated and soldered. The simulation answers the questions a process
engineer actually asks — *how many boards per hour does this line produce, which
machine is the constraint, how long does a board spend waiting rather than being
worked on* — without needing the real line.

![smtsim demo](demo/demo.gif)

Stage 2 adds machine breakdowns and a paired what-if comparison, which is what
the line was built to answer: *is a second placement head worth buying?*

![smtsim compare](demo/compare.gif)

## The line

```
              ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  boards ───▶ │  solder  │──▶│  pick &  │──▶│   SPI    │──▶│  reflow  │──▶ done
              │  paste   │   │  place   │   │  paste   │   │   oven   │
              │ printer  │   │          │   │ inspect  │   │ (tunnel) │
              └──────────┘   └──────────┘   └──────────┘   └──────────┘
   capacity        1              1              1              6
   cycle time    ~25 s          ~52 s          ~19 s          240 s
   MTBF / MTTR  120 / 4 min    90 / 7 min       never        480 / 15 min
```

The first three stations hold one board at a time. The reflow oven is a
conveyorised tunnel: boards enter one after another and several are inside at
once, each taking a fixed time to traverse.

Pick-and-place is deliberately the slowest single-board step, and boards are fed
in slightly faster than it can clear them. Nothing in the code says "form a
queue" — the queue in front of the placer is simply what happens when work
arrives faster than a machine can absorb it.

The MTBF row is stage 2. `configs/baseline.toml` is the line above with
breakdowns; the built-in default line has none, so a config written for stage 1
still runs unchanged.

### The baseline is deliberately run close to its limit

Worth knowing before reading any number below. The placer takes 52 s a board and
is available about 93% of the time, so it can really clear a board every 56 s.
Boards arrive every 57 s. The line is therefore loaded to about **98% of the
placer's effective capacity** — stable, but only just.

That is not an accident and it is not a mistake. It is where interesting SMT
lines actually sit, and it is what makes the stage 2 question worth asking. It
does have two consequences that shape how the results must be read:

- **Single runs are nearly worthless.** Across 30 seeds the baseline's mean cycle
  time runs from 8.8 to 23.4 minutes. Any one shift tells you almost nothing,
  which is exactly why `smtsim compare` exists and why it runs 30 seeds a side.
- **Cycle time has not settled by the end of a shift.** At this loading the queue
  is still finding its level after eight hours, so "mean cycle time" means *over
  an eight-hour shift starting from an empty line*, not a steady-state figure. A
  warm-up exclusion trims the fill transient but cannot manufacture a steady
  state that the shift is too short to reach. Throughput, being bounded by the
  arrival rate, is far better behaved.

One shift on the baseline line (seed 42, first 30 minutes discarded) — a mild
seed, chosen for the demo because everything fits on one screen:

| station | cap | util | util (up) | max queue | mean wait |
|---|---|---|---|---|---|
| solder_paste_printer | 1 | 43.1 % | 43.9 % | 5 | 4.3 s |
| **pick_and_place** | 1 | **88.8 %** | **92.6 %** | **8** | **118.5 s** |
| spi | 1 | 33.5 % | 33.5 % | 1 | 0.0 s |
| reflow_oven | 6 | 69.8 % | 69.8 % | 1 | 0.0 s |

| station | availability | failures | downtime | MTBF observed | MTTR observed |
|---|---|---|---|---|---|
| solder_paste_printer | 98.1 % | 2 | 8.5 min | 96.9 min | 4.3 min |
| pick_and_place | 95.9 % | 4 | 18.4 min | 99.9 min | 6.1 min |

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
    yield from station.visit(self.env, board_id, self.sink)
```

and a station's turn at a machine reads as:

```python
sink.emit(Event(env.now, EventType.QUEUE_ENTERED, board_id, self.name))
with self.resource.request() as request:
    yield request                              # wait if the machine is busy
    yield from self._wait_for_repair()         # and if it is broken, for the fix
    remaining = self.config.service_time.sample(self.service_rng)
    sink.emit(Event(env.now, EventType.SERVICE_STARTED, board_id, self.name))
    ...
```

There is no queue data structure anywhere in this repository. `simpy.Resource`
holds the pending requests, and the queue statistics in the summary table are
*reconstructed after the fact* from the gap between `queue_entered` and
`service_started` in the log.

## Architecture

```
src/smtsim/
  rng.py        named random streams derived from one master seed
  config.py     frozen dataclasses for the line, its stations, their
                service-time distributions and their failure behaviour;
                loads TOML/JSON/YAML or uses defaults
  events.py     the Event record, the EventSink protocol, and the sinks
                (null, in-memory, JSONL) — the only module that knows about files
  stations.py   one station's process: queue, seize, serve, release, break down
  line.py       builds stations into a line, drives arrivals, runs the clock
  stats.py      pure reduction of an event stream to summary metrics, plus the
                paired-difference statistics
  compare.py    runs two configurations across shared seeds
  cli.py        argument parsing, progress display, tables — all of the I/O
```

The dependency arrows only ever point one way: `cli` → `compare` → `line`/`stats`
→ `stations` → `config`/`events`/`rng`. Nothing lower in that list imports
anything higher.

`compare.py` exists as a separate module rather than living in `stats.py`
because it has to *run simulations*, and `stats.py` must stay a pure reduction
over an event stream. If `stats.py` imported `line.py` the layering would
invert. So the paired-difference arithmetic lives in `stats.py`, where it can be
unit-tested against hand-computed examples with no simulation in sight, and the
orchestration that produces the samples lives one layer up.

### One random stream per station, not one per run

Stage 1 threaded a single `random.Random` through the whole model. That is fine
for a single run and wrong for comparing two.

The problem is subtle. Suppose you give the placer a second head and throughput
improves. Some of that improvement is the extra head; some of it is that every
random draw after the first placement shifted onto a different board, so the
printer and the oven saw a different sample too. The measurement mixes the change
under test with a reshuffled draw, and there is no way afterwards to separate
them.

So the master seed now derives a *named* stream per consumer:

```python
def derive_seed(master_seed: int, stream_name: str) -> int:
    payload = f"{master_seed}:{stream_name}".encode("utf-8")
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")
```

`arrivals`, `station:pick_and_place:service`, `station:pick_and_place:failures`,
and so on. A station is handed its streams at construction and owns them, so it
cannot draw from another station's numbers even by accident. Two runs from the
same master seed then see identical randomness on every stream they share —
**common random numbers**, the standard variance-reduction technique for exactly
this situation, and the thing that makes `smtsim compare` a paired experiment
rather than two unrelated samples.

BLAKE2b rather than the built-in `hash`, because `hash` of a string is salted per
process by `PYTHONHASHSEED`; a run would then be reproducible only within one
interpreter.

**Service and failure draws are on separate streams for the same station.** The
brief asked for one stream per station, which is not quite enough: if a station's
service times and its breakdowns shared a generator, the interleaving between
them would depend on the run's timing, so switching failures on for a station
would silently reshuffle that station's service times too. Splitting them
reintroduces nothing.

How strong is the guarantee? Stronger than it should be, for a reason worth
knowing. A test asserts that changing the placer leaves every one of the
printer's timestamps identical for the *whole run* — not just an opening prefix.
That holds because buffers between stations are unbounded, so a backed-up placer
never blocks the printer, and the printer's behaviour is a function of arrivals
alone. When stage 2b adds finite buffers, that assertion will have to weaken to a
prefix of boards. That will be a sign the model got more realistic, not that the
streams broke, and the test says so in place.

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

It holds because every generator in the run is derived from the master seed and
threaded explicitly into every `sample()` call — one per station, one for
arrivals, created in `Line.build` and never shared. No module-level RNG, no
`random.random()`, no implicit global state: a test seeds the global `random`
module, runs a full simulation, and asserts the global stream is undisturbed.
SimPy's event queue breaks timestamp ties by insertion order, so the event
sequence is fixed too.

Determinism is not a nicety here. It is the precondition for the whole of stage
2: if two runs differ, the difference has to come from the change being tested
and not from the sampler. See *One random stream per station* below for why one
generator per run was not enough.

## Breakdowns

A station gains an optional `failures` block:

```toml
[stations.failures]
mtbf = 5400.0     # mean OPERATING seconds between failures
mttr = 420.0      # mean seconds to repair
mttr_cv = 0.6     # spread of repair times
```

Time to failure is exponential — a machine is no likelier to break for having
run a while — and repair time is lognormal, because most repairs are quick and a
few drag on. Both reuse the same distribution classes as service times. A station
with no `failures` block never fails, starts no availability process and
schedules no events, so a stage 1 config behaves exactly as it did; a pinned hash
of the default line's board-level events guards that.

### Failures accrue on operating time, not calendar time

The countdown to the next failure only runs while the station has a board under
the head. An idle machine does not wear out. Feeders jam because feeders are
indexing; nozzles clog because nozzles are picking. This is why the lightly
loaded SPI station in the tests breaks down far less often than the placer given
the *same* MTBF, in rough proportion to how much of the shift each one spends
working — there is a test asserting exactly that ratio.

The consequence to keep in mind is that **observed calendar-time MTBF will look
longer than the configured MTBF**, by roughly a factor of one over utilisation.
The reliability table reports MTBF against operating time so it can be compared
directly with the configured value.

If you want calendar-time failures instead — appropriate for something like a
scheduled recalibration, or a chiller that fails whether or not the oven is
running — the change is confined to `Station._wear_and_repair`: delete the
`if self.working == 0: yield self._wait_until_working(env)` guard and the
`_end_work` interrupt that pauses the countdown, and the timeout consumes wall
time instead. Nothing else in the model would need to change.

### Interrupted work resumes; it does not restart

A failure interrupts every board currently under the head. The station tracks
remaining work, so a board 40 s into a 52 s placement resumes with 12 s left once
the machine is repaired. A tunnel oven failure stops the belt and interrupts
every board inside it at once.

While a station is down it starts no new boards, and the queue in front of it
grows on its own — the same way it does under ordinary congestion, for the same
reason.

Stage 1's extension point said this would replace the single
`yield env.timeout(service_time)` in `Station.visit` with a loop, and it does.
The invariant that survived is the one that mattered: everything about failures
is still confined to that one block and to the availability process beside it.
Boards, arrivals, the event schema's shape, the stats reduction and the CLI were
not restructured to accommodate it.

### What the log had to gain

Four event types: `station_failed`, `station_repaired`, `service_interrupted`
and `service_resumed`.

The last two are not decoration. Without them you cannot tell, from the log
alone, how much of a board's time at a station was work and how much was waiting
for a repair — and with a multi-slot station you cannot even tell which boards a
failure caught. Utilisation against uptime would stop being reconstructible, and
the rule that stats are a pure function of the log would break. So they are
recorded explicitly rather than inferred.

### What is reported

Per station: availability (uptime over the measured window), failure count, total
downtime, and MTBF and MTTR *as observed*, so a run can be checked against its
own configuration. Utilisation is reported twice, and the table says which is
which:

- **util** — busy ÷ (capacity × measured time). What fraction of the shift the
  machine was producing. This is the number that identifies the bottleneck.
- **util (up)** — busy ÷ (capacity × time not under repair). How hard the machine
  worked when it was available at all. The gap between the two is downtime.

### Limits

- **Repairs are instant to start and need no repairer.** There is no maintenance
  crew, so two machines can be under repair simultaneously with no queueing for
  a technician. On a real line at 2 a.m. that is not true.
- **Failures are independent.** No common cause, no cascading, no infant
  mortality after a repair.
- **A repair restores the machine exactly.** No degraded running, no
  repeat-offender machine.
- **No preventive maintenance**, and no scheduled breaks or changeovers.

## Warm-up

Both `run` and `stats` take `--warmup MINUTES`, which excludes an opening stretch
from every metric. Metrics integrate over the window `[warmup, horizon]`: events
before it are still processed, because they establish the state of the line when
measurement opens — a queue that built during warm-up is still there — but the
time they occupy contributes nothing.

`run` records the warm-up in `run_started`, so `smtsim stats log.jsonl`
reproduces a run's own table without being told how. Pass `--warmup 0` to
measure the whole log regardless.

Some guidance:

- A line starting empty produces its first boards with no queue in front of
  anything, so the opening minutes flatter every metric. **30 minutes on a
  480-minute shift** is a reasonable default here, and is what `configs/` and
  the demo use.
- Breakdowns make this worse, not better. A run begins with every machine
  healthy and no downtime yet, so a short run can miss the first failure
  entirely and report 100% availability.
- Warm-up trims a transient; it cannot conjure a steady state. At the baseline's
  98% loading the queue is still developing at the end of the shift, so no
  warm-up setting turns the cycle-time figure into a steady-state one. Lengthen
  the run if that is what you need.

## Comparing two configurations

```bash
smtsim compare configs/baseline.toml configs/two_placers.toml \
    --seeds 30 --minutes 480 --warmup 30 --out runs/comparison.json
```

The two configs differ in exactly one character: `pick_and_place` capacity 1
versus 2. Both are run under seeds 1…30, and because of the named streams, seed
*k* gives both arms identical arrivals, identical printer service times,
identical SPI and oven draws — everything the two configurations share. The pair
of results for a seed differ only by the change under test.

That is why the differences are analysed **pairwise**: subtract within a seed
first, then average, rather than comparing two independent means. The seed-to-
seed variation that both arms share cancels instead of drowning the signal. A
test compares a configuration against *itself* and asserts the mean difference is
exactly `0.0` — not approximately; the pairing is exact when nothing differs.

### Reading the output

```
metric                  baseline   variant    diff           95% CI
throughput (boards/h)      61.89     63.05   +1.16   [+0.72, +1.60]
mean cycle time (min)      14.74      8.12   -6.61   [-7.92, -5.31]
```

Both intervals are clear of zero, so both differences are unlikely to be
artefacts of which seeds happened to be drawn. **That is all an interval clear of
zero means.** It is not a claim that the difference is large, and it is certainly
not a claim that a second placement head pays for itself — that is a question
about the price of the machine and the value of the boards, which this program
knows nothing about. The output says so in as many words, every time.

An interval that *spans* zero is equally worth reading carefully: it means these
runs did not separate the two configurations, not that the configurations are the
same. More seeds narrow the interval.

The result above is a good illustration of why you want both metrics. Throughput
barely moves — the baseline manages 61.9 boards/hour against an arrival rate of
63.2, so the second head recovers about 1.3 boards/hour and then hits the ceiling
imposed by how fast boards are fed in. What it really buys is cycle time, nearly
halved, because the queue in front of the placer largely disappears. If the
question is "can we ship more boards", the answer is *not much, feed the line
faster first*. If it is "why is work-in-progress so high and delivery so
erratic", the answer is *this*.

`--verbose` prints the per-seed table so the pairing is visible; `--out` writes
the whole result, per-seed values included, as JSON.

### Why a paired t interval rather than a bootstrap

Either would have been defensible, and the interval is the same to two decimal
places at n = 30. The t interval wins here on two grounds: it is exactly
reproducible without a resampling RNG, and every number it produces can be
checked against a published t table — which is what the tests do, at df = 1, 2,
5, 10, 29 and 100.

It assumes the *differences* are roughly normal. For a mean over a 480-minute
shift that is mild, by the central limit theorem, and the pairing helps further
because differences are better behaved than the raw values. It would be the wrong
assumption for something like a p95, where a bootstrap would be the better tool.

There is no scipy, so the t quantile comes from the regularised incomplete beta
function evaluated by continued fraction, inverted by bisection — about fifty
lines in `stats.py`, tested against table values and against its own CDF.

### Other modelling decisions worth knowing about

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
  and stops working. This is the largest remaining gap, and it is stage 2b.
- **Utilisation counts a service still in progress at the horizon** as busy up to
  the horizon, and divides by `capacity × window` so multi-slot stations are
  measured on the same scale as single-slot ones.
- **Boards still on the line at the horizon are counted as in-system**, not as
  losses. With a warm-up the conservation identity `arrived = completed +
  in system` only holds for `--warmup 0`, since the counts are window-scoped
  while the census is taken at the horizon.

## Installing and running

Requires Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # or: uv sync --extra yaml, for YAML config support
```

```bash
# simulate an eight-hour shift on a line that breaks down
uv run smtsim run --minutes 480 --warmup 30 --config configs/baseline.toml \
    --out runs/run1.jsonl

# rebuild the same tables from the saved log, without simulating again
uv run smtsim stats runs/run1.jsonl

# is a second placement head worth buying? 30 shifts each, paired by seed
uv run smtsim compare configs/baseline.toml configs/two_placers.toml \
    --seeds 30 --minutes 480 --warmup 30 --out runs/comparison.json
```

`run` options:

| flag | meaning |
|---|---|
| `--minutes` | length of the simulated shift (default 480) |
| `--seed` | master seed; same seed, same log (default 42) |
| `--warmup` | opening minutes to exclude from the metrics (default 0) |
| `--out` | where to write the JSONL log (default `runs/run1.jsonl`) |
| `--config` | a line configuration file; omit for the built-in defaults |
| `--quiet` | skip the progress display |

`stats` takes a log path and an optional `--warmup`, which defaults to whatever
the run was recorded with. `compare` takes two config paths plus `--seeds`,
`--minutes`, `--warmup`, `--out` and `--verbose`.

`make install`, `make test`, `make run`, `make stats`, `make compare` and
`make demo` wrap the common commands.

A note on the progress bars, since honesty is cheaper than a nice demo: a
480-minute shift simulates in about a tenth of a second, and a full 60-run
comparison takes about a second. Neither bar lingers. They are there because
sweeps get much bigger than this — and because a bar that only appears when a
run is slow is a bar nobody has tested.

## Configuring a line

Two kinds of config file live in this repository, and the distinction is worth
keeping:

- **`line.example.toml`** is the annotated syntax reference. Every option, with
  a comment saying what it does. Start here when writing your own.
- **`configs/*.toml`** are scenarios — concrete lines meant to be run and
  compared. `baseline.toml` and `two_placers.toml` are the two sides of the
  canonical what-if.

Every station takes a `capacity`, a `service_time` drawn from one of four
distributions, and an optional `failures` block:

| kind | parameters | use it for |
|---|---|---|
| `lognormal` | `mean`, `cv` | machine cycles — right-skewed, occasionally slow, never negative |
| `triangular` | `low`, `mode`, `high` | a bounded estimate from an operator: best, typical, worst |
| `exponential` | `mean` | memoryless waits; time to failure |
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
| `event` | one of the event types below |
| `board` | board id, counting from 1, or `null` for whole-run events |
| `station` | station name, or `null` where it does not apply |

Event types, in the order a board meets them:

| event | meaning |
|---|---|
| `board_arrived` | a bare board entered the head of the line |
| `queue_entered` | the board is waiting for a station |
| `service_started` | the station began working on it |
| `service_interrupted` | the station broke down mid-job; work so far is kept |
| `service_resumed` | the station was repaired and picked up where it left off |
| `service_finished` | the station finished the board |
| `board_completed` | the board left the line |
| `station_failed` / `station_repaired` | a station's downtime window |
| `run_started` / `run_finished` | bracket the run |

`run_started` carries a `detail` object with the seed, the horizon, the warm-up
and the full line configuration. That is what makes a log self-describing: it is
why `smtsim stats` can compute utilisation — which needs each station's capacity
and the length of the run — from the file alone, and why it can reproduce a run's
own warm-up without being told. A log with the header stripped still summarises;
it just falls back to assuming single-slot stations and infers the horizon from
the last timestamp.

Because the format is line-oriented JSON, `jq` works on it directly:

```bash
jq -r 'select(.event=="board_completed") | .t' runs/run1.jsonl | tail -1
```

## Tests

```bash
uv run pytest
```

109 tests, in about three seconds. The ones that matter are properties rather
than golden values:

- **Determinism** — the same seed writes byte-identical files; different seeds do
  not; the progress hook and the compare run-hook do not perturb results; the
  global `random` module is never touched; and a pinned hash of the default
  line's board-level events fails loudly if the model ever drifts.
- **Common random numbers** — changing the placer's service time, or its
  capacity, leaves every printer and arrival timestamp identical, while the
  downstream stations demonstrably do change. A test that could not fail would
  prove nothing, so the converse is asserted too.
- **Conservation** — boards arrived equals boards completed plus boards still on
  the line, with and without breakdowns.
- **Monotonicity** — timestamps never decrease.
- **Bottleneck** — pick-and-place has strictly the highest utilisation of the
  four, across several seeds.
- **Causality** — no board's `service_started` precedes its `queue_entered`, no
  board is served without having queued, no service starts or finishes inside a
  station's downtime window, and every completed board visits all four stations
  in line order.
- **Capacity** — no station is ever working on more boards than its capacity,
  which tests that the resource is doing the work rather than the station code.
- **Breakdowns** — pooled over 40 seeds, observed MTBF and MTTR land within 10%
  of the configured values; interrupted work resumes rather than restarting;
  failures track operating time rather than the clock; an oven failure interrupts
  every board in the tunnel at once.
- **Stats arithmetic** — utilisation, availability, waits, percentiles, queue
  depths and the warm-up window are checked against short logs worked out by
  hand, so the metric definitions are pinned independently of the simulation.
- **Statistics** — t quantiles against a published table, the incomplete beta
  against known values, and a paired interval against an example computed by
  hand. A configuration compared against itself must give a mean difference of
  exactly zero.

## Extension points

Stage 1 marked one, for breakdowns; stage 2 filled it in, and the comment in
`stations.py` now records what it became rather than what it would be. The
design bet it was making — that the whole feature could be confined to the
service block and one process beside it — held. Boards, arrivals, the stats
reduction and the CLI were extended, not restructured.

The next one is finite buffers, and it is not marked with a comment because it
is not local: it changes when a station releases its resource, which is a change
to the shape of `Station.visit` rather than a change inside it. See stage 2b.

## Roadmap

**Stage 1 — the line.** ✅ Four stations, variable service times, queues that
emerge from timing, a deterministic event log, a CLI.

**Stage 2 — breakdowns and what-if comparison.** ✅ Per-station random streams,
MTBF/MTTR failures on operating time with work resumed rather than restarted, a
warm-up window, availability and observed-reliability metrics, and
`smtsim compare` with paired confidence intervals.

**Stage 2b — finite buffers.** The conveyor between two stations holds a handful
of boards, not infinitely many. When it fills, the upstream machine finishes its
board and then cannot release it — it *blocks* — and when it empties, the
downstream machine *starves*. This is the largest remaining gap between the model
and a real line, and it is what makes a breakdown on the placer stop the printer
too, which today it does not. It also weakens the common-random-numbers property
in an interesting way, since upstream stations stop being independent of
downstream changes.

**Stage 3 — FastAPI and Postgres.** A service that accepts a line configuration,
runs the simulation, and streams events. The event sink becomes a database writer
and a WebSocket publisher; the simulation core is untouched, which is the bet the
I/O-free design has been making since stage 1. `compare` becomes a job that
returns a comparison id.

**Stage 4 — web replay UI.** Scrub through a stored run and watch boards move
along the line, with queue depths building and draining and stations going red
when they fail. The event log already contains everything this needs; it is a
rendering problem, not a simulation one.

## Regenerating the demo

The GIFs are recorded with [VHS](https://github.com/charmbracelet/vhs) from
`demo/demo.tape` and `demo/compare.tape`:

```bash
brew install vhs
make demo
```

## Licence

MIT.
