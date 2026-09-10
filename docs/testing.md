# Following these instructions from cold — what happened

> **Written during development.** This document uses the short labels the work carried at
> the time (`A6b`, `T2`, `L1` and so on) and cites paths in the authors' research
> repository, not all of which are part of this package.
> [`development-labels.md`](development-labels.md) is the key to both.

The instructions in this package were, until now, transcribed from internal working documents
rather than executed. This file records the first end-to-end run of them by someone starting
from a clean tree and typing what is written, and it is the reason for the corrections now in
`building.md`, `patches/`, `scripts/` and the READMEs.

Run on 2026-09-01, on the M2 Pro (12 cores, 32 GB, macOS 14.5) named in `evidence.md`.

**Verdict: yes, the fork builds from this package as written, and the result passes.** Six
defects stood between a stranger and that result. All six are fixed; none of them was in the
patch's code. What remains untested is listed in §4.

**§5 records a second exercise, later the same day: the interactive benchmark
([`interactive/`](../code/benchmarks/interactive)) was packaged and then run from the package. Four more defects
had to be fixed to make it shippable; all six of its Mathlib sessions then passed, 77
assertions, zero failures.**

> ### §§1–4 are SUPERSEDED by §7 (2026-09-01, evening)
>
> Everything in §§1–4 was run against the patch **as it stood that morning**: 15 files,
> +1,799/−82, carrying the tactic index image in its *cache* form. The patch was regenerated
> later the same day from the `a6b-shipped-image` branch and was then **16 files, +2,101/−82**,
> carrying the image as a shipped artefact instead. Two further commits have landed since, so the
> patch that ships today is **16 files, +2,119/−82** (Linux: **+2,150/−82**); the figures quoted
> in the dated sections below are the ones that were current when each section was run. The six defects §2 records are all still
> fixed and the equivalence verdict still stands, but §1's table row, its "15 files" and its
> measurement of the fork are a record of the earlier patch. §7 re-verifies the patch that
> actually ships, and finds four more defects — one of them in these very scripts.

---

## 1. What was run, and what it did

Starting point: a clean checkout of `leanprover/lean4` at tag `v4.33.1`
(`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`) in a scratch directory. No part of the existing
fork tree was reused.

| Step | Source | Result |
|---|---|---|
| `scripts/check-patches.sh --lean4 <clone>` | `patches/README.md` | **pass** — all five patches apply alone, each fix applies on top of the fork, and all four apply cumulatively. 8 s, 668 MB of temporary export |
| `git apply --binary patches/lean4-v4.33.1-optimized.patch` | `building.md` §1 | **pass** — 15 files, +1,799/−82, exactly as claimed once the stray file is removed (see §2.1) |
| `cmake --preset release -DLEAN_GITHASH_OVERRIDE=… -DLEAN_PLATFORM_TARGET=…` | `building.md` §1.1 | **pass** — no prerequisite was missing beyond the four named (`cmake`, `gmp`, `libuv`, `pkgconf`, clang) |
| `cmake --build --preset release -- -j8` (stage 1) | `building.md` §1.1 | **pass** — 7 min 19 s |
| `cmake --build --preset release --target stage2 -- -j8` | `building.md` §1.1 | **pass** — 12 min 25 s; 19 min 45 s and 9.6 GB in total |
| `elan toolchain link` + version check | `building.md` §1.3 | the link works; **the version command as written did not exist** (§2.2) |
| `scripts/repro-import.py --fork <the tree just built>` | `scripts/README.md` | **pass** — numbers below |
| `scripts/check-equivalence.sh --fork <same>` | `README.md` §5 | **pass — 26/26 byte-identical** in stdout, stderr and exit code, about 6 minutes |
| `lean-toolchain` selection, `lake env printenv LEAN_SYSROOT`, `elan show`, `LEAN_A*_VERBOSE=1` | `building.md` §2.1, §2.3 | **pass**, after §2.3's first check was replaced (§2.2) |

### The build

Both `-D` flags behave as `building.md` §1.1 says: the built `lean --version` prints the stock
v4.33.1 string byte for byte, and the stock community `.olean` cache loads under it unchanged.

The build ran while another job was saturating the machine — load average 40 to 55 throughout,
against 12 cores. `building.md`'s "about 25 minutes from scratch" therefore stands; 19m45s
under 4× oversubscription is consistent with it, and `README.md`'s "about an hour" was the
outlier. It has been corrected to "about half an hour".

### The headline measurement

`repro-import.py` against the toolchain built above, Mathlib v4.33.1 with the community olean
cache, medians of 3 interleaved runs:

```
| case           | config | wall s | user s | sys s | max RSS GB | minor faults |
| import-mathlib | stock  |  10.35 |   1.67 |  8.53 |       5.29 |       423249 |
| import-mathlib | fork   |   3.25 |   1.34 |  1.30 |       1.39 |       166445 |
```

Against the sample run in `scripts/README.md` — 10.2 s / 5.29 GB / 397,918 faults and 2.3 s / 1.40 GB / 167,993:
**max RSS and minor faults reproduce to within a couple of percent.** Wall time does not, and
the reason is the load: the same session's fastest fork run was 2.46 s and its slowest stock
run 16.0 s. The ratio came out 3.19× rather than 4.4×. Treat the seconds here as indicative
only; the RSS and fault columns are the part that transferred cleanly, which is exactly what
`scripts/README.md` says to expect ("your wall times will differ with load, disk and machine;
the *ratios* and the fault counts are the comparable part" — on an unloaded machine).

Side files written: 480 MB under `--cache-dir`, matching the "~500 MB" estimate — 112 MB in the
script's `lazy-parts` directory (the lazy-loading index) and 368 MB in its `tactic-index`
directory (the tactic index image). Its `search-index` directory stayed empty, as it should:
`import Mathlib` alone never calls `exact?`.

### The equivalence suite

26/26 byte-identical, `VERDICT: EQUIVALENT`, against a fork built from the patch in this
package rather than from the project's own tree. `eq-goals` produced 5,352 lines of stock
output, the figure `cases/README.md` names.

---

## 2. The six defects, all now fixed

### 2.1 The fork patches created a stray file, and it was being counted

Both `lean4-v4.33.1-optimized.patch` and `-linux.patch` began with a stanza creating an empty
`.metadata_never_index` at the repository root — a macOS Spotlight-exclusion marker that had
been picked up from the author's working tree. Harmless to the build, but it is the first
thing in the patch a reader opens, and it was being counted: "16 files" throughout the package
was 15 real files plus this one.

Fixed: the stanza is removed from both patches, and every "16 files" is now "15 files". The
`+1,799 / −82` line counts are unaffected — the stray file was empty.

### 2.2 `building.md` §1.3's verification command does not exist

```sh
lean-fork/bin/lean --version    # zsh: no such file or directory
```

`lean-fork` is an elan toolchain name, not a directory in the working directory. Replaced with
`elan run lean-fork lean --version`, and the expected output is now quoted in full.

### 2.3 `building.md` §2.3's first "way to be sure" cannot work

§2.3 told you to run `lake env lean --version` and check that it "must print the fork's
toolchain, not the stock one". It cannot: §1.1 of the same document explains that
`LEAN_GITHASH_OVERRIDE` makes the fork print the stock version string, and it does — the two
lines are byte-identical. Confirmed directly: the fork and the stock toolchain, and
`repro-import.py`'s own banner for both configurations, all print

```
Lean (version 4.33.1, arm64-apple-darwin24.6.0, commit 819816b2e0a3bf405af45ae5c7af2491d8f5bee6, Release)
```

This is the worst of the six, because §2.3 exists to stop you from believing you are testing
the fork when you are not, and its first check would have confirmed that false belief.

Fixed: the check is now `lake env printenv LEAN_SYSROOT`, which names the toolchain
*directory* and does distinguish them (verified: it printed the scratch build's `stage2` path
in a project whose `lean-toolchain` named the fork). §1.3 now states the ambiguity where the
override is introduced. §2.3's second method — `LEAN_TACTIC_INDEX_VERBOSE=1 LEAN_LAZY_PARTS_VERBOSE=1` — was
verified to work exactly as described: the fork logs `lazy-parts: mode 2, index … (hit)` and `tactic-index:
cache … (hit, 93 extensions, mmap=true)` on stderr — the log prefixes, like the variables, carry
the changes' development names — and stock Lean with the same variables set prints nothing.

### 2.4 The bug fixes' file count was wrong

`README.md` and `patches/README.md` both understated how many further files the separately
shipped fixes touch. The claim that matters — that they are disjoint from the fork's own
files — holds. Corrected.

### 2.5 `check-patches.sh` claimed a composition it did not test

Its header said it verifies the fixes "compose with the fork and with each other", and
`patches/README.md` said it "demonstrates all four applying together". It did neither: it
checked each fix against a fork-patched tree independently and never applied more than one.

Fixed by making the claim true rather than by weakening it — the script now applies all three
cumulatively on top of the fork as a final stage. All four do apply together.

### 2.6 Smaller things

* Five equivalence cases exit 1 by design, not four (`eq-goals`, `eq-pp`, `eq-simp`,
  `eq-synth`, `eq-tactics`). Corrected in `cases/README.md`, `scripts/README.md` and
  `check-equivalence.sh`.
* `check-equivalence.sh` said "the 6 … files in `../cases/equivalence`"; there are 7. Its own
  total of 26 was right (7 + 15 + 4).
* `--help` on `check-patches.sh` and `check-equivalence.sh` printed the first few lines of
  shell code after the comment block. Line ranges corrected.
* `repro-import.py` and `check-equivalence.sh` referred readers to `docs/WARNINGS.md`,
  `docs/BUILDING.md` and `docs/SWITCHES.md` — paths from the research repository that do not
  exist in this package. Rewritten to point into this package's `docs/`.
* `check-patches.sh` was billed at "~1 min" in three places; it takes 8 seconds. The 700 MB
  temporary figure is right (the export measured 668 MB).
* `--binary` was described as "required". It is a documented no-op on current git, which
  applies binary hunks unconditionally; the fork patch applies without it. Kept in the recipe
  for older git, but no longer described as required.

---

## 3. Things a stranger would still trip over

Not defects in the instructions, but worth knowing before you conclude anything.

* **The fork is invisible to `--version` by design.** This follows from
  `LEAN_GITHASH_OVERRIDE` and it is the point of that flag, but it means no command that
  prints a version can tell you which binary you are running. Use the path or the verbose
  logs. Everything in §2.3 exists because of this.
* **`building.md` §2.3 and `warnings.md` §4 look contradictory.** §2.3 tells you to run
  `LEAN_TACTIC_INDEX_VERBOSE=1 … lake env lean file.lean`; §4 says never run a fork binary inside
  `lake env`. Both are right: §4's trap is invoking a *fork binary by path* while `lake env`
  points the dynamic loader at a *stock* toolchain. When the project's `lean-toolchain` names
  the fork, `lake env` points at the fork's own `lib/lean` and there is no mismatch. The two
  sections do not say this to each other.
* **A `stage2` directory is not quite a released toolchain.** It has `bin/`, `lib/`, `share/`
  and `include/`, and elan links it happily — but it also carries the whole CMake build tree
  around it, so the "toolchain" you link is 9.6 GB. Fine for measurement; not something to
  hand to anyone.
* **`repro-import.py`'s first stock run is not warm.** On a machine whose page cache does not
  already hold Mathlib, warm-up pass 1 took 37.6 s against the 10.35 s it settled at. The
  script's warm-up handles this; do not read pass-1 numbers.

---

## 4. What this run did not exercise

* **`scripts/setup-mathlib.sh`** — not run. It would clone Mathlib and download ~7 GB, and an
  existing v4.33.1 checkout at the right commit (`0df444a3…`) with its olean cache was used
  via `--project` instead. What was checked: the script parses and its `--help` is correct,
  and both tags it depends on resolve to the commits this package names —
  `leanprover/lean4` `v4.33.1` → `819816b2…`, `leanprover-community/mathlib4` `v4.33.1` →
  `0df444a3…`.
* **`building.md` §1.2, the Linux build** — not run; this is a macOS host. The Linux patch
  applies cleanly to a clean v4.33.1 (`check-patches.sh` covers that) but has not been built
  from this package's instructions. Its three named traps — absolute compiler paths, GMP
  ≥ 6.3.0, keeping `tests/` — are untested here.
* **`building.md` §2, the VS Code half** — the shell paths were verified (`lean-toolchain`
  file, `elan override`, `elan show`, `lake env printenv LEAN_SYSROOT`). The extension menu
  item, the profile advice in §2.2 and the wrapper-toolchain recipe in §2.1 were not.
* **Rebuilding Mathlib from source** (`building.md` §3, `warnings.md` §1 and §3) — deliberately
  not run. `LEAN_TACTIC_INDEX=0` is therefore still an unverified instruction in this package, though
  it is a well-documented finding elsewhere in the project.
* **`tools/`** — `leansnap.py` and the compiled-tactics recipe were not run. Both files parse.
* **The three bug-fix patches were not built or exercised**, only applied. `check-patches.sh`
  is an application check, not a build check, and does not claim otherwise.

---

## 5. The interactive benchmark, packaged and run

`interactive/` was added to this package on 2026-09-01 and then run from it, on the same M2 Pro.
Four things had to be fixed before it ran at all, and they are the kind that only appear when
the package is used rather than described.

### 5.1 What had to be fixed to make it shippable

* **`lspbench.py` is stdlib-only but it is not self-contained.** It inserts `code/benchmarks/harness/` on
  `sys.path` and imports three modules from it (result files, run metadata, Lean version
  probing). Shipping the script and the session files alone would have failed on the first line
  with `ModuleNotFoundError: meta`. Those three modules are now carried as `interactive/lib/`.
* **Result files would have landed outside the directory.** `meta.py` derives its results path
  from its own location (`<repo>/bench/results`), which in this package would have been
  `release/bench/results`. The shipped copy resolves it relative to `interactive/` instead —
  the single line of difference between the two copies, marked as such in the file.
* **`--all` would have swept sessions whose projects are not shipped.** It globs `*.json` in the
  sessions directory, so the four corpus sessions had to move to `sessions/corpus/` for
  `--all` to mean "the six that work out of the box".
* **The `sessions/multi/` set is not uniformly standalone.** Four of the five need only Mathlib;
  `multi-04-corpus-same-header-ladder` needs the formal-conjectures checkout. All five ship;
  the README says which is which, and they are run by name rather than by `--all`.

### 5.2 The run

```sh
cd release/interactive
python3 lspbench.py run --all --project-dir mathlib=<the v4.33.1 Mathlib checkout>
```

Toolchain: the project's own, `leanprover/lean4:v4.33.1` — the toolchain `scripts/setup-mathlib.sh`
gives you. Mathlib at `0df444a360eaa60ab8c11dca51a86af692955474`, the commit this package names.
Six sessions, one repetition each, 7 minutes 40 seconds wall in total, 1-minute load 2.2–4.9
throughout (a Linux container measurement was running on the same machine).

**All six passed. 77 `expect_*` assertions, zero failures, every session ending with an
error-free file** — which is the property that makes a passing session mean something.

| session | header | edit med / p90 / max | Lean wait | worker RSS after header / peak | wall |
|---|---|---|---|---|---|
| `novice-01-induction-sum` | 38.15 s | 214 / 230 ms / 4.69 s | 47.18 s | 5.27 / 7.43 GB | 100.97 s |
| `novice-02-logic-sets` | 15.06 s | 212 / 217 ms / 4.81 s | 24.26 s | 5.29 / 7.43 GB | 77.69 s |
| `novice-03-real-inequalities` | 15.35 s | 214 / 218 ms / 4.66 s | 24.14 s | 5.26 / 7.44 GB | 72.57 s |
| `inter-01-finset-sums` | 6.54 s | 210 / 221 ms / 3.67 s | 13.13 s | 1.26 / 1.44 GB | 59.55 s |
| `inter-02-real-analysis` | 5.47 s | 207 / 208 ms / 209 ms | 8.99 s | 1.65 / 1.79 GB | 60.39 s |
| `inter-03-mathlib-topology` | 3.23 s | 208 / 212 ms / 212 ms | 5.41 s | 0.59 / 0.78 GB | 34.14 s |

Against the stock figures the manuscript reports for the same six sessions:

* **What reproduced almost exactly.** Median edit latency 207–214 ms against a reported
  207–210 ms — the server's debounce, unmoved. Peak worker RSS 7.43–7.44 GB against 7.44–7.45.
  The `exact?` edit 4.66–4.81 s against 4.6–4.8. First completion 1.24–1.35 s against 1.3–1.4.
  Goal requests 1–4 ms against 1–3. Novice `lean_wait` 24.1–24.3 s against 22.4–26.3 — which is
  the headline "about 24 seconds of waiting per beginner session".
* **What did not, and why.** `novice-01`'s header came out at 38.15 s against a reported 16.1 s.
  It was the first `import Mathlib` of the day and paid a cold page cache; the two novice
  sessions that followed it, on the same closure, came in at 15.06 and 15.35 s. The
  intermediate headers are roughly double their reported values (6.54 / 5.47 / 3.23 s against
  2.7 / 3.3 / 2.5) because these are 2–5 s measurements taken at load 3–5 and the fixed
  `lake setup-file` cost (4.51 s of `novice-03`'s header, recorded separately) dominates them.
  This is exactly the behaviour `README.md` and `interactive/README.md` §5 warn about: read the
  ratios and the minima, not the absolute seconds, and do not compare across machine states.

Nothing in the run contradicted a claim the package makes.

### 5.3 What this did not exercise

* **The four corpus sessions and `fetch-corpus.sh`** — not run. They need ~30 GB and three
  further Lean toolchains, and the machine was 4 GB above its disk floor. The script parses,
  its `--help` is correct, and all four repository URLs and the toolchain each project pins are
  as recorded. Nothing else about it is tested, including whether upstream drift has already
  broken the sessions' assertions.
* **The five multi-file sessions** — not run. `mreport` was not exercised either. The
  `vmmap`/`vm_stat` memory probes they use are macOS-only.
* **Any toolchain but the stock one.** The comparison the benchmark exists for — stock against
  the fork, in the same session — was not performed from this package. A fork toolchain was not
  built for this check.
* **`--repeat` above 1.** Every figure above is a single observation.

---

## 6. For the author

* `README.md` §6 still flags the missing `LICENSE`. Nothing here changes that.
* ~~The patches now differ from whatever produced them, by exactly the removed
  `.metadata_never_index` stanza. If they are regenerated from a branch, that file will come
  back unless it is removed from the branch too.~~ **Done, 2026-09-08.** The marker is no longer
  tracked on `rename-switches` or `rename-switches-linux` — it stays on disk, in
  `.git/info/exclude` — so `git diff v4.33.1..<branch>` is now exactly the shipped patch on both
  platforms, and regenerating is safe.
* Every "16 files" in the research repository's own documents (`docs/fork/consolidated-full.md`
  §7 among them) had the same off-by-one, since it counts the same patch. Only `release/` was
  corrected here.

  > **Overtaken, later on 2026-09-01 — needs a decision.** The two bullets above describe the
  > patch as it was when it was applied for §1: 15 files, +1,799/−82. The patch now in
  > `patches/` is **16 files, +2,101/−82** (counted directly: 16 `diff --git` headers, the
  > sixteenth being `stage0/src/runtime/object.cpp`), which is what `README.md` §2,
  > `evidence.md` and the manuscript all say, and it is the figure that stands. The Linux
  > patch is 15 files, +1,830/−82. So §1's table row and the off-by-one bullet are a record of
  > an earlier patch, not of the shipped one; they have been left as the record of that run
  > rather than rewritten, but the shipped patch has **not** been re-applied end to end since
  > it was regenerated, and §1's build was of the earlier one. `building.md` §1 has been
  > corrected to "sixteen files".
  >
  > **Answered in §7**, except for the Linux figure, which was itself out of date: the Linux
  > patch was regenerated at the same time and is **16 files, +2,132/−82**. `.metadata_never_index`
  > did *not* come back through the regeneration — §7 checked, and both patches are clean of it.

---

## 7. The shipped patch, re-verified (2026-09-01, evening)

The patch in `patches/` was regenerated after §1 was written, from the `a6b-shipped-image`
branch, and it changed the design of the tactic index image — from a cache Lean wrote for itself
to an artefact somebody builds on purpose. §1's run therefore does not cover the patch that
ships. This section is that coverage.

**Verdict: the shipped patch is the source that was built, and that source passes everything the
package claims for it — 26/26 byte-identical, and the headline measurement reproduces to within
a percent. The tactic index image behaves exactly as `docs/improvements/06-tactic-index-image.md` describes.
Four more defects were found, one of which had silently switched the feature off in this
package's own two measurement scripts.**

**One thing was not done, and it matters: the fork was not compiled from scratch.** See
"What this section does not cover" at the end.

### 7.1 The patch reproduces the tree that was built, byte for byte

```sh
mkdir -p ~/scratch/lean4 && git -C <lean4 clone> archive v4.33.1 | tar -x -C ~/scratch/lean4
cd ~/scratch/lean4 && git apply --binary <…>/patches/lean4-v4.33.1-optimized.patch
```

* 668 MB exported, patch applies with no output and exit 0. **16 files**, matching
  `git apply --numstat`'s +2,101/−82. Worth noting because `building.md` §1 says to `git clone`:
  `git apply` also works in a directory that is **not a git repository at all**, which a
  `git archive` extraction is not — including the binary hunk for `stage0/src/runtime/object.cpp`.
* All **13,102** files tracked at `a6b-shipped-image` (`923e65a`) were then compared with the
  patched archive, byte for byte. **Every one is identical**, and the archive has no file the
  branch does not. The single difference is in the other direction: the branch still carries the
  empty `.metadata_never_index` that §2.1 removed from the patch. So the regeneration did not
  reintroduce it, and the patch is a faithful description of the tree.
* That tree is the one `~/lean-work/lean4-v4.33.1/build/release` was built from, and the build is
  newer than every source file in it — including `stage0/src/runtime/object.cpp`, the sixteenth
  file, whose stage-0 object file was rebuilt after it changed. So every file the patch touches
  has been through a compiler in that tree.
* `elan run <fork> lean --version` prints the stock string, byte for byte, as §1.3 says it will.

### 7.2 `check-patches.sh`

All five patches apply alone to a clean `v4.33.1`; each of the three fixes applies on top of the
fork; and all four apply cumulatively. **5 seconds**, 668 MB of temporary export. This is the
first run that covers the regenerated **Linux** patch (16 files, +2,132/−82) — it applies.

### 7.3 The tactic index image, exercised directly

Project: a Mathlib `v4.33.1` checkout with the community olean cache. Fork binary run bare with
an explicit `LEAN_PATH`, never under `lake env`. File: `import Mathlib`.

| step | what happened |
|---|---|
| plain run, no image | `tactic-index: image …/.lake/build/lib/lean/exts-<key>.tacticindex (absent)` — and **no file created anywhere** |
| `LEAN_TACTIC_INDEX_WRITE=1` | wrote `exts-2731516974744549714.tacticindex`, **212,133,024 B**, plus a **10,498 B** `.tacticindex.deps` sidecar, into `<project>/.lake/build/lib/lean/` — the default placement rule of `a6b-shipped-image.md` §3, unassisted |
| plain run again | `tactic-index: image … (used, 93 extensions, mmap=true)` — **2.18 s / 1.39 GB** against 2.75 s / 1.64 GB claimed for no image |
| two further plain runs | `find <project> ~/.cache -newermt <before>` returns **0 files**. Nothing is written, stamped or evicted at run time |
| `LEAN_TACTIC_INDEX_WRITE=1` with the image already there | file mtime unchanged — the write is idempotent, as §6 of that document claims |

A/B on the same binary, three interleaved repeats each:

| | wall s | max RSS GB |
|---|---|---|
| image present | 2.21 – 2.63 | 1.38 – 1.58 |
| `LEAN_TACTIC_INDEX_DIR` → an empty directory | 2.62 – 2.69 | 1.87 |
| `LEAN_TACTIC_INDEX=0` | 2.61 – 2.68 | 1.87 – 1.89 |
| stock `lean` | 9.94 – 30.1 | 5.29 |

"No image" and `LEAN_TACTIC_INDEX=0` agree, which is the claim that with no image the feature costs
nothing at all.

**The key moves with the lazy-loading index.** The very first run of a cold closure looked for
`exts-18104233348525861743.tacticindex`; every run after the lazy-loading index existed looked for
`exts-2731516974744549714.tacticindex`. This is the "build it warm" hazard `a6b-shipped-image.md` §6
warns about, observed rather than asserted, and it is now handled by `repro-import.py` (§7.5).

### 7.4 Equivalence and the headline measurement

* `check-equivalence.sh`: **26/26 byte-identical** in stdout, stderr and exit code,
  `VERDICT: EQUIVALENT`, about 13 minutes. The 22 cases with an `import Mathlib` header ran with
  the image **mapped**; the four in-place Mathlib source files have their own closures and ran on
  the no-image path, so both halves are covered.
* `repro-import.py`, medians of 3 interleaved runs on an otherwise idle machine:

```
| case           | config | wall s | user s | sys s | max RSS GB | minor faults |
| import-mathlib | stock  |  11.09 |   1.68 |  8.51 |       5.29 |       423273 |
| import-mathlib | fork   |   2.30 |   1.25 |  0.92 |       1.38 |       166420 |

import-mathlib   speed-up 4.82x wall, 3.82x max RSS
```

  Against the sample run in `scripts/README.md` — 10.2 s / 5.29 GB / 397,918 faults and 2.3 s / 1.40 GB / 167,993:
  **the fork row reproduces to within a percent on every column**, and max RSS is exact. The stock
  row is 9 % slower in wall time and 6 % higher in faults than claimed, which is the usual
  machine-state spread. §1 could not say this — it ran under load average 40 and got 3.25 s for
  the fork.

### 7.5 Four more defects, all now fixed

**7.5.1 — `repro-import.py` and `check-equivalence.sh` had the tactic index image switched off.**
The worst of the four. Both scripts set `LEAN_TACTIC_INDEX_CACHE_DIR`, which the redesign **deleted**.
Nothing warns about an unknown `LEAN_*` variable, so both scripts ran the fork with no image
directory and no image, silently measuring the feature's *off* path while printing it as the
package's headline configuration. Both now set `LEAN_TACTIC_INDEX_DIR`, and both do a deliberate
`LEAN_TACTIC_INDEX_WRITE=1` pass after the lazy-loading index is warm — `repro-import.py` as warm-up
pass 2, `check-equivalence.sh` in its pre-warm. The numbers in §7.4 are from the fixed scripts;
the difference is 0.4 s and 0.25 GB on the fork row.

**7.5.2 — the distributor recipe cannot work as written.** `technical/tactic-index-image.md` §6
and `docs/improvements/06-tactic-index-image.md` §6 both tell a distributor to run

```sh
LEAN_TACTIC_INDEX_WRITE=1 lake env lean /tmp/closure.lean
```

`lake env` runs the toolchain the *project's* `lean-toolchain` names — this package's own
`building.md` §2.3 is about exactly that trap. In a Mathlib checkout pinned to
`leanprover/lean4:v4.33.1`, `lake env printenv LEAN_SYSROOT` names the **stock** toolchain
(verified), so the command runs stock Lean, which ignores `LEAN_TACTIC_INDEX_WRITE` and writes nothing —
and says nothing. The recipe is correct only for a project whose `lean-toolchain` already names
the fork. `building.md` §3.1 now gives a recipe that works either way.

**7.5.3 — `building.md` §3 still required `LEAN_TACTIC_INDEX=0` for a from-source rebuild.** That was a
property of the cache. `warnings.md` §1 had already been marked superseded; §3 had not.
`README.md` §1 ("one of the six changes will fill your disk") and §3 ("two of the six write
per-closure side files … one has an unbounded-growth defect") were stale in the same way. All
three corrected.

**7.5.4 — `check-patches.sh`'s verdict said the fork touches 15 files.** It touches 16. The
figure is printed on success, so it contradicted `README.md`, `evidence.md` and `patches/README.md`
in the same terminal session that confirms them.

`building.md` also had a **gap** rather than an error: nothing in it told a reader how to obtain a
tactic index image, while §2.1 said the image is used "if the library you are using ships one".
No library ships one. New §3.1 gives the recipe and the three ways to get it wrong.

### 7.6 What this section does not cover

* **The fork was not compiled from scratch.** The machine had 43 GB free against a 40 GB floor,
  a Lean source tree plus build is ~10 GB, and the two directories that could have been reclaimed
  (`~/.cache/lean-tactic-index`, 5.0 GB, dead; a Mathlib `.lake/build`, 11 GB) could not be deleted — the
  attempt was refused. So §7.1 establishes that the patch *is* the source that was built, and
  §§7.2–7.4 exercise the binary built from that source, but nobody has run `cmake --preset
  release` on a fresh extraction of this patch. §1 did run it, on a tree differing from this one
  only in `src/Lean/Environment.lean`, `src/runtime/io.cpp` and `stage0/src/runtime/object.cpp` —
  all three of which have since been compiled in the fork tree. The residual risk is a
  configure-from-scratch failure, not a compile failure. **It should still be closed before
  release.**
* **The Linux patch was not built either**, for the same reason plus the absence of a container
  in this session. It applies (§7.2); nothing more.
* **`building.md` §1.1's `cmake` invocation** was therefore not re-run, and the local `cmake` is
  now 4.4.2 — newer than whatever §1 used.
* **`setup-mathlib.sh`, `tools/`, the VS Code half of §2** — unchanged from §4; still not run.
