# The interactive benchmark — what the editor actually makes you wait for

This is the *editing* workload family of **`lean-perfbench`**, the benchmark suite behind the
manuscript. A word on the name, because a reader's first guess will be wrong: the other Lean
benchmarks in the field (LeanDojo, VeriBench, LeanCat and the rest) measure whether a system
can *prove* a given theorem. `lean-perfbench` measures none of that. It measures resource use
and the latency a person experiences, on proofs already known to succeed. The suite has five
workload families — starting up, checking a file, building a project, **editing**, and disk —
and this directory is the fourth of them. The other four are batch measurements driven by
`scripts/repro-import.py` and the harness described in the manuscript.

`lspbench.py` speaks the Language Server Protocol to `lake serve` exactly as the VS Code
extension does, and replays *session scripts*: timed sequences of edits, thinking pauses, goal
/ hover / completion requests and assertions that model a person editing a file.

---

## 1. Why this is not a batch benchmark, and why it is here

A batch benchmark runs `lean file.lean` and times the process. That is a real workload — it is
what a build does — but it is not what a mathematician experiences. In an editor the file is
never re-run from scratch. There is one long wait when the file is opened, and after that a
sequence of much smaller waits: the time until the goal display and the error markers are
current again after each burst of typing, the latency of a goal or completion request, the
several seconds an `exact?` takes.

Those two things do not move together. **This benchmark twice caught a change that improved
every batch measurement and made interactive use worse.**

* Lazy part loading (improvement 4) cut `import Mathlib` from 3.4 s to 2.7 s and its memory
  from 2.76 GB to 1.64 GB — an unambiguous win by every batch number. In a session it took the
  first `exact?` from 15.3 s to **22.2 s**, worse than unmodified Lean, because the
  library-search walk faults in precisely the module parts the change had avoided loading.
  A beginner's session went from 23.7 s of waiting to 27.5 s.
* The same measurement is what justified building the prebuilt search index (improvement 5) and
  the tactic index image (improvement 6). With all six improvements on, that session is 11.3 s
  and that `exact?` is 3.7 s.

No batch measurement in the suite shows either of those. This is the only instrument in the
package that detects that class of regression, which is why it is shipped.

The other thing it establishes is a negative result worth having: **keystroke latency is not
the problem.** Median edit latency is 207–216 ms on every toolchain and every tier — that is
the Lean server's own `reportDelayMs` debounce, not elaboration. Of the ~24 s a beginner spends
waiting on Lean in one session, 63 % is the file's opening import and most of the rest is a
single `exact?`. Re-elaborating everything below an edit ("cascades") costs 200–390 ms in files
whose bodies elaborate in under a second, which all ten sessions' files do.

---

## 2. What you need

* **Python 3.10+**, standard library only. `lspbench.py` imports nothing else, and the three
  modules under `lib/` (result files, metadata capture, Lean version probing) are likewise
  stdlib-only. There is nothing to install.
* **`elan`** on `PATH`, and a Lake project with its `.olean` cache already downloaded.
  Nothing here builds a library; a session opens a file and requires the oleans to exist
  (`dependencyBuildMode: never`).
* For the six sessions shipped ready to run: **Mathlib v4.33.1**, exactly as
  [`code/scripts/setup-mathlib.sh`](../../scripts/setup-mathlib.sh) produces it —

  ```sh
  ../scripts/setup-mathlib.sh --dir ~/mathlib4        # ~5 min, ~7 GB
  ```

  That checkout pins `leanprover/lean4:v4.33.1`, which is the toolchain the rest of this
  package measures against.
* macOS or Linux. The process sampler uses `ps`; the memory probes in the multi-file sessions
  additionally use `vmmap` and `vm_stat`, which are macOS-only and are simply absent from the
  results elsewhere.

---

## 3. How to run it

From this directory:

```sh
# the six Mathlib sessions on the project's own toolchain (stock v4.33.1)
python3 lspbench.py run --all --project-dir mathlib=~/mathlib4

# one session, without writing a result file
python3 lspbench.py run --session sessions/novice-01-induction-sum.json \
        --project-dir mathlib=~/mathlib4 --no-write

# A/B: the same sessions against a second toolchain directory (e.g. the fork you built
# per docs/building.md).  Configurations are run session by session, not all of one then
# all of the other.
python3 lspbench.py run --all --project-dir mathlib=~/mathlib4 \
        --toolchain project --toolchain ~/lean4/build/release/stage2 --repeat 2

# comparison tables from everything measured so far
python3 lspbench.py report
python3 lspbench.py show results/<file>.json
```

`--all` picks up the `*.json` files directly in `sessions/` — the six Mathlib ones — and does
not descend into `sessions/corpus/` or `sessions/multi/`. Those are run by naming them with
`--session`.

Options worth knowing:

| flag | what it does |
|---|---|
| `--project-dir mathlib=PATH` | where the project lives (repeatable, one per project name). Without it the default is `$LEAN_WORK/mathlib4-v4.33.1`, i.e. the layout of the development machine. |
| `--toolchain` | `project` (the default): the project's own toolchain through the `elan` proxy. Otherwise a directory whose `bin/lake` and `bin/lean` are used directly, so the watchdog *and* every file worker are that toolchain's `lean` — verified from `ps` and recorded in `summary.worker_bin`. Repeatable. Sessions whose project pins a different Lean version are skipped for an explicit toolchain rather than silently mismeasured. |
| `--repeat N` | timed repeats per session per toolchain. Header times vary ±20 % between repeats (page cache, `lake setup-file` I/O); two is thin, and for toolchain comparisons the *minima* are more informative than the means. |
| `--check` | validation mode: stop repeating a session as soon as one of its assertions fails. |
| `--strict` | abort a session at the first failed assertion instead of recording it and continuing. |
| `--no-write` | do not write a result file or touch the index. |
| `--env K=V` | extra environment for the server process — this is how a single fork switch is turned off; see [`docs/switches.md`](../../../docs/switches.md). |
| `--tag`, `--notes` | stamped into the result file, and selectable in `report`. |

**Two rules, the same two the batch scripts follow.** Do not run a fork binary inside
`lake env` (it exports a library path pointing at the stock toolchain — see
[`docs/building.md`](../../../docs/building.md) §2.3); `lspbench.py` invokes `<toolchain>/bin/lake serve`
directly for exactly this reason. And interleave configurations rather than running all of one
and then all of the other, which `--toolchain a --toolchain b` does per session.

Scratch files are written under `$LEAN_WORK/scratch/lspbench/<session>/` (default
`~/lean-work/scratch/lspbench`); a session that starts from a real project file **copies** it
there and never edits the project in place.

---

## 4. What the sessions are, and what they assert

Ten sessions: three novice, three intermediate, four advanced. All of them type in line-sized
chunks with 0.5–1.5 s between lines and 2–10 s thinking pauses, read the goal after steps, make
mistakes and correct them, and end with a file that elaborates without errors. Scripted thinking
time is real `sleep` and is never counted as Lean time.

**The scripts are self-validating.** Every mistake carries an `expect_errors` or
`expect_message` assertion and every fix an `expect_no_errors`; the runner additionally requires
the final file to be error-free. So a session that "passes" really did exercise the intended
error → fix cycle, rather than, say, silently failing to produce the error it was written
around. A toolchain that produced different diagnostics would fail visibly instead of quietly
returning a faster number. This is what makes the benchmark usable as an equivalence check as
well as a timing one: in the manuscript's comparison runs, every assertion of all 48 session
runs held, with the fork's diagnostics identical to stock's at every assertion point.

It also means **a failing assertion is not automatically a bug in Lean.** For the corpus
sessions it usually means the upstream file has been edited since the session was written
(§6).

### Ship-ready: Mathlib only (`sessions/`)

| session | tier | models |
|---|---|---|
| `novice-01-induction-sum` | novice | Gauss' sum and the sum of odd numbers by induction: `rw [ih]` before the sum is unfolded, forgetting `mul_add`, an unsolved goal closed by `ring`, rewriting with `Nat.succ_eq_add_one` on a goal that already shows `k + 1`, `simp made no progress`, `exact?`, hover, completion |
| `novice-02-logic-sets` | novice | logic and sets: `intro` on a conjunction goal, `constructor` bullets in the wrong order, one `intro` too many, misplaced parentheses, `exact hx` for `x ∈ s ∩ t`, a tactic after the goal was already closed (`no goals`), `exact?`, completion |
| `novice-03-real-inequalities` | novice | real inequalities: a guessed lemma name, expecting `rw [add_sq]` to finish an identity, `positivity` then `linarith` on a goal that needs `nlinarith [sq_nonneg _]`, forgetting `.2` of `abs_lt.mp h`, `decide` on ℝ, `exact?`, completion |
| `inter-01-finset-sums` | intermediate | precise imports: a `calc` with a wrong intermediate step; extracting the algebra into a lemma *above* the theorem (a cascade); using `linarith` before its import exists, then adding the import — **which restarts the worker and re-imports mid-session**; `linarith` failing on a nonlinear goal → `nlinarith`; tuning a `simp only` list |
| `inter-02-real-analysis` | intermediate | `Mathlib.Data.Real.Sqrt` plus tactic imports: `linarith` on a nonlinear goal, a `have` that is still nonlinear, `nlinarith`; `ring` where `field_simp` is needed; `Real.sqrt_sq` vs `Real.sq_sqrt`; a helper lemma inserted above and used |
| `inter-03-mathlib-topology` | intermediate | a contributor working inside `Mathlib/Topology/Basic.lean` (244 lines, `module` header): appends a lemma with one argument too many, moves it near the top so ~170 lines re-elaborate, breaks and repairs a mid-file proof, goal / hover / completion |

The novice tier is `import Mathlib` — the whole library, which is what a beginner writes. The
intermediate tier uses precise imports and is where the *cascade* behaviour is exercised: a
helper lemma inserted above a theorem re-elaborates everything below it.

### Corpus sessions (`sessions/corpus/`) — see §6

| session | project | Lean version it pins | file it edits |
|---|---|---|---|
| `adv-01-flt-quadratic` | FLT | v4.34.0-rc1 | `FLT/Mathlib/Algebra/Polynomial/QuadraticDiscriminant.lean` |
| `adv-02-carleson-lintegral` | Carleson | v4.34.0-rc2 | `Carleson/ToMathlib/MeasureTheory/Integral/Misc.lean` |
| `adv-03-pnt-gamma` | PrimeNumberTheoremAnd | v4.32.2 | `PrimeNumberTheoremAnd/Mathlib/Analysis/SpecialFunctions/Gamma/CriticalLineDecay.lean` |
| `adv-04-fc-polygon` | formal-conjectures | v4.33.1 | `FormalConjectures/OEIS/64313.lean` |

These model research-level work: a 10 s catch-up pause on a 100-line proof, changing the
*statement* of a lemma something below depends on, a typeclass-heavy corollary with an
undeclared universe, `measurability`, `gcongr`, `linear_combination`. `adv-04` is the only one
pinned to v4.33.1, and therefore the only advanced session that can also be run on the fork
toolchains this package builds.

### Multi-file sessions (`sessions/multi/`) — what a second open file costs

The editor's server starts **one `lean --worker` per open file**, and each imports the library
independently. These sessions describe one `lake serve` with several documents open; each
document is an ordinary session with its own events and its own assertions, and every
measurement is reported per worker.

| session | needs | models |
|---|---|---|
| `multi-00-single-control` | Mathlib | the control: one file, one worker, the same script as the primary document below |
| `multi-01-same-header-ladder` | Mathlib | three `import Mathlib` files opened one at a time, each allowed to settle, with a memory probe after each — the cost of the 2nd and 3rd open file with no contention |
| `multi-02-same-header-overlap` | Mathlib | the same three opened at t=0, 4 s, 8 s, so all three import at once, while file 1 is edited — contention |
| `multi-03-distinct-headers-ladder` | Mathlib | three real Mathlib files with three *different* headers — the case a per-header snapshot cache could not help |
| `multi-04-corpus-same-header-ladder` | **formal-conjectures** | three real files sharing one header, in a real project — the same-header case in the wild |

Four of the five run against Mathlib alone. `multi-04` needs the formal-conjectures checkout
(§6). Run them by name:

```sh
python3 lspbench.py run --session sessions/multi/multi-01-same-header-ladder.json \
        --project-dir mathlib=~/mathlib4
python3 lspbench.py mreport        # the multi-file tables
```

Two extra things the multi-file mode records, in single-file runs too:

* **the header is split** into its `lake setup-file` part and its `import` part. The split is
  protocol-exact rather than a guess: the worker publishes an *empty* diagnostics list for the
  version as soon as Lake has answered, long before the imports are loaded.
* **worker → document attribution**: the `lake setup-file` child carries the target `.lean`
  path on its command line and its parent is the worker, so RSS is tracked per document rather
  than summed over all workers.

`--prewarm` (reads every `.olean` of the project before each run) exists but is **off by
default and was not used for any published number**: on a machine whose page cache cannot hold
the library, reading 5.2 GB of oleans evicts as much as it warms. A discarded warm-up *run* of
the same session is the warm-up that works.

---

## 5. What the numbers mean

One JSON file per session run lands in `results/`, with `summary` also appended to
`results/index.json`; the raw LSP trace and the server's stderr for that run go to
`results/raw/<run-id>/<session>_r<rep>/`. (Those are your run's traces. The traces behind the
manuscript's own numbers are not in this package; they are available on request.)

The fields that carry the argument:

| field | what it is |
|---|---|
| `header_s` | `didOpen` → the first `fileProgress` whose range starts after the import block: the moment Lean begins elaborating your first line. Includes `lake serve` start-up, `lake setup-file` (4–5 s of it under `import Mathlib`, on every toolchain), the `initialize` handshake, and the import itself. **This is the number that dominates everything.** |
| `open_full_s` | the whole initial file finished. `open_full_s − header_s` is the body's own elaboration — 0.1–0.5 s in all ten sessions' files. |
| `edit_latency` | median / p90 / max over edits: `didChange` → that version fully processed. The median is the server's debounce and does not move. The **max** is where a regression shows. |
| `edit_blocked` | how long the *person* was actually blocked — zero when a scripted thinking pause already covered the latency. This is the honest denominator: latency that fits inside a pause costs nothing. |
| `lean_wait_total_s` + `lean_wait_breakdown` | total time waiting on Lean, split into open / edits blocked / requests. **The headline of the whole benchmark**: 23.7 s → 11.3 s per beginner session, of which 63 % of the "before" is `header_s`. |
| `human_pause_total_s` | scripted thinking time. Reported so that a session's wall clock can be read as `lean_wait + human_pause`; it is never counted against Lean. |
| `goal`, `hover`, `completion` | request latency distributions. Goal requests are 1–16 ms and are not a cost. The *first* completion pays for building the completion index: 0.23–0.5 s with precise imports, 1.2–2.0 s under `import Mathlib`. |
| `worker_rss_after_header_bytes`, `worker_rss_peak_bytes` | per-worker resident set. After the header: 5.3 GB stock vs 1.9 GB with the fork under `import Mathlib`; 1.1–1.6 GB vs 0.5–0.65 GB with precise imports. The *peak* is set by `exact?` on every toolchain, and `exact?` costs about +2 GB that is never given back. |
| `worker_restarts` | a worker restart mid-session, e.g. after an import is added. Costs 3.3 s on stock. |
| `worker_bin` | the `lean` binary actually running, read from `ps`. Check it. `lean --version` cannot distinguish the fork from stock (both print the same string by design), and Lake will happily run the project's toolchain instead of the one you thought you selected. |
| `loadavg_before` / `_after`, `dup_notifications` | interference and protocol hygiene. |

Read the *ratios*, not the absolute seconds: header time in particular moves ±20 % with the
page cache and with what else the machine is doing. Note also that a single busy Lean worker
drives the 1-minute load average to 3–6 on a 12-core machine all by itself, so a load stamp in
that range is not evidence of interference.

Two caveats that apply to the memory columns and are easy to get wrong:

1. **RSS counts shared pages.** Of a 5.3 GB post-import worker, ~4.8 GB is clean memory-mapped
   olean pages that the operating system holds *once* and shares between every worker, and only
   ~0.83 GB is genuinely private. Three open files cost about 8.4 GB, not 3 × 5.3 GB.
2. **On Linux, RSS depends on the machine's free memory**, because fault-around maps cached
   neighbours on every fault. Quote RSS at the memory size the target machine will have. See
   [`../README.md`](../README.md) §3.

---

## 6. The corpus sessions: what they additionally need

The four sessions in `sessions/corpus/` (and `sessions/multi/multi-04-…`) edit files in real
formalization projects. **The projects are not shipped** — they are large, they are other
people's, and each pins its own Lean version. Fetch them with the script here:

```sh
./fetch-corpus.sh                                  # all four, into ~/lean-work/corpus
./fetch-corpus.sh --only "formal-conjectures"      # just one
```

This clones each project shallow and runs `lake exe cache get`, which downloads its dependency
oleans and, on first use, makes `elan` install the Lean version that project pins. Budget
about **30 GB of disk and 20–40 minutes** for all four, plus roughly 1 GB per distinct
toolchain (three of them beyond v4.33.1: v4.34.0-rc1, v4.34.0-rc2, v4.32.2). One project alone
is ~8 GB.

Then:

```sh
python3 lspbench.py run --session sessions/corpus/adv-01-flt-quadratic.json
python3 lspbench.py run --session sessions/corpus/adv-04-fc-polygon.json \
        --project-dir formal-conjectures=~/lean-work/corpus/formal-conjectures
```

`--project-dir` is only needed if you cloned somewhere other than `$LEAN_WORK/corpus`.

**Three things to know before you read a corpus result.**

* **They can only be run on their own toolchain.** A v4.33.1 binary cannot load oleans built by
  v4.34.0-rc1, so `--toolchain <fork>` *skips* these sessions rather than mismeasuring them.
  `adv-04` (formal-conjectures, v4.33.1) is the exception and is the one advanced session that
  can be compared stock against fork.
* **They drift.** The scripts are written against the file contents at a particular upstream
  revision, and `fetch-corpus.sh` clones whatever those repositories point to today. When a
  session's assertions start failing, the overwhelmingly likely cause is that the file was
  edited upstream — a line the script inserts "after" no longer exists, or a lemma was renamed.
  Every run stamps its checkout's commit into `meta.project.git_commit` in the result file, so
  a failure can be traced to the revision it was seen on. Re-anchor the script, or pin the
  checkout to a revision at which it passes.
* **Adapting one is the intended use.** The session format is documented in §7; the most
  valuable thing you can do with this harness is write a session for *your* project and *your*
  editing habits, and see whether a change helps you.

---

## 7. Session script format

```json
{"name": "…", "tier": "novice|intermediate|advanced", "project": "mathlib|FLT|carleson|…",
 "initial_text": ["import Mathlib", "…"]   or   "initial_file": "path/relative/to/project",
 "description": "what human behaviour this models",
 "events": [ … ]}
```

The file is written to `$LEAN_WORK/scratch/lspbench/<name>/` (a project file named by
`initial_file` is copied there, never edited in place unless `"edit_in_place": true`) and
opened with `lake serve` running in the project directory, so its imports resolve through the
project's own `LEAN_PATH`.

| op | fields | what happens / what is measured |
|---|---|---|
| `insert` | `text`, position: `after` / `before` / `after_line` / `append` (+ `occurrence`) | one `didChange`; latency = `didChange` → `fileProgress` done for that version |
| `type` | as `insert`, plus `pause_ms` (default 400) and `chunks` = `lines`/`words` | one `didChange` per line with a typing pause between; pauses count as human time, latency is measured for the last version |
| `delete` | `text` | one `didChange` |
| `replace` | `find`, `text` (+ `occurrence`) | one `didChange` |
| `wait` | `ms` | a human pause; records whether Lean had finished the pending edit by the end of it |
| `diagnostics` | | block until the current version is fully processed |
| `goal`, `term_goal` | position (`after` / `before` / `end_of_line` / `line`+`col`), `expect_goals`, `expect_contains` | `$/lean/plainGoal` round-trip (blocks until that position is elaborated) |
| `hover` | position, `expect_hover` | `textDocument/hover` |
| `completion` | position, `expect_item`, `expect_min_items` | `textDocument/completion` — the first call pays for the completion index |
| `expect_errors` *n* (int or `[lo,hi]`), `expect_no_errors`, `expect_warnings`, `expect_message` `contains`, `expect_no_message` | | settle the pending edit, then assert on the whole file's diagnostics (message matching is case-insensitive) |
| `save` | | write the buffer to disk and send `didSave` |

A multi-file session sets `"kind": "multi"` and lists `docs`; `docs[0]` runs the session's own
`events`, every other document runs its own after it is opened. Three further ops are available
in the primary script: `open_doc` (a non-blocking `didOpen` in a background thread — a person
opening a second tab), `await_doc` (wait until that document has finished its header, its
initial elaboration and its own events), and `mem` (a labelled memory probe: `ps` RSS, plus
`vmmap -summary` physical footprint / mapped-file residency / dirty size per worker and for the
watchdog, plus system-wide `vm_stat`). A document with `"open_at_ms": N` is opened by a timer
*N* ms after the primary's `didOpen` — the case where a second file is opened while the first
is still importing.

---

## 8. Layout

```
lspbench.py            the harness (stdlib only)
lib/                   the three modules it imports: result files, run metadata, Lean version probing
sessions/              the six Mathlib-only sessions — `--all` runs exactly these
sessions/corpus/       the four sessions that need a real project (§6)
sessions/multi/        the several-open-files sessions; all but multi-04 need only Mathlib
fetch-corpus.sh        clone and prepare the four corpus projects
results/               created on first run: one JSON per session run, plus index.json and raw/
```

`lib/` is the measurement harness shared with the batch benchmarks, carried here so that this
directory runs on its own. Result files use the same `meta` schema as every other measurement
in the suite: machine, toolchain, project commit, Mathlib revision, load average and disk free
are stamped into each one, so a number can always be traced back to what produced it.
