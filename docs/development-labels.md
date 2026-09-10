# Development labels

During the work each line of investigation carried a short label — `A6b`, `T2`, `L1` and so on.
Those labels are gone from the paper, from the patches and from the code, because a reader has no
way to know what they mean. They survive in three documents that were written while the work was
going on and are shipped as they were: [`evidence.md`](evidence.md),
[`evidence-audit.md`](evidence-audit.md) and [`testing.md`](testing.md). This page is the key to
them.

Those three also cite paths in the authors' research repository, which is not part of this
package. Where a cited document has a counterpart here, the citation has been repointed; where it
does not, the path is left as written and names a file you will not find.

## The improvements

| label | improvement | |
|---|---|---|
| `A1` | 1 | address reservation (macOS) |
| `A2` | 2 | shell-first `.olean` layout |
| `A3` | 3 | no-touch reference counts |
| `A5` | 4 | lazy part loading |
| `A6a` | 5 | prebuilt search index |
| `A6b` | 6 | tactic index image |
| `A4` | 7 | snapshot wrapper script |
| `T2` | 8 and 9 | compiled Mathlib tactics, and the code-generation fix it exposed |
| — | 10 | fork safety |
| `L1` | 11 | language-server configuration watching |

The numbering is not a renaming of the labels in order: `A4` is improvement 7 and `A5` is
improvement 4, because the labels record the order the work was started and the numbers record the
order the paper presents it.

## Measurement and investigation

These produced figures rather than changes, and several are cited in the three documents.

| label | what it was |
|---|---|
| `T1` | the tactic elaboration census — where elaboration time goes, and the 30 % interpreted share |
| `tc1` | a census of type-class inference |
| `L1` | the editor-session replay benchmark, as well as improvement 11 |
| `E1`, `E2` | what a second and third open file cost; where an editor file-open spends its time |
| `B1`, `B2` | redundant loading during a build; the single import walker, which is not shipped |
| `U1`–`U4` | surveys of upstream: what the Lean developers were already doing, and prior art |
| `X0`–`X10`, `P1`–`P8`, `D1`–`D9` | numbered hazards in the fork-safety investigation; see [`improvements/10-fork-safety.md`](improvements/10-fork-safety.md) |

## Environment variables

The runtime switches were renamed for release, and the old names appear in the three documents
above. [`switches.md`](switches.md) documents the current names; the mapping is:

| was | is |
|---|---|
| `LEAN_A5` | `LEAN_LAZY_PARTS` |
| `LEAN_A6A_PREBUILT` | `LEAN_SEARCH_INDEX` |
| `LEAN_A6B` | `LEAN_TACTIC_INDEX` |
| `LEAN_COMPACT_LAYOUT` | `LEAN_OLEAN_LAYOUT` |

`LEAN_MMAP_RESERVE` and `LEAN_NO_TOUCH` were not renamed. Most supporting variables followed
their stems, so `LEAN_A5_VERBOSE` is now `LEAN_LAZY_PARTS_VERBOSE`. Two did not survive the rename
at all, because the tactic index stopped being a cache: `LEAN_A6B_CACHE_DIR` and the directory
`~/.cache/lean-a6b` are both gone, and the image is now written beside the library's `.olean`
files, under `LEAN_TACTIC_INDEX_DIR` if you want it somewhere else. An image file named
`exts-<key>.a6b` is now `exts-<key>.tacticindex`. See [`switches.md`](switches.md) for what the
tactic index became.

Anything still emitting the old names is running a build from before the rename, not the fork in
this package.
