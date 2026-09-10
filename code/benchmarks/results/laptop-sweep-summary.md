timed runs: 96, nonzero exits: 0

### LADDER — import Mathlib

| configuration | wall s | max RSS GB | CPU s | page reclaims | walls |
|---|---:|---:|---:|---:|---|
| unmodified v4.33.1 | 10.17 | 5.68 | 9.26 | 423,362 | 10.14 / 10.17 / 10.33 |
| [control] fork, all switches off | 10.74 | 5.69 | 9.64 | 423,131 | 10.64 / 10.74 / 10.82 |
| + improvement 1 | 3.83 | 5.69 | 3.48 | 423,383 | 3.75 / 3.83 / 3.90 |
| + improvements 1,3 | 3.66 | 3.44 | 3.31 | 286,356 | 3.65 / 3.66 / 3.93 |
| + improvements 1,2,3 | 3.57 | 2.76 | 3.17 | 244,516 | 3.50 / 3.57 / 3.69 |
| + improvements 1,2,3,4 | 2.79 | 1.64 | 2.51 | 176,137 | 2.77 / 2.79 / 3.10 |
| + improvements 1,2,3,4,5 | 2.86 | 1.65 | 2.56 | 177,069 | 2.79 / 2.86 / 2.95 |
| + improvement 6 = full fork | 2.33 | 1.39 | 2.04 | 160,553 | 2.31 / 2.33 / 2.52 |

_ablation — lost when one is switched off, others on (import Mathlib):_

| off | wall s | % | RSS GB | % |
|---|---:|---:|---:|---:|
| no1 | +0.16 | +6.9 | -0.01 | -0.7 |
| no2 | +0.08 | +3.4 | +0.13 | +9.1 |
| no3 | +0.13 | +5.6 | +0.01 | +0.5 |
| no23 | +0.35 | +15.0 | +1.39 | +100.0 |
| no4 | +0.81 | +34.8 | +0.93 | +67.2 |
| no5 | +0.06 | +2.6 | -0.02 | -1.1 |
| no45 | +0.82 | +35.2 | +0.93 | +67.3 |
| no6 | +0.46 | +19.7 | +0.25 | +18.0 |

### LADDER — import Mathlib + one exact?

| configuration | wall s | max RSS GB | CPU s | page reclaims | walls |
|---|---:|---:|---:|---:|---|
| unmodified v4.33.1 | 15.05 | 7.89 | 38.57 | 558,163 | 14.96 / 15.05 / 15.11 |
| [control] fork, all switches off | 15.52 | 7.87 | 40.21 | 556,812 | 15.51 / 15.52 / 15.98 |
| + improvement 1 | 8.66 | 7.88 | 33.73 | 556,952 | 8.50 / 8.66 / 8.72 |
| + improvements 1,3 | 8.45 | 7.88 | 34.18 | 557,000 | 8.45 / 8.45 / 8.67 |
| + improvements 1,2,3 | 8.60 | 5.81 | 33.50 | 431,000 | 8.53 / 8.60 / 8.65 |
| + improvements 1,2,3,4 | 8.78 | 4.73 | 46.04 | 365,032 | 8.61 / 8.78 / 8.80 |
| + improvements 1,2,3,4,5 | 4.25 | 1.82 | 15.67 | 187,575 | 4.18 / 4.25 / 4.34 |
| + improvement 6 = full fork | 3.81 | 1.57 | 14.15 | 171,602 | 3.72 / 3.81 / 3.88 |

_ablation — lost when one is switched off, others on (import Mathlib + one exact?):_

| off | wall s | % | RSS GB | % |
|---|---:|---:|---:|---:|
| no1 | +0.11 | +2.9 | -0.00 | -0.0 |
| no2 | -0.07 | -1.8 | +1.06 | +67.7 |
| no3 | +0.05 | +1.3 | +0.01 | +0.5 |
| no23 | +0.42 | +11.0 | +1.35 | +86.0 |
| no4 | +0.26 | +6.8 | +1.10 | +70.1 |
| no5 | +4.66 | +122.3 | +2.94 | +187.8 |
| no45 | +4.11 | +107.9 | +3.87 | +246.9 |
| no6 | +0.39 | +10.2 | +0.27 | +17.4 |

### DERIVED — cost of the first `exact?` alone (t-easy minus t-import, same config)

| configuration | wall s | RSS added GB | CPU s |
|---|---:|---:|---:|
| unmodified v4.33.1 | +4.88 | +2.21 | +29.31 |
| [control] fork, all switches off | +4.78 | +2.19 | +30.57 |
| + improvement 1 | +4.83 | +2.19 | +30.25 |
| + improvements 1,3 | +4.79 | +4.43 | +30.87 |
| + improvements 1,2,3 | +5.03 | +3.05 | +30.33 |
| + improvements 1,2,3,4 | +5.99 | +3.09 | +43.53 |
| + improvements 1,2,3,4,5 | +1.39 | +0.17 | +13.11 |
| + improvement 6 = full fork | +1.48 | +0.18 | +12.11 |
| no1 | +1.43 | +0.19 | +12.54 |
| no2 | +1.33 | +1.11 | +11.48 |
| no3 | +1.40 | +0.18 | +13.27 |
| no23 | +1.55 | +0.14 | +12.65 |
| no4 | +0.93 | +0.34 | +5.99 |
| no5 | +6.08 | +3.14 | +43.73 |
| no45 | +4.77 | +3.11 | +30.23 |
| no6 | +1.41 | +0.20 | +12.55 |

### SUMS (import) — do the ablation rows add up?

t-import: six singles sum to 1.70 s / 1.29 GB; fork vs stock is 7.84 s / 4.29 GB
t-easy: six singles sum to 5.40 s / 5.38 GB; fork vs stock is 11.24 s / 6.32 GB
