#!/usr/bin/env python3
"""Reproduce the headline measurement: warm `import Mathlib`, stock Lean vs the fork.

This is the cheapest meaningful reproduction in the package.  It builds nothing:
it needs a stock Lean v4.33.1 toolchain, a fork toolchain built from
`patches/lean4-v4.33.1-optimized.patch` (see ../../docs/building.md), and a Mathlib
v4.33.1 checkout whose `.olean` cache has been downloaded with `lake exe cache get`.

    python3 repro-import.py --fork  /path/to/lean4/build/release/stage2 \
                            --stock ~/.elan/toolchains/leanprover--lean4---v4.33.1 \
                            --project ~/mathlib4

Cost: ~1 minute of warm-up per configuration plus ~15 s per repeat, and roughly
500 MB of side-file caches under --cache-dir (see ../../docs/warnings.md).  Nothing is
rebuilt and nothing outside --cache-dir and a scratch directory is written.

Both toolchains are run as bare `lean` binaries with an explicit LEAN_PATH; we
never go through `lake env`, because `lake env` exports DYLD_LIBRARY_PATH /
LD_LIBRARY_PATH pointing at the *stock* toolchain's lib/lean, and a fork `lean`
run under it silently loads the stock runtime library and produces stock numbers.
That mistake cost us a measurement session; see ../../docs/warnings.md.
"""

import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import time

IS_MAC = platform.system() == "Darwin"

CASES = {
    "import-mathlib": "import Mathlib\n",
    "module-root": "module\npublic import Mathlib\n",
    "mathlib-tactic": "import Mathlib.Tactic\n",
}


def die(msg):
    print("error: " + msg, file=sys.stderr)
    sys.exit(1)


def toolchain_bin(d, name="lean"):
    p = os.path.join(d, "bin", name)
    if not os.path.isfile(p):
        die("no %s in %s (expected a toolchain directory with bin/ and lib/lean/)" % (name, d))
    return p


def lean_version(binpath, tcdir):
    env = dict(os.environ)
    libdir = os.path.join(tcdir, "lib", "lean")
    key = "DYLD_LIBRARY_PATH" if IS_MAC else "LD_LIBRARY_PATH"
    env[key] = libdir + os.pathsep + env.get(key, "")
    out = subprocess.run([binpath, "--version"], capture_output=True, text=True, env=env)
    return (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else "?"


def discover_lean_path(project):
    """Package olean directories of the project, with any toolchain lib/lean stripped."""
    lake = shutil.which("lake")
    if lake is None:
        die("`lake` not on PATH; needed once to discover LEAN_PATH for --project")
    r = subprocess.run([lake, "env", "printenv", "LEAN_PATH"], cwd=project,
                       capture_output=True, text=True)
    if r.returncode != 0:
        die("`lake env printenv LEAN_PATH` failed in %s:\n%s" % (project, r.stderr))
    parts = [p for p in r.stdout.strip().split(os.pathsep) if p]
    if not parts:
        die("`lake env printenv LEAN_PATH` produced nothing in " + project)
    # Lake puts the *toolchain's* lib/lean last and the project's package olean
    # directories before it.  Drop the last entry; each configuration appends its
    # own toolchain lib/lean instead, so the fork reads the fork's core oleans.
    project_abs = os.path.abspath(project)
    if os.path.abspath(parts[-1]).startswith(project_abs + os.sep):
        die("unexpected LEAN_PATH shape (last entry is inside the project): " + parts[-1])
    return parts[:-1]


def measure(binpath, argv, env):
    """Run once; return rusage-derived metrics.  Uses wait4 like bench/harness/measure.py."""
    devnull = os.open(os.devnull, os.O_WRONLY)
    t0 = time.perf_counter()
    pid = os.posix_spawn(binpath, [binpath] + argv, env,
                         file_actions=[(os.POSIX_SPAWN_DUP2, devnull, 1),
                                       (os.POSIX_SPAWN_DUP2, devnull, 2)])
    _, status, ru = os.wait4(pid, 0)
    wall = time.perf_counter() - t0
    os.close(devnull)
    maxrss = ru.ru_maxrss if IS_MAC else ru.ru_maxrss * 1024  # macOS bytes, Linux KiB
    return {
        "wall_s": wall,
        "user_s": ru.ru_utime,
        "sys_s": ru.ru_stime,
        "maxrss_bytes": maxrss,
        "minflt": ru.ru_minflt,
        "majflt": ru.ru_majflt,
        "exit_code": os.waitstatus_to_exitcode(status) if hasattr(os, "waitstatus_to_exitcode") else status,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fork", required=True, metavar="DIR",
                    help="fork toolchain directory (…/build/release/stage2, or a linked toolchain)")
    ap.add_argument("--stock", metavar="DIR",
                    default=os.path.expanduser("~/.elan/toolchains/leanprover--lean4---v4.33.1"),
                    help="stock Lean v4.33.1 toolchain directory")
    ap.add_argument("--project", metavar="DIR",
                    default=os.environ.get("MATHLIB", os.path.expanduser("~/mathlib4")),
                    help="Mathlib v4.33.1 checkout with a downloaded olean cache")
    ap.add_argument("--repeat", type=int, default=3, help="timed repeats per configuration (default 3)")
    ap.add_argument("--cases", nargs="+", default=["import-mathlib"], choices=sorted(CASES),
                    help="which cases to run (default: import-mathlib)")
    ap.add_argument("--cache-dir", metavar="DIR",
                    default=os.path.expanduser("~/.cache/lean-fork-repro"),
                    help="where the fork's per-closure side files go (~500 MB for one closure)")
    ap.add_argument("--scratch", metavar="DIR",
                    default=os.path.expanduser("~/.cache/lean-fork-repro/scratch"))
    ap.add_argument("--fork-env", action="append", default=[], metavar="K=V",
                    help="extra environment for the fork run, e.g. --fork-env LEAN_LAZY_PARTS=0 "
                         "(repeatable; see ../../docs/switches.md)")
    ap.add_argument("--json", metavar="FILE", help="also write the raw per-run numbers here")
    args = ap.parse_args()

    for d in (args.fork, args.stock, args.project):
        if not os.path.isdir(d):
            die("not a directory: " + d)
    stock_bin, fork_bin = toolchain_bin(args.stock), toolchain_bin(args.fork)

    pkgs = discover_lean_path(args.project)
    os.makedirs(args.scratch, exist_ok=True)
    lazy_idx = os.path.join(args.cache_dir, "lazy-parts")
    tactic_idx = os.path.join(args.cache_dir, "tactic-index")
    search_idx = os.path.join(args.cache_dir, "search-index")
    for d in (lazy_idx, tactic_idx, search_idx):
        os.makedirs(d, exist_ok=True)

    def env_for(tcdir, extra):
        e = dict(os.environ)
        for k in ("LEAN_PATH", "DYLD_LIBRARY_PATH", "LD_LIBRARY_PATH", "LEAN_SYSROOT"):
            e.pop(k, None)
        e["LEAN_PATH"] = os.pathsep.join(pkgs + [os.path.join(tcdir, "lib", "lean")])
        e.update(extra)
        return e

    fork_extra = {
        "LEAN_LAZY_PARTS": "all",
        "LEAN_LAZY_PARTS_INDEX_DIR": lazy_idx,
        "LEAN_TACTIC_INDEX_DIR": tactic_idx,
        "LEAN_SEARCH_INDEX_CACHE_DIR": search_idx,
    }
    for kv in args.fork_env:
        if "=" not in kv:
            die("--fork-env expects K=V, got " + kv)
        k, v = kv.split("=", 1)
        fork_extra[k] = v

    configs = [
        ("stock", stock_bin, env_for(args.stock, {})),
        ("fork", fork_bin, env_for(args.fork, fork_extra)),
    ]

    print("machine : %s %s, %s cores" % (platform.system(), platform.machine(), os.cpu_count()))
    for name, b, _ in configs:
        d = args.stock if name == "stock" else args.fork
        print("%-8s: %s\n          %s" % (name, b, lean_version(b, d)))
    print("project : %s" % args.project)
    print("cache   : %s" % args.cache_dir)
    print("fork sw : %s" % " ".join("%s=%s" % kv for kv in sorted(fork_extra.items())))
    print()

    files = {}
    for c in args.cases:
        p = os.path.join(args.scratch, c + ".lean")
        with open(p, "w") as f:
            f.write(CASES[c])
        files[c] = p

    # Warm-up.  The fork needs two passes per closure.  Pass 1 writes the lazy-loading index
    # (a cache, written automatically).  Pass 2 is run with LEAN_TACTIC_INDEX_WRITE=1 and *builds* the
    # tactic index image, which is not a cache: nothing ever writes it at run time, so without
    # this deliberate pass no image would exist and the timed runs below would measure the
    # no-image path.  The image is keyed on the region list the lazy-loading index produces,
    # which is why pass 2 must follow pass 1 — an image built while that index was still cold
    # is keyed differently and would never be found again.  This is what a library distributor
    # does once (see ../../docs/switches.md, ../../docs on the shipped image); here we do it for you so the
    # measurement is of the configuration the package claims.  The first pass of a cold closure
    # can take ~30 s; that cost is paid once, ever.
    print("warm-up (fork: 2 passes per case, ~30 s each on a cold cache; "
          "pass 2 builds the tactic index image) …", flush=True)
    for p in (1, 2):
        for name, b, env in configs:
            if name == "stock" and p == 2:
                continue
            penv = env
            if p == 2 and name == "fork":
                penv = dict(env)
                penv["LEAN_TACTIC_INDEX_WRITE"] = "1"
            for c in args.cases:
                t = time.perf_counter()
                measure(b, [files[c]], penv)
                print("  pass %d %-6s %-16s %5.1f s" % (p, name, c, time.perf_counter() - t), flush=True)

    results = {c: {n: [] for n, _, _ in configs} for c in args.cases}
    print("\ntimed runs (interleaved) …", flush=True)
    for i in range(args.repeat):
        for c in args.cases:
            for name, b, env in configs:
                m = measure(b, [files[c]], env)
                if m["exit_code"] != 0:
                    die("%s exited %s on %s — rerun without redirecting output to diagnose"
                        % (name, m["exit_code"], c))
                results[c][name].append(m)
                print("  run %d %-6s %-16s %6.2f s  %6.2f GB" %
                      (i + 1, name, c, m["wall_s"], m["maxrss_bytes"] / 2**30), flush=True)

    def med(rs, k):
        return statistics.median(r[k] for r in rs)

    print("\n| case | config | wall s | user s | sys s | max RSS GB | minor faults |")
    print("|---|---|---|---|---|---|---|")
    for c in args.cases:
        for name, _, _ in configs:
            rs = results[c][name]
            print("| %s | %s | %.2f | %.2f | %.2f | %.2f | %s |" %
                  (c, name, med(rs, "wall_s"), med(rs, "user_s"), med(rs, "sys_s"),
                   med(rs, "maxrss_bytes") / 2**30, int(med(rs, "minflt"))))
    print()
    for c in args.cases:
        s, f = results[c]["stock"], results[c]["fork"]
        print("%-16s speed-up %.2fx wall, %.2fx max RSS  (medians of %d)" %
              (c, med(s, "wall_s") / med(f, "wall_s"),
               med(s, "maxrss_bytes") / med(f, "maxrss_bytes"), args.repeat))

    print("\nRead these numbers with ../../docs/warnings.md §'What the numbers mean' in hand:\n"
          "  * max RSS counts shared, memory-mapped library pages and depends on how much\n"
          "    free memory the machine has (Linux fault-around).  Quote it at the memory\n"
          "    size of the machine you care about.\n"
          "  * these are warm-cache numbers; cold numbers need a page-cache drop we cannot\n"
          "    do portably.")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"machine": platform.uname()._asdict(), "project": args.project,
                       "fork_switches": fork_extra, "results": results}, f, indent=1)
        print("\nraw runs written to " + args.json)


if __name__ == "__main__":
    main()
