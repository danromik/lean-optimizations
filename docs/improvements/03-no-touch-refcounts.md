# Improvement 3 — no-touch reference counts

_Against Lean `v4.33.1` (`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`). Part of
[`patches/lean4-v4.33.1-optimized.patch`](../../patches/lean4-v4.33.1-optimized.patch).
Active on macOS and Linux by different routes (§2.3); on other platforms every new function
degenerates to the stock code. Switches: `LEAN_NO_TOUCH=0` (the address test always answers
`false`), `LEAN_MMAP_RESERVE=0` (no ranges are registered at all)._

---

## 1. The problem

### 1.1 The mechanism

`finalizeImport` (`src/Lean/Environment.lean`) builds `privateConstantMap : Std.HashMap Name
ConstantInfo` — and, for `module` roots, `publicConstantMap` — from each module's `constNames`
and `constants` arrays, which live in memory-mapped compacted regions. The stock loop is

```lean
for cname in data.constNames, cinfo in data.constants do
  match privateConstantMap.getThenInsertIfNew? cname cinfo with ...
```

The compiler emits `lean_array_uget_borrowed` for the *name* — it is only hashed and compared, so
a borrow suffices — but `lean_array_fget` for `cinfo`, because the value is *stored* into the map
and must therefore be owned. `lean_array_fget` calls `lean_inc`, and `lean_inc_ref` reads `m_rc`
from the object header.

Objects in a compacted region are written with `lean_set_non_heap_header`, so `m_rc = 0`. For
them `lean_inc_ref_n` is a no-op *after the read* (`lean_is_st` is false;
`(unsigned)0 > (unsigned)LEAN_RC_STICKY` is false). **The only effect of the increment is that
the page containing the `ConstantInfo` is faulted in.**

The compactor writes objects in post-order, so each `ConstantInfo` sits immediately after its own
type and value expression trees (this is what improvement 2 changes). Touching one 8-byte header
per constant therefore faults in about **2.8 GB of the 7.5 GB** of mapped files, for 772k
constants — and nothing in the import path needs those pages. Names are emitted contiguously at
the front of each file (≈ 20 MB for all 772k); extension entries are reached through separate
top-level arrays.

`Runtime.markPersistent` then walks the finished environment, reaches every stored `ConstantInfo`
pointer, and reads its header again. It stops there, because `lean_has_rc` is false — but it has
to read the header to find that out.

### 1.2 What it costs

`import Mathlib`, legacy root, on top of improvement 1: **5.69 GB RSS and 423k page faults**, of
which 2.25 GB and 137k are this.

## 2. What the change does

Give the runtime a cheap, dereference-free answer to "is this pointer a memory-mapped
compacted-region object?", and use it to skip the reads.

### 2.1 `src/include/lean/lean.h` — the interface (+26 lines, mostly the doc comment)

```c
LEAN_EXPORT void lean_register_persistent_address_range(uintptr_t start, uintptr_t end);
LEAN_EXPORT void lean_unregister_persistent_address_range(uintptr_t start, uintptr_t end);
LEAN_EXPORT bool lean_is_in_persistent_address_range(void const * o);
LEAN_EXPORT lean_obj_res lean_array_fget_no_touch(b_lean_obj_arg a, b_lean_obj_arg i);
```

### 2.2 `src/runtime/object.cpp` — the table and the three uses

The table is a writer-side `std::map<uintptr_t, uintptr_t>` under a mutex, plus a reader-side
immutable snapshot published with an atomic pointer, so that a lookup is lock-free:

| identifier | what it is |
|---|---|
| `struct persistent_range` | `{ m_start, m_end }` |
| `struct persistent_range_snapshot` | a sorted `std::vector<persistent_range>` plus `m_index`, a bucket index over the address space at `bucket_shift = 32` (4 GB buckets), and `m_index_base` |
| `g_persistent_range_mutex`, `g_persistent_range_map` | writers |
| `g_persistent_range_snapshot` (atomic), `g_persistent_range_dirty` (atomic) | the published snapshot and its staleness flag |
| `g_persistent_range_lo` / `g_persistent_range_hi` (atomic) | a bounding box that only ever widens; rejects a heap pointer without touching the array |
| `persistent_range_rebuild()` | called with the mutex held; builds a new snapshot and publishes it with a release store. **Retired snapshots are leaked deliberately**, so that a reader can never observe a dangling snapshot |
| `persistent_range_search(snap, p)` | bucket lookup, then a short backward scan — 2–3 dependent loads. The bucket index exists because a plain binary search over an 840 KB array was measured to cost ~0.3 s per import in cache misses |

`lean_register_persistent_address_range` inserts, widens the bounding box, and sets `dirty` with
a release store. `lean_unregister_persistent_address_range` erases and rebuilds **eagerly** —
after a `munmap` the range may be reused by heap mappings, so no reader may keep treating it as
persistent. It deliberately leaves the bounding box alone: the box is only a conservative
pre-filter.

`lean_is_in_persistent_address_range` reads `LEAN_NO_TOUCH` once into a function-local static; if
it is `0` the function always answers `false`, which disables every no-touch path while leaving
the address reservation in place. Otherwise: bounding-box test, then the snapshot (rebuilding it
under the mutex if `dirty`).

Three uses:

```c
lean_obj_res lean_array_fget_no_touch(b_lean_obj_arg a, b_lean_obj_arg i) {
    lean_object * o = lean_array_get_core(a, lean_unbox(i));
    if (!lean_is_scalar(o) && !lean_is_in_persistent_address_range(o)) lean_inc_ref(o);
    return o;
}
```

and one added conjunct in each of `lean_mark_persistent` and `lean_mark_mt`:

```c
if (!lean_is_scalar(o) && !lean_is_in_persistent_address_range(o) && lean_has_rc(o)) { ... }
if (!lean_is_scalar(o) && !lean_is_in_persistent_address_range(o) && lean_is_st(o))  { ... }
```

(`lean_mark_mt` also gains the test in its early-out.) The `lean_mark_mt` guard was added later,
for the lazy-loading work — see §6.3.

### 2.3 `src/library/module.cpp` — where ranges come from

Two producers, and they are different on the two platforms.

* **macOS.** `olean_address_reserver::reserve` calls
  `lean_register_persistent_address_range(s, e)` immediately after the reservation `mmap`
  succeeds, i.e. **before any file can be mapped into it**. Two to six ranges in practice. They
  are never unregistered: `unmap` turns a freed region back into an inaccessible `PROT_NONE`
  reservation.
* **Linux**, under a second guard `LEAN_OLEAN_RANGE_REGISTER` (`__linux__ && LEAN_MMAP &&
  !LEAN_WINDOWS`). There is no address reservation on Linux — it would leave one extra VMA per
  region against `vm.max_map_count`, whose 65,530 default a Mathlib import already approaches
  with ~52k file mappings. Instead `lean_compacted_region_read` registers the exact page-rounded
  range of **every region that landed at its saved address** (~52k ranges), and
  `lean_compacted_region_free` unregisters it *before* `munmap`. `olean_range_register_enabled()`
  reads `LEAN_MMAP_RESERVE` once; `olean_range_end()` does the page rounding.

The bounding box is what makes the Linux case cheap: `mk_base_addr` produces addresses below
`0x7f0000000000`, while the PIE, brk and mmap areas live at `0xaaaa…`/`0xffff…` on arm64 and
`0x5555…`/`0x7fff…` on x86-64, so heap pointers are rejected by two compares.

### 2.4 `src/Lean/Environment.lean` — the primitive and the loops

```lean
@[extern "lean_array_fget_no_touch"]
private unsafe def getPersistentElemUnsafe (a : @& Array α) (i : @& Nat) (h : i < a.size) : α := a[i]

@[implemented_by getPersistentElemUnsafe]
private def getPersistentElem (a : @& Array α) (i : @& Nat) (h : i < a.size) : α := a[i]
```

The model is `a[i]`; the implementation differs from it only in a reference count that is
provably not maintained for the objects concerned.

Both constant-map loops in `finalizeImport` are rewritten so that the compiled code holds
`cinfo` in **exactly one** owned reference that flows straight into the map insertion:

```lean
for h : i in *...(min data.constNames.size data.constants.size) do
  let cname := data.constNames[i]
  let numConsts := privateConstantMap.size
  privateConstantMap := privateConstantMap.insertIfNew cname (getPersistentElem data.constants i hc)
  if privateConstantMap.size == numConsts then
    -- the name was already present and the map is unchanged; rare (~1k of 772k)
    let cinfo := data.constants[i]
    if let some cinfoPrev := privateConstantMap[cname]? then
      if subsumesInfo privateConstantMap cinfo cinfoPrev then ...
```

The shape matters. The first attempt kept the original `getThenInsertIfNew?` form with `cinfo`
bound once and re-used in the duplicate branch; the compiler then correctly emitted
`lean_inc(cinfo)` before the insertion and `lean_dec(cinfo)` in the continuation — two header
reads, defeating the purpose. This was caught by generating the C with the stock compiler
(`lean -c`) and reading the loop, and the final shape was verified the same way: exactly one
`lean_array_fget_no_touch` per constant, feeding `Raw₀.insertIfNew` → `AssocList.cons`
(`lean_ctor_set`, no reference-count traffic), in both loops.

## 3. Why it is correct

### 3.1 The invariant

> **Every object whose address lies inside a registered range is an object of a memory-mapped
> compacted region, and therefore has `m_rc = 0`.**

On macOS this holds because inside a reservation there are exactly two kinds of bytes:
inaccessible `PROT_NONE` reservation, and regions mapped there by `map_file`. On Linux it holds
because the registered range *is* one region's page-rounded extent, registered when the region is
mapped and unregistered before it is unmapped.

**If it failed** — if a heap object could be allocated inside a registered range — then
`lean_array_fget_no_touch` would hand out an unowned reference to a reference-counted object, and
`lean_mark_persistent` / `lean_mark_mt` would skip it. The consequences are a premature free (a
use-after-free) or a leak, and a shared object never marked multi-threaded. This is the single
assumption the change rests on. Two facts protect it: the kernel never places a hinted or
unhinted `mmap` over an existing mapping, and mimalloc uses `MAP_FIXED` only to decommit memory
it already owns (`prim/unix/prim.c`).

Note the *converse* is not needed and is not true: regions that fell back to `read()` +
relocation live in the heap with `m_rc = 0` as well, are not in any range, and take the ordinary
path. Correct, just not optimised.

### 3.2 Skipping is a no-op, not a change

For `m_rc = 0`, `lean_inc_ref`, `lean_dec_ref` and the body of `lean_mark_persistent` are all
no-ops apart from the header read. So skipping them changes no reference count anywhere, and
`lean_mark_persistent`'s walk would have stopped at those objects regardless — it just would have
read the header to find out.

### 3.3 The rewritten loop computes the same map

`getThenInsertIfNew? k v` conses `(k, v)` onto the bucket and increments `size` iff `k` is
absent, and leaves the map untouched otherwise. `insertIfNew k v` does the same — both walk the
bucket with `==`, and the "absent" branch is literally the same code (`AssocList.cons` +
`expandIfNecessary`). So *"size unchanged"* ⟺ *"key present"* ⟺ the original's `some cinfoPrev`
case, in which the original read `cinfoPrev` from the unchanged map, which is the same object
`privateConstantMap[cname]?` returns. The subsequent `subsumesInfo` / `insert` /
`throwAlreadyImported` logic is unchanged.

Bucket layout, insertion order and therefore iteration order of the resulting maps are identical.
Neither map ever expands — capacity is set to the number of constants and
`numBucketsForCapacity` is ≥ size·4/3 by construction — so `expand`'s reinsertion path, which
might increment and decrement values, is not exercised, exactly as before.

### 3.4 Lifetimes

`ConstantInfo` objects are owned by their compacted region, never by reference counts. A region
is freed only by `CompactedRegion.free`, whose contract already says no live references to
imported objects may exist at that moment. The frontend uses `leakEnv := true` and never frees;
`withImportModules` frees only after its action returned. Nothing here changes who owns what or
when anything is freed.

### 3.5 Concurrency

Registration happens under the writers' mutex. Readers take one acquire load of the snapshot
pointer; a reader that has not yet seen a newly registered range simply answers `false` and takes
the ordinary path — always safe, only slower. Unregistration rebuilds eagerly under the mutex, so
no snapshot published after a `munmap` contains the freed range. Retired snapshots are never
freed, so a reader holding a stale pointer cannot dereference freed memory.

## 4. How it was verified, and what that does not cover

Stock `lean v4.33.1` versus a hybrid toolchain (fork binaries, **stock** `.olean` files), both
run directly with Mathlib's `LEAN_PATH`:

| check | result |
|---|---|
| 15 textbook files + `#print axioms` of every theorem/lemma | identical, 15/15 |
| four Mathlib files re-elaborated in place | identical, 4/4 |
| `lean --stats` on `import Mathlib` (modules, bytes, 771,129 constants, 361 extensions with entry counts) | identical except the ASLR-dependent memory-mapped count |
| a purpose-written constant-map exercise: `#check` / `#print` / `#print axioms` of four Mathlib theorems, `inferInstance : Field ℝ`, two `exact?`, `simp` with Mathlib lemmas, `ring`, `linarith`, `nlinarith`, `norm_num` — 174 lines of output | identical, exit 0 |
| `lake build` of a 9-module test project from a clean `.lake`, stock `lake` vs fork `lake` | 18 `.olean`/`.ilean`/`.c` artefacts byte-identical; build log identical after normalising Lake's per-job timings; program output identical |

**Generated C was read**, not just trusted: `build/release/stage1/lib/temp/Lean/Environment.c`
contains `lean_array_fget_no_touch` in both loops and in the boxed wrapper, and `nm` shows the
three new symbols exported from `libleanshared.dylib`.

**`importModules` in isolation** (`rss-steps.lean` under `lean --run`; absolute numbers are ~1 GB
high because `--run` maps the `Lean` oleans a second time): RSS delta of `importModules` with
`loadExts := false`, stock/reservation-only **+5.44 GB** → **+3.30 GB**; with `loadExts := true`,
+5.95 → **+4.09 GB**; with `leakEnv := false` (no `markPersistent` at all) **+3.25 GB**, which
confirms that after the map fix `markPersistent` is not touching anything either.

**What this does not cover.**

* **No memory-safety tooling.** No AddressSanitizer, Valgrind or leak-checker run. The invariant
  in §3.1 is argued, not instrumented. This is the gap a reviewer should care most about.
* **Cold-cache numbers are inconclusive.** Without root there is no `purge`; touching 20 GB of
  anonymous memory evicted the page cache only for the first run. The mechanism is nevertheless
  clear — every avoided fault is a 16 KB read from disk when the cache is cold, so 137k fewer
  faults ≈ 2.2 GB less I/O per cold import.
* **The unregistration path is exercised only by the Linux build**, and only when regions are
  freed, which the frontend does not do.
* **Long-lived repeated-import processes** (the language server) were not exercised for the
  range-table paths specifically.

## 5. Measured effect

**Alone, on top of improvement 1** (fastest of 3 interleaved runs; minima for RSS and faults;
warm page cache, quiet machine):

| case | binary | wall s | sys s | max RSS GB | page reclaims |
|---|---|---|---|---|---|
| `import Mathlib` (legacy root) | stock | 10.62 | 7.58 | 5.68 | 423,315 |
| | improvement 1 only | 3.71 | 1.68 | 5.69 | 423,144 |
| | **1 + 3** | **3.56** | **1.56** | **3.44** | **286,061** |
| `module` / `public import Mathlib` | 1 → **1+3** | 3.04 → **2.96** | 1.20 → 1.17 | 3.26 → **2.54** | 276k → **232k** |
| `import Mathlib.Tactic` | 1 → **1+3** | 1.94 → **1.90** | 0.78 → 0.74 | 2.89 → **1.89** | 227k → **166k** |

RSS −2.25 GB (−40 %) and −137k faults (−32 %) for the legacy root. User time is unchanged. **The
honest headline is memory, not time**: wall moves by 0.15 s on a warm machine, and what changes
is that a Mathlib-importing process is 3.4 GB instead of 5.7 GB — which is what decides how many
`lean` processes fit in RAM before the page cache thrashes.

`vmmap` of an idle post-import process confirms where it went: resident `.olean.private`
2.586 → **0.835 GB**, resident `.olean` 1.777 → **1.284 GB**, every other category identical to
the byte.

**At build scale.** Clean `lake build` of `formal-conjectures` (1,387 files, 12 concurrent `lean`
processes): stock 47.7 min / 22,336 s system CPU / 72.9 GB peak tree RSS → improvement 1 alone
24.6 min / 10,211 s / 72.9 GB → **1+3: 20.3 min / 7,555 s / 50.0 GB**. Peak memory of the
12-process build **−31 %**; user CPU unchanged; same job count.

**In the finished fork**, switching only this off (`LEAN_NO_TOUCH=0`) costs **+0.07 s and
+0.02 GB** on `import Mathlib` and +0.05 s with an `exact?`. Improvement 2 has since taken the
resident memory this was saving, exactly as improvement 2's own document predicted; what remains
is about 1,400 page faults.

## 6. What is unfinished, provisional or known to be wrong

### 6.1 The snapshot leak is unbounded on Linux, and the comment understates it

The code comment says retired snapshots are leaked and *"a snapshot is rebuilt once per import
batch, so this leaks ~16 bytes per range per import."* That is true when registrations are
batched, which is the case for a plain import.

**It is not true when regions are loaded on demand.** With lazy part loading (improvement 4)
active on Linux, every on-demand region load registers a new range and sets `dirty`; the next
`lean_is_in_persistent_address_range` rebuilds the whole snapshot — for a Mathlib closure a
~840 KB `m_ranges` vector plus a ~130 KB bucket index — and leaks the previous one. A workload
that loads many parts on demand therefore leaks roughly one megabyte per load. In the ordinary
case this is bounded by the ~120 on-demand loads a session makes (~120 MB, itself not nothing);
in the pathological case that the prebuilt search index was written to remove, it would be
thousands of loads.

This is not observed as a failure anywhere in the measurements — the Linux measurements were not
taken under a workload that loads thousands of parts — but it is a defect a reviewer would find,
and it should be fixed before this is proposed anywhere: either by reclaiming snapshots under
epoch/RCU discipline, or by batching registrations, or by not rebuilding on every registration.

### 6.2 Platform scope

The macOS route depends on improvement 1's reservation; with `LEAN_MMAP_RESERVE=0` no range is
registered and the whole feature is off. Windows registers nothing. Both are correct, just
unoptimised.

### 6.3 The `lean_mark_mt` guard has its own history

It is in this file because it belongs to the same mechanism, but it was found by the lazy-loading
work: `LEAN_LAZY_PARTS=ir` had a *higher* RSS (5.4 GB) than the eager path (3.4 GB) although it mapped
21k fewer files. `vmmap` showed the `.olean.private` parts 2.59 GB resident — the pre-fix number.
Storing the lazy state, which references the eagerly built constant map, into a process-global
`IO.Ref` marks the value multi-threaded, and `lean_mark_mt` walked the map reading the header of
every `ConstantInfo`: the same gratuitous page touches, resurfacing through a different runtime
walk. Anyone auditing this should assume there are **other** runtime walks with the same shape and
look for them, rather than assume these three are all of them.

### 6.4 Not done

* An A/B of `lake build` of a single Mathlib file with the fork toolchain.
* Cold-cache numbers (§4).
* Upstreaming shape: the natural form is the same runtime helper plus the `finalizeImport` loop
  shape, with the range source being whatever mechanism upstream adopts for mapping regions.
