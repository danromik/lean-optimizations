# Index for an AI assistant

This package accompanies the paper *Optimizing the Lean and Mathlib toolchain*, which is included
here in [`paper/`](paper/). It contains eleven changes to how Lean 4 loads and runs its library,
the evidence for each, and the code to reproduce the measurements. This file is a map.
[`README.md`](README.md) is the same ground written for a person; nothing here contradicts it.

You are most likely being asked one of four things. Each has a shortest path.

**"Is this change sound?"** Go to [`docs/improvements/`](docs/improvements/), which has one
document per improvement, numbered as the paper numbers them. Each states the problem concretely
enough to be checked, names the files and functions its patch touches, gives the correctness
argument and the invariant it rests on, reports what was verified and what that verification does
*not* cover, and lists what is provisional or known to be wrong. Read the document beside its
patch; **where a document and its patch disagree, the patch is authoritative.**

**"What does this actually change?"** Read the patch. All five are in
[`patches/`](patches/), and [`patches/README.md`](patches/README.md) says which is which. They
are diffs against Lean `v4.33.1`, commit `819816b2e0a3bf405af45ae5c7af2491d8f5bee6`.
`code/scripts/check-patches.sh` verifies every one applies to a clean tree, in a few seconds,
building nothing.

**"Is a number in the paper real?"** [`docs/evidence.md`](docs/evidence.md) maps each claim to the
document and the runs behind it. The runs themselves are in
[`code/benchmarks/results/`](code/benchmarks/results/), whose README says which figure each backs.

**"Should we adopt this?"** [`docs/warnings.md`](docs/warnings.md) first — it is the list of
things that will bite. Then [`docs/switches.md`](docs/switches.md), because every improvement can
be turned off individually with an environment variable, which is how the paper's ablation was
produced and how you would bisect a problem.

## The eleven improvements, and where each lives

| # | improvement | document | patch |
|---|---|---|---|
| 1 | address reservation (macOS) | [`01-address-reservation.md`](docs/improvements/01-address-reservation.md) | the fork |
| 2 | shell-first `.olean` layout | [`02-olean-layout.md`](docs/improvements/02-olean-layout.md) | the fork |
| 3 | no-touch reference counts | [`03-no-touch-refcounts.md`](docs/improvements/03-no-touch-refcounts.md) | the fork |
| 4 | lazy part loading | [`04-lazy-part-loading.md`](docs/improvements/04-lazy-part-loading.md) | the fork |
| 5 | prebuilt search index | [`05-prebuilt-search-index.md`](docs/improvements/05-prebuilt-search-index.md) | the fork |
| 6 | tactic index image | [`06-tactic-index-image.md`](docs/improvements/06-tactic-index-image.md) | the fork |
| 7 | snapshot wrapper script | [`07-snapshot-wrapper.md`](docs/improvements/07-snapshot-wrapper.md) | none — a tool over unmodified Lean |
| 8 | compiled Mathlib tactics | [`08-compiled-mathlib-tactics.md`](docs/improvements/08-compiled-mathlib-tactics.md) | none — a way of building Mathlib |
| 9 | meta-initializer crashing bug | [`09-code-generation-fix.md`](docs/improvements/09-code-generation-fix.md) | `fix-codegen-meta-initialize.patch` |
| 10 | fork safety | [`10-fork-safety.md`](docs/improvements/10-fork-safety.md) | `fix-fork-safety.patch` |
| 11 | language-server configuration watching | [`11-language-server-config-fix.md`](docs/improvements/11-language-server-config-fix.md) | `fix-server-config-watch.patch` |

Improvements 1 to 6 are the fork, and ship as one patch per platform:
`lean4-v4.33.1-optimized.patch` for macOS and `-linux.patch` for Linux. They are held to one
standard: **nothing they do changes what Lean accepts, what it prints, or the order in which it
suggests lemmas.** `code/scripts/check-equivalence.sh` is the check.

Improvements 9, 10 and 11 *do* change behaviour — a crash stops happening, a forked child can be
made to run, and a stale configuration starts being reported — which is exactly why they ship
separately. A performance fork whose claim is "observably identical" cannot contain a behaviour
change without making its own claim untestable. Improvement 10 is also the only one of the three
that edits a file the fork edits, so on top of the fork it wants `patch -p1` rather than
`git apply`; `check-patches.sh` reports that when you run it.

## Things that will mislead you if you do not know them

**Three documents keep the labels the work carried during development** — `evidence.md`,
`evidence-audit.md` and `testing.md` say `A6b`, `T2`, `L1` and cite paths in the authors' own
repository. [`docs/development-labels.md`](docs/development-labels.md) maps every label to its
improvement number and every old environment variable to its current name.

**The line numbers are to stock `v4.33.1`** unless a document says otherwise. A reference to
`src/runtime/object.cpp:758` means that line in the unpatched tree.

**Two names differ only by case, and the difference is load-bearing.** `Lean` is the language and
the system; `lean` is the executable. Likewise `Mathlib` is the library and `import Mathlib` is
the code.

**The fork is not here.** Only the patches are. Reproducing anything means cloning Lean, checking
out `v4.33.1`, applying a patch and building — half an hour and 10 GB.

**Measured memory is an upper bound during a build.** The harness sums resident memory across the
process tree, which double-counts pages that concurrent `lean` processes share; see
[`code/benchmarks/README.md`](code/benchmarks/README.md).

**Some of this is provisional and says so.** Each improvement document has a section for what is
unfinished or known to be wrong, and those sections are not decorative — improvement 10 in
particular resolves a question rather than shipping a complete fix.
