# The case files

## `textbook/` — 15 files

Self-contained undergraduate-level Lean files, each starting `import Mathlib`, all compiling
with no `sorry`. They span induction and `Finset` sums, `linarith`/`nlinarith`, `omega`,
`norm_num`, `decide`, real limits, `continuity`, group lemmas, `ring`, `positivity`/`gcongr`,
`aesop`, `Finset.card` and a polynomial/measure-theory file.

They are used two ways. As a *benchmark* they are the elaboration suite: what a real
five-line-to-fifty-line proof costs once the library is loaded. As an *equivalence* suite
`check-equivalence.sh` appends `#print axioms` for every theorem in each file, which turns
each one into a check that the fork accepted the same proof from the same axioms.

## `equivalence/` — 7 files

Chosen so that they *move* if the fork perturbed something it must not perturb.

| File | What it is designed to catch |
|---|---|
| `eq-simp.lean` | 40 `simp?` calls — the lemma names and firing order the simp set selected, which is the first thing a changed `DiscrTree` insertion order would move |
| `eq-synth.lean` | 28 `#synth` queries — the *chosen* instance, decided by the instance tree and priority order |
| `eq-tactics.lean` | 35 cases over the other cached tables: `norm_num`, `positivity`, `push_cast`, `gcongr`, `ext`, `to_additive`, `fun_prop`, `aesop`, `omega`, `decide`, `linarith`, `nlinarith`, match equations |
| `eq-pp.lean` | 23 pretty-printing cases. The delaborator and unexpander tables are *not* cached by the fork; this file is a tripwire in case that ever changes |
| `eq-goals.lean` | **the load-bearing case.** 29 library-search goals — `exact?` ×16, `apply?` ×6, `rw?` ×6, two of them producing ~300-line suggestion lists. Stock output is 5,352 lines and 661 `Try this` lines, and the *order* of the candidates is part of the comparison. It is the only file that exercises the prebuilt index and the tactic-index image at once on the same goal |
| `constmap.lean` | the constant-map suite from the no-touch-refcounts work: `#check`/`#print`/`exact?`/`simp`/`ring`/`linarith`/`norm_num` |
| `lazy-paths.lean` | the lazy-loading suite: theorem bodies, unexposed definitions reached through `decide`/`unfold`/`rfl`, `open private`, Mathlib tactics, `#eval`, `exact?` — i.e. every path that forces a deferred part to be loaded |

Five of these — `eq-goals`, `eq-pp`, `eq-simp`, `eq-synth`, `eq-tactics` — exit with code 1
by design: they contain a deliberate failing goal or error.
The exit code is part of what is compared, so a fork that turned an error into a success
would fail the suite rather than pass it quietly.

This is a suite, not a proof. It is 26 files chosen by people who knew what they had changed
and what it could plausibly break. It has caught real bugs during development — a candidate
ordering that picked `Right.mul_pos` instead of `Left.mul_pos` because the exported constants
came out in sorted rather than declaration order, and a crash on a module root with `meta`
imports — which is the reason to trust it as far as it goes, and the reason not to trust it
further.
