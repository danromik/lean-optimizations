"""`disk` suite: on-disk footprint of a project's .lake tree and of the active toolchain."""
from __future__ import annotations

import argparse
import heapq
import os
from pathlib import Path
from typing import Any

from suites.common import Context

# Order matters: multi-suffix olean parts must be tested before ".olean".
EXT_CLASSES = [".olean.server", ".olean.private", ".olean", ".ilean", ".ir.sig", ".ir",
               ".c", ".o", ".a", ".dylib", ".so", ".hash", ".trace", ".json", ".lean", ".ltar", ".export", ".bc", ".h"]


# Extension classes whose sizes are also reported as scalars in the summary / index.
SUMMARY_EXTS = (".olean", ".olean.private", ".olean.server", ".ilean", ".ir", ".ir.sig", ".c")


def _ext_key(ext: str) -> str:
    return ext.strip(".").replace(".", "_")


def classify(name: str) -> str:
    for ext in EXT_CLASSES:
        if name.endswith(ext):
            return ext
    return "other"


class Walker:
    """Fast os.scandir walk accumulating bytes/counts by extension class and by top-level subdir."""

    def __init__(self, top_k: int = 25):
        self.top_k = top_k
        self.heap: list[tuple[int, str]] = []

    def walk(self, root: Path, *, by_subdir_depth: int = 1) -> dict[str, Any]:
        root = Path(root)
        by_ext: dict[str, dict[str, int]] = {}
        by_sub: dict[str, dict[str, int]] = {}
        total = {"bytes": 0, "files": 0, "dirs": 0}
        if not root.exists():
            return {"root": str(root), "exists": False}
        stack = [(root, ())]
        while stack:
            d, rel = stack.pop()
            try:
                it = os.scandir(d)
            except OSError:
                continue
            with it:
                for e in it:
                    try:
                        if e.is_dir(follow_symlinks=False):
                            total["dirs"] += 1
                            stack.append((Path(e.path), rel + (e.name,)))
                            continue
                        if not e.is_file(follow_symlinks=False):
                            continue
                        size = e.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
                    total["bytes"] += size
                    total["files"] += 1
                    cls = classify(e.name)
                    b = by_ext.setdefault(cls, {"bytes": 0, "files": 0})
                    b["bytes"] += size
                    b["files"] += 1
                    sub = "/".join(rel[:by_subdir_depth]) if rel else "."
                    s = by_sub.setdefault(sub, {"bytes": 0, "files": 0})
                    s["bytes"] += size
                    s["files"] += 1
                    if cls == ".olean" and self.top_k > 0:
                        item = (size, e.path)
                        if len(self.heap) < self.top_k:
                            heapq.heappush(self.heap, item)
                        elif item > self.heap[0]:
                            heapq.heapreplace(self.heap, item)
        return {
            "root": str(root), "exists": True, "total": total,
            "by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1]["bytes"])),
            "by_subdir": dict(sorted(by_sub.items(), key=lambda kv: -kv[1]["bytes"])),
        }

    def largest_oleans(self) -> list[dict[str, Any]]:
        return [{"bytes": s, "path": p} for s, p in sorted(self.heap, reverse=True)]


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--top", type=int, default=25, help="how many largest .olean files to list")
    p.add_argument("--no-toolchain", action="store_true")
    p.add_argument("--no-project", action="store_true")


def run(ctx: Context, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    body: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    if not args.no_project:
        w = Walker(args.top)
        proj = ctx.project
        body["project"] = {
            "dir": str(proj),
            "sources": _sources(proj),
            "lake_build": w.walk(proj / ".lake" / "build", by_subdir_depth=1),
            "lake_packages": w.walk(proj / ".lake" / "packages", by_subdir_depth=1),
            "lake_total": _dir_total(proj / ".lake"),
        }
        pk = body["project"]["lake_packages"]
        if pk.get("exists"):
            w0 = Walker(0)  # overlapping sub-walk: do not feed the largest-olean heap twice
            body["project"]["packages"] = {
                name: w0.walk(proj / ".lake" / "packages" / name / ".lake" / "build", by_subdir_depth=1)
                for name in sorted(pk["by_subdir"]) if name != "."
            }
        body["project"]["largest_oleans"] = w.largest_oleans()
        summary["project_lake_build_bytes"] = body["project"]["lake_build"].get("total", {}).get("bytes")
        summary["project_lake_packages_bytes"] = pk.get("total", {}).get("bytes")
        summary["project_lake_total_bytes"] = body["project"]["lake_total"]["bytes"]
        summary["project_olean_bytes"] = sum(
            v.get("by_ext", {}).get(".olean", {}).get("bytes", 0)
            for v in (body["project"]["lake_build"], pk))
        summary["project_olean_files"] = sum(
            v.get("by_ext", {}).get(".olean", {}).get("files", 0)
            for v in (body["project"]["lake_build"], pk))
        for ext in SUMMARY_EXTS:
            summary[f"project_build_{_ext_key(ext)}_bytes"] = body["project"]["lake_build"].get("by_ext", {}).get(ext, {}).get("bytes", 0)
            summary[f"project_packages_{_ext_key(ext)}_bytes"] = pk.get("by_ext", {}).get(ext, {}).get("bytes", 0)
        summary["source_lean_files"] = body["project"]["sources"]["files"]
        summary["source_lean_bytes"] = body["project"]["sources"]["bytes"]
        ctx.log(f"  project .lake total {summary['project_lake_total_bytes']/2**30:.2f} GiB "
                f"(build {(summary['project_lake_build_bytes'] or 0)/2**30:.2f} GiB, "
                f"packages {(summary['project_lake_packages_bytes'] or 0)/2**30:.2f} GiB)")
    if not args.no_toolchain:
        w2 = Walker(args.top)
        w0 = Walker(0)  # sub-walks overlap with `all`; only `all` feeds the largest-olean heap
        tc = Path(ctx.le.sysroot)
        body["toolchain"] = {
            "dir": str(tc),
            "all": w2.walk(tc, by_subdir_depth=1),
            "lib": w0.walk(tc / "lib", by_subdir_depth=1),
            "lib_lean": w0.walk(tc / "lib" / "lean", by_subdir_depth=1),
            "bin": w0.walk(tc / "bin", by_subdir_depth=1),
            "include": w0.walk(tc / "include", by_subdir_depth=1),
            "share": w0.walk(tc / "share", by_subdir_depth=1),
            "largest_oleans": w2.largest_oleans(),
        }
        summary["toolchain_bytes"] = body["toolchain"]["all"]["total"]["bytes"]
        summary["toolchain_files"] = body["toolchain"]["all"]["total"]["files"]
        summary["toolchain_olean_bytes"] = body["toolchain"]["all"]["by_ext"].get(".olean", {}).get("bytes")
        summary["toolchain_olean_files"] = body["toolchain"]["all"]["by_ext"].get(".olean", {}).get("files")
        for ext in SUMMARY_EXTS:
            summary[f"toolchain_{_ext_key(ext)}_bytes"] = body["toolchain"]["all"]["by_ext"].get(ext, {}).get("bytes", 0)
        ctx.log(f"  toolchain {summary['toolchain_bytes']/2**30:.2f} GiB in {summary['toolchain_files']} files")
    return body, summary


def _dir_total(root: Path) -> dict[str, int]:
    w = Walker(0)
    r = w.walk(root)
    return r.get("total", {"bytes": 0, "files": 0, "dirs": 0})


def _sources(proj: Path) -> dict[str, int]:
    """Count .lean sources outside .lake."""
    files = 0
    nbytes = 0
    lines = 0
    stack = [proj]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            continue
        with it:
            for e in it:
                if e.is_dir(follow_symlinks=False):
                    if e.name not in (".lake", ".git"):
                        stack.append(Path(e.path))
                elif e.is_file(follow_symlinks=False) and e.name.endswith(".lean"):
                    files += 1
                    try:
                        data = Path(e.path).read_bytes()
                    except OSError:
                        continue
                    nbytes += len(data)
                    lines += data.count(b"\n")
    return {"files": files, "bytes": nbytes, "lines": lines}
