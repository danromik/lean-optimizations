#!/bin/sh
# Stand in for `lean`, adding one --load-dynlib=... for each of Mathlib's compiled tactic
# libraries. This is improvement 8: the libraries hold machine code the interpreter prefers
# wherever it exists, so loading them makes Mathlib's own tactics run compiled rather than
# interpreted.
#
# Why a wrapper rather than a lakefile setting: Lake records `moreLeanArgs` in the traces that
# decide what to rebuild, so naming the libraries there would invalidate the compiled files this
# improvement depends on leaving untouched. A wrapper installed as the `lean` of a toolchain
# directory is invisible to those traces.
#
#   LEAN_REAL     the actual lean to run. Defaults to the `lean` sitting beside this script in a
#                 toolchain's bin/, which is how it is meant to be installed; failing that, the
#                 first `lean` on PATH that is not this wrapper.
#   LEAN_DYNLIBS  file listing one shared library per line, in load order (dependencies first;
#                 the wrong order fails with an unresolved-symbol error). Defaults to
#                 dynlibs.txt beside this script.
#   LEAN_NATIVE=0 run without the libraries. This is the A/B control: with it the same binary
#                 elaborates the same files interpreting the tactics, which is how the
#                 byte-identical comparison in docs/improvements/08-compiled-mathlib-tactics.md
#                 was made.
#
# See that document for building the libraries and for the staleness caveat: nothing checks that
# they were built from the sources of the Mathlib they are loaded against.

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -n "$LEAN_REAL" ]; then
    real=$LEAN_REAL
elif [ -x "$here/lean.real" ]; then
    real=$here/lean.real
else
    real=$(command -v lean 2>/dev/null)
    if [ "$real" = "$here/lean" ] || [ -z "$real" ]; then
        echo "lean-native-wrapper: cannot find the real lean; set LEAN_REAL" >&2
        exit 127
    fi
fi

list=${LEAN_DYNLIBS:-$here/dynlibs.txt}

if [ "${LEAN_NATIVE:-1}" = 0 ] || [ ! -f "$list" ]; then
    exec "$real" "$@"
fi

# one --load-dynlib per line, keeping the file's order
set -- $(sed -e '/^[[:space:]]*$/d' -e '/^[[:space:]]*#/d' -e 's/^/--load-dynlib=/' "$list") "$@"
exec "$real" "$@"
