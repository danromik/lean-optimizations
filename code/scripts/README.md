# The scripts, and what each one costs

Nothing here rebuilds a library. The two measurement scripts read an existing `.olean`
cache and write only their own side files.

| Script | What it does | Time | Disk |
|---|---|---|---|
| `setup-mathlib.sh` | clone Mathlib v4.33.1 and download the community olean cache | ~5 min | ~7 GB |
| `demo-fork-hang.sh` | build and run the 53-line fork demonstration: hangs a child deterministically, and shows the two cases that do not hang (~15 s, needs a C compiler) |
| `check-patches.sh` | verify every patch applies to a clean v4.33.1 export, that each fix applies on top of the fork, and that all four apply together | ~10 s | ~700 MB, temporary |
| `repro-import.py` | the headline measurement: warm `import Mathlib`, stock vs fork, interleaved | ~5 min (first run on a closure adds ~1 min of cache warming) | ~500 MB of fork side files under `--cache-dir` |
| `check-equivalence.sh` | 26 case files run under both toolchains; stdout, stderr and exit code compared byte for byte | ~8 min | a few MB of output, plus the same side files |

Not included here, and expensive: building the fork (~25 min from scratch, ~10 GB —
[`docs/building.md`](../../docs/building.md)), and rebuilding Mathlib from source with the fork
(~43 min, ~11 GB, and **`LEAN_TACTIC_INDEX=0` is mandatory** — [`docs/warnings.md`](../../docs/warnings.md) §1).

## The cheapest meaningful run

```sh
./setup-mathlib.sh --dir ~/mathlib4
# … build the fork per docs/building.md, then:
python3 repro-import.py --fork ~/lean4/build/release/stage2 --project ~/mathlib4
```

On an M2 Pro this prints something close to

```
| case | config | wall s | user s | sys s | max RSS GB | minor faults |
| import-mathlib | stock | 10.2 | 1.8 | 8.0 | 5.29 | 397918 |
| import-mathlib | fork  |  2.3 | 1.3 | 1.0 | 1.40 | 167993 |
```

Your wall times will differ with load, disk and machine; the **ratios** and the fault counts
are the comparable part. Read [`docs/warnings.md`](../../docs/warnings.md) §5 before quoting the RSS
column anywhere.

**The stock RSS this script reports is not the paper's.** It gives about 5.3 GB for stock
`import Mathlib`, where Table 1 says 5.7 from the sweep in
[`code/benchmarks/results/`](../benchmarks/results/). Each figure is stable within its own tool
and we have not established why they differ; the gap is confined to the stock row, and the fork
row agrees at 1.39–1.40. The fork-to-stock ratio is the part that reproduces.

## Options worth knowing

`repro-import.py`

* `--cases import-mathlib module-root mathlib-tactic` — the three headline cases. A `module`
  root imports only the public level and is cheaper for both configurations.
* `--fork-env K=V` (repeatable) — turn individual changes off; see
  [`docs/switches.md`](../../docs/switches.md). This is how each change was attributed.
* `--repeat N` — timed repeats per configuration; the table reports medians.
* `--json FILE` — every run's raw `rusage` numbers.

`check-equivalence.sh`

* `--no-mathlib-files` — skip the four Mathlib sources re-elaborated in place, if your
  checkout is not Mathlib.
* `--out DIR` — keep every case's stdout, stderr and exit code for inspection. Five cases
  exit 1 by design; the exit code is part of what is compared.

`check-patches.sh`

* `--keep` — leave the exported tree in place (with the fork patch applied) so you can look
  at the result.

## Two rules these scripts follow, and you should too

1. **Never run a fork binary inside `lake env`.** It exports a library path pointing at the
   stock toolchain, and the fork binary then loads the stock runtime and reports stock
   numbers. The scripts compute `LEAN_PATH` once and run the bare binary.
2. **Interleave the configurations.** Machine load moves over minutes; running all of A then
   all of B attributes the drift to the change. Every timed loop here alternates.
