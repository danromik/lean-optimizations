# Evidence index — every claim, and where it comes from

> **Written during development.** This document uses the short labels the work carried at
> the time (`A6b`, `T2`, `L1` and so on) and cites paths in the authors' research
> repository, not all of which are part of this package.
> [`development-labels.md`](development-labels.md) is the key to both.

Each row names the claim, the technical document that reports it in full (method, machine,
versions, caveats) and the directory holding the raw logs and result files that document was
written from.

Paths are relative to the root of the research repository. **The technical documents and raw
results are not in `release/`**; whether they are published alongside this package, published
in part, or kept private, is a decision for the author. Where a row's evidence has not been
shipped, the claim in the manuscript rests on the document named here.

Every measurement carries its own machine, load average, Lean commit and Mathlib commit in
its result file. The suite is `lean-perfbench`, five families of workload — starting up, checking
a file, building a project, editing, and disk; the harness that produced most of these numbers is
`code/benchmarks/harness/` and its schema is documented in `bench/README.md`. The editing family is shipped
here, as [`interactive/`](../code/benchmarks/interactive).

> **Audited 2026-09-01.** Every row below was checked against its document and its raw results.
> What was found, what was repaired and what is still open is recorded in
> [`evidence-audit.md`](evidence-audit.md).

## Machines

| Name | What it is |
|---|---|
| "the Mac" / M2 Pro | Apple M2 Pro, 12 cores (8P+4E), 32 GB, macOS 14.5, 16 KB pages |
| the Docker VM | arm64 Linux container on the same Mac, 4 KB pages — **cold-cache seconds from here are not trustworthy**; fault counts are |
| `t4g.large` | AWS, 2 vCPU Graviton2, 8 GB, gp3 — real hardware, and the shape of a small production server |
| `c7i.4xlarge` | AWS, 16 vCPU Sapphire Rapids, 32 GB, x86-64 — the architecture replication |

## The fork

| Claim | Document | Raw results |
|---|---|---|
| `import Mathlib` 10.17 → 2.27 s, 5.68 → 1.37 GB warm on the Mac; import + first `exact?` 15.3 → 3.73 s, 7.89 → 1.56 GB | `docs/fork/consolidated-full.md` §0, §3 | `bench/experiments/consolidated-full/` (`ab-raw.txt`, `exact-raw.txt`, `ab-summary.md`) |
| Equivalence 26/26 byte-identical on macOS/arm64, in both the switches-on and switches-off configurations, plus a `lake build` whose `.c` and `.ilean` outputs match byte for byte (the 9 `.olean`s are *not*, and that is the prebuilt search index's recorded entries — `LEAN_SEARCH_INDEX_RECORD=0` returns the tree to 9/9 graph-identical) | `docs/fork/consolidated-full.md` §5 | `bench/experiments/consolidated-full/equiv-*-summary.txt`, `equiv.sh` |
| Equivalence 27/27 byte-identical on Linux, on both architectures, in both configurations — and, on x86-64, a third time against a Mathlib and core library the fork built itself | `docs/linux/linux-full-fork.md` §4, `docs/linux/x86-linux.md` §6 | `code/benchmarks/results/linux-container-and-cloud/raw/equiv-*-summary.txt`, `code/benchmarks/results/linux-x86-64/raw/equiv-*-summary.txt` |
| Where the ten seconds went: 52,490 mapped files, macOS walking a sorted free-address list per request | `docs/analysis/import-path.md`, `docs/improvements/01-address-reservation.md` | `bench/experiments/import-path/`, `bench/experiments/a1-mmap-order/` |
| 4.8 GB of clean mapped library pages resident after the import, of which **+2.8 GB is the 8-byte counter read alone** — 772k `ConstantInfo` headers, one per definition, scattered through the files by the compactor's post-order emission | `docs/analysis/import-path.md` §2.4 (the step table: `+2,773 MB`), `docs/improvements/03-no-touch-refcounts.md` §0, §1, `docs/improvements/02-olean-layout.md` | `bench/experiments/a3-no-touch/`, `a2-layout/` |
| Lazy private parts and compiled code: 10,498 files mapped instead of 52,490 | `docs/improvements/04-lazy-part-loading.md` | `bench/experiments/a5-lazy-parts/` |
| Prebuilt `exact?` index: first call 5.5 → 0.8 s, +2–3 GB → +0.2 GB, suggestions identical (661 `Try this` lines). The 5.5 s is the baseline for the fork before this index existed, not stock's 3.8–3.9 s — lazy part loading is what made the walk expensive | `docs/improvements/05-prebuilt-search-index.md` §0, §4 | `bench/experiments/a6a-library-search/` |
| Tactic index image: 2.68 → 2.23 s, the 839 ms extension-rebuild loop removed | `docs/improvements/06-tactic-index-image.md` §0, §1.1, §4.1 — **but see the next row: the cache this document describes was replaced on 2026-09-01** | `bench/experiments/a6b-mappable-indexes/` |
| **The tactic index image is a shipped artifact, not a cache** (2026-09-01). The image is written on purpose once (`LEAN_TACTIC_INDEX_WRITE=1`) beside the library's `.olean` files; an ordinary run writes nothing. Same binary, image present vs absent: `import Mathlib` 2.36 vs 2.75 s, 1.39 vs 1.64 GB. A from-source-shaped workload of 250 distinct closures writes **0 images and 0 bytes**; `equiv.sh` 26/26. `LEAN_TACTIC_INDEX_CACHE_DIR`, `_EXTS`, `_TIMING`, `_MIN_FREE`, `_MAX_SIZE`, `_MIN_USES` and `~/.cache/lean-tactic-index` no longer exist | `docs/improvements/06-tactic-index-image.md`, superseding `docs/fork/a6b-cache-bound.md` on this point | `bench/experiments/a6b-shipped-image/` |
| What the cache cost, and why it had to go: 4,331 files / ~21 GB in seven minutes unbounded; 254.6 GiB written by a from-source Mathlib build to answer 216 lookups, +58.5 % on its wall time; −19 % on a real project's warm rebuild at the 5 GB cap | `docs/fork/a6b-cache-bound.md` §1, §5, §5.4 (superseded as a *design*, retained as the evidence for the decision) | `bench/experiments/a6b-cache-bound/` |
| **Leave-one-out attribution — the number to quote per change.** Each feature switched off in the finished fork, against all-on: the prebuilt search index +4.64 s / +2.96 GB on `exact?`; lazy part loading +0.76 s / +0.95 GB at import; the shell-first layout +0.34 GB at import, +1.08 GB on `exact?`, at no wall cost; the tactic index image +0.41 s / +0.28 GB; the address reservation +0.11 s and the no-touch reference counts +0.07 s, no measurable RSS. All-on vs unmodified v4.33.1: 10.46 → 2.37 s, 5.68 → 1.37 GB; + one `exact?` 15.07 → 3.84 s, 7.89 → 1.57 GB. **This supersedes the improvement-by-improvement ladder for attribution** — the ladder is order-dependent, and the six losses do not sum to the whole (1.49 s against an 8.09 s total gain) | `docs/fork/leave-one-out.md` | `bench/experiments/leave-one-out/` |
| The address reservation made `fork()` expensive, and `posix_spawn` fixed it: Lake start-up 11.2 → 0.74 s (stock 1.30 s) | `docs/fork/consolidated.md` §0, §8 (the measured `posix_spawn` result); `docs/fork/lake-startup-artefact.md` (the diagnosis, and the earlier hybrid-`lake` workaround, which reached 2.8 s) | `bench/experiments/consolidated/spawn-validation.txt`; `bench/experiments/lake-startup/forkcost.c` is the fork-cost microbenchmark |
| A 1,387-file clean build on the Mac: 47.7 min / 67.9 GB peak → 23.0 min / 33.8 GB (`LEAN_LAZY_PARTS=0`, i.e. improvements 1–3 plus `posix_spawn`, without lazy part loading) | `docs/fork/consolidated.md` §0, §4.4 | `bench/experiments/consolidated/` |
| The fork patch is 16 files, +2,119/−82, and applies cleanly to a clean v4.33.1. It carries the tactic index image's shipped-image redesign and the bounding of the lazy-loading index on top of the six improvements; a stray macOS metadata file (`.metadata_never_index`) was removed from it; The shipped Linux patch is 16 files, +2,150/−82, regenerated 2026-09-01 from `linux-shipped` (`linux-full` merged with the tactic-index redesign; the two touch disjoint files) so that it carries the same design as the macOS patch | `docs/fork/consolidated-full.md` §7 | `release/scripts/check-patches.sh` reproduces this in a few seconds |
| The three fixes are adoptable independently of each other and of the fork, and the reason is structural: the fork touches 16 files, the three fixes touch six further files the fork does not touch | `release/patches/README.md`, `docs/improvements/09-code-generation-fix.md`, `docs/build/olean-reproducibility.md` §5, `docs/improvements/11-language-server-config-fix.md` | `release/scripts/check-patches.sh` |

## Linux, and the architecture question

| Claim | Document | Raw results |
|---|---|---|
| `t4g.large`: warm import 5.68 → 3.99 s, cold 104.1 → 35.4 s, RSS 5.26 → 1.75 GB, ten-check loop 6.33 → 4.51 s per check | `docs/linux/linux-full-fork.md` §3.5, §3.6 | `code/benchmarks/results/linux-container-and-cloud/` |
| `t4g.large`, import + one `exact?`: 30.68 → 11.47 s, 6.33 → 2.50 GB. **The stock row is not a clean measurement** — stock's post-`exact?` peak is 8.60 GB on an unconstrained machine, more than a `t4g.large` has, so it is partly the box paging against itself | `docs/linux/linux-full-fork.md` §3.5; `docs/linux/x86-linux.md` §0 item 3, §3.2 | `code/benchmarks/results/linux-container-and-cloud/raw/`, `code/benchmarks/results/linux-x86-64/raw/` |
| Docker VM, arm64 container: `import Mathlib` 2.44 → 1.41 s, 5.70 → 1.95 GB (seconds from this machine are indicative; see the last row of this section) | `docs/linux/linux-full-fork.md` §0, §3.1 | `code/benchmarks/results/linux-container-and-cloud/raw/ab-12-warm.log`, `table-12-warm.md` |
| Everything replicates on x86-64 to within a few percent, and the ratios are architecture-independent | `docs/linux/x86-linux.md` | `code/benchmarks/results/linux-x86-64/` |
| **`max RSS` depends on the machine's memory headroom, not the ISA** — the sign of one comparison flips between 32 GB and 8 GB | `docs/linux/x86-linux.md` §3.2, §7.3, §7.6 | `code/benchmarks/results/linux-x86-64/raw/x86-ab-warm-8g*.log` |
| `vm.max_map_count`: 52,583 → 12,922 VMAs, 80 % → 20 % of the default | `docs/linux/x86-linux.md` §5, `docs/linux/linux-full-fork.md` §3.4 | as above |
| The shell-first layout on 4 KB pages; disk cost +0.0002 % over the Mathlib corpus | `docs/linux/a2-linux.md`, `docs/linux/x86-linux.md` §7 | `bench/experiments/a2-linux/`, `x86-linux/` |
| Stored `exact?` index entries cost +1.6 % of `.olean` size and beat the side-file cache | `docs/linux/a6a-linux.md`, `docs/linux/x86-linux.md` §8 | `bench/experiments/a6a-linux/` |
| Cold seconds from the Docker VM are not publishable; fault counts are | `docs/linux/a2-linux.md` §7 | — |

## The snapshot wrapper (no patched Lean needed)

| Claim | Document | Raw results |
|---|---|---|
| A check on the `t4g.large` 5.8 → 1.5 s warm, 104 → 34 s cold, ten checks 60 s → 15–20 s | `docs/linux/aws-sandbox.md` §5.2 (per check: 5.58 → 1.46 s; the 5.8 s is §0/§8's headline figure for the same case), §5.3 (ten checks: 6.15 → 1.9 s each), summarised in `docs/improvements/07-snapshot-wrapper.md` §0 | `bench/experiments/aws/raw/a4-ab.log`, `a4-loop.log`, `a4-save.log`, `a4-equiv.log` |
| It replicates on x86-64 with a stock toolchain: per check 2.87 → 0.71 s (4.0×), cold 109 → 25 s, ten in a row 3.04 → 0.82 s each, equivalence 17/17, and a snapshot check fits inside a 2 GB cgroup where a plain check does not | `docs/linux/a4-x86.md`; `docs/improvements/07-snapshot-wrapper.md` §0 item 12, §7 | `code/benchmarks/results/linux-x86-64/raw/a4-x86-*.log` |
| The macOS layer numbers the wrapper was designed against: stock 10.6 → 9.0 s (the loader still re-maps the 52k files), fork with improvements 1 and 3 3.54 → 1.77 s; ten sequential checks fork 41 → 19 s | `docs/improvements/07-snapshot-wrapper.md` §0, §4 | `bench/experiments/a4-snapshot/measure-raw.txt`, `measure-summary.md` |
| Lean's own snapshot loader validates almost nothing: a rebuilt dependency segfaults it, `-D` options are silently ignored | `docs/improvements/07-snapshot-wrapper.md` §2 | `bench/experiments/a4-snapshot/failmodes-raw.txt` |
| The wrapper's validation modes and their costs (0.12 s for 52k files in `stat` mode) | `docs/improvements/07-snapshot-wrapper.md` §3 | `bench/experiments/a4-snapshot/measure-raw.txt` |
| The snapshot and lazy part loading cannot be combined | `docs/linux/linux-full-fork.md` §5.2 | `code/benchmarks/results/linux-container-and-cloud/` |

## Where the thinking time goes

| Claim | Document | Raw results |
|---|---|---|
| Across 212 files and 105,700 tactic calls: 30 % of elaboration CPU is the bytecode interpreter, 22.5 % typeclass inference, 1.3 % kernel checking | `docs/census/t1-tactic-census.md` | `bench/results/*_census.json`, `bench/census/` |
| Compiling Mathlib's tactics cuts elaboration CPU 29–78 %; the interpreter share falls 88–94 %; `aesop` 98 → 5 ms/call | `docs/improvements/08-compiled-mathlib-tactics.md`, `docs/linux/t2-linux.md` | `code/benchmarks/results/compiled-tactics/`, `t2-linux/` |
| 74 % of typeclass inference is a question the same process already answered; 38.6 % is cross-file | `docs/census/tc1-typeclass-inference.md` | `bench/experiments/tc1-typeclass/` |
| In the editor: median edit latency 208–220 ms (the server's own debounce), 63 % of a beginner's waiting is the import, `exact?` is most of the rest; 23.7 → 11.3 s per session | `docs/interactive/l1-interactive-benchmark.md`, `docs/fork/consolidated-full.md` §3.3 | `code/benchmarks/interactive/` (shipped in this package as [`interactive/`](../code/benchmarks/interactive)), `bench/experiments/consolidated-full/lsp-*.log` |

## Build architecture

| Claim | Document | Raw results |
|---|---|---|
| A 1,373-file build performs 13.8 M module-loads over 12,387 distinct modules; 69–76 % of its CPU is loading | `docs/build/b1-build-redundancy.md` | `bench/experiments/b1-redundancy/` |
| Extending an environment is byte-identical to loading it from scratch, at full Mathlib scale: all 334 persistent extensions checked (0 of 334 with a different `importedEntries` order), 213 of them compared as bytes, plus a byte-identical 14 MB digest over 98,715 `simp` leaves and 42,904 instance leaves | `docs/build/b2-stage1-extend.md` §0, §4, §5 | `bench/experiments/b2-extend/` |
| A forked child can go on to import further modules — which our own earlier audit had said was impossible | `docs/build/b2-stage5-fork-walker.md`, `docs/improvements/10-fork-safety.md` | `bench/experiments/b2-fork-walker/`, `fork-safety/` |
| 118 Carleson files: 489,690 module-loads → 64,330 with a forking walker (the theoretical minimum), 7.6× less loading but only 1.14× less time | `docs/build/b2-incremental-import.md`, `b2-stage6-resumed-walker.md` | `bench/experiments/b2-resumed-walker/` |
| The 1.26× ceiling is a ratio, not a constant: import is 21 % of a build today, so the same saving is worth more if checking gets cheaper | `docs/build/b2-incremental-import.md` | as above |
| Unmodified Lean is deterministically fork-unsafe; 61-line fix; 40/40 byte-identical forked children; 0.25 s per check on a `t4g.large` | `docs/improvements/10-fork-safety.md`, `docs/fork-safety/linux-payoff.md` | `bench/experiments/fork-safety/` |

## The codegen and language-server fixes

| Claim | Document | Raw results |
|---|---|---|
| The codegen defect: 9 lines; 50 runs of 50 crash configurations exit 0; equivalence 21/22 → 22/22; 8,303 of 8,312 emitted `.c` files differ only by the inserted call | `docs/improvements/09-code-generation-fix.md`, `docs/linux/t2-linux.md` §6 | `code/benchmarks/results/compiled-tactics/` |
| **The codegen defect is already lean4#14359** (open, `P-high`, filed 2026-07-10). What is ours is a manifestation outside the specialization case, a stock-toolchain reproducer, and a built fix | `docs/lean-team/u4-prior-art.md` §2 | — (a paper survey; nothing was posted) |
| The server does not watch `lakefile.toml` / `lakefile.lean` / `lake-manifest.json`; 54 lines in three files; demonstrated on a real project with two files open and `warn.sorry` flipped | `docs/improvements/11-language-server-config-fix.md` | `bench/experiments/watchdog-config-watch/` |
| The `vscode-lean4` extension *does* already watch these files client-side and offers a server restart — so the user-visible gap is narrower than "nothing tells you", and wider for every non-VS-Code client | `docs/improvements/11-language-server-config-fix.md` §3.4 | — |

## Is the Lean team about to deliver this anyway?

| Claim | Document |
|---|---|
| v4.34.0-rc2 is within 1–2 % of v4.33.1 on every metric; nothing has become lazy | `docs/lean-team/u1a-rc2-vs-4.33.1.md` |
| One of the six changes is superseded by draft PR #14362, which achieves it by a cleaner mechanism; the rest compose with it, and the patch set applies to today's `master` with zero conflicts | `docs/lean-team/u1-lean-team-comparison.md` |
| None of the four in-flight PRs has landed or been reviewed; three are drafts; newest activity 2026-07-27 | `docs/lean-team/u1-lean-team-comparison.md`, `docs/lean-team/u3-rebase-survey.md` |

## The caveats, and the operational claims the package makes

These are claims made in `README.md`, `warnings.md`, `building.md` and `switches.md` rather than
in the manuscript's tables. They had no entry here until the 2026-09-01 audit.

| Claim | Document | Raw results |
|---|---|---|
| **Max RSS counts shared, memory-mapped pages.** Of a 5.68 GB post-import process, ~4.7–4.9 GB is clean file-backed olean pages and only ~0.83–0.86 GB is genuinely private; three editor workers cost ~4.8 GB once plus ~0.83 GB each plus a 1.10 GB watchdog nobody had counted, ≈ 8.4 GB, not 3 × 5.3 GB | `docs/analysis/import-path.md` §3.1, `docs/interactive/e1-worker-cost.md` §6 | `bench/experiments/import-path/`, `code/benchmarks/interactive/` (the multi-file sessions shipped in [`code/benchmarks/interactive/sessions/multi/`](../code/benchmarks/interactive/sessions/multi)) |
| **The shell-first layout and the no-touch reference counts attack the same waste, and the layout supersedes where both apply**, but they are not interchangeable: against Mathlib's *downloaded* library the layout is inert and the reference-count change gives 5.69 → 3.44 GB; against a library *rebuilt from source* the layout gives 5.68 → 2.76 GB and the reference-count change adds a further 47 MB on top (x86-64 at 8 GB: reference counts alone 4.150, layout alone 3.050, both 3.058 GB — the reference-count change *costs* 8 MB there) | `docs/improvements/02-olean-layout.md` §0, §6; `docs/linux/x86-linux.md` §7.3; `docs/improvements/03-no-touch-refcounts.md` §0 | `bench/experiments/a2-layout/`, `a3-no-touch/`, `x86-linux/raw/x86-ab-warm-8g*.log` |
| **On Linux the compiled-tactic libraries need `LD_LIBRARY_PATH`**: two of the eight `:shared` libraries carry a `DT_NEEDED` on `libLake_shared.so` and Lake's link adds no `RUNPATH`, so `--load-dynlib` fails outright. macOS's install name hides this, which is why it was missed until the port | `docs/linux/t2-linux.md` §0 item 3, §2 | `bench/experiments/t2-linux/` |
| **Never run a fork binary inside `lake env`**: it exports `DYLD_LIBRARY_PATH`/`LD_LIBRARY_PATH` at the *stock* `lib/lean`, and the `lean` stub then loads the stock runtime. The first harness run of the address-reservation change reported 10.35 s — a perfect stock number — for exactly this reason, and the result was deleted from `bench/results` | `docs/improvements/01-address-reservation.md` §5 (the harness note) | — (the bad result was deleted; `code/benchmarks/harness/leanenv.py` carries the fix) |
| **Rebuilding Mathlib from source costs ~43 min and ~11 GB** on an M2 Pro; 50 min 38 s on a 16-vCPU x86-64 box; 56 min 20 s in an arm64 container with `LEAN_SEARCH_INDEX_RECORD=1` (+1.5 % of build CPU) | `docs/improvements/09-code-generation-fix.md` (the 43-minute `LEAN_TACTIC_INDEX=0` control build); `docs/linux/x86-linux.md` §7.1 (50 m 38 s); `docs/linux/a6a-linux.md` §0 item 5, §2 (56 m 20 s, 11 GB tree, +1.5 % CPU) | `bench/experiments/consolidated/rebuild-mathlib.sh`, `x86-linux/raw/`, `a6a-linux/raw/` |
| Cold `import Mathlib` on the Mac, first Lean process after a reboot: **34.4 s**, 234k major faults, 5.69 GB | `docs/baseline-report.md` (tag `cold-after-reboot`) | `bench/results/20260825T023751Z_env-detail.json` — **note:** `baseline-report.md` cites `20260825T023603Z_env-detail`, which does not exist |
| Two changes are a **regression on their own**: lazy part loading alone takes a process's first `exact?` from stock's 15.3 s to 22.2 s, and a novice editor session from 23.7 s of waiting to 27.5 s. Only the prebuilt search index and the tactic index image together turn that into 3.73 s and 11.3 s | `docs/fork/consolidated-full.md` §0, §3.2, §3.3 | `bench/experiments/consolidated-full/exact-raw.txt`, `lsp-summary.txt` |

## The limits of the novelty claims

**Nothing here claims novelty. Every such claim reads "we found no public report", and that is
a negative search result, not a fact about the world.**

`docs/lean-team/u4-prior-art.md` is the record of what was searched and what was found. The
coverage is: the `leanprover/lean4` and `vscode-lean4` issue trackers via the GitHub search
API; Zulip; and the current development-branch source read directly.

**The Zulip leg was wrong until 2026-09-01 and has been redone** (`u4-prior-art.md` §5). The
first search used `leanprover-community.github.io/archive/`, a static mirror frozen at
2026-02-28 — a six-month hole covering exactly the period of this work. The live instance
(`leanprover.zulipchat.com`) has now been searched read-only via the API across `#lean4`,
`#general`, `#mathlib4` and `#lean4 dev`, with history reaching back to 2018, and all three
verdicts were re-checked and stand. Two traps are recorded there and are worth knowing:
a full-text search from a **new** account silently returns only messages since the account was
created unless the narrow includes a channel, so every negative must be validated against a
known-positive control first (`bench/experiments/zulip-search/`).

What is still not covered: **private streams** — so the phrase is "no public discussion", never
"no discussion" — and channels outside the four swept.

Where a document says *verified absent*, the current source was read and the thing demonstrably
is not there; where it says *nothing found*, that is a negative search result inheriting the
gaps above. One of the defects, the codegen one, turned out on inspection to be known
(lean4#14359), which is the honest measure of how much weight the phrase can carry.

> **Not yet propagated.** `release/README.md` §3 still describes the Zulip archive as frozen at
> 2026-02-28 with the live instance login-walled. That predates the redone search; see
> [`evidence-audit.md`](evidence-audit.md).
