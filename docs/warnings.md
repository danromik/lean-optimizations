# Warnings

> **Superseded, 2026-09-01.** This warning applied to the tactic index image (improvement 6) when
> it was a cache. It is no longer a cache: it writes nothing at run time, so a from-source build
> now writes zero image bytes (verified
> over 250 distinct import sets) and `LEAN_TACTIC_INDEX=0` is not required for one. The warning is retained
> below as the record of what the cache did, because it explains why the design changed. See
> [`improvements/06-tactic-index-image.md`](improvements/06-tactic-index-image.md). **The lazy-loading index directory is still a bounded
> cache**, so the disk-space advice still applies to it.


Read this before running anything in this package that rebuilds a library.

---

## 1. `LEAN_TACTIC_INDEX=0` is required for a from-source library build

**This is the most important line in the package.**

One of the six changes — the tactic index image (improvement 6, whose switch is named
`LEAN_TACTIC_INDEX` for historical reasons) — writes a compacted image of the loaded
tactic indexes — about 4.5 MB — for every distinct *import closure* it sees, into
`$HOME/.cache` by default. Nothing ever deletes one.

On the workloads it was designed for that is fine: a project's files share a handful of
import headers, so the number of distinct closures is small and the cache directory
converges. A cold build of a 1,373-file project produced 213 distinct keys.

**A library build from source is the opposite shape.** Every module has its own import
closure, so every build job is a fresh key. A Mathlib build presents **8,705 of them**. We
found this the way you would expect: a rebuild had to be killed after seven minutes with
4,331 files and **~21 GB** written, free disk falling about 2 GB per minute. The full build
would have written about 38 GB. Rerun with `LEAN_TACTIC_INDEX=0`, free space was flat for all 43
minutes of it.

So:

```sh
LEAN_TACTIC_INDEX=0 lake build          # any from-source build of Mathlib or a comparable library
```

This is **unfixed**. It is not a configuration mistake you can avoid by being careful; it is
a missing eviction policy. A bound would be cheap — a size cap with LRU eviction, or a rule
not to write an image for closures below some size, or a key that does not produce a fresh
entry per module — and none of them is written yet. The same absence of eviction applies to
the lazy-part index directory, which grows more slowly.

**No script in this package rebuilds a library.** `repro-import.py` and
`check-equivalence.sh` read an existing olean cache and write only their own side files
(about 500 MB for one import closure, under `--cache-dir`). If you write a script that does
rebuild one, set `LEAN_TACTIC_INDEX=0` in it.

---

## 2. On Linux, the compiled-tactic libraries need `LD_LIBRARY_PATH`

If you follow the compiled-Mathlib-tactics recipe ([`code/tools/README.md`](../code/tools/README.md) §2)
on Linux, `--load-dynlib` fails outright with

```
error: error loading library, libLake_shared.so: cannot open shared object file
```

because two of the eight shared libraries carry a `DT_NEEDED` on `libLake_shared.so` and
Lake's `:shared` link adds no `RUNPATH`. Set the loader path before loading anything:

```sh
export LD_LIBRARY_PATH="<toolchain>/lib/lean${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
```

macOS does not need this — the install name resolves it — which is exactly why it was missed
until the work was ported. Anyone shipping compiled tactic libraries on Linux has to pass
`-Wl,-rpath` at link time or set the loader path at run time.

---

## 3. Rebuilding Mathlib from source is expensive

Two of the six changes have a **library-side half**: the shell-first `.olean` layout and the
prebuilt `exact?` index only take effect in libraries compiled by the patched compiler. If
you want those, you need Mathlib rebuilt from source rather than fetched from the community
cache.

| | |
|---|---|
| Time | ~43 min on an M2 Pro (12 cores); ~50 min on a 16-vCPU x86-64 box; ~56 min in an arm64 container |
| Disk | ~11 GB for the rebuilt tree |
| Requires | `LEAN_TACTIC_INDEX=0` — see §1 |

Neither the headline measurement nor the equivalence suite in this package needs it: both
run against the stock olean cache, which the fork reads unmodified. The rebuild buys a
further −0.10 to −0.26 s and −0.08 to −0.22 GB on an 8 GB machine, and it lets the `exact?`
index be stored in the `.olean` files instead of in a 51 MB side file — which is the shape a
real release would prefer, because there is then nothing to prime, key or invalidate.

---

## 4. Never run a fork binary through `lake env`

`lake env` exports `DYLD_LIBRARY_PATH` (macOS) or `LD_LIBRARY_PATH` (Linux) pointing at the
**stock** toolchain's `lib/lean`. The `lean` executable is a small stub that loads
`libleanshared` through that path, so a fork `lean` run inside a `lake env` silently loads the
*stock* runtime library and produces stock numbers.

This cost us a measurement session: the first harness run of the address-reservation change
reported 10.35 s — a perfect stock number — with the fork binary in hand, for exactly this
reason. The scripts here therefore compute `LEAN_PATH` once and run the bare binary; they
never invoke `lake env` at measurement time. If you write your own A/B, do the same.

There is a related, more common version of this trap in [`building.md`](building.md) §2.3:
Lake uses the toolchain named in the project's `lean-toolchain` file unless you override it,
so you can believe you are testing the fork and be measuring stock Lean.

---

## 5. What the numbers mean

* **Max RSS counts shared, memory-mapped library pages.** The OS holds them once. Of a
  5.68 GB resident post-import process, only ~0.86 GB is genuinely private. "N workers use
  N × 5.3 GB" is wrong.
* **On Linux, max RSS also depends on the machine's free memory** (fault-around maps up to
  64 KB of cached neighbours per fault). Quote RSS at the memory size of the machine you care
  about; a 32 GB box and an 8 GB box measuring the same binary can disagree by a factor that
  changes the *sign* of a comparison.
* **Minor-fault counts are kernel-policy quantities.** Only the ratio between configurations
  on one machine means anything.
* **Cold-cache seconds measured in a Docker VM on macOS are not trustworthy**: `drop_caches`
  clears the guest's page cache, but the host's cache of the VM disk image survives and is
  warmer for one tree than for another. Fault *counts* transfer; seconds do not. Cold seconds
  in the manuscript come from real hardware.
* **`lean --profile` phase times are Lean's own accounting** (thread time summed across
  elaboration tasks), not wall time; their sum can exceed wall time and excludes process
  start-up.

---

## 6. Two changes are a regression on their own

Lazy part loading, on its own, makes the first `exact?` of a process *slower than stock* —
22.2 s against 15.3 s end to end — because the library-search walk then has to fault in
nearly every module the change had avoided loading. The prebuilt index removes the walk and
the tactic-index image shrinks the heap it walks; only with all three does that case become
3.73 s. A beginner's editor session against the fork-before-those-two took 27.5 s of waiting
against stock Lean's 23.7 s: worse, not better; with them, 11.3 s.

If you switch pieces off one at a time (see [`switches.md`](switches.md)), expect
combinations that are worse than either endpoint. That is a real property of the work, not a
measurement error.
