"""Shared plumbing for suites: the run Context and helpers to measure `lean` on a file."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from leanenv import LeanEnv, parse_eval, parse_profile, parse_stats
from measure import Measurement, read_text, run_measured
from meta import SCRATCH_DIR
from results import raw_dir, strip_measurement

_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class Context:
    project: Path
    le: LeanEnv
    run_id: str
    timeout_s: float
    sample_interval_s: float
    keep_samples: bool
    text_limit: int
    verbose: bool = True

    @property
    def raw(self) -> Path:
        return raw_dir(self.run_id)

    @property
    def scratch(self) -> Path:
        d = SCRATCH_DIR / self.run_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg, flush=True)


def safe_label(s: str) -> str:
    return _SAFE.sub("_", s).strip("_") or "x"


def import_header(imports: list[str] | str | None) -> str:
    """Lean source header for a list of module names (None/[] => no import line)."""
    if not imports:
        return ""
    if isinstance(imports, str):
        imports = [imports]
    return "".join(f"import {m}\n" for m in imports)


# Root-file variants (Lean module system).  A root file without a `module` header imports every
# transitive module at the *private* level (.olean + .olean.private + ...); a root starting with
# `module` and `public import X` maps only the public level.  `import all X` re-exposes the
# private level from a `module` root.
ROOT_VARIANTS = ("legacy", "module", "all")


def root_source(variant: str, imports: list[str] | None) -> str:
    """Header lines for *variant* importing *imports* (imports may be empty)."""
    imports = list(imports or [])
    if variant == "legacy":
        return import_header(imports)
    if variant == "module":
        return "module\n" + "".join(f"public import {m}\n" for m in imports)
    if variant == "all":
        return "module\n" + "".join(f"import all {m}\n" for m in imports)
    raise ValueError(f"unknown root variant {variant!r}; known: {ROOT_VARIANTS}")


def split_imports(src: str) -> tuple[list[str], str, bool]:
    """Split a Lean source into (imports, body, had_module_header).  Only leading `import`,
    `public import`, `import all`, blank and comment lines are treated as the header."""
    lines = src.splitlines(keepends=True)
    mods: list[str] = []
    had_module = False
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        if t == "module" or t.startswith("module "):
            had_module = True
        elif t.startswith("public import "):
            mods.append(t[len("public import "):].strip())
        elif t.startswith("import all "):
            mods.append(t[len("import all "):].strip())
        elif t.startswith("import "):
            mods.append(t[len("import "):].strip())
        elif t == "" or t.startswith("--"):
            pass  # blank lines / line comments between header lines; `/-` docstrings belong to the body
        else:
            break
        i += 1
    return mods, "".join(lines[i:]), had_module


def rewrite_root(src: str, variant: str) -> str:
    """Return *src* with its import header rewritten for *variant*."""
    mods, body, _ = split_imports(src)
    return root_source(variant, mods) + body


def write_case(ctx: Context, name: str, source: str) -> Path:
    p = ctx.scratch / f"{safe_label(name)}.lean"
    p.write_text(source)
    return p


def lean_cmd(ctx: Context, file: Path, *, via: str = "direct", extra: list[str] | None = None) -> list[str]:
    """Command line to elaborate *file*: via='direct' uses the toolchain lean binary with
    LEAN_PATH exported from `lake env`; via='lake' goes through `lake env lean`."""
    extra = extra or []
    if via == "direct":
        return [ctx.le.lean_bin, *extra, str(file)]
    if via == "lake":
        return [ctx.le.lake_bin, "env", "lean", *extra, str(file)]
    raise ValueError(via)


def measure_lean(ctx: Context, label: str, file: Path, *, via: str = "direct",
                 extra: list[str] | None = None, cwd: Path | None = None,
                 sample_interval_s: float | None = None, keep_samples: bool | None = None,
                 want_profile: bool = False, want_stats: bool = False, want_eval: bool = False,
                 ) -> dict[str, Any]:
    """Run lean on *file* once; return a result-file friendly dict (Measurement + parsed extras)."""
    label = safe_label(label)
    cmd = lean_cmd(ctx, file, via=via, extra=extra)
    m: Measurement = run_measured(
        cmd, cwd=cwd or ctx.project, env=ctx.le.env, timeout_s=ctx.timeout_s,
        sample_interval_s=sample_interval_s if sample_interval_s is not None else ctx.sample_interval_s,
        keep_samples=ctx.keep_samples if keep_samples is None else keep_samples,
        stdout_path=ctx.raw / f"{label}.stdout.log", stderr_path=ctx.raw / f"{label}.stderr.log",
        text_limit=ctx.text_limit,
    )
    d = strip_measurement(m.to_dict())
    d["label"] = label
    d["via"] = via
    d["file"] = str(file)
    full = read_text(m.stdout_path) + "\n" + read_text(m.stderr_path)
    if want_profile:
        d["profile"] = parse_profile(full)
    if want_stats:
        d["stats"] = parse_stats(full)
    if want_eval:
        d["eval"] = parse_eval(full)
    d["ok"] = (m.exit_code == 0) and not m.timed_out
    ctx.log(f"  {label:60s} wall={m.wall_s:7.2f}s user={m.user_s:6.2f}s sys={m.sys_s:6.2f}s "
            f"rss={m.maxrss_bytes/2**30:5.2f}GiB faults={m.minflt:>7d}/{m.majflt} exit={m.exit_code}"
            + ("  TIMEOUT" if m.timed_out else "") + ("" if d["ok"] else "  **FAILED**"))
    return d


def headline(d: dict[str, Any]) -> dict[str, Any]:
    """A few headline numbers of a single measurement dict, for summaries."""
    return {k: d.get(k) for k in ("wall_s", "user_s", "sys_s", "maxrss_bytes", "tree_rss_peak_bytes",
                                  "minflt", "majflt", "exit_code")}
