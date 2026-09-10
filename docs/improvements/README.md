# Technical notes, one per change

These are the documents for a reader who wants to decide whether a change is *sound*, not
merely whether it is fast. Each one states the defect concretely enough to be checked, names
the files and functions the patch touches, gives the correctness argument and the invariant it
rests on, reports the verification and what the verification does not cover, and lists what is
unfinished, provisional or known to be wrong.

They are written to be read next to the patches in [`patches/`](../../patches). Where a
document and a patch disagree, the patch wins; the disagreements we know about are recorded in
the documents themselves.

## The fork

Six changes, one patch: [`patches/lean4-v4.33.1-optimized.patch`](../../patches/lean4-v4.33.1-optimized.patch),
16 files, **+2,119 / −82** against Lean `v4.33.1` (`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`).
None of them changes what Lean accepts, what it prints, or in what order it suggests lemmas.

| # | Change | Document | Where the code is |
|---|---|---|---|
| 1 | The macOS address reservation | [`01-address-reservation.md`](01-address-reservation.md) | `src/library/module.cpp`, `src/runtime/process.cpp`, `src/CMakeLists.txt` |
| 2 | The shell-first `.olean` layout | [`02-olean-layout.md`](02-olean-layout.md) | `src/runtime/compact.{h,cpp}`, `src/library/module.cpp`, `src/Lean/Environment.lean` |
| 3 | No-touch reference counts | [`03-no-touch-refcounts.md`](03-no-touch-refcounts.md) | `src/include/lean/lean.h`, `src/runtime/object.cpp`, `src/library/module.cpp`, `src/Lean/Environment.lean` |
| 4 | Lazy part loading | [`04-lazy-part-loading.md`](04-lazy-part-loading.md) | `src/Lean/Environment.lean`, `src/Lean/Compiler/IR/CompilerM.lean`, `src/Lean/Parser/Extension.lean`, `src/runtime/{object,io}.cpp` |
| 5 | The prebuilt search index | [`05-prebuilt-search-index.md`](05-prebuilt-search-index.md) | `src/Lean/Meta/Tactic/LibrarySearch.lean`, `src/Lean/Meta/LazyDiscrTree.lean`, `src/Lean/Environment.lean`, `src/Lean/MonadEnv.lean`, `src/Lean/Elab/Frontend.lean` |
| 6 | The tactic index image | [`06-tactic-index-image.md`](06-tactic-index-image.md) | `src/Lean/Environment.lean`, `src/runtime/io.cpp` |

## Outside the fork

| Document | What it is |
|---|---|
| [`07-snapshot-wrapper.md`](07-snapshot-wrapper.md) | `leansnap.py` — a validated layer over Lean's own `--incr-header-save` / `--incr-load`. **No patched Lean.** |
| [`09-code-generation-fix.md`](09-code-generation-fix.md) | A fix for an **already-reported** upstream crash ([lean4#14359](https://github.com/leanprover/lean4/issues/14359)). Changes behaviour, so it ships outside the fork. |
| [`11-language-server-config-fix.md`](11-language-server-config-fix.md) | The language server does not watch `lakefile.toml` / `lakefile.lean` / `lake-manifest.json`. Changes behaviour, so it ships outside the fork. |
| [`08-compiled-mathlib-tactics.md`](08-compiled-mathlib-tactics.md) | Mathlib's tactics compiled to native code, using only the C files a Mathlib checkout already has. **No patched Lean and no change to Mathlib** — but it needs the code-generation fix. |
| [`10-fork-safety.md`](10-fork-safety.md) | Whether a Mathlib-loaded Lean process can be forked. It cannot, deterministically; the patch removes the one obstacle a caller cannot work around, and the document lists the five conditions that remain. |

## One convention used throughout

**Novelty claims are bounded.** Where a document says *"we found no public report"*, that
means the GitHub issue and PR trackers and the public web were searched. The Zulip archive is
stale and its live instance is login-walled, so an unnoticed prior report there is possible.
The one change that is definitely **not** novel — the code-generation fix — says so at the top.

## Machine and conditions, unless a document says otherwise

macOS 14.5, Apple M2 Pro, 12 cores, 32 GB, 16 KB pages. Warm page cache, one `lean` process at
a time, `/usr/bin/time -l`, interleaved repeats, the 1-minute load average stamped at every
run. Linux figures, where they appear, are labelled with their machine.
