#!/usr/bin/env bash
# Fetch the Mathlib checkout the other scripts measure against: tag v4.33.1 with the
# community olean cache downloaded.  Nothing is compiled — `lake exe cache get` downloads
# 8,690 prebuilt files.
#
#   ./setup-mathlib.sh [--dir ~/mathlib4]
#
# Cost: about 5 minutes on a fast connection, and ~7 GB of disk (6.5 GB of .lake/build,
# 391 MB of packages).  If the directory already exists this only refreshes the cache.
#
# Requires elan on PATH; it will install the v4.33.1 toolchain on first use if you do not
# have it.

set -eu
DIR="$HOME/mathlib4"
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2;;
    -h|--help) sed -n '2,12p' "$0"; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
command -v elan >/dev/null || { echo "error: elan not on PATH (https://github.com/leanprover/elan)" >&2; exit 2; }
command -v git  >/dev/null || { echo "error: git not on PATH" >&2; exit 2; }

if [ -d "$DIR/.git" ]; then
  echo "using existing checkout at $DIR"
  rev="$(git -C "$DIR" rev-parse HEAD)"
  echo "  HEAD = $rev"
  [ "$rev" = "0df444a360eaa60ab8c11dca51a86af692955474" ] || \
    echo "  note: this is not Mathlib v4.33.1 (0df444a360eaa60ab8c11dca51a86af692955474);" \
         "the numbers in the manuscript are against that commit"
else
  echo "cloning Mathlib v4.33.1 into $DIR (shallow) …"
  git clone --depth 1 --branch v4.33.1 https://github.com/leanprover-community/mathlib4 "$DIR"
fi

echo "downloading the olean cache (this is the ~7 GB part) …"
( cd "$DIR" && lake exe cache get )
echo
echo "done.  Check it loads:"
echo "  cd $DIR && echo 'import Mathlib' > /tmp/t.lean && lake env lean /tmp/t.lean"
echo
echo "Then:  python3 $(cd "$(dirname "$0")" && pwd)/repro-import.py --fork <toolchain> --project $DIR"
