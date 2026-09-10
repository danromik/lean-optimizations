# Fork safety, and a partial fix

_Patch: [`../../patches/fix-fork-safety.patch`](../../patches/fix-fork-safety.patch), **one file,
+61 / −0**, against Lean `v4.33.1` (`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`)._

> **This patch is the exception to the package's independence.** It edits
> `src/runtime/object.cpp`, which the performance fork also edits. The two sets of edits do not
> overlap, so `patch -p1` applies this on top of the fork; `git apply` will not, because the
> patch's context lines come from the unpatched file.
> `../../code/scripts/check-patches.sh` reports exactly that.

## 1. The question, and why it was open

`fork` duplicates a running process, and the copy shares the original's memory for free: nothing is
physically copied until one of the two writes to it. So a Lean process that has finished loading
Mathlib could in principle be duplicated per file to check, paying the import once rather than once
per file.

Whether that is *safe* had been asked and never answered. Two issues in the `repl` repository ask
it: [#87](https://github.com/leanprover-community/repl/issues/87), open since 2025-04-18 with one
speculative comment and no maintainer reply, and
[#84](https://github.com/leanprover-community/repl/issues/84), where it is raised three times
across ten comments and answered none. That second thread also contains a flat contradiction, one
participant saying a Lean process is single-threaded and another that threads appear as it runs.

## 2. The answer: not safe, and the failure is deterministic

A forked child of a Lean process that has run any standard-priority task **hangs, every time**.

The cause is Lean's pool of worker threads (`task_manager`, `src/runtime/object.cpp:758`), which is
kept together with a count of how many workers are idle. `fork` copies the count but not the
threads, so the child believes workers are waiting when none exist; it dispatches work, signals
them, and waits for a reply that cannot come. The result is a silent hang rather than a crash,
which is the worse outcome, since nothing indicates what happened.

Deterministic is better news than a race: it can be diagnosed and repaired rather than merely made
rarer.

**See it for yourself.** [`../../code/scripts/demo-fork-hang.sh`](../../code/scripts/demo-fork-hang.sh)
builds [`forktest.c`](../../code/scripts/forktest.c) — 53 lines against `lean/lean.h`, needing no
Mathlib and no patched Lean — and runs it three ways: pool warmed then fork (hangs), pool never
used then fork (completes), pool warmed but the child asking for its own thread (completes). The
last two are what make this a diagnosis rather than an anecdote; they place the cause in the
inherited idle-worker count and nowhere else. About fifteen seconds.

**The single-threaded claim is false.** Measured: 8 threads after import on a 12-core machine, 4 at
Lean's lowest thread setting, and at least 2 before any Lean code runs. There is no setting below
that one — `lean -j0` does not run single-threaded, it stops with an internal panic. The same
belief is built into a published system: the Kimina Lean Server runs one process per core on the
stated grounds that "a single Lean REPL process is single-threaded, and its CPU usage does not
exceed one core" (arXiv:2504.21230, §3).

**Some work can nonetheless be done in a forked child of unmodified Lean.** Work marked as
requiring its own dedicated thread bypasses the broken pool and runs correctly (priority 9
succeeds; 0 and 8 hang). Ordinary proof checking does not use that path, but the language server
does, by policy (`src/Lean/Server/ServerTask.lean:44-70`).

## 3. What the patch does

It adds one exported function, `lean_reinit_task_manager_after_fork`, placed beside the task
manager's own initializer, which the child calls once before it runs any Lean code. The function:

1. lets go of the worker handles the child inherited, without trying to join threads that no longer
   exist (the `lthread::imp` control blocks leak, a few hundred bytes);
2. sets the idle and dedicated counts back to zero;
3. rebuilds the mutex and the three condition variables in place, in case the fork landed while
   another thread held one of them — safe precisely because no other thread exists in the child;
4. starts a worker if the parent left queued work behind.

Step 4 is what distinguishes it from the shortcut of simply re-initialising the manager: with three
tasks queued but not started at the fork instant, this patch completes them and the shortcut hangs
forever.

## 4. What was verified

With the fix, a forked child of a Mathlib-loaded process elaborated Lean correctly and
byte-identically **40 times out of 40**, every one of those forks taken while four of the parent's
threads were mid-elaboration. Also 10/10 from a quiesced parent, and 5/5 sequential children from
one parent at about 430 ms each, with the parent healthy afterwards.

That is a better result than expected, and it should be read with care: it means the window in
which a fork does damage is narrow, not that it is absent. The conditions in §5 are what keep a
child clear of it.

**All the failures produced are hangs and aborts rather than wrong answers.** The one case that
could have yielded silently incorrect output — a child reading an object graph other threads were
mutating at the fork instant — instead fails loudly, 12 times out of 12, with an assertion
violation. Against a quiesced parent: 6/6 clean.

## 5. The conditions a caller must meet

The patch removes the one obstacle no amount of care on the caller's part could work around. The
rest is the caller's responsibility, which is why this is a partial fix.

1. **The forking program must be a custom host binary**, not the `lean` shell: a program that links
   Lean's shared library and calls into it directly, so it can choose the moment to fork and can
   carry out the child's repairs. The `lean` executable provides no way of doing either.
2. **The parent must be quiescent at the instant of the fork.** With another thread mutating a
   shared reference the child hangs 10/10; with another thread mutating a Lean object graph the
   child aborts 12/12; a quiesced parent is clean in both.
3. **In the child, before any Lean code runs**: call `lean_reinit_task_manager_after_fork`, reseed
   the random number generator, and take fresh file descriptors if it performs input or output,
   since parent and child otherwise share one file offset.
4. **For its whole life the child must stay out of Lean's asynchronous input and output layer**,
   which aborts the process as soon as anything reaches it.
5. **The child must exit by calling `_Exit`** — Lean exposes it as `IO.Process.forceExit` — rather
   than by returning from `main`, which leaves it hanging indefinitely at no processor usage.

Several of these fail silently, which is what makes them a recipe rather than a set of
suggestions.

A child may also extend the environment it inherited — one 3,181-module closure went to 3,305 with
byte-identical results — provided it calls `enableInitializersExecution` before importing. That
was measured on an unmodified toolchain with an existing runtime call standing in for this one, so
it is a property of `fork` and of Lean's import machinery rather than anything this patch provides.

## 6. Is it worth doing at all?

On **Linux**, yes. `fork` of a Mathlib-loaded process costs 20–47 ms against a 2.4–5.9 s import,
and the child costs 8–18 MB of private dirty memory against ~470–500 MB for an independent
process. On an AWS `t4g.large`, a single check goes from 5.9 s to **0.25 s**.

On **macOS**, no. The same `fork` of a process holding all of Mathlib costs **3.2–7.5 s**, which
makes it pointless as an optimisation. That is macOS's `vm_map_fork` copying the memory map, and it
is why this improvement is Linux-only in practice.

## 7. What is unfinished, provisional or known to be wrong

### 7.1 The evidence is bounded, and here is the boundary

Of the failure modes identified by reading the runtime, **three resisted every attempt to provoke
them** (mimalloc's delayed-free spin, the loader lock under `dlsym`, a child spawning its own
threads), **one could not be tested at all** with the equipment available (`lean_obj_once_cold`'s
futex window), and **six were never exercised** (mimalloc's arena-abandon mutex, Mathlib's
`Std.Mutex` and `DeclCache`, the compacted-region rule, the profiler global, and `fork` failing
under strict overcommit). None can be ruled out. What is demonstrated is safe *in every case we
ran*, not safe in general.

Behaviour under memory allocators other than the one Lean ships with is untested.

### 7.2 Upstream may make this unnecessary

[lean4#13123](https://github.com/leanprover/lean4/pull/13123) retires idle worker threads after
five seconds, which empties the pool a child would otherwise inherit. We backported it and
confirmed that waiting until idle and then forking works **with no patch at all**, 5 times out of
5, against a control that hung. So that change would render this one unnecessary for the standard
pool — though not for a parent with a dedicated worker still running, and not for any of the
conditions in §5.

It was merged on 12 June 2026 and reverted three days later. A re-landing commit exists but sits on
a branch that was never merged: the code is absent from `v4.32.0`, `v4.33.0`, `v4.33.1`,
`v4.34.0-rc2` and the current development branch, verified against the source. The Lean developers
should weigh this patch against that one.
