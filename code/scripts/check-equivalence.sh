#!/usr/bin/env bash
# Check that the fork behaves identically to stock Lean v4.33.1: byte-identical
# stdout, stderr and exit code on the project's equivalence suite.
#
#   ./check-equivalence.sh --fork DIR [--stock DIR] [--project DIR] [--out DIR]
#
# --fork/--stock are toolchain directories (bin/lean + lib/lean); --project is a
# Mathlib v4.33.1 checkout with a downloaded olean cache.
#
# Cases: the 7 tactic/library-search/pretty-printing files in ../../code/benchmarks/cli/equivalence,
# the 15 textbook files in ../../code/benchmarks/cli/textbook with `#print axioms` appended for every
# theorem, and (unless --no-mathlib-files) 4 Mathlib source files re-elaborated in
# place.  Five cases exit 1 by design; the exit code is part of what is compared.
#
# Cost: ~8 minutes and no disk beyond the output directory and the fork's caches.
# It builds nothing.  `eq-goals.lean` (29 exact?/apply?/rw? goals, 5,352 lines of
# output) is the slowest case and the load-bearing one.
#
# This is *our* pass criterion, run on your machine.  It is not a proof of
# equivalence: it is 26 files chosen to move if the fork perturbed a DiscrTree
# insertion order, an instance priority, a candidate ordering or an axiom set.

set -u
STOCK="$HOME/.elan/toolchains/leanprover--lean4---v4.33.1"
FORK=""
PROJECT="${MATHLIB:-$HOME/mathlib4}"
OUT=""
CACHE="$HOME/.cache/lean-fork-repro"
WITH_ML=1

while [ $# -gt 0 ]; do
  case "$1" in
    --fork) FORK="$2"; shift 2;;
    --stock) STOCK="$2"; shift 2;;
    --project) PROJECT="$2"; shift 2;;
    --out) OUT="$2"; shift 2;;
    --cache-dir) CACHE="$2"; shift 2;;
    --no-mathlib-files) WITH_ML=0; shift;;
    -h|--help) sed -n '2,21p' "$0"; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[ -n "$FORK" ] || { echo "error: --fork DIR is required" >&2; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
CASES="$HERE/../../code/benchmarks/cli"
OUT="${OUT:-$CACHE/equiv-$(date -u +%Y%m%dT%H%M%SZ)}"

for d in "$FORK" "$STOCK" "$PROJECT"; do
  [ -d "$d" ] || { echo "error: not a directory: $d" >&2; exit 2; }
done
command -v lake >/dev/null || { echo "error: lake not on PATH" >&2; exit 2; }

# LEAN_PATH: the project's package olean directories, with the toolchain's own
# lib/lean appended per configuration.  Never run a fork binary under `lake env`:
# it exports DYLD_LIBRARY_PATH/LD_LIBRARY_PATH pointing at the stock toolchain and
# the fork then loads the stock runtime library (see ../../docs/warnings.md).
PKGS="$(cd "$PROJECT" && lake env printenv LEAN_PATH | sed 's|:[^:]*$||')"
[ -n "$PKGS" ] || { echo "error: could not discover LEAN_PATH in $PROJECT" >&2; exit 2; }
LP_STOCK="$PKGS:$STOCK/lib/lean"
LP_FORK="$PKGS:$FORK/lib/lean"

mkdir -p "$OUT/cases" "$CACHE/lazy-parts" "$CACHE/tactic-index" "$CACHE/search-index"
FORKENV=(LEAN_LAZY_PARTS=all "LEAN_LAZY_PARTS_INDEX_DIR=$CACHE/lazy-parts"
         "LEAN_TACTIC_INDEX_DIR=$CACHE/tactic-index" "LEAN_SEARCH_INDEX_CACHE_DIR=$CACHE/search-index")

cp "$CASES"/equivalence/*.lean "$OUT/cases/"
for f in "$CASES"/textbook/*.lean; do
  b="$(basename "$f" .lean)"; c="$OUT/cases/tb_$b.lean"; cp "$f" "$c"
  grep -oE "^(theorem|lemma) [^ :(]+" "$f" | awk '{print "#print axioms " $2}' >> "$c"
done
if [ "$WITH_ML" = 1 ]; then
  for m in Mathlib/Logic/Basic.lean Mathlib/Order/Basic.lean Mathlib/Topology/Basic.lean \
           Mathlib/LinearAlgebra/Matrix/Determinant/Basic.lean; do
    [ -f "$PROJECT/$m" ] && cp "$PROJECT/$m" "$OUT/cases/ml_$(echo "$m" | tr / _)"
  done
fi

SUM="$OUT/summary.txt"; : > "$SUM"
log() { echo "$@" | tee -a "$SUM"; }
log "equivalence: stock=$STOCK"
log "             fork =$FORK"
log "             proj =$PROJECT   out=$OUT"

# The fork writes its lazy-loading index on the first import of a closure; do that once,
# outside the comparison, so the timings below are not the warm-up.  The tactic index image
# is *not* written automatically — it is a shipped artefact — so the second pass builds one
# with LEAN_TACTIC_INDEX_WRITE=1, which is what puts the mapped-image path under test here.  It must
# follow the first pass: the image is keyed on the region list the lazy-loading index
# produces, so one built against a cold index is keyed differently and never found again.
# Every case in cases/ has the same header (`import Mathlib`), so one image serves them all;
# the four Mathlib source files (--no-mathlib-files to skip) have their own closures and run
# on the no-image path, which is the other half of what must be equivalent.
log "pre-warming the fork's lazy-loading index and building a tactic index image (a minute, once) …"
env LEAN_PATH="$LP_FORK" "${FORKENV[@]}" "$FORK/bin/lean" "$OUT/cases/eq-simp.lean" >/dev/null 2>&1
env LEAN_PATH="$LP_FORK" "${FORKENV[@]}" LEAN_TACTIC_INDEX_WRITE=1 "$FORK/bin/lean" "$OUT/cases/eq-simp.lean" >/dev/null 2>&1
env LEAN_PATH="$LP_FORK" "${FORKENV[@]}" "$FORK/bin/lean" "$OUT/cases/eq-goals.lean" >/dev/null 2>&1

n=0; ok=0; fail=0
for c in "$OUT"/cases/*.lean; do
  b="$(basename "$c" .lean)"; n=$((n+1))
  ( cd "$OUT" && LEAN_PATH="$LP_STOCK" "$STOCK/bin/lean" "$c" > "$OUT/$b.stock.out" 2> "$OUT/$b.stock.err"; echo $? > "$OUT/$b.stock.rc" )
  ( cd "$OUT" && env LEAN_PATH="$LP_FORK" "${FORKENV[@]}" "$FORK/bin/lean" "$c" > "$OUT/$b.fork.out" 2> "$OUT/$b.fork.err"; echo $? > "$OUT/$b.fork.rc" )
  st=OK
  cmp -s "$OUT/$b.stock.out" "$OUT/$b.fork.out" || st="DIFF(stdout)"
  cmp -s "$OUT/$b.stock.err" "$OUT/$b.fork.err" || st="$st DIFF(stderr)"
  cmp -s "$OUT/$b.stock.rc"  "$OUT/$b.fork.rc"  || st="$st DIFF(rc)"
  if [ "$st" = OK ]; then ok=$((ok+1)); else fail=1; fi
  printf "%-44s rc=%s lines=%s %s\n" "$b" "$(cat "$OUT/$b.stock.rc")" \
         "$(wc -l < "$OUT/$b.stock.out" | tr -d ' ')" "$st" | tee -a "$SUM"
done
log "cases: $ok/$n byte-identical in stdout, stderr and exit code"
if [ "$fail" = 0 ]; then log "VERDICT: EQUIVALENT"; else log "VERDICT: DIFFERENCES FOUND — see $OUT"; fi
exit $fail
