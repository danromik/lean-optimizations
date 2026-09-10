# Improvement 6 — the tactic index image

_Against Lean `v4.33.1` (`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`). Part of
[`patches/lean4-v4.33.1-optimized.patch`](../../patches/lean4-v4.33.1-optimized.patch).
Platform-independent in principle; measured on macOS only. Switches: `LEAN_TACTIC_INDEX=0` (off — stock
behaviour), `LEAN_TACTIC_INDEX_DIR=<dir>`, `LEAN_TACTIC_INDEX_WRITE=1` (build and write the image, then carry
on), `LEAN_TACTIC_INDEX_VERBOSE=1`, `LEAN_CACHE_STATS=<file>`._

> **Read §1.1 first.** This was a cache until 2026-09-01. It is not one any more: **writing is an
> explicit act by a distributor, reading is automatic, and nothing is written at run time.** The
> earlier cache design is superseded here, and it is also the design that is still in the shipped
> **Linux** patch — see §7.1, which is the most important caveat in this document.

---

## 1. The problem

`finalizeImport` → `finalizePersistentExtensions` runs every persistent extension's
`addImportedFn` at every start. For the *eager* ones this rebuilds an in-heap index from data
that has not changed since the last start.

Measured in situ, from inside `finalizePersistentExtensions`, for `import Mathlib` on a fork that
already has the other five improvements:

| extension | imported entries | `addImportedFn` | can it be imaged? |
|---|---|---|---|
| `Lean.Parser.parserExtension` | 117,414 | **380.0 ms** | **no** — holds `ParserFn` closures |
| `Lean.Meta.simpExtension` | 99,669 | **175.2 ms** | yes (123.9 MB compacted) |
| `Lean.Meta.instanceExtension` | 43,527 | 39.7 ms | yes (26.7 MB) |
| `Lean.Elab.macroAttribute` | 1,748 | 26.8 ms | no (closures) |
| `Mathlib.Meta.FunProp.functionTheoremsExt` | 2,300 | 19.2 ms | yes |
| `Mathlib.Meta.NormNum.normNumExt` | 85 | 15.7 ms | no (closures) |
| `…appUnexpanderAttribute` | 953 | 15.0 ms | no (closures) |
| `Mathlib.Meta.Positivity.positivityExt` | 117 | 13.0 ms | no (closures) |
| `…Specialize.specCacheExt` | 37,710 | 6.5 ms | yes (42.5 MB) |
| `Lean.Meta.Grind.grindExt` | 13,348 | 5.0 ms | yes |
| `Lean.Meta.Match.Extension.extension` | 26,842 | 3 ms | yes |
| `Mathlib.Tactic.ToAdditive.translations` | 25,422 | 0 ms | yes |
| … 316 more | 5.1 M | 82 ms | mixed |
| **all 334** | **5.4 M** | **838.7 ms** | 93 imaged |

So `finalizePersistentExtensions` is **839 ms of a 2.70 s `import Mathlib`** — 31 % of what is
left after the other five improvements. Of that, the parser is 45 % and cannot be taken (§7.4);
the 93 extensions this change covers account for the ~390 ms of user time §6 measures as the
saving. The compactable states together are **217 MB of heap that every process rebuilds at every
start**.

One correction to the folklore, measured here: `reducibilityCore` (87,515 entries) is **not**
eager in v4.33.1 — its `addImportedFn` costs 0 ms and produces a 96-byte state. Nor are
`backwardDefeqAttr`, `defeqAttr` or `protectedExt`. The eager set is smaller than community
discussion suggests.

### 1.1 Two designs were rejected before this one

**Per-module `.olean` sections that are merged at import.** The natural reading of "mappable
index", and it fails on the merge: a `DiscrTree` is a trie whose leaves are arrays *in insertion
order*, and `simp` picks among equally scored candidates by that order, so merging 10,498
per-module trees must reproduce the global insertion order exactly. That is achievable — insert
module by module in `ModuleIdx` order — but it saves nothing, because the cost of `addImportedFn`
**is** the 99,669 insertions and a merge performs the same insertions. It also changes the
`.olean` format.

**Lazy: build the index on first use of the tactic.** Implemented first, and killed twice over.
It only *moves* the cost — a file that calls `simp` pays 156–240 ms at its first `simp` instead of
at start-up — and it cannot be made to work in a bootstrapping compiler: discriminating "is this
extension-state slot a deferred thunk?" needs either a new runtime tag test or a global side
table, and both are evaluated by code that the **stage-0** compiler runs *interpreted* while it
elaborates stage 1. Stage 0 has neither the new C symbol (its runtime is a committed snapshot
under `stage0/src`) nor the new `builtin_initialize` constant. Three variants were built and all
three failed in the same place, `Lean.Parser.Term.Basic`.

A residue of that dead design is still in the shipped patch: see §7.5.

## 2. What the change does

### 2.1 The shape

```
                     an ordinary run                 the distributor, once
                     ──────────────                  ─────────────────────
  compute the key    yes                             yes
  look for the image yes                             yes (and stops if it is already there)
  map and install    if it is there                  n/a
  probe 334 exts     never                           yes  (~10 s)
  write 212 MB       never                           yes
  stamp / evict      never                           never
```

An ordinary run creates no directory, stamps no file and evicts nothing. If the image is absent,
mismatched or unreadable, the import rebuilds the indexes exactly as stock Lean does.

### 2.2 The code — `src/Lean/Environment.lean` only

| identifier | what it is |
|---|---|
| `tacticIndexNeverDefer` | the denylist — `regularInitAttr`, `builtinInitAttr`, `parserExtension`, `macroAttribute`, `termElabAttribute`, `commandElabAttribute`, `tacticElabAttribute`, `attributeExtension`. Never probed, so a 117k-entry state is not compacted just to have the attempt fail |
| `tacticIndexImageVersion` (1), `tacticIndexMinStateBytes` (4096) | format version, and the floor below which a state is not worth mapping |
| `structure TacticIndexImage` | `{ key : UInt64, moduleNames : Array Name, extNames : Array Name, states : Array EnvExtensionState }` — the root object of the file |
| `tacticIndexRegions` | a process-global `IO.Ref (Array CompactedRegion)` keeping mapped images alive. Deliberately **not** added to `EnvironmentHeader.regions`, which is the dependency list used on both sides and must be identical |
| `tacticIndexKey moduleNames regions extEntryCounts` | the closure key (§2.3) |
| `tacticIndexImageDirOf modules` | the default directory: the parent of the `.olean` of the closure's topmost module |
| `tacticIndexDepsPath` / `tacticIndexWriteDeps` / `tacticIndexReadDeps` | the dependency sidecar (§2.4) |
| `tacticIndexLoadImage` / `tacticIndexSaveImage` | `CompactedRegion.read`/`save` wrappers; the writer verifies and retries (§2.5) |
| `finalizePersistentExtensions` | one extra parameter `(image : Std.HashMap Name EnvExtensionState)` and **three lines**: if the extension has an entry in the image, `setState env { s with state := st }`; otherwise the stock `addImportedFn` call |
| `finalizeImport` | the key/read block before the loop, and the write block after it, both behind `LEAN_TACTIC_INDEX` |

`importedEntries` is untouched, so `getModuleEntries`, `exportEntriesFn` and `--stats` see exactly
what they saw before.

### 2.3 The key

A hash of: the module names in `ModuleIdx` order; the path **and size** of every region in
`EnvironmentHeader.regions` (the size from this process's own `fstat`, so it is current); and the
number of imported entries every extension registered so far sees. It is computed **before**
`runInitAttrs` registers the user extensions, so the writing and the reading process agree on
which extensions it covers. It is both the file name (`exts-<key>.tacticindex`) and a field inside the
image, so a file put under the wrong name is rejected rather than installed.

The per-extension entry counts are what distinguish a `LEAN_LAZY_PARTS=all` import (10,498 regions,
entries patched from the lazy-loading index) from a `LEAN_LAZY_PARTS=0` one (52,490 regions).

### 2.4 The dependency sidecar, and why it exists

`CompactedRegion.save`'s `depRegions` is what keeps the image small: `SimpTheorem`s, `Expr`s and
`Name`s that already live in an `.olean` are referenced, not copied. The states' own graph is
217 MB and the image is 212 MB; the difference **is** the shared objects.

But `region_reader` takes its fast path — no pointer fixup, not one page of the image touched —
only if the image **and every dependency region** are at their saved addresses. A Mathlib import
has ~38 of 10,498 regions that collide and fall back to `read()` + relocate, and a single such
dependency forces a full relocation walk over the whole image. That is not hypothetical; the
first working version measured it:

| dependency handling | wall | user | max RSS | page reclaims |
|---|---|---|---|---|
| control (`LEAN_TACTIC_INDEX=0`) | 3.18 s | 1.89 s | 1.66 GB | 177,150 |
| all regions as dependencies (relocation walk) | 2.83 s | 1.50 s | 1.60 GB | 186,440 |
| **only the regions at their saved address** | **2.68 s** | **1.41 s** | **1.39 GB** | **160,650** |

The relocation walk cost 0.15 s and, worse, copy-on-write-dirtied ~150 MB of the mapping, so the
memory saving nearly vanished and the fault count went *up*.

So only regions with `CompactedRegion.isMemoryMapped` are used as dependencies, and objects the
states hold in the other ~38 are copied into the image. The reader must use *exactly* the same
dependency list, and must build it **before** it can read the image — hence a one-byte-per-region
sidecar `exts-<key>.tacticindex.deps` (10,498 bytes for Mathlib).

### 2.5 Writing

Only when `LEAN_TACTIC_INDEX_WRITE` is set, and only if the read found nothing (so it is idempotent).
Every extension outside the denylist whose `importedEntries` are non-empty is **probed**: its
state is compacted alone into a temporary file, and it is kept if the compactor accepts it and
the result is ≥ 4 KB. Probing rather than naming the extensions is what lets this cover **user**
extensions — Mathlib's `to_additive` table, its Aesop rule sets, its `@[simp]`-style attribute
sets — without knowing anything about them, and it is what guarantees the combined save cannot
fail.

Then `tacticIndexSaveImage` saves under `` `_tacticIndexImage0 ``, writes the sidecar, and **reads the image back
through the reader's own path**; if that fails it retries under `` `_tacticIndexImage1 ``, up to eight
times, and deletes the file rather than leave an unusable one behind. Why, in §3.3.

### 2.6 Where the image lives

The directory holding the `.olean` of the closure's topmost module — `.lake/build/lib/lean` for a
Lake package. `LEAN_TACTIC_INDEX_DIR` overrides it for reading and writing alike.

Four reasons: an image is valid for exactly one (toolchain, library revision) pair, which is what
an `.olean` cache is keyed on too, so it belongs where the `.olean`s are and travels with them
through `lake exe cache`; reader and writer compute the directory from the same closure with no
configuration and no search; it scopes the artefact to the library at the top of the stack, which
is the project whose CI would build it; and `rm -rf .lake` takes it with it, which a
`$HOME/.cache` directory never did.

The cost is that a 212 MB file sits in a directory of `.olean`s. It is inert — nothing globs
`*.tacticindex`, and Lean only ever looks for one exact name — but it is 212 MB in a tree whose size
people watch, and a distributor should decide deliberately whether to ship it.

## 3. Why it is correct

### 3.1 The installed state is the state the fold would have produced

The chain is mechanical rather than argued:

* `addImportedFn` of every imaged extension is a fold of a **pure** `addEntry` over
  `importedEntries`. This is checked per extension, and it is checked *by the mechanism itself*:
  the extensions that consult the environment — the parser, the `KeyedDeclsAttribute` tables,
  `norm_num`, `positivity` — are exactly the ones the compactor rejects, so they are never imaged.
* The image holds the compacted image of the value that fold produced, in a process whose
  `importedEntries` were the same (the key covers the module list, every region's identity and the
  per-extension entry counts).
* **Compaction preserves the object graph**: same constructors, same field order, same array
  orders. A `DiscrTree`'s trie shape and every per-key candidate array are therefore identical —
  which is the property `simp`'s choice among equally scored lemmas depends on.
* Nothing else about the environment changes: `importedEntries` are the live ones, the extension
  list, the order of `finalizePersistentExtensions`, `runInitAttrs` and the second
  `markPersistent` are untouched.

### 3.2 A stale image cannot be used

* The image's dependency regions **are** the closure's `.olean` regions; `CompactedRegion.read`
  refuses an image whose dependencies are not the ones it was saved against.
* The key covers every region's path *and size*. A rebuilt library changes sizes, so it changes
  the key, so the image is not even looked for — a different name is.
* The key is stored inside the image as well as in its name, and re-checked.
* **Every rejection path falls through to `addImportedFn`.** The failure mode is a slow correct
  run.

The residual risk is the key, not the mechanism: it is **metadata, not content**. Two `.olean`
sets agreeing on module names, every path and size, and every per-extension entry count would
collide. §7.2.

### 3.3 Two defects the verification found, both fixed here

Both were inherited unchanged from the cache version, and neither could have been found without
asking a *shipped* artefact to survive being copied, renamed and damaged — which is not a
question a self-written cache invites.

**An image without its sidecar was used anyway.** `tacticIndexReadDeps` returned `#[]` when the sidecar
was missing or the wrong length — indistinguishable from "no region was memory-mapped". Passing
`#[]` to `CompactedRegion.read` then **succeeds**, because the fast path performs no pointer fixup
at all: if the image and every dependency happen to be at their saved addresses, the cross-region
pointers are already right and nothing notices. They are right only by accident. One relocated
dependency — a Mathlib import has ~38 — and the installed `DiscrTree` would hold pointers into
the wrong region. `tacticIndexReadDeps` now returns `Option`, and `none` means the image is not used.

**The saved base address is a hash of a name, and can collide unrecoverably.**
`compacted_region_save_core` derives a region's saved base address from
`name(mod, true).hash() % 0x7f0000000000`, a fixed function of the key name the caller passes. If
the resulting 212 MB range overlaps the saved range of any dependency,
`region_reader::sort_and_validate_dep_regions` throws — *for every reader, for ever*, because the
check is on saved addresses only. This was found the hard way: renaming the save key
during development moved the base address onto a Mathlib `.olean`'s saved range and the image
became unreadable in every process. With ~10,500 dependencies and a 212 MB image
the exposure is on the order of one name in sixty — small, permanent, and silent under the old
design (the reader missed, the writer rewrote the same unreadable file, and the only symptom was
that the cache never hit). Hence the write-back verification and retry of §2.5.

## 4. How it was verified, and what that does not cover

**Equivalence.** The 26-case suite, byte-identical stdout/stderr/exit code against stock `lean`
v4.33.1 with stock `.olean`s: `cases: 26/26 identical`, `overall: EQUIVALENT`. The suite's
pre-warm lines record that it really ran on the image path
(`tactic-index: image …/exts-761946790491372349.tacticindex (used, 93 extensions, mmap=true)`), so the 40 `simp?`
calls, the 28 `#synth` queries and the 29 library-search goals were answered out of the mapped
`DiscrTree`s, not rebuilt ones. **The `simp?` file is the load-bearing test**: `simp?` prints the
names of the lemmas the simp set selected *and the order in which they fired*, which is exactly
what a changed insertion order would move.

**Nothing is written at run time.** A marker file, one run, then `find -newer` over `$HOME/.cache`,
the Mathlib tree, the toolchain's `stage2`, the scratch tree and `/tmp`. In every arm — image
present, no image, `LEAN_TACTIC_INDEX=0`, and with an `exact?` — **nothing from this feature**, including
the "no image" arm, which under the cache design is precisely the case that wrote 212 MB.

**A from-source-shaped build writes nothing.** 250 leaf modules each importing `Mathlib.Tactic`
plus one trivial module of its own, so each presents a distinct Mathlib-sized closure; six at a
time, 222 s. Result: 250 distinct closures, 250 lookups, 250 misses, **0 images, 0 bytes, no
files created anywhere**. The same 250 closures under the cache design would have written
~19–30 GB.

**A stale or damaged image is rejected.** Each arm's stdout, stderr and exit code compared byte
for byte against the same case run with no image at all:

| the image in the directory | disposition |
|---|---|
| the right one | **used**, output identical |
| built for a different closure, renamed onto this key | ignored (key mismatch), identical |
| first 4 KB zeroed | ignored (unreadable), identical |
| sidecar truncated to 5,000 of 10,498 bytes | ignored, identical |
| sidecar deleted | ignored, identical |
| **truncated to 100 MB of 212 MB** | **not detectable** |

**What this does not cover.**

* **A truncated image is undetectable.** The compacted-region format has no length field and no
  checksum, so a truncated file is mapped and whether it hurts depends on whether anything reaches
  a page past the end: across runs this both completed normally with identical output and died on
  SIGBUS. That is the format's behaviour, not something the image adds — a `.olean` truncated the
  same way was measured killing **stock** `lean v4.33.1` (SIGSEGV) and the fork (SIGBUS) on the
  same file. A distributor ships an image the way `.olean`s are shipped, with whatever integrity
  the transport provides.
* **Only macOS, and only two cases.** No Linux run of the redesigned form (§7.1), no
  language-server measurement of it, and **no measurement of several processes mapping one image
  at once** — which is where a `lake build` with 12 workers should show a genuine 12× memory
  saving over 12 heap copies, since the mapping is `MAP_PRIVATE` but nothing writes to it.
* **No test asserts that the denylist is right.** It is a name list; an extension added to it
  wrongly would silently lose the saving, and one omitted from it is protected only by the
  compactor rejecting its state.

## 5. Measured effect

Three arms of the **same binary**, two cases, 5 repeats interleaved by case, medians, one `lean`
at a time, load 1.9–5.7.

| case | arm | wall s | user | sys | max RSS GB | page reclaims |
|---|---|---|---|---|---|---|
| `import Mathlib` | **image present** | **2.36** | **1.22** | 1.04 | **1.39** | **160,682** |
| | no image | 2.75 | 1.62 | 1.03 | 1.64 | 176,171 |
| | `LEAN_TACTIC_INDEX=0` | 2.75 | 1.62 | 1.04 | 1.64 | 175,967 |
| + one `exact?` | **image present** | **3.77** | 14.05 | 1.19 | **1.57** | **171,549** |
| | no image | 4.15 | 14.68 | 1.21 | 1.82 | 187,454 |
| | `LEAN_TACTIC_INDEX=0` | 4.16 | 14.63 | 1.19 | 1.82 | 187,503 |

**Cost of having no image**: `import Mathlib` **+0.39 s / +0.25 GB / +15,489 faults**; with an
`exact?` **+0.38 s / +0.25 GB**. The independently run ablation of the whole fork recorded
+0.41 s / +0.28 GB and +0.42 s / +0.26 GB, so the redesigned form reproduces the cache's benefit.

The second thing the table says: **with no image, the feature is switched off.** 2.75 vs 2.75 s,
1.64 vs 1.64 GB. The read attempt is one `stat` of a file that is not there.

The user-time saving is the `addImportedFn` work no longer done. The *fault* count falls because
217 MB of in-heap index becomes 212 MB of mapped file that nothing reads.

**What building the image costs**, same machine and closure: **12.44 s wall, 2.83 GB peak RSS**,
against 2.75 s / 1.64 GB for the same import without it — so about 10 s and 1.2 GB, once, by
whoever ships. Output: 212,133,024 B plus a 10,498-byte sidecar, 93 extensions.

**Earlier figures, for context**, from the cache-era measurements on the same mechanism: with the
other improvements but before the library-search index, `import Mathlib` went 2.68 → **2.23 s**
(−17 %), user 1.59 → **1.20 s** (−25 %), RSS 1.64 → **1.37 GB** (−16 %), faults −9 %; a `module`
root 2.90 → 1.94 s; `import Mathlib.Tactic` 1.67 → 1.29 s. In the language server the header
dropped 6.10 → 5.69 s and the worker's resident set right after it 1.91 → 1.48 GB.

**The language-server figure has since been re-measured under the shipped design and it holds.**
Those numbers were taken when the worker wrote its own cache, so they needed re-checking now that
it cannot. Same binary, novice session under a fixed `import Mathlib`, three repeats per arm,
`--prewarm`:

| | header s (3 runs) | median | worker RSS GB |
|---|---|---|---|
| **image present** | 5.76, 5.71, 5.67 | **5.71** | 1.53, 1.55, 1.56 |
| image absent | 6.28, 6.02, 6.24 | 6.24 | 1.89, 1.87, 1.85 |
| `LEAN_TACTIC_INDEX=0` | 6.12, 5.98, 6.02 | 6.02 | 1.85, 1.88, 1.89 |

No run with the image is slower or heavier than any run without it. The worker mapped an image a
command-line `lean` had written into the same checkout — see §7.7, which also withdraws the
belief that a server worker gets a different key.

## 6. What a distributor does

For Mathlib, three lines in CI after the `.olean`s are built and before they are packed:

```sh
echo 'import Mathlib' > /tmp/closure.lean
lean /tmp/closure.lean                                # pass 1: warms the lazy-loading index
LEAN_TACTIC_INDEX_WRITE=1 lean /tmp/closure.lean               # ~12 s, writes .lake/build/lib/lean/exts-<key>.tacticindex
# ship .lake/build/lib/lean/exts-*.tacticindex and exts-*.tacticindex.deps with the oleans
```

> **Corrected 2026-09-01 (evening).** This recipe previously said `LEAN_TACTIC_INDEX_WRITE=1 lake env lean
> …`, and that **does not work** unless the project's `lean-toolchain` already names the fork.
> `lake env` runs the toolchain the project names; in a Mathlib checkout pinned to
> `leanprover/lean4:v4.33.1` it runs **stock** Lean, which ignores `LEAN_TACTIC_INDEX_WRITE`, writes
> nothing and reports nothing (verified: `lake env printenv LEAN_SYSROOT` names the stock
> toolchain there). In real CI the library *is* built by the toolchain it pins, so `lake env lean`
> is right there — but anyone building an image against a downloaded olean cache must run the fork
> binary with an explicit `LEAN_PATH`, as `release/scripts/repro-import.py` does. The first pass
> was also missing: without it the image is keyed on a cold lazy-loading index and is never found
> again.

Points a distributor has to know:

* **One image per closure, and the closure includes the reader's configuration.** `LEAN_LAZY_PARTS=all`
  and `LEAN_LAZY_PARTS=0` present different region lists and therefore different keys, as do a
  `lake serve` worker's `--setup` artefacts and a plain CLI import. An image built under one
  configuration is **invisible** — not wrong, invisible — under another. `LEAN_CACHE_STATS` on a
  real user's session is how you find out which closures are worth building.
* **Build it warm.** The first import of a closure builds the lazy-loading index, which changes
  the region list and hence the key. Run the closure twice before writing, or the image will be
  keyed on a configuration nobody will present again.
* **Writing is idempotent.** With the image in place `LEAN_TACTIC_INDEX_WRITE=1` finds it and does nothing.
  To rebuild, delete the file.
* **Check the exit path.** If no readable image could be written (§3.3, eight collisions in a
  row), `lean` says so on stderr and leaves no file. That is the one case worth failing CI on.
* **Ship the sidecar.** It is 10 KB and the image is unusable without it — safely unusable, since
  §3.3, but unusable.
* **It is optional at every point.** A user without the image loses 0.39 s and 0.25 GB and
  nothing else, and no user can be worse off than a user of stock Lean.

## 7. What is unfinished, provisional or known to be wrong

### 7.1 The shipped Linux patch still contains the superseded cache design

[`patches/lean4-v4.33.1-optimized-linux.patch`](../../patches/lean4-v4.33.1-optimized-linux.patch)
is an **older revision** of this feature, and this needs saying plainly because
[`patches/README.md`](../../patches/README.md) describes the two patches as "the same six
changes".

In the Linux patch: the image is written automatically on every miss, into
`$HOME/.cache/lean-tactic-index`; the switches are `LEAN_TACTIC_INDEX_CACHE_DIR`, `LEAN_TACTIC_INDEX_EXTS` and
`LEAN_TACTIC_INDEX_TIMING` (there is no `LEAN_TACTIC_INDEX_WRITE` and no `LEAN_TACTIC_INDEX_DIR`); there are **no
bounds at all** on the lazy-loading index directory (no `lean_io_free_disk_space` primitive, no
`CacheBound`, no cap, no eviction — the Linux patch does not touch `src/runtime/io.cpp`); and
`tacticIndexReadDeps` returns `Array` rather than `Option`, i.e. **the missing-sidecar defect of §3.3 is
present and unfixed**, as is the base-address collision (no write-back verification, no retry).

**Resolved 2026-09-01.** The Linux patch has been regenerated from a branch that merges the Linux work with this redesign — the two touch disjoint files (`object.cpp` against `Environment.lean` and `io.cpp`), so the merge is mechanical — and now carries the same design as the macOS patch: 16 files / +2,132 / −82 as of that date, verified to apply to a clean `v4.33.1`. (It is 16 files / +2,150 / −82 today; the posix_spawn coupling landed afterwards.) The description below is retained as the record of what was wrong.

Concretely, before the fix: 15 files / +1,830 / −82, against the macOS patch's 16 files / +2,101 / −82, and the

Anyone applying the Linux patch is getting the design this document supersedes.

### 7.2 The key is metadata, not content

Unchanged from the original design, and now **load-bearing in a way it was not** when the image
was self-written: a shipped artefact is presented to processes that never built one. Two `.olean`
sets agreeing on module names, every path and size, and every per-extension entry count would
collide and a stale image would be installed. Storing the data in the `.olean`s removes the
question, and that is what a production version does.

### 7.3 The base-address retry is a workaround, not a fix

§3.3 makes the writer refuse to ship an unreadable image. It does not stop `CompactedRegion.save`
from choosing addresses by hashing a name. An upstream version would allocate the region's address
against the dependency ranges it was given.

### 7.4 `parserExtension` is untouched

The largest single `addImportedFn` — 380 ms in situ, 45 % of the whole extension loop. Its state
holds `ParserFn` closures, which the `v2` compacted format rejects, and it cannot be deferred
either because the state is needed to parse the *next* command. The same applies to
`macroAttribute`, the elaborator and delaborator tables and `norm_num`/`positivity` (~90 ms
together). The experimental `v3` `allowClosures` format is the obvious follow-up; it requires the
loading process to use byte-identical executables, which is plausible for a per-toolchain artefact
and has not been tried.

### 7.5 One dead C helper is in the patch

`lean_ext_state_is_thunk` in `src/runtime/object.cpp` (and its copy in the binary delta to
`stage0/src/runtime/object.cpp`) is left over from the rejected lazy design of §1.1. Its comment
still describes that design, which no longer exists. It is referenced from no Lean or C++ code and
cannot affect behaviour; removing it would force a stage-0 rebuild, so it was left in the tree at
the commit the measurements were taken from. **A reviewer will find it, and it is disclosed here
rather than left to be found.**

### 7.6 The independent ablation recommended dropping this feature

When the whole fork was measured with one improvement switched off at a time, this was the weakest
of the three that do anything, and at the time it was the only one with an unbounded cost outside
the process: the cache design wrote **254.6 GiB** during a from-source Mathlib build to answer 216
lookups, and the bounds added to stop that had defaults its own measurement disowned (one of them
cost 19 % on a real project's warm rebuild). The recommendation was to drop it, and the note
that **the headline survives losing this improvement** — without it, `import Mathlib` is 2.78 s / 1.65 GB
against stock's 10.46 s / 5.68 GB — still stands.

The redesign answers the *cost* half of that argument completely (§4: 0 bytes written, in every
arm, including the from-source-shaped workload). It does not change the *benefit* half: 0.39 s and
0.25 GB, an order of magnitude below the library-search index on the row that matters most. A
reader deciding whether to keep this should weigh those two facts together, and should know that
the redesign is not a code simplification either: the feature is **241 lines** (203 code, 38
comment) against 241 for the cache version and 211 before the cache was bounded. What did shrink
is the part a reviewer of a patch against Lean cares about: **three lines inside
`finalizePersistentExtensions`**, down from eight lines, one parameter and a configuration record.

### 7.7 Smaller open items

* **The image regions are never freed** (kept in a process-global ref). `Environment.freeRegions`
  would not release them.
* ~~**A `lake serve` worker has its own closure**, hence its own key and its own image (12.6 MB for
  the sessions measured). Under the redesign a worker cannot write one, so a server user gets the
  benefit only if a distributor built an image for the worker's configuration.~~
  **Withdrawn 2026-09-01 (evening): this is not true for an ordinary file.** Measured five ways —
  bare `lean`, `lake env lean`, `lean --setup=<lake setup-file output>`, `lean
  -DElab.inServer=true`, and a real `lake serve` file worker — a legacy (non-`module`) root gives
  **the same key in all five**, and a worker was observed mapping an image a command-line `lean`
  had written (`used, 93 extensions, mmap=true`, nine runs out of nine). The `--setup` artefacts
  cannot change the key: Lake's artefact paths and the `LEAN_PATH` lookup build byte-identical
  strings. The one genuine server-only key is a **`module`-system root**, where the worker
  elaborates at import level `.server` rather than `.exported` — a different key *and* a different
  entry set, servable with one extra flag (`-DElab.inServer=true`) at write time. The 12.6 MB
  figure was a `module`-root worker.
  A far more serious key problem surfaced in the same investigation: the key hashes the **absolute
  path** of every region, including the toolchain's, so an image built by a distributor would miss
  for every user whose checkout is not at the distributor's path.
* **The lazy-loading index directory keeps all three cache bounds**, and their defaults are still
  open. Removing this feature from the cache business did not remove that machinery; it removed
  the case that made it urgent.
