# The runtime switches — applying one change at a time

The fork is six changes in one binary, and each one can be turned off at run time with an
environment variable. This is how every number in the manuscript was attributed: the same
binary, on the same machine, in the same session, with one thing changed. It is also the
cleanest control available — turning a change off must return the measurement to the value
the unpatched binary gives, and where the manuscript claims a saving, it claims that too.

**Everything defaults to on.** You only need these to turn something off.

| Change | Switch | Off means |
|---|---|---|
| the macOS address reservation (improvement 1) | `LEAN_MMAP_RESERVE=0` | macOS: no address-space reservation, and therefore no persistent ranges — the no-touch paths take the stock route too; child processes are also started with `fork`+`execvp` again instead of `posix_spawn`, since that change exists only to pay for the reservation (see below). Linux: no per-region range registration. Does not affect the layout or lazy loading. |
| the shell-first `.olean` layout (improvement 2) | `LEAN_OLEAN_LAYOUT=0` | olean parts are *written* in stock order, byte-identical to stock. Reading is layout-agnostic in both directions. **Asymmetric — see below.** |
| no-touch reference counts (improvement 3) | `LEAN_NO_TOUCH=0` | the reservation stays (so improvement 1's wall-clock gain is kept) but every object header is touched as in stock. This is the switch that separates the memory the layout saves from the memory the reference-count change saves. |
| lazy part loading (improvement 4) | `LEAN_LAZY_PARTS=0` | private parts and compiled code are loaded eagerly, as in stock. `LEAN_LAZY_PARTS=ir` is an intermediate setting: compiled code lazily, private parts eagerly. |
| the prebuilt `exact?` index (improvement 5) | `LEAN_SEARCH_INDEX=0` | stored and cached index entries are ignored and the stock walk runs. **Asymmetric — see below.** |
| the tactic index image (improvement 6) | `LEAN_TACTIC_INDEX=0` | no image is read or written; the extension-import function runs for all 334 extensions, as in stock. **Also the setting you need for a from-source library build — see [`warnings.md`](warnings.md) §1.** |

**About the variable names.** Every variable is named for what the change does, and the names
here are exactly the strings the binary reads. Earlier revisions of the fork used the
development shorthand (`LEAN_LAZY_PARTS`, `LEAN_SEARCH_INDEX`, `LEAN_TACTIC_INDEX`, `LEAN_OLEAN_LAYOUT`); those
names are gone and are no longer read by anything — a binary built from these patches ignores
them silently.

**About spawning on macOS.** A `fork()` in a process holding the reservation costs about 0.4 s,
so the fork starts child processes with `posix_spawn` instead. That change is part of
improvement 1 and is covered by the same switch: with `LEAN_MMAP_RESERVE=0` the binary both
stops reserving and goes back to `fork`+`execvp`, which is what stock Lean does. The two are
deliberately not separable — a reservation without the spawning change is worse than no
reservation at all for anything that starts child processes, and that configuration should not
be reachable. Anyone who wants to measure the spawning change on its own can give it its own
condition in `src/runtime/process.cpp`.

Supporting variables:

| Variable | Default | What it does |
|---|---|---|
| `LEAN_LAZY_PARTS_INDEX_DIR` | `$HOME/.cache/lean-lazy-parts` | where the lazy-loading index lives. One file per distinct import closure, sized by the closure's constant count: 59 MB for `import Mathlib.Topology.Basic`, 73 MB for `import Mathlib.Analysis.Calculus.Deriv.Basic`, 81 MB for `import Mathlib.Tactic`, 103 MB for `import Mathlib` (measured 2026-09-05, MB = 10^6 bytes as everywhere else here; earlier revisions of this file said 200–300 MB, which was the total for several closures, not one) |
| `LEAN_TACTIC_INDEX_DIR` | beside the library's `.olean` files | where the tactic-index image is read from, and written to under `LEAN_TACTIC_INDEX_WRITE=1` (~212 MB for the `import Mathlib` closure) |
| `LEAN_TACTIC_INDEX_WRITE=1` | off | build an image for the current import set and write it. For whoever *distributes* a library, run once; never needed at run time |
| `LEAN_SEARCH_INDEX_CACHE_DIR` | unset (no cache) | per-closure `exact?` entry cache, ~51 MB; unnecessary if the library was built with the fork |
| `LEAN_SEARCH_INDEX_RECORD=0` | on | do not record index entries into `.olean` files being written |
| `LEAN_LAZY_PARTS_VERBOSE`, `LEAN_SEARCH_INDEX_VERBOSE`, `LEAN_TACTIC_INDEX_VERBOSE` | off | one line per cache decision on stderr. Useful for confirming you are actually running the fork. |

Two more the fork reads, which change nothing and only report:

| Variable | Default | What it does |
|---|---|---|
| `LEAN_CACHE_STATS=<file>` | unset | appends one line per cache decision to that file, across every process. `O_APPEND` writes of a few bytes do not interleave, so a whole build can be counted afterwards. This is how a distributor learns which import closures its users present — see [`improvements/06-tactic-index-image.md`](improvements/06-tactic-index-image.md) |
| `LEAN_COMPACT_DUMP=1` | off | prints the `.olean` layout to stderr as it is written. The only diagnostic the layout change has — see [`improvements/02-olean-layout.md`](improvements/02-olean-layout.md) |

To try a single change on top of stock behaviour, turn the other five off. For example, the
address reservation alone:

```sh
python3 scripts/repro-import.py --fork <toolchain> --project <mathlib> \
    --fork-env LEAN_LAZY_PARTS=0 --fork-env LEAN_TACTIC_INDEX=0 --fork-env LEAN_SEARCH_INDEX=0 \
    --fork-env LEAN_NO_TOUCH=0
```

## Two of the switches are not symmetric

**Turning a change off is not always the inverse of turning it on**, because two of the six
have a *library-side* half that lives in the `.olean` files rather than in the binary.

* **The shell-first layout (improvement 2)** is a change to how olean parts are *written*. Setting
  `LEAN_OLEAN_LAYOUT=0` on a reader does nothing at all: the reader is layout-agnostic, and
  a stock `lean` reads a fork-written olean and vice versa. The switch only matters to the
  process doing the writing. To measure it you need Mathlib rebuilt from source twice — once
  with the layout and once without — which is ~43 min and ~11 GB each. Running the fork
  against the stock community olean cache means the layout change is *present in the binary
  and inert*, and that is exactly the configuration the scripts in this package use.
* **The prebuilt `exact?` index (improvement 5)** stores its entries in the `.olean` files of a library
  built with the fork. `LEAN_SEARCH_INDEX=0` makes the reader ignore them and walk instead, so
  the switch does work in the off direction — but the *on* direction has two different
  sources: stored entries (needs a rebuilt library, +1.6 % on `.olean` size) or a per-closure
  side file named by `LEAN_SEARCH_INDEX_CACHE_DIR` (51 MB, written by the first walk, ~12 s of CPU
  once). The manuscript's macOS numbers come from the side file; the Linux numbers come from
  stored entries, and the stored path is slightly *better* than the cached one it stood in
  for — the first `exact?` costs +0.12–0.22 s against +0.52–0.87 s.

Two of the six are also platform-specific in the other direction: the address reservation is
macOS-only (Linux gets the range-registration variant instead), and the no-touch change is
the one whose motivation is weakest under lazy loading — with `LEAN_LAZY_PARTS=all` most of the
constant map is never walked at all. It stays in the tree because it is what makes
`LEAN_LAZY_PARTS=0` tolerable and because the Linux port shares its range table.

## Expect non-monotone combinations

Turning things on one at a time does **not** produce a monotone improvement, and this is a
real property of the work rather than measurement noise. Lazy part loading alone makes the
first `exact?` of a process *slower than stock* — 22.2 s against 15.3 s — because the
library-search walk then faults in nearly every module it had avoided. A beginner's editor
session with the fork as it stood before the two index changes took 27.5 s of waiting against
stock's 23.7 s. With
all six on, the same session is 11.3 s and the same `exact?` case is 3.73 s. The prebuilt
index removes the walk; the index image shrinks the heap the walk traverses; neither alone
is enough.


## The reference-count and layout changes overlap, and one of them has a shelf life

The no-touch reference counts (`LEAN_NO_TOUCH`) and the shell-first layout
(`LEAN_OLEAN_LAYOUT`) attack the same waste from two sides, and where both apply **the layout
supersedes**: with a rebuilt library, turning the reference-count change off costs 47 MB of the
2.9 GB the layout saves, and what it still buys is page faults rather than resident memory.

Both ship anyway, because they are not interchangeable:

| | with Mathlib's **downloaded** precompiled library | with a library **rebuilt from source** |
|---|---|---|
| shell-first layout (`LEAN_OLEAN_LAYOUT`) | **no effect** — the layout is written at build time | 5.68 → 2.76 GB |
| no-touch reference counts (`LEAN_NO_TOUCH`) | 5.69 → 3.44 GB | +47 MB on top of the layout |

Almost everyone downloads the precompiled library, so **the reference-count change is the one
that works today** and the layout is the better answer for anyone who can rebuild.

**For whoever maintains this:** the reference-count change is a bridge and the layout is the
destination. If Mathlib distributed its compiled library in the new layout — a change to how it is
built, not to what it contains, and byte-neutral in size — the reference-count change could be
retired, and with it the only part of this work that needed a separate port per platform. Until
then, retiring it would remove the memory saving for the majority of users.


## The tactic index image changed shape (2026-09-01): it is a shipped artifact, not a cache

`LEAN_TACTIC_INDEX_CACHE_DIR`, `LEAN_TACTIC_INDEX_EXTS`, `LEAN_TACTIC_INDEX_TIMING`, and the bounding variables
`LEAN_TACTIC_INDEX_MIN_FREE` / `_MAX_SIZE` / `_MIN_USES` **no longer exist**, and neither does
`~/.cache/lean-tactic-index`. The image is now written on purpose, once, by whoever distributes a library:

```
LEAN_TACTIC_INDEX_WRITE=1 lean <a file with the import line you want an image for>
```

and it is written beside that library's `.olean` files (`.lake/build/lib/lean` for a Lake package),
keyed by toolchain and library revision exactly as the oleans are. `lake exe cache get` would
deliver a matching image or none; `rm -rf .lake` removes it along with everything else.

**At run time nothing is ever written.** If an image for the exact import set is present it is
mapped; if not, Lean rebuilds the tables as it always has. `LEAN_TACTIC_INDEX=0` still disables the read.
`LEAN_TACTIC_INDEX_DIR` overrides where the reader and writer look.

Why: as a cache this filled disks. A Mathlib build from source presents 8,705 distinct import sets,
so it wrote 255 GiB to answer 216 lookups and added 58% to the build; capping it turned a 10% gain
on a real project into a 19% loss as it thrashed. Every one of those is a property of the cache and
none of the image. See [`improvements/06-tactic-index-image.md`](improvements/06-tactic-index-image.md).

**The lazy-loading index directory is still a cache** and still has `LEAN_LAZY_PARTS_MIN_FREE` /
`_MAX_SIZE` / `_MIN_USES`. Only the tactic index image changed shape.
