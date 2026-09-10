# Where the paper's figures come from

This directory records the provenance of the measurements in the paper. It does not carry the run
logs. We are not asking anyone to trust our logs: the point of the harness in
[`../harness/`](../harness/) and the scripts in [`../../scripts/`](../../scripts/) is that you can
measure your own machine, and absolute numbers from ours would not reproduce on yours anyway.

## The laptop figures

Every laptop figure in the paper — the `import Mathlib` rows of Table 1, and all of Table 2 —
comes from a single sweep of 96 timed runs on one machine in one sitting, summarised in
[`laptop-sweep-summary.md`](laptop-sweep-summary.md). Times are medians of three, on a self-warmed
cache.

That it is one sitting is deliberate. The per-improvement ladder was originally assembled from six
development sessions run over three weeks, each with its own stock baseline, and those baselines
disagreed by up to 15 %: 9.2, 10.5, 10.76 and 10.46 seconds for the same nominal warm
`import Mathlib`. Rather than explain the disagreement in the paper, the whole ladder was
re-measured at once.

What made that cheap is also the check that licenses the method: one binary with every improvement
compiled in but switched off reproduces the stock release to within 0.6 s of wall time and nothing
measurable in memory — 10.74 s / 5.69 GB / 423,131 page reclaims against the release's
10.17 s / 5.68 GB / 423,362. The disabled code is inert, so the whole ladder is reachable from that
one binary by setting environment variables. That control appears in the summary as `forkoff`.

## The arms, and the switches that define them

Every row of the summary is the same binary under a different set of switches, documented in
[`../../../docs/switches.md`](../../../docs/switches.md). Reproducing the ablation means setting
these; nothing else differs between rows except the `.olean` set, which improvement 2 requires to
have been written by a patched compiler.

| arm | what is on | switches set |
|---|---|---|
| `stock` | nothing — the unmodified release | — |
| `forkoff` | nothing, but on the fork binary | all five off |
| `imp1` … `imp12345` | improvements added one at a time | the later ones off |
| `full` | all six | `LEAN_LAZY_PARTS=all` |
| `no1` | all but improvement 1 | `LEAN_MMAP_RESERVE=0` |
| `no2` | all but improvement 2 | stock `.olean` set |
| `no3` | all but improvement 3 | `LEAN_NO_TOUCH=0` |
| `no4` | all but improvement 4 | `LEAN_LAZY_PARTS=0` |
| `no5` | all but improvement 5 | `LEAN_SEARCH_INDEX=0` |
| `no6` | all but improvement 6 | `LEAN_TACTIC_INDEX=0` |
| `no23`, `no45` | two off at once | the pair above |

Improvement 2 has no switch, because it changes how `.olean` files are *written*: turning it off
means pointing `LEAN_PATH` at a library built by an unpatched compiler.

## The other machines

Table 1's container, cloud-server and x86-64 rows were measured on a Linux container and on AWS
instances, with driver scripts specific to those hosts. Those drivers are not included: they
hardcode the sandbox's paths and toolchains and would not run anywhere else. The figures they
produced are in the paper, and the same measurements can be taken with
[`../../scripts/repro-import.py`](../../scripts/repro-import.py) on any machine with a built fork.

## Reproducing

[`../../scripts/repro-import.py`](../../scripts/repro-import.py) re-measures the import figures in
about five minutes given a built fork; it builds nothing itself.
[`../../scripts/check-equivalence.sh`](../../scripts/check-equivalence.sh) re-runs the
byte-identical-output check in about eight. [`../README.md`](../README.md) describes the five
suites the harness covers.
