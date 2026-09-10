# Improvement 1 — the macOS address reservation

_Against Lean `v4.33.1` (`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`). Part of
[`patches/lean4-v4.33.1-optimized.patch`](../../patches/lean4-v4.33.1-optimized.patch).
Compiled on macOS only; on Linux and Windows the binary takes the stock path byte for byte.
Switch: `LEAN_MMAP_RESERVE=0` restores stock behaviour._

---

## 1. The problem

### 1.1 The mechanism

Every `.olean`, `.olean.server`, `.olean.private`, `.ir.sig` and `.ir` file is a *compacted
region*: a byte image of an object graph whose internal pointers are absolute addresses,
computed at save time from a base address stored in the file header (`olean_header::base_addr`).
`mk_base_addr` derives that address from a hash of the module name, modulo `0x7f0000000000`.

`lean_compacted_region_read` (`src/library/module.cpp`) therefore maps each part with a *hinted*
`mmap` at `base_addr`. If the mapping lands there, the region is used as it lies — no pointer
fixup, no page touched beyond the header. If it does not, the file is `read()` into the heap and
every pointer in it is relocated.

`import Mathlib` performs **52,490** such hinted `mmap`s, at addresses spread uniformly over a
47-bit space.

XNU's `vm_map_enter` locates the free range ("hole") containing a caller-supplied address by
walking a singly-linked, **address-sorted** hole list from the bottom of the address space. Each
mapping dropped into fresh space splits a hole and adds one to that list. With uniformly spread
addresses the k-th mapping therefore costs O(k), and the whole loop O(n²).

Linux does not have this problem: it finds gaps with an augmented red-black tree. This is
presumably why the Lean developers, who benchmark on Linux, never saw it.

### 1.2 The size of it

`mmapbench2.c` replays exactly the per-file syscall sequence of `lean_compacted_region_read`
(`open`, `fstat`, `read` of the 88-byte header, `mmap` at `base_addr`) over the real 52,490 files
of the Mathlib closure, in a chosen order and with a chosen mapping strategy, and reports the
kernel time of the mapping loop alone. Quiet machine, two runs:

| strategy (all 52,490 files unless stated) | `mmap` loop, system time |
|---|---|
| hinted `mmap`, Lean's DAG order — **this is stock** | **6.1–7.2 s** |
| hinted, ascending `base_addr` | 11.5–13.4 s |
| hinted, descending `base_addr` | 0.78 s |
| hinted, only the 10,498 `.olean` parts, DAG order | 0.41–0.46 s |
| hinted, `.olean` in DAG order then the other 41,992 parts descending | 3.2–3.4 s |
| `MAP_FIXED` into free space, Lean order | 4.1 s |
| **reserve free space above 1 TB, `MAP_FIXED` over the reservation, Lean order** | **0.89–0.90 s** (+0.1 s for the reservation) |
| the same, descending order | 0.87–0.89 s |

So ~7 s of the ~10 s of a warm `import Mathlib` on this machine is kernel bookkeeping in the
mapping loop, and the cost is a function of *how many holes exist*, not of what is read.

### 1.3 Why the obvious fix does not work

Descending `base_addr` order reaches the floor — but it is not available. The import DAG is
discovered by reading each module's `imports` array out of its own `.olean`, so a module's
`.olean` must be mapped before its imports are known. A "read all headers, sort, map" two-pass
would need either a precomputed module list (Lake's `--setup`, not available to a plain `lean`
invocation) or a full `read()` of 2.19 GB of `.olean` parts.

Ordering only the *other* 41,992 parts after the DAG walk was measured (row 5 above) and
rejected: once the 10.5k `.olean` mappings are scattered, every later mapping still walks past
every hole below its address.

## 2. What the change does

### 2.1 `src/library/module.cpp` — the reserver

A new anonymous-namespace class, compiled only under

```c
#if defined(__APPLE__) && defined(LEAN_MMAP) && !defined(LEAN_WINDOWS)
#define LEAN_OLEAN_ADDRESS_RESERVE 1
```

`class olean_address_reserver` holds a mutex, an `m_enabled` flag, the page size, and
`std::map<uintptr_t, uintptr_t> m_free` — the reserved-but-not-yet-occupied ranges, disjoint,
sorted and coalesced. Two compile-time constants bound the region of interest:

```c
static constexpr uintptr_t floor_addr = uintptr_t(1) << 40;                          // 1 TB
static constexpr uintptr_t ceil_addr  = uintptr_t(0x7f0000000000) + (uintptr_t(1) << 32);
```

`ceil_addr` mirrors the modulus in `mk_base_addr` plus room for one file.

| member | what it does |
|---|---|
| `initialize()` | Lazy, on the first region read. Honours `LEAN_MMAP_RESERVE=0` by returning with `m_enabled` false. Walks the task's VM map with `mach_vm_region` from `floor_addr` to `ceil_addr` and calls `reserve` on each free gap. Sets `m_enabled = !m_free.empty()`. |
| `reserve(s, e)` | `mmap(s, e - s, PROT_NONE, MAP_ANON \| MAP_PRIVATE \| MAP_NORESERVE, -1, 0)` — **without** `MAP_FIXED`. If the kernel returns a different address (another thread mapped into the gap between the enumeration and the call) it `munmap`s and returns false; `initialize` then re-scans that hole, at most 8 times. On success it inserts the range into `m_free` and calls `lean_register_persistent_address_range(s, e)` (improvement 3). |
| `insert_free(s, e)` | Insert with coalescing against both neighbours. |
| `covers(s, e, it)` | Is `[s, e)` wholly inside one free range? `m_free.upper_bound(s)`, step back one, compare both ends. |
| `map_file(base_addr, size, fd)` | Under the mutex: initialize if needed; require `base_addr` page-aligned and `size > 0`; require `covers`; then `mmap(base_addr, size, PROT_READ \| PROT_WRITE, MAP_PRIVATE \| MAP_FIXED, fd, 0)`; assert the result equals `base_addr`; carve `[s, e)` out of the free range. Returns `nullptr` on any refusal. |
| `unmap(buffer, size)` | If the range is inside `[floor_addr, ceil_addr)` and page-aligned, replace the file mapping with a `PROT_NONE` anonymous `MAP_FIXED` mapping and return the range to `m_free`. Otherwise plain `munmap`. |

`get_olean_address_reserver()` is a deliberately leaked function-local singleton.

Two call sites change:

* `lean_compacted_region_read` tries `map_file` first; when it returns `nullptr` it falls through
  to the **unchanged** hinted-`mmap` block. Everything after the mapping — header checks, v2/v3
  handling, `region_reader::read`'s fast path vs its relocation walk, `CompactedRegion`
  construction — is untouched.
* `lean_compacted_region_free` calls `unmap` instead of `munmap`.

### 2.2 `src/runtime/process.cpp` — `posix_spawn`

The reservation makes `fork()` expensive, and this half of the change is what pays for that. It
is not a separate improvement: **the two must ship together.**

XNU's `vm_map_fork` copies the parent's VM map, doing `pmap`-level work proportional to the
*span* of copy-inherited entries even when nothing is resident. A 126 TB `PROT_NONE` entry is
expensive to inherit. Measured with a 40-line C program (`forkcost.c`), 10 forks + `execl`:

| reservation | inherit mode | 10 forks, wall |
|---|---|---|
| none | – | 0.05 s |
| 8 TB | copy (default) | 0.34 s |
| 64 TB | copy | 2.2–2.4 s |
| 64 TB | `VM_INHERIT_NONE` (`minherit`) | 0.66 s |
| 64 TB | copy, **`posix_spawn` instead of `fork`** | **0.036 s** |

Lake's `loadWorkspace` runs 16 `git` subprocesses on *every* invocation — `lake env`,
`lake setup-file` and `lake serve` included — so the tax was 6–8 s per Lake call, and a clean
1,387-module build pays it on more than 1,400 children.

`static obj_res spawn(...)` gains an `#ifdef __APPLE__` branch that uses `posix_spawnp`. It
reproduces exactly what the `fork` child did between `fork` and `execvp`:

* `posix_spawn_file_actions_adddup2` / `addclose` for each of the three pipes, and
  `posix_spawn_file_actions_addopen(…, "/dev/null", …)` for each `stdio::NUL` stream;
* `posix_spawn_file_actions_addchdir_np` when `cwd` is set;
* `posix_spawnattr_setflags(&attr, POSIX_SPAWN_SETSID)` when `do_setsid` is set;
* an explicit `envp`: start from `environ` when `inherit_env`, then apply the requested
  set/unset entries through two local `setvar` / `unsetvar` lambdas, exactly as the child's
  `setenv`/`unsetenv` loop did.

The parent-side pipe handling (close the child ends, `fdopen` the parent ends, build the
`Child` constructor object with the pid and the `do_setsid` byte) is duplicated verbatim in the
new branch; the original `fork` path is kept under `#else` for every other platform.

### 2.3 `src/CMakeLists.txt` — `LEAN_GITHASH_OVERRIDE`

Seven lines: a cache variable that replaces the `git describe`-derived `GIT_SHA1` before
`githash.h` is generated. It exists so a patched toolchain reports the *release's* commit
(`lean --githash`, `Lean.githash`), which is what makes Lake accept it for a
`leanprover/lean4:v4.33.1` project and what keeps the stock `.olean` files loadable when a
toolchain is configured with `CHECK_OLEAN_VERSION=ON`. It is a build convenience, not part of the
optimisation.

## 3. Why it is correct

**`MAP_FIXED` is only ever used over memory this class already owns.** `MAP_FIXED` silently
replaces whatever is at the target address, so using it wrongly is a memory-corruption bug.
`map_file` calls it only after `covers` has confirmed the whole page-rounded range lies inside a
range that (a) this class mapped `PROT_NONE` itself and (b) it has not since handed out. The
reservation `mmap` in `reserve` deliberately does **not** use `MAP_FIXED`, precisely so that a
concurrent mapping in the enumerated gap causes a retry rather than a silent overwrite.

**The invariant the rest of the fork depends on.** Inside a reserved range there are exactly two
kinds of bytes: inaccessible `PROT_NONE` reservation, and mapped compacted regions. `unmap`
restores the `PROT_NONE` reservation rather than leaving a hole, so this stays true after a
region is freed. Improvement 3 (no-touch reference counts) is built on it: it treats "this
pointer is inside a registered range" as proof of "this is a compacted-region object with
reference count 0". **If a heap object could ever be allocated inside a reserved range, that
inference would be false and its reference count would stop being maintained** — a
use-after-free or a leak. Two facts keep it true: the kernel never places a hinted or unhinted
`mmap` over an existing mapping, and mimalloc uses `MAP_FIXED` only to decommit memory it
already owns (`prim/unix/prim.c`).

**Nothing about which regions land at their address changes.** A file is mapped at `base_addr`
under exactly the same condition as before — the range is free — and the fallback path is the
original code. So the fast-path/relocation decision, and therefore the loaded environment, is
unchanged. Only the kernel's bookkeeping differs.

**mimalloc cannot be starved.** Its aligned-allocation hints (2–30 TB) now fall inside reserved
space; the kernel places those mappings elsewhere and mimalloc's `unix_mmap` accepts a different
address as its documented fallback. Its first arena is created before any import in any case,
so it is *outside* the reservation (the enumeration walks the live VM map and reserves only
gaps).

**`posix_spawn` is semantically what the `fork` child did.** The child's entire pre-`exec`
behaviour is the four items in §2.2, each of which has a direct `posix_spawn` counterpart.

## 4. How it was verified, and what that does not cover

Stock `lean v4.33.1` versus the patched binary, both run directly with Mathlib's `LEAN_PATH`
(not through `lake env` — see §6):

| check | result |
|---|---|
| 15 textbook files, each with `#print axioms` appended for every `theorem`/`lemma` (56 declarations) | stdout, stderr and exit code identical, 15/15 |
| `Mathlib/Logic/Basic.lean`, `Order/Basic.lean`, `Topology/Basic.lean`, `LinearAlgebra/Matrix/Determinant/Basic.lean` re-elaborated in place | identical, 4/4 |
| `lean --stats` on `import Mathlib` (1,012 lines: 52,490 imported modules, 7,495,455,112 bytes, 771,129 constants, 361 extensions with per-extension entry counts) | identical except `number of memory-mapped modules`: 52,328 stock vs 52,325 |
| the same binary with `LEAN_MMAP_RESERVE=0` | reproduces the stock timings exactly (10.55 s vs stock 10.53 s) |

On the memory-mapped count: it is not stable in *stock* runs either (52,325 and 52,328 were both
observed from stock), because the ~400 files with `base_addr` below 1 TB collide or not with
ASLR-placed system mappings. Those files are below the reservation floor and take the unchanged
path in both binaries. Whether a region is mapped or relocated affects speed only, never the
loaded environment — which the identical constant and extension counts confirm.

Later, the whole fork's 26-case equivalence suite
([`code/scripts/check-equivalence.sh`](../../code/scripts/check-equivalence.sh)) passes with this change
in it, in every configuration measured.

**What this does not cover.**

* **XNU's algorithm was never traced.** `dtrace` needs `sudo`, which this environment did not
  have. The mechanism in §1.1 is inferred from the timing table, not observed. The fix does not
  depend on the exact algorithm — only on "no new holes" — but the *explanation* is inference.
* **The `unmap` path is barely exercised.** It runs only from `CompactedRegion.free`, which the
  frontend never calls (`leakEnv := true`). A long-lived process that imports repeatedly and
  frees regions — the language server's watchdog does not, but an embedding host might — would
  exercise the free-map bookkeeping in ways nothing here has.
* **No cold-cache measurement on macOS.** There is no `purge` without root; touching 20 GB of
  anonymous memory evicted the cache only for the first run.
* **No memory-safety tooling.** No AddressSanitizer or Valgrind run over the mapping path.
* **The `posix_spawn` branch's failure path was not compared with the `fork` path's.** On
  `posix_spawnp` failure the new code frees `pargs` and `throw rc` (an `int`); the pipe file
  descriptors opened for the child are not closed on that path. Whether that matches the
  original `pid == -1` handling is not visible in the patch and is worth checking against the
  base file before upstreaming.

## 5. Measured effect

**Alone, on top of stock** (quiet machine, warm page cache, median of 3 interleaved runs,
`/usr/bin/time -l`):

| case | binary | wall s | user s | sys s | max RSS GB | page reclaims |
|---|---|---|---|---|---|---|
| `import Mathlib` (legacy root) | stock | 10.53 | 1.67 | 7.77 | 5.68 | 423,373 |
| | **with the reservation** | **3.73** | 1.70 | **1.60** | 5.69 | 423,222 |
| | the same, `LEAN_MMAP_RESERVE=0` | 10.55 | 1.66 | 7.80 | 5.68 | 422,911 |
| `module` / `public import Mathlib` | stock → fork | 6.12 → **3.06** | 1.50 | 4.30 → **1.20** | 3.26 | — |
| `import Mathlib.Tactic` | stock → fork | 3.02 → **2.06** | 0.93 | 1.93 → **0.85** | 2.89 | — |
| `--incr-load` of a Mathlib header snapshot | stock → fork | 8.79 → **1.49** | 0.50 | 9.46 → **1.53** | 1.76 | — |

User time, RSS and fault counts are unchanged, as a pure kernel-bookkeeping change should leave
them. The −6.2 s of system time is exactly what `mmapbench2` predicted (7.2 → 0.9 s).

**At build scale.** Clean `lake build` of `formal-conjectures` (1,387 files, 12 concurrent
`lean` processes): stock **47.7 min** / 22,336 s system CPU → **24.6 min** / 10,211 s. User CPU
and peak memory unchanged, same job count, build succeeded.

**Lake latency, with `posix_spawn`.** `lake env true` in the Mathlib checkout: **11.2 s → 0.74 s**
(stock 1.30 s). The reservation alone made it 11.2 s; `posix_spawn` makes it faster than stock,
because stock's `fork` still copies the map of a process holding Lake's own imports.

**In the finished fork.** With the other five improvements present, switching *this* one off
(`LEAN_MMAP_RESERVE=0`) costs only **+0.11 s wall and +0.01 GB** on `import Mathlib`. That is not
a contradiction: the lazy part loading and the shell-first layout have since removed most of the
mappings this change made cheap. Both numbers are true, and a reader deciding what to keep needs
the second one.

## 6. What is unfinished, provisional or known to be wrong

1. **macOS only, by construction.** The Linux build of the same source takes the stock path; the
   Linux patch replaces this with per-region range registration for improvement 3's benefit
   only (see [`docs/improvements/03-no-touch-refcounts.md`](03-no-touch-refcounts.md) §2.3). Windows is untouched.
2. **The process's virtual size becomes ~126 TB.** `vmmap` shows two large `VM_ALLOCATE`
   reservations with 0 resident. RSS and footprint are unaffected, but any tool or cgroup that
   reads *virtual* size will report an alarming number.
3. **162–165 regions per `import Mathlib` still fall back to `read()` + relocation** (about
   30 MB). Their `base_addr` lands in `[0x10_0000_0000, 0x70_0000_0000)`, which is XNU's arm64
   GPU carve-out (`MACH_VM_MIN/MAX_GPU_CARVEOUT_ADDRESS`); user mappings cannot use it and
   `MAP_FIXED` there fails with `ENOMEM`. The patch leaves this exactly as stock does. The fix
   is a one-line change to `mk_base_addr` at *save* time, which would require rebuilding every
   `.olean`.
4. **The 1 TB floor and the `0x7f00_0000_0000 + 4 GB` ceiling are hard-coded constants** that
   mirror `mk_base_addr`. If upstream changes how base addresses are chosen — and
   [lean4#14563](https://github.com/leanprover/lean4/pull/14563) proposes to, for ASLR safety —
   these constants have to move with it. The two changes compose (that PR changes how a base
   address is chosen, not how many hinted mappings there are), but they are not independent.
5. **An embedding host that already uses the high address space** would find those ranges
   enumerated as occupied and simply not reserved; correct, and slower. A process that `dlopen`s
   a very large plugin *after* the first import may be refused its hinted address and relocated.
6. **The 8-attempt retry in `initialize` gives up silently.** If a gap cannot be reserved after
   8 attempts the loop moves on; the result is a correct, slower process, with no diagnostic.
7. **Do not A/B a fork binary from inside `lake env`.** `lake env` exports
   `DYLD_LIBRARY_PATH=<stock toolchain>/lib/lean:…`, and the `lean` executable is a 33 KB stub
   that loads `libleanshared.dylib` via `@rpath` — so a foreign `lean` silently picks up the
   **stock** runtime and produces stock numbers. One measurement pass was invalidated by exactly
   this and discarded. Direct shell runs are unaffected, because macOS strips `DYLD_*` when
   exec'ing system binaries.
8. **The platform triple.** The fork must be configured with
   `-DLEAN_PLATFORM_TARGET=<the release's triple>` if it shares a checkout with the stock
   toolchain: Lake's configuration cache is keyed on `System.Platform.target`, so otherwise every
   switch between the two re-elaborates the lakefile (3–5 s for Mathlib). This is a build flag,
   not part of the patch.
