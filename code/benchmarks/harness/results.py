"""Result files, index.json maintenance and small statistics helpers."""
from __future__ import annotations

import json
import os
import statistics
from pathlib import Path
from typing import Any, Iterable

from meta import RAW_DIR, REPO_DIR, RESULTS_DIR, utc_now


def timestamp_id() -> str:
    return utc_now().strftime("%Y%m%dT%H%M%SZ")


def make_run_id(suite: str) -> str:
    return f"{timestamp_id()}_{suite}"


def raw_dir(run_id: str) -> Path:
    d = RAW_DIR / run_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def relpath(p: str | os.PathLike | None) -> str | None:
    if p is None:
        return None
    try:
        return str(Path(p).resolve().relative_to(REPO_DIR))
    except ValueError:
        return str(p)


def describe(values: Iterable[float]) -> dict[str, float | int]:
    vals = [float(v) for v in values]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "mean": statistics.fmean(vals),
        "min": min(vals),
        "max": max(vals),
        "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        "median": statistics.median(vals),
    }


METRIC_KEYS = ("wall_s", "user_s", "sys_s", "cpu_util", "maxrss_bytes", "tree_rss_peak_bytes",
               "minflt", "majflt", "nvcsw", "nivcsw")


def describe_runs(runs: list[dict[str, Any]], keys: tuple[str, ...] = METRIC_KEYS) -> dict[str, Any]:
    return {k: describe(r[k] for r in runs if k in r) for k in keys}


def write_result(run_id: str, suite: str, meta: dict[str, Any], body: dict[str, Any],
                 summary: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{run_id}.json"
    doc = {"schema_version": 1, "run_id": run_id, "suite": suite, "meta": meta,
           "summary": summary, **body}
    path.write_text(json.dumps(doc, indent=1, sort_keys=False) + "\n")
    append_index(path, suite, meta, summary)
    return path


def append_index(path: Path, suite: str, meta: dict[str, Any], summary: dict[str, Any]) -> None:
    idx = RESULTS_DIR / "index.json"
    entries: list[dict[str, Any]] = []
    if idx.exists():
        try:
            entries = json.loads(idx.read_text())
        except ValueError:
            entries = []
    entries.append({
        "file": path.name,
        "suite": suite,
        "timestamp": meta.get("timestamp_utc"),
        "tag": meta.get("tag"),
        "project": meta.get("project", {}).get("dir"),
        "lean_version": meta.get("lean", {}).get("version"),
        "mathlib_rev": meta.get("project", {}).get("mathlib_rev"),
        "summary": summary,
    })
    idx.write_text(json.dumps(entries, indent=1) + "\n")


def load_index() -> list[dict[str, Any]]:
    idx = RESULTS_DIR / "index.json"
    if not idx.exists():
        return []
    return json.loads(idx.read_text())


def strip_measurement(m: dict[str, Any], keep_samples: bool = True) -> dict[str, Any]:
    """Make a Measurement dict result-file friendly (relative log paths)."""
    m = dict(m)
    m["stdout_path"] = relpath(m.get("stdout_path"))
    m["stderr_path"] = relpath(m.get("stderr_path"))
    if not keep_samples:
        m["samples"] = []
    return m
