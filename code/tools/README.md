# Two results that need no patched Lean

## 1. `leansnap.py` — the snapshot wrapper

Lean already has an experimental feature that saves the fully-loaded post-import state to
disk and reloads it: `lean --incr-header-save=F hdr.lean` writes it, `lean --incr-load=F
file.lean` reads it. On a small Linux server this takes a check from **5.8 s to 1.5 s**, with
an **unmodified** toolchain.

**Lean validates almost nothing about that snapshot**, and that is what this wrapper is for.
Measured failure modes of the raw feature:

| What you do | What Lean does |
|---|---|
| rebuild one dependency `.olean` and reload | **segfault** — the loader follows stale pointers, because a rebuilt olean lands at the same address with different bytes |
| truncate the snapshot | **segfault** |
| pass `-DautoImplicit=false` at load time | **silently ignored** — the options baked into the snapshot win, so the file elaborates differently from a plain run with no diagnostic |
| pass `-DmaxHeartbeats=1` at load time | silently ignored |
| use a file whose header is the same imports at a different byte offset | silently falls back to a full import, having already paid for mapping the snapshot |
| load a snapshot saved by a different build of the same version | clean error |

`leansnap.py` (486 lines, stdlib only) puts a lookup key and a manifest around it. The key is
a hash of the `lean --version` line, `LEAN_PATH`, the exact header text and the sorted `-D`
options — so a header or option mismatch is a *miss*, not a silent semantic difference. The
manifest records size, mtime, inode and Lake hash of all 52,490 dependency files at save time
and re-checks them before every load. Any miss or mismatch falls through to a plain `lean`
with identical arguments; stdout, stderr and exit code are passed through untouched.

```sh
python3 leansnap.py save --header hdr.lean            # build a snapshot for that header
python3 leansnap.py run  file.lean                    # use it if valid, else run lean plainly
python3 leansnap.py check file.lean                   # print the key, status and timings
```

Validation modes: `stat` (default, 0.12 s for 52k files), `lakehash` (0.9 s), `trace`
(≈ 0 s, transitive under Lake's discipline) and `none`.

**Costs and limits.** The snapshot for `import Mathlib` is 311 MB plus a 6.4 MB dependency
list and a 9 MB manifest; saving takes ~42 s on stock Lean, ~21 s on the fork, with a 7.4 GB
peak. It is **incompatible with the fork's lazy part loading** — with `LEAN_LAZY_PARTS=all` every
check through a snapshot fails; `LEAN_LAZY_PARTS=0` plus a snapshot is byte-identical to stock. And
on a `t4g.large` it is now *beaten* by forking a loaded process (0.25 s per check), which is
a Linux-only technique.

Equivalence: byte-identical stdout, stderr and exit code against a plain `lean` on all 15
textbook cases with `#print axioms` appended, on the constant-map suite, and on a
header-mismatch file that correctly took the plain path.

## 2. Compiled Mathlib tactics — a build recipe, not a patch

Mathlib's tactics ship uncompiled and run in Lean's bytecode interpreter, which the census
measured at **30 % of all elaboration CPU**. They ship that way because Mathlib's build cache
must work on every platform. Compiling them cuts elaboration CPU by 29–73 % depending on the
corpus, and the interpreter share falls 88–94 % everywhere (`aesop` 98 → 5 ms per call,
`linarith` 112 → 47 ms, `ring1` 18 → 8 ms). It needs **no change to Lean**, and it does not
invalidate a single `.olean`.

```sh
lake build batteries/Batteries:shared Qq:shared aesop/Aesop:shared \
           proofwidgets/ProofWidgets:shared plausible/Plausible:shared \
           importGraph/ImportGraph:shared LeanSearchClient:shared Mathlib:shared
```

That produces eight shared libraries (153 MB on macOS, ~152 MB on Linux) from the `.c` files
Lake had already emitted, in about 13.6 minutes on an M2 Pro. The delivery mechanism is a
three-line wrapper `lean` that prepends one `--load-dynlib=` per library, in dependency order.

Three things to know before you try it.

* **It needs the codegen fix to be safe.** Without
  `../patches/fix-codegen-meta-initialize.patch`, some files crash deterministically — an
  import-time segfault and an elaboration-time one inside a linter. This is upstream
  [lean4#14359](https://github.com/leanprover/lean4/issues/14359). With the fix, 50 runs of
  50 across ten configurations exit 0.
* **On Linux you must set `LD_LIBRARY_PATH`** or `--load-dynlib` fails outright — see
  [`docs/warnings.md`](../../docs/warnings.md) §2.
* **In the editor it changes nothing, in either direction.** Median edit latency 212 → 212 ms
  (that *is* the server's 200 ms debounce), `exact?` unchanged, peak worker RSS unchanged.
  An interactive session's cost is the header and the debounce, not tactic execution. Getting
  the libraries into the file *worker* also needed `LEAN_WORKER_PATH`; the documented
  `--load-dynlib` route does not reach the worker.

Whether to build and distribute compiled tactic code is a decision for the Mathlib
maintainers, not something this package can make. What it can say is what it costs and what
it buys: 153 MB per platform, 13.6 minutes on top of an unchanged olean cache, and roughly a
third of elaboration CPU on tactic-heavy files.
