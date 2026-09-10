"""`mathlib-files` suite: re-elaborate selected Mathlib source files in place (their imports'
.olean files are already built, so this measures the file's own elaboration on top of import)."""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from meta import CASES_DIR
from results import describe_runs
from suites.common import Context, measure_lean, safe_label

DEFAULT_LIST = CASES_DIR / "mathlib-files.txt"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--list", type=Path, default=DEFAULT_LIST, help="file with one Mathlib path per line")
    p.add_argument("--files", nargs="*", default=None, help="explicit paths (relative to project); overrides --list")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--via", choices=["direct", "lake"], default="direct",
                   help="direct = toolchain lean + LEAN_PATH from `lake env` (default); lake = `lake env lean`")
    p.add_argument("--limit", type=int, default=None, help="only the first N files of the list")


def read_list(path: Path) -> list[str]:
    out = []
    for line in path.read_text().splitlines():
        s = line.split("#", 1)[0].strip()
        if s:
            out.append(s)
    return out


def run(ctx: Context, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    paths = args.files if args.files else read_list(args.list)
    if args.limit:
        paths = paths[: args.limit]
    out: list[dict[str, Any]] = []
    for rel in paths:
        f = ctx.project / rel
        if not f.exists():
            ctx.log(f"== SKIP (missing): {rel}")
            out.append({"file": rel, "missing": True, "ok": False})
            continue
        ctx.log(f"== mathlib file: {rel}")
        runs = [measure_lean(ctx, f"{safe_label(rel)}_r{i}", f, via=args.via, extra=["--profile"],
                             want_profile=True) for i in range(args.repeat)]
        st = describe_runs(runs)
        prof = runs[0].get("profile", {})
        src = f.read_text()
        out.append({
            "file": rel, "lines": src.count("\n"), "bytes": len(src.encode()),
            "runs": runs, "stats": st,
            "profile_phases": prof.get("phases", {}),
            "profile_import_s": prof.get("import_s"),
            "profile_elab_s": prof.get("elab_s"),
            "slowest_items": prof.get("slowest_items", []),
            "ok": all(r["ok"] for r in runs),
        })
    done = [e for e in out if not e.get("missing")]
    summary = {
        "repeat": args.repeat, "via": args.via, "n_files": len(out),
        "all_ok": all(e["ok"] for e in done) and len(done) == len(out),
        "wall_total_s": round(sum(e["stats"]["wall_s"]["mean"] for e in done), 3),
        "user_total_s": round(sum(e["stats"]["user_s"]["mean"] for e in done), 3),
        "profile_elab_total_s": round(sum(e["profile_elab_s"] or 0 for e in done), 3),
        "files": {e["file"]: ({"missing": True} if e.get("missing") else {
            "wall_s": round(e["stats"]["wall_s"]["mean"], 3),
            "user_s": round(e["stats"]["user_s"]["mean"], 3),
            "maxrss_bytes": int(e["stats"]["maxrss_bytes"]["mean"]),
            "profile_import_s": e["profile_import_s"],
            "profile_elab_s": round(e["profile_elab_s"], 3) if e["profile_elab_s"] is not None else None,
            "ok": e["ok"]}) for e in out},
    }
    return {"files": out}, summary
