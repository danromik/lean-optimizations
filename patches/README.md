# The patches

All five are diffs against **Lean v4.33.1**, commit
`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`. `../scripts/check-patches.sh --lean4 <checkout>`
verifies every one of them applies to a clean export of that tag, that each of the three
fixes applies on top of the fork, and that the fork and those three apply together — a few seconds, nothing
built.

```sh
git clone https://github.com/leanprover/lean4 && cd lean4 && git checkout v4.33.1
git apply --binary /path/to/patches/<name>.patch
```

Only the fork patch carries a binary hunk (`stage0/src/runtime/object.cpp`, a generated file
git treats as binary). `--binary` is a no-op on current git, which applies binary hunks
unconditionally; it is what older versions needed, and it is harmless everywhere.

---

## `lean4-v4.33.1-optimized.patch` — the fork (macOS + Linux source, macOS build)

16 files, **+2,119 / −82**. Six changes to how Lean loads its library:

1. reserve the library's address range in one go, so macOS stops walking a sorted free-list
   for each of 52,490 mappings — and switch process spawning from `fork` to `posix_spawn`,
   which the reservation would otherwise have made expensive (and which ends up faster than
   stock, because `fork` copies the memory map of a process holding all of Mathlib);
2. write the record "shells" together at the front of each `.olean` part;
3. do not read the reference counter of records that live in mapped files;
4. load private parts and compiled code on first demand;
5. build the `exact?` index from stored per-module entries instead of walking every
   definition;
6. store the 93 compactable tactic indexes in one mappable image per import closure instead
   of rebuilding them at every start.

**It contains nothing that changes Lean's observable behaviour.** That is the whole point of
the boundary: same language, same proofs accepted, same error messages, same tactic
suggestions in the same order. `../scripts/check-equivalence.sh` is the check.

Build instructions and the two required `cmake` flags: [`docs/building.md`](../docs/building.md).
Runtime switches: [`docs/switches.md`](../docs/switches.md).

## `lean4-v4.33.1-optimized-linux.patch` — the same fork for Linux

16 files, **+2,150 / −82**. The same six changes with the macOS address reservation replaced by a per-region range
registration. Reserving a range on Linux would double the VMA count against
`vm.max_map_count`, and Linux does not need it: its free-address list is a balanced tree, not
a sorted list walked from the front — which is presumably why the Lean developers, who
benchmark on Linux, never saw the problem the reservation solves.

---

## The three changes that ship separately, and this is deliberate

Each of these **changes Lean's behaviour** — for the better. That is exactly why none of them
is in the fork. A performance fork whose claim is "observably identical" cannot contain a
behaviour change without making its own central claim untestable, and someone who wants one
of these fixes should not have to take a performance fork with it.

Two of them are independent of each other and of the fork, and the reason is structural
rather than lucky: the fork touches 16 files, and those two touch four further files that the
fork does not touch. `fix-fork-safety.patch` is the exception: it edits `src/runtime/object.cpp`,
which the fork edits too. `check-patches.sh` shows all of it — each patch on a clean tree, the
two on top of the fork, and fork-safety on top of the fork with `patch -p1`.

**But they are not equally useful to you, and it is worth saying which is which.**

* `fix-codegen-meta-initialize.patch` and `fix-server-config-watch.patch` affect **unmodified
  Lean as you use it today** — the first if you enable Lake's `precompileModules`, the second
  whenever you edit a lakefile with files open.
* `fix-fork-safety.patch` is a **partial fix**, and useful only if you are building something
  that forks a Lean process after it has loaded a library. It removes the one obstacle a caller
  cannot work around and leaves five conditions the caller must meet.

### `fix-codegen-meta-initialize.patch` — a known upstream defect, with a fix

**+9 / −0 lines** (five of them comment), one file:
`src/Lean/Compiler/LCNF/EmitC.lean`.

For a module-system module `M`, the C emitter writes `meta_initialize_<M>` so that it calls
the *imports'* runtime initializers but never `M`'s own. Compiled meta code can then reach
globals of `M` that were never assigned, and the process dies. The fix emits
`runtime_initialize_<M>(builtin)` at the head of `meta_initialize_<M>`; it is idempotent,
because that function guards on its own flag.

**This is already an open issue upstream: [lean4#14359](https://github.com/leanprover/lean4/issues/14359),
labelled `bug` and `P-high`, filed 2026-07-10, still open with no fix PR.** We did not
discover it; we hit it, and this patch should be read as a contribution to that issue, not as
a finding. What it adds to what the issue already has:

* **a manifestation that is not a specialization.** The issue's title, body and proposed fix
  are all about a compiler-generated specialization. One of our two crashes is an ordinary
  private `def` whose closed-`Nat` globals are assigned only by its module's runtime
  initializer, in a linter that is *registered* by the comptime initializer and then runs at
  elaboration time, long after import. That changes what a correct fix has to cover.
* **a stock-toolchain reproducer**: three lines (`module` / `public import Mathlib` / one
  theorem) with stock Mathlib, exit 139 on 5 runs of 5, on macOS/arm64 and Linux/arm64. The
  issue's reproducer is a purpose-built three-module project.
* **a fix, built and exercised.** A whole-Mathlib rebuild through it: 8,303 of 8,312 emitted
  `.c` files differ from the stock-emitted ones only by the inserted call (8,312 of 8,312 on
  Linux, where the comparison is clean); 50 runs of 50 across ten crash configurations exit 0;
  the native-vs-interpreted equivalence suite goes 21/22 → **22/22**; the eight shared
  libraries grow +0.28 %.

The issue also carries a different, AI-proposed patch relayed with an explicit disclaimer from
the person who filed it; it is not the same change.

### `fix-fork-safety.patch` — a forked child of a Lean process hangs

**+61 / −0 lines**, one file: `src/runtime/object.cpp`.

A `fork`ed child of a Lean process that has run any standard-priority task hangs, every time.
Lean's pool of worker threads is kept together with a count of how many are idle; `fork` copies
the count but not the threads, so the child dispatches work, signals workers that do not exist,
and waits for a reply that cannot come. A silent hang, which is worse than a crash.

The patch adds one exported function, `lean_reinit_task_manager_after_fork`, for the child to
call before it runs any Lean code: it resets the state that describes threads and keeps the work
the parent had already queued. That removes the one obstacle no care on the caller's part could
work around. Five conditions remain, and they are the caller's to meet;
[`docs/improvements/10-fork-safety.md`](../docs/improvements/10-fork-safety.md) lists them.
[`code/scripts/demo-fork-hang.sh`](../code/scripts/demo-fork-hang.sh) reproduces the hang against
unmodified Lean in about fifteen seconds.

> **The exception to the package's independence.** The fork edits this file too. The edits do not
> overlap, so `patch -p1` applies this on top of the fork; `git apply` will not, because the
> patch's context lines come from the unpatched file.

### `fix-server-config-watch.patch` — the server does not watch the build configuration

**+54 / −8 lines**, three files: `src/Lean/Data/Lsp/Internal.lean`,
`src/Lean/Server/FileWorker.lean`, `src/Lean/Server/Watchdog.lean`.

The watchdog registers file watchers for `**/*.lean` and `**/*.ilean` and nothing else. A
change to `lakefile.toml`, `lakefile.lean` or `lake-manifest.json` — the files that decide
which linters run, whether `autoImplicit` is on, whether `sorry` warns, which dependency
versions are used — never reaches a running server. Observed on a real project: with two files
open and `warn.sorry` flipped from `false` to `true`, the file you restart shows six
`declaration uses 'sorry'` warnings and the file you do not shows none. Same project, same
moment, two behaviours, no diagnostic.

The fix uses the mechanism already there: three more globs in the capability registration, and
a branch that sends the existing stale-dependency notification to every open worker.
`lean-toolchain` is deliberately **not** watched — a file restart cannot pick up a toolchain
change, so the diagnostic's advice would be false; that needs a server restart, which is a
client-side action.

**One honest qualification.** The VS Code extension (`vscode-lean4`) already watches these
files client-side and offers to restart the server. So VS Code users are not entirely
unwarned, and the correct claim is narrower than "nothing tells you" — but the gap is total
for every other client of `lake serve`, and the server-side report is the one nobody has
filed. **No public report found** in either tracker; `Watchdog.lean` on the current
development branch is byte-identical to v4.33.1.
