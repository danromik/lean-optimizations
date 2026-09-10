# `lean-perfbench` — the benchmark suite

A word on the name, because a reader's first guess will be wrong. The other Lean benchmarks in the
field measure whether a system can *prove* a given theorem. This one measures none of that. It
measures what Lean costs to run: time, memory, and pages fetched from disk, across five kinds of
work a Lean user actually does.

| | what it measures |
|---|---|
| **starting up** | how long `import Mathlib` takes and what it costs in memory, from importing nothing through single components to all of it. The fixed cost every other activity pays. |
| **elaborating** | what a real proof costs once the library is loaded, over the fifteen textbook files in [`cli/textbook/`](cli/textbook/). |
| **building a project** | a complete project built from scratch, sampling memory across the whole family of processes the build spawns rather than the one we started. |
| **editing** | scripted editor sessions driven through the language-server protocol: what the person at the keyboard waits for. [`interactive/`](interactive/). |
| **checking a corpus** | six real Lean projects, checked file by file. They pin six different Lean versions between them, so this family runs each under the version it pins; only `formal-conjectures` is at v4.33.1 and can therefore be compared against the fork. |

Every run records wall-clock time; processor time, split into the program's own work and the
operating system's work on its behalf; peak memory across the whole process family; pages fetched
from disk; and the versions of every component involved. Results are written as one
self-describing file per run, so any number can be traced back to the exact toolchain, library and
machine that produced it.

## What is here

| | |
|---|---|
| [`harness/`](harness/) | the measurement code: process-tree sampling, the five suites, result serialisation |
| [`cli/`](cli/) | the case files — fifteen textbook proofs, and the equivalence corpus |
| [`interactive/`](interactive/) | the editor-session benchmark and its recorded sessions |
| [`results/`](results/) | the runs the paper's figures are computed from, and what each one backs |

## A note on measuring memory

Peak memory is sampled across the process tree and summed. That double-counts pages two processes
share — and during a build, forty concurrent `lean` processes share a great deal, since they map
the same compiled library files. The summed figure is therefore an upper bound rather than a
measurement of physical memory: on the builds we measured it reaches 4.1–4.9 GB while no single
process exceeds 0.42 GB. Time has no equivalent difficulty, which is why the paper attributes time
per improvement and treats memory more cautiously.

## Running it

The harness needs a Lean toolchain and a project to point at.
[`../scripts/setup-mathlib.sh`](../scripts/setup-mathlib.sh) prepares the latter.
[`../scripts/repro-import.py`](../scripts/repro-import.py) is the short path: it re-measures the
paper's import figures in about five minutes and builds nothing itself.
