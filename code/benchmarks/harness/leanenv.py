"""Lean/Lake environment discovery and parsers for `lean --profile` / `--stats` output."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_VERSION_RE = re.compile(r"Lean \(version ([^,]+), ([^,]+), commit ([0-9a-f]+), (\w+)\)")
_UNITS = {"ns": 1e-9, "us": 1e-6, "μs": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "min": 60.0, "h": 3600.0}
_TIME_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+(?:e[-+]?\d+)?)\s*(ns|us|μs|µs|ms|s|min|h)\s*$")


@dataclass
class LeanEnv:
    project: Path
    lake_vars: dict[str, str]            # everything `lake env` prints
    lean_bin: str                        # absolute path of the toolchain's lean
    lake_bin: str
    lean_path: str                       # LEAN_PATH as computed by lake
    env: dict[str, str] = field(default_factory=dict)  # os.environ + lake vars

    @property
    def sysroot(self) -> str:
        return self.lake_vars.get("LEAN_SYSROOT", str(Path(self.lean_bin).parent.parent))


def discover(project: Path) -> LeanEnv:
    """Run `lake env` once in *project* and remember the variables it sets."""
    vars_: dict[str, str] = {}
    if os.environ.get("LEANBENCH_LEAN_PATH"):
        # Pre-computed environment (e.g. a container whose Mathlib tree is read-only for the
        # measuring user, like MathBB's baked /leanpath): skip `lake env` entirely.
        lean = shutil.which("lean") or "lean"
        vars_["LEAN_PATH"] = os.environ["LEANBENCH_LEAN_PATH"]
        vars_["LEAN"] = str(Path(lean).resolve())
        vars_["LAKE"] = str(Path(shutil.which("lake") or "lake").resolve())
        vars_["LEAN_SYSROOT"] = str(Path(lean).resolve().parent.parent)
    else:
        r = subprocess.run(["lake", "env"], cwd=project, capture_output=True, text=True, check=False)
        if r.returncode != 0:
            raise SystemExit(f"`lake env` failed in {project}:\n{r.stderr}")
        for line in r.stdout.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if v != "":  # `lake env` prints unset vars as KEY= ; exporting "" would break e.g. LEAN_CC
                    vars_[k] = v
    # LEANBENCH_LEAN overrides the toolchain's lean (e.g. a patched fork binary, see docs/fork/);
    # LEAN_PATH etc. still come from the project's `lake env`.
    lean_bin = os.environ.get("LEANBENCH_LEAN") or vars_.get("LEAN") or shutil.which("lean") or "lean"
    if os.environ.get("LEANBENCH_LEAN"):
        # `lake env` exports DYLD_LIBRARY_PATH=<toolchain>/lib/lean, which would make a foreign
        # `lean` binary load the *toolchain's* libleanshared. Point it at the override's own lib dir.
        lib = str(Path(lean_bin).resolve().parent.parent / "lib" / "lean")
        vars_["DYLD_LIBRARY_PATH"] = lib
        # Linux: the stock `lean` finds libleanshared.so via $ORIGIN rpath, but `lake env` may
        # export LD_LIBRARY_PATH too; make the override's lib dir win in the same way.
        vars_["LD_LIBRARY_PATH"] = lib + (os.pathsep + vars_["LD_LIBRARY_PATH"] if vars_.get("LD_LIBRARY_PATH") else "")
        vars_["LEAN_SYSROOT"] = str(Path(lean_bin).resolve().parent.parent)
    lake_bin = vars_.get("LAKE") or shutil.which("lake") or "lake"
    env = dict(os.environ)
    env.update(vars_)
    # Put the real toolchain bin dir first so `lean`/`leanc` resolve without the elan proxy.
    tc_bin = str(Path(lean_bin).parent)
    env["PATH"] = tc_bin + os.pathsep + env.get("PATH", "")
    return LeanEnv(project=project, lake_vars=vars_, lean_bin=lean_bin, lake_bin=lake_bin,
                   lean_path=vars_.get("LEAN_PATH", ""), env=env)


def lean_version(lean_bin: str = "lean") -> dict[str, Any]:
    info: dict[str, Any] = {"lean_bin": lean_bin}
    try:
        out = subprocess.run([lean_bin, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    except OSError as e:
        return {**info, "error": str(e)}
    info["lean_version_string"] = out
    m = _VERSION_RE.search(out)
    if m:
        info.update(version=m.group(1), target=m.group(2), commit=m.group(3), build=m.group(4))
    return info


def lake_version(lake_bin: str = "lake") -> str | None:
    try:
        return subprocess.run([lake_bin, "--version"], capture_output=True, text=True, check=False).stdout.strip()
    except OSError:
        return None


def toolchain_info(le: LeanEnv) -> dict[str, Any]:
    info = lean_version(le.lean_bin)
    info["lake_version_string"] = lake_version(le.lake_bin)
    info["elan_toolchain"] = le.lake_vars.get("ELAN_TOOLCHAIN")
    info["sysroot"] = le.sysroot
    info["lean_path"] = le.lean_path.split(os.pathsep) if le.lean_path else []
    return info


# ----------------------------------------------------------------- parsers

def parse_time(s: str) -> float | None:
    m = _TIME_RE.match(s)
    if not m:
        return None
    return float(m.group(1)) * _UNITS[m.group(2)]


def parse_profile(text: str) -> dict[str, Any]:
    """Parse `lean --profile` output.

    Returns {"phases": {name: seconds}, "phases_total_s", "import_s",
             "elab_s" (= total minus import/initialization), "decl_lines": [...]}.
    The cumulative block looks like::

        cumulative profiling times:
        \telaboration 29.5ms
        \timport 4.17s
    """
    phases: dict[str, float] = {}
    decls: list[dict[str, Any]] = []
    in_block = False
    import_took = None
    for line in text.splitlines():
        if line.startswith("cumulative profiling times:"):
            in_block = True
            continue
        if in_block:
            if not line.startswith(("\t", " ")):
                in_block = False
            else:
                m = re.match(r"^\s+(.*\S)\s+(\S+)\s*$", line)
                if m and (t := parse_time(m.group(2))) is not None:
                    phases[m.group(1)] = t
                continue
        m = re.match(r"^(.*?)\btook\s+(\S+)\s*$", line.strip())
        if m:
            t = parse_time(m.group(2))
            if t is not None:
                what = m.group(1).strip()
                if what == "import":
                    import_took = t
                else:
                    decls.append({"what": what, "s": t})
    total = sum(phases.values())
    non_elab = sum(v for k, v in phases.items() if k in ("import", "initialization"))
    decls.sort(key=lambda d: -d["s"])
    return {
        "phases": phases,
        "phases_total_s": total,
        "import_s": phases.get("import", import_took),
        "elab_s": total - non_elab,
        "n_slow_items": len(decls),
        "slowest_items": decls[:15],
    }


_STATS_KEYS = {
    "number of imported modules": "imported_module_parts",   # counts .olean parts (5 per module in v4.33)
    "number of memory-mapped modules": "mmapped_module_parts",
    "number of imported bytes": "imported_bytes",
    "number of imported consts": "imported_consts",
    "number of buckets for imported consts": "const_buckets",
    "number of extensions": "extensions",
    "trust level": "trust_level",
}


def parse_stats(text: str) -> dict[str, Any]:
    """Parse `lean --stats` output: the header (module-part / mmap / byte / const counts, kept
    raw in `raw_header`) and the per-extension imported/local entry counts (`extensions`)."""
    out: dict[str, Any] = {}
    header: list[str] = []
    exts: dict[str, dict[str, int]] = {}
    cur: str | None = None
    in_stats = False
    for line in text.splitlines():
        if line.startswith("direct imports:"):
            in_stats = True
        if not in_stats:
            continue
        m = re.match(r"extension '(.+)'\s*$", line)
        if m:
            cur = m.group(1)
            exts[cur] = {}
            continue
        if cur is not None and line.startswith((" ", "\t")):
            m = re.match(r"\s+number of (imported|local) entries:\s+(\d+)", line)
            if m:
                exts[cur][m.group(1)] = int(m.group(2))
            continue
        if line.startswith("cumulative profiling times:"):
            break
        cur = None
        header.append(line)
        for key, name in _STATS_KEYS.items():
            if line.startswith(key + ":"):
                val = line.split(":", 1)[1].strip()
                try:
                    out[name] = int(val)
                except ValueError:
                    out[name] = val
        if line.startswith("direct imports:"):
            out["direct_imports"] = line.split(":", 1)[1].strip()
    if out:
        out["raw_header"] = "\n".join(header).strip()
        out["extensions"] = {k: v for k, v in exts.items() if v}
        out["imported_extension_entries_total"] = sum(v.get("imported", 0) for v in exts.values())
        out["local_extension_entries_total"] = sum(v.get("local", 0) for v in exts.values())
        top = sorted(((k, v.get("imported", 0)) for k, v in exts.items()), key=lambda kv: -kv[1])[:15]
        out["top_extensions_by_imported_entries"] = dict(top)
    return out


EVAL_SNIPPET = """
#eval show Lean.CoreM Unit from do
  let env ← Lean.getEnv
  let nLocal := env.constants.map₂.foldl (fun n _ _ => n + 1) 0
  IO.println s!"LEANBENCH modules={env.header.moduleNames.size} constants_imported={env.constants.map₁.size} constants_local={nLocal}"
"""


def parse_eval(text: str) -> dict[str, int]:
    m = re.search(r"LEANBENCH modules=(\d+) constants_imported=(\d+) constants_local=(\d+)", text)
    if not m:
        return {}
    return {"modules": int(m.group(1)), "constants_imported": int(m.group(2)), "constants_local": int(m.group(3))}
