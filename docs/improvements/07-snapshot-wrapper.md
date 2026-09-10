# The snapshot wrapper script (`leansnap.py`)

_Not a change to Lean. A 486-line stdlib-only Python 3.8+ script,
[`code/tools/leansnap.py`](../../code/tools/leansnap.py), that puts a lookup key and a validity manifest
around Lean's own experimental `--incr-header-save` / `--incr-load`. Developed against Lean
`v4.33.1`; the flags exist from v4.32. Works with a completely unmodified toolchain._

---

## 1. The problem

### 1.1 What the flags do

`lean --incr-header-save=F hdr.lean` runs the import of `hdr.lean` and writes the post-import
state — the `HeaderProcessedSnapshot`: the whole `Environment` with both constant maps and all 361
extension states, the `Options` in effect for the header, `mainModule`, the header `Syntax` with
source positions, the header's message log, and the indices of imported modules carrying `[init]`
declarations — as **one compacted region**. For `import Mathlib` that is 311 MB, plus a 6.4 MB
JSON sidecar `F.deps` listing the absolute paths of the 52,490 dependency region files.

The snapshot's pointers refer **by address** into those dependency files. Closures are allowed
(the extension states hold `addImportedFn` results and parser tables), and their function pointers
are recorded in a relocation table keyed by the loaded runtime library's identity and base
address.

`lean --incr-load=F file.lean` re-maps every dependency file at its `base_addr` on up to four
worker threads, maps `F`, re-runs the `[init]` declarations of the recorded modules, and continues
with `file.lean`'s commands — **skipping `finalizeImport`, every `addImportedFn`, and every
initializer's environment effect.** The snapshot is used only if `parseHeader` finds the new
file's header syntax equal to the saved one under `Syntax.eqWithInfo`, a structural comparison
that **includes source positions**.

### 1.2 What Lean validates: essentially nothing

Measured on a 7-module toy project on the stock toolchain, so that `.olean`s could be rebuilt,
removed and moved freely:

| scenario | what happens | class |
|---|---|---|
| same header, different file name, `private def`s | identical output, exit 0 | ok |
| the file imports a *subset* of the saved header | correct output; **silent full import** (snapshot discarded after being mapped) | silent slow path |
| same imports, preceded by a comment line (positions shift) | correct output; silent full import | silent slow path |
| **`-DautoImplicit=false` on the load command line** | **ignored.** The file with an auto-bound `n` elaborates and exits 0; a plain run gives 2 errors and exits 1 | **silent semantic difference** |
| `-DmaxHeartbeats=1` at load time | ignored (plain run: 3 timeouts) | silent semantic difference |
| `set_option autoImplicit false in` *inside* the file | honoured (command options are not part of the header) | ok |
| the option given at **save** time | applies at load, with or without the flag | confirms: options are a property of the snapshot |
| `LEAN_PATH` unset at load | works (deps are absolute paths) | ok, but the fallback needs it |
| saved by one build, loaded by another of the same version and githash | `library required for closure relocation is not loaded in this process`, exit 1 | clean error |
| snapshot header githash altered | `incompatible header`, exit 1 | clean error |
| snapshot truncated to 100 kB | **segfault**, exit 139 | crash |
| `F.deps` missing | clean error | clean error |
| **one dependency module rebuilt with a changed constant** | **segfault**, exit 139 (plain run prints the new value) | **crash, stale pointers** |
| a base module grew and every dependant was rebuilt | segfault, exit 139 | crash |
| a dependency `.olean` deleted / the project moved | clean error (paths are absolute) | clean error |

The last two crash classes are the point. A rebuilt `.olean` has the **same** `base_addr` — it is
a hash of the module name — with different bytes, and the loader follows stale pointers. Segfault
in every case that could be constructed; silent garbage is possible in principle.

So the loader is only safe when the caller guarantees that (a) the dependency files are
byte-identical to save time, (b) the header text is byte-identical *and at the same offset*, (c)
the load-time options are the save-time ones, and (d) the binary and its runtime library are the
ones that saved. (a) and (c) fail silently or crash; (b) fails silently into a slow path that has
already paid for a 300 MB mapping.

This is why the upstream proposal to put validation in core was rejected as belonging in "a layer
on top". This script is that layer.

## 2. What the script does

### 2.1 The lookup key

Computed from the request alone, before anything is loaded:

```
sha256( lean --version line,      # version, platform triple, githash, build type
        LEAN_PATH,
        the exact header text of the file,
        the sorted -D options,
        BINARY IDENTITY,
        CONFIGURATION SWITCHES )[:24]
```

`header_text(src)` takes the maximal prefix of `module`/`prelude`/`import` lines — blank and `--`
lines between them included, since they shift positions — and strips trailing whitespace. The
snapshot is saved from a temporary file containing **exactly** that text, so any file starting
with the same bytes hits the fast path and anything else is a *miss*, never a wrong load.

`binary_identity(cfg)` is the resolved path, size and a digest of the `lean` executable **and** of
the `libleanshared` it loads (`runtime_lib` finds it via `lean --print-prefix`). `--binid` selects
`quick` (first and last 64 KiB, the default), `full` or `path`. It is in the key because the
`--version` line does not distinguish a fork build from stock — every fork branch of this project
reports the same line — so without it a stock and a fork snapshot of the same header would share
one filename and silently overwrite each other.

`config_env(cfg)` adds the set `LEAN_*` variables in `CONFIG_ENV_VARS` that change *what* is
imported. Unset variables contribute nothing, so a plain stock invocation keys exactly as it did
before that was added. Extendable with `LEANSNAP_ENV=NAME1,NAME2`.

### 2.2 The manifest

Written at save time by `build_manifest`, next to the snapshot as `<key>.manifest.json`:

```
{ lean_version, lean_path, header, options, binary_identity, config_env,
  runtime_lib: [realpath, size, mtime_ns, ino],
  files:  { path: [size, mtime_ns, ino, lake_hash|null] }   # every dep region file
  traces: { direct import: depHash from <Mod>.trace } }
```

`check_manifest` re-checks it before every load, in one of four modes (`--check` /
`LEANSNAP_CHECK`), all preceded by the version-line and runtime-library identity checks:

| mode | what is compared | cost, 52,490 files, warm | sound against |
|---|---|---|---|
| `stat` (default) | size + `mtime_ns` + inode of every dependency file | **0.12 s** | any rebuild, `cache get`, `cp`, package upgrade — but not an in-place overwrite preserving size and mtime |
| `lakehash` | Lake's `<file>.hash` sidecar, falling back to `stat` for files without one | 0.9 s warm (6 s cold) | the above plus in-place edits, *provided* Lake maintains the sidecars |
| `trace` | the `depHash` in `<Mod>.trace` of each **direct** import (Lake's transitive hash of sources, options and import traces) | ≈ 0 s | everything Lake tracks, if the tree is Lake-managed and `.trace` files are kept |
| `none` | nothing beyond version and runtime library | 0 | what bare `--incr-load` gives |

Content-hashing the 7.5 GB of `.olean`s was rejected: ~4 s at NVMe speed per check, more than the
import it replaces. The upstream objection that "hashing all inputs completely negates any
snapshot benefits" is right for content hashes and wrong for stats.

### 2.3 The commands

```
leansnap save --header hdr.lean [--out DIR] [-- -Dopt=val ...]
leansnap run  file.lean [-- lean args...]
leansnap check file.lean          # {"key":…, "snapshot":…, "reason":"stat ok (52490 files)", "check_ms":…}
leansnap key  file.lean
```

`cmd_save` runs `lean --incr-header-save` on the canonical header file, builds the manifest from
the produced `.deps`, and installs all three files with `os.replace`. `find_snapshot` computes
the key, checks that all three files exist, and runs the manifest check. `cmd_run` is a thin
wrapper: it execs `lean [--incr-load=F] <args> file` with the caller's environment, streams its
stdio and returns its exit code.

## 3. Why it is correct

**The key covers every one of the four guarantees §1.2 requires.** (a) is the manifest's job;
(b) is the header text, byte for byte, and the snapshot is saved from exactly those bytes;
(c) the `-D` options are in the key and are passed to the save run, so the baked-in options are by
construction the requested ones; (d) is the version line plus the binary identity plus the runtime
library, checked in both the key and the manifest.

**Every failure is a fallback, not an error.** A miss, a failed manifest check, a missing header
— all lead to `lean` with the same arguments and no `--incr-load`. Stdout, stderr and the exit
code are passed through untouched in both cases. So the equivalence of a `leansnap run` is the
equivalence of `--incr-load` itself, and its worst case is the cost of the checks.

**Belt and braces on the identity.** The binary identity and the configuration switches are in
the key *and* re-checked in the manifest, so even a hash collision or a hand-copied snapshot
directory falls back to a plain run rather than handing a snapshot to a binary that cannot use
it.

**The residual hole in `stat` mode** is a same-size, same-mtime, same-inode in-place overwrite.
Nothing in a normal Lake or `cache get` workflow does that; `lakehash` and `trace` close it at
0.9 s and ≈ 0 s respectively.

## 4. How it was verified, and what that does not cover

**macOS.** `leansnap run` versus plain `lean`, for both the stock and the fork toolchain: 15
textbook files with `#print axioms` appended for every theorem, the constant-map suite (174 lines)
and a header-mismatch file — **stdout, stderr (minus the layer's own `leansnap:` line) and exit
code byte-identical in all 34 pairs.** The 32 matching-header cases used the snapshot; the 2
mismatch cases took the plain path.

**Stale-dependency drill.** After `touch` on `Mathlib/Logic/Basic.olean` the `stat` check fails in
54 ms and the run falls back with identical stdout; `lakehash` and `trace` correctly still accept
the snapshot, because the content did not change.

**Linux, both architectures**, with the **stock** toolchain on real servers: 34/34 equivalent on
`t4g.large` (arm64, two binaries) and 17/17 on `c7i.4xlarge` (x86-64, one binary). The `.snap`
files came out the same size to the byte on every machine tried.

**What this does not cover.**

* **The initializer re-run is an assumption, not a proof.** `--incr-load` re-runs `[init]`
  declarations for their global side effects, on the assumption that their *environment* effects
  are already in the snapshot (the Lean source calls this "the central incr HACK"). A Mathlib
  initializer doing I/O or holding time-dependent state would diverge. The equivalence suite (16
  files, all tactic families) shows none, but that is evidence, not a proof.
* **The REPL is not covered.** `leanprover-community/repl` builds every fresh environment through
  `Lean.Elab.processHeader` → `importModules`; `--incr-load` is a `runFrontend` feature and does
  not apply. §6.3.
* **A second snapshot, or a second import, in the same process was never tested.** The frontend
  loads at most one snapshot per process and the language server never uses the flag.
* **Windows was not tried.**
* **The 486-line script has no unit tests.** It is verified end to end, by the equivalence runs
  above.

## 5. Measured effect

**macOS** (quiet machine, warm cache, fastest of 3; `plain` = `lean file`; `leansnap` = the whole
wrapper timed, RSS = max over the process tree; `rawload` = `lean --incr-load` with no wrapper):

| case | binary | mode | wall s | user s | max RSS GB | page reclaims |
|---|---|---|---|---|---|---|
| `import Mathlib` | stock | plain | 10.58 | 1.66 | 5.68 | 416,589 |
| | stock | **leansnap** | **9.04** | 0.63 | **1.76** | 135,616 |
| | stock | rawload | 8.78 | 0.50 | 1.76 | 140,743 |
| | fork | plain | 3.54 | 1.66 | 3.44 | 286,071 |
| | fork | **leansnap** | **1.77** | 0.67 | **1.76** | 134,272 |
| | fork | rawload | 1.41 | 0.53 | 1.76 | 140,425 |
| `module` root | stock → **leansnap** | | 6.05 → **5.07** | 1.47 → 0.53 | 3.26 → **1.44** | |
| | fork → **leansnap** | | 2.92 → **1.47** | 1.46 → 0.54 | 2.54 → **1.44** | |
| four textbook files | stock | plain → leansnap | 10.6–10.8 → **9.1–9.3** | | 5.7 → **1.8** | |
| | fork | plain → leansnap | 3.6–3.8 → **1.7–2.1** | | 3.5 → **1.8** | |

The snapshot removes ~1.0–1.1 s of user time (`finalizeImport`, the `addImportedFn`s, the
initializer effects) and ~280k page touches, **regardless of the binary**. What it cannot remove
is the mapping of the 52k dependency files — and on stock macOS that mapping *is* the import
(9.5 s of system time), which is why the stock wall gain is only 1.5 s. With the fork's address
reservation the mapping costs 1.5 s and the load lands at 1.4 s raw / 1.8 s through the wrapper.

**Save cost:** 311.5 MB `.snap` + 6.4 MB `.deps` + 9.0 MB manifest, in 42 s on stock / 21 s on the
fork, 7.4 GB peak. The manifest adds 5–9 s, most of it the cold `.hash` sidecars.

**Throughput.** Ten sequential checks of ten different textbook files: stock 120.5 → **94.0 s**;
fork 41.0 → **19.0 s** (4.1 → **1.9 s** per check).

**Linux, stock toolchain, real servers** — this is the configuration the script is actually for:

| machine | per check, warm | per check, cold | ten in a row | equivalence |
|---|---|---|---|---|
| `t4g.large` (2 vCPU, arm64) | **5.58 → 1.46 s** (3.8×) | 104 → 34 s | 6.15 → 1.9 s each | 34/34 |
| `c7i.4xlarge` (x86-64) | **2.87 → 0.71 s** (4.0×) | 109 → 25 s | 3.04 → 0.82 s each | 17/17 |

A `module` root gains 4.1–5.1×. **A snapshot check fits in a 2 GB container limit with room** —
every check completes at full speed with zero major faults inside the limit, and the cgroup's
`memory.peak` for a container that ran twelve of them is 612 MiB; a *plain* check does not fit at
all and thrashes. (The `max RSS` figures on a machine with a large idle page cache are inflated by
Linux's fault-around; with `fault_around_bytes=4096` the same x86 runs read 3.23 GB plain →
**0.49 GB** through the snapshot.)

One unexpected result: **cold, the 52,490-file `stat` pass is cheaper than no layer at all**
(22,493 major faults against raw `--incr-load`'s 27,645), because it pulls the inodes and
directory entries in one sequential pass instead of 52,490 separate EBS round trips.

**The miss path is what the key exists to prevent.** Raw `--incr-load` of an `import Mathlib`
snapshot for a file whose header is `import Mathlib.Tactic` — it maps 52k deps and 311 MB,
discards them, then imports 4.5k modules — costs **21.5 s** on stock, against 3.34 s for a plain
run.

## 6. What is unfinished, provisional or known to be wrong

### 6.1 It is incompatible with the fork's lazy part loading

Measured on Linux: with `LEAN_LAZY_PARTS=all`, **every** check through a snapshot fails. `LEAN_LAZY_PARTS=0` plus
a snapshot is byte-identical to stock. The cause is structural — lazily loaded regions are not in
`header.regions`, which is what a snapshot is saved against. The two features cannot be combined
as they stand.

### 6.2 The wrapper's own overhead is 0.25–0.35 s, and it is the dominant share on a fast machine

Python start-up (~50 ms), two `lean` subprocesses for `--version` and `--print-prefix` (~40 ms),
parsing the 9 MB manifest (~70 ms), and 52k `stat`s (0.12 s). On the c7i that is **42 % of a
0.71 s check**: the check got 4× faster, the wrapper only 2×. All of it is removable — cache the
two subprocess results keyed on the binary's stat, store the manifest as relative paths in a
compact binary form (9 MB JSON → < 2 MB), or port the script to Lean or C and exec `lean`
directly. In an immutable container image the `stat` check could be skipped entirely
(`--check none`); the key alone still covers toolchain, header and options.

### 6.3 The REPL needs about 60 lines that were not written

`REPL/Frontend.lean`'s `processInput` with no prior state runs `Parser.parseHeader` →
`Lean.Elab.processHeader` → `importModules`, so every command without an `env` index pays a full
import. Making it snapshot-aware means: compute the key for the command's header text; look up
and validate; load with a copy of `loadIncrSnapshot` (it is `private` in `Lean.Elab.Frontend`, but
the snapshot structure can be re-declared with the same shape since `CompactedRegion.read` is
untyped); `setMainModule`, `runInitAttrsForModules`, `enableInitializersExecution`; and take the
loaded `cmdState` as the header-only state. It is `unsafe`, it was not implemented, and a
long-lived REPL raises the untested "second snapshot in one process" question of §4.

### 6.4 Four small fixes belong upstream, and would turn crashes into errors

1. Record `(size, hash)` per dependency in `.deps` and verify at least the size — turns the
   stale-`.olean` segfault into an error.
2. Validate the snapshot region's `data_size`/trailer before reading — turns the truncation
   segfault into an error.
3. Either apply command-line `-D` options on the reuse path, or refuse `--incr-load` when they
   differ from the saved ones. **This is the worst finding of the four**: a silent semantic
   difference.
4. Compare the header modulo source positions, or document that it must sit at offset 0.

### 6.5 The header must be byte-identical, which is a real constraint

Every distinct import set is a different key and therefore a full-cost miss. A pipeline should
pre-save the small set of headers it expects (`import Mathlib`; `import Lean\nimport Mathlib`;
`module` + `public import Mathlib`) at image-build time, and must never let a miss fall into raw
`--incr-load` (§5).

### 6.6 A stale variable list in the shipped script

`CONFIG_ENV_VARS` still names `LEAN_TACTIC_INDEX_CACHE_DIR` and `LEAN_TACTIC_INDEX_EXTS`, which the fork's
tactic-index redesign removed, and does **not** name their replacements `LEAN_TACTIC_INDEX_DIR` and
`LEAN_TACTIC_INDEX_WRITE`. In practice the consequence is benign — the tactic-index image installs a state
that is equal by construction to the one the fold produces, so two snapshots differing only in
where the image was read from should have equal contents — but the list is meant to be the
authoritative set of switches that change what is imported, and it is out of date. Add the two
names, or pass them through `LEANSNAP_ENV`.

Two smaller documentation mismatches: the script is **486 lines**, not the 340 that
[`code/tools/README.md`](../../code/tools/README.md) used to quote (the binary-identity and
configuration-switch parts of the key were added later), and the snapshot sizes differ between configurations
(310.1 MB with 52,490 deps under `LEAN_LAZY_PARTS=0`, 400.7 MB with 10,498 deps under `LEAN_LAZY_PARTS=all`) in a
way the older documents do not reflect.

### 6.7 One more failure mode, not the wrapper's

The snapshot region's own `base_addr` is a hash of the fixed name `_snap`, the same for every
snapshot. Two snapshots therefore cannot be mapped in one process, and an ASLR collision forces a
311 MB relocation walk. Not observed in ~150 loads here; the fork's address reservation also
protects the address on macOS. Upstream
[lean4#14563](https://github.com/leanprover/lean4/pull/14563) is the relevant change.
