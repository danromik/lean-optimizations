#!/usr/bin/env bash
# Fetch the four real projects the `sessions/corpus/` sessions edit.  Each is cloned
# shallow and its dependency olean cache is downloaded; nothing is compiled here.
#
#   ./fetch-corpus.sh [--dir ~/lean-work/corpus] [--only "FLT carleson"]
#
# Cost: about 30 GB of disk and 20-40 minutes on a fast connection for all four, plus
# one Lean toolchain per project (each project pins its own; elan installs it on first
# use, ~1 GB each, three distinct ones here).  Per project, roughly:
#
#   FLT                    v4.34.0-rc1   ~8 GB
#   carleson               v4.34.0-rc2   ~8 GB
#   PrimeNumberTheoremAnd  v4.32.2       ~9 GB
#   formal-conjectures     v4.33.1       ~8 GB
#
# These are upstream repositories at whatever they point to today, so they drift.  The
# sessions are written against the file contents at a particular revision, so a session can
# start failing its assertions after an upstream edit: a failing corpus session almost
# always means the file moved, not that Lean got slower.  Every run you make stamps the
# checkout's commit into its own result file (meta.project.git_commit), so a failure can be
# traced to the revision it was seen on.  See README.md, "The corpus sessions".
#
# Requires elan and git on PATH.

set -eu
DIR="${LEAN_WORK:-$HOME/lean-work}/corpus"
ONLY=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2;;
    --only) ONLY="$2"; shift 2;;
    -h|--help) sed -n '2,23p' "$0"; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
command -v elan >/dev/null || { echo "error: elan not on PATH (https://github.com/leanprover/elan)" >&2; exit 2; }
command -v git  >/dev/null || { echo "error: git not on PATH" >&2; exit 2; }

NAMES=(FLT carleson PrimeNumberTheoremAnd formal-conjectures)
URLS=(https://github.com/ImperialCollegeLondon/FLT
      https://github.com/fpvandoorn/carleson
      https://github.com/AlexKontorovich/PrimeNumberTheoremAnd
      https://github.com/google-deepmind/formal-conjectures)

mkdir -p "$DIR"
for i in "${!NAMES[@]}"; do
  name=${NAMES[$i]}; url=${URLS[$i]}; dest="$DIR/$name"
  if [ -n "$ONLY" ] && ! [[ " $ONLY " == *" $name "* ]]; then continue; fi
  echo "=== $name ==="
  if [ -d "$dest/.git" ]; then
    echo "  using existing checkout at $dest"
  else
    echo "  cloning $url (shallow) …"
    git clone --depth 1 "$url" "$dest"
  fi
  echo "  commit:    $(git -C "$dest" rev-parse HEAD)"
  echo "  toolchain: $(cat "$dest/lean-toolchain")"
  # `lake exe cache get` installs the pinned toolchain via elan on first use and downloads
  # the dependency oleans (Mathlib's, mostly).  This is the multi-GB part.
  echo "  downloading the olean cache …"
  ( cd "$dest" && lake exe cache get )
done

echo
echo "done.  Run one corpus session with, for example:"
echo "  python3 $(cd "$(dirname "$0")" && pwd)/lspbench.py run \\"
echo "      --session sessions/corpus/adv-04-fc-polygon.json --project-dir formal-conjectures=$DIR/formal-conjectures"
