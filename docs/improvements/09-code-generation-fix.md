# The code-generation fix

_Patch: [`patches/fix-codegen-meta-initialize.patch`](../../patches/fix-codegen-meta-initialize.patch),
**one file, +9 / −0** (five of the nine lines are comment), against Lean `v4.33.1`
(`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`). Ships separately from the fork, because it changes
Lean's behaviour — a crash stops happening — and a performance fork whose claim is "observably
identical" cannot contain a behaviour change without making its own central claim untestable._

> **This defect is already reported upstream.** It is
> [lean4#14359](https://github.com/leanprover/lean4/issues/14359), labelled `bug` and `P-high`,
> filed 2026-07-10, still open with no fix PR. **We did not discover it. We hit it.** What follows
> should be read as a contribution to that issue — a second manifestation, a stock-toolchain
> reproducer, and a fix that has been built and exercised over a whole Mathlib — and not as a
> finding. §5 states precisely what is new and what is not.

---

## 1. The defect

### 1.1 What the emitter writes

For a module-system module `M`, `Lean.Compiler.LCNF.EmitC.main`
(`src/Lean/Compiler/LCNF/EmitC.lean`) emits three initializers:

```lean
if (← getEnv).header.isModule then
  emitInitFn (phases := .runtime)     -- runtime_initialize_<M>
  emitInitFn (phases := .comptime)    -- meta_initialize_<M>
  emitLegacyInitFn                    -- initialize_<M>
else
  emitInitFn (phases := .all)         -- initialize_<M>
```

`emitInitFn` emits, for each *import* `I` belonging to the phase being emitted, a call to `I`'s
initializer, and then the assignments of the module's own globals for that phase. The import
filter is

```lean
if phases != .all && imp.isMeta != (phases == .comptime) then return none
...
let fn := mkModuleInitializationFunctionName
  (phases := if phases == .all then .all else if imp.isMeta then .runtime else phases) imp.module pkg?
```

— so `meta_initialize_<M>` calls `runtime_initialize_<I>` for every `meta` import `I`, on the
correct reasoning that `M`'s meta code may call `I`'s ordinary definitions.

**The same reasoning applies to `M` itself, and was not applied.** `M`'s own `initialize` blocks
and meta definitions routinely call `M`'s own plain `def`s, whose closed-term globals only
`runtime_initialize_<M>` assigns. Only the legacy `initialize_<M>` calls both phases.

### 1.2 Why the loader does not rescue it

`runInitAttrForMod` (`src/Lean/Compiler/InitAttr.lean`) does run the runtime initializer first —
but only conditionally:

```lean
let initRuntime := Elab.inServer.get opts || mod.irPhases != .runtime
...
if env.header.isModule then
  let initializedRuntime ← pure initRuntime <&&> runModInit (phases := .runtime) mod.module pkg?
  let initializedComptime ← runModInit (phases := .comptime) mod.module pkg?
```

`mod.irPhases` is `.runtime` for a module the import walk reached only through non-`meta`
imports. For such a module, outside the language server, `initRuntime` is **false**: the runtime
initializer is deliberately skipped, because the elaborator is not going to run that module's
runtime code. `meta_initialize_<M>` is nevertheless called unconditionally, to register the
module's environment-extension entries — and it is then running native code whose own globals are
still NULL.

Interpreted, the same code never touches those C globals. That is why stock Lean is fine and why
the failure appears only once a native library is loaded. **It is not an artefact of loading a
whole library**: Lake's own `precompileModules` option reaches `meta_initialize_<M>` by exactly
the same route, which is why this is worth having even if you never compile Mathlib's tactics.

### 1.3 The two manifestations

Both were reproduced on this machine, on macOS/arm64 and on Linux/arm64:

* **At import.** `Mathlib/Util/Export.c`: `meta_initialize_mathlib_Mathlib_Util_Export` calls
  `_init_lp_mathlib_Lean_Export_instInhabitedState_default()`, which reads a global assigned only
  in `runtime_initialize_mathlib_Mathlib_Util_Export`. SIGSEGV in `lean_mark_persistent`, inside
  `Lean_importModules`. Any file with a `module` header that imports Mathlib.
* **At elaboration.** `Mathlib/Tactic/Linter/DocString.c`: `meta_initialize_` registers the
  docString linter; the linter calls the module's own plain `def deindentString`, whose boxed-`Nat`
  constants are NULL. This one hits `Mathlib/Logic/Basic.lean` — an ordinary source file, not a
  test case.

## 2. What the change does

`src/Lean/Compiler/LCNF/EmitC.lean`, in `emitInitFn`, immediately after the import-initializer
loop and before the loop over the module's own declarations:

```lean
  if phases == .comptime then
    -- The `comptime` initializer runs this module's `initialize` blocks, whose code may reference
    -- closed-term globals of this module's *runtime* declarations; those are assigned by
    -- `runtime_initialize_<M>`, which is not guaranteed to have run first (`runInitAttrForMod`
    -- skips it when the module's IR is needed at runtime only). Run it here; it guards on its own
    -- `_G_runtime_initialized` flag, so this is idempotent.
    withErrRet do
      emit s!"{← getModInitFn (phases := .runtime)}(builtin)"
    emitLn "lean_dec_ref(res);"
```

Nothing else is touched: no runtime, no loader, no `.olean` format, no Lake.
`runtime_initialize_<M>` is emitted *before* `meta_initialize_<M>` in the same translation unit
(see the `main` listing in §1.1), so no forward declaration is needed. `emitLegacyInitFn` already
called both phases and is unchanged. `src/Lean/Compiler/IR/EmitLLVM.lean` only ever emits the
`.all` initializer and is unaffected.

Resulting C, for the module of the import-time crash:

```c
LEAN_EXPORT lean_object* meta_initialize_mathlib_Mathlib_Util_Export(uint8_t builtin) {
lean_object * res;
if (_G_meta_initialized) return lean_io_result_mk_ok(lean_box(0));
_G_meta_initialized = true;
res = runtime_initialize_Init(builtin);                         /* an import */
…
res = runtime_initialize_mathlib_Mathlib_Util_Export(builtin);  /* <-- the fix */
if (lean_io_result_is_error(res)) return res;
lean_dec_ref(res);
lp_mathlib_Lean_Export_instInhabitedState_default = _init_lp_…();
lean_mark_persistent(lp_mathlib_Lean_Export_instInhabitedState_default);   /* no longer NULL */
```

## 3. Why it is correct

**Idempotence.** `runtime_initialize_<M>` opens with `if (_G_runtime_initialized) return
lean_io_result_mk_ok(lean_box(0));` and sets the flag. Calling it from `meta_initialize_<M>` is
therefore safe whether or not the loader already called it, and calling it twice is a no-op.

**Ordering.** The call is emitted after the import initializers and before the module's own
comptime assignments, which is the only position that works: the runtime initializer's own body
may call imported code, and the comptime assignments are what need the runtime globals.

**Error propagation.** The emitted call uses the same `withErrRet` / `lean_dec_ref(res)` idiom as
every other initializer call in the function, so an error in the runtime initializer propagates
exactly as an error in an import's initializer does.

**The generated C differs by exactly this and nothing else.** Every Mathlib `.c` file was diffed
against the stock-emitted one and each difference classified. On Linux, where both trees come from
the same branch and the comparison is clean: **8,312 of 8,312 files differ only by inserted
`runtime_initialize_<M>` calls, 0 differ otherwise.** On macOS the same run gives 8,303 of 8,312,
and the nine exceptions differ additionally by one integer — `122` → `125` — which is the line
number of `Lean.isCtor?` in `src/Lean/MonadEnv.lean` embedded in a `panic!` message that never
fires. The fork's search-index change adds three comment lines above it. That is pre-existing fork
noise, not this fix.

**The signature is visible in the artefact.** The eight shared libraries grow from 209,711,000 B
to 210,300,392 B, **+589,392 B (+0.28 %)**. Three grow (Mathlib +457,584, Aesop +66,272,
ProofWidgets +65,632) and five do not move at all — which is exactly right, since the call is
emitted only into `meta_initialize_<M>`, which only a module-system module has. Mathlib's share is
**55 bytes per module**.

## 4. How it was verified, and what that does not cover

The toolchain was built, Mathlib and its eight dependency packages were rebuilt from source with
it (8,705 jobs, 42:53 on macOS, 1:01:03 on Linux/arm64), and the eight native shared libraries
were built from the freshly emitted `.c`.

**The crashes are gone, deterministically.** Ten configurations × 5 runs = **50 runs, 0 signals**,
on macOS/arm64 and again on Linux/arm64, including both crashing configurations:

| case | exit codes, 5 runs |
|---|---|
| `module` header + `public import Mathlib` + one theorem, native libraries loaded | 0 0 0 0 0 |
| the same with a doc-string, native | 0 0 0 0 0 |
| the same with linters off, native | 0 0 0 0 0 |
| `Mathlib/Logic/Basic.lean`, dependency libraries + `libmathlib_Mathlib` | 0 0 0 0 0 |
| the six non-crashing controls | 0 throughout |

The **unfixed** configuration was re-checked on the same machine immediately before, and still
exits 139 on both of those rows.

**Nothing else moved.** The 22-case byte-for-byte suite comparing "libraries loaded" against
"libraries not loaded" on the fixed toolchain is **22/22 identical** in stdout, stderr and exit
code — including `Mathlib/Logic/Basic`, which the original report could only record as `rc 0` vs
`rc 139`. Separately, stock `lean` + stock Mathlib against the fixed toolchain + its rebuilt
Mathlib, with **no** libraries loaded on either side, is **15/15 byte-identical** on the textbook
suite with `#print axioms`: the ordinary interpreted path is untouched.

`lean --stats` differs in exactly two counters, both of them the fork's lazy-loading counters and
both expected: with native code available the interpreter resolves 134 modules' functions by
`dlsym` and never asks for their `.ir` part.

**Cost: no measurable time.** `import Mathlib` + one theorem through the fixed toolchain:
2.68–2.76 s with the libraries off, **2.19–2.42 s with them on**; a `module` header 2.40–3.20 s
off, **1.97–2.02 s on**. Loading the libraries is still a net gain, and the `module`-header case —
the one that used to die — is now the fastest configuration measured.

**What this does not cover.**

* **x86-64 was never tested.** Both architectures tried are arm64.
* **The equivalence suites do not exercise §6.1**, the one known behaviour difference.
* **No upstream review.** The fix has not been proposed on the issue, and the question §6.1 raises
  may be the reason upstream has not simply done this.

## 5. What this adds to the issue that is already open

Stated precisely, because the rest is not ours:

* **A manifestation that is not a specialization.** The issue's title, body and proposed fix are
  all about a compiler-generated specialization. One of the two crashes here is an ordinary
  private `def` whose closed-`Nat` globals are assigned only by its module's runtime initializer,
  in a linter that is *registered* by the comptime initializer and then runs at elaboration time,
  long after import. That changes what a correct fix has to cover.
* **A stock-toolchain reproducer**: three lines (`module` / `public import Mathlib` / one theorem)
  with stock Mathlib, exit 139 on 5 runs of 5, on macOS/arm64 and Linux/arm64. The issue's
  reproducer is a purpose-built three-module project.
* **A fix, built and exercised** over a whole Mathlib rebuild: §3 and §4.

The issue also carries a different, AI-proposed patch, relayed with an explicit disclaimer from
the person who filed it. It is not the same change.

## 6. What is unfinished, provisional or known to be wrong

### 6.1 The fix runs `[init]` blocks the interpreter would have skipped

This is the one real behaviour difference, and it is the question a reviewer should ask about.

`runInitAttrForMod` skips the runtime phase for a module whose IR is needed at runtime only, and
the interpreted path skips those `[init]` declarations too (`getIRPhases env decl == .runtime`).
After this change, a module with any meta content runs `runtime_initialize_<M>` — **including its
runtime `[init]` side effects** — as soon as its meta initializer runs.

That is what the legacy `initialize_<M>` always did, and it is the only way to make the globals
non-NULL without a finer-grained initializer. But it is a difference from the interpreted path,
and **the equivalence suites here happen not to expose it**. The surgical alternative is to split
`runtime_initialize_<M>` into a globals-only part and an `[init]` part and call only the former —
a larger emitter change, and one that would want upstream's opinion. If upstream has a reason not
to have done the nine-line version, this is very likely it.

### 6.2 A second, unrelated defect is untouched

`--load-dynlib` still cannot reach a language-server worker: the watchdog forwards it as
`-l<path>`, which `lean` rejects. Setting `LEAN_WORKER_PATH` remains the way in. That is a
separate v4.33.1 defect found alongside this one and not addressed by this patch.

### 6.3 Something to know before rebuilding a library with the fork

A from-source Mathlib build under the fork's *cache-era* tactic-index design wrote 4,331 files and
21 GB into `~/.cache/lean-tactic-index` in seven minutes and nearly filled the disk, which forced the first
rebuild attempt to be killed. Every script in this work therefore sets `LEAN_TACTIC_INDEX=0`. **That
defect is fixed** — the tactic index image no longer writes anything at run time
([`docs/improvements/06-tactic-index-image.md`](06-tactic-index-image.md)) — but the shipped **Linux** fork patch still
contains the old design, so the warning still applies there.

`LEAN_TACTIC_INDEX=0` costs nothing for this work in any case: the tactic index is an import-time
optimisation with no effect on the `.olean` or `.c` a build produces, and with it off the
docString linter's registration really does come from `meta_initialize_<M>` rather than from a
mapped extension state, which is the path under test.
