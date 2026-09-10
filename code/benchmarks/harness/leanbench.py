#!/usr/bin/env python3
"""leanbench — benchmark harness for Lean 4 + Mathlib toolchain performance.

Usage: python3 bench/harness/leanbench.py [global options] <command> [command options]

Commands: import, textbook, mathlib-files, build, disk, env-detail, run <suite>, summarize FILE, list
Stdlib only.  Results go to bench/results/<UTC-timestamp>_<suite>.json (+ index.json, raw logs).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import leanenv  # noqa: E402
from meta import DEFAULT_PROJECT, RESULTS_DIR, collect_meta  # noqa: E402
from results import load_index, make_run_id, write_result  # noqa: E402
from suites import build, disk, env_detail, import_suite, mathlib_files, textbook  # noqa: E402
from suites.common import Context  # noqa: E402
from summarize import summarize_file, summarize_index  # noqa: E402

SUITES = {
    "import": import_suite, "textbook": textbook, "mathlib-files": mathlib_files,
    "build": build, "disk": disk, "env-detail": env_detail,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="leanbench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", type=Path, default=DEFAULT_PROJECT,
                   help=f"Lake project dir providing the environment (default {DEFAULT_PROJECT}; env LEANBENCH_PROJECT)")
    p.add_argument("--tag", default=None, help="label stored in meta.tag and the index")
    p.add_argument("--notes", default=None, help="free-form note stored in meta.notes")
    p.add_argument("--timeout", type=float, default=1800.0, help="per-command timeout in seconds (default 1800)")
    p.add_argument("--sample-interval", type=float, default=0.2, help="process-tree RSS sampling interval (s), default 0.2")
    p.add_argument("--no-samples", action="store_true", help="do not store RSS time series in result files")
    p.add_argument("--text-limit", type=int, default=4000, help="max chars of stdout/stderr kept inline")
    p.add_argument("-q", "--quiet", action="store_true")
    sub = p.add_subparsers(dest="command", required=True)
    for name, mod in SUITES.items():
        sp = sub.add_parser(name, help=(mod.__doc__ or "").strip().splitlines()[0])
        mod.add_args(sp)
    runp = sub.add_parser("run", help="alias: run <suite> [suite options]")
    runp.add_argument("suite", choices=list(SUITES))
    runp.add_argument("rest", nargs=argparse.REMAINDER)
    sp = sub.add_parser("summarize", help="print a markdown summary of a result file")
    sp.add_argument("file", type=Path)
    sub.add_parser("list", help="print index.json as a markdown table")
    return p


def run_suite(name: str, args: argparse.Namespace, argv: list[str]) -> Path:
    mod = SUITES[name]
    project = args.project.expanduser().resolve()
    if not project.is_dir():
        raise SystemExit(f"project dir not found: {project}")
    le = leanenv.discover(project)
    lean_info = leanenv.toolchain_info(le)
    run_id = make_run_id(name)
    ctx = Context(project=project, le=le, run_id=run_id, timeout_s=args.timeout,
                  sample_interval_s=args.sample_interval, keep_samples=not args.no_samples,
                  text_limit=args.text_limit, verbose=not args.quiet)
    ctx.log(f"leanbench {name}: run_id={run_id} project={project} lean={lean_info.get('version')}")
    meta = collect_meta(suite=name, project=project, lean_info=lean_info, tag=args.tag, notes=args.notes, argv=argv)
    body, summary = mod.run(ctx, args)
    meta["loadavg_after"] = dict(zip(("1m", "5m", "15m"), __import__("os").getloadavg()))
    path = write_result(run_id, name, meta, body, summary)
    ctx.log(f"wrote {path}")
    ctx.log(summarize_file(path))
    return path


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        # re-parse as `<globals> <suite> <rest>`
        i = argv.index("run")
        return main(argv[:i] + [args.suite] + args.rest)
    if args.command == "summarize":
        print(summarize_file(args.file))
        return 0
    if args.command == "list":
        entries = load_index()
        print(summarize_index(entries) if entries else f"(no results yet in {RESULTS_DIR})")
        return 0
    run_suite(args.command, args, argv)
    return 0


if __name__ == "__main__":
    sys.exit(main())
