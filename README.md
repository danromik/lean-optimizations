# Optimizing the Lean and Mathlib toolchain — the release package

This package accompanies the paper *Optimizing the Lean and Mathlib toolchain*, which is included
here in [`paper/`](paper/). 

*Note on AI usage by the project author (Dan Romik): the paper is written by me and is the careful writeup 
of the project meant for human readers. This README and the other technical documentss in the package are 
written by AI and either left untouched by me or only lightly edited. For more details on my AI usage, see 
the AI usage acknowledgement section of the paper (section 1.8).*

------

What follows is the code and the evidence behind it: eleven
improvements, of three kinds.

**Loading the library faster.** Six changes to how Lean maps and reads Mathlib, shipped as one
patch per platform: a warm `import Mathlib` goes from 10.2 seconds and 5.7 GB to 2.3 seconds and
1.4 GB, with byte-identical output. A seventh, needing no patched Lean, skips the import entirely
when many files are checked against the same header.

**Running it faster.** Mathlib's own tactics are Lean programs that a normal build leaves to the
bytecode interpreter. Compiling them — using only the C files a Mathlib checkout already
contains — takes elaboration processor time on our textbook suite from 22.9 to 6.2 seconds.

**Fixing what we broke or found broken.** A crash in Lean's code generator that compiling those
tactics exposes; an answer to whether a Lean process can be forked, with a partial fix; and a
language server that never notices when a project's build configuration changes, which is about
correctness rather than speed.

Everything here is against **Lean v4.33.1** (commit `819816b2e0a3bf405af45ae5c7af2491d8f5bee6`,
released 2026-08-21) and **Mathlib v4.33.1** (commit `0df444a360eaa60ab8c11dca51a86af692955474`).

---

## 1. The result

Running a file that says `import Mathlib` costs about ten seconds and 5.3 GB before Lean
reads your first line. Almost all of that is bookkeeping about *how* the library is loaded,
not work the program needs. The optimized build loads the same library the same way in
2.3 seconds and 1.4 GB, and produces byte-identical output.

| Situation | Before | After | Where |
|---|---|---|---|
| `import Mathlib`, warm, Mac (M2 Pro) | 10.2 s · 5.7 GB | **2.3 s · 1.4 GB** | full fork |
| `import Mathlib` **plus one `exact?`**, Mac | 15.3 s · 7.9 GB | **3.7 s · 1.6 GB** | full fork |
| `import Mathlib`, Linux container (arm64) | 2.44 s · 5.9 GB | **1.41 s · 1.6 GB** | full Linux fork |
| `import Mathlib`, AWS `t4g.large`, warm | 5.68 s · 5.26 GB | **3.99 s · 1.75 GB** | full Linux fork |
| `import Mathlib`, AWS `t4g.large`, cold | 104.1 s | **35.4 s** | full Linux fork |
| Import + first `exact?`, `t4g.large` | 30.68 s · 6.33 GB | **11.47 s · 2.50 GB** | full Linux fork |
| Full build, 1,387-file project, Mac | 47.7 min · 68 GB peak | **23.0 min · 34 GB** | fork |
| One check on the `t4g.large`, warm | 5.8 s · 6.0 GB | **1.5 s · 1.8 GB** | snapshot, **unmodified** Lean |
| One check on the `t4g.large`, forked worker | 5.9 s | **0.25 s** | fork-the-loader (Linux only) |
| One check on the `t4g.large`, cold | 104 s | 34 s | snapshot |
| Ten checks in a row on the `t4g.large` | 60 s | 15–20 s | snapshot |
| Beginner's editor session, total waiting on Lean | 23.7 s | **11.3 s** | full fork |
| `exact?` first call in the editor | 4.7 s · +2 GB | **1.4 s · +0.2 GB** | full fork |
| Editor worker memory, peak during a session (RSS) | 8.0 GB | **3.7 GB** | full fork |
| Lake per-invocation start-up | 11.2 s | 0.74 s | `posix_spawn` |
| Tactic elaboration CPU, textbook suite | 22.9 s | 6.2 s | compiled Mathlib tactics |

The optimized build is six changes: reserving the library's address range in one go so
macOS stops searching for gaps (and switching process spawning from `fork` to
`posix_spawn`); writing the record "shells" at the front of each `.olean`; not reading the
reference counter of records that live in mapped files; loading private parts and compiled
code on first demand; a prebuilt `exact?` index; and one mappable image holding the tactic
indexes instead of rebuilding them at every start. [`docs/switches.md`](docs/switches.md) names each
one and the environment variable that turns it off.

**Read [`warnings.md`](docs/warnings.md) before running anything that rebuilds a library.** One of
the six changes used to fill your disk on a from-source Mathlib build; it was redesigned on
2026-09-01 and no longer writes anything at run time, and `warnings.md` §1 is now the record of
why rather than an instruction. The lazy-loading index is still a bounded cache and still costs
disk.

---

## 2. The delivery boundary — what is in the fork and what is not

This is the organising principle of the package, and it is deliberate.

**`lean4.33.1-optimized` — the fork — contains only changes that leave Lean's observable
behaviour unchanged.** Same language, same proofs accepted, same error messages, same tactic
suggestions in the same order. It is 16 files, +2,119/−82, in
[`patches/lean4-v4.33.1-optimized.patch`](patches/lean4-v4.33.1-optimized.patch).

**Three bug fixes ship separately**, one patch each, because each one *changes* behaviour —
for the better. Mixing a behaviour change into a performance fork would make the fork's
central claim untestable, and would force anyone who wants the fix to take the fork with it.
`scripts/check-patches.sh` verifies that each applies on its own. Two of them are adoptable
independently of the fork as well, and the reason is structural — the fork touches 16 files
and those two touch four further files that the fork does not touch. The fork-safety fix is
the exception: it edits `src/runtime/object.cpp`, which the fork edits too.

| Patch | What it changes | Status upstream |
|---|---|---|
| [`fix-codegen-meta-initialize.patch`](patches/fix-codegen-meta-initialize.patch) | `meta_initialize_<M>` never runs `runtime_initialize_<M>`, so compiled meta code can dereference globals that were never assigned | **Already an open upstream issue, [lean4#14359](https://github.com/leanprover/lean4/issues/14359)** (open, `P-high`, filed 2026-07-10). We did not discover this; what we add is a manifestation outside the specialization case the issue describes, a stock-toolchain reproducer, and a built and exercised fix |
| [`fix-fork-safety.patch`](patches/fix-fork-safety.patch) | A forked child of a Lean process that has run any task hangs, because the inherited count of idle worker threads describes threads that `fork` did not copy | The question is asked and unanswered in [repl#87](https://github.com/leanprover-community/repl/issues/87) and [repl#84](https://github.com/leanprover-community/repl/issues/84). A partial fix: it removes the one obstacle a caller cannot work around |
| [`fix-server-config-watch.patch`](patches/fix-server-config-watch.patch) | The language server watches `*.lean` and `*.ilean` and nothing else, so editing `lakefile.toml` never reaches a running server | No public report found |

**Two results in the manuscript need no patched Lean at all:**

* the **snapshot wrapper** ([`code/tools/leansnap.py`](code/tools/leansnap.py)) works with a stock
  toolchain today: it wraps Lean's own experimental snapshot loader and adds the validation
  Lean omits. On a `t4g.large` it takes a check from 5.8 s to 1.5 s.
* **compiled Mathlib tactics** are a build recipe, not a patch — `lake build Mathlib:shared …`
  plus a three-line wrapper `lean`. The speed-up needs no change to Lean; it needs the
  codegen fix above to be *safe*, because without it some files crash deterministically.
  See [`code/tools/README.md`](code/tools/README.md).

---

## 3. What is claimed, and what is not

**Claimed.** Every number in the table above was measured, on a named machine, with a named
Lean and Mathlib commit, and the document that reports it is named in
[`evidence.md`](docs/evidence.md). The equivalence claim — that the fork's output is byte-identical
to stock Lean's — is checked by a suite you can run yourself
(`code/scripts/check-equivalence.sh`), and it passes 26/26 on macOS/arm64 and 27/27 on Linux, in
both the switches-on and the switches-off configuration.

**Not claimed.**

* **Not "novel", only "no public report found".** Where this package says a defect or a
  technique is new, that means a search of the `leanprover/lean4` issue tracker, the
  `vscode-lean4` tracker, the Zulip public archive and the current development branch source
  turned up nothing. The Zulip *public* archive is frozen at 2026-02-28 and the live instance
  is login-walled, so roughly six months of the community's main discussion channel is
  invisible to us. Every "nothing found" inherits that gap. One of the three defects —
  the codegen one — turned out on inspection to be known, which is the honest illustration
  of how much weight the phrase can carry.
* **Not a proof of equivalence.** The suite is 26 files chosen to move if the fork perturbed
  a `DiscrTree` insertion order, an instance priority, a library-search candidate ordering or
  an axiom set, plus a `lake build` whose generated `.c` and `.ilean` files are compared byte
  for byte. It is evidence, not a theorem.
* **Not production-ready.** One of the six changes — lazy part loading — writes a per-closure
  side file, a cache with a size cap, LRU eviction and a free-space floor whose three defaults
  are not yet justified by anything. A second, the tactic index image, writes nothing at run
  time but is only useful if somebody built an image for your exact import closure *and
  configuration*, which no library does today and which a language-server worker does not get
  even when they have (see [`docs/improvements/06-tactic-index-image.md`](docs/improvements/06-tactic-index-image.md)).
  Both belong in the `.olean` files in a real version.
* **Cold-cache seconds measured in the Docker VM are not publishable.** `drop_caches` clears
  the guest's page cache but the macOS host's cache of the VM's disk image survives, and it is
  warmer for one tree than the other. Major-fault *counts* are unaffected and do transfer;
  the seconds do not. The cold numbers quoted in the table above are from real hardware (a
  `t4g.large`), not from the VM.
* **Memory figures depend on the machine's memory headroom.** See below.

### About the memory numbers

Two independent caveats, both of which change what an RSS figure means.

1. **RSS counts shared pages.** These are resident-set sizes, and they count memory-mapped
   library pages in every process that maps them — but the operating system holds those pages
   once and shares them. On an idle post-import process: 5.68 GB resident, of which ~4.9 GB is
   clean mapped library and only **0.86 GB is genuinely private**. Three editor workers cost
   roughly 4.9 GB once plus ~0.9 GB each, not three times 5.3 GB. Our changes cut both
   numbers, but "N workers duplicate N copies of the library" is wrong.
2. **On Linux, RSS depends on how much free memory the machine has.** Linux's fault-around
   maps up to 64 KB of *already-cached* neighbours around every page a process actually needs.
   On a machine with the whole library in cache, every fault brings sixteen extra pages; on a
   machine that has to evict, few. The inflation is roughly constant per fault, so it flatters
   a configuration that faults on most of the file and penalises one that faults on very few.
   Measured naively on a 32 GB x86-64 box, one change's saving reads as −24 % where an 8 GB
   arm64 box measured −48 %; re-run inside an 8 GB cgroup, everything else identical, the
   8 GB figures come back to within 2 %. **Quote RSS at the memory size the target machine
   will have.** Wall time, CPU, VMA counts, major faults and equivalence are unaffected.

---

## 4. What is in this package

```
README.md            this file
AGENTS.md            the same ground, indexed for an AI assistant
LICENSE              Apache-2.0, covering patches/ and code/
LICENSE-DOCS         CC BY 4.0, covering docs/ and the paper

paper/               the paper: paper.pdf and its LaTeX source

patches/             the five patches, one per separately adoptable change
code/
  tools/             programs you run: leansnap.py, lean-native-wrapper.sh
  scripts/           setup, checking and reproduction
  benchmarks/        lean-perfbench: the harness, the case files, the editor
                     sessions, and the runs the paper's figures come from
docs/
  improvements/      one document per improvement, 01 to 11
  building.md        build the fork; point your editor at it; the trap we fell into
  switches.md        the runtime switch that turns each improvement off
  warnings.md        read before rebuilding any library with the fork
  evidence.md        every claim, mapped to the document and the runs behind it
  testing.md         these instructions followed from cold: what broke, what was
                     fixed, and what is still untested
  development-labels.md   the short labels the work carried while it was being
                     done, and what each one means
```

There is no Lean source here and no compiled binary. Using the fork means building it: clone
Lean's own repository, check out `v4.33.1`, apply the patch, build. About half an hour and 10 GB
the first time. [`docs/building.md`](docs/building.md) has the steps.

## 5. Where to start

1. **Just want the numbers checked?** Build the fork ([`docs/building.md`](docs/building.md) §1, about
   half an hour and 10 GB the first time), then `code/scripts/repro-import.py`. It builds nothing
   itself and takes about five minutes.
2. **Want to see the fork behave identically?** `code/scripts/check-equivalence.sh`, about eight
   minutes.
3. **Want to try it in your editor?** [`docs/building.md`](docs/building.md) §2 — and read §2.3 first,
   which is the mistake that makes people think they are testing the fork when they are not.
4. **Want one change at a time?** [`docs/switches.md`](docs/switches.md).
5. **Want the fastest per-check time on a small Linux server with no patched Lean?**
   [`code/tools/README.md`](code/tools/README.md) §1.
6. **Want to know whether a change helps the person in the editor, not the build server?**
   [`code/benchmarks/interactive/README.md`](code/benchmarks/interactive/README.md). This is the instrument that twice caught a
   change our batch benchmarks called an improvement and which made interactive use worse, and
   it is the one part of the suite you can point at your own project and your own habits.

---

## 6. Provenance and licensing

The five patches in `patches/` are diffs against
[leanprover/lean4](https://github.com/leanprover/lean4) at tag `v4.33.1`, which is licensed under
Apache-2.0; they are derivative of it and inherit that licence. Everything else here is original
work: `code/tools/leansnap.py` and the wrapper, the benchmark harness, the scripts, the case
files, and the documents.

The package carries two licences, and the boundary is the directory split:

* **`patches/` and `code/` — Apache-2.0** ([`LICENSE`](LICENSE)). The same licence as Lean and
  Mathlib, so anything here can be taken upstream with no compatibility question.
* **`docs/` and the paper — CC BY 4.0** ([`LICENSE-DOCS`](LICENSE-DOCS)), the licence written for
  prose rather than for software.

The Lean case files in `code/benchmarks/cli/textbook/` were written for this project; the seven
files in `code/benchmarks/cli/equivalence/` are the equivalence suite and are likewise ours.
