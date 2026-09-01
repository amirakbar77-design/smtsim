# smtsim — a discrete-event simulation of an SMT assembly line

A simulated surface-mount technology (SMT) line: bare printed circuit boards are
fed in at one end, pass through four machines in sequence, and come out the other
end populated and soldered. The simulation answers the questions a process
engineer actually asks — *how many boards per hour does this line produce, which
machine is the constraint, how long does a board spend waiting rather than being
worked on* — without needing the real line.

![The replay UI: boards moving through the line, then a placer breakdown backing up the conveyor behind it](demo/replay.gif)

Watch the second half of that. The placer breaks down, the three-board conveyor
in front of it fills, and the printer behind it turns amber — it has finished a
board and has nowhere to put it. That is *blocking*, and it is the reason a line
of four machines never runs at the speed of its slowest machine. Nothing in the
code says "form a queue" or "propagate a stall"; it falls out of capacities and
timing.

The project is built in stages, and each one is a decision about architecture as
much as about features:

| stage | what it added |
|---|---|
| 1 | the line, variable service times, a deterministic event log, a CLI |
| 2 | machine breakdowns, per-station random streams, paired what-if comparison |
| 2b | finite conveyors, so a stall in one machine reaches its neighbours |
| 3 | an HTTP API, Postgres, Docker — a second consumer of the same core |
| 4 | this replay UI |

The claim the whole thing rests on is that the simulation core knows nothing
about its consumers. By stage 4 there are three — a CLI, an HTTP service and a
browser — and `line.py` and `stations.py` have not changed since stage 2b. There
are tests that say so rather than a claim that hopes so.

<details>
<summary>The command line and the API, for the same run</summary>

![smtsim demo](demo/demo.gif)

![smtsim API](demo/api.gif)

![smtsim compare](demo/compare.gif)

</details>

## The line

```
              ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
  boards ───▶ │  solder  │─▷─│  pick &  │─▷─│   SPI    │─▷─│  reflow  │──▶ done
              │  paste   │   │  place   │   │  paste   │   │   oven   │
              │ printer  │   │          │   │ inspect  │   │ (tunnel) │
              └──────────┘   └──────────┘   └──────────┘   └──────────┘
   capacity        1              1              1              6
   cycle time    ~25 s          ~52 s          ~19 s          240 s
   MTBF / MTTR  120 / 4 min    90 / 7 min       never        480 / 15 min
                          ▷ = a 3-board conveyor
```

The first three stations hold one board at a time. The reflow oven is a
conveyorised tunnel: boards enter one after another and several are inside at
once, each taking a fixed time to traverse.

Pick-and-place is deliberately the slowest single-board step, and boards are fed
in slightly faster than it can clear them. Nothing in the code says "form a
queue" — the queue in front of the placer is simply what happens when work
arrives faster than a machine can absorb it.

Between the machines are short conveyors, three boards long. They are the reason
the machines are not independent, and stage 2b is about what that costs.

### What blocking and starving mean

Put a machine between two short conveyors and it can fail to produce for two
quite different reasons that have nothing to do with the machine itself.

**Starving** is having nothing to work on. The conveyor feeding the machine is
empty, so it stands idle waiting for the machine before it. Boards are late
arriving.

**Blocking** is having nowhere to put what you have finished. The machine has
completed a board, but the conveyor in front of the *next* machine is full, so
it cannot let go. It sits holding the finished board, doing nothing, until a
space opens up. Boards are late leaving.

Neither is a fault. Both are what a coupled line does, and together with
breakdowns they are why a line of four machines never runs at the speed of its
slowest machine. On the baseline line the printer spends 43% of a shift
producing, 37% starved waiting for boards, 18% blocked behind a busy placer, and
2% under repair. Only the first of those is work.

A machine that breaks down now takes its neighbours with it: the station behind
it blocks within a couple of minutes, and the station in front of it runs dry.
That is the whole point of modelling buffers, and there is a test that asserts
exactly that behaviour, because it is the clearest possible demonstration that
the line is coupled at all.

### The baseline is deliberately run close to its limit

Worth knowing before reading any number below. The placer takes 52 s a board and
is available about 93% of the time, so it can really clear a board every 56 s.
Boards arrive every 57 s. The line is therefore loaded to about **98% of the
placer's effective capacity** — stable, but only just.

That is not an accident and it is not a mistake. It is where interesting SMT
lines actually sit, and it is what makes the what-if questions worth asking. It
does have two consequences that shape how the results must be read:

- **Single runs are nearly worthless.** Across 30 seeds the baseline's mean cycle
  time runs from about 9 to about 26 minutes. Any one shift tells you almost
  nothing, which is exactly why `smtsim compare` exists and why it runs 30 seeds
  a side.
- **Cycle time has not settled by the end of a shift.** At this loading the queue
  is still finding its level after eight hours, so "mean cycle time" means *over
  an eight-hour shift starting from an empty line*, not a steady-state figure. A
  warm-up exclusion trims the fill transient but cannot manufacture a steady
  state that the shift is too short to reach. Throughput, being bounded by the
  arrival rate, is far better behaved.

One shift on the baseline line (seed 42, first 30 minutes discarded) — a mild
seed, chosen for the demo because everything fits on one screen. 472 boards,
62.9 boards/hour, mean cycle time 7.88 min, p95 11.37 min:

| station | cap | work | block | starve | down | max q | mean wait |
|---|---|---|---|---|---|---|---|
| solder_paste_printer | 1 | 43.1 % | 17.6 % | 37.4 % | 1.9 % | 5 | 37.2 s |
| **pick_and_place** | 1 | **88.8 %** | 0.0 % | 7.1 % | 4.1 % | **3** | **87.7 s** |
| spi | 1 | 33.5 % | 0.0 % | 66.5 % | 0.0 % | 1 | 0.0 s |
| reflow_oven | 6 | 69.8 % | 0.0 % | 30.2 % | 0.0 % | 1 | 0.0 s |

| station | availability | util (uptime) | failures | MTBF observed | MTTR observed |
|---|---|---|---|---|---|
| solder_paste_printer | 98.1 % | 43.9 % | 2 | 96.9 min | 4.3 min |
| pick_and_place | 95.9 % | 92.6 % | 4 | 99.9 min | 6.1 min |

Read the first table across a row and it says what a machine did with its shift.
The placer is the only one that spends most of its time producing; the printer
loses more time to blocking than the placer loses to breakdowns. Read it down the
`block` column and the coupling is visible at a glance: only stations upstream of
the bottleneck ever block, because only they can run into a full conveyor.

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

src/smtsim_service/   the HTTP service (stage 3). Depends on the simulation;
                      the simulation does not know it exists, and a test
                      enforces that.
  settings.py     configuration from the environment
  db.py           the psycopg connection pool
  repository.py   all the SQL, including the COPY path for events
  sinks.py        DatabaseSink and LoopBridgeSink — two more EventSinks
  streaming.py    the WebSocket broker, batching and backpressure
  jobs.py         the thread pool that keeps simulations off the event loop
  schemas.py      request and response models
  app.py          the routes

web/                  the replay UI (stage 4). Depends on the API's HTTP
                      contract and on nothing Python at all.
  src/replay/     the reducer, the keyframe timeline, the playback clock —
                  pure TypeScript over plain data, no React in any of it
  src/api/        the REST client and the WebSocket loader
  src/components/ the SVG line, the timeline strip, the controls, the panels
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

#### How strong the guarantee is, and why it got weaker

Through stage 2 this held absolutely: changing the placer left every one of the
printer's timestamps identical for the *whole run*. That was never a sign of
unusually good isolation. It was a symptom of the model being uncoupled —
buffers were unbounded, so a backed-up placer could never block the printer, and
the printer's behaviour was a function of arrivals alone.

Stage 2b takes that away, on purpose. With a three-board conveyor in front of the
placer, a slower placer eventually fills it, and from that moment the printer
blocks and its timeline legitimately diverges. Measured across seeds, the
earliest the printer ever notices is board 11.

So the test now asserts two things instead of one:

- for the first ten boards — before backpressure could possibly have propagated —
  the printer's timestamps must be **identical**, or the streams are leaking;
- somewhere after that they must **diverge**, or the buffers are not actually
  coupling the line and the first assertion is proving nothing.

The second half is the important one. A test that had merely been loosened until
it passed would still pass with the streams thoroughly broken.

The practical consequence for `smtsim compare` is that common random numbers now
cancel *less* of the shared variation than they did in stage 2, because upstream
stations are no longer bitwise identical between the two arms. The pairing itself
is unaffected — it only requires that both arms use the same seed, which they
still do — so the intervals remain valid. They are just a little wider than they
would be on an uncoupled line. That is the price of a more honest model, and it
is worth paying.

### The simulation core does no I/O, and that is the point

`line.py` and `stations.py` never open a file, never print, never read the wall
clock. They are handed an object with an `emit(event)` method and call it. Three
things follow from that.

*The core is testable without a filesystem.* Every test in `tests/` runs the real
model against an in-memory `ListSink`; only the determinism tests touch disk, and
only because the property under test is about bytes on disk.

*A different consumer can be dropped in without changing the model.* The CLI
injects a `JsonlSink` that writes lines to a file. Stage 3's service injects a
`DatabaseSink` that batches COPY inserts into Postgres and a `LoopBridgeSink`
that hands events to an asyncio loop, both wrapped in a `FanOutSink`. Stage 4
adds a third consumer that never touches Python at all — a browser reducing the
same event stream in TypeScript. `line.py` did not change for any of them — it
cannot tell the difference, because `EventSink` is a one-method protocol:

```python
class EventSink(Protocol):
    def emit(self, event: Event) -> None: ...
```

That was the bet stages 1 and 2 were making, and stage 3 is where it was
settled. See *The service* below for what it actually cost.

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

## Buffers, blocking and starving

Each station takes an optional conveyor length:

```toml
[[stations]]
name = "pick_and_place"
input_buffer = 3      # the conveyor feeding this machine holds three boards
capacity = 1
```

Omit it and the buffer is unbounded, which reproduces a line with no buffer
modelling at all. That is not merely similar: a station with no `input_buffer`
builds no buffer resource, makes no request and yields nowhere, so the board
follows exactly the code path it followed before buffers existed. The pinned
hash of the default line's board-level events does not move by a single byte,
and there is a test asserting it from both sides.

### Where a board sits, and when

**A board occupies a buffer slot from the moment it joins the queue until the
moment the machine starts work on it.** The machine lifts it off the conveyor
and into its own nest, freeing the slot behind it.

Both conventions exist in the literature; this one matches a physical conveyor
feeding a machine, where the machine's board clamp is not part of the conveyor.
It has two consequences worth stating plainly:

- A station's total work-in-progress is `input_buffer + capacity`, not
  `input_buffer`. A three-board conveyor in front of a single-head placer means
  four boards at that station at most.
- **Queue length still means what the table says it means.** The queue is the
  conveyor, so `max q` is peak conveyor occupancy and can never exceed the
  buffer size — there is a test asserting exactly that, for buffers of 1, 2
  and 3. Under the other convention the queue would be capped at
  `input_buffer - capacity`, which reads oddly and makes a buffer of 1 on a
  single-head machine mean "no waiting room at all".

The printer's input is left unbounded in `configs/`, because it is fed from a
magazine of bare boards rather than from a conveyor. Raw material waiting to be
loaded is not the constraint this model is about.

### Blocking after service

A machine finishes its board and *then* discovers whether it has anywhere to put
it. It does not check first and decline to start.

```python
sink.emit(Event(env.now, EventType.SERVICE_FINISHED, board_id, self.name))

# The machine is not free until the board it just finished has somewhere to go.
next_slot = None
if downstream is not None:
    next_slot = yield from downstream.enter_queue(env, board_id, sink, self)
```

That `yield` sits *inside* the `with self.resource.request()` block, so the
machine is still held while it waits. That is the entire mechanism. No other
part of the model needs to know that blocking exists.

The last station has no downstream, so `downstream` is `None` and it never
blocks: completed boards leave the line, and the line always has room for one
more finished board.

### Blocking, starving and breakdowns

Only half of backpressure is new in this stage, and it is worth being precise
about which half.

**Starving was already there.** With unbounded buffers a dead placer still passes
nothing downstream, so the SPI runs dry exactly as it does now. Starvation needs
no buffers — it is a fact about flow, and it is derivable from the existing
`service_finished` and `service_started` events.

**Blocking is what finite buffers add.** An uncoupled printer works happily into
an infinite queue and never notices that the machine ahead of it has stopped. A
coupled one blocks within a couple of minutes. There is a test pinning this
distinction, so that neither half is mistaken for the other.

A blocked station does not wear out. Failures accrue on operating time, and a
station holding a finished board it cannot put down is not operating. This falls
out of the existing mechanism rather than needing one of its own — `_end_work`
has already run for the board being held, so the wear countdown is suspended the
same way it is for an idle machine. That is exactly the kind of thing that
silently works or silently does not, so it is verified rather than assumed: a
test runs a printer that spends over 15% of the shift blocked and checks that its
observed MTBF, measured against operating time, still matches the configured
value to within 12%.

### Why this line cannot deadlock

A deadlock would need a cycle of machines each waiting on the next. There is no
cycle here, and the reason is structural rather than lucky.

Station *k* blocks only while waiting for a slot in station *k+1*'s buffer. That
slot frees when station *k+1* starts work on a board, which needs its machine
free, which needs its own current board to have departed — which needs a slot in
station *k+2*. So the waits-for relation only ever points **forwards along the
line**, from *k* to *k+1*. A relation that strictly increases an integer index
cannot contain a cycle.

The chain also terminates. Station *n*, the last one, waits on nothing: its
boards leave the line. So station *n* always finishes and releases its machine,
which frees a slot in buffer *n*, which unblocks station *n−1*, and so on
backwards. Every blocked station is eventually released, by induction from the
end of the line.

Two conditions this argument leans on, both of which are enforced or true today:

- **Every buffer holds at least one board.** `input_buffer` must be `>= 1`, or
  omitted for unbounded; zero is rejected by the config validator.
- **The routing is a simple path.** Every board visits the stations in order,
  once each.

The second is where the argument would fail. **Add a rework loop — an SPI
failure sending a board back to the printer — and the waits-for relation stops
being monotonic in the station index, and deadlock becomes possible.** A board
being reworked upstream could hold a slot that a downstream station is waiting
on, while the rework itself waits on that downstream station. If rework is ever
added, it will need a real remedy: a dedicated rework buffer outside the main
path, or a deadlock-avoidance rule. This is not a hypothetical worry about the
current model, but it is a live constraint on what can be added to it.

A test runs the line with one-board conveyors everywhere and breakdowns on three
of the four stations, and asserts that boards still flow, that they are
conserved, and that completions continue right up to the horizon.

### The four time accounts

Every station's capacity, over the measured window, is partitioned into exactly
four accounts:

| account | meaning |
|---|---|
| **work** | a board under the head, being processed |
| **block** | finished a board, holding it, waiting for space downstream |
| **starve** | free capacity with no board in it to work on |
| **down** | under repair — the whole station, however many slots it has |

They sum to 100% of `capacity × window`, and the station table reports all four.
`work` is the same number that used to be called utilisation.

The identity is the sharpest structural check available on this stage, but only
because of how it is computed. If `starve` were derived as
`window − work − block − down` the identity would be true by construction and
would test nothing at all. Instead station **occupancy** — how many boards are
physically inside — is tracked from its own events, and starvation is
`capacity − occupancy`. The identity then holds only if occupancy always equals
the number of boards being worked on plus the number stuck waiting to leave. Get
the buffer wiring wrong and it breaks. It is asserted across four seeds and five
configurations, with and without breakdowns.

### What the log had to gain

Two event types: `transfer_blocked` and `transfer_unblocked`, carrying the board
and the station being held up.

Strictly, blocked intervals *are* derivable without them, since a block runs from
`service_finished` at station *k* to `queue_entered` at station *k+1*. They are
recorded explicitly anyway, for a better reason: `summarise` does not know the
line's topology. It learns which stations exist from the run header, but not
their order, and inferring one station's metric by pairing events across two
stations would mean teaching the stats layer the routing. Explicit events keep
the reduction topology-agnostic, and keep it correct if routing ever stops being
a simple path.

## The service

Stage 3 adds a second consumer of the simulation: an HTTP API with Postgres
behind it. The interesting question was never whether FastAPI can be wired up.
It was whether the claim this project has been making since stage 1 -- that the
core knows nothing about its consumers -- would survive contact with one.

**It did.** Adding a database and a live event stream required writing two
classes, both of which implement the one-method `EventSink` protocol the CLI's
JSONL sink already implemented:

```python
class DatabaseSink:
    def emit(self, event: Event) -> None:
        self._pending.append(event)
        if len(self._pending) >= self._batch_size:
            self.flush()          # COPY into run_events


class LoopBridgeSink:
    def emit(self, event: Event) -> None:
        self._loop.call_soon_threadsafe(self._deliver, event)
```

`line.py` and `stations.py` were not touched. Not a line -- and the proof is not
an assertion of good intentions, it is a test: the same configuration and seed
submitted through the API produces events **byte-identical** to the CLI's JSONL
output, after travelling through a worker thread, a COPY and Postgres.

What the model did gain was three optional keyword arguments in `compare.py`, so
the service can persist a comparison's constituent runs, and a `to_dict` on the
stats objects. Both are additions; nothing was removed and no signature the CLI
uses changed.

A test walks the AST of every module under `src/smtsim` and fails if any of them
imports the service package or any service-only dependency. A second spawns a
subprocess and asserts that `import smtsim` pulls in no fastapi, no psycopg, no
pydantic. The dependency arrow is enforced, not merely intended.

```
src/smtsim/          the simulation. Depends on simpy and the standard library.
src/smtsim_service/  the API. Depends on the simulation.
```

### Why threads and not processes

The simulation is synchronous and CPU-bound; FastAPI runs an asyncio loop.
Calling `Line.run` in a request handler would stall every other connection for
its duration, so runs go to a `ThreadPoolExecutor` and `POST /runs` returns a job
id immediately.

The GIL is a fair objection to that and deserves a real answer rather than a
shrug. Threads do not give this service parallelism: two simulations on two
worker threads take about as long as running them one after the other. What
threads *do* give is the thing actually needed here — the event loop stays
responsive, because CPython releases the GIL every 5 ms by default and the loop
gets its slice. A 480-minute shift is about a tenth of a second of CPU, so the
worst case a competing request sees is a handful of milliseconds of added
latency.

Processes would give real parallelism, and they cost more than they look:

- **The sink boundary would have to become a pipe.** Today a sink is a method
  call on the same object the simulation is writing to. Across a process
  boundary every one of 7,000 events per run would need pickling and a socket
  write, and `loop.call_soon_threadsafe` — the whole live-streaming design —
  stops being available. You would end up with a multiprocessing queue and a
  reader thread, which is a thread pool with extra steps.
- **Configs would have to be picklable and copied.** They are, but it is another
  constraint on a model that currently has none.
- **Memory multiplies.** Each worker carries its own interpreter and its own
  copy of the event list.

**The threshold at which I would revisit it.** Two triggers, either one
sufficient:

1. **A single run stops being short.** At a tenth of a second per shift, the GIL
   is a rounding error. At ten seconds — multi-day horizons, or comparisons with
   hundreds of seeds — one run monopolises the interpreter and concurrent API
   latency becomes visible. The number to watch is the ratio of run CPU time to
   acceptable request latency; once that exceeds roughly 100:1, move.
2. **Untrusted configurations.** A worker thread cannot be killed in Python. A
   configuration that makes simulated time stand still runs forever and takes a
   worker with it — see below. A process can be killed, and if this service ever
   accepted configs from people who are not the operator, that alone would
   justify the move.

The honest end state for either trigger is not a process pool inside the API but
a separate worker service — the database is already the job queue in everything
but name, since job state lives in `runs.status` rather than in memory.

### A config that never finishes

Found while writing the tests, and worth recording. A line whose interarrival
distribution is fixed at zero — `{"kind": "constant", "seconds": 0}` — is
accepted by the configuration validator and then loops forever at `t=0`,
scheduling zero-length timeouts. Simulated time never advances, so the horizon is
never reached and the run never ends.

On the command line that costs you a terminal. Through the API it permanently
consumes a worker thread and strands a run in `running` with no way back, which
makes it a denial of service on an open endpoint. The service refuses such a
config at the boundary. The general problem is undecidable, so this is a guard on
the one reachable case rather than a solution, and it is the sharpest argument
for processes in the list above.

### Persistence

Postgres, with the schema managed by Alembic. No `create_all`, no hand-made
tables; a test asserts that no service module contains DDL at all.

| table | holds |
|---|---|
| `runs` | status, seed, horizon, warm-up, config, summary, timestamps, error |
| `run_events` | the event log, primary key `(run_id, seq)` |
| `comparisons` | both configs, seed count, the paired result |
| `comparison_runs` | which runs a comparison was built from, and their roles |

Events are written with **COPY**, not row-by-row `execute`. A shift is about
7,000 events and a 30-seed comparison is 60 runs and roughly 400,000; at that
volume the per-statement round trip dominates everything else, and COPY turns
the write into one streamed transfer. `DatabaseSink` buffers on the worker
thread and pays that round trip once per 2,000 events.

**`run_events.detail` is `json`, not `jsonb`, and deliberately.** jsonb is a
parsed representation: it sorts object keys and discards the original text, so a
detail object does not come back out the way it went in. That column exists to
reproduce a saved log exactly, and nothing ever queries inside it. The queryable
copy of the configuration lives on `runs.config`, which is jsonb. This was
measured rather than assumed — a round trip through jsonb reorders
`horizon_seconds, warmup_seconds, seed, line` into `line, seed, warmup_seconds,
horizon_seconds`, which is enough to break the byte-identity test.

The stored stream keeps the header events. `stats.summarise` reads `run_started`
for each station's capacity, the horizon and the warm-up, so a log stripped to
its board-level events could not be re-summarised. There is a test that
re-summarises the stored stream and asserts it reproduces the stored summary.

**Where this schema stops being appropriate.** Postgres will hold tens of
millions of rows without complaint, so the limit is not the row count as such:

- At around **a million events per run** — a 30-day horizon at this event rate —
  a single run's insert becomes a multi-second transaction and `GET
  /runs/{id}/events` becomes a paging exercise nobody enjoys. The fix is to stop
  storing raw events in Postgres for finished runs: keep the summary in the
  database and the log as a compressed object in blob storage, fetched on demand
  for replay.
- At **high run volume**, `run_events` is the only table that grows without
  bound, and it is append-only and never updated. Partition it by `run_id` range
  or by month and drop partitions on a retention policy, rather than issuing
  large `DELETE`s.
- For **analytics across runs** — "what was the p95 queue at the placer over
  every run last quarter" — this row layout is the wrong shape entirely. That
  wants a columnar store, and the summaries are already the right grain to feed
  one.

Two smaller limits already in the code. `store_events` defaults to `true` for a
run and `false` for a comparison, because a 30-seed comparison is 400,000 events
and you asked for a statistic, not a log. And the runner holds each run's events
in memory to compute its summary, so peak memory grows with event count; at a
million events that is the next thing to fix, by summarising incrementally.

### The API

| endpoint | |
|---|---|
| `GET /healthz` | liveness, plus whether the database answers |
| `POST /runs` | 202 with a run id |
| `GET /runs` | paginated, filterable by status |
| `GET /runs/{id}` | status and summary |
| `GET /runs/{id}/events` | paginated by `seq`, `?after=&limit=` |
| `WS /runs/{id}/stream` | live events, or replay if the run has finished |
| `DELETE /runs/{id}` | cascades to events |
| `POST /comparisons` | 202 with a comparison id |
| `GET /comparisons/{id}` | status, paired result, constituent runs |

Requests carry a configuration as JSON, not a path: the server has no access to
the client's filesystem.

**The configuration schema is defined exactly once**, in `smtsim.config`. The
request models take `config` as an opaque object and validate it by building the
real dataclasses. A hand-written Pydantic mirror of `LineConfig` would be a
second definition of four distribution kinds, capacities, failure blocks and
buffer rules, and the two would drift. It would also have its own error
messages; this way a buffer of zero is rejected by exactly the code that rejects
it in the CLI:

```
422  station 'pick_and_place' input_buffer must be >= 1, or omitted for unbounded
```

The cost is that OpenAPI shows `config` as a generic object rather than a typed
schema. That is paid for with a worked example generated from `DEFAULT_LINE`, so
`/docs` still shows a complete, valid, copy-pasteable body — and the example
cannot go stale, because it is derived rather than written down.

The summary the API returns is the same structure the CLI prints: all four time
accounts, the reliability figures, the bottleneck. Stage 4 renders from it and
recomputes nothing.

### Streaming, and what happens when a client falls behind

`WS /runs/{id}/stream` gives stage 4 exactly one code path. Connect and read
frames until the stream ends; whether the events are arriving from a worker
thread or being read back out of Postgres is not the client's problem. The
opening frame says which.

Frames are **batched** — flushed when 250 events have accumulated or 100 ms have
passed, whichever comes first, both configurable. A 480-minute run emits its
whole log in about a tenth of a second; sent one frame per event that would be
7,000 frames in 100 ms, which no browser will absorb. In practice the demo run
streams 83,000 events in 326 frames.

**A client that falls behind is disconnected, not degraded.** Each subscriber
has a bounded queue of events; when it fills, the socket is closed with code
1013 and the client is told to use `GET /runs/{id}/events`, which is lossless.
Dropping events was the other option and is the wrong one here: every event in
this stream mutates the state of the line, so a consumer that misses a
`service_finished` shows a board stuck at a station forever. A silently wrong
picture is worse than a visible disconnect when the lossless path is one HTTP
call away.

The consequence worth stating plainly: **the simulation is not a real-time
source.** It produces a shift far faster than any socket carries it, and the
queue is what absorbs the difference. The default holds 10,000 events, about a
shift and a half of headroom. A long enough run will outrun any finite queue, and
such a client should replay instead. Live streaming is for watching a run in
progress, not for guaranteed delivery.

### No authentication

There is none. Every endpoint is open, anyone who can reach the port can submit
runs, read every run and delete any of them. That is a deliberate scope
boundary for a portfolio project, not an oversight, but it is a real gap and
this service should not be exposed to a network you do not control. Adding it
would mean an API key or OIDC at the edge, per-run ownership on the `runs`
table, and rate limiting on `POST` — which is also where the untrusted-config
problem above becomes urgent.

## Running the service

```bash
cp .env.example .env      # then fill in POSTGRES_PASSWORD and SMTSIM_DATABASE_URL
docker compose up -d --wait
open http://localhost:8000/docs
```

`--wait` blocks until the database is healthy, the migration has run and the API
answers its healthcheck. Then:

```bash
curl -s localhost:8000/healthz

curl -s -X POST localhost:8000/runs -H 'content-type: application/json' \
  -d '{"config": <a line config>, "minutes": 480, "warmup_minutes": 30, "seed": 42}'

python scripts/stream_run.py --minutes 5000    # POST, then watch it stream
```

`make serve` wraps the compose invocation and `make demo-api` re-records the GIF
above.

**Migrations run as their own one-shot service, not on API startup.** Compose
waits for it with `service_completed_successfully`. Migrating on boot means every
replica races to run the same DDL, a bad migration takes down every instance at
once with no window to intervene, and the application container needs DDL
privileges at runtime that it otherwise would not. One explicit actor, one
controlled step, reviewable in isolation.

The image is multi-stage and built with uv: the build stage carries uv, the
lockfile and the toolchain, and none of it reaches the 73 MB runtime image, which
runs as an unprivileged user. Only the `service` extra is installed. The same
image carries the CLI, so `docker compose run --rm api smtsim --version` works.

Every environment variable is listed in `.env.example`. No credential is
committed, and the three that must be set use `${VAR:?message}` in the compose
file, so a missing password fails loudly instead of defaulting to something
unsafe.

### Running the service tests

They need a real Postgres — not a mock and not SQLite, neither of which would
exercise jsonb, enums or COPY:

```bash
docker run -d -p 55432:5432 -e POSTGRES_PASSWORD=test -e POSTGRES_USER=smtsim \
  -e POSTGRES_DB=smtsim_test postgres:17-alpine
export SMTSIM_TEST_DATABASE_URL=postgresql://smtsim:test@localhost:55432/smtsim_test
uv run pytest
```

Without that variable the 31 service tests skip with those instructions and the
169 simulation tests run as they always have.

## The replay UI

React and TypeScript in `web/`, built with Vite. SVG for the line rather than
Canvas: a few dozen moving elements is comfortably inside SVG's range, and it
keeps every board and station a real element that can be inspected, hit-tested
and — as it turns out — asserted on from a browser test.

### The WebSocket is a loader, not a clock

This is the whole problem of the stage, and everything else follows from getting
it right.

A 480-minute shift streams out of the API in about a tenth of a second. Nobody
can watch that. The naive design — render as events arrive — would produce a
blank screen, one frantic flicker, and a finished run, and it would also mean
seven thousand React renders in that tenth of a second.

So arrival and playback are separated completely:

- **The loader** takes events off the socket as fast as the network delivers
  them and appends them to a `ReplayTimeline`. It has no notion of speed.
- **The clock** decides what the viewer sees. Each animation frame it works out
  how much *simulated* time should have passed from how much *wall* time
  actually passed, times a speed multiplier, asks the timeline for the state at
  that moment, and reports **once**.

```ts
const simDelta = (Math.min(wallDelta, 250) / 1000) * this.speed;
this.time += simDelta;
this.state = this.timeline.advance(this.state, this.time);
this.emit();          // one React render, however many events were applied
```

Playback speed is therefore independent of arrival speed, which is what makes
live mode and replay mode the same thing as far as the viewer is concerned. The
`Math.min(wallDelta, 250)` clamp is there because a backgrounded tab hands back
an enormous delta on return; without it, switching away and back teleports you
to the end of the run.

### The reducer is the frontend's simulation core

`web/src/replay/reducer.ts` is a pure `(LineState, SimEvent) => LineState`. No
React, no DOM, no fetch, no clock — the same discipline as `smtsim/line.py`, for
the same reason: it can be tested by feeding it a real event log and comparing
the answer against the one Python computed from the same log.

That test is the frontend's equivalent of stage 3's byte-identity test, and it
works the same way — two independent implementations, in two languages, reducing
one log, agreeing on the numbers. It caught a genuine modelling gap: a board
caught mid-service by a breakdown belonged to no list in the first version, so it
vanished from the view *exactly* when the UI is most worth looking at. The
conservation test came up one board short and found it.

The reducer is immutable with structural sharing, which turns out to matter for
more than tidiness — see the next section.

### Seeking backwards, and the keyframe interval

The reducer only moves forward. There is no inverse of "a board finished at the
placer", so scrubbing backwards means starting from an earlier state and
replaying. Keeping every state costs too much; keeping only the first means a
scrub to the end of a long run replays the whole log.

Hence keyframes: retain the state every N events, and seek by finding the latest
keyframe at or before the target and replaying forward. Worst-case replay work is
N events, which bounds seek latency independently of run length.

What makes this cheap is the structural sharing. **A keyframe is not a snapshot
that has to be copied — it is a reference to a state object that already
exists**, and its incremental cost is only the station objects that changed since
the previous keyframe. Retaining one is a pointer.

N was measured, not guessed (`web/tests/keyframe-bench.ts`, `npm run bench`):

| events | interval | keyframes | worst seek | mean seek | objects retained |
|---|---|---|---|---|---|
| 7,322 | none | 1 | 0.62 ms | 0.28 ms | 1 |
| 7,322 | 1,000 | 8 | 0.11 ms | 0.04 ms | 36 |
| 73,220 | none | 1 | 6.29 ms | 2.93 ms | 1 |
| 73,220 | 1,000 | 74 | 0.10 ms | 0.04 ms | 366 |
| 292,880 | none | 1 | **23.29 ms** | 11.61 ms | 1 |
| 292,880 | 1,000 | 293 | 0.15 ms | 0.04 ms | 1,461 |
| 292,880 | 5,000 | 59 | 0.43 ms | 0.20 ms | 291 |

The honest headline is that **the 480-minute fixture does not need keyframes at
all**: seeking its whole 7,322-event log costs 0.62 ms. They start to matter
around 100,000 events, and at 292,880 — a multi-day horizon — seeking without
them costs 23 ms worst case, which is a dropped frame on every scrub.

1,000 is the choice. At that same 292,880-event log it seeks in 0.15 ms while
retaining 1,461 objects, on the order of 100 KB. Halving it to 500 doubles what
is retained and buys no latency anyone can perceive; raising it to 5,000 still
fits inside a frame but gives up headroom to save memory that was never the
problem.

### What the colours mean

Exactly the four time accounts the CLI table prints and the API returns, with no
fifth and none renamed. They partition each station's capacity over the measured
window and sum to 100%:

| colour | account | meaning |
|---|---|---|
| green | **working** | a board under the head, being processed |
| amber | **blocked** | finished a board and holding it — no room downstream |
| grey | **starved** | free capacity with no board to work on |
| red | **down** | under repair |

Conveyors are drawn as slots and labelled `n/capacity`, so a full conveyor is
visibly full. That is what makes blocking legible rather than something you have
to be told about: the amber station and the 3/3 conveyor in front of it are the
same fact, seen from two sides.

Below the line, a timeline strip gives one lane per station and marks where each
was down (red) and where it was blocked (amber), across the whole run. A
featureless scrub bar tells you nothing about where to look; this one shows the
interesting moments before you scrub to them, and clicking it seeks.

### One code path for a live run and a finished one

`WS /runs/{id}/stream` serves live events for a running run and replays stored
events for a finished one, and says which in its opening frame. The UI has one
implementation. The *only* thing it does differently with the answer:

- the scrub bar's extent grows as events arrive, rather than being fixed;
- a follow-the-tail toggle appears.

The timeline, the reducer, the clock and every component are identical either
way, which is what stage 3's design of that endpoint was for.

**The documented failure mode is handled.** When a client cannot keep up with a
live run the service closes with code 1013 — deliberately, because every event
mutates line state and a stream with holes in it draws a silently wrong picture.
The UI says so and falls back to paging `GET /runs/{id}/events`, which is
lossless.

That fallback restarts from the beginning rather than resuming where the socket
stopped: frames carry no sequence number, and in live mode the socket starts
wherever the run had got to, so there is no reliable way to line up what arrived
with what to ask for next. Reloading a few thousand rows is cheap; a stream
stitched together at the wrong offset is not.

It also has to keep up with a run that is *still going*. Paging until a page
comes back short only means the reader has caught up with what has been
**persisted** — the first version stopped there and cheerfully reported a
335,000-event run as complete at 40,000. It now consults the run's status and
keeps paging until the run is genuinely finished.

### The rest of it

The run list polls only while something is actually running; a list of finished
runs does not change on its own and polling it would be a request every second
and a half for the life of the tab.

The new-run form prefills from the worked example in the API's **own OpenAPI
document**, which the service derives from `smtsim.config.BASELINE_LINE`. A
config literal in the frontend would be a second definition of the line and it
would drift the first time a station gained a field. 422 messages are shown in
the service's own words — they come from the same validators the CLI uses, and
rewording them would only make them worse:

```
config: station 'pick_and_place' input_buffer must be >= 1, or omitted for unbounded
```

The summary panel renders what `GET /runs/{id}` returns and computes nothing.
Recomputing any of it in the browser would be a second implementation of
`stats.py` waiting to disagree with the first.

Comparisons are deliberately not built. The API serves them and the CLI reads
them well; a paired-difference view is its own piece of work.

## Running the UI

Under compose, with everything else:

```bash
cp .env.example .env      # then fill in POSTGRES_PASSWORD and SMTSIM_DATABASE_URL
docker compose up -d --wait
open http://localhost:8080
```

nginx serves the built assets and proxies `/api` to the service, so **the browser
only ever talks to one origin and no CORS is configured anywhere** — not in the
service, not in nginx. The API's own port is published for convenience and for
CI; a deployment can drop it entirely and reach the API only through the UI.

For development:

```bash
make serve          # the API and its database
make web-dev        # Vite on :5173, proxying /api to :8000
```

The dev proxy exists for the same reason as the nginx one: same origin, no CORS.

### Still no authentication, and now it has a front door

Stage 3 noted that the service has no authentication. Stage 4 gives it a UI, and
a UI makes a thing look finished in a way an OpenAPI page does not. So, plainly:
**every endpoint is open**. Anyone who can reach port 8080 can start runs, read
every run stored, and delete any of them. There is no login, no ownership, no
rate limiting, and a browser tab is a much easier way to find that out than curl
was.

This is a deliberate scope boundary for a portfolio project, not an oversight,
and it is why the compose stack binds to localhost by default. It should not be
exposed to a network you do not control. Closing the gap means an API key or
OIDC at the edge, per-run ownership on the `runs` table, and rate limiting on
`POST` — which is also where the runaway-configuration problem in *The service*
stops being theoretical.

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
smtsim compare configs/tight_buffers.toml configs/baseline.toml \
    --seeds 30 --minutes 480 --warmup 30 --out runs/comparison.json
```

Both configurations are run under seeds 1…30. Because of the named streams, seed
*k* gives both arms identical arrivals and identical service-time draws
everywhere the two configurations agree, so the pair of results for a seed
differ only by the change under test.

That is why the differences are analysed **pairwise**: subtract within a seed
first, then average, rather than comparing two independent means. The seed-to-
seed variation that both arms share cancels instead of drowning the signal. A
test compares a configuration against *itself* and asserts the mean difference is
exactly `0.0` — not approximately; the pairing is exact when nothing differs.

### The cheap what-if: longer conveyors

Lengthening the conveyors from one board to three changes no machine at all. It
is a length of steel, not a placement head.

```
metric                  baseline   variant    diff           95% CI
throughput (boards/h)      60.52     61.10   +0.59   [+0.38, +0.80]
mean cycle time (min)      18.56     16.74   -1.82   [-2.57, -1.07]
```

Both intervals are clear of zero. Half a board an hour is a small effect — and
that is the point. **On several individual seeds the two configurations produce
exactly the same number of boards.** No single shift could tell you this change
did anything; only the paired comparison across 30 seeds resolves it. That makes
this a better demonstration of the tool than the two-placer comparison, whose
effect is large enough to be obvious.

The mechanism is worth following, because it is not the obvious one. Shortening
the conveyors makes the *printer* block far more — 30.5% of the shift instead of
17.6%. But blocking upstream of the bottleneck is nearly free, because the
bottleneck is the constraint and it stays fed either way. What actually costs
output is the small increase in how often the placer **starves**: with only one
board of conveyor in front of it, a printer breakdown or a run of slow prints
empties its input before the placer has finished its current board. The
throughput loss is entirely that.

### The expensive what-if: a second placement head

```
metric                  baseline   variant    diff            95% CI
throughput (boards/h)      61.10     63.00   +1.90   [+1.28, +2.52]
mean cycle time (min)      16.74      8.11   -8.62  [-10.42, -6.83]
```

Throughput barely moves — the baseline manages 61.1 boards/hour against an
arrival rate of 63.2, so the second head recovers about two boards an hour and
then hits the ceiling imposed by how fast boards are fed in. What it really buys
is cycle time, more than halved, because the queue in front of the placer largely
disappears. If the question is "can we ship more boards", the answer is *not
much, feed the line faster first*. If it is "why is work-in-progress so high and
delivery so erratic", the answer is *this*.

Coupling the line made both numbers worse than they looked in stage 2: the
baseline lost about 0.8 boards/hour and gained about two minutes of cycle time
once the conveyors became finite. That is the honest cost of the extra realism,
and it is why the stage 2 figures are not quoted anywhere above.

### Reading the output

An interval clear of zero means the difference is unlikely to be an artefact of
which seeds happened to be drawn. **That is all it means.** It is not a claim
that the difference is large, and it is certainly not a claim that the change
pays for itself — that is a question about the price of conveyor, the price of a
placement head, and the value of a board, which this program knows nothing about.
The output says so in as many words, every time.

An interval that *spans* zero is equally worth reading carefully: it means these
runs did not separate the two configurations, not that the configurations are the
same. More seeds narrow the interval.

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
- **A board is never scrapped or reworked.** Every board that enters leaves at
  the far end, and the SPI station inspects without ever rejecting. Real solder
  paste inspection exists precisely to reject boards, and rework is the obvious
  next thing to model — but see the deadlock argument above, because a rework
  loop is not a free addition.
- **Transport between machines is instantaneous.** A conveyor holds boards but
  takes no time to move them. For a line whose cycle times are tens of seconds
  and whose conveyors are a metre long, this is a small error; on a line with
  long transfer sections it would not be.
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

# are longer conveyors worth it? 30 shifts each, paired by seed
uv run smtsim compare configs/tight_buffers.toml configs/baseline.toml \
    --seeds 30 --minutes 480 --warmup 30 --out runs/comparison.json

# is a second placement head worth buying?
uv run smtsim compare configs/baseline.toml configs/two_placers.toml \
    --seeds 30 --minutes 480 --warmup 30
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

`make install`, `make test`, `make lint`, `make run`, `make stats`,
`make compare`, `make serve` and `make demo` wrap the common commands.

None of the above needs a database, a container or the service extra. The
simulation is installable and usable on its own, which is the point of the
optional dependency group: `uv sync` gives a lean environment with simpy and
rich, and `uv sync --extra service` adds fastapi, psycopg and the rest. See
*Running the service* for that side.

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
  compared. `baseline.toml` is the reference line; `tight_buffers.toml` differs
  from it only in conveyor lengths, and `two_placers.toml` only in the placer's
  capacity. Each pairs with the baseline to make one what-if.

Every station takes a `capacity`, a `service_time` drawn from one of four
distributions, an optional `failures` block and an optional `input_buffer`:

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
| `transfer_blocked` | the finished board has nowhere to go; the station is stuck holding it |
| `transfer_unblocked` | space opened downstream and the board moved on |
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

202 Python tests, in about fourteen seconds with a database and six without —
the 31 service tests skip cleanly when `SMTSIM_TEST_DATABASE_URL` is unset — plus
46 Vitest tests and 3 Playwright specs in `web/`. The ones that matter are
properties rather than golden values:

- **Determinism** — the same seed writes byte-identical files; different seeds do
  not; the progress hook and the compare run-hook do not perturb results; the
  global `random` module is never touched; and a pinned hash of the default
  line's board-level events fails loudly if the model ever drifts.
- **Common random numbers** — on an uncoupled line, changing the placer leaves
  every printer and arrival timestamp identical. On a buffered line it leaves the
  first ten boards identical and then demonstrably diverges. Both halves are
  asserted: a test that had merely been loosened until it passed would still pass
  with the streams broken.
- **Buffer compatibility** — a line with no `input_buffer` produces the pinned
  board-level hash, byte for byte, and emits no blocking event at all.
- **The four time accounts** — work, block, starve and down sum to the whole
  window per unit of capacity, across four seeds and five configurations, with
  and without breakdowns. Starvation is computed from independently tracked
  occupancy rather than as the residual, so the identity has something to catch.
- **Backpressure** — a placer breakdown blocks the printer behind it and starves
  the SPI in front of it, and the same run with unbounded buffers does the second
  but not the first. Blocked time from `stats.py` is cross-checked against an
  independent reduction of the same log.
- **Deadlock freedom** — one-board conveyors everywhere plus breakdowns on three
  stations still runs to completion, conserves boards, and keeps completing right
  up to the horizon.
- **Conservation** — boards arrived equals boards completed plus boards still on
  the line, with and without breakdowns.
- **Monotonicity** — timestamps never decrease.
- **Bottleneck** — pick-and-place has strictly the highest utilisation of the
  four, across several seeds.
- **Causality** — no board's `service_started` precedes its `queue_entered`, no
  board is served without having queued, no service starts or finishes inside a
  station's downtime window, and every completed board visits all four stations
  in line order.
- **Capacity** — no station is ever working on plus blocking more boards than its
  capacity, re-derived from the log independently of the stats accumulator.
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
- **Layering** — the AST of every core module is walked for a forbidden import,
  and a subprocess confirms that `import smtsim` pulls in no service dependency
  at all. CI reinforces it by running the simulation suite in an environment
  where those packages are not installed.
- **The service** — run and comparison lifecycles end to end against a real
  Postgres, event pagination, live streaming, replay of a finished run, frame
  batching, the disconnect-on-slow-consumer policy, 422s carrying the config
  validator's own messages, a run that fails mid-simulation recording its error
  instead of hanging in `running`, and migrations up, down and up again.
- **The proof** — a run submitted through the API produces events byte-identical
  to the same seed and config through the CLI. If that passes, the core really
  is untouched.
- **The frontend reducer** — fed a real 7,322-event log committed as a fixture,
  it must agree with the summary Python computed from the same log: boards
  completed, boards arrived, per-station failure counts, and conservation. Two
  implementations, two languages, one log.
- **Seeking** — state at time *t* reached by playing forward must equal state at
  *t* reached by seeking from a keyframe, over a dozen sample times, in both
  directions, and identically for every keyframe interval from 1 to 100,000.
- **The browser** — three Playwright specs against the real compose stack: play
  advances the clock and boards come off the line, the timeline seeks forwards
  and backwards, and a conveyor fills to its capacity and never past it.

## Extension points

Stage 1 marked one, for breakdowns; stage 2 filled it in, and the comment in
`stations.py` now records what it became rather than what it would be. The
design bet it was making — that the whole feature could be confined to the
service block and one process beside it — held. Boards, arrivals, the stats
reduction and the CLI were extended, not restructured.

Stage 2b's finite buffers were the prediction that did *not* hold. The stage 2
README said they would not be a local change, because they alter when a station
releases its resource rather than what it does while holding it — and that was
right. `Station.visit` grew a parameter, a return value and a handoff step, and
`Line._board` had to start carrying a slot from one station to the next. The
change was contained, but it was structural, not an extension point.

What did hold is the compatibility guarantee: a station that declares no buffer
takes no part in any of it, and the pinned hash proves it.

## Roadmap

**Stage 1 — the line.** ✅ Four stations, variable service times, queues that
emerge from timing, a deterministic event log, a CLI.

**Stage 2 — breakdowns and what-if comparison.** ✅ Per-station random streams,
MTBF/MTTR failures on operating time with work resumed rather than restarted, a
warm-up window, availability and observed-reliability metrics, and
`smtsim compare` with paired confidence intervals.

**Stage 2b — finite buffers.** ✅ Conveyors of a few boards between the machines,
blocking-after-service when one fills, starving when one empties, a four-way
partition of each station's time, and a deadlock argument that holds for a linear
line and would not survive a rework loop.

**Stage 3 — FastAPI and Postgres.** ✅ An HTTP and WebSocket API, simulations
run off the event loop in a worker thread, job state and event logs in Postgres
behind Alembic migrations, a paired comparison endpoint, Docker Compose and CI.
The simulation core was untouched, and there is a byte-identity test that says
so rather than a claim that hopes so.

**Stage 4 — web replay UI.** ✅ A React and TypeScript front end that replays a
stored run or follows a live one through the same endpoint and the same code
path. A pure reducer with keyframe seeking, a requestAnimationFrame clock
decoupled from arrival rate, an SVG line coloured by the four time accounts, and
conveyors that visibly fill. Served by nginx on the same origin as the API, so
there is no CORS anywhere. The event log already contained everything it needed:
no endpoint was added and no event type was invented for it.

**Later, if it earns its place.** Authentication and per-run ownership, which is
now the most conspicuous gap; a comparison view in the UI, since the API already
serves everything one would need; a separate worker process so a runaway
configuration can be killed; event logs in blob storage once runs get long enough
that Postgres is the wrong home for them; scrap and rework, which the deadlock
argument above says is not a free addition.

## Regenerating the demo

The terminal GIFs are recorded with [VHS](https://github.com/charmbracelet/vhs)
from `demo/demo.tape`, `demo/compare.tape` and `demo/api.tape` (which needs a
running Docker daemon):

```bash
brew install vhs
make demo        # the simulation demos
make demo-api    # the service demo; brings the compose stack up itself
```

The replay GIF is a browser, not a terminal, so VHS cannot record it. It is a
Playwright script that captures frames and hands them to ffmpeg
(`web/scripts/record-replay.mjs`). It stages the recording rather than just
pressing play: at 1000x a four-minute breakdown flashes past in a single frame,
so it runs the line at speed, then drops to 100x over a real placer failure —
which is the sequence the project is about.

```bash
make serve       # the stack must be up, with a finished run
make demo-web
```

## Licence

MIT.
