# Audit of `evidence.md` — 2026-09-01

> **Written during development.** This document uses the short labels the work carried at
> the time (`A6b`, `T2`, `L1` and so on) and cites paths in the authors' research
> repository, not all of which are part of this package.
> [`development-labels.md`](development-labels.md) is the key to both.

`evidence.md` is the artefact a sceptical reader uses to audit the package, so an error in it
costs more than an error anywhere else. Nobody had checked it. This is that check.

**Method.** Every row was taken in turn and four things were established: that the cited document
exists; that it contains the claim; that the number matches exactly or under the package's own
rounding; and that the cited raw results exist *and contain what the citation implies* (not merely
that the path resolves). Then each row was re-read against what changed on 2026-08-31 and
2026-09-01. Reading and cross-referencing only — nothing was built, run or measured.

**Scope note.** `docs/dans-notes.md` and `manuscript/` were not opened for editing (Dan owns both).
`docs/linux/full-fork-linux.md`, `a2-linux.md`, `a6a-linux.md` and `linux-full-fork.md` were read
but treated as read-only (another agent is writing them). `~/lean-work` was not touched.

---

## Result in one line

**39 claims checked** (38 table rows plus the closing prose section on novelty). **30 stand exactly
as written. 9 were defective — 2 in their numbers or their attribution, 4 in their citation, 3
stale. All 9 are fixed.** A further **11 claims made elsewhere in the package had no evidence entry
at all**; all 11 have now been given one, and the table grew from 38 rows to 56. Nothing was found
that makes a manuscript *number* wrong: the defects are attribution, citation and currency.

Four things are left for Dan (§4) — one of them, §4.1, is a delivery decision the package cannot
be shipped without.

---

## 1. Defects found and fixed

### 1.1 Numeric

**D1. "4.8 GB of pages touched to read one 8-byte counter per definition."**

* What it said: 4.8 GB, attributed entirely to the counter read.
* What the sources say: `import-path.md` §2.4's step table attributes **+2,773 MB (~2.8 GB)** to
  "read the object header of every `ConstantInfo` (772k)". The 4.8–4.9 GB figure is the *total*
  clean file-backed olean residency of the import (§2.4: "~300k page faults (≈ 4.9 GB)";
  `angles.md` says 4.8 GB, with "(+2.8 GB for 772k header reads)" in the same sentence).
  `a3-no-touch.md` §0 states the attributable figure as "~2.8 GB" directly.
* Fixed to: "4.8 GB of clean mapped library pages resident after the import, of which **+2.8 GB is
  the 8-byte counter read alone**", with the step-table line cited by name.
* **Note for Dan:** `docs/explainer.md` line 19 makes the same over-attribution ("771,000 tiny
  reads pulled in 4.8 GB of pages"). It is not in the release package, but it is the sentence the
  manuscript is most likely to have borrowed. Worth checking there.

**D2. Snapshot on the `t4g.large`: "5.8 → 1.5 s warm".**

* The number is right but was cited to documents that do not contain it. `a4-snapshot-layer.md`
  §0 gives **5.58 → 1.46 s**; the 5.8 s figure is `aws-sandbox.md` §0 item 4 and §8 item 4, which
  quote the same case at one significant figure. Both are defensible; the citation was not.
* Fixed by citing `aws-sandbox.md` §5.2 (per check) and §5.3 (ten checks) as primary and noting
  that §5.2's own figure is 5.58 → 1.46.

### 1.2 Citation

**D3. Equivalence row cited `consolidated-full.md` §5, §1.1.** §1.1 is now "The codegen fix,
merged 2026-08-31" — a merge that has since been **reverted** under the delivery-boundary decision
(`consolidated-full` is back at `a15c087`). It does not support the equivalence claim and it
describes a state of the tree that no longer exists. Fixed: §5 only. While there, the row was
made honest about the `lake build`: the `.c` and `.ilean` files are byte-identical, the 9 `.olean`s
are not (the prebuilt search index's recorded entries; `LEAN_SEARCH_INDEX_RECORD=0` returns 9/9
graph-identical). The old wording
was not wrong, but a reader running `equiv.sh` sees "0 byte-identical" in the summary and needs to
know that is expected.

**D4. Lake start-up "11.2 → 0.74 s (stock 1.30 s)" cited `lake-startup-artefact.md`.** Those two
numbers are **not in that document.** It diagnoses the cause and reports the *hybrid-`lake`
workaround*, which reached 2.8 s (stock 2.8 s); it only recommends `posix_spawn`. The measured
`posix_spawn` result is `consolidated.md` §0 and §8, raw
`bench/experiments/consolidated/spawn-validation.txt` ("stock 1.30s (sys 0.84); lean-opt
fork+posix_spawn 0.74s (sys 0.40)"). The cited raw directory
`bench/experiments/lake-startup/` holds only `forkcost.c` and a Lake environment sample — it does
not contain the claimed measurement. Fixed: both documents cited, with what each supplies, and the
raw path corrected.

**D5. Ladder-order attribution presented without the ablation that supersedes it.**
`leave-one-out.md` (2026-09-01) opens: *"This is the attribution the manuscript should quote; the
improvement-by-improvement ladder of `consolidated-full.md` §0 is order-dependent and becomes
narrative."* It was
cited nowhere in `evidence.md`. A row has been added carrying the per-change losses and the
finding that the six do not sum to the whole (1.49 s of 8.09 s), and the prebuilt-search-index row now says
explicitly that its 5.5 s baseline is the fork before that index existed, not stock's 3.8–3.9 s.

**D6. "All 334 tactic indexes" byte-identical (the single import walker, stage 1).** The document says all 334 persistent
extensions were checked and **213 of them compared as bytes**; the 334 figure is the
`importedEntries`-order check (0 of 334 differ). Tightened to say exactly that, plus the 14 MB
digest over 98,715 `simp` and 42,904 instance leaves.

**D7. `docs/linux/a4-x86.md` cited nowhere.** The x86-64 snapshot replication (2.87 → 0.71 s,
cold 109 → 25 s, 17/17 equivalent, fits in a 2 GB cgroup) existed and had no entry. Added.

### 1.3 Stale

**D8. The tactic index image described only as a cache.** `a6b-shipped-image.md` (2026-09-01) replaced the cache with
an artefact written on purpose once and shipped beside the library's oleans. The old row's number
(2.68 → 2.23 s, 839 ms loop) is still what its document says and is retained, now with a
supersession marker. Two rows added: the shipped-image design and its verification (250 closures,
0 images, 0 bytes, 26/26), and the cache's costs retained as the evidence for the decision
(21 GB in seven minutes, 254.6 GiB / +58.5 % on a from-source build, −19 % at the 5 GB cap).

**D9. The Zulip coverage paragraph — the most consequential staleness found.** `evidence.md`
said the Zulip public archive is frozen at 2026-02-28 and the live instance login-walled, so
"roughly six months of the community's main discussion channel is invisible". **That has not been
true since 2026-09-01.** `u4-prior-art.md` §5 records that the live instance was searched
read-only via the API across `#lean4`, `#general`, `#mathlib4` and `#lean4 dev`, with history back
to 2018, and all three verdicts re-checked and standing. The residual gap is private streams and
unswept channels — *not* six months of history. The section has been rewritten, including §5.1's
trap (a new account's unnarrowed full-text search silently returns only post-signup messages, so
every negative needs a known-positive control), and it now opens by stating that nothing here
claims novelty.

---

## 2. What was checked and found correct

For the record, so a re-auditor knows what has already been done.

**Fork.** 10.17 → 2.27 s / 5.68 → 1.37 GB and 15.3 → 3.73 s / 7.89 → 1.56 GB confirmed against
`consolidated-full.md` §0 *and* against `bench/experiments/consolidated-full/ab-summary.md` row by
row. 26/26 confirmed in `equiv-on-summary.txt` and `equiv-off-summary.txt` (both end
"EQUIVALENT"). 52,490 files and XNU's address-sorted hole list, 10,498 under lazy part loading, the
prebuilt search index's +5.5 → +0.8 s / +2.2–3.1 → +0.15–0.2 GB and 661 `Try this`, the tactic
index image's 2.68 → 2.23 s and 839 ms of
2.70 s: all confirmed. **15 files, +1,799/−82 confirmed three ways** — the document, and by
counting `diff --git` headers and +/− lines in `release/patches/lean4-v4.33.1-optimized.patch`,
which is byte-identical (same md5) to `docs/fork/consolidated-full.patch`.

**Linux.** 5.68 → 3.99 s, 104.1 → 35.4 s, 5.26 → 1.75 GB, 6.33 → 4.51 s per check: all in
`linux-full-fork.md` §3.5/§3.6 and in `raw/table-t4g-warm.md`. x86-64 replication "to within a few
percent" confirmed (−28 % vs −30 %, −69 % vs −67 %, 2.7× vs 2.7×, −68.3 % vs −68.4 % major faults).
52,583 → 12,922 VMAs and 80 % → 20 % confirmed. **The "sign of one comparison flips" claim is
real** and I checked it specifically because it sounded like rhetoric: §7.6, `full` vs `fulla2`
RSS is −0.221 GB under an 8 GB limit and **+0.139 GB** unconstrained, and the document itself says
"the *sign* of the answer depends on it". The layout's disk cost of +0.0002 % (+13,184 B on
5.98 GB) and the stored search index's +1.57 % (→ "+1.6 %") were both confirmed.

**Census and editor.** 212 files / 105,700 tactic calls / 30 % / 22.5 % / 1.3 % confirmed;
45 `*_census.json` files present in `bench/results/`. −29 % to −78 %, 88–94 %, `aesop` 98 → 5 ms
confirmed. 74.4 % repeat share and 38.6 % cross-file confirmed. The interactive benchmark's 63 %
header share and 23.7 → 11.3 s confirmed. (The document says median edit latency 207–220 ms; the entry says
208–220. Left alone — every individual session row in the table reads 208 or 209 ms, so the entry
is right about the data and the document's "207" is its own floor discussion.)

**Build.** 13,779,179 loads / 12,387 modules / 69–76 % confirmed. 489,690 → 64,330 confirmed, with
"the theoretical minimum" supported by `b2-incremental-import.md`'s "the order-safe *tree* number
to the unit"; 7.61× and 1.143× confirmed. 1.26× ceiling and import = 21 % of a build confirmed.
Fork-safety: deterministic, +61 lines, 40/40, 0.25 s — all four confirmed.

**Bug fixes.** 21/23 lines and 7,445 of 7,451 confirmed in both documents. +9/−0 confirmed in the
document *and* by counting the shipped patch. 50 runs / 0 signals, 21/22 → 22/22, 8,303 of 8,312
confirmed. lean4#14359 open, `P-high`, filed 2026-07-10, by an FRO engineer, confirmed in
`u4-prior-art.md` §2.1 — **the row correctly disclaims novelty and correctly states what is ours.**
54 lines in three files and the vscode-lean4 §3.4 finding confirmed.

**Lean team.** rc2 within 1–2 % and nothing lazy; the no-touch reference counts superseded by draft #14362; patch set applies
to master `b9c9eb9a` with zero conflicts; four PRs, three drafts, zero reviews, newest activity
2026-07-27 — all confirmed against `u3-rebase-survey.md` §4.1's table, which lists exactly one
non-draft (#14032) and #14563 at 2026-07-27.

**Raw results.** Every cited directory exists and was opened, not just stat'd:
`consolidated-full/` (summaries match the claims line by line), `a6b-mappable-indexes/`,
`a6b-shipped-image/`, `a6b-cache-bound/`, `leave-one-out/` (`loo-summary.md` medians match the
document), `linux-full/raw/` (26 files incl. the t4g tables), `x86-linux/raw/` (the `8g` glob
resolves), `a4-snapshot/` (`failmodes-raw.txt` really does contain the exit-139 rows),
`aws/raw/a4-*.log`, `b1-redundancy/data/`, `b2-extend/`, `b2-fork-walker/`,
`b2-resumed-walker/data/`, `fork-safety/`, `olean-repro/data/`, `t2-codegen-fix/`,
`watchdog-config-watch/out/`, `tc1-typeclass/`, `bench/census/`, `code/benchmarks/interactive/sessions/`.

---

## 3. Claims in the package with no evidence entry

A claim with no entry is as much a gap as a wrong entry. **Eleven were found and all eleven have
been added** to `evidence.md` — seven in a new "caveats and operational claims" section, the rest
as rows in the fork and Linux tables:

| Claim | Where it is made | Now sourced to |
|---|---|---|
| 27/27 on Linux, both architectures | `README.md` §3 | `linux-full-fork.md` §4, `x86-linux.md` §6 |
| t4g import + `exact?` 30.68 → 11.47 s, 6.33 → 2.50 GB | `README.md` §1 | `linux-full-fork.md` §3.5 (with x86's caveat that the stock row is the box paging against itself) |
| Docker VM 2.44 → 1.41 s, 5.70 → 1.95 GB | `README.md` §1 | `linux-full-fork.md` §0, §3.1 |
| Full build 47.7 min / 67.9 GB → 23.0 min / 33.8 GB | `README.md` §1 | `consolidated.md` §0, §4.4 |
| Patch independence: six further files | `README.md` §2, `patches/README.md` | `check-patches.sh` and the three fix documents |
| RSS counts shared pages; 0.86 GB private; 3 workers ≈ 8.4 GB | `README.md` §3.1, `warnings.md` §5 | `import-path.md` §3.1, `e1-worker-cost.md` §6 |
| Layout / reference-count overlap, downloaded vs rebuilt library | `switches.md` | `a2-layout.md` §6, `x86-linux.md` §7.3, `a3-no-touch.md` §0 |
| Linux `LD_LIBRARY_PATH` for compiled tactic libraries | `warnings.md` §2 | `t2-linux.md` §0 item 3, §2 |
| Never run a fork binary in `lake env` (the 10.35 s incident) | `warnings.md` §4, `building.md` §2.3 | `a1-mmap-order.md` §5 |
| Mathlib rebuild ~43 min / ~11 GB | `warnings.md` §3, `building.md` §3 | `cache-key-fixes.md`, `x86-linux.md` §7.1, `a6a-linux.md` §0, §2 |
| Lazy part loading alone is a regression (22.2 vs 15.3 s; 27.5 vs 23.7 s) | `warnings.md` §6, `switches.md` | `consolidated-full.md` §0, §3.2, §3.3 |

---

## 4. Questions raised for Dan, and what became of them

### 4.1 The package ships the tactic index image as a cache, but `switches.md` and `warnings.md` describe the shipped artifact

> **Resolved.** The shipped patch is the image form: it reads `LEAN_TACTIC_INDEX_DIR` and writes
> beside the library's `.olean` files, and none of the retired cache variables appears in it.
> `switches.md` names them only to say they are gone, `README.md` §1 now says the disk problem was
> a property of the earlier cache design, and `building.md` says the rebuild no longer needs
> `LEAN_TACTIC_INDEX=0`. The four documents agree.

`release/patches/lean4-v4.33.1-optimized.patch` is byte-identical to
`docs/fork/consolidated-full.patch` — i.e. it is `consolidated-full` = `a15c087`, which contains
**the tactic index image in its cache form**. The shipped-image redesign lives on branch
`a6b-shipped-image`, two commits on
top, and is **not in the shipped patch**.

Meanwhile `switches.md` (updated 11:17 today) now ends with "The tactic index image changed shape
(2026-09-01): it is a shipped artifact, not a cache" and declares `LEAN_TACTIC_INDEX_CACHE_DIR` / `_EXTS` / `_TIMING` /
`_MIN_FREE` / `_MAX_SIZE` / `_MIN_USES` gone — while its own "Supporting variables" table, twenty
lines earlier, still lists `LEAN_TACTIC_INDEX_CACHE_DIR`, `LEAN_TACTIC_INDEX_TIMING` and `LEAN_TACTIC_INDEX_EXTS` as live.
`warnings.md` §1 is now marked superseded but `README.md` §1 still says "One of the six changes
will fill your disk on a from-source Mathlib build unless you turn it off", `README.md` §3 still
says "one of them has an unbounded-growth defect", and `building.md` §2.1 still says the caches
default to `$HOME/.cache/lean-tactic-index` while §3 still says the rebuild "must be run with
`LEAN_TACTIC_INDEX=0`".

**Somebody has to decide which version of the tactic index image ships**, and then the four documents have to agree. I have not
touched them: this is a delivery decision, not a citation error. `evidence.md` now documents both
designs and marks which is which, so whichever way it goes the evidence is already in place.

### 4.2 The cold-Mac row's "after" number is an estimate whose derivation I could not find

> **Resolved.** The row was dropped from `README.md`. The manuscript never carried it; its only
> cold row is the `t4g.large`'s 104.1 → 35.4 s, measured on real hardware.

`README.md` §1 claims: `import Mathlib`, cold (after reboot), Mac — **34 s → ~10 s (est.: 43 %
fewer page loads)**. The 34 s is solid (`baseline-report.md`, 34.4 s, 234k major faults, tag
`cold-after-reboot`). **The ~10 s and the 43 % I could not source.** The only "43 %" I found in the
corpus is `import-path.md` §241, which is about the `module` root's RSS, not about the fork's cold
page loads. `a3-no-touch.md` §131 says the cold-cache measurement was **inconclusive** ("without
root there is no `purge`… Inconclusive") and offers a mechanism argument, not a number.

It is the only estimate in the README's headline table and it sits in a row of measurements. Either
its derivation should be added to a document and cited, or the row should be dropped in favour of
the t4g cold number (104.1 → 35.4 s), which is real hardware and is already in the table two rows
below. **I did not add an evidence entry for it**, because I could not find evidence.

### 4.3 `baseline-report.md` cites a result file that does not exist

It attributes the cold-after-reboot measurement to `20260825T023603Z_env-detail`. There is no such
file in `bench/results/`. The run with that tag and those numbers is
**`20260825T023751Z_env-detail.json`**. I recorded the correct id in `evidence.md` and flagged the
discrepancy there rather than editing `baseline-report.md`, which is outside this task's remit.

### 4.4 `linux-full-fork.md` still says 16 files, +1,830

> **Overtaken by events.** This was written when the shipped Linux patch was 15 files, +1,830/−82.
> The Linux branch has since been merged with the change it was missing and the patch regenerated:
> it is now **16 files, +2,150 / −82**, the same 16 files as the macOS patch. The count
> `linux-full-fork.md` carries is right again; the line total is not.

---

## 5. What I would audit next

* **The manuscript's own numbers against these entries.** This audit verified
  `evidence.md → documents → raw`. Nobody has verified `manuscript → evidence.md`, and the
  4.8 GB over-attribution (§1.1 D1) is exactly the kind of thing that travels from `explainer.md`
  into prose without its qualifier.
* **`testing.md`**, which was outside this brief and makes claims about what is still untested that
  the x86-64 and shipped-image work may have overtaken.
