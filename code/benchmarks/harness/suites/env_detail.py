"""`env-detail` suite: one `import Mathlib` run with fine-grained (50 ms) RSS sampling to see
the shape of memory growth: peak, time-to-peak, RSS at exit, plus the usual rusage numbers."""
from __future__ import annotations

import argparse
from typing import Any

from suites.common import ROOT_VARIANTS, Context, measure_lean, root_source, write_case
from suites.import_suite import TRIVIAL


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--import", dest="imports", nargs="*", default=["Mathlib"], help="module(s) to import")
    p.add_argument("--interval", type=float, default=0.05, help="sampling interval in seconds (default 0.05)")
    p.add_argument("--profile", action="store_true", help="also pass --profile to lean")
    p.add_argument("--root-variant", default="legacy", choices=list(ROOT_VARIANTS),
                   help="root-file variant: legacy `import X` (default), module `public import X`, or `import all X`")


def run(ctx: Context, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    name = "+".join(args.imports) or "none"
    f = write_case(ctx, f"envdetail_{name}_{args.root_variant}", root_source(args.root_variant, args.imports) + TRIVIAL)
    ctx.log(f"== env-detail: import {name} [{args.root_variant}] @ {args.interval*1000:.0f} ms")
    d = measure_lean(ctx, f"envdetail_{name}_{args.root_variant}", f, sample_interval_s=args.interval, keep_samples=True,
                     extra=["--profile"] if args.profile else None, want_profile=args.profile)
    samples = d["samples"]
    # Growth milestones: first time the tree RSS crosses fractions of the peak.
    peak = d["tree_rss_peak_bytes"] or 1
    milestones = {}
    for frac in (0.25, 0.5, 0.75, 0.9, 0.99):
        for t, rss, *_ in samples:
            if rss >= frac * peak:
                milestones[f"t_{int(frac*100)}pct_s"] = t
                break
    body = {"imports": args.imports, "root_variant": args.root_variant, "run": d, "milestones": milestones}
    summary = {
        "imports": name, "root_variant": args.root_variant, "interval_s": args.interval, "n_samples": d["n_samples"],
        "wall_s": round(d["wall_s"], 3), "user_s": round(d["user_s"], 3), "sys_s": round(d["sys_s"], 3),
        "maxrss_bytes": d["maxrss_bytes"], "tree_rss_peak_bytes": d["tree_rss_peak_bytes"],
        "time_to_peak_s": round(d["tree_rss_peak_t"], 3), "rss_at_exit_bytes": d["tree_rss_exit_bytes"],
        "minflt": d["minflt"], "majflt": d["majflt"], **milestones, "ok": d["ok"],
    }
    ctx.log(f"  peak {peak/2**30:.2f} GiB at t={d['tree_rss_peak_t']:.2f}s; at exit {d['tree_rss_exit_bytes']/2**30:.2f} GiB; "
            f"{d['n_samples']} samples")
    return body, summary
