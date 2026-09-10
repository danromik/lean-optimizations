"""`textbook` suite: elaborate every .lean file in bench/cases/textbook/ (undergraduate-level
statements, `import Mathlib`).  Each file is run under the requested root-file variants
(default: legacy `import Mathlib` and `module` + `public import Mathlib`; the harness rewrites
the header, see suites.common.rewrite_root).  Elaboration cost is estimated four ways:
  * elab_wall_est_s = wall - wall of an import-only baseline with the same import header and
    variant (baseline measured in the same run, same repeat count);
  * elab_user_est_s = user CPU - baseline user CPU (less noisy than wall on macOS);
  * profile_elab_s  = sum of `lean --profile` cumulative phases minus import/initialization;
  * profile_elab_net_s = the same, additionally netted phase-by-phase against the baseline's
    phases (removes e.g. the ~0.6 s of "interpretation" that `import Mathlib` itself costs).
TODO(--minimal-imports): variant that rewrites `import Mathlib` to the minimal set of modules
actually needed by each file (e.g. via `lake exe mkAllImports`-style analysis or a hand-curated
header per case) to quantify how much of the cost is the monolithic import."""
from __future__ import annotations

import argparse
import statistics
from pathlib import Path
from typing import Any

from meta import CASES_DIR
from results import describe_runs
from suites.common import (ROOT_VARIANTS, Context, measure_lean, root_source, safe_label, split_imports,
                           write_case)

DEFAULT_CASES_DIR = CASES_DIR / "textbook"


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repeat", type=int, default=1, help="timed repeats per file (default 1)")
    p.add_argument("--cases-dir", type=Path, default=DEFAULT_CASES_DIR)
    p.add_argument("--files", nargs="*", default=None, help="subset of case file names (basenames)")
    p.add_argument("--root-variants", nargs="*", default=["legacy", "module"], choices=list(ROOT_VARIANTS),
                   help="root-file variants to run every case under (default: legacy module)")
    p.add_argument("--no-profile", action="store_true", help="run timed runs without --profile")
    p.add_argument("--minimal-imports", action="store_true",
                   help="TODO: not implemented yet (see module docstring)")


def parse_imports(src: str) -> list[str]:
    return split_imports(src)[0]


def run(ctx: Context, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    if args.minimal_imports:
        raise SystemExit("--minimal-imports is not implemented yet (TODO)")
    files = sorted(args.cases_dir.glob("*.lean"))
    if args.files:
        want = set(args.files)
        files = [f for f in files if f.name in want]
    if not files:
        raise SystemExit(f"no case files in {args.cases_dir}")
    extra = [] if args.no_profile else ["--profile"]
    sources = {f: f.read_text() for f in files}
    headers: dict[tuple[str, ...], list[str]] = {}
    for f in files:
        mods = parse_imports(sources[f])
        headers.setdefault(tuple(mods), mods)

    baselines: list[dict[str, Any]] = []
    out_files: list[dict[str, Any]] = []
    for variant in args.root_variants:
        base_by_hdr: dict[tuple[str, ...], dict[str, Any]] = {}
        for key, mods in headers.items():
            name = "+".join(mods) or "none"
            ctx.log(f"== baseline for header [{name}] [{variant}]")
            bf = write_case(ctx, f"baseline_{name}_{variant}", root_source(variant, mods))
            runs = [measure_lean(ctx, f"baseline_{name}_{variant}_r{i}", bf, extra=extra,
                                 want_profile=not args.no_profile) for i in range(args.repeat)]
            b = {"imports": mods, "root_variant": variant, "runs": runs, "stats": describe_runs(runs),
                 "profile_phases": runs[0].get("profile", {}).get("phases", {}) if runs else {}}
            base_by_hdr[key] = b
            baselines.append(b)

        for f in files:
            src = sources[f]
            mods, body, _ = split_imports(src)
            b = base_by_hdr[tuple(mods)]
            base = b["stats"]
            ctx.log(f"== textbook: {f.name} [{variant}]")
            if variant == "legacy":
                target = f
            else:
                target = write_case(ctx, f"{f.stem}_{variant}", root_source(variant, mods) + body)
            runs = [measure_lean(ctx, f"{safe_label(f.stem)}_{variant}_r{i}", target, extra=extra,
                                 want_profile=not args.no_profile) for i in range(args.repeat)]
            st = describe_runs(runs)
            prof = runs[0].get("profile", {}) if runs else {}
            phases = prof.get("phases", {})
            base_ph = b["profile_phases"]
            delta = {k: v - base_ph.get(k, 0.0) for k, v in phases.items()}
            net = sum(v for k, v in delta.items() if k not in ("import", "initialization"))
            out_files.append({
                "file": str(f),
                "name": f.name,
                "root_variant": variant,
                "imports": mods,
                "lines": src.count("\n"),
                "runs": runs,
                "stats": st,
                "elab_wall_est_s": st["wall_s"]["mean"] - base["wall_s"]["mean"],
                "elab_user_est_s": st["user_s"]["mean"] - base["user_s"]["mean"],
                "profile_phases": phases,
                "profile_phases_delta": delta,
                "profile_elab_s": prof.get("elab_s"),
                "profile_elab_net_s": net if phases else None,
                "profile_import_s": prof.get("import_s"),
                "slowest_items": prof.get("slowest_items", []),
                "ok": all(r["ok"] for r in runs),
            })

    summary: dict[str, Any] = {
        "repeat": args.repeat,
        "root_variants": args.root_variants,
        "n_files": len(files),
        "all_ok": all(e["ok"] for e in out_files),
        "baselines": {f'{"+".join(b["imports"]) or "none"}@{b["root_variant"]}': {
            "wall_mean_s": round(b["stats"]["wall_s"]["mean"], 3),
            "user_mean_s": round(b["stats"]["user_s"]["mean"], 3),
            "sys_mean_s": round(b["stats"]["sys_s"]["mean"], 3),
            "maxrss_mean_bytes": int(b["stats"]["maxrss_bytes"]["mean"])} for b in baselines},
        "files": {},
    }
    for e in out_files:
        summary["files"].setdefault(e["name"], {})[e["root_variant"]] = {
            "wall_s": round(e["stats"]["wall_s"]["mean"], 3),
            "user_s": round(e["stats"]["user_s"]["mean"], 3),
            "maxrss_bytes": int(e["stats"]["maxrss_bytes"]["mean"]),
            "elab_wall_est_s": round(e["elab_wall_est_s"], 3),
            "elab_user_est_s": round(e["elab_user_est_s"], 3),
            "profile_elab_s": round(e["profile_elab_s"], 4) if e["profile_elab_s"] is not None else None,
            "profile_elab_net_s": round(e["profile_elab_net_s"], 4) if e["profile_elab_net_s"] is not None else None,
            "ok": e["ok"]}
    for variant in args.root_variants:
        es = [e for e in out_files if e["root_variant"] == variant]
        nets = [e["profile_elab_net_s"] for e in es if e["profile_elab_net_s"] is not None]
        if es:
            summary[f"{variant}_wall_mean_s"] = round(statistics.fmean(e["stats"]["wall_s"]["mean"] for e in es), 3)
            summary[f"{variant}_user_mean_s"] = round(statistics.fmean(e["stats"]["user_s"]["mean"] for e in es), 3)
            summary[f"{variant}_maxrss_mean_bytes"] = int(statistics.fmean(e["stats"]["maxrss_bytes"]["mean"] for e in es))
            summary[f"{variant}_profile_elab_net_total_s"] = round(sum(nets), 3) if nets else None
            summary[f"{variant}_profile_elab_net_median_s"] = round(statistics.median(nets), 3) if nets else None
            summary[f"{variant}_all_ok"] = all(e["ok"] for e in es)
    return {"baselines": baselines, "files": out_files}, summary
