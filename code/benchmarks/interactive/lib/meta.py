"""Metadata captured in every result file (machine, toolchain, project, load)."""
from __future__ import annotations

import datetime as _dt
import json
import os
import platform
import resource
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HARNESS_DIR = Path(__file__).resolve().parent
# In this package the benchmark is self-contained: results land in `results/` beside
# lspbench.py.  (In the development tree this file lives in bench/harness/ and REPO_DIR is
# the repository root; that is the only difference between the two copies.)
REPO_DIR = Path(os.environ.get("LSPBENCH_HOME", HARNESS_DIR.parent))
BENCH_DIR = REPO_DIR
RESULTS_DIR = BENCH_DIR / "results"
RAW_DIR = RESULTS_DIR / "raw"
CASES_DIR = BENCH_DIR / "cases"
WORK_DIR = Path(os.environ.get("LEAN_WORK", Path.home() / "lean-work"))
SCRATCH_DIR = WORK_DIR / "scratch" / "leanbench"
DEFAULT_PROJECT = Path(os.environ.get("LEANBENCH_PROJECT", WORK_DIR / "mathlib4-v4.33.1"))


def _run(cmd: list[str], cwd: str | os.PathLike | None = None) -> str | None:
    try:
        r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def _sysctl(key: str) -> str | None:
    return _run(["sysctl", "-n", key])


def utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def machine_info() -> dict[str, Any]:
    info: dict[str, Any] = {
        "hostname": platform.node(),
        "platform": sys.platform,
        "arch": platform.machine(),
        "python": platform.python_version(),
        "page_size": resource.getpagesize(),
        "ncpu": os.cpu_count(),
    }
    if sys.platform == "darwin":
        info["model"] = _sysctl("machdep.cpu.brand_string")
        info["hw_model"] = _sysctl("hw.model")
        ncpu = _sysctl("hw.ncpu")
        mem = _sysctl("hw.memsize")
        info["ncpu"] = int(ncpu) if ncpu else info["ncpu"]
        info["memsize_bytes"] = int(mem) if mem else None
        info["perflevel0_cores"] = _sysctl("hw.perflevel0.physicalcpu")
        info["perflevel1_cores"] = _sysctl("hw.perflevel1.physicalcpu")
        info["os"] = f"macOS {_run(['sw_vers', '-productVersion'])} ({_run(['sw_vers', '-buildVersion'])})"
        info["kernel"] = _run(["uname", "-v"])
    else:
        model = None
        cpuinfo: dict[str, str] = {}
        try:
            for line in Path("/proc/cpuinfo").read_text().splitlines():
                if ":" in line:
                    k, v = (x.strip() for x in line.split(":", 1))
                    cpuinfo.setdefault(k, v)
        except OSError:
            pass
        # x86 has "model name"; arm64 only exposes implementer/part codes (and no name in a VM).
        model = cpuinfo.get("model name") or cpuinfo.get("Hardware") or cpuinfo.get("Model")
        if model is None and "CPU implementer" in cpuinfo:
            model = "arm64 implementer=%s part=%s variant=%s" % (
                cpuinfo.get("CPU implementer"), cpuinfo.get("CPU part"), cpuinfo.get("CPU variant"))
        info["model"] = model
        info["cpuinfo_features"] = cpuinfo.get("Features") or cpuinfo.get("flags")
        info["lscpu"] = _run(["lscpu"])
        info["linux"] = _linux_container_info()
        try:
            info["memsize_bytes"] = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError):
            info["memsize_bytes"] = None
        info["os"] = platform.platform()
        info["kernel"] = _run(["uname", "-v"])
    return info


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _linux_container_info() -> dict[str, Any]:
    """Container / cgroup facts: memory and CPU limits as the process actually sees them,
    docker markers, kernel release, mounts of interest, free memory."""
    d: dict[str, Any] = {
        "in_docker": Path("/.dockerenv").exists(),
        "kernel_release": _run(["uname", "-r"]),
        "os_release": _read("/etc/os-release"),
        # cgroup v2 (Docker Desktop's VM uses it); values are "max" or bytes / "quota period"
        "cgroup_memory_max": _read("/sys/fs/cgroup/memory.max"),
        "cgroup_memory_high": _read("/sys/fs/cgroup/memory.high"),
        "cgroup_memory_current": _read("/sys/fs/cgroup/memory.current"),
        "cgroup_cpu_max": _read("/sys/fs/cgroup/cpu.max"),
        "cgroup_cpuset_effective": _read("/sys/fs/cgroup/cpuset.cpus.effective"),
        # cgroup v1 fallbacks
        "cgroup1_memory_limit": _read("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
        "cgroup1_cpu_quota": _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"),
        "sched_affinity_cpus": len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else None,
        "meminfo": None,
        "drop_caches_writable": os.access("/proc/sys/vm/drop_caches", os.W_OK),
        "transparent_hugepage": _read("/sys/kernel/mm/transparent_hugepage/enabled"),
    }
    mi = _read("/proc/meminfo")
    if mi:
        want = ("MemTotal", "MemFree", "MemAvailable", "Cached", "SwapTotal", "SwapFree", "Mapped")
        d["meminfo"] = {k: v for k, v in (l.split(":", 1) for l in mi.splitlines() if ":" in l)
                        if k in want}
        d["meminfo"] = {k: v.strip() for k, v in d["meminfo"].items()}
    return d


def git_rev(path: str | os.PathLike) -> str | None:
    return _run(["git", "rev-parse", "HEAD"], cwd=path)


def git_dirty(path: str | os.PathLike) -> bool | None:
    out = _run(["git", "status", "--porcelain"], cwd=path)
    return None if out is None else bool(out)


def project_info(project: Path) -> dict[str, Any]:
    info: dict[str, Any] = {"dir": str(project), "exists": project.is_dir()}
    if not project.is_dir():
        return info
    info["git_commit"] = git_rev(project)
    info["git_dirty"] = git_dirty(project)
    tc = project / "lean-toolchain"
    info["lean_toolchain"] = tc.read_text().strip() if tc.exists() else None
    manifest = project / "lake-manifest.json"
    info["name"] = None
    info["mathlib_rev"] = None
    if manifest.exists():
        try:
            m = json.loads(manifest.read_text())
            info["name"] = m.get("name")
            pkgs = m.get("packages", [])
            info["packages"] = [{"name": p.get("name"), "rev": p.get("rev"), "type": p.get("type")} for p in pkgs]
            for p in pkgs:
                if p.get("name") == "mathlib":
                    info["mathlib_rev"] = p.get("rev")
            if info["name"] == "mathlib":
                info["mathlib_rev"] = "self"
        except (OSError, ValueError) as e:
            info["manifest_error"] = str(e)
    return info


def disk_free(path: str | os.PathLike) -> dict[str, int] | None:
    try:
        u = shutil.disk_usage(path)
    except OSError:
        return None
    return {"total_bytes": u.total, "used_bytes": u.used, "free_bytes": u.free}


def collect_meta(*, suite: str, project: Path, lean_info: dict[str, Any], tag: str | None,
                 notes: str | None, argv: list[str]) -> dict[str, Any]:
    la = os.getloadavg()
    return {
        "suite": suite,
        "timestamp_utc": utc_now().isoformat(timespec="seconds"),
        "harness_git_rev": git_rev(REPO_DIR),
        "harness_git_dirty": git_dirty(REPO_DIR),
        "argv": argv,
        "tag": tag,
        "notes": notes,
        "machine": machine_info(),
        "lean": lean_info,
        "project": project_info(project),
        "loadavg_before": {"1m": la[0], "5m": la[1], "15m": la[2]},
        "disk_free": disk_free(project if project.is_dir() else Path.home()),
        "limitations": [
            ("linux: page cache dropped only if the caller did so (see notes/tag)" if sys.platform != "darwin" else
             "no sudo: page cache cannot be purged, so runs are warm-cache unless noted"),
            "background load (see loadavg_before) is not controlled",
        ],
    }
