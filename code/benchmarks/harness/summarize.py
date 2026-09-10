"""Markdown summaries of result files and of the index."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def gib(b: float | int | None) -> str:
    return "-" if b is None else f"{b / 2**30:.2f}"


def s(x: float | None, nd: int = 2) -> str:
    return "-" if x is None else f"{x:.{nd}f}"


def table(headers: list[str], rows: list[list[Any]]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def meta_block(doc: dict[str, Any]) -> str:
    m = doc["meta"]
    mach = m["machine"]
    lean = m["lean"]
    proj = m["project"]
    la = m["loadavg_before"]
    lines = [
        f"**{doc['suite']}** run `{doc['run_id']}` at {m['timestamp_utc']}"
        + (f" (tag: {m['tag']})" if m.get("tag") else ""),
        f"- machine: {mach.get('model')} / {mach.get('ncpu')} cores / {gib(mach.get('memsize_bytes'))} GiB / {mach.get('os')}",
        f"- lean {lean.get('version')} ({(lean.get('commit') or '')[:10]}), {lean.get('lake_version_string')}",
        f"- project: {proj.get('dir')} @ {(proj.get('git_commit') or '')[:10]} (mathlib {proj.get('mathlib_rev')}, toolchain {proj.get('lean_toolchain')})",
        f"- load average before: {la['1m']:.2f} {la['5m']:.2f} {la['15m']:.2f}; harness rev {(m.get('harness_git_rev') or '')[:10]}",
    ]
    if m.get("notes"):
        lines.append(f"- notes: {m['notes']}")
    return "\n".join(lines)


def summarize_import(doc: dict[str, Any]) -> str:
    rows = []
    for c in doc["cases"]:
        d = c["direct"]
        rows.append([
            c["name"], c.get("root_variant", "legacy"), d["wall_s"]["n"],
            f"{s(d['wall_s']['mean'])} ± {s(d['wall_s']['stdev'])}", s(d["wall_s"]["min"]), s(d["wall_s"]["max"]),
            s(d["user_s"]["mean"]), s(d["sys_s"]["mean"]), gib(d["maxrss_bytes"]["mean"]),
            f"{int(d['minflt']['mean'])}/{int(d['majflt']['mean'])}",
            s(c.get("lake_overhead_wall_s")),
            c.get("env_counts", {}).get("modules", "-"),
            c.get("env_counts", {}).get("constants_imported", c.get("stats", {}).get("imported_consts", "-")),
            gib(c.get("stats", {}).get("imported_bytes")),
            s(c.get("profile_phases", {}).get("import")),
        ])
    t = table(["import", "variant", "n", "wall s (mean ± sd)", "min", "max", "user s", "sys s", "maxRSS GiB",
               "minflt/majflt", "lake overhead s", "modules", "constants", "imported GiB", "profile import s"], rows)
    phases = []
    for c in doc["cases"]:
        if c.get("profile_phases"):
            ph = c["profile_phases"]
            top = sorted(ph.items(), key=lambda kv: -kv[1])[:6]
            phases.append([c["name"], c.get("root_variant", "legacy"), ", ".join(f"{k} {s(v, 3)}" for k, v in top)])
    return t + "\n\n" + table(["import", "variant", "top profile phases (s)"], phases)


def summarize_textbook(doc: dict[str, Any]) -> str:
    rows = []
    for b in doc["baselines"]:
        st = b["stats"]
        rows.append(["_baseline_ " + ("+".join(b["imports"]) or "none"), b.get("root_variant", "legacy"), st["wall_s"]["n"],
                     s(st["wall_s"]["mean"]), s(st["user_s"]["mean"]), s(st["sys_s"]["mean"]), gib(st["maxrss_bytes"]["mean"]),
                     "-", "-", "-", "-", "-"])
    for e in doc["files"]:
        st = e["stats"]
        rows.append([e["name"], e.get("root_variant", "legacy"), st["wall_s"]["n"], s(st["wall_s"]["mean"]), s(st["user_s"]["mean"]), s(st["sys_s"]["mean"]),
                     gib(st["maxrss_bytes"]["mean"]), s(e["elab_wall_est_s"]), s(e["elab_user_est_s"]),
                     s(e["profile_elab_s"], 3), s(e.get("profile_elab_net_s"), 3), "ok" if e["ok"] else "FAIL"])
    return table(["file", "variant", "n", "wall s", "user s", "sys s", "maxRSS GiB", "elab est (wall−base)", "elab est (user−base)",
                  "profile elab s", "profile elab net s", "status"], rows)


def summarize_mathlib_files(doc: dict[str, Any]) -> str:
    rows = []
    for e in doc["files"]:
        if e.get("missing"):
            rows.append([e["file"], "-", "-", "-", "-", "-", "-", "-", "missing"])
            continue
        st = e["stats"]
        ph = e["profile_phases"]
        top = sorted(((k, v) for k, v in ph.items() if k not in ("import",)), key=lambda kv: -kv[1])[:3]
        rows.append([e["file"], e["lines"], s(st["wall_s"]["mean"]), s(st["user_s"]["mean"]), s(st["sys_s"]["mean"]),
                     gib(st["maxrss_bytes"]["mean"]), s(e["profile_import_s"]), s(e["profile_elab_s"]),
                     ", ".join(f"{k} {s(v)}" for k, v in top), "ok" if e["ok"] else "FAIL"])
    return table(["file", "lines", "wall s", "user s", "sys s", "maxRSS GiB", "import s", "elab s", "top phases", "status"], rows)


def summarize_build(doc: dict[str, Any]) -> str:
    b = doc["build"]
    rows = [
        ["command", " ".join(b["cmd"])], ["exit code", b["exit_code"]], ["timed out", b["timed_out"]],
        ["wall s", s(b["wall_s"])], ["user s", s(b["user_s"])], ["sys s", s(b["sys_s"])], ["CPU util", s(b["cpu_util"])],
        ["max RSS (single proc) GiB", gib(b["maxrss_bytes"])], ["peak tree RSS GiB", gib(b["tree_rss_peak_bytes"])],
        ["time to peak s", s(b["tree_rss_peak_t"])], ["lean processes seen", b["lean_procs_seen"]],
        ["max concurrent lean", b["max_concurrent_lean"]], ["all processes seen", b["procs_seen"]],
        ["process census", json.dumps(b["comm_census"])], ["lake reported jobs", b.get("lake_reported_jobs")],
        ["minflt/majflt", f"{b['minflt']}/{b['majflt']}"],
    ]
    if "clean" in doc:
        rows.append(["clean", json.dumps(doc["clean"])])
    return table(["metric", "value"], rows)


def _ext_table(w: dict[str, Any]) -> str:
    if not w.get("exists"):
        return f"_{w.get('root')} does not exist_"
    rows = [[k, v["files"], gib(v["bytes"]), f"{v['bytes']/2**20:.1f}"] for k, v in w["by_ext"].items()]
    rows.append(["**total**", w["total"]["files"], gib(w["total"]["bytes"]), f"{w['total']['bytes']/2**20:.1f}"])
    return table(["ext", "files", "GiB", "MiB"], rows)


def _sub_table(w: dict[str, Any]) -> str:
    if not w.get("exists"):
        return ""
    rows = [[k, v["files"], gib(v["bytes"])] for k, v in w["by_subdir"].items()]
    return table(["subdir", "files", "GiB"], rows)


def summarize_disk(doc: dict[str, Any]) -> str:
    out = []
    if "project" in doc:
        p = doc["project"]
        out.append(f"### Project {p['dir']}\n\nSources: {p['sources']['files']} .lean files, "
                   f"{p['sources']['lines']} lines, {p['sources']['bytes']/2**20:.1f} MiB. "
                   f".lake total: {gib(p['lake_total']['bytes'])} GiB, {p['lake_total']['files']} files.")
        out.append("#### .lake/build by extension\n\n" + _ext_table(p["lake_build"]))
        out.append("#### .lake/build by subdir\n\n" + _sub_table(p["lake_build"]))
        out.append("#### .lake/packages by package\n\n" + _sub_table(p["lake_packages"]))
        out.append("#### .lake/packages by extension\n\n" + _ext_table(p["lake_packages"]))
        out.append("#### Largest .olean files (project)\n\n" + table(
            ["MiB", "path"], [[f"{o['bytes']/2**20:.1f}", o["path"]] for o in p["largest_oleans"]]))
    if "toolchain" in doc:
        t = doc["toolchain"]
        out.append(f"### Toolchain {t['dir']}\n\n" + _ext_table(t["all"]))
        out.append("#### by top-level subdir\n\n" + _sub_table(t["all"]))
        out.append("#### lib/ by subdir\n\n" + _sub_table(t["lib"]))
        out.append("#### Largest .olean files (toolchain)\n\n" + table(
            ["MiB", "path"], [[f"{o['bytes']/2**20:.1f}", o["path"]] for o in t["largest_oleans"]]))
    return "\n\n".join(out)


def summarize_env_detail(doc: dict[str, Any]) -> str:
    sm = doc["summary"]
    rows = [[k, (gib(v) + " GiB" if k.endswith("bytes") else v)] for k, v in sm.items()]
    r = doc["run"]
    # crude ASCII sparkline of RSS over time (20 buckets)
    samples = r["samples"]
    spark = ""
    if samples:
        n = 24
        peak = max(x[1] for x in samples) or 1
        step = max(1, len(samples) // n)
        pts = [samples[i][1] for i in range(0, len(samples), step)][:n]
        chars = "▁▂▃▄▅▆▇█"
        spark = "".join(chars[min(7, int(v / peak * 7.999))] for v in pts)
    return table(["metric", "value"], rows) + f"\n\nRSS over time: `{spark}`"


SUMMARIZERS = {
    "import": summarize_import, "textbook": summarize_textbook, "mathlib-files": summarize_mathlib_files,
    "build": summarize_build, "disk": summarize_disk, "env-detail": summarize_env_detail,
}


def summarize_file(path: Path) -> str:
    doc = json.loads(Path(path).read_text())
    fn = SUMMARIZERS.get(doc["suite"])
    body = fn(doc) if fn else "```\n" + json.dumps(doc["summary"], indent=1) + "\n```"
    return meta_block(doc) + "\n\n" + body + "\n"


def summarize_index(entries: list[dict[str, Any]]) -> str:
    rows = []
    for e in entries:
        sm = e.get("summary", {})
        head = {k: v for k, v in sm.items() if not isinstance(v, (dict, list))}
        rows.append([e["file"], e["suite"], e.get("timestamp"), e.get("tag") or "", e.get("lean_version"),
                     (e.get("mathlib_rev") or "")[:10], json.dumps(head)[:120]])
    return table(["file", "suite", "timestamp", "tag", "lean", "mathlib", "summary"], rows)
