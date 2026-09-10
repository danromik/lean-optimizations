# Improvement 5 — the prebuilt search index

_Against Lean `v4.33.1` (`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`). Part of
[`patches/lean4-v4.33.1-optimized.patch`](../../patches/lean4-v4.33.1-optimized.patch).
Platform-independent. Switches: `LEAN_SEARCH_INDEX=0` (ignore stored and cached entries — walk
every constant, as stock does), `LEAN_SEARCH_INDEX_RECORD=0` (write no entries when compiling a module),
`LEAN_SEARCH_INDEX_CACHE_DIR=<dir>` (unset = no cache file), `LEAN_SEARCH_INDEX_VERBOSE=1`._

> This change has **two independent halves**. The first is sixteen lines and is a pure fix. The
> second is a prebuilt index and is the fork's largest single improvement — and the one whose
> macOS measurements were taken through a prototype side file rather than through the mechanism
> that is meant to ship. Both are described here; §6 separates them.

---

## 1. The problem

### 1.1 What the first `exact?` in a process does

`Lean.Meta.LibrarySearch.libSearchFindDecls` → `LazyDiscrTree.findMatches` builds, on the first
call in a process, one `LazyDiscrTree` over **every imported constant** and caches it in a
process-global `IO.Ref`. `createImportedDiscrTree` splits `env.header.moduleData` into tasks of
≥ 6,500 constants (about 120 tasks for Mathlib) and, per constant, runs:

1. `blacklistInsertion env name` — `allowCompletion` (an internal-name test, `isAuxRecursor`,
   `isNoConfusion`, **`isRecCore`**, the completion blacklist, `isMatcherCore`), plus `sorryAx`,
   `_private` names unless the root `import all`s, `.inj`/`.noConfusionType`;
2. `addImport name c` in `MetaM` at `reducible` transparency: `isDeprecated`,
   `isMetaprogramming`, then `forallTelescope c.type` and `InitEntry.fromExpr body` — which is
   `rootKey` = `reduceDT` (`whnfCore` + reducible unfolding) + `getFunInfoNArgs` + `ignoreArg`
   (`isType`/`isProof` → `inferType`) on the arguments; an `Iff` yields two more sub-entries.

The result per constant is a root `Key` plus a `LazyEntry` = `(todo : Array Expr, local context,
value)`, where `todo` holds *instantiated copies* of the subterms still to index. Entries are
pushed into per-root-key arrays, the task results appended in task order, then `toLazy`; deeper
trie levels are computed on demand at match time.

**Cost, on stock Lean:** about 25 s of CPU (12 threads, 4.6–5.7 s wall on a quiet machine) and
**+2.2 GB of resident memory that is never released**, because the tree is process-global and
every one of its 770k entries holds an instantiated copy of a type body and a local context.

### 1.2 What lazy part loading did to it

`isRecCore` was

```lean
env.findAsync? declName matches some { kind := .recursor, .. }
```

and `findAsync?` goes through the constant-map path, which under lazy loading resolves a
*weakened* exported `ConstantInfo` by loading its module's `.olean.private` part. Legacy modules
export every theorem as an axiom, so the **first theorem of every module triggered that module's
private-part load**: 9,859 of 10,498 parts for `import Mathlib` + one `exact?`, with 12 worker
threads serialising on the loader's state ref. 190 s of CPU, 21 s of added wall.

Everything *else* in the walk was already type-only: `inferType` uses `getConstVal` →
`findConstVal?`, and reducible unfolding needs definitions, which legacy modules export with
their bodies.

## 2. The first half: answer the kind question without loading

`src/Lean/Environment.lean`:

```lean
def findKindNoLoad? (env : Environment) (n : Name) : Option ConstantKind := do
  if let some c := (env.base.get env).constants.map₁[n]? then
    return .ofConstantInfo c
  env.findAsyncCore? n |>.map (·.kind)
```

`src/Lean/MonadEnv.lean`:

```lean
-  env.findAsync? declName matches some { kind := .recursor, .. }
+  env.findKindNoLoad? declName == some .recursor
```

**Why this is exact.** Only theorems and unexposed definitions are ever weakened, and both weaken
to `.axiom`. A recursor is never weakened, and a weakened entry is never a recursor. So for the
question actually being asked — "is this a recursor?" — the answer read off the exported view
equals the answer stock computes, for every name. The function's docstring says exactly this and
warns that callers testing for kinds that *can* be weakened would get `.axiom`; the only caller
added here tests for a kind that cannot.

Sixteen lines. No `.olean` change, no rebuild. Private parts loaded by `import Mathlib` + one
`exact?` go from **9,859 to 120** — the 8 the import itself loads, ~100 genuine reducible
unfoldings and attribute reads during the walk, and the candidate lemmas `exact?` applies.

Stock and `LEAN_LAZY_PARTS=0` are untouched by this half.

## 3. The second half: compute the keys once, at build time

The walk's root key depends only on the constant's type and on data fixed when its module is
written: reducibility and `deprecated` can only be set in the defining module; `isClass`,
instance binders and literals are properties of the type; the blacklist tags are
defining-module tags. So the keys can be computed once, when the module is elaborated.

### 3.1 `src/Lean/Meta/Tactic/LibrarySearch.lean`

```lean
structure IndexEntry where
  name : Name
  key  : LazyDiscrTree.Key
  mod  : DeclMod
  pfx  : Array LazyDiscrTree.Key := #[]   -- the first `prefixDepth` keys below the root
  complete : Bool := false                -- the path ends after `pfx`

builtin_initialize indexExt : SimplePersistentEnvExtension IndexEntry (Array IndexEntry) ←
  registerSimplePersistentEnvExtension { addEntryFn := Array.push, addImportedFn := fun _ => #[] }
```

`recordModuleIndex env opts` computes the entries at the end of elaboration by running **the very
same `addImport`** on the module's constants, in `mkModuleData` order
(`kenv.constants.foldStage2` — the order of the `constants` array stock walks), in a fresh
`MetaM` at `reducible` transparency with `maxHeartbeats := 0`, i.e. the context
`createTreeCtx`/`addConstImportData` use. `src/Lean/Elab/Frontend.lean` calls it in `runFrontend`
immediately before `writeModule`.

`prebuiltEntries env cache : LazyDiscrTree.Prebuilt (Name × DeclMod)` turns a module's stored (or
cached) entries into `InitEntry`s, **re-applying `blacklistInsertion` at import time** — that is
the one filter that depends on the importing environment, since `_private` names are candidates
only for a root that `import all`s the module.

`searchIndexSources env` decides where entries come from and whether a cache file should be written;
`libSearchFindDecls` consults it only when the import tree is about to be built.

### 3.2 `src/Lean/Meta/LazyDiscrTree.lean`

`LazyEntry` stops being an abbreviation and becomes an inductive with two constructors:

```lean
inductive LazyEntry (α : Type) where
  | pending  (todo : Array Expr) (lctx : LocalContext × LocalInstances) (v : α)
  | deferred (declName : Name) (sub : Option Nat) (v : α) (pfx : Array Key) (complete : Bool)
             (consumed : Nat := 0)

def prefixDepth : Nat := 3
```

A deferred entry is 6 words plus 3 keys, instead of an instantiated copy of a type body plus a
local context. `prefixDepth = 3` covers the `[Eq, *, *, *]` dropped-key extraction of library
search with no recomputation at all.

`evalLazyEntry` is refactored so that the "place `next` under key `k`" logic becomes a local
`place` function shared by both constructors, and gains a `deferred` branch that:

* serves the next key straight from `pfx` while `consumed < pfx.size`;
* pushes the value if `complete`;
* otherwise **recomputes** what `InitEntry.fromExpr`/`mkSubEntry` would have stored —
  `getConstVal`, `forallTelescope`, `rootKey`, and for a sub-entry `rootKey todo[i]` — and
  replays the `consumed` steps already taken with `pushArgs`.

`LazyEntry.keyPrefix` computes the stored prefix from a pending entry exactly as `evalLazyEntry`
would.

The plumbing: `abbrev Prebuilt (α) := ModuleIdx → Option (Array (InitEntry α))` and
`noPrebuilt`; `createImportedEnvironmentSeq`, `createImportedDiscrTree`, `findImportMatches`,
`findMatchesExt` and `findMatches` all gain a `prebuilt` parameter (defaulted to `noPrebuilt`, so
no existing call site changes), and `createImportedDiscrTree`/`findMatches*` gain an optional
`onPreTree` callback used to write the cache file. `InitEntry.fromExpr` and `mkSubEntry` build
`.pending` entries; `mkSubEntry` now throws if handed a deferred one.

For a module with entries, the task pushes them into the `PreDiscrTree` with **no `MetaM` work at
all**; a module without them is walked as before. Task order and per-module order are unchanged,
so every root array is the same sequence of `(name, mod)` values as stock's.

### 3.3 The cache file, and why it exists

Mathlib could not be rebuilt on the macOS machine (12 GB free; a second `.olean` set is 11 GB), so
the same entries can also come from a per-closure cache file: `deriveCache` walks the finished
`PreDiscrTree` — every root array in order, each entry appended to its module's list — and saves
it as a compacted region keyed on the module names and the sizes/mtimes of all `.olean` files.
Re-inserting the lists module by module rebuilds every root array in the same order. Stored
entries take precedence over cached ones per module; modules with neither are walked.

**This is a prototype device.** It is what the macOS numbers below were measured through, and the
Linux rebuild showed it is unnecessary once the entries are in the `.olean`s (§5.3).

## 4. Why it is correct

**The stored keys are the keys the walk would have computed.** `recordModuleIndex` calls the same
`addImport`, on the same constants, in the same order, in the same transparency setting. The
filters that depend only on the defining module are applied at record time; the one that depends
on the importing environment is re-applied at import time.

**The deferred entries evaluate to the same keys.** `evalLazyEntry`'s deferred branch calls the
same functions (`getConstVal`, `forallTelescope`, `rootKey`, `pushArgs`) on the same terms in the
same context, and its `MatchM` evaluation runs in the calling `MetaM`'s context under
`withReducible`, exactly as stock's evaluation of pending entries does.

**The order is preserved by construction, not by luck.** This matters more than the keys: `simp`
and library search choose among equally scored candidates in insertion order. Task order and
per-module order are unchanged, and the cache path's derivation is order-preserving by
construction (§3.3).

**One case is *more* deterministic than stock, and one is a real difference.** Stock computes its
process-global tree in whatever context the first `exact?` happens to run in — `Meta.Config`,
options, and any `attribute [local reducible]` in the root file. The stored keys are computed in
a fixed reducible context at module-write time. So a root that sets `attribute [local reducible]`
before its first `exact?` is the one situation where a stored key can differ from a key stock
would have computed. This makes the prebuilt index *more* predictable, but it is a difference,
and it is not gated (§6.2).

## 5. How it was verified, and what that does not cover

### 5.1 Equivalence

`eq-goals.lean`: **29 goals** — `exact?` ×16 (including one that fails), `apply?` ×6 (four
closable, two partial), `rw?` ×6 (two of them producing ~300-line suggestion lists). Stock output
is **5,352 lines and 661 `Try this` lines**, and the *order* of the candidates is part of the
comparison.

Byte-identical stdout, stderr and exit code against stock for every configuration: the
kind-lookup fix alone on two toolchains under `LEAN_LAZY_PARTS=all` and `LEAN_LAZY_PARTS=0`; the prebuilt path
with `LEAN_SEARCH_INDEX=0` (the walk on the new entry type), with a warm cache, with a warm cache
plus lazy loading, and with genuine `.olean`-stored entries for 1,461 modules.

Plus the whole fork's 26-case suite, in which this file is included.

Worth stating explicitly: with lazy part loading active, stock's candidate order is matched **by
construction**, not just on these tests — lazy loading walks `header.moduleData` in the exported
(name-sorted) order, while the prebuilt entries are recorded in `foldStage2` order, which is
stock's.

### 5.2 What the verification does not cover

* **`--stats` gains an extension** (361 → 362) and the fork's `.olean`s are no longer
  graph-identical to stock's, because they carry the index entries. Both are expected and both
  are visible in the equivalence summary as its only differences.
* **A constant whose key computation throws** is logged by the stock walk
  (`Processing failure with …`) at the user's first `exact?`; with stored entries it is silently
  absent. None occur in Mathlib, but "none occur in Mathlib" is the whole of the evidence.
* **`rw?` still walks.** `Lean.Meta.Tactic.Rewrites`'s `addImport` uses `forallTelescopeReducing`
  and `whnfR` on the body and different name filters, so it needs its own entry kind — the same
  mechanism, about 40 lines, not written. Under lazy loading it does benefit from the first half.
* **No Windows, and no measurement of several processes sharing one stored index.**

### 5.3 Two things the Linux rebuild settled

Mathlib **was** rebuilt with this on Linux/arm64 (2026-08-31), and it answers the two questions
the macOS caveat left open.

* Stored entries cover **9,184 of the 10,498 modules** of the `import Mathlib` closure (480,492
  entries; the other 1,314 modules have no eligible constant).
* **The `.olean` path is faster than the cache path**: first `exact?` **+0.12–0.22 s /
  +0.13–0.18 GB / 5.5–6.3 s CPU**, against +0.52–0.87 s / 6.2–6.7 s CPU for the cache.
* **The 51 MB per-closure cache file becomes unnecessary**: adding it on top of stored entries
  changes RSS by under 10 MB and costs 0.1–0.3 s of wall.
* Disk cost: **+1.57 %** on Mathlib's `.olean` parts (+1.81 % on the toolchain library's) — 75
  bytes per entry — with `.olean.private` and `.olean.server` unchanged to within a word per
  module. Recording costs **+1.5 % of build CPU**.
* 26/26 byte-identical, under both `LEAN_LAZY_PARTS=0` and `LEAN_LAZY_PARTS=all`.

## 6. Measured effect

### 6.1 The first half, on the command line

`import Mathlib` alone versus the same plus one `exact?`, best of 3 interleaved, `/usr/bin/time
-l`. `Δ` is relative to the import run of the same binary. Under `LEAN_LAZY_PARTS=all`:

| `exact?` goal | before → after |
|---|---|
| trivial `True` | Δwall **+21.0 → +5.8 s**; CPU **192 → 41 s**; peak RSS 5.46 → 4.72 GB |
| `a + b = b + a` | +21.1 → **+5.8 s**; 188 → **40 s** |
| `0 < a ^ 2` | +21.0 → **+5.8 s**; 191 → **41 s** |
| `m ∣ n → 0 < n → m ≤ n` | +20.9 → **+5.9 s**; 189 → **42 s** |

With `LEAN_LAZY_PARTS=0`, before and after are the same (+4.7–4.8 s, ~26 s of CPU): the walk itself is
untouched by this half.

### 6.2 Both halves, on the command line

Best of 3 interleaved, load 3.5–8. `walk` = `LEAN_SEARCH_INDEX=0`; `cache` = a warm 51 MB cache
file for the Mathlib closure.

| case | config | wall s | Δwall vs import | user s | max RSS GB | ΔRSS |
|---|---|---|---|---|---|---|
| `import Mathlib` | stock | 21.1 | | 1.9 | 5.68 | |
| | walk / cache | 5.4 / 3.5 | | 1.7 / 1.6 | 2.76 / 2.76 | |
| | cache + `LEAN_LAZY_PARTS=all` | 3.0 | | 1.6 | 1.64 | |
| `True` | stock | 15.4 | +5.7 | 24.3 | 7.88 | +2.20 |
| | walk | 11.2 | +5.7 | 27.3 | 5.81 | +3.05 |
| | **cache** | 4.2 | **+0.74** | **6.0** | 2.91 | **+0.15** |
| | cache + `LEAN_LAZY_PARTS=all` | **4.4** | +1.37 | 15.0 | **1.81** | +0.17 |
| `a + b = b + a` | stock → **cache** | 25.8 → **4.3** | +4.7 → **+0.81** | 24.9 → **7.1** | 7.88 → 2.94 | +2.20 → **+0.18** |
| `0 < a ^ 2` | stock → **cache** | 25.1 → **4.3** | +3.9 → **+0.86** | 24.1 → **6.6** | 7.89 → 2.95 | +2.20 → **+0.19** |
| `m ∣ n → …` | stock → **cache** | 24.9 → **4.3** | +3.8 → **+0.87** | 26.4 → **6.4** | 7.89 → 2.95 | +2.20 → **+0.19** |

So: the first `exact?` of a process costs **0.7–0.9 s of wall and +0.15–0.2 GB** instead of
3.8–5.7 s and +2.2–3.1 GB, and 6–7 s of CPU instead of 24–28 s. Combined with lazy loading, the
whole process — `import Mathlib` plus a first `exact?` — is **4.4 s and 1.86 GB**, against stock's
25 s and 7.9 GB on this machine.

### 6.3 In the language server

Three recorded novice editing sessions driven through `lake serve`, 2 repeats each, hybrid
toolchains (fork binaries, **stock** Mathlib `.olean`s), quiet machine. The `exact?` step is the
slowest edit of each session.

| toolchain | `exact?` latency | worker RSS before → after | peak worker RSS | Lean wait / session |
|---|---|---|---|---|
| stock | 4.65–4.79 s | 5.72 → 7.90 GB | 7.99–8.00 GB | 22.7–24.5 s |
| lazy loading, before this change | **17.1–17.2 s** | 2.1 → 7.4 GB | 7.5 GB | 28 s |
| + the first half only | **5.9–6.1 s** | 2.1 → 4.8 GB | 5.5 GB | 16.8–17.4 s |
| **+ both halves** | **1.32–1.38 s** | 2.1 → 2.7–2.9 GB | **3.75 GB** | **12.0–13.0 s** |

### 6.4 In the finished fork

Switching only this off (`LEAN_SEARCH_INDEX=0`) costs **+4.64 s of wall (+121 %), +2.96 GB of RSS
(+189 %) and +30.9 s of CPU** on `import Mathlib` + one `exact?` — the largest single loss of the
six. At import it is worth **nothing at all** (+0.01 s), exactly as designed.

Because the macOS oleans carry no stored entries, that measurement is through the cache path,
which the Linux rebuild showed to be the slower of the two. **This change's value is, if
anything, understated here.**

## 7. What is unfinished, provisional or known to be wrong

1. **On macOS the `.olean` half was never exercised at scale.** `mathlib4-opt` was compiled by a
   binary that does not contain `indexExt` at all, so no `.olean` in either macOS set carries an
   entry, and every macOS number above comes from the cache file. The `.olean` path was validated
   on Lean's own 1,039 modules (stage 2 builds them with the extension present) plus 428
   recompiled Mathlib files into a shadow `.olean` root (1,461 modules, 111,860 entries), where it
   behaved exactly like the cache — same data, different container — and then at full scale on
   Linux (§5.3). But the *shipped macOS story* is the cache file, and the shipped design is the
   stored entries.
2. **The context-independence gate is designed and not implemented.** §4's last paragraph
   describes the one case where a stored key can differ from a key stock would compute. The
   mitigation is to gate the prebuilt path on the calling environment's reducibility state
   matching the build-time one — cheap to test (the root's local `reducibilityAttrs` entries) —
   after which the change becomes strictly-faster-or-equal with byte-identical suggestions **in
   every case**, rather than in every case measured. The same gate would cover a non-default
   `Meta.Config`. It was not written.
3. **`rw?` is not covered** (§5.2).
4. **Import-failure messages are silently absent** with stored entries (§5.2).
5. **The cache file is a per-closure side cache** with the same shape, and the same unanswered
   policy questions, as the lazy-loading index directory — except that it has **no bounds at all**
   (no free-space floor, no size cap, no eviction). It is 51 MB per closure. The Linux result says
   it should not exist in a shipped version; today it does, and a project presenting many closures
   would accumulate it.
6. **`Key` values are stored as objects.** A packed encoding (name plus arity in one object,
   shared per module) would take the disk cost from ~1.6 % to about 1 %.
7. **The fork changes what `.olean` files contain.** This is the one improvement in the fork that
   does, and it is why the fork's `.olean`s are not graph-identical to stock's. It does not change
   the *format*, so a stock `lean` reads them.
8. **Upstream shape.** The first half is a one-idea change (`findKindNoLoad?` for every kind test
   on the hot path of a lazy-loading environment — `isInductiveCore`, `isStructure`, …). The
   second is the library-search instance of "persist an eager index in mappable form": with the
   deferred entry form the tree needs only names and keys, which is exactly what a
   discrimination-tree-shaped `.olean` section could hold directly, and that would also remove the
   remaining 0.4–0.8 s of per-process assembly of 770k entries into root arrays.
