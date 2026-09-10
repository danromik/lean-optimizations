#!/usr/bin/env python3
"""The interactive benchmark: drive the Lean language server headlessly with
scripted editing sessions and measure what a human would feel in VS Code.

Stdlib only.  Launches `lake serve` (the project's own toolchain via elan, or an explicit
`--toolchain` directory whose `bin/lake` is used directly) in a Lake project, opens a
file and replays a JSON *session script* of timed events (see README.md):

  insert / delete / replace / type   edits, sent as textDocument/didChange (versioned)
  wait                               human thinking pause (ms) — never counted as Lean time
  diagnostics                        wait until Lean is done with the current version
  goal / hover / completion          $/lean/plainGoal, textDocument/hover, textDocument/completion
  expect_errors N / expect_no_errors / expect_message  self-validating assertions
  save                               textDocument/didSave

Every event records its latency, the diagnostic counts and the worker RSS.  One result
file per session (suite `interactive`) is written to results/ using the existing
meta/index conventions of the measurement harness (lib/).

Usage:
  lspbench.py run --session sessions/novice-01.json [--toolchain lean-fork] [--repeat 2]
  lspbench.py run --all [--tier novice] [--toolchain stock --toolchain lean-fork ...]
  lspbench.py report [--since 20260825T00]           comparison tables from results/index.json
  lspbench.py show results/<file>.json                 markdown summary of one result file
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import meta as _meta  # noqa: E402
import results as _results  # noqa: E402
from leanenv import lake_version, lean_version  # noqa: E402

SESSIONS_DIR = HERE / "sessions"
WORK_DIR = _meta.WORK_DIR
SCRATCH_DIR = WORK_DIR / "scratch" / "lspbench"
STOCK_TOOLCHAIN = "leanprover--lean4---v4.33.1"

PROJECTS = {
    "mathlib": WORK_DIR / "mathlib4-v4.33.1",
    "formal-conjectures": WORK_DIR / "corpus" / "formal-conjectures",
    "FLT": WORK_DIR / "corpus" / "FLT",
    "carleson": WORK_DIR / "corpus" / "carleson",
    "PrimeNumberTheoremAnd": WORK_DIR / "corpus" / "PrimeNumberTheoremAnd",
    "PhysLean": WORK_DIR / "corpus" / "PhysLean",
    "equational_theories": WORK_DIR / "corpus" / "equational_theories",
}


# ----------------------------------------------------------------------------- utilities

def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def path_to_uri(p: Path) -> str:
    return "file://" + urllib.parse.quote(str(p))


def loadavg() -> dict[str, float]:
    la = os.getloadavg()
    return {"1m": la[0], "5m": la[1], "15m": la[2]}


def pct(vals: list[float], q: float) -> float | None:
    if not vals:
        return None
    s = sorted(vals)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def dist(vals: list[float]) -> dict[str, Any]:
    vals = [v for v in vals if v is not None]
    if not vals:
        return {"n": 0}
    return {"n": len(vals), "median": statistics.median(vals), "p90": pct(vals, 0.9),
            "max": max(vals), "min": min(vals), "mean": statistics.fmean(vals), "sum": sum(vals)}


_SIZE_RE = re.compile(r"^([0-9.]+)\s*([KMGT]?)B?$", re.I)
_MULT = {"": 1, "K": 2**10, "M": 2**20, "G": 2**30, "T": 2**40}


def parse_size(s: str) -> int | None:
    m = _SIZE_RE.match(s.strip())
    if not m:
        return None
    return int(float(m.group(1)) * _MULT[m.group(2).upper()])


def vm_stat() -> dict[str, int]:
    """System-wide page counters, in bytes.  `Anonymous pages` + `Pages occupied by compressor`
    is the machine's private (non file-backed) footprint — the honest denominator for
    "what does one more worker cost", since mapped oleans are file-backed and shared."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=20, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    m = re.search(r"page size of (\d+) bytes", out)
    ps = int(m.group(1)) if m else 4096
    d: dict[str, int] = {}
    for line in out.splitlines()[1:]:
        k, _, v = line.partition(":")
        v = v.strip().rstrip(".")
        if v.isdigit():
            d[k.strip()] = int(v) * ps
    d["_private_bytes"] = d.get("Anonymous pages", 0) + d.get("Pages occupied by compressor", 0)
    d["_file_backed_bytes"] = d.get("File-backed pages", 0)
    return d


def ps_rss(pid: int) -> int | None:
    try:
        out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=10, check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return int(out) * 1024 if out.isdigit() else None


def vmmap_summary(pid: int, timeout: float = 180.0) -> dict[str, Any]:
    """`vmmap -summary` for one process.  Returns the physical footprint (macOS's own
    private-memory accounting: dirty anonymous + compressed + IOKit, *excluding* clean
    file-backed pages) and the region-type totals, so that the shared mapped-olean pages
    can be separated from what the process really costs."""
    res: dict[str, Any] = {"pid": pid}
    try:
        r = subprocess.run(["vmmap", "-summary", str(pid)], capture_output=True, text=True,
                           timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        res["error"] = str(e)
        return res
    out = r.stdout
    if r.returncode != 0 and not out:
        res["error"] = (r.stderr or "")[:200]
        return res
    for key, pat in (("phys_footprint", r"^Physical footprint:\s+(\S+)"),
                     ("phys_footprint_peak", r"^Physical footprint \(peak\):\s+(\S+)")):
        m = re.search(pat, out, re.M)
        if m:
            res[key] = parse_size(m.group(1))
    # region-type table: NAME  VIRTUAL RESIDENT DIRTY SWAPPED ...  (the first TOTAL row;
    # a second TOTAL belongs to the malloc-zone table further down)
    for label, key in (("TOTAL", "total"), ("mapped file", "mapped_file"), ("MALLOC_LARGE", "malloc_large")):
        m = re.search(rf"^{re.escape(label)}\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", out, re.M)
        if m:
            res[key] = {"virtual": parse_size(m.group(1)), "resident": parse_size(m.group(2)),
                        "dirty": parse_size(m.group(3)), "swapped": parse_size(m.group(4))}
    return res


def prewarm_oleans(project: Path) -> dict[str, Any]:
    """Read every `.olean` of the project and its Lake packages so that the file cache is warm.

    This matters more than it looks: `import Mathlib` faults ~4.8 GB of mapped olean pages into
    the worker, and whether those pages are already in the unified buffer cache is worth ~20 s of
    header time on this machine (measured: 32 s of import cold vs 9-10 s warm).  Without it the
    *first* worker of a run is always slower than the second for a reason that has nothing to do
    with the question the multi-file mode asks, so every timed run pre-warms."""
    t0 = time.perf_counter()
    dirs = [project / ".lake" / "build" / "lib"]
    pkgs = project / ".lake" / "packages"
    if pkgs.is_dir():
        dirs += [p / ".lake" / "build" / "lib" for p in pkgs.iterdir() if p.is_dir()]
    total = 0
    files = 0
    buf = bytearray(1 << 20)
    for d in dirs:
        if not d.is_dir():
            continue
        # `.olean.private` holds the private parts and is 2x the size of the `.olean` itself
        # (1.76 GB vs 3.46 GB for Mathlib); the watchdog reads `.ilean`
        for f in sorted(list(d.rglob("*.olean*")) + list(d.rglob("*.ilean"))):
            try:
                with open(f, "rb", buffering=0) as fh:
                    while True:
                        n = fh.readinto(buf)
                        if not n:
                            break
                        total += n
                files += 1
            except OSError:
                pass
    return {"files": files, "bytes": total, "s": time.perf_counter() - t0}


def resolve_toolchain(name: str | None) -> dict[str, Any]:
    """`None` → the project's own toolchain via the elan `lake` proxy.
    Otherwise a directory (or a name under ~/.elan/toolchains or ~/lean-work/toolchains)
    whose bin/lake and bin/lean are used directly."""
    if name is None or name in ("project", "elan"):
        return {"name": "project", "dir": None, "lake": shutil.which("lake") or "lake", "lean": None}
    if name == "stock":
        name = STOCK_TOOLCHAIN
    cands = [Path(name), Path.home() / ".elan" / "toolchains" / name, WORK_DIR / "toolchains" / name]
    for c in cands:
        if (c / "bin" / "lake").exists():
            c = c.resolve()
            return {"name": name if not Path(name).is_absolute() else c.name, "dir": str(c),
                    "lake": str(c / "bin" / "lake"), "lean": str(c / "bin" / "lean")}
    raise SystemExit(f"toolchain not found: {name} (tried {[str(c) for c in cands]})")


# ----------------------------------------------------------------------------- process sampler

class ProcSampler:
    """Samples `ps` and keeps RSS time series of the lake/lean processes below the server."""

    def __init__(self, root_pid: int, interval: float = 0.25) -> None:
        self.root_pid = root_pid
        self.interval = interval
        self.samples: list[dict[str, Any]] = []
        self.worker_cmds: dict[int, str] = {}
        self.worker_pids_seen: list[int] = []
        self.server_pids_seen: list[int] = []
        self.setup_file_seen: list[float] = []
        # (t, pid, ppid, cmd) for every `lake setup-file` sighting: the command line carries the
        # target .lean file and the parent is the worker process, which is how a worker is
        # attributed to a document in multi-file runs
        self.setup_file_events: list[tuple[float, int, int, str]] = []
        self.t0 = time.perf_counter()
        self._stop = threading.Event()
        self._thr = threading.Thread(target=self._loop, daemon=True)
        self._lock = threading.Lock()

    def start(self) -> None:
        self._thr.start()

    def stop(self) -> None:
        self._stop.set()
        self._thr.join(timeout=3)

    def now(self) -> float:
        return time.perf_counter() - self.t0

    def sample_once(self) -> dict[str, Any] | None:
        try:
            out = subprocess.run(["ps", "-axo", "pid=,ppid=,rss=,command="], capture_output=True,
                                 text=True, timeout=10, check=False).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
        procs: dict[int, tuple[int, int, str]] = {}
        for line in out.splitlines():
            parts = line.split(None, 3)
            if len(parts) < 4:
                continue
            try:
                pid, ppid, rss = int(parts[0]), int(parts[1]), int(parts[2])
            except ValueError:
                continue
            procs[pid] = (ppid, rss, parts[3])
        # descendants of root
        desc: set[int] = set()
        frontier = [self.root_pid]
        while frontier:
            p = frontier.pop()
            for pid, (ppid, _, _) in procs.items():
                if ppid == p and pid not in desc:
                    desc.add(pid)
                    frontier.append(pid)
        t = self.now()
        s = {"t": round(t, 3), "worker_rss": 0, "server_rss": 0, "other_rss": 0, "n_workers": 0,
             "setup_file": False, "workers": []}
        for pid in desc:
            ppid, rss, cmd = procs[pid]
            rss_b = rss * 1024
            if "--worker" in cmd:
                s["n_workers"] += 1
                s["worker_rss"] += rss_b
                s["workers"].append([pid, rss_b])
                if pid not in self.worker_cmds:
                    self.worker_cmds[pid] = cmd.split(" --worker")[0]
                    self.worker_pids_seen.append(pid)
            elif "--server" in cmd:
                s["server_rss"] += rss_b
                if pid not in self.server_pids_seen:
                    self.server_pids_seen.append(pid)
            elif "setup-file" in cmd:
                s["setup_file"] = True
                s["other_rss"] += rss_b
                self.setup_file_events.append((t, pid, ppid, cmd))
            else:
                s["other_rss"] += rss_b
        if s["setup_file"]:
            self.setup_file_seen.append(t)
        with self._lock:
            self.samples.append(s)
        return s

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.sample_once()
            self._stop.wait(self.interval)

    def worker_rss_now(self) -> int:
        with self._lock:
            return self.samples[-1]["worker_rss"] if self.samples else 0

    def peak(self, t_from: float = 0.0, t_to: float | None = None) -> int:
        with self._lock:
            return max((s["worker_rss"] for s in self.samples
                        if s["t"] >= t_from and (t_to is None or s["t"] <= t_to)), default=0)

    def worker_rss_pid(self, pid: int) -> int:
        with self._lock:
            for s in reversed(self.samples):
                for wp, rss in s["workers"]:
                    if wp == pid:
                        return rss
            return 0

    def peak_pid(self, pid: int, t_from: float = 0.0, t_to: float | None = None) -> int:
        with self._lock:
            best = 0
            for s in self.samples:
                if s["t"] < t_from or (t_to is not None and s["t"] > t_to):
                    continue
                for wp, rss in s["workers"]:
                    if wp == pid and rss > best:
                        best = rss
            return best


# ----------------------------------------------------------------------------- LSP client

class LspError(Exception):
    pass


class LeanLsp:
    """Minimal JSON-RPC-over-stdio client for `lake serve` / `lean --server`."""

    def __init__(self, cmd: list[str], cwd: Path, env: dict[str, str], log_dir: Path) -> None:
        self.cmd = cmd
        self.cwd = cwd
        self.stderr_path = log_dir / "server.stderr.log"
        self.trace_path = log_dir / "lsp-trace.jsonl"
        self._stderr_f = open(self.stderr_path, "wb")
        self._trace_f = open(self.trace_path, "w")
        self.proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=self._stderr_f, start_new_session=True)
        self.t0 = time.perf_counter()
        self._next_id = 1
        self._lock = threading.Lock()
        self._cv = threading.Condition(self._lock)
        self._responses: dict[int, dict[str, Any]] = {}
        self._pending_ids: set[int] = set()
        self.notifications: list[tuple[float, str, dict[str, Any]]] = []
        # per-uri state
        self.diags: dict[str, dict[str, Any]] = {}   # uri -> {"version", "diagnostics", "t"}
        self.progress: dict[str, dict[str, Any]] = {}  # uri -> {"version", "processing", "t", "done"}
        self.progress_history: list[dict[str, Any]] = []
        self.diag_history: list[dict[str, Any]] = []
        self.server_requests: list[dict[str, Any]] = []
        self.dup_notifications = 0
        self._last_notif_key: tuple[str, str] | None = None
        self.dead = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def now(self) -> float:
        return time.perf_counter() - self.t0

    # --- wire
    def _send(self, msg: dict[str, Any]) -> None:
        data = json.dumps(msg).encode()
        with self._lock:
            try:
                self.proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(data) + data)
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self.dead = True
                raise LspError(f"server stdin closed: {e}")
        self._trace("->", msg)

    def _trace(self, d: str, msg: dict[str, Any]) -> None:
        try:
            s = json.dumps(msg)
            if len(s) > 4000:
                s = s[:4000] + "...(truncated)"
            self._trace_f.write(f"{self.now():9.3f} {d} {s}\n")
        except (OSError, ValueError):
            pass

    def _read_loop(self) -> None:
        out = self.proc.stdout
        try:
            while True:
                headers: dict[str, str] = {}
                while True:
                    line = out.readline()
                    if not line:
                        raise EOFError
                    line = line.strip()
                    if not line:
                        break
                    k, _, v = line.decode(errors="replace").partition(":")
                    headers[k.strip().lower()] = v.strip()
                n = int(headers.get("content-length", "0"))
                body = out.read(n)
                if len(body) < n:
                    raise EOFError
                msg = json.loads(body)
                self._trace("<-", msg)
                self._dispatch(msg)
        except (EOFError, ValueError, OSError):
            with self._cv:
                self.dead = True
                self._cv.notify_all()

    def _dispatch(self, msg: dict[str, Any]) -> None:
        t = self.now()
        with self._cv:
            if "id" in msg and "method" not in msg:
                self._responses[msg["id"]] = msg
                self._cv.notify_all()
                return
            method = msg.get("method", "")
            params = msg.get("params") or {}
            if "id" in msg:  # server → client request (e.g. workspace/configuration, applyEdit)
                self.server_requests.append({"t": t, "method": method})
                resp: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"]}
                if method == "workspace/configuration":
                    resp["result"] = [None for _ in params.get("items", [])]
                elif method == "client/registerCapability":
                    resp["result"] = None
                else:
                    resp["result"] = None
                threading.Thread(target=self._send, args=(resp,), daemon=True).start()
                return
            # the watchdog forwards every worker notification, and in practice each arrives twice
            # (see docs/interactive/l1-interactive-benchmark.md, protocol pitfalls); count them
            key = (method, json.dumps(params, sort_keys=True))
            if key == self._last_notif_key:
                self.dup_notifications += 1
            self._last_notif_key = key
            self.notifications.append((t, method, params))
            if method == "textDocument/publishDiagnostics":
                uri = params.get("uri")
                ver = params.get("version")
                ds = params.get("diagnostics", [])
                self.diags[uri] = {"version": ver, "diagnostics": ds, "t": t}
                self.diag_history.append({"t": t, "uri": uri, "version": ver, "n": len(ds),
                                          "errors": sum(1 for d in ds if d.get("severity") == 1)})
            elif method == "$/lean/fileProgress":
                td = params.get("textDocument", {})
                uri = td.get("uri")
                ver = td.get("version")
                proc = params.get("processing", [])
                # kind 2 = fatalError: the reporter sends a single non-empty range at position 0
                # and never an empty list, so it must count as "done" (with a fatal flag)
                fatal = any(p.get("kind", 1) == 2 for p in proc)
                self.progress[uri] = {"version": ver, "processing": proc, "t": t,
                                      "done": len(proc) == 0 or fatal, "fatal": fatal}
                self.progress_history.append({"t": t, "uri": uri, "version": ver, "n_ranges": len(proc),
                                              "first_line": (proc[0]["range"]["start"]["line"] if proc else None),
                                              "kinds": sorted({p.get("kind", 1) for p in proc})})
            self._cv.notify_all()

    # --- API
    def request(self, method: str, params: Any, timeout: float = 600.0) -> dict[str, Any]:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        with self._cv:
            while rid not in self._responses:
                if self.dead:
                    raise LspError(f"server died while waiting for {method}")
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise LspError(f"timeout waiting for response to {method}")
                self._cv.wait(min(rem, 1.0))
            return self._responses.pop(rid)

    def notify(self, method: str, params: Any) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def wait_progress_done(self, uri: str, version: int, timeout: float, min_t: float = 0.0) -> float | None:
        """Wait for a `$/lean/fileProgress` with empty `processing` for a version >= `version`
        (received after `min_t`).  Returns the arrival time (client clock) or None on timeout."""
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                p = self.progress.get(uri)
                if p and p["done"] and (p["version"] or 0) >= version and p["t"] >= min_t:
                    return p["t"]
                if self.dead:
                    raise LspError("server died")
                rem = deadline - time.monotonic()
                if rem <= 0:
                    return None
                self._cv.wait(min(rem, 0.5))

    def wait_diag_version(self, uri: str, version: int, timeout: float) -> float | None:
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                d = self.diags.get(uri)
                if d and (d["version"] or 0) >= version:
                    return d["t"]
                if self.dead:
                    raise LspError("server died")
                rem = deadline - time.monotonic()
                if rem <= 0:
                    return None
                self._cv.wait(min(rem, 0.5))

    def close(self) -> None:
        try:
            if not self.dead:
                try:
                    self.request("shutdown", None, timeout=10)
                    self.notify("exit", None)
                except LspError:
                    pass
            self.proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        try:
            os.killpg(self.proc.pid, 15)
            self.proc.wait(timeout=5)
        except (ProcessLookupError, subprocess.TimeoutExpired, OSError):
            try:
                os.killpg(self.proc.pid, 9)
            except (ProcessLookupError, OSError):
                pass
        for f in (self._stderr_f, self._trace_f):
            try:
                f.close()
            except OSError:
                pass


# ----------------------------------------------------------------------------- text buffer

class Buffer:
    """Client-side copy of the document; all positions are 0-based (line, UTF-16 col)."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.version = 1

    def lines(self) -> list[str]:
        return self.text.split("\n")

    def offset_to_pos(self, off: int) -> dict[str, int]:
        before = self.text[:off]
        line = before.count("\n")
        col_str = before.rsplit("\n", 1)[-1]
        return {"line": line, "character": utf16_len(col_str)}

    def pos_to_offset(self, line: int, character: int) -> int:
        ls = self.lines()
        if line >= len(ls):
            return len(self.text)
        off = sum(len(l) + 1 for l in ls[:line])
        # convert utf16 col → python index
        s = ls[line]
        i = 0
        u = 0
        while i < len(s) and u < character:
            u += 2 if ord(s[i]) > 0xFFFF else 1
            i += 1
        return off + i

    def find_unique(self, needle: str, occurrence: int | None = None, what: str = "anchor") -> int:
        idxs = []
        start = 0
        while True:
            i = self.text.find(needle, start)
            if i < 0:
                break
            idxs.append(i)
            start = i + 1
        if not idxs:
            raise ValueError(f"{what} not found in buffer: {needle!r}")
        if occurrence is not None:
            if occurrence < 0:
                occurrence += len(idxs)
            if occurrence >= len(idxs) or occurrence < 0:
                raise ValueError(f"{what} {needle!r}: occurrence {occurrence} out of {len(idxs)}")
            return idxs[occurrence]
        if len(idxs) > 1:
            raise ValueError(f"{what} is ambiguous ({len(idxs)} occurrences): {needle!r}")
        return idxs[0]

    def apply(self, start: int, end: int, new: str) -> dict[str, Any]:
        """Replace text[start:end] with new; returns the didChange content change."""
        change = {"range": {"start": self.offset_to_pos(start), "end": self.offset_to_pos(end)},
                  "text": new}
        self.text = self.text[:start] + new + self.text[end:]
        self.version += 1
        return change


def utf16_len(s: str) -> int:
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


# ----------------------------------------------------------------------------- session runner

class AssertionFailed(Exception):
    pass


class SessionRun:
    def __init__(self, session: dict[str, Any], sess_path: Path, tc: dict[str, Any], args: argparse.Namespace,
                 rep: int, multi: "MultiRun | None" = None, doc_id: str | None = None) -> None:
        self.session = session
        self.sess_path = sess_path
        self.tc = tc
        self.args = args
        self.rep = rep
        # multi-file mode: the server, the sampler and the raw dir belong to the MultiRun and are
        # shared by every document; `run_op` handles the multi-only ops (open_doc/await_doc/mem)
        self.multi = multi
        self.doc_id = doc_id
        self.worker_pid: int | None = None
        self.events: list[dict[str, Any]] = []
        self.human_pause_s = 0.0
        self.lean_wait_s = 0.0
        self.assert_failures: list[str] = []
        self.notes: list[str] = []
        self.pending_edit: dict[str, Any] | None = None  # last edit not yet synced

    # --- project / file setup
    def prepare(self) -> None:
        s = self.session
        proj_key = s.get("project", "mathlib")
        self.project = PROJECTS.get(proj_key, Path(proj_key)).expanduser()
        if not self.project.is_dir():
            raise SystemExit(f"project dir not found: {self.project}")
        # initial text: inline or from a file relative to sessions dir / project
        if "initial_file" in s:
            src = (self.sess_path.parent / s["initial_file"])
            if not src.exists():
                src = self.project / s["initial_file"]
            text = src.read_text()
        else:
            text = s.get("initial_text", "")
        if isinstance(text, list):
            text = "\n".join(text)
        self.buf = Buffer(text)
        # where the edited file lives: a scratch copy (default — corpus files are never modified;
        # `lake serve` resolves the imports of a file outside the workspace through the
        # workspace's LEAN_PATH exactly as for a file inside it) or, with `edit_in_place`, the
        # corpus file itself (restored afterwards, even on abort)
        if s.get("edit_in_place"):
            target = self.project / s["initial_file"]
            self.original_text = target.read_text()
            self.file = target
            self.restore_file = True
        else:
            d = SCRATCH_DIR / s["name"]
            d.mkdir(parents=True, exist_ok=True)
            self.file = d / s.get("file_name", f"{s['name']}.lean")
            self.file.write_text(text)
            self.restore_file = False
            self.original_text = text
        self.project_toolchain = project_toolchain(self.project)
        self.uri = path_to_uri(self.file)

    # --- server launch
    def attach(self, other: "SessionRun") -> None:
        """Multi-file mode: use another document's already-running server and sampler."""
        for k in ("lsp", "sampler", "env_extra", "init_s", "server_info", "run_dir", "load_before", "run_id"):
            setattr(self, k, getattr(other, k))

    def launch(self) -> None:
        env = dict(os.environ)
        env.pop("LEAN_SYSROOT", None)
        env.pop("LEAN", None)
        env.pop("LAKE", None)
        tcdir = self.tc["dir"]
        if tcdir:
            env["PATH"] = str(Path(tcdir) / "bin") + os.pathsep + env.get("PATH", "")
            env["LEAN_SYSROOT"] = tcdir
            env["ELAN_TOOLCHAIN"] = self.tc["name"]
        if "lazy" in self.tc["name"] or "fork" in self.tc["name"]:
            # Lazy part loading (improvement 4) keeps its per-closure index files here (default would
            # be ~/.cache/lean-lazy-parts); LEAN_LAZY_PARTS=all is the fork's default, set explicitly for the record
            env.setdefault("LEAN_LAZY_PARTS_INDEX_DIR", str(WORK_DIR / "scratch" / "lazy-parts-index"))
            env.setdefault("LEAN_LAZY_PARTS", "all")
        for kv in self.args.env:
            k, _, v = kv.partition("=")
            env[k] = v
        self.env_extra = {k: env[k] for k in ("LEAN_SYSROOT", "ELAN_TOOLCHAIN", "LEAN_LAZY_PARTS", "LEAN_LAZY_PARTS_INDEX_DIR")
                          if k in env} | {kv.partition("=")[0]: kv.partition("=")[2] for kv in self.args.env}
        cmd = [self.tc["lake"], "serve", "--"] + list(self.args.server_arg)
        self.run_dir = _results.raw_dir(self.run_id) / f"{self.session['name']}_r{self.rep}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.load_before = loadavg()
        self.lsp = LeanLsp(cmd, self.project, env, self.run_dir)
        self.sampler = ProcSampler(self.lsp.proc.pid, interval=self.args.sample_interval)
        self.sampler.t0 = self.lsp.t0
        self.sampler.start()
        t = self.lsp.now()
        r = self.lsp.request("initialize", {
            "processId": os.getpid(),
            "rootUri": path_to_uri(self.project),
            "capabilities": {
                "textDocument": {
                    "publishDiagnostics": {"versionSupport": True},
                    "completion": {"completionItem": {"snippetSupport": False}},
                    "hover": {"contentFormat": ["markdown", "plaintext"]},
                },
                "workspace": {"applyEdit": True, "configuration": True},
            },
            "initializationOptions": {"editDelay": 0, "hasWidgets": False},
        }, timeout=120)
        self.lsp.notify("initialized", {})
        self.init_s = self.lsp.now() - t
        self.server_info = r.get("result", {}).get("serverInfo")

    def open_file(self) -> dict[str, Any]:
        t0 = self.lsp.now()
        self.lsp.notify("textDocument/didOpen", {
            "textDocument": {"uri": self.uri, "languageId": "lean", "version": self.buf.version,
                             "text": self.buf.text},
            "dependencyBuildMode": self.args.build_mode,
        })
        ev = {"i": 0, "op": "open", "t": t0, "version": self.buf.version}
        first_progress = None
        # header done := first fileProgress whose first processing line is beyond the header, or done
        hdr_line = header_end_line(self.buf.text)
        timeout = self.args.timeout
        deadline = time.monotonic() + timeout
        body_start_t = None
        with self.lsp._cv:
            while True:
                for p in self.lsp.progress_history:
                    if p["t"] < t0 or p.get("uri") not in (None, self.uri):
                        continue
                    if first_progress is None:
                        first_progress = p["t"]
                    if p["n_ranges"] == 0 or (p["first_line"] is not None and p["first_line"] > hdr_line):
                        body_start_t = p["t"]
                        break
                if body_start_t is not None or self.lsp.dead:
                    break
                if time.monotonic() > deadline:
                    break
                self.lsp._cv.wait(0.5)
        if self.lsp.dead:
            raise LspError("server died during header processing")
        ev["header_s"] = (body_start_t - t0) if body_start_t is not None else None
        ev["first_progress_s"] = (first_progress - t0) if first_progress is not None else None
        self._attribute_worker(ev, t0, body_start_t)
        # then wait for the whole initial version
        done_t = self.sync_version(self.buf.version, t0)
        ev["full_s"] = (done_t - t0) if done_t is not None else None
        ev["lean_wait_s"] = ev["full_s"]
        self.lean_wait_s += ev["full_s"] or 0.0
        self._record_diags(ev)
        ev["worker_rss"] = self._rss_now()
        ev["worker_rss_after_header"] = self._rss_peak(t0, body_start_t) if body_start_t else None
        ev["t_end"] = self.lsp.now()
        ev["load_1m"] = loadavg()["1m"]
        ev["worker_pid"] = self.worker_pid
        self.events.append(ev)
        return ev

    # --- per-document worker attribution and the header split
    def _attribute_worker(self, ev: dict[str, Any], t0: float, body_start_t: float | None) -> None:
        """Find this document's worker process and split the header wait into its
        `lake setup-file` part and its `import` part.

        The `lake setup-file` invocation carries the target .lean path on its command line and
        is a child of the worker, which gives both the worker pid and the wall window of the
        Lake call (sampled by `ps`, so ±one sample interval).  The protocol also marks the end
        of `setup-file` exactly: `FileWorker.setupImports` publishes an *empty* diagnostics list
        for the version as soon as Lake has answered, long before the imports are loaded
        (see docs/interactive/l1-interactive-benchmark.md §3.1) — that is the precise split
        point, and the `ps` window is kept as a cross-check."""
        end = body_start_t if body_start_t is not None else self.lsp.now()
        fpath = str(self.file)
        evs = [x for x in self.sampler.setup_file_events if x[0] >= t0 - 0.5 and fpath in x[3]]
        if not evs:  # fall back to any setup-file sighting in the window (single-file sessions)
            evs = [x for x in self.sampler.setup_file_events if t0 - 0.5 <= x[0] <= end + 1.0]
        if evs:
            ev["lake_setup_file_first_s"] = min(x[0] for x in evs) - t0
            ev["lake_setup_file_visible_s"] = max(x[0] for x in evs) - t0
            ppids = [x[2] for x in evs]
            self.worker_pid = ppids[-1]
        else:
            ev["lake_setup_file_first_s"] = ev["lake_setup_file_visible_s"] = None
        if self.worker_pid is None and self.sampler.worker_pids_seen:
            self.worker_pid = self.sampler.worker_pids_seen[-1]
        empties = [d["t"] for d in self.lsp.diag_history
                   if d.get("uri") in (None, self.uri) and d["n"] == 0 and t0 <= d["t"] <= end]
        ev["setup_done_s"] = (max(empties) - t0) if empties else None
        ev["n_empty_diag_before_body"] = len(empties)
        if ev.get("header_s") is not None and ev["setup_done_s"] is not None:
            ev["import_s"] = ev["header_s"] - ev["setup_done_s"]

    def _rss_now(self) -> int:
        if self.multi is not None and self.worker_pid:
            return self.sampler.worker_rss_pid(self.worker_pid)
        return self.sampler.worker_rss_now()

    def _rss_peak(self, t_from: float = 0.0, t_to: float | None = None) -> int:
        if self.multi is not None and self.worker_pid:
            return self.sampler.peak_pid(self.worker_pid, t_from, t_to)
        return self.sampler.peak(t_from, t_to)

    # --- sync helpers
    def sync_version(self, version: int, t_sent: float) -> float | None:
        """Block until diagnostics for `version` are complete.  Uses both the
        `$/lean/fileProgress` done-notification and `textDocument/waitForDiagnostics`."""
        timeout = self.args.timeout
        done_t = self.lsp.wait_progress_done(self.uri, version, timeout, min_t=t_sent)
        try:
            self.lsp.request("textDocument/waitForDiagnostics", {"uri": self.uri, "version": version},
                             timeout=timeout)
        except LspError as e:
            self.notes.append(f"waitForDiagnostics v{version}: {e}")
        t2 = self.lsp.now()
        # publishDiagnostics for a finished version can trail the done-progress by a few ms
        dt = self.lsp.wait_diag_version(self.uri, version, timeout=2.0)
        if dt is None and self.lsp.diags.get(self.uri) is None:
            self.notes.append(f"no publishDiagnostics for v{version} within 2 s after done")
        if done_t is None:
            self.notes.append(f"fileProgress done for v{version} not seen within {timeout}s")
            return t2
        return max(done_t, min(t2, done_t + 0.05)) if dt is None else max(done_t, dt)

    def _record_diags(self, ev: dict[str, Any]) -> None:
        d = self.lsp.diags.get(self.uri) or {"version": None, "diagnostics": []}
        ds = d["diagnostics"]
        ev["diag_version"] = d["version"]
        ev["errors"] = sum(1 for x in ds if x.get("severity") == 1)
        ev["warnings"] = sum(1 for x in ds if x.get("severity") == 2)
        ev["infos"] = sum(1 for x in ds if x.get("severity", 3) >= 3)
        ev["messages"] = [(x.get("range", {}).get("start", {}).get("line"),
                           x.get("severity"), (x.get("message") or "")[:160].replace("\n", " ")) for x in ds]

    # --- events
    def run_events(self) -> None:
        evs = self.session["events"]
        for i, e in enumerate(evs, start=1):
            op = e.get("op")
            try:
                self.run_event(i, op, e)
            except AssertionFailed as af:
                self.assert_failures.append(f"event {i} ({op}): {af}")
                self.events[-1]["assert_failed"] = str(af)
                if self.args.strict:
                    raise
            if self.lsp.dead:
                raise LspError("server died")

    def _edit(self, i: int, op: str, e: dict[str, Any]) -> None:
        b = self.buf
        if op == "insert" or op == "type":
            text = e["text"] if isinstance(e["text"], str) else "\n".join(e["text"])
            if "append" in e and e["append"]:
                start = len(b.text)
            elif "after" in e:
                start = b.find_unique(e["after"], e.get("occurrence")) + len(e["after"])
            elif "before" in e:
                start = b.find_unique(e["before"], e.get("occurrence"))
            elif "after_line" in e:  # insert a new line after the line containing the anchor
                idx = b.find_unique(e["after_line"], e.get("occurrence"))
                nl = b.text.find("\n", idx)
                start = len(b.text) if nl < 0 else nl + 1
                if nl < 0:
                    text = "\n" + text
            else:
                start = b.pos_to_offset(e["line"], e.get("col", 0))
            end = start
        elif op == "delete":
            if "text" in e:
                start = b.find_unique(e["text"], e.get("occurrence"), "delete text")
                end = start + len(e["text"])
                text = ""
            else:
                start = b.pos_to_offset(e["line"], e.get("col", 0))
                end = b.pos_to_offset(e["end_line"], e.get("end_col", 0))
                text = ""
        elif op == "replace":
            start = b.find_unique(e["find"], e.get("occurrence"), "replace text")
            end = start + len(e["find"])
            text = e["text"] if isinstance(e["text"], str) else "\n".join(e["text"])
        else:
            raise ValueError(op)

        if op == "type":
            # human typing: send the text in chunks (lines by default) with a pause between
            # chunks; Lean gets the whole sequence of didChanges and we measure only the final
            # settle time (each intermediate version is superseded before it finishes).
            mode = e.get("chunks", "lines")
            if mode == "lines":
                parts = text.split("\n")
                chunks = [p + ("\n" if k < len(parts) - 1 else "") for k, p in enumerate(parts)]
            elif mode == "words":
                chunks = re.findall(r"\S+\s*|\s+", text)
            else:
                chunks = [text]
            chunks = [c for c in chunks if c]
            pause = e.get("pause_ms", 400) / 1000.0
            t_first = self.lsp.now()
            off = start
            versions = []
            for k, c in enumerate(chunks):
                ch = b.apply(off, off, c)
                off += len(c)
                self.lsp.notify("textDocument/didChange", {
                    "textDocument": {"uri": self.uri, "version": b.version}, "contentChanges": [ch]})
                versions.append(b.version)
                if k < len(chunks) - 1:
                    time.sleep(pause)
                    self.human_pause_s += pause
            t_sent = self.lsp.now()
            ev = {"i": i, "op": op, "t": t_first, "t_last_change": t_sent, "version": b.version,
                  "n_chunks": len(chunks), "typing_pause_s": pause * (len(chunks) - 1),
                  "chars": len(text)}
            self.pending_edit = {"t_sent": t_sent, "version": b.version, "ev": ev}
        else:
            t_sent = self.lsp.now()
            ch = b.apply(start, end, text)
            self.lsp.notify("textDocument/didChange", {
                "textDocument": {"uri": self.uri, "version": b.version}, "contentChanges": [ch]})
            ev = {"i": i, "op": op, "t": t_sent, "version": b.version, "chars": len(text),
                  "range": ch["range"], "line": ch["range"]["start"]["line"]}
            self.pending_edit = {"t_sent": t_sent, "version": b.version, "ev": ev}
        ev["header_edit"] = ch["range"]["start"]["line"] <= header_end_line(b.text)
        ev["note"] = e.get("note")
        self.events.append(ev)
        if e.get("sync", True):
            self._settle(ev)

    def _settle(self, ev: dict[str, Any], from_pause: bool = False) -> None:
        """Wait for Lean to finish the pending version; attribute waiting time."""
        p = self.pending_edit
        if p is None:
            return
        t_wait_start = self.lsp.now()
        done_t = self.sync_version(p["version"], p["t_sent"])
        t_end = self.lsp.now()
        pev = p["ev"]
        pev["lean_latency_s"] = (done_t - p["t_sent"]) if done_t else None   # edit → fresh diagnostics
        blocked = max(0.0, t_end - t_wait_start)
        pev["blocked_s"] = pev.get("blocked_s", 0.0) + blocked                  # human actually waited
        self.lean_wait_s += blocked
        self._record_diags(pev)
        pev["worker_rss"] = self._rss_now()
        pev["worker_rss_peak_during"] = self._rss_peak(p["t_sent"], t_end)
        pev["t_end"] = t_end
        pev["load_1m"] = loadavg()["1m"]
        pev["n_workers"] = self.sampler.samples[-1]["n_workers"] if self.sampler.samples else None
        pev["worker_pid"] = self.worker_pid or (
            self.sampler.samples[-1]["workers"][0][0] if self.sampler.samples and self.sampler.samples[-1]["workers"] else None)
        if ev is not pev:
            ev["settled_version"] = p["version"]
        self.pending_edit = None

    def run_event(self, i: int, op: str, e: dict[str, Any]) -> None:
        if op in ("insert", "delete", "replace", "type"):
            self._edit(i, op, e)
            return
        t = self.lsp.now()
        ev: dict[str, Any] = {"i": i, "op": op, "t": t, "note": e.get("note")}
        if op == "wait":
            ms = e["ms"]
            time.sleep(ms / 1000.0)
            self.human_pause_s += ms / 1000.0
            ev["human_s"] = ms / 1000.0
            # was Lean already done by the end of the pause?
            p = self.pending_edit
            if p is not None:
                pr = self.lsp.progress.get(self.uri)
                ev["lean_done_during_pause"] = bool(pr and pr["done"] and (pr["version"] or 0) >= p["version"])
            self.events.append(ev)
            return
        if op == "diagnostics":
            self.events.append(ev)
            self._settle(ev)
            ev["blocked_s"] = self.lsp.now() - t
            self._record_diags(ev)
            return
        if op in ("goal", "term_goal", "hover", "completion"):
            pos = self._position(e)
            ev["position"] = pos
            params = {"textDocument": {"uri": self.uri}, "position": pos}
            method = {"goal": "$/lean/plainGoal", "term_goal": "$/lean/plainTermGoal",
                      "hover": "textDocument/hover", "completion": "textDocument/completion"}[op]
            ev["load_1m"] = loadavg()["1m"]
            self.events.append(ev)   # before any assertion, so a failure is attached to this event
            r = self.lsp.request(method, params, timeout=self.args.timeout)
            ev["latency_s"] = self.lsp.now() - t
            self.lean_wait_s += ev["latency_s"]
            res = r.get("result")
            if "error" in r:
                ev["error"] = r["error"]
            if op == "goal":
                goals = (res or {}).get("goals", []) if isinstance(res, dict) else []
                ev["n_goals"] = len(goals)
                ev["goal_head"] = (goals[0][:200] if goals else ((res or {}).get("rendered", "")[:80] if isinstance(res, dict) else None))
                if "expect_goals" in e and len(goals) != e["expect_goals"]:
                    raise AssertionFailed(f"expected {e['expect_goals']} goals, got {len(goals)}: {ev['goal_head']!r}")
                if "expect_contains" in e and not any(e["expect_contains"] in g for g in goals):
                    raise AssertionFailed(f"no goal contains {e['expect_contains']!r}: {goals[:1]!r}")
            elif op == "term_goal":
                ev["has_goal"] = res is not None
            elif op == "hover":
                ev["has_hover"] = res is not None
                if res:
                    c = res.get("contents")
                    ev["hover_head"] = (c.get("value") if isinstance(c, dict) else str(c))[:160]
                if e.get("expect_hover", True) and res is None:
                    raise AssertionFailed("no hover result")
            elif op == "completion":
                items = res.get("items", []) if isinstance(res, dict) else (res or [])
                ev["n_items"] = len(items)
                ev["is_incomplete"] = res.get("isIncomplete") if isinstance(res, dict) else None
                want = e.get("expect_item")
                # Lean labels dotted completions by their last component (`Nat.add_z` → `add_zero`)
                if want and not any(it.get("label") in (want, want.rsplit(".", 1)[-1]) for it in items):
                    raise AssertionFailed(f"completion lacks {e['expect_item']!r} ({len(items)} items)")
                if "expect_min_items" in e and len(items) < e["expect_min_items"]:
                    raise AssertionFailed(f"completion has {len(items)} items < {e['expect_min_items']}")
            return
        if op in ("expect_errors", "expect_no_errors", "expect_message", "expect_warnings", "expect_no_message"):
            self.events.append(ev)
            if self.pending_edit is not None:
                self._settle(ev)
            self._record_diags(ev)
            ds = (self.lsp.diags.get(self.uri) or {}).get("diagnostics", [])
            errs = [d for d in ds if d.get("severity") == 1]
            if op == "expect_errors":
                n = e["n"]
                if isinstance(n, list):
                    ok = n[0] <= len(errs) <= n[1]
                else:
                    ok = len(errs) == n
                if not ok:
                    raise AssertionFailed(f"expected {n} errors, got {len(errs)}: {[m[2][:80] for m in ev['messages'] if m[1]==1]}")
            elif op == "expect_no_errors":
                if errs:
                    raise AssertionFailed(f"expected no errors, got {len(errs)}: {[m[2][:80] for m in ev['messages'] if m[1]==1]}")
            elif op == "expect_warnings":
                n = e["n"]
                ws = [d for d in ds if d.get("severity") == 2]
                if len(ws) != n:
                    raise AssertionFailed(f"expected {n} warnings, got {len(ws)}")
            elif op == "expect_message":
                sub = e["contains"].lower()   # case-insensitive: wording/capitalisation drifts between versions
                if not any(sub in (d.get("message") or "").lower() for d in ds):
                    raise AssertionFailed(f"no diagnostic contains {sub!r}: {[m[2][:80] for m in ev['messages']]}")
            elif op == "expect_no_message":
                sub = e["contains"].lower()
                if any(sub in (d.get("message") or "").lower() for d in ds):
                    raise AssertionFailed(f"a diagnostic contains {sub!r}")
            return
        if op == "save":
            self.file.write_text(self.buf.text)
            self.lsp.notify("textDocument/didSave", {"textDocument": {"uri": self.uri}})
            self.events.append(ev)
            return
        if op == "note":
            self.events.append(ev)
            return
        if self.multi is not None and self.multi.run_op(op, e, ev, self):
            self.events.append(ev)
            return
        raise ValueError(f"unknown op {op!r} at event {i}")

    def _position(self, e: dict[str, Any]) -> dict[str, int]:
        b = self.buf
        if "line" in e:
            return {"line": e["line"], "character": e.get("col", 0)}
        if "after" in e:
            off = b.find_unique(e["after"], e.get("occurrence")) + len(e["after"])
        elif "before" in e:
            off = b.find_unique(e["before"], e.get("occurrence"))
        elif "end_of_line" in e:
            idx = b.find_unique(e["end_of_line"], e.get("occurrence"))
            nl = b.text.find("\n", idx)
            off = len(b.text) if nl < 0 else nl
        else:
            raise ValueError("position needs line/col, after, before or end_of_line")
        return b.offset_to_pos(off)

    # --- finish
    def finish(self, ok_final: bool) -> dict[str, Any]:
        t_end = self.lsp.now()
        self.load_after = loadavg()
        if self.multi is None:      # in multi-file mode the MultiRun owns the server and sampler
            self.sampler.stop()
            self.lsp.close()
        if self.restore_file:
            self.file.write_text(self.original_text)
        worker_bins = sorted(set(self.sampler.worker_cmds.values()))
        edits = [ev for ev in self.events if ev["op"] in ("insert", "delete", "replace", "type")]
        lat = [ev["lean_latency_s"] for ev in edits if ev.get("lean_latency_s") is not None]
        blocked = [ev.get("blocked_s", 0.0) for ev in edits]
        goals = [ev["latency_s"] for ev in self.events if ev["op"] in ("goal", "term_goal") and "latency_s" in ev]
        hovers = [ev["latency_s"] for ev in self.events if ev["op"] == "hover" and "latency_s" in ev]
        comps = [ev["latency_s"] for ev in self.events if ev["op"] == "completion" and "latency_s" in ev]
        open_ev = self.events[0] if self.events and self.events[0]["op"] == "open" else {}
        final = self.events[-1] if self.events else {}
        n_err = None
        for ev in reversed(self.events):
            if "errors" in ev:
                n_err = ev["errors"]
                break
        requests_s = sum(goals) + sum(hovers) + sum(comps)
        open_s = open_ev.get("full_s") or 0.0
        edits_blocked_s = sum(blocked)
        summary = {
            "session": self.session["name"],
            "tier": self.session.get("tier"),
            "project": str(self.project),
            "project_toolchain": self.project_toolchain,
            "toolchain": self.tc["name"],
            "worker_bin": worker_bins,
            "env": self.env_extra,
            "rep": self.rep,
            "ok": ok_final and not self.assert_failures,
            "n_assert_failures": len(self.assert_failures),
            "final_errors": n_err,
            "wall_s": t_end,
            "init_s": self.init_s,
            "header_s": open_ev.get("header_s"),
            "open_full_s": open_ev.get("full_s"),
            "lake_setup_file_visible_s": open_ev.get("lake_setup_file_visible_s"),
            "lake_setup_file_first_s": open_ev.get("lake_setup_file_first_s"),
            # header = worker start-up + `lake setup-file` (setup_done_s) + import (import_s)
            "setup_done_s": open_ev.get("setup_done_s"),
            "import_s": open_ev.get("import_s"),
            "doc_id": self.doc_id,
            "worker_pid": self.worker_pid,
            "file_name": self.file.name,
            "n_edits": len(edits),
            "edit_latency": dist(lat),
            "edit_blocked": dist(blocked),
            "lean_wait_total_s": self.lean_wait_s,
            # where the waiting went: initial open (header + first elaboration), edits the human
            # actually waited on (re-elaboration), and goal/hover/completion round-trips
            "lean_wait_breakdown": {"open_s": open_s, "edits_blocked_s": edits_blocked_s,
                                    "requests_s": requests_s,
                                    "other_s": max(0.0, self.lean_wait_s - open_s - edits_blocked_s - requests_s)},
            "human_pause_total_s": self.human_pause_s,
            "dup_notifications": self.lsp.dup_notifications,
            "n_notifications": len(self.lsp.notifications),
            "goal": dist(goals),
            "hover": dist(hovers),
            "completion": dist(comps),
            "worker_rss_peak_bytes": self._rss_peak(),
            "worker_rss_after_header_bytes": open_ev.get("worker_rss_after_header"),
            "worker_rss_end_bytes": self._rss_now(),
            "worker_restarts": max(0, len(self.sampler.worker_pids_seen) - 1),
            "loadavg_before": self.load_before, "loadavg_after": self.load_after,
        }
        # slowest edits
        slow = sorted((ev for ev in edits if ev.get("lean_latency_s") is not None),
                      key=lambda ev: -ev["lean_latency_s"])[:5]
        summary["slowest_edits"] = [{"i": ev["i"], "op": ev["op"], "line": ev.get("line"),
                                     "lean_latency_s": ev["lean_latency_s"], "note": ev.get("note")} for ev in slow]
        body = {
            "session_file": str(self.sess_path.relative_to(HERE.parent.parent)) if str(self.sess_path).startswith(str(HERE.parent.parent)) else str(self.sess_path),
            "file": str(self.file),
            "server_cmd": self.lsp.cmd,
            "server_info": self.server_info,
            "events": self.events,
            "assert_failures": self.assert_failures,
            "notes": self.notes,
            "progress_history": self.lsp.progress_history[-400:],
            "diag_history": self.lsp.diag_history[-400:],
            "server_requests": self.lsp.server_requests[:50],
            "rss_samples": [[s["t"], s["worker_rss"], s["server_rss"], s["n_workers"]] for s in self.sampler.samples]
            if not self.args.no_samples else [],
            "worker_pids": self.sampler.worker_pids_seen,
            "final_text": self.buf.text,
            "raw_dir": _results.relpath(self.run_dir),
        }
        return {"summary": summary, "body": body}


def header_end_line(text: str) -> int:
    """0-based line index of the last import/module line (−1 if none).  Block comments
    (`/- … -/`, e.g. copyright headers) before or between imports are skipped."""
    last = -1
    in_block = False
    for k, line in enumerate(text.split("\n")):
        s = line.strip()
        if in_block:
            if "-/" in s:
                in_block = False
            continue
        if s.startswith("/-"):
            if "-/" not in s[2:]:
                in_block = True
            continue
        if (s.startswith("import ") or s == "module" or s.startswith("public import ")
                or s.startswith("meta import ") or s.startswith("public meta import ")
                or s.startswith("import all ") or s.startswith("prelude")):
            last = k
        elif s == "" or s.startswith("--") or s.startswith("set_option"):
            continue
        else:
            break
    return last


def project_toolchain(project: Path) -> str | None:
    try:
        return (project / "lean-toolchain").read_text().strip()
    except OSError:
        return None


def toolchain_compatible(tc: dict[str, Any], project: Path) -> bool:
    """Explicit toolchains (stock / fork hybrids, all v4.33.1 binaries) can only serve projects
    whose oleans were built by v4.33.1; other corpus projects must use their own toolchain."""
    if tc["dir"] is None:
        return True
    pt = project_toolchain(project) or ""
    return pt.endswith("v4.33.1")


# ----------------------------------------------------------------------------- driver

def load_session(p: Path) -> dict[str, Any]:
    s = json.loads(p.read_text())
    s.setdefault("name", p.stem)
    return s


def run_session(sess_path: Path, tc: dict[str, Any], args: argparse.Namespace) -> Path | None:
    session = load_session(sess_path)
    run_id = _results.make_run_id("interactive")
    # ensure unique run ids (timestamps have 1 s resolution)
    while (_results.RESULTS_DIR / f"{run_id}.json").exists():
        time.sleep(1)
        run_id = _results.make_run_id("interactive")
    runs = []
    probe = SessionRun(session, sess_path, tc, args, 0)
    probe.prepare()
    if not toolchain_compatible(tc, probe.project):
        log(f"== {session['name']} [{tc['name']}]: SKIPPED — project {probe.project.name} pins "
            f"{probe.project_toolchain}; only its own toolchain ('project') can serve its oleans")
        return None
    for rep in range(args.repeat):
        sr = SessionRun(session, sess_path, tc, args, rep)
        sr.run_id = run_id
        sr.prepare()
        if getattr(args, "prewarm", False):
            pw = prewarm_oleans(sr.project)
            log(f"   prewarm {pw['files']} oleans / {fmt_b(pw['bytes'])} in {pw['s']:.1f} s")
        log(f"== {session['name']} [{tc['name']}] rep {rep}  project={sr.project.name} ({sr.project_toolchain})  "
            f"file={sr.file.name}  load={loadavg()['1m']:.2f}")
        sr.launch()
        ok = True
        try:
            ev = sr.open_file()
            log(f"   open: header {fmt_s(ev.get('header_s'))}  full {fmt_s(ev.get('full_s'))}  "
                f"errors={ev.get('errors')} worker_rss={fmt_b(ev.get('worker_rss'))}")
            sr.run_events()
            if sr.pending_edit is not None:
                sr._settle(sr.events[-1])
            # the final state must be error-free
            d = (sr.lsp.diags.get(sr.uri) or {}).get("diagnostics", [])
            n_err = sum(1 for x in d if x.get("severity") == 1)
            if n_err:
                sr.assert_failures.append(f"final state has {n_err} errors: "
                                          + "; ".join((x.get("message") or "")[:100].replace("\n", " ") for x in d if x.get("severity") == 1)[:600])
        except (LspError, AssertionFailed, ValueError) as e:
            ok = False
            sr.assert_failures.append(f"aborted: {e}")
            log(f"   !! {e}")
        except BaseException:
            # never leave a corpus file modified or a server running
            sr.finish(False)
            raise
        res = sr.finish(ok)
        for f in sr.assert_failures:
            log(f"   assertion: {f}")
        s = res["summary"]
        log(f"   done: wall {fmt_s(s['wall_s'])}  edits {s['n_edits']} med {fmt_s(s['edit_latency'].get('median'))} "
            f"p90 {fmt_s(s['edit_latency'].get('p90'))}  lean-wait {fmt_s(s['lean_wait_total_s'])} "
            f"human {fmt_s(s['human_pause_total_s'])}  peak worker RSS {fmt_b(s['worker_rss_peak_bytes'])}  ok={s['ok']}")
        runs.append(res)
        if args.check and not s["ok"]:
            break
    # aggregate
    summ = aggregate([r["summary"] for r in runs])
    summ["session"] = session["name"]
    summ["tier"] = session.get("tier")
    summ["toolchain"] = tc["name"]
    summ["project"] = str(runs[0]["body"]["file"]) if runs else None
    summ["worker_bin"] = sorted({b for r in runs for b in r["summary"]["worker_bin"]})
    summ["repeat"] = args.repeat
    summ["ok"] = all(r["summary"]["ok"] for r in runs)
    project = SessionRun(session, sess_path, tc, args, 0)
    project.prepare()
    lean_bin = tc["lean"] or worker_lean_bin(summ["worker_bin"]) or "lean"
    lean_info = lean_version(lean_bin)
    lean_info["lake_version_string"] = lake_version(tc["lake"])
    lean_info["elan_toolchain"] = tc["name"]
    lean_info["sysroot"] = tc["dir"]
    lean_info["worker_bin"] = summ["worker_bin"]
    md = _meta.collect_meta(suite="interactive", project=project.project, lean_info=lean_info,
                            tag=args.tag, notes=args.notes, argv=sys.argv)
    md["session"] = session["name"]
    md["tier"] = session.get("tier")
    md["session_description"] = session.get("description")
    md["server_env"] = runs[0]["summary"].get("env") if runs else None
    md["project_toolchain"] = probe.project_toolchain
    md["limitations"] = list(md.get("limitations") or []) + [
        "edit latency floor = server.reportDelayMs (200 ms): the worker's reporter debounces before publishing progress/diagnostics",
        "worker RSS from `ps` includes file-backed (mmapped olean) pages; not private memory",
        "single-file session; the watchdog's other memory (ilean references) is in server_rss, not in worker RSS",
    ]
    body = {"session": {k: v for k, v in session.items() if k != "events"},
            "n_events": len(session["events"]), "runs": [r["body"] | {"summary": r["summary"]} for r in runs]}
    if args.no_write:
        print(render_summary(summ, runs))
        return None
    path = _results.write_result(run_id, "interactive", md, body, summ)
    print(render_summary(summ, runs))
    log(f"   -> {path.relative_to(_meta.REPO_DIR)}")
    return path


# ------------------------------------------------------------------- multi-file mode (several open files)

class MultiRun:
    """One `lake serve`, several documents open at once — what the editor really does when a
    person has three files open.  Each document is an ordinary `SessionRun` sharing the server,
    the process sampler and the raw directory, so every per-event measurement applies per
    worker.  The primary document's script drives the timeline: `open_doc` starts another
    document's open in the background (a person opening a second tab), `await_doc` waits for it,
    `mem` takes a memory probe (`ps` RSS, `vmmap` physical footprint and mapped-file residency
    per worker, plus system-wide `vm_stat`)."""

    def __init__(self, session: dict[str, Any], sess_path: Path, tc: dict[str, Any],
                 args: argparse.Namespace, rep: int) -> None:
        self.session = session
        self.sess_path = sess_path
        self.tc = tc
        self.args = args
        self.rep = rep
        self.docs: list[SessionRun] = []
        self.by_id: dict[str, SessionRun] = {}
        self.threads: dict[str, threading.Thread] = {}
        self.mem_probes: list[dict[str, Any]] = []
        self.open_order: list[str] = []
        self.notes: list[str] = []
        self.doc_spec: dict[str, dict[str, Any]] = {}
        self._olock = threading.Lock()

    def _note_open(self, did: str) -> int:
        with self._olock:
            if did not in self.open_order:
                self.open_order.append(did)
            return self.open_order.index(did) + 1

    def prepare(self) -> None:
        s = self.session
        for k, doc in enumerate(s["docs"]):
            self.doc_spec[doc["id"]] = doc
            sub: dict[str, Any] = {
                "name": f"{s['name']}-{doc['id']}",
                "tier": s.get("tier", "multi"),
                "project": doc.get("project", s.get("project", "mathlib")),
                "description": doc.get("description"),
                "events": (s.get("events", []) if k == 0 else doc.get("events", [])),
            }
            for key in ("initial_text", "initial_file", "file_name", "edit_in_place"):
                if key in doc:
                    sub[key] = doc[key]
            sr = SessionRun(sub, self.sess_path, self.tc, self.args, self.rep, multi=self, doc_id=doc["id"])
            sr.run_id = self.run_id
            sr.prepare()
            self.docs.append(sr)
            self.by_id[doc["id"]] = sr
        self.primary = self.docs[0]
        self.project = self.primary.project
        self.project_toolchain = self.primary.project_toolchain

    def launch(self) -> None:
        self.primary.launch()
        for sr in self.docs[1:]:
            sr.attach(self.primary)
        self.lsp = self.primary.lsp
        self.sampler = self.primary.sampler
        self.run_dir = self.primary.run_dir
        self.load_before = self.primary.load_before
        self.mem_probe("0-workers")

    # --- the multi-only ops, dispatched from SessionRun.run_event
    def run_op(self, op: str, e: dict[str, Any], ev: dict[str, Any], sr: SessionRun) -> bool:
        if op == "open_doc":
            did = e["doc"]
            target = self.by_id[did]
            ev["doc"] = did
            ev["open_index"] = self._note_open(did)
            th = threading.Thread(target=self._doc_thread, args=(target,), daemon=True)
            self.threads[did] = th
            th.start()
            return True
        if op == "await_doc":
            did = e["doc"]
            t = time.perf_counter()
            th = self.threads.get(did)
            if th is not None:
                th.join(timeout=e.get("timeout_s", self.args.timeout))
                if th.is_alive():
                    self.notes.append(f"doc {did} still opening after await_doc timeout")
            ev["doc"] = did
            ev["blocked_s"] = time.perf_counter() - t
            tgt = self.by_id[did]
            oev = tgt.events[0] if tgt.events else {}
            ev["doc_header_s"] = oev.get("header_s")
            ev["doc_open_full_s"] = oev.get("full_s")
            return True
        if op == "mem":
            entry = self.mem_probe(e.get("label") or f"probe-{len(self.mem_probes)}")
            ev["label"] = entry["label"]
            ev["mem_index"] = len(self.mem_probes) - 1
            ev["blocked_s"] = entry["probe_wall_s"]
            ev["n_workers"] = entry["n_workers"]
            return True
        return False

    def _doc_thread(self, sr: SessionRun) -> None:
        try:
            self._note_open(sr.doc_id)
            oev = sr.open_file()
            log(f"   doc {sr.doc_id} ({sr.file.name}): header {fmt_s(oev.get('header_s'))} "
                f"= setup {fmt_s(oev.get('setup_done_s'))} + import {fmt_s(oev.get('import_s'))}; "
                f"full {fmt_s(oev.get('full_s'))}  rss {fmt_b(oev.get('worker_rss'))}  errors={oev.get('errors')}")
            sr.run_events()
            if sr.pending_edit is not None:
                sr._settle(sr.events[-1])
        except (LspError, AssertionFailed, ValueError) as ex:
            sr.assert_failures.append(f"aborted: {ex}")
            log(f"   !! doc {sr.doc_id}: {ex}")

    # --- memory
    def mem_probe(self, label: str) -> dict[str, Any]:
        t0 = self.lsp.now()
        entry: dict[str, Any] = {"label": label, "t": t0, "load_1m": loadavg()["1m"],
                                 "vm_stat": vm_stat(), "workers": []}
        for sr in self.docs:
            if sr.worker_pid is None:
                continue
            w: dict[str, Any] = {"doc": sr.doc_id, "pid": sr.worker_pid, "ps_rss": ps_rss(sr.worker_pid)}
            w.update(vmmap_summary(sr.worker_pid))
            if w.get("ps_rss") is None:
                continue     # worker gone (restart)
            entry["workers"].append(w)
        spids = self.sampler.server_pids_seen
        if spids:
            entry["server"] = {"pid": spids[-1], "ps_rss": ps_rss(spids[-1])} | vmmap_summary(spids[-1])
        entry["n_workers"] = len(entry["workers"])
        entry["sum_ps_rss"] = sum(w["ps_rss"] or 0 for w in entry["workers"])
        entry["sum_phys_footprint"] = sum(w.get("phys_footprint") or 0 for w in entry["workers"])
        entry["sum_mapped_file_resident"] = sum((w.get("mapped_file") or {}).get("resident") or 0
                                                for w in entry["workers"])
        entry["sum_dirty"] = sum((w.get("total") or {}).get("dirty") or 0 for w in entry["workers"])
        entry["probe_wall_s"] = self.lsp.now() - t0
        self.mem_probes.append(entry)
        log(f"   mem[{label}] {entry['n_workers']} worker(s): RSS {fmt_b(entry['sum_ps_rss'])}  "
            f"footprint {fmt_b(entry['sum_phys_footprint'])}  mapped-file {fmt_b(entry['sum_mapped_file_resident'])}  "
            f"system-private {fmt_b(entry['vm_stat'].get('_private_bytes'))}  (probe took {entry['probe_wall_s']:.1f} s)")
        return entry

    # --- run / finish
    def run(self) -> None:
        # docs with `open_at_ms` are opened by a timer relative to the primary's didOpen —
        # the case where a second file is opened *while the first is still importing*
        for sr in self.docs[1:]:
            ms = self.doc_spec[sr.doc_id].get("open_at_ms")
            if ms is not None:
                t = threading.Timer(ms / 1000.0, self._doc_thread, args=(sr,))
                t.daemon = True
                self.threads[sr.doc_id] = t
                t.start()
        self._note_open(self.primary.doc_id)
        ev = self.primary.open_file()
        log(f"   doc {self.primary.doc_id} ({self.primary.file.name}): header {fmt_s(ev.get('header_s'))} "
            f"= setup {fmt_s(ev.get('setup_done_s'))} + import {fmt_s(ev.get('import_s'))}; "
            f"full {fmt_s(ev.get('full_s'))}  rss {fmt_b(ev.get('worker_rss'))}  errors={ev.get('errors')}")
        self.primary.run_events()
        if self.primary.pending_edit is not None:
            self.primary._settle(self.primary.events[-1])
        for did, th in self.threads.items():
            th.join(timeout=self.args.timeout)
            if th.is_alive():
                self.notes.append(f"doc {did} never finished opening")

    def finish(self, ok: bool) -> dict[str, Any]:
        self.load_after = loadavg()
        try:
            self.mem_probe("end")
        except (OSError, LspError):
            pass
        docres = [sr.finish(ok) for sr in self.docs]
        self.sampler.stop()
        self.lsp.close()
        docs_sum = []
        for sr, r in zip(self.docs, docres):
            s = r["summary"]
            oi = self.open_order.index(sr.doc_id) + 1 if sr.doc_id in self.open_order else None
            docs_sum.append({
                "doc": sr.doc_id, "open_index": oi, "file": sr.file.name,
                # only the header's import/module lines — copyright blocks are not part of it
                "header_text": "; ".join(
                    l.strip() for l in sr.buf.text.split("\n")[:header_end_line(sr.buf.text) + 1]
                    if l.strip().startswith(("import ", "public import ", "meta import ",
                                             "public meta import ", "import all ", "prelude"))
                    or l.strip() == "module")[:300],
                "header_s": s["header_s"], "setup_done_s": s["setup_done_s"], "import_s": s["import_s"],
                "lake_setup_file_first_s": s["lake_setup_file_first_s"],
                "lake_setup_file_visible_s": s["lake_setup_file_visible_s"],
                "open_full_s": s["open_full_s"], "worker_pid": s["worker_pid"],
                "worker_rss_after_header_bytes": s["worker_rss_after_header_bytes"],
                "worker_rss_peak_bytes": s["worker_rss_peak_bytes"],
                "worker_rss_end_bytes": s["worker_rss_end_bytes"],
                "n_edits": s["n_edits"], "edit_latency": s["edit_latency"],
                "lean_wait_total_s": s["lean_wait_total_s"], "lean_wait_breakdown": s["lean_wait_breakdown"],
                "goal": s["goal"], "hover": s["hover"], "completion": s["completion"],
                "slowest_edits": s["slowest_edits"],
                "worker_restarts": s["worker_restarts"], "final_errors": s["final_errors"],
                "n_assert_failures": s["n_assert_failures"], "ok": s["ok"],
            })
        base = next((p for p in self.mem_probes if p["label"] == "0-workers"), None)
        mem_table = []
        for p in self.mem_probes:
            row = {"label": p["label"], "t": p["t"], "n_workers": p["n_workers"],
                   "sum_ps_rss": p["sum_ps_rss"], "sum_phys_footprint": p["sum_phys_footprint"],
                   "sum_mapped_file_resident": p["sum_mapped_file_resident"], "sum_dirty": p["sum_dirty"],
                   "server_ps_rss": (p.get("server") or {}).get("ps_rss"),
                   "server_phys_footprint": (p.get("server") or {}).get("phys_footprint"),
                   "system_private": p["vm_stat"].get("_private_bytes"),
                   "system_file_backed": p["vm_stat"].get("_file_backed_bytes"),
                   "probe_wall_s": p["probe_wall_s"], "load_1m": p["load_1m"],
                   "per_worker": [{"doc": w["doc"], "ps_rss": w.get("ps_rss"),
                                   "phys_footprint": w.get("phys_footprint"),
                                   "mapped_file_resident": (w.get("mapped_file") or {}).get("resident"),
                                   "dirty": (w.get("total") or {}).get("dirty"),
                                   "swapped": (w.get("total") or {}).get("swapped")}
                                  for w in p["workers"]]}
            if base:
                row["system_private_delta"] = (row["system_private"] or 0) - (base["vm_stat"].get("_private_bytes") or 0)
                row["system_file_backed_delta"] = (row["system_file_backed"] or 0) - (base["vm_stat"].get("_file_backed_bytes") or 0)
            mem_table.append(row)
        prim = docres[0]["summary"]
        summary = {
            "session": self.session["name"], "kind": "multi", "tier": self.session.get("tier", "multi"),
            "project": str(self.project), "project_toolchain": self.project_toolchain,
            "toolchain": self.tc["name"], "worker_bin": sorted(set(self.sampler.worker_cmds.values())),
            "env": self.primary.env_extra, "rep": self.rep,
            "ok": all(d["ok"] for d in docs_sum) and not self.notes,
            "n_docs": len(self.docs),
            "n_workers_max": max((s["n_workers"] for s in self.sampler.samples), default=0),
            "init_s": self.primary.init_s,
            "header_s_by_open_index": [d["header_s"] for d in sorted(docs_sum, key=lambda d: d["open_index"] or 99)],
            "import_s_by_open_index": [d["import_s"] for d in sorted(docs_sum, key=lambda d: d["open_index"] or 99)],
            "setup_done_s_by_open_index": [d["setup_done_s"] for d in sorted(docs_sum, key=lambda d: d["open_index"] or 99)],
            "same_header": len({d["header_text"] for d in docs_sum}) == 1,
            "docs": docs_sum,
            "mem": mem_table,
            "primary": {"doc": self.primary.doc_id, "edit_latency": prim["edit_latency"],
                        "goal": prim["goal"], "hover": prim["hover"], "completion": prim["completion"],
                        "lean_wait_total_s": prim["lean_wait_total_s"],
                        "slowest_edits": prim["slowest_edits"]},
            "wall_s": max(r["summary"]["wall_s"] for r in docres),
            "loadavg_before": self.load_before, "loadavg_after": self.load_after,
            "prewarm": getattr(self, "prewarm", None),
            "notes": self.notes + [n for sr in self.docs for n in sr.notes],
            "assert_failures": [f"{sr.doc_id}: {f}" for sr in self.docs for f in sr.assert_failures],
        }
        body = {
            "session_file": str(self.sess_path),
            "server_cmd": self.lsp.cmd, "server_info": self.primary.server_info,
            "open_order": self.open_order,
            "mem_probes": self.mem_probes,
            "docs": [{"doc": sr.doc_id, "file": str(sr.file), "events": sr.events,
                      "final_text": sr.buf.text, "assert_failures": sr.assert_failures,
                      "notes": sr.notes, "summary": r["summary"]}
                     for sr, r in zip(self.docs, docres)],
            "rss_samples": [[s["t"], s["worker_rss"], s["server_rss"], s["n_workers"],
                             [list(w) for w in s["workers"]]] for s in self.sampler.samples]
            if not self.args.no_samples else [],
            "setup_file_events": [[round(t, 3), pid, ppid, cmd[:200]]
                                  for (t, pid, ppid, cmd) in self.sampler.setup_file_events],
            "worker_pids": self.sampler.worker_pids_seen,
            "raw_dir": _results.relpath(self.run_dir),
        }
        return {"summary": summary, "body": body}


def run_multi_session(sess_path: Path, tc: dict[str, Any], args: argparse.Namespace) -> Path | None:
    session = load_session(sess_path)
    run_id = _results.make_run_id("interactive-multi")
    while (_results.RESULTS_DIR / f"{run_id}.json").exists():
        time.sleep(1)
        run_id = _results.make_run_id("interactive-multi")
    probe = MultiRun(session, sess_path, tc, args, 0)
    probe.run_id = run_id
    probe.prepare()
    if not toolchain_compatible(tc, probe.project):
        log(f"== {session['name']} [{tc['name']}]: SKIPPED — project {probe.project.name} pins "
            f"{probe.project_toolchain}")
        return None
    runs = []
    for rep in range(args.repeat):
        mr = MultiRun(session, sess_path, tc, args, rep)
        mr.run_id = run_id
        mr.prepare()
        mr.prewarm = prewarm_oleans(mr.project) if args.prewarm else None
        log(f"== {session['name']} [{tc['name']}] rep {rep}  project={mr.project.name}  "
            f"docs={[d.file.name for d in mr.docs]}  load={loadavg()['1m']:.2f}"
            + (f"  prewarm {mr.prewarm['files']} oleans / {fmt_b(mr.prewarm['bytes'])} in {mr.prewarm['s']:.1f} s"
               if mr.prewarm else ""))
        mr.launch()
        ok = True
        try:
            mr.run()
        except (LspError, AssertionFailed, ValueError) as e:
            ok = False
            mr.notes.append(f"aborted: {e}")
            log(f"   !! {e}")
        except BaseException:
            mr.finish(False)
            raise
        res = mr.finish(ok)
        s = res["summary"]
        for f in s["assert_failures"]:
            log(f"   assertion: {f}")
        log(f"   done: headers {[fmt_s(h) for h in s['header_s_by_open_index']]}  "
            f"imports {[fmt_s(h) for h in s['import_s_by_open_index']]}  wall {fmt_s(s['wall_s'])}  ok={s['ok']}")
        runs.append(res)
        if args.check and not s["ok"]:
            break
    summ = aggregate_multi([r["summary"] for r in runs])
    summ |= {"session": session["name"], "kind": "multi", "tier": session.get("tier", "multi"),
             "toolchain": tc["name"], "project": str(probe.project), "repeat": args.repeat,
             "worker_bin": sorted({b for r in runs for b in r["summary"]["worker_bin"]}),
             "ok": all(r["summary"]["ok"] for r in runs)}
    lean_bin = tc["lean"] or worker_lean_bin(summ["worker_bin"]) or "lean"
    lean_info = lean_version(lean_bin)
    lean_info["lake_version_string"] = lake_version(tc["lake"])
    lean_info["elan_toolchain"] = tc["name"]
    lean_info["sysroot"] = tc["dir"]
    lean_info["worker_bin"] = summ["worker_bin"]
    md = _meta.collect_meta(suite="interactive-multi", project=probe.project, lean_info=lean_info,
                            tag=args.tag, notes=args.notes, argv=sys.argv)
    md["session"] = session["name"]
    md["session_description"] = session.get("description")
    md["server_env"] = runs[0]["summary"].get("env") if runs else None
    md["project_toolchain"] = probe.project_toolchain
    md["limitations"] = list(md.get("limitations") or []) + [
        "edit latency floor = server.reportDelayMs (200 ms)",
        "`ps` RSS counts file-backed (mmapped olean) pages shared between workers; the honest "
        "per-worker cost is `vmmap`'s physical footprint, and the honest machine cost is the "
        "delta of vm_stat's anonymous+compressed pages",
        "a `vmmap` probe of a multi-GB worker takes ~1-3 s and is taken only at explicit `mem` "
        "events, never during a latency measurement",
        "worker->document attribution is via the `lake setup-file` child's command line and ppid",
    ]
    body = {"session": {k: v for k, v in session.items() if k != "events"},
            "n_events": len(session.get("events", [])),
            "runs": [r["body"] | {"summary": r["summary"]} for r in runs]}
    if args.no_write:
        print(render_multi(summ, runs))
        return None
    path = _results.write_result(run_id, "interactive-multi", md, body, summ)
    print(render_multi(summ, runs))
    log(f"   -> {path.relative_to(_meta.REPO_DIR)}")
    return path


def _med(vals: list[Any]) -> float | None:
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None


def aggregate_multi(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    """Medians and spread over repeats, per document and per memory probe."""
    n = len(summaries)
    doc_ids = [d["doc"] for d in summaries[0]["docs"]] if summaries else []
    docs = []
    for k, did in enumerate(doc_ids):
        ds = [s["docs"][k] for s in summaries]
        row: dict[str, Any] = {"doc": did, "open_index": ds[0]["open_index"], "file": ds[0]["file"],
                               "header_text": ds[0]["header_text"], "n_runs": n}
        for key in ("header_s", "setup_done_s", "import_s", "open_full_s",
                    "lake_setup_file_first_s", "lake_setup_file_visible_s"):
            vals = [d.get(key) for d in ds if d.get(key) is not None]
            row[key + "_median"] = _med(vals)
            row[key + "_min"] = min(vals) if vals else None
            row[key + "_max"] = max(vals) if vals else None
            row[key + "_all"] = vals
        for key in ("worker_rss_after_header_bytes", "worker_rss_peak_bytes", "worker_rss_end_bytes"):
            row[key + "_median"] = _med([d.get(key) for d in ds])
        row["edit_latency_median"] = _med([d["edit_latency"].get("median") for d in ds])
        row["edit_latency_p90"] = _med([d["edit_latency"].get("p90") for d in ds])
        row["edit_latency_max"] = _med([d["edit_latency"].get("max") for d in ds])
        row["completion_median"] = _med([d["completion"].get("median") for d in ds])
        row["goal_median"] = _med([d["goal"].get("median") for d in ds])
        row["lean_wait_total_s_median"] = _med([d.get("lean_wait_total_s") for d in ds])
        row["n_edits"] = ds[0]["n_edits"]
        row["ok"] = all(d["ok"] for d in ds)
        docs.append(row)
    labels = [m["label"] for m in summaries[0]["mem"]] if summaries else []
    mem = []
    for k, lab in enumerate(labels):
        ms = [s["mem"][k] for s in summaries if k < len(s["mem"]) and s["mem"][k]["label"] == lab]
        if not ms:
            continue
        mem.append({"label": lab, "n_workers": ms[0]["n_workers"],
                    "sum_ps_rss_median": _med([m["sum_ps_rss"] for m in ms]),
                    "sum_phys_footprint_median": _med([m["sum_phys_footprint"] for m in ms]),
                    "sum_mapped_file_resident_median": _med([m["sum_mapped_file_resident"] for m in ms]),
                    "sum_dirty_median": _med([m["sum_dirty"] for m in ms]),
                    "server_ps_rss_median": _med([m["server_ps_rss"] for m in ms]),
                    "server_phys_footprint_median": _med([m["server_phys_footprint"] for m in ms]),
                    "system_private_delta_median": _med([m.get("system_private_delta") for m in ms]),
                    "system_file_backed_delta_median": _med([m.get("system_file_backed_delta") for m in ms]),
                    "per_worker_phys_footprint_median": [
                        _med([(m["per_worker"][j].get("phys_footprint") if j < len(m["per_worker"]) else None)
                              for m in ms]) for j in range(ms[0]["n_workers"])],
                    "per_worker_ps_rss_median": [
                        _med([(m["per_worker"][j].get("ps_rss") if j < len(m["per_worker"]) else None)
                              for m in ms]) for j in range(ms[0]["n_workers"])],
                    "probe_wall_s_median": _med([m["probe_wall_s"] for m in ms])})
    return {"n_runs": n, "docs": docs, "mem": mem,
            "same_header": summaries[0].get("same_header") if summaries else None,
            "header_s_by_open_index_median": [d["header_s_median"] for d in sorted(docs, key=lambda d: d["open_index"] or 99)],
            "import_s_by_open_index_median": [d["import_s_median"] for d in sorted(docs, key=lambda d: d["open_index"] or 99)],
            "wall_s_median": _med([s["wall_s"] for s in summaries]),
            "n_workers_max": max((s["n_workers_max"] for s in summaries), default=0),
            "prewarm": [s.get("prewarm") for s in summaries],
            "loadavg_1m_before": [s["loadavg_before"]["1m"] for s in summaries],
            "loadavg_1m_after": [s["loadavg_after"]["1m"] for s in summaries],
            "primary_edit_latency_median": _med([s["primary"]["edit_latency"].get("median") for s in summaries]),
            "primary_edit_latency_p90": _med([s["primary"]["edit_latency"].get("p90") for s in summaries]),
            "primary_edit_latency_max": _med([s["primary"]["edit_latency"].get("max") for s in summaries]),
            "primary_completion_median": _med([s["primary"]["completion"].get("median") for s in summaries]),
            "primary_goal_median": _med([s["primary"]["goal"].get("median") for s in summaries]),
            "assert_failures": [f for s in summaries for f in s["assert_failures"]],
            "notes": [x for s in summaries for x in s["notes"]]}


def render_multi(summ: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    out = [f"### {summ['session']} — toolchain {summ['toolchain']} — {summ['n_runs']} run(s), "
           f"ok={summ['ok']}, same header={summ['same_header']}", ""]
    out.append("| # | doc | file | header | = setup | + import | open full | header spread (min–max) | worker RSS after hdr |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    for d in sorted(summ["docs"], key=lambda d: d["open_index"] or 99):
        out.append(f"| {d['open_index']} | {d['doc']} | {d['file']} | {fmt_s(d['header_s_median'])} | "
                   f"{fmt_s(d['setup_done_s_median'])} | {fmt_s(d['import_s_median'])} | {fmt_s(d['open_full_s_median'])} | "
                   f"{fmt_s(d['header_s_min'])}–{fmt_s(d['header_s_max'])} | {fmt_b(d['worker_rss_after_header_bytes_median'])} |")
    out.append("")
    out.append("| probe | workers | Σ RSS | Σ physical footprint | Σ mapped-file resident | system private Δ | watchdog RSS |")
    out.append("|---|---|---|---|---|---|---|")
    for m in summ["mem"]:
        out.append(f"| {m['label']} | {m['n_workers']} | {fmt_b(m['sum_ps_rss_median'])} | "
                   f"{fmt_b(m['sum_phys_footprint_median'])} | {fmt_b(m['sum_mapped_file_resident_median'])} | "
                   f"{fmt_b(m['system_private_delta_median'])} | {fmt_b(m['server_ps_rss_median'])} |")
    out.append("")
    out.append(f"primary edit latency median/p90/max: {fmt_s(summ['primary_edit_latency_median'])} / "
               f"{fmt_s(summ['primary_edit_latency_p90'])} / {fmt_s(summ['primary_edit_latency_max'])}; "
               f"goal {fmt_s(summ['primary_goal_median'])}, completion {fmt_s(summ['primary_completion_median'])}; "
               f"wall {fmt_s(summ['wall_s_median'])}; load before {summ['loadavg_1m_before']}")
    if summ["assert_failures"]:
        out.append("")
        out.append("**assertion failures:** " + "; ".join(summ["assert_failures"][:10]))
    if summ["notes"]:
        out.append("notes: " + "; ".join(dict.fromkeys(summ["notes"]))[:500])
    return "\n".join(out)


def cmd_mreport(args: argparse.Namespace) -> None:
    idx = _results.load_index()
    rows = [e for e in idx if e.get("suite") == "interactive-multi"]
    if args.since:
        rows = [e for e in rows if (e.get("timestamp") or "").replace("-", "").replace(":", "") >= args.since]
    if args.tag:
        rows = [e for e in rows if e.get("tag") == args.tag]
    if not rows:
        print("no interactive-multi results")
        return
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for e in rows:
        s = e["summary"]
        latest[(s["session"], s["toolchain"])] = e
    print("## Multi-file results (latest per session x toolchain)\n")
    print("| session | toolchain | runs | same hdr | doc | open # | header (med) | setup | import | spread | RSS | footprint |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for (sname, tc) in sorted(latest):
        s = latest[(sname, tc)]["summary"]
        foot = {m["label"]: m for m in s["mem"]}
        for d in sorted(s["docs"], key=lambda d: d["open_index"] or 99):
            fp = None
            for lab in ("end", f"{d['open_index']}-workers"):
                if lab in foot and (d["open_index"] or 1) <= len(foot[lab].get("per_worker_phys_footprint_median") or []):
                    fp = foot[lab]["per_worker_phys_footprint_median"][(d["open_index"] or 1) - 1]
                    break
            print(f"| {sname} | {tc} | {s['n_runs']} | {s['same_header']} | {d['doc']} | {d['open_index']} | "
                  f"{fmt_s(d['header_s_median'])} | {fmt_s(d['setup_done_s_median'])} | {fmt_s(d['import_s_median'])} | "
                  f"{fmt_s(d['header_s_min'])}–{fmt_s(d['header_s_max'])} | "
                  f"{fmt_b(d['worker_rss_after_header_bytes_median'])} | {fmt_b(fp)} |")
    print("\n## memory probes\n")
    print("| session | toolchain | probe | workers | Σ RSS | Σ footprint | Σ mapped-file | system private Δ | watchdog RSS |")
    print("|---|---|---|---|---|---|---|---|---|")
    for (sname, tc) in sorted(latest):
        s = latest[(sname, tc)]["summary"]
        for m in s["mem"]:
            print(f"| {sname} | {tc} | {m['label']} | {m['n_workers']} | {fmt_b(m['sum_ps_rss_median'])} | "
                  f"{fmt_b(m['sum_phys_footprint_median'])} | {fmt_b(m['sum_mapped_file_resident_median'])} | "
                  f"{fmt_b(m['system_private_delta_median'])} | {fmt_b(m['server_ps_rss_median'])} |")


def worker_lean_bin(bins: list[str]) -> str | None:
    for b in bins:
        p = b.strip().split(" ")[0]
        if Path(p).exists():
            return p
    return None


def aggregate(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    def m(key: str, sub: str | None = None) -> float | None:
        vals = []
        for s in summaries:
            v = s.get(key)
            if sub is not None and isinstance(v, dict):
                v = v.get(sub)
            if v is not None:
                vals.append(v)
        return statistics.fmean(vals) if vals else None

    def mn(key: str, sub: str | None = None) -> float | None:
        vals = []
        for s in summaries:
            v = s.get(key)
            if sub is not None and isinstance(v, dict):
                v = v.get(sub)
            if v is not None:
                vals.append(v)
        return min(vals) if vals else None

    return {
        "n_runs": len(summaries),
        "header_s_mean": m("header_s"), "header_s_min": mn("header_s"),
        "open_full_s_mean": m("open_full_s"),
        "lake_setup_file_visible_s_mean": m("lake_setup_file_visible_s"),
        "edit_latency_median_mean": m("edit_latency", "median"),
        "edit_latency_p90_mean": m("edit_latency", "p90"),
        "edit_latency_max_mean": m("edit_latency", "max"),
        "edit_blocked_sum_mean": m("edit_blocked", "sum"),
        "lean_wait_total_s_mean": m("lean_wait_total_s"),
        "wait_open_s_mean": m("lean_wait_breakdown", "open_s"),
        "wait_edits_blocked_s_mean": m("lean_wait_breakdown", "edits_blocked_s"),
        "wait_requests_s_mean": m("lean_wait_breakdown", "requests_s"),
        "human_pause_total_s_mean": m("human_pause_total_s"),
        "dup_notifications_mean": m("dup_notifications"),
        "n_notifications_mean": m("n_notifications"),
        "goal_median_mean": m("goal", "median"), "goal_max_mean": m("goal", "max"),
        "hover_median_mean": m("hover", "median"),
        "completion_median_mean": m("completion", "median"), "completion_max_mean": m("completion", "max"),
        "worker_rss_peak_bytes_mean": m("worker_rss_peak_bytes"),
        "worker_rss_after_header_bytes_mean": m("worker_rss_after_header_bytes"),
        "worker_rss_end_bytes_mean": m("worker_rss_end_bytes"),
        "wall_s_mean": m("wall_s"),
        "n_edits": summaries[0]["n_edits"] if summaries else 0,
        "final_errors": summaries[-1]["final_errors"] if summaries else None,
        "worker_restarts_max": max((s.get("worker_restarts", 0) for s in summaries), default=0),
        "loadavg_1m_mean": m("loadavg_before", "1m"),
        "per_run": [{"rep": s["rep"], "ok": s["ok"], "header_s": s["header_s"], "wall_s": s["wall_s"],
                     "edit_latency_median": s["edit_latency"].get("median"), "edit_latency_p90": s["edit_latency"].get("p90"),
                     "lean_wait_total_s": s["lean_wait_total_s"], "worker_rss_peak_bytes": s["worker_rss_peak_bytes"],
                     "loadavg_1m": s["loadavg_before"]["1m"], "n_assert_failures": s["n_assert_failures"]}
                    for s in summaries],
    }


# ----------------------------------------------------------------------------- rendering

def fmt_s(v: float | None) -> str:
    if v is None:
        return "–"
    if v < 1:
        return f"{v * 1000:.0f} ms"
    return f"{v:.2f} s"


def fmt_b(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{v / 2**30:.2f} GB" if v >= 2**30 else f"{v / 2**20:.0f} MB"


def render_summary(summ: dict[str, Any], runs: list[dict[str, Any]]) -> str:
    out = [f"### {summ['session']} ({summ['tier']}) — toolchain {summ['toolchain']} — {summ['n_runs']} run(s), ok={summ['ok']}", ""]
    out.append("| metric | value |")
    out.append("|---|---|")
    rows = [
        ("header time (open → body starts elaborating)", fmt_s(summ["header_s_mean"])),
        ("open → whole initial file done", fmt_s(summ["open_full_s_mean"])),
        ("`lake setup-file` visible until", fmt_s(summ["lake_setup_file_visible_s_mean"])),
        ("edits", str(summ["n_edits"])),
        ("edit latency median / p90 / max", f"{fmt_s(summ['edit_latency_median_mean'])} / {fmt_s(summ['edit_latency_p90_mean'])} / {fmt_s(summ['edit_latency_max_mean'])}"),
        ("time waiting for Lean (total)", fmt_s(summ["lean_wait_total_s_mean"])),
        ("  of which: initial open / edits blocked / goal+hover+completion",
         f"{fmt_s(summ.get('wait_open_s_mean'))} / {fmt_s(summ.get('wait_edits_blocked_s_mean'))} / {fmt_s(summ.get('wait_requests_s_mean'))}"),
        ("human pause time (total)", fmt_s(summ["human_pause_total_s_mean"])),
        ("goal request median / max", f"{fmt_s(summ['goal_median_mean'])} / {fmt_s(summ['goal_max_mean'])}"),
        ("hover median", fmt_s(summ["hover_median_mean"])),
        ("completion median / max", f"{fmt_s(summ['completion_median_mean'])} / {fmt_s(summ['completion_max_mean'])}"),
        ("worker RSS after header / peak / end", f"{fmt_b(summ['worker_rss_after_header_bytes_mean'])} / {fmt_b(summ['worker_rss_peak_bytes_mean'])} / {fmt_b(summ['worker_rss_end_bytes_mean'])}"),
        ("session wall", fmt_s(summ["wall_s_mean"])),
        ("worker binary", ", ".join(summ["worker_bin"]) or "?"),
        ("worker restarts", str(summ["worker_restarts_max"])),
        ("final errors", str(summ["final_errors"])),
    ]
    for k, v in rows:
        out.append(f"| {k} | {v} |")
    out.append("")
    # per-event table for the first run
    if runs:
        out.append("| # | op | line | note | Lean latency | blocked | err/warn | worker RSS |")
        out.append("|---|---|---|---|---|---|---|---|")
        for ev in runs[0]["body"]["events"]:
            lat = ev.get("lean_latency_s", ev.get("latency_s", ev.get("full_s")))
            if ev["op"] == "wait":
                lat = None
            note = (ev.get("note") or "")[:60]
            ew = f"{ev.get('errors', '')}/{ev.get('warnings', '')}" if "errors" in ev else ""
            extra = ""
            if ev["op"] == "goal":
                extra = f" goals={ev.get('n_goals')}"
            if ev["op"] == "completion":
                extra = f" items={ev.get('n_items')}"
            if ev["op"] == "wait":
                extra = f" {ev['human_s']:.1f}s" + (" (Lean finished during pause)" if ev.get("lean_done_during_pause") else "")
            if ev.get("assert_failed"):
                extra += f" **ASSERT FAILED: {ev['assert_failed'][:80]}**"
            out.append(f"| {ev['i']} | {ev['op']}{extra} | {ev.get('line', '')} | {note} | {fmt_s(lat)} | "
                       f"{fmt_s(ev.get('blocked_s')) if ev.get('blocked_s') is not None else ''} | {ew} | {fmt_b(ev.get('worker_rss')) if ev.get('worker_rss') else ''} |")
        out.append("")
    return "\n".join(out)


def cmd_report(args: argparse.Namespace) -> None:
    idx = _results.load_index()
    rows = [e for e in idx if e.get("suite") == "interactive"]
    if args.since:
        rows = [e for e in rows if (e.get("timestamp") or "").replace("-", "").replace(":", "") >= args.since]
    if args.tag:
        rows = [e for e in rows if e.get("tag") == args.tag]
    if not rows:
        print("no interactive results")
        return
    # latest per (session, toolchain)
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for e in rows:
        s = e["summary"]
        latest[(s["session"], s["toolchain"])] = e
    tcs = sorted({k[1] for k in latest}, key=lambda t: (t != STOCK_TOOLCHAIN, t))
    sessions = sorted({k[0] for k in latest})
    tiers = {}
    for e in latest.values():
        tiers[e["summary"]["session"]] = e["summary"].get("tier")
    print(f"## interactive results (latest per session × toolchain; {len(latest)} entries)\n")
    print("| session | tier | toolchain | ok | header | open full | edit med | edit p90 | edit max | Lean wait | human | goal med | compl med | worker RSS hdr/peak | wall | load |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for sname in sorted(sessions, key=lambda n: ({"novice": 0, "intermediate": 1, "advanced": 2}.get(tiers.get(n), 9), n)):
        for tc in tcs:
            e = latest.get((sname, tc))
            if not e:
                continue
            s = e["summary"]
            print(f"| {sname} | {s.get('tier')} | {tc} | {'yes' if s['ok'] else 'NO'} | {fmt_s(s['header_s_mean'])} | {fmt_s(s['open_full_s_mean'])} | "
                  f"{fmt_s(s['edit_latency_median_mean'])} | {fmt_s(s['edit_latency_p90_mean'])} | {fmt_s(s['edit_latency_max_mean'])} | "
                  f"{fmt_s(s['lean_wait_total_s_mean'])} | {fmt_s(s['human_pause_total_s_mean'])} | {fmt_s(s['goal_median_mean'])} | "
                  f"{fmt_s(s['completion_median_mean'])} | {fmt_b(s['worker_rss_after_header_bytes_mean'])}/{fmt_b(s['worker_rss_peak_bytes_mean'])} | "
                  f"{fmt_s(s['wall_s_mean'])} | {s.get('loadavg_1m_mean', 0) or 0:.1f} |")
    # per tier × toolchain aggregate
    print("\n## per tier × toolchain (means over sessions)\n")
    print("| tier | toolchain | sessions | header | edit med | edit p90 | Lean wait / session | of which open / edits / requests | human / session | goal med | peak worker RSS | wall / session |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for tier in ("novice", "intermediate", "advanced"):
        for tc in tcs:
            es = [e["summary"] for (sn, t), e in latest.items() if t == tc and tiers.get(sn) == tier]
            if not es:
                continue

            def mm(k: str) -> float | None:
                v = [x[k] for x in es if x.get(k) is not None]
                return statistics.fmean(v) if v else None
            print(f"| {tier} | {tc} | {len(es)} | {fmt_s(mm('header_s_mean'))} | {fmt_s(mm('edit_latency_median_mean'))} | "
                  f"{fmt_s(mm('edit_latency_p90_mean'))} | {fmt_s(mm('lean_wait_total_s_mean'))} | "
                  f"{fmt_s(mm('wait_open_s_mean'))} / {fmt_s(mm('wait_edits_blocked_s_mean'))} / {fmt_s(mm('wait_requests_s_mean'))} | "
                  f"{fmt_s(mm('human_pause_total_s_mean'))} | "
                  f"{fmt_s(mm('goal_median_mean'))} | {fmt_b(mm('worker_rss_peak_bytes_mean'))} | {fmt_s(mm('wall_s_mean'))} |")


def cmd_show(args: argparse.Namespace) -> None:
    doc = json.loads(Path(args.file).read_text())
    runs = [{"body": r, "summary": r["summary"]} for r in doc["runs"]]
    print(render_summary(doc["summary"], runs))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run session scripts")
    r.add_argument("--session", action="append", default=[], help="session JSON (repeatable)")
    r.add_argument("--all", action="store_true", help="all sessions in --sessions-dir")
    r.add_argument("--tier", action="append", default=[], help="with --all: only these tiers")
    r.add_argument("--sessions-dir", type=Path, default=SESSIONS_DIR)
    r.add_argument("--toolchain", action="append", default=[],
                   help="'project' (elan, default), 'stock', a name under ~/.elan/toolchains or ~/lean-work/toolchains, or a dir; repeatable")
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--build-mode", default="never", choices=["never", "once", "always"],
                   help="dependencyBuildMode sent with didOpen (default never: oleans must exist)")
    r.add_argument("--timeout", type=float, default=900.0, help="per-wait timeout (s)")
    r.add_argument("--sample-interval", type=float, default=0.25)
    r.add_argument("--no-samples", action="store_true")
    r.add_argument("--strict", action="store_true", help="abort a session at the first failed assertion")
    r.add_argument("--check", action="store_true", help="validation mode: stop repeating a session once it fails")
    r.add_argument("--no-write", action="store_true", help="do not write result files / index")
    r.add_argument("--prewarm", action="store_true",
                   help="read every .olean of the project (and its Lake packages) before each run "
                        "so the file cache is warm; worth ~20 s of header on a cold cache")
    r.add_argument("--env", action="append", default=[], metavar="KEY=VAL",
                   help="extra environment for the server (e.g. LEAN_LAZY_PARTS=0); repeatable")
    r.add_argument("--server-arg", action="append", default=[], metavar="ARG",
                   help="extra argument passed to `lean --server` after `lake serve --` (e.g. --load-dynlib=LIB); repeatable")
    r.add_argument("--project-dir", action="append", default=[], metavar="KEY=PATH",
                   help="override a PROJECTS entry (e.g. mathlib=~/lean-work/mathlib4-precomp); repeatable")
    r.add_argument("--tag")
    r.add_argument("--notes")
    p = sub.add_parser("report", help="comparison tables from index.json")
    p.add_argument("--since", help="UTC timestamp prefix, e.g. 20260825T06")
    p.add_argument("--tag")
    mp = sub.add_parser("mreport", help="multi-file comparison tables from index.json")
    mp.add_argument("--since")
    mp.add_argument("--tag")
    s = sub.add_parser("show", help="markdown summary of one result file")
    s.add_argument("file")
    args = ap.parse_args(argv)
    if args.cmd == "report":
        cmd_report(args)
        return
    if args.cmd == "mreport":
        cmd_mreport(args)
        return
    if args.cmd == "show":
        cmd_show(args)
        return
    sessions = [Path(x) for x in args.session]
    if args.all:
        for f in sorted(args.sessions_dir.glob("*.json")):
            sess = load_session(f)
            if args.tier and sess.get("tier") not in args.tier:
                continue
            sessions.append(f)
    if not sessions:
        raise SystemExit("no sessions (use --session or --all)")
    for kv in getattr(args, "project_dir", []):
        k, _, v = kv.partition("=")
        PROJECTS[k] = Path(v).expanduser()
    tcs = [resolve_toolchain(t) for t in (args.toolchain or [None])]
    for tc in tcs:
        for sp in sessions:
            if load_session(sp).get("kind") == "multi":
                run_multi_session(sp, tc, args)
            else:
                run_session(sp, tc, args)


if __name__ == "__main__":
    main()
