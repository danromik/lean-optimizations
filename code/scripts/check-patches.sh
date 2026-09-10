#!/usr/bin/env bash
# Verify that every patch in ../../patches applies cleanly to a clean Lean v4.33.1
# source tree, that each bug fix applies on top of the fork, and that they
# apply together.  Costs a few seconds and ~700 MB of temporary disk; nothing is
# built and your checkout is not touched (the tree is exported with `git archive`).
#
#   ./check-patches.sh --lean4 /path/to/lean4          # a clone with tag v4.33.1
#
# If you have no clone yet:
#   git clone https://github.com/leanprover/lean4 && cd lean4 && git checkout v4.33.1
# (commit 819816b2e0a3bf405af45ae5c7af2491d8f5bee6)

set -u
LEAN4=""
KEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --lean4) LEAN4="$2"; shift 2;;
    --keep) KEEP=1; shift;;
    -h|--help) sed -n '2,11p' "$0"; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
[ -n "$LEAN4" ] || { echo "error: --lean4 DIR is required" >&2; exit 2; }
[ -d "$LEAN4/.git" ] || { echo "error: $LEAN4 is not a git checkout" >&2; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"; P="$HERE/../../patches"
FORK="$P/lean4-v4.33.1-optimized.patch"
FIXES="$P/fix-codegen-meta-initialize.patch $P/fix-server-config-watch.patch"
# Improvement 10 is the one change outside the fork that edits a file the fork also edits
# (src/runtime/object.cpp), so it is checked against a clean tree but not on top of the fork.
FORKSAFETY="$P/fix-fork-safety.patch"

TMP="$(mktemp -d)"; trap '[ "$KEEP" = 1 ] || rm -rf "$TMP"' EXIT
echo "exporting v4.33.1 from $LEAN4 …"
git -C "$LEAN4" archive v4.33.1 | tar -x -C "$TMP" || {
  echo "error: could not export tag v4.33.1 from $LEAN4" >&2; exit 2; }

rc=0
check() { # check <patch> <label>
  printf "  %-46s " "$(basename "$1")"
  if git -C "$TMP" apply --binary --check "$1" 2>"$TMP/err"; then echo "ok  ($2)"
  else echo "FAILED ($2)"; sed 's/^/      /' "$TMP/err"; rc=1; fi
}

echo
echo "each patch on its own, against clean v4.33.1:"
for p in "$FORK" "$P/lean4-v4.33.1-optimized-linux.patch" $FIXES "$FORKSAFETY"; do check "$p" "alone"; done

echo
echo "each bug fix on top of the fork:"
git -C "$TMP" apply --binary "$FORK" || { echo "  could not apply the fork patch" >&2; exit 1; }
for p in $FIXES; do check "$p" "on top of the fork"; done

echo
echo "all three together (fork + the two fixes, applied cumulatively):"
for p in $FIXES; do
  printf "  %-46s " "+ $(basename "$p")"
  if git -C "$TMP" apply --binary "$p" 2>"$TMP/err"; then echo "ok  (applied)"
  else echo "FAILED (cumulative)"; sed 's/^/      /' "$TMP/err"; rc=1; fi
done

echo
echo "the fork-safety patch, on top of the fork:"
printf "  %-46s " "$(basename "$FORKSAFETY")"
if git -C "$TMP" apply --binary --check "$FORKSAFETY" 2>/dev/null; then
  echo "ok  (on top of the fork)"
elif (cd "$TMP" && patch -p1 --dry-run --forward --silent < "$FORKSAFETY" 2>/dev/null); then
  echo "needs fuzz  (both it and the fork edit src/runtime/object.cpp;"
  echo "  the edits do not overlap, so \`patch -p1\` applies it, but \`git apply\`"
  echo "  will not: its context lines come from the unpatched file)"
else
  echo "FAILED (on top of the fork)"; rc=1
fi

echo
if [ "$rc" = 0 ]; then
  echo "VERDICT: all patches apply.  The fork touches 16 files; two of the bug fixes touch"
  echo "four further files that the fork does not touch, which is why each is adoptable"
  echo "independently of the others and of the fork.  The fork-safety patch is the"
  echo "exception: it edits src/runtime/object.cpp, which the fork edits too."
else
  echo "VERDICT: at least one patch did not apply."
fi
exit $rc
