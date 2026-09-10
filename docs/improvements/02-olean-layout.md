# Improvement 2 — the shell-first `.olean` layout

_Against Lean `v4.33.1` (`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`). Part of
[`patches/lean4-v4.33.1-optimized.patch`](../../patches/lean4-v4.33.1-optimized.patch).
Platform-independent. This is a **writer** change: it has no effect until a library is rebuilt
with it. Switches: `LEAN_OLEAN_LAYOUT=0` (write in the stock order — byte-identical output),
`LEAN_COMPACT_DUMP=1` (print the layout to stderr)._

---

## 1. The problem

### 1.1 The mechanism

`object_compactor` (`src/runtime/compact.cpp`) writes a region by a depth-first **post-order**
traversal from the root. `insert_constructor` / `insert_array` first call `to_offset` on every
child — which compacts not-yet-seen children recursively — and then `alloc` the parent's copy
right behind them. Every copy passes through `save_max_sharing` (structural deduplication by
content hash, possible only because a parent's content is final when it is emitted) and `save`
(the pointer-identity table `m_obj_table`).

The root of every part is a `ModuleData` whose pointer fields are, in order: `imports` (0),
`constNames` (1), `constants` (2), `extraConstNames` (3), `entries` (4).

Post-order therefore lays a part out as:

```
imports | the Name objects of constNames | for each constant: ITS TYPE AND VALUE
EXPRESSION TREES, then ConstantVal, then <Kind>Val, then ConstantInfo | extraConstNames |
the extension entries (at the very end of the file)
```

The three small objects at the end of each constant's run — `ConstantInfo` (16 B), its
`<Kind>Val` (32–64 B), its `ConstantVal` (32 B) — are what this document calls the constant's
**shell**. They are the only part of a constant that `finalizeImport` needs, and the compactor
scatters them behind everybody's proofs.

### 1.2 The size of it

Measured with `olean-layout.py`, which parses a part, locates `constNames` and `constants`,
follows `ConstantInfo → <Kind>Val → ConstantVal` for every constant, and reports the byte span
each group occupies (16 KB pages):

| part | pages | constants | shells span, stock |
|---|---|---|---|
| `Init/Prelude.olean` | — | 2,204 | 68 % of the file |
| `Mathlib/Analysis/Calculus/Deriv/Basic.olean` | 27 | 204 | 22 pages, **81.5 %** |
| `Mathlib/Logic/Basic.olean.private` | 62 | 365 | 39 pages, **63 %** |
| `Mathlib/Analysis/Calculus/Deriv/Basic.olean.private` | 33 | 216 | 17 pages |

The `Name` objects, by contrast, sit in 1–2 pages: they are small and emitted in one run.

So "touch one 8-byte object header per constant" faults in ~2.8 GB of the 7.5 GB of mapped
files, for ~100 MB of shells. That is why improvement 3 (no-touch reference counts) helps so
much at import — and it is also why, *after* improvement 3, every later `env.find?` still faults
in a whole 16 KB page holding one shell and 16 KB of somebody's proof.

## 2. What the change does

Reserve each shell's slot at the front of the file, and fill in its child pointers after
everything else has been emitted.

### 2.1 `src/runtime/compact.h`

`object_compactor` gains two members and three methods:

```c
std::vector<std::pair<object *, bool>> m_front;   // layout hints: (object, shallow)
std::vector<std::pair<size_t, object *>> m_pending; // (buffer offset of the reserved copy, source object)
void reserve_shallow(object * o);
void process_front();
void compact_pending();
```

and one public entry point:

```c
void add_front(object * o, bool shallow) { m_front.emplace_back(o, shallow); }
```

`add_front` records a hint for the **next** `operator()` call. With `shallow = false` the whole
subgraph of `o` is compacted first, post-order and max-shared as usual. With `shallow = true`
only the constructor object `o` itself is copied to the front; its children are compacted after
the entire graph reachable from the root has been emitted.

### 2.2 `src/runtime/compact.cpp`

`operator()` becomes: allocate the root slot → `process_front()` → the ordinary
`to_offset(root)` → `compact_pending()` → store the root address.

* **`process_front()`** swaps out `m_front` and, for each hint that is not a scalar and not
  already in `m_obj_table`: if the hint is not shallow, or the object is not a constructor,
  `to_offset` it normally. Otherwise it checks whether *every* child is already compacted; if so
  it calls `to_offset` (which costs nothing extra and keeps the structural deduplication — this
  is the case for a shell whose expression trees were emitted in an earlier part of the same
  module), and only otherwise calls `reserve_shallow`.
* **`reserve_shallow(o)`** copies the constructor object to the current end of the buffer with
  `copy_object`, records its buffer-relative offset, calls `save(o, new_o)` so that every other
  path to `o` resolves to this copy, and pushes `(offset, o)` on `m_pending`. It deliberately
  does **not** enter the copy in the max-sharing table: its contents are not final yet.
* **`compact_pending()`** walks `m_pending` in order and, for each reserved copy, calls
  `to_offset` on each child (compacting the expression trees now, at the end of the file) and
  patches the copy's pointer fields with `lean_ctor_set`. Offsets rather than pointers are held
  because `alloc` may reallocate the buffer.
* `LEAN_COMPACT_DUMP=1` (read once into `g_compact_dump`) prints each reserved shell's offset,
  tag and size, plus the region size and the reservation count.

### 2.3 `src/library/module.cpp`

The body of `lean_compacted_region_save` is moved unchanged into a new static
`compacted_region_save_core(ofname, mod, odata, dep_regions, oprev, allow_closures, front)`,
with one addition: it calls `compactor.add_front(f.first, f.second)` for each hint. The old
entry point calls it with an empty hint list, so `CompactedRegion.save` — used by the snapshot
feature, by the lazy-loading index, by the tactic-index image and by the `.ir` parts — is
bit-for-bit unchanged.

A new entry point computes the hints:

```c
extern "C" LEAN_EXPORT object * lean_save_module_data_part(
    b_obj_arg ofname, b_obj_arg mod, b_obj_arg odata, obj_arg oprev, object *);
```

It reads `LEAN_OLEAN_LAYOUT` once, then, if enabled and `odata` looks like a `ModuleData`
(constructor tag, ≥ 3 object fields): `front.emplace_back(const_names, false)` for field 1, then
for each element `cinfo` of field 2, in `constants` order,

```c
front.emplace_back(cval, true);   // ConstantInfo.<k>Info.val.toConstantVal
front.emplace_back(val,  true);   // <Kind>Val
front.emplace_back(cinfo, true);  // ConstantInfo
```

Every `ConstantInfo` constructor has exactly one field and every `<Kind>Val` `extends
ConstantVal`, so field 0 of field 0 is the `ConstantVal` in each case. Tags and arities are
checked and anything that does not match is silently skipped.

**The order of those three `emplace_back` calls is load-bearing** and is discussed in §6.1.

### 2.4 `src/Lean/Environment.lean`

```lean
@[extern "lean_save_module_data_part"]
private unsafe opaque saveModuleDataPart (fname : @& System.FilePath) (key : @& Name)
    (data : @& ModuleData) (prev : Option Compactor) : IO Compactor
```

`saveModuleDataParts` and `saveModuleData` call it instead of
`CompactedRegion.save fname mod data #[] prev`. Two lines.

### 2.5 The resulting layout

```
root slot | Names of constNames | ALL SHELLS, contiguous, in constants order
| imports, the constNames/constants/extraConstNames arrays, the entries arrays and entry
objects | the expression trees, level-parameter lists, `all` lists, ...
```

Everything `importModules` reads is now in the prefix of the file. In
`Deriv/Basic.olean` the `constNames` array sits at offset 17,328 and the `constants` array at
35,024, i.e. in the first three pages; in the stock file the `constants` array is at 387,944 of
435,632.

## 3. Why it is correct

**The file format, the header, the root slot and the reader are untouched.** A stock `lean` reads
a rebuilt `.olean` and vice versa; only the address of each object inside the file differs. This
is what makes the mixed-toolchain configurations in the measurements possible, and it is
verified (§4).

**Sharing is preserved.** `reserve_shallow` calls `save(o, new_o)`, so every other path to the
same heap object resolves to the reserved copy. No object is emitted twice by this mechanism.

**No structural deduplication is lost among the shells.** The reserved copies are the only
objects that bypass `save_max_sharing`, because their content is not final when they are placed.
Each constant's shell contains its own unique `Name`, so no two shells are ever structurally
equal and nothing could have been merged. What *is* lost is the rare case of an object emitted
later being structurally equal to a reserved shell; §4 measures that this is within noise, and
notes that the stock order has the mirror-image artefact (children of an object that is then
found in the max-sharing table stay behind as dead bytes).

**`compact_pending` terminates and is complete.** `m_pending` cannot grow while it is iterated:
nothing reserves during `to_offset`. Reserved objects are in `m_obj_table`, so they are never
re-entered. Every reserved copy's every field is written exactly once.

**Relocation is unaffected.** `region_reader`'s slow path fixes each object's pointers
independently of the order in which objects appear, so a relocated region is correct whatever
the layout.

**The `ConstantVal`-first hint order is what makes the deduplication work.** If the hints were
processed outermost-first, then when the `ConstantInfo` of a constant whose whole shell already
exists in an earlier part is examined, its child `<Kind>Val` is not yet in `m_obj_table` (it is
hinted next), so the "all children known" test fails and the 16-byte wrapper is reserved and
duplicated. Innermost-first, each wrapper finds all its children compacted and is deduplicated
exactly as in stock. See §6.1 — this was originally wrong, and the patch contains the fix.

## 4. How it was verified, and what that does not cover

**Object-graph identity.** `olean-canon.py` computes a layout-independent DFS fingerprint of the
graph reachable from the private root through all three parts, with and without sharing.
Identical `graph` and `tree` hashes and identical reachable-object counts, stock vs rebuilt, for
`Mathlib/Logic/Basic` (42,652 objects), `Analysis/Calculus/Deriv/Basic` (29,158) and
`Topology/Basic` (7,338). `constNames` dumps of all three parts of those modules plus
`Algebra/Group/Basic`, `Init/Prelude` and `Lean/Expr` are identical.

**The off switch.** A two-file project compiled with `LEAN_OLEAN_LAYOUT=0` produces `.olean`s
byte-identical to stock's.

**Layout, measured.** With the change on, the shells' span drops from 81.5 % to **7.4 %** of
`Deriv/Basic.olean`, from 63 % to **3.2 %** of `Logic/Basic.olean.private`, and from 68 % to
**6.7 %** of `Init/Prelude.olean`.

**Behaviour.** Byte-identical stdout, stderr and exit code, stock `lean` + stock `.olean`s versus
the fork + a Mathlib rebuilt from source with it, on: the 15 textbook files with `#print axioms`
appended; four Mathlib files re-elaborated from source; the constant-map suite (174 lines); a
20-name `#check`/`#print`/`#print axioms` sample drawn with a fixed seed from 1,383 Mathlib names
(460 lines); and a `lake build` of a 9-module test project, whose `.ilean` and `.c` files are
identical and whose `.olean`s differ in bytes but have identical graph fingerprints.

**Disk.** After the hint-order fix, the layout is byte-neutral: `.olean.private` grows by
**6,608 B** in total across 11,173 files, excluding the three modules whose *source* the fork
changes. (Before the fix it was +2.9 MB; see §6.1.)

**What this does not cover.**

* **`lean --stats` reports 11 more constants** on the rebuilt tree (771,140 vs 771,129), with
  four extension entry counts off by ±2–3. This was chased down and is **not** the layout:
  `dump-consts.lean` lists every `(module, constName)` of both environments, and the difference
  is exactly (i) `Lean.Environment`, whose source the fork changes, contributing 8 stock-only and
  13 fork-only private auxiliaries, and (ii) five Mathlib modules whose `simp`/`abel` auxiliary
  lemmas are numbered differently. Recompiling `Mathlib/Algebra/MvPolynomial/Rename.lean` with
  the **stock** toolchain from source, twice, also produces the differently numbered name, which
  the downloaded Mathlib cache does not contain: auxiliary-lemma numbering is not reproducible
  across builds. This is worth knowing before anyone reads a constant-count diff as a defect.
* **Only macOS, only 16 KB pages.** On Linux's 4 KB pages the same argument holds at four times
  the granularity — the shells of a module (10–20 KB) will span 3–5 small pages instead of 1–2
  large ones, still far below the 20–60 pages they span today — but that was not measured.
* **Cold-cache behaviour was not measured.**
* **The `.ir` parts are untouched.** Their `Decl` objects are emitted post-order too.
* **No test asserts the layout.** The only diagnostic is `LEAN_COMPACT_DUMP=1`. See §6.3.

## 5. Measured effect

Best of 3 interleaved repeats, one `lean` at a time, warm page cache, `/usr/bin/time -l`, load
1.4–2.5. Every row is the same binary; the rows that include improvement 2 are on `.olean`s
rebuilt with this change, the others on stock `.olean`s.

| case | config | wall s | sys s | max RSS GB | page reclaims |
|---|---|---|---|---|---|
| `import Mathlib` (legacy root) | stock | 10.76 | 8.51 | 5.68 | — |
| | improvements 1+3 | 3.42 | 1.54 | 3.44 | 283k |
| | **1+2+3** | 3.48 | **1.38** | **2.76** | **182k** |
| | 1+2 (no-touch off) | 3.48 | 1.47 | 2.77 | 245k |
| | 2 alone | 9.81 | 7.46 | **2.76** | 245k |
| `module` / `public import Mathlib` | 1+3 → **1+2+3** | 2.98 → 2.87 | 1.16 → 0.99 | 2.54 → **2.04** | — |
| `import Mathlib.Tactic` | 1+3 → **1+2+3** | 1.85 → 1.86 | — | 1.89 → **1.57** | — |

Three readings matter.

1. **This is a memory change, not a time change.** Wall and user time move by less than the
   run-to-run spread; system time falls by 0.1–0.17 s. RSS falls by 0.68 GB on top of
   improvements 1+3 — **−51 % against stock** for the legacy root.
2. **It subsumes the no-touch saving and does not need it.** 1+2 (no-touch off) has the same
   RSS as 1+2+3 to within 10 MB, and improvement 2 alone reaches the same 2.76 GB. Touching one shell
   per constant is harmless once the shells of a module share 1–2 pages. What the no-touch change
   still buys on top of this is page *faults* (245k → 182k), not resident memory — and this is
   the portable form of the same saving, since Linux has no address-reservation problem but pays
   the same page touches.
3. **The saving is exactly where it should be.** `vmmap` of an idle post-import process: resident
   `.olean` 1.284 → 0.890 GB, resident `.olean.private` 0.835 → 0.552 GB, and **every other
   category identical to the byte** (`.ir`, `.ir.sig`, `.olean.server`, binary, dyld, malloc).

**After the import.** Twenty `#print`s cost the same on both layouts (+20–30 MB): a `#print`
needs the value expression tree, which lives in the tree area either way. Three lemma-heavy
textbook files stay 0.68 GB below the 1+3 configuration with unchanged wall time. No case got
slower.

**The snapshot writer is where it pays most.** `--incr-header-save` of `import Mathlib`:
peak 7.36 → **4.45 GB**, faults 525k → **348k**. The writer serialises the environment, i.e.
walks every `ConstantInfo` it stores, and used to fault in the whole 7.4 GB.

**In the finished fork** (median of 3, everything else on), swapping the rebuilt `.olean` set for
the stock one costs **+0.34 GB at import (+24.5 %)** and **+1.08 GB (+68.7 %)** on `import
Mathlib` plus one `exact?`, at **zero** wall-clock cost (−0.02 s). It is the largest memory
contributor on the `exact?` row, and the only one of the six improvements that is free at
runtime — because it is a property of how the library was compiled.

## 6. What is unfinished, provisional or known to be wrong

### 6.1 An earlier version of this patch had the hint order wrong

During development the hints were pushed outermost-first (`ConstantInfo`, `<Kind>Val`,
`ConstantVal`), which cost **+2.9 MB** of `.olean.private` across 8,129 files — 16 bytes per
constant, shared with the `.olean` part. **The shipped patch has the fix**:
`lean_save_module_data_part` pushes `cval`, then `val`, then `cinfo`, and its comment says so.
The disk figure that goes with it is the byte-neutral one in §4 (+6,608 B).

One design consequence is *not* settled by the fix, and the measurement cannot settle it: for a
legacy root the constant map points at the **private** objects, so a private part that carries
its own dense copy of every `ConstantInfo` wrapper is the better layout for private-level
lookups, whereas deduplication is better for disk and for `module` roots. The current code
deduplicates.

### 6.2 The extension entries moved too, and the effect was not isolated

The chosen order emits `imports` and `entries` right after the shells, before the deferred trees.
`entries` used to be at the very end of the file. The A/B cannot separate this from the shell
move; it is presumably part of the `.olean` residency drop, since `setImportedEntries` reads
every module's top-level entry arrays at import.

### 6.3 The hint computation reads `ModuleData` by field index

`lean_save_module_data_part` reads fields 1 and 2 positionally and checks only tags and arity. A
change to `ModuleData` would make it degrade **silently** to the stock layout, with
`LEAN_COMPACT_DUMP=1` as the only diagnostic. A production version should pass the hints from
Lean, or key on a format version, or assert the layout in a test that reads a freshly written
part. This matters concretely: the upstream draft
[lean4#14145](https://github.com/leanprover/lean4/pull/14145) removes `ModuleData.extraConstNames`
and would shift the field indices.

### 6.4 It requires a rebuild, and a rebuild is not free

A library must be recompiled with this toolchain for the layout to exist. Mathlib and its eight
dependency packages took **66 min 24 s** wall on this machine. `ELAN_TOOLCHAIN` must be set: a
`lake build` with the fork's `lake` but without it silently runs the toolchain named in
`lean-toolchain` through elan's proxy — i.e. stock — and produces stock-layout `.olean`s. A first
attempt did exactly that.

### 6.5 Not done

* **Linux**, where this change is expected to carry over unchanged and to be the *whole* gain.
* **A combined measurement with lazy part loading.** They are orthogonal in mechanism and should
  compose — lazy loading defers whole files, this changes what a mapped file costs to use, and
  lazy loading's on-demand loader touches exactly the pages this makes dense — but the two were
  developed on separate branches and only later merged, and no measurement isolates the
  interaction.
* **The `.ir` parts** (§4).
* **Interaction with upstream's lazy discrimination-trie work**
  ([lean4#14362](https://github.com/leanprover/lean4/pull/14362)), which would stop touching
  every extension entry at import. The two compose rather than conflict, but neither has landed.
