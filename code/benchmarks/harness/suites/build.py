"""`build` suite: `lake build [target]` of a project with process-tree RSS sampling."""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path
from typing import Any

from measure import read_text, run_measured
from results import strip_measurement
from suites.common import Context


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--target", nargs="*", default=None, help="lake build targets (default: package default)")
    p.add_argument("--clean", action="store_true",
                   help="delete the PROJECT's own .lake/build first (never the packages')")
    p.add_argument("--jobs", type=int, default=None, help="lake -j N")
    p.add_argument("--lake-args", nargs="*", default=None, help="extra args appended to `lake build`")


def clean_project_build(project: Path, log) -> dict[str, Any]:
    build = (project / ".lake" / "build").resolve()
    proj = project.resolve()
    info = {"path": str(build), "existed": build.exists(), "deleted": False}
    if not build.exists():
        return info
    # Safety: must be exactly <project>/.lake/build and inside the project.
    if build.parent.name != ".lake" or build.name != "build" or build.parent.parent != proj:
        raise SystemExit(f"refusing to delete unexpected path {build}")
    log(f"  deleting {build}")
    shutil.rmtree(build)
    info["deleted"] = True
    return info


def run(ctx: Context, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    body: dict[str, Any] = {}
    if args.clean:
        body["clean"] = clean_project_build(ctx.project, ctx.log)
    cmd = [ctx.le.lake_bin, "build"]
    if args.jobs:
        cmd += ["-j", str(args.jobs)]
    if args.target:
        cmd += list(args.target)
    if args.lake_args:
        cmd += list(args.lake_args)
    ctx.log(f"== build: {' '.join(cmd)}  (cwd {ctx.project})")
    m = run_measured(cmd, cwd=ctx.project, env=ctx.le.env, timeout_s=ctx.timeout_s,
                     sample_interval_s=ctx.sample_interval_s, keep_samples=True,
                     stdout_path=ctx.raw / "lake_build.stdout.log",
                     stderr_path=ctx.raw / "lake_build.stderr.log", text_limit=ctx.text_limit)
    d = strip_measurement(m.to_dict())
    full = read_text(m.stdout_path) + read_text(m.stderr_path)
    jobs = re.search(r"Build completed successfully(?: \((\d+) jobs?\))?", full)
    d["lake_reported_jobs"] = int(jobs.group(1)) if jobs and jobs.group(1) else None
    d["lake_success_line"] = bool(jobs)
    d["n_built_lines"] = len(re.findall(r"^\s*✔\s*\[\d+/\d+\]", full, re.M))
    d["n_error_lines"] = len(re.findall(r"^\s*✖", full, re.M))
    ctx.log(f"  wall={m.wall_s:.2f}s user={m.user_s:.2f}s sys={m.sys_s:.2f}s tree_peak={m.tree_rss_peak_bytes/2**30:.2f}GiB "
            f"lean_procs={m.lean_procs_seen} max_conc_lean={m.max_concurrent_lean} exit={m.exit_code}")
    body["build"] = d
    summary = {
        "cmd": cmd, "wall_s": round(m.wall_s, 3), "user_s": round(m.user_s, 3), "sys_s": round(m.sys_s, 3),
        "cpu_util": round(m.cpu_util, 2), "maxrss_bytes": m.maxrss_bytes,
        "tree_rss_peak_bytes": m.tree_rss_peak_bytes, "lean_procs_seen": m.lean_procs_seen,
        "max_concurrent_lean": m.max_concurrent_lean, "procs_seen": m.procs_seen,
        "lake_reported_jobs": d["lake_reported_jobs"], "exit_code": m.exit_code,
        "timed_out": m.timed_out, "clean": args.clean,
    }
    return body, summary
