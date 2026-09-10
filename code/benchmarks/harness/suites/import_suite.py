"""`import` suite: cost of importing nothing / Init / Lean / Batteries / Mathlib.Tactic / a mid
Mathlib module / all of Mathlib, measured with the bare `lean` binary (LEAN_PATH exported from
`lake env`, computed once) and once through `lake env lean` to quantify Lake's overhead.

Every case is run under several *root-file variants* (see suites.common.ROOT_VARIANTS):
  legacy : `import X`                       (no `module` header: private-level import of everything)
  module : `module` + `public import X`     (public level only)
  all    : `module` + `import all X`        (module root re-exposing the private level)
One extra run per case+variant with `--profile --stats` (+ an #eval that counts modules/constants)
gives phase times, `--stats` (mmap counts, bytes mapped, per-extension imported entries) and the
size of the imported environment."""
from __future__ import annotations

import argparse
from typing import Any

from leanenv import EVAL_SNIPPET
from results import describe_runs
from suites.common import ROOT_VARIANTS, Context, measure_lean, root_source, write_case

TRIVIAL = "theorem t (n : Nat) : n + 0 = n := by simp\n"

DEFAULT_CASES: list[tuple[str, list[str]]] = [
    ("none", []),
    ("Init", ["Init"]),
    ("Lean", ["Lean"]),
    ("Batteries", ["Batteries"]),
    ("Mathlib.Tactic", ["Mathlib.Tactic"]),
    ("Mathlib.Analysis.SpecialFunctions.Log.Basic", ["Mathlib.Analysis.SpecialFunctions.Log.Basic"]),
    ("Mathlib", ["Mathlib"]),
]
# `import Lean` is needed for the #eval that inspects the environment.
EVAL_OK = {"Lean", "Batteries", "Mathlib.Tactic", "Mathlib.Analysis.SpecialFunctions.Log.Basic", "Mathlib"}
HEADLINE_KEYS = ("wall_mean_s", "wall_min_s", "user_mean_s", "sys_mean_s", "maxrss_mean_bytes",
                 "minflt_mean", "majflt_mean", "modules", "constants", "imported_bytes", "profile_import_s")


def add_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repeat", type=int, default=3, help="timed repeats per case (default 3)")
    p.add_argument("--warmup", type=int, default=0, help="untimed warm-up runs per case (default 0)")
    p.add_argument("--cases", nargs="*", default=None,
                   help="subset of case names to run (default: all of %s)" % [c for c, _ in DEFAULT_CASES])
    p.add_argument("--variants", nargs="*", default=list(ROOT_VARIANTS), choices=list(ROOT_VARIANTS),
                   help="root-file variants to run (default: all of %s)" % list(ROOT_VARIANTS))
    p.add_argument("--no-lake", action="store_true", help="skip the `lake env lean` overhead run")
    p.add_argument("--lake-all-variants", action="store_true",
                   help="do the `lake env lean` run for every variant (default: legacy only)")
    p.add_argument("--no-stats", action="store_true", help="skip the --profile/--stats/#eval run")


def _case_summary(c: dict[str, Any]) -> dict[str, Any]:
    d = c["direct"]
    return {
        "wall_mean_s": round(d["wall_s"]["mean"], 3),
        "wall_min_s": round(d["wall_s"]["min"], 3),
        "user_mean_s": round(d["user_s"]["mean"], 3),
        "sys_mean_s": round(d["sys_s"]["mean"], 3),
        "maxrss_mean_bytes": int(d["maxrss_bytes"]["mean"]),
        "minflt_mean": int(d["minflt"]["mean"]),
        "majflt_mean": int(d["majflt"]["mean"]),
        "lake_overhead_wall_s": round(c["lake_overhead_wall_s"], 3) if "lake_overhead_wall_s" in c else None,
        "modules": c.get("env_counts", {}).get("modules"),
        "constants": c.get("env_counts", {}).get("constants_imported") or c.get("stats", {}).get("imported_consts"),
        "imported_bytes": c.get("stats", {}).get("imported_bytes"),
        "mmapped_module_parts": c.get("stats", {}).get("mmapped_module_parts"),
        "profile_import_s": c.get("profile_phases", {}).get("import"),
        "ok": all(r["ok"] for r in c["direct_runs"]),
    }


def run_case(ctx: Context, args: argparse.Namespace, name: str, imports: list[str], variant: str) -> dict[str, Any]:
    src = root_source(variant, imports) + TRIVIAL
    base = f"import_{name}_{variant}"
    f = write_case(ctx, base, src)
    case: dict[str, Any] = {"name": name, "root_variant": variant, "imports": imports, "file": str(f), "source": src}
    for i in range(args.warmup):
        measure_lean(ctx, f"{base}_warmup{i}", f, keep_samples=False)
    runs = [measure_lean(ctx, f"{base}_direct_r{i}", f) for i in range(args.repeat)]
    case["direct_runs"] = runs
    case["direct"] = describe_runs(runs)
    if not args.no_lake and (variant == "legacy" or args.lake_all_variants):
        lk = measure_lean(ctx, f"{base}_lake", f, via="lake")
        case["lake_env_run"] = lk
        case["lake_overhead_wall_s"] = lk["wall_s"] - case["direct"]["wall_s"]["mean"]
    if not args.no_stats:
        use_eval = name in EVAL_OK
        fs = write_case(ctx, f"{base}_stats", src + (EVAL_SNIPPET if use_eval else ""))
        st = measure_lean(ctx, f"{base}_stats", fs, extra=["--profile", "--stats"],
                          want_profile=True, want_stats=True, want_eval=use_eval, keep_samples=False)
        if use_eval and not st["eval"]:
            ctx.log("  (#eval snippet failed; retrying --stats without it)")
            st = measure_lean(ctx, f"{base}_stats_noeval", f, extra=["--profile", "--stats"],
                              want_profile=True, want_stats=True, keep_samples=False)
        case["stats_run"] = st
        case["profile_phases"] = st.get("profile", {}).get("phases", {})
        case["stats"] = st.get("stats", {})
        case["env_counts"] = dict(st.get("eval", {}))
        parts = case["stats"].get("imported_module_parts")
        if "modules" not in case["env_counts"] and variant == "legacy" and parts and parts % 5 == 0:
            # v4.33 legacy roots: `--stats` counts the 5 on-disk parts of every module (.olean,
            # .olean.server, .olean.private, .ir, .ir.sig); verified against #eval
            # header.moduleNames.size.  (Not 5x for `module` roots, so only derived for legacy.)
            case["env_counts"]["modules"] = parts // 5
            case["env_counts"]["modules_derived_from_stats"] = True
    return case


def run(ctx: Context, args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any]]:
    cases = DEFAULT_CASES if not args.cases else [c for c in DEFAULT_CASES if c[0] in set(args.cases)]
    if args.cases and len(cases) != len(args.cases):
        raise SystemExit(f"unknown case(s); known: {[c for c, _ in DEFAULT_CASES]}")
    out_cases: list[dict[str, Any]] = []
    for name, imports in cases:
        for variant in args.variants:
            if variant == "all" and not imports:
                continue  # `module` with no import line == the module variant
            ctx.log(f"== import case: {name} [{variant}]")
            out_cases.append(run_case(ctx, args, name, imports, variant))

    summary: dict[str, Any] = {"repeat": args.repeat, "variants": args.variants, "cases": {}}
    for c in out_cases:
        summary["cases"].setdefault(c["name"], {})[c["root_variant"]] = _case_summary(c)
    if out_cases:
        heavy = cases[-1][0]
        summary["headline_case"] = heavy
        for variant, cs in summary["cases"].get(heavy, {}).items():
            for k in HEADLINE_KEYS:
                summary[f"{variant}_{k}"] = cs.get(k)
            if variant == "legacy":  # back-compatible unprefixed scalars
                for k in HEADLINE_KEYS:
                    summary[k] = cs.get(k)
        summary["all_ok"] = all(cs["ok"] for v in summary["cases"].values() for cs in v.values())
    return {"cases": out_cases}, summary
