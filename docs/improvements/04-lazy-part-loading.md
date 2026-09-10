# Improvement 4 — lazy part loading

_Against Lean `v4.33.1` (`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`). Part of
[`patches/lean4-v4.33.1-optimized.patch`](../../patches/lean4-v4.33.1-optimized.patch).
Platform-independent. Switches: `LEAN_LAZY_PARTS` = `0`/`off` (stock eager import), `ir` (defer the
`.ir` parts), `all` (default: defer `.ir` and `.olean.server`/`.olean.private`);
`LEAN_LAZY_PARTS_INDEX_DIR`, `LEAN_LAZY_PARTS_VERBOSE`, and the three bounds `LEAN_LAZY_PARTS_MIN_FREE`,
`LEAN_LAZY_PARTS_MAX_SIZE`, `LEAN_LAZY_PARTS_MIN_USES`._

> **This is the most invasive change in the fork, and the one with the most caveats.** It works,
> it is the largest single improvement at import, and it carries a prototype side file that a
> production version would not have. §6 is long on purpose.

---

## 1. The problem

A root file **without** `module` — the ordinary way almost everyone writes Lean today — imports
every module at the `.private` level. Stock Lean maps all five files of every module: for `import
Mathlib`, **52,490 files and 7.50 GB**. It builds the constant map from the `.olean.private`
parts (771k constants) and hands the `.ir` parts to the interpreter. A `module` root, by
contrast, maps 36,400 files and ends 0.9 GB lower.

Measured over the Mathlib closure (11,173 modules; the `import Mathlib` closure is 10,498 of
them):

| | `.olean` | `.olean.server` | `.olean.private` | `.ir.sig` | `.ir` |
|---|---|---|---|---|---|
| bytes | 2.24 GB | 0.14 GB | **5.06 GB** | 2.0 MB | 0.65 GB |

The private parts hold proof bodies, unexposed definition bodies and private declarations. The
`.ir` parts hold the interpreter's IR. **Both are needed only on demand** — but a legacy root
also needs *some* data out of them eagerly, and that is the whole difficulty.

### 1.1 What is actually in the private parts

Two measurements decided the design.

**Constants.** 664,547 exported vs 806,765 private. 142,218 names exist only in the private part,
142,155 of them `_private.…` names. Of the 664,547 shared names, **211,256 are literally the same
object** in both parts (the parts share a compactor, so an object emitted in the `.olean` is
referenced by address from the `.olean.private`), and 453,291 differ — **all of them weakenings**:
axiom ← theorem 418,223, axiom ← definition 32,693, axiom ← opaque 2,375. No shared name differs
in any other way.

Two consequences, both used below: *an exported `ConstantInfo` that is not an `axiomInfo` **is**
the private object*; and a weakened `ConstantInfo` keeps `name`, `levelParams` and `type` (its
`toConstantVal` is identical), so **any consumer that needs only the type can be served from the
`.olean` part**.

**Extension entries.** The private-only entries are almost entirely in extensions whose
`addImportedFn` ignores its input and which are read per module through `getModuleEntries` —
`impureSigExt` 580k, `extraModUses` 684k, `monoExt` 541k, `declRangeExt` 446k,
`functionSummariesExt` 270k, `declMetaExt` 205k, `docStringExt` 113k, and so on. **But eager
extensions have private-only entries too**: `Match.Extension.extension` 26,848,
`specCacheExt` 50,537, `backwardDefeqAttr` 2,138, `simpExtension` **1,289** (across 272 modules —
these are `@[simp] private theorem`s), `instanceExtension` 672, `grindExt` 618, `regularInitAttr`
53. A legacy root sees all of them, and `simp`, instances and `grind` build their discrimination
trees from the *whole* entry array at import, **in array order** — which decides which of two
equally scored lemmas fires first.

**IR.** 762,480 `extraConstNames` and 774,103 `.ir` entries. The `extraConstNames` are every IR
declaration name; `finalizeImport` uses them to fill `const2ModIdx` so that the code generator's
cross-module specialisation cache and the interpreter can route an auxiliary name
(`f._closed_1`, `List.map._at_.g.spec_0`, …) to its module.

So the private parts and the `.ir` parts cannot simply be deferred wholesale.

## 2. What the change does

`importModulesCore` reads only the `.olean` part of every module. `finalizeImport` then either
loads the rest eagerly (stock behaviour) or registers it for on-demand loading. The data a legacy
root needs *eagerly* out of the deferred parts comes from a per-closure **index file**.

### 2.1 The import walk — `src/Lean/Environment.lean`

`ImportedModule` gains two fields:

```lean
deferredParts : Array System.FilePath := #[]   -- .olean.server / .olean.private, not yet read
irFiles       : Array System.FilePath := #[]   -- .ir.sig / .ir, not yet read
```

`importModulesCore.loadData` now reads only `fnames[0]` with `CompactedRegion.read` and returns
the remaining paths; `loadIRPaths` returns the `.ir.sig`/`.ir` paths without reading them.
`ImportedModule.getData?` falls back to the highest loaded part
(`self.parts[level.ctorIdx]? <|> self.parts.back?`), which is what keeps the DAG walk working:
all parts share `imports` and `isModule`.

One case must stay eager, and does: a module reached only through its `.ir` (a data-less,
IR-only module) has its imports discovered *from* the `.ir`, so `loadIR` is still called for it.
This was a real crash — a `module` root with `meta` imports died in the interpreter with
"unknown declaration `…setConfig'._redArg`" — found by the equivalence suite and fixed.

### 2.2 The index

```lean
structure LazyPartsModIndex where
  extraConstNames  : Array Name                      -- of the .ir part
  irEntries        : Array (Name × Array EnvExtensionEntry)  -- .ir entries other than declMapExt
  privOnlyNames    : Array Name                      -- private constNames ∖ exported constNames
  privOrder        : Array Nat                       -- indices into the exported arrays, in private (declaration) order
  privEagerEntries : Array (Name × Array EnvExtensionEntry)  -- private-level arrays of eager extensions that differ

structure LazyPartsIndex where
  moduleNames : Array Name
  mods        : Array LazyPartsModIndex
```

`lazyPartsKey` hashes the module list in `ModuleIdx` order together with the size and mtime of each
module's deferred `.olean.private` and `.ir`, plus `lazyPartsIndexVersion` (currently 3).
`lazyPartsLoadIndex` / `lazyPartsSaveIndex` are `CompactedRegion.read`/`save` wrappers (temp file + rename);
`lazyPartsBuildIndex` builds it from *fully loaded* modules, so it can only be written on a run that was
eager anyway.

`privOrder` deserves a note: it exists because `header.moduleData` must list a module's constants
in **declaration** order, not the exported parts' name-sorted order. Its absence was a real bug —
`exact?` picked `Right.mul_pos` instead of `Left.mul_pos` — caught by the equivalence suite.

`privEagerEntries` is built by comparing, element by element with `ptrAddrUnsafe`, each private
entry array against the exported one; only arrays that genuinely differ are copied.

### 2.3 `finalizeImport`

Only for legacy roots (`level == .private && !isModule`). Reads `LEAN_LAZY_PARTS`, computes the key,
tries to map `$LEAN_LAZY_PARTS_INDEX_DIR/index-<key>.lazyparts` and checks its module list. Then:

* **materialise what is still needed** — with no index (the first import of a closure) or
  `LEAN_LAZY_PARTS=0`, read the deferred parts and the IR parts, i.e. exactly the stock state;
* `moduleData[m]` becomes either the private data (stock) or, in lazy mode, the **exported
  `ModuleData` with its `entries` array patched from the index** and its constants permuted into
  `privOrder`, read with `getPersistentElem` so that no `ConstantInfo` page is touched;
* `irData[m]` becomes either the real `.ir` data or a synthetic `ModuleData` carrying the index's
  `extraConstNames` and `[init]`/package entries;
* `const2ModIdx` is built exactly as stock (module constants + `extraConstNames`) **plus** the
  index's `privOnlyNames`;
* the lazy state is registered.

`module` roots keep the stock path except that they no longer opportunistically map the
`.olean.server`/`.olean.private` parts they will not use — which is worth 0.5 GB on its own and
would be a one-line change upstream.

### 2.4 The lazy state, and why it lives outside the environment

```lean
structure LazyPartsState where
  lazyPrivate, verbose : Bool
  moduleNames  : Array Name
  mods         : Array LazyPartsLazyMod            -- per module: privFiles, privDeps, irFiles
  exported     : Std.HashMap Name ConstantInfo   -- the eagerly built map, for duplicate resolution
  privEntries  : Array (Option (Std.HashMap Name (Array EnvExtensionEntry)))
  irEntries    : Array (Option (Std.HashMap Name (Array EnvExtensionEntry)))
  overlay      : PHashMap Name ConstantInfo -- constants of loaded private parts
  regions      : Array CompactedRegion
  numPrivLoads, numIRLoads, numOverlay : Nat

builtin_initialize lazyPartsRegistry : IO.Ref (Array (IO.Ref LazyPartsState)) ← IO.mkRef #[]
```

The environment object itself is unchanged and stays persistent; all mutable state is behind refs
in a process-global registry that `markPersistent` never reaches. The state's index (+1) is
stored **under a reserved key in `EnvironmentHeader.moduleName2Idx`**:

```lean
def lazyPartsIdKey : Name := `_lazyParts.id
@[inline] def EnvironmentHeader.lazyPartsId (h : EnvironmentHeader) : Nat := ...
```

This is not elegance, it is a bootstrapping constraint (§3.4): a new *field* on
`EnvironmentHeader` cannot be introduced in one stage.

### 2.5 On-demand loading

| function | trigger |
|---|---|
| `lazyPartsLoadPriv ref m reason` | loads module `m`'s `.olean.server`+`.olean.private`, chained on its `.olean` region (`lazyPartsReadPartsWithDeps`), inserts every constant into the overlay with `getPersistentElem`, resolving duplicates with the same `subsumesInfo` rule `finalizeImport` uses (`lazyPartsSubsumes`, a copy of `subsumesInfo` taking an arbitrary lookup function), and stores the module's entry map |
| `lazyPartsLoadIR ref m reason` | loads `.ir.sig`+`.ir` |
| `lazyPartsResolveConst lazyId const2ModIdx n c?` | the constant path. Overlay hit → return it. `c? = some c` with `c` not an `axiomInfo` → return `c` (§1.1: it *is* the private object). `c?` an `axiomInfo` or `none` → `const2ModIdx[n]` names the module; load its private part and answer from the overlay |
| `lazyPartsModuleEntries lazyId extName m ir` | the extension path. For `ir = true`, only `Lean.IR.declMapExt` is served lazily. For `ir = false`, only extensions in `lazyPartsLazyExtNames` |

Both are `@[implemented_by]` pairs over `unsafeBaseIO`, so `getModuleEntries`,
`getModuleIREntries` and `find?` keep their pure signatures and **no call site changes**.

`Environment.baseFind?` is new and routes the imported-constant lookup through
`lazyPartsResolveConst`; `find?`, `findAsync?`, `findTask` and `containsOnBranch` use it.
`findConstVal?` deliberately short-circuits on any eager hit, because a weakened
`ConstantInfo` has the same `ConstantVal`.

`lazyPartsLazyExtNames` is the explicit list of extensions whose imported entries are consumed only per
module through `getModuleEntries` (their `addImportedFn` ignores the entries): the four LCNF
compiler-phase extensions, `extraModUses`, `declRangeExt`, the five docstring extensions,
`inlineAttrs`, `nospecializeAttr`, `matchPatternAttr`, `sparseCasesOnInfoExt` and the two
`eqnInfoExt`s. **`declMetaExt` is deliberately excluded** although it is lazy in the same sense:
`getIRPhases` consults it for every constant the interpreter evaluates, which would load the
private part of every module with an initializer or a parser at import time.

### 2.6 Two call sites that only needed a type

```lean
-- src/Lean/Parser/Extension.lean, mkParserOfConstantUnsafe
-  match env.find? constName with
+  match env.findConstVal? constName with

-- src/Lean/Environment.lean, evalConstCheck
-  match env.find? constName with
+  match env.findConstVal? constName with
```

Both inspect only `info.type`. The effect is large and is the best illustration of how the
laziness behaves:

| version | private parts loaded during `import Mathlib` |
|---|---|
| first prototype | 1,011 of 10,498 |
| `declMetaExt` made eager | 1,011 |
| parser extension → `findConstVal?` | 977 |
| `evalConstCheck` → `findConstVal?` | **8** |

### 2.7 `src/Lean/Compiler/IR/CompilerM.lean`

`findInterpDecl` and `findInterpDeclBoxed` consult the **`.olean`** copy of `declMapExt` first
when a lazy id is set:

```lean
if env.header.lazyPartsId != 0 then
  match findAtSorted? (declMapExt.getModuleEntries env modIdx) declName with
  | some d@(.fdecl ..) => some d
  | d? => findAtSorted? (declMapExt.getModuleIREntries env modIdx) declName <|> d?
else <the stock `ir <|> olean` order>
```

For a `meta` declaration the `.olean` holds the full `fdecl` — the same declaration as the `.ir`,
since both come from `declMapExt.getEntries env` in `writeModule` — so Mathlib's tactic
implementations never need the `.ir` part. Only when the `.olean` copy is an `.extern` stub or
absent is the `.ir` consulted, in the stock order. Same `Decl`s, fewer loads.

### 2.8 The index directory is a cache, and it is bounded

`src/runtime/io.cpp` gains one primitive:

```c
extern "C" LEAN_EXPORT obj_res lean_io_free_disk_space(b_obj_arg path);
```

One `uv_fs_statfs`; every failure reports `0`, which callers read as "unknown, do not block the
write". `Environment.lean` gains `CacheBound` and the helpers `cacheParseSize`,
`cacheReadBound`, `cacheFloorOk`, `cacheAdmit`, `cacheTouch`, `cacheEnforceCap`, `cacheStats`,
implementing three bounds:

* **a free-space floor** (`LEAN_LAZY_PARTS_MIN_FREE`, default 10 GB): below it nothing new is written,
  but existing files are still read;
* **a size cap with LRU eviction** (`LEAN_LAZY_PARTS_MAX_SIZE`, default 5 GB), enforced after each write
  by one `readDir` plus one `stat` per file. Recency is the newest mtime in an entry's group of
  files, which is why every hit rewrites a one-byte `.used` stamp (~30 µs);
* **an admission threshold** (`LEAN_LAZY_PARTS_MIN_USES`, default **1**), the analogue of nginx's
  `proxy_cache_min_uses`. At 1 nothing changes. At 2 a from-source build writes one empty marker
  per closure and no indexes at all, because no closure there ever recurs.

`LEAN_CACHE_STATS=<file>` appends one short line per decision.

## 3. Why it is correct

For a legacy root, stock Lean's observable post-import state is: the constant map, `const2ModIdx`,
the extensions' `importedEntries` and the states computed from them, `header.moduleData`, and the
interpreter's IR tables. Taking them in turn.

**Constants.** `find?` returns the same `ConstantInfo` *object* as stock whenever the exported and
private objects coincide (the 211k shared, plus every inductive, constructor, recursor and
quotient, none of which is ever weakened), and for weakened or private-only names the object from
the private part after loading it — the same object stock would have stored. The dispatch is
sound because of the §1.1 fact: a non-`axiomInfo` exported entry *is* the private object, so it
needs no load; an `axiomInfo` is either a genuine axiom (same object) or a weakening, and both
cases are resolved by loading.

**Types-only consumers** see the same `ConstantVal` with no load at all, because a weakening
preserves it.

**The kernel** only ever calls `find?`. Unexposed definitions are unfolded from the real
`defnInfo`; theorems are opaque to definitional equality but their values are available for
`#print` and `Expr` access.

**Extensions.** Eager extensions get bit-identical entry arrays: the exported array when the
private one is element-wise pointer-identical, else the private array copied into the index.
Lazy extensions get the private array on demand, which is the same array stock would have handed
to `getModuleEntries`. This is the property that keeps `simp`'s and the instance tree's insertion
order identical, and it is what the 40 `simp?` calls and 28 `#synth` queries in the equivalence
suite are there to catch.

**`const2ModIdx`** is identical: module constants + `privOnlyNames` + the `.ir`
`extraConstNames`.

**The interpreter** gets the same `Decl`s (§2.7) and the same `[init]` declarations in the same
order (`runInitAttrs` reads the synthetic `regularInitAttr` entries in stock order).

### 3.1 Where it is *not* identical, and this is known

**`header.moduleData` holds the exported view.** Consumers that enumerate a module's constants —
`LazyDiscrTree` (`exact?`, `apply?`, `rw?`), `assert_not_exists`, `lake shake` — see weakened
infos (same types, kind `axiom` instead of `theorem`/`definition`) and do not see private-only
names. The `privOrder` permutation restores declaration order, and `exact?` uses only types and
blacklists internal names, and the results are identical in every test — but an ordering-sensitive
candidate list could differ in principle. This is a real semantic deviation, not a rounding
error.

**Duplicate constants across modules.** About 1k equation theorems are realised in several
modules. Stock keeps the first module in import order unless a later one is richer; this keeps
whichever module was loaded first unless the other is richer. The types are equal by
`subsumesInfo`; only `#print` of such a theorem could show a different proof term.

**`lean --stats`** differs by construction in region, byte and constant counts, and in the
imported-entry counts of the 15 lazily served extensions.

### 3.2 Thread safety

Loads race benignly: the region is read outside the ref, then merged with `ref.modify` whose
first check ("already loaded") keeps the first winner; a lost race leaks one duplicate mapping.
The overlay is a persistent hash map, so readers never observe partial state and inserts do not
copy. The hot path is one `ref.get` plus a `PHashMap` lookup; an eager hit that is not weakened
costs one extra `moduleName2Idx` lookup and one overlay miss.

### 3.3 Immutability

The environment stays persistent; nothing mutable is reachable from it. This is a deliberate
difference from the upstream draft
[lean4#14145](https://github.com/leanprover/lean4/pull/14145), which puts an `IO.Ref` *inside*
the environment and therefore has to swap in a fresh ref after every `markPersistent` (the
interpreter would otherwise deadlock taking a frozen ref on its first on-demand load).

### 3.4 The bootstrapping constraint

The stage-1 build elaborates stage-1 sources with the **stage-0** compiler and *interprets*
stage-1 IR for anything evaluated at build time. Two things therefore cannot be introduced in one
stage: a new `@[extern]` symbol reachable from interpreted code (the interpreter aborts with
"could not find native implementation"), and a new field in a structure that stage-0 native code
constructs (interpreted stage-1 code then reads garbage past the end of the object — the build
crashed in the interpreter's `unreachable` assertion). Both hit `Kernel.Environment.find?`, which
is interpreted during the build. Hence: all of this is plain Lean (`implemented_by` +
`unsafeBaseIO`, no new externs), and the lazy id lives under a reserved key in an existing map
(§2.4). This is why the code looks the way it does, and it is not a style choice.

## 4. How it was verified, and what that does not cover

Stock `lean v4.33.1` versus a hybrid toolchain (fork binaries, **stock** `.olean`s), run directly
with Mathlib's `LEAN_PATH`, for each of `LEAN_LAZY_PARTS=all`, `ir` and `0`. Because the first import of
a closure is eager and writes the index, every case file was run once beforehand.

| check | `all` | `ir` | `0` |
|---|---|---|---|
| 15 textbook files + `#print axioms` of every theorem | 15/15 identical | 15/15 | 15/15 |
| four Mathlib files re-elaborated (`module` roots) | 4/4 | 4/4 | 4/4 |
| the constant-map suite (174 lines) | identical | identical | identical |
| `lazy-paths.lean` — 290 lines: `#print` of theorem bodies, `decide`/`unfold`/`rfl` through unexposed definitions, `open private … from Mathlib.Tactic.CongrExclamation` + `#print axioms` of the private theorem, seven `#eval`s, `positivity`/`ring`/`linarith`/`norm_num`/`omega`/`simp`, two `exact?` | identical | identical | identical |
| `lake build` of a 9-module project from a clean `.lake` (Lake's `--setup`/artifact path) | `.olean`/`.ilean`/`.c` byte-identical, normalised log identical | same | same |
| `lean --stats` | regions 10,498 vs 52,490, bytes 2.19 vs 7.50 GB, constants 643,304 vs 771,129; **all entry counts identical except the 15 lazily served extensions** | regions 31,494, constants identical, **all entry counts identical** | identical |

`lazy-paths.lean` is the file that exists to force loads: it drives 9,861 of 10,498 private parts
(5.1 GB, 768k overlay constants) and 1,204 IR parts, and its output is identical.

**A first run of this suite found two real bugs**, both fixed and both listed above: the
data-less-module crash (§2.1) and the candidate-order bug (§2.2). That is the reason to trust the
suite as far as it goes, and the reason not to trust it further.

**What this does not cover.**

* **The language server was not measured for this change in isolation.** Repeated imports in one
  process share nothing across environments except the registry, and regions of discarded
  environments are never freed.
* **`--incr-header-save` / `--incr-load` in lazy mode was not measured** — and is now known to be
  broken (§6.3).
* **No Linux measurement of this change alone.**
* **No test asserts that `lazyPartsLazyExtNames` is complete or correct.** Each of the 15 extensions was
  checked by hand to have an `addImportedFn` that ignores its input and to be read only through
  `getModuleEntries`/`findExtEntry?`. A future extension added to that list wrongly would be a
  silent behaviour change. §6.2.
* **The cache bounds' defaults are not justified by measurement.** §6.5.

## 5. Measured effect

Fastest of 3 interleaved runs, minima for RSS and faults, warm page cache, one `lean` at a time,
load 1.6–2.2.

| case | binary | wall s | sys s | max RSS GB | files mapped | bytes mapped |
|---|---|---|---|---|---|---|
| `import Mathlib` (legacy) | stock | 9.22 | 7.52 | 5.68 | 52,490 | 7.50 GB |
| | 1+3 | 3.50 | 1.77 | 3.44 | 52,490 | 7.50 GB |
| | `LEAN_LAZY_PARTS=0` | 3.51 | 1.82 | 3.44 | 52,490 | 7.50 GB |
| | `LEAN_LAZY_PARTS=ir` | 3.27 | 1.44 | 3.14 | 31,494 (+1,148 on demand) | 6.99 GB |
| | **`LEAN_LAZY_PARTS=all`** | **2.69** | **1.02** | **2.08** | **10,498** (+1,156, 0.31 GB) | **2.19 GB** (+0.10 index) |
| `module` / `public import Mathlib` | stock → 1+3 → any mode | 5.67 → 2.87 → **2.38** | 4.19 → 1.32 → 0.91 | 3.26 → 2.54 → **1.99** | 36,400 → 15,716 | 7.23 → 2.51 GB |
| `import Mathlib.Tactic` | stock → 1+3 → **all** | 2.79 → 1.83 → **1.47** | 1.89 → 0.94 → **0.58** | 2.88 → 1.89 → **1.15** | 22,610 → 4,522 | 3.71 → 1.03 GB |

Against stock: wall −71 %, RSS −63 %, faults −52 %. `vmmap` of the idle post-import process shows
**no `.olean.private` mapped at all**.

**On-demand cost of a single command**, on top of the import baseline (2.66 s / 2.06 GB):

| `import Mathlib` + … | lazy wall / RSS | private parts | IR parts | bytes on demand |
|---|---|---|---|---|
| nothing | 2.66 s / 2.06 GB | 8 | 1,148 | 0.31 GB |
| `#print Real.exp_pos` | 2.68 / 2.08 | 20 | 1,153 | 0.32 GB |
| `by decide` | 2.77 / 2.07 | 10 | 1,154 | 0.31 GB |
| `by norm_num` | 2.79 / 2.08 | 15 | 1,172 | 0.33 GB |
| `by ring` | 2.85 / 2.08 | 15 | 1,163 | 0.33 GB |
| `#eval Nat.gcd 1234 5678` | 2.78 / 2.08 | 13 | 1,154 | 0.32 GB |
| textbook file 15 (polynomials/measure) | 2.90 / 2.12 | 45 | 1,186 | 0.35 GB |
| **`by exact?`** | **20.2 s / 7.38 GB** | **9,859** | 1,154 | 5.08 GB |

A command touches 2–45 private parts at ≤ 0.2 s of wall including its own work. **`exact?` was
the pathological case**, and it is the reason improvement 5 exists: it walked every imported
constant's type and pulled almost every private part, with 12 worker threads serialising on the
state ref (159 s of CPU against 24 s eager). Improvement 5 takes those loads from 9,859 to 120.

**In the finished fork**, switching only this off (`LEAN_LAZY_PARTS=0`) costs **+0.76 s (+32 %) and
+0.95 GB (+69 %)** on `import Mathlib` — the largest single contribution at import — and
+0.94 GB on the `exact?` row. But see §6.1.

## 6. What is unfinished, provisional or known to be wrong

### 6.1 It still costs 7 seconds of CPU on the `exact?` row

In the finished fork, `LEAN_LAZY_PARTS=0` makes `import Mathlib` + one `exact?` cost **8.80 s of CPU**
where lazy loading costs **15.77 s**. Wall time hides it (the search is parallel and the machine
has 12 cores) but it is real: deferred entries are evaluated from parts that are no longer
resident, and every load builds a per-module entry `HashMap` and inserts every constant into one
persistent hash map behind one `IO.Ref`. Improvement 5 repaired the *wall*, not the CPU. A
production version needs a cheaper per-module load: per-module constant tables looked up by
binary search, a sharded or lock-free overlay, or a bulk "load everything" path when a consumer
announces a full walk.

### 6.2 The index file is a prototype stand-in for a format change

The eagerly needed data — `extraConstNames`, `[init]` declarations, private-only names, and the
private-level entries of eager extensions — belongs in the `.olean` (or in a small fourth part).
It is in a side file only because that avoided changing the format and therefore avoided
rebuilding Mathlib. The file is 59–103 MB per closure over the closures measured, keyed on file sizes and mtimes, and its
size is dominated by 762k auxiliary names and 142k private names that a format-level solution
would not duplicate at all.

Related, and also prototype-shaped: `lazyPartsLazyExtNames` is a **name list**. It should be a flag on
the extension descriptor (`lazyImportedEntries`, set at registration). As it stands, an extension
whose `addImportedFn` starts consuming its entries would silently change behaviour if it were on
the list, and there is no test that would notice.

### 6.3 It is incompatible with the snapshot wrapper

Measured on Linux: with `LEAN_LAZY_PARTS=all`, **every** check through a saved header snapshot fails.
`LEAN_LAZY_PARTS=0` plus a snapshot is byte-identical to stock. The cause is structural — lazily loaded
regions are not in `header.regions`, which is the dependency list a snapshot is saved against, so
a snapshot taken after on-demand loads would not name the regions it references. See
[`docs/improvements/07-snapshot-wrapper.md`](07-snapshot-wrapper.md) §6.

The same omission means `Environment.freeRegions` would not release lazily loaded regions. They
are never freed today, which is safe because the frontend leaks the environment anyway.

### 6.4 `header.moduleData` and duplicate resolution deviate

§3.1. `exact?`-style consumers see the exported view; the duplicate rule differs in principle
from stock's. Both were checked and neither produced a difference on anything measured, but
neither is *proved* equivalent, and the second could show a different proof term under `#print`.

### 6.5 The cache bounds have three switches and two unjustified defaults

The 5 GB cap is the figure `ccache` and Gradle's build cache have used for caches of this shape —
a convention, not a measured optimum. The `MIN_USES` default of 1 means the first miss writes, as
before; setting it to 2 stops a from-source build writing indexes at all, at the price that a
project whose files *do* share headers loses the index on the first sighting of each closure.
Neither default is derived from a measurement of this workload. The right answer is the format
change of §6.2, which removes the directory entirely.

The eviction is also blunt: it removes whole entries oldest-first with no notion of what an entry
cost to build, so a 103 MB `import Mathlib` index is discarded as readily as a small one.

### 6.6 Relation to the upstream draft

[lean4#14145](https://github.com/leanprover/lean4/pull/14145) ("perf: lazy IR loading", one
commit, `breaks-mathlib` label) defers **`.ir` only, and only for `module` roots** — a legacy root
is unaffected. It changes the signatures of `findEnvDecl`, `getSorryDep`, `findInterpDecl`, the IR
checker, `EmitLLVM`, `KeyedDeclsAttribute` and `runInitAttrForMod`; it **removes
`ModuleData.extraConstNames`**, which is an `.olean` format change and is why it breaks Mathlib;
and it adds a per-`.ir` side table so the interpreter can route auxiliaries of not-yet-loaded
modules. Reported effect: imported bytes −14.7 %, imported parts −38.7 %, `maxrss` −45…−84 MiB,
`.ir` compile wall +88 %.

**If it lands, most of this becomes obsolete or has to be rewritten against it.** It is a draft
and has not landed in v4.33 or v4.34, but a reader assessing whether this work is worth pursuing
should know that upstream is moving in the same direction for the IR half.

### 6.7 Not done

* Per-environment state without a process-global registry (needs a stage-0 update to make the
  lazy id a real header field).
* An audit of the remaining `find?` callers that only need a type.
* Recording in the index which module wins a duplicate, so the resolution matches stock exactly.
* Windows.
