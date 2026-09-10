# Compiled Mathlib tactics

_No patch. This improvement changes neither Lean nor Mathlib: it is a way of building Mathlib that
uses only what a Mathlib checkout already contains, plus a wrapper script,
[`../../code/tools/lean-native-wrapper.sh`](../../code/tools/lean-native-wrapper.sh), to put the
result in front of `lean`._

> **It needs improvement 9.** With the libraries loaded, any file that opens with a `module`
> header crashes during `import` — a real Lean bug, already reported upstream, that this work
> exposed rather than caused. See [`09-code-generation-fix.md`](09-code-generation-fix.md). Files
> in the traditional style, with a plain `import Mathlib` and no `module` header, are unaffected,
> and that is what nearly everyone writes today.

## 1. The waste

Mathlib's tactics are themselves Lean programs. In a normal build they are not compiled to native
code: they run in Lean's bytecode interpreter, one instruction at a time. The elaboration census
found that this accounts for about 30 % of elaboration processor time across our corpus — time
spent interpreting `aesop`, `linarith`, `norm_num` and the rest rather than doing the work those
tactics describe.

The machine code exists in principle. Lake emits a C file for every module it builds, Mathlib's
artifact cache ships those C files to every user, and Lean's interpreter prefers native code for a
declaration wherever it can find it. Nothing compiles them.

## 2. What to do about it

Compile and link the C files, one shared library per package:

```sh
lake build Mathlib:shared batteries/Batteries:shared …      # eight packages in all
```

That produces eight shared libraries totalling **153 MB**, in about fourteen minutes of wall time
on an M2 Pro (813 s wall, 1,374 s CPU). It **invalidates no compiled library file** and requires
no change to any lakefile — the C files were already there.

Then hand them to `lean`, one `--load-dynlib=` per library, **in dependency order**; the wrong
order fails with an unresolved-symbol error.

### Why a wrapper rather than a lakefile setting

A build is driven by Lake, which invokes `lean` once per module, so the libraries have to reach
every invocation. The obvious route is a lakefile's `moreLeanArgs`, and it is the wrong one: Lake
records those arguments in the traces that decide what needs rebuilding, so naming the libraries
there would invalidate the compiled files this whole approach depends on leaving untouched.

The wrapper stands in for the `lean` executable instead. Installed as the `lean` of a toolchain
directory, it receives Lake's arguments, prepends its own, and executes the real binary. Lake
never sees it.

## 3. What it buys

Elaboration processor time, two independent rounds per group:

| group | files | elaboration CPU | of which interpreted |
|---|---|---|---|
| textbook | 15 | 22.9 → **6.2 s** (−73 %) | 11.5 → 0.9 s (−92 %) |
| Mathlib's own files | pooled | 219 → **156 s** (−29 %) | 72.1 → 4.3 s (−94 %) |
| formal-conjectures | 10 | 69.3 → **42.6 s** (−41 %) | 27.9 → 3.3 s (−88 %) |

The spread between groups is entirely explained by how much of each was interpreted to begin
with — 78 %, 33 % and 40 % respectively. The interpreter share collapses by 88–94 % everywhere;
what differs is how much of the total that share was.

Per call, the Lean-implemented tactics get two to twenty times faster and the ones already written
in C++ do not move at all, which is a useful check that the mechanism is doing what it claims:

| faster | | unchanged |
|---|---|---|
| `aesop` | 98 → 5 ms | `simp`, `simp only` |
| `linarith` | 112 → 47 ms | `omega`, `decide` |
| `fun_prop` | 77 → 32 ms | `grind`, `nlinarith` |
| `ring1` | 18 → 8 ms | `simpa` |
| `gcongr` | 16 → 7.9 ms | |
| `field_simp` | 13 → 6.0 ms | |
| `norm_num` | 12 → 5.5 ms | |

Mathlib's own linters and `to_additive` drop 78–91 %.

**In an editor it changes nothing, in either direction.** Six scripted sessions per arm: median
edit latency 212 → 212 ms — which is the language server's own 200 ms debounce — with `exact?`,
goal display and completion latency unchanged, and peak worker memory unchanged to within 10 MB.
An interactive session's cost is the opening import and the debounce, not tactic execution. The
gain here is for batch work: building a project, checking a corpus, running a proof-search service.

## 4. Equivalence

22 cases and 1,008 lines of `#print`, `#print axioms`, `#check` and diagnostic output:
byte-identical stdout and stderr on all 22, and `lean --stats` on `import Mathlib` identical.
Results are identical by construction — the same program is being run, compiled rather than
interpreted — and the suite is there to catch the ways that reasoning could be wrong.

Reproduce with `LEAN_NATIVE=0`, which makes the wrapper run the same binary on the same files
without the libraries. That is the A/B control.

## 5. What is unfinished, provisional or known to be wrong

### 5.1 Nothing checks that the libraries match the library

This is the one caveat a user of this improvement must know. Because Lake is not told about the
shared libraries, nothing keeps them in step with the rest of the build. A user who updates
Mathlib and fetches the new artifact cache, but does not rebuild the libraries, is left with
libraries built from the old source. Lean runs the compiled form of a declaration whenever it can
find one, so any declaration whose name did not change will silently run its old implementation.
Declarations that were renamed or removed are not at risk: the lookup fails for them and Lean
interprets them as it would have anyway.

Lake's own `precompileModules` does not have this problem, because it records the libraries in the
traces that decide what to rebuild — the mechanism given up here in order to leave the cache
intact. The caveat would be removed by a check that each library was built from the same sources
as the compiled files it accompanies; we did not build one.

### 5.2 Distribution is not solved

Fourteen minutes is more than one would want to ask of every Lean user, so the libraries would
ideally be distributed the way the rest of the build is. That can be done without disturbing the
existing cache, which is platform-independent by contract: it holds compiled library files and C
source, the same on every machine. Shared libraries are not, so they need a cache of their own —
one archive per library per platform, keyed by operating system and processor as well as by the
toolchain version and library revision the existing cache already uses. A user whose platform
nobody had built for would be told so and left where they are today. This is a change to how
Mathlib is distributed rather than to Lean or Mathlib, which is why it is described and not built.

### 5.3 The language server needs an environment variable

`lean --server --load-dynlib=X` forwards the flag to its file workers as `-lX`, which `lean`
rejects. The documented route therefore cannot give a language-server worker these libraries;
`LEAN_WORKER_PATH` is the way in. This is a second, unrelated Lean defect that this work
uncovered, and it is not fixed here.

### 5.4 A finished version would live inside Lean

It would be enough for Lean, on importing a module, to look in the build directory of that
module's package for the package's native library and load it if it is there, since the symbol
lookup that follows is automatic already. Such a design would inherit the caveat of §5.1, since
Lean would be finding the libraries rather than being handed them, and would need the same check.
Neither the check nor the integration is difficult; we did not pursue either.
