#!/usr/bin/env bash
# Show that a forked child of a Lean process hangs, and show exactly what arms the failure.
#
#   ./demo-fork-hang.sh --toolchain ~/.elan/toolchains/leanprover--lean4---v4.33.1
#
# Needs a Lean toolchain and a C compiler. Nothing else: no Mathlib, no patched Lean, no corpus.
# Compiles forktest.c (53 lines, against lean/lean.h) and runs it three ways:
#
#   1. pool warmed, then fork    -> the child HANGS, every time
#   2. pool never used, then fork -> the child completes
#   3. pool warmed, child uses a dedicated thread -> the child completes
#
# Case 1 is the defect. Cases 2 and 3 are what make it a diagnosis rather than an anecdote: the
# child hangs only once a standard-priority worker exists to be inherited, and work that asks for
# its own thread bypasses the broken pool. See ../../docs/improvements/10-fork-safety.md.
#
# Takes about fifteen seconds; each hang is detected by a five-second timeout in the harness.

set -u
TOOLCHAIN=""; KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --toolchain) TOOLCHAIN="$2"; shift 2;;
    --keep) KEEP=1; shift;;
    -h|--help) sed -n '2,18p' "$0"; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done

if [ -z "$TOOLCHAIN" ]; then
  # the toolchain that owns whichever `lean` is on the PATH
  L=$(command -v lean 2>/dev/null) || true
  [ -n "${L:-}" ] && TOOLCHAIN=$(cd "$(dirname "$L")/.." && pwd)
fi
[ -n "$TOOLCHAIN" ] || { echo "error: --toolchain DIR is required (no lean on PATH)" >&2; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"
LIBDIR="$TOOLCHAIN/lib/lean"
for f in "$TOOLCHAIN/include/lean/lean.h" "$LIBDIR"; do
  [ -e "$f" ] || { echo "error: $f not found; is $TOOLCHAIN a Lean toolchain?" >&2; exit 2; }
done

TMP="$(mktemp -d)"; trap '[ "$KEEP" = 1 ] || rm -rf "$TMP"' EXIT
CC=${CC:-cc}
case "$(uname -s)" in
  Darwin) SDK=$(xcrun --show-sdk-path 2>/dev/null || true)
          ISYS=${SDK:+-isysroot $SDK}
          LIB=libleanshared.dylib ;;
  *)      ISYS=""; LIB=libleanshared.so ;;
esac
[ -e "$LIBDIR/$LIB" ] || { echo "error: $LIBDIR/$LIB not found" >&2; exit 2; }

echo "compiling forktest.c against $TOOLCHAIN"
# shellcheck disable=SC2086
$CC -O2 $ISYS -I"$TOOLCHAIN/include" "$HERE/forktest.c" -o "$TMP/forktest" \
    -L"$LIBDIR" -lleanshared -Wl,-rpath,"$LIBDIR" || {
  echo "error: compilation failed" >&2; exit 1; }

rc=0
run() { # run <label> <expectation: hang|complete> <args...>
  label=$1; want=$2; shift 2
  printf "\n%s\n" "$label"
  out=$("$TMP/forktest" "$@" 2>&1); st=$?
  printf '%s\n' "$out" | sed 's/^/    /'
  if [ "$want" = hang ]; then
    [ $st -ne 0 ] && echo "  => hung, as expected" || { echo "  => DID NOT HANG (unexpected)"; rc=1; }
  else
    [ $st -eq 0 ] && echo "  => completed, as expected" || { echo "  => HUNG (unexpected)"; rc=1; }
  fi
}

run "1. one standard-priority task, then fork:"            hang     0
run "2. the pool never used, then fork:"                   complete 1
run "3. one standard-priority task, child asks for its own thread:" complete 0 9

echo
if [ $rc = 0 ]; then
  cat <<'EOF'
VERDICT: as documented. A child forked from a process whose task pool has run
anything hangs deterministically; the same code forked before the pool is ever
used completes, and so does a child that keeps to its own thread. The cause is
the pool's idle-worker count, which fork copies while the threads it counts are
left behind.
EOF
else
  echo "VERDICT: at least one case did not behave as documented."
fi
exit $rc
