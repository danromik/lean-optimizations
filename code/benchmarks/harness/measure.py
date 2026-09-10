"""Measurement core: run a command and record time, CPU, memory and fault counters.

Two independent sources are combined for every run:

* ``os.wait4`` rusage of the direct child (which, per POSIX, includes every
  descendant the child has waited for -- e.g. the ``lean`` workers spawned by
  ``lake build``).  This gives user/sys CPU, max RSS, minor/major page faults and
  voluntary/involuntary context switches.  ``ru_maxrss`` is bytes on macOS and
  KiB on Linux; we normalise to bytes.  (We prefer ``wait4`` over
  ``getrusage(RUSAGE_CHILDREN)`` deltas because the latter's ``ru_maxrss`` is a
  lifetime *max* over all children ever reaped by this Python process, so it
  cannot be attributed to one run; the cumulative counters are identical.)

* A sampler thread that polls ``ps`` every ``sample_interval`` seconds, walks
  the process tree rooted at the child (plus every process in its process
  group) and sums RSS.  That yields a memory-over-time series, the "peak tree
  RSS" (what ``ru_maxrss`` cannot give for a tree of concurrent processes) and
  a census of the processes observed.

Timeouts kill the whole process group / tree (SIGTERM, then SIGKILL).
"""
from __future__ import annotations

import os
import resource
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

IS_DARWIN = sys.platform == "darwin"


@dataclass
class Sample:
    t: float          # seconds since process start
    rss_bytes: int    # sum of RSS over the process tree
    nprocs: int       # processes in the tree at this instant
    nlean: int        # of which are `lean` binaries

    def as_list(self) -> list:
        return [round(self.t, 4), self.rss_bytes, self.nprocs, self.nlean]


@dataclass
class Measurement:
    cmd: list[str]
    cwd: str
    wall_s: float
    user_s: float
    sys_s: float
    cpu_util: float               # (user+sys)/wall
    maxrss_bytes: int             # rusage ru_maxrss (largest single process in the waited tree)
    minflt: int
    majflt: int
    nvcsw: int
    nivcsw: int
    inblock: int
    oublock: int
    exit_code: int | None
    timed_out: bool
    tree_rss_peak_bytes: int      # max over samples of summed tree RSS
    tree_rss_peak_t: float        # time (s) at which that peak was observed
    tree_rss_exit_bytes: int      # last sample before exit
    procs_seen: int               # distinct pids observed in the tree
    lean_procs_seen: int          # distinct pids whose comm is `lean`
    max_concurrent_procs: int
    max_concurrent_lean: int
    comm_census: dict[str, int]   # distinct pids by command basename
    sample_interval_s: float
    n_samples: int
    samples: list[list] = field(default_factory=list)  # [t, rss_bytes, nprocs, nlean]
    stdout: str = ""
    stderr: str = ""
    stdout_bytes: int = 0
    stderr_bytes: int = 0
    stdout_path: str | None = None
    stderr_path: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------- ps

def _ps_snapshot() -> list[tuple[int, int, int, int, str]]:
    """Return [(pid, ppid, pgid, rss_kb, comm)] for all processes."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,pgid=,rss=,comm="],
            capture_output=True, text=True, check=False,
        ).stdout
    except OSError:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 4:
            continue
        try:
            pid, ppid, pgid, rss = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        except ValueError:
            continue
        comm = parts[4] if len(parts) > 4 else ""
        rows.append((pid, ppid, pgid, rss, comm))
    return rows


def tree_pids(rows, root_pid: int, pgid: int | None) -> set[int]:
    """All descendants of root_pid (inclusive) plus members of pgid."""
    children: dict[int, list[int]] = {}
    for pid, ppid, _pg, _rss, _c in rows:
        children.setdefault(ppid, []).append(pid)
    seen: set[int] = set()
    stack = [root_pid]
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        stack.extend(children.get(p, []))
    if pgid is not None:
        for pid, _pp, pg, _rss, _c in rows:
            if pg == pgid:
                seen.add(pid)
    return seen


def _basename(comm: str) -> str:
    return comm.rsplit("/", 1)[-1]


def kill_tree(root_pid: int, pgid: int | None, extra_pids: set[int] | None = None,
              grace_s: float = 3.0) -> None:
    """SIGTERM then SIGKILL the process group, the tree and any remembered pids."""
    targets: set[int] = set(extra_pids or ())
    targets |= tree_pids(_ps_snapshot(), root_pid, pgid)
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if pgid is not None:
            try:
                os.killpg(pgid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        for pid in targets:
            try:
                os.kill(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
        deadline = time.time() + grace_s
        while time.time() < deadline:
            alive = {pid for pid in targets if _alive(pid)}
            if not alive:
                return
            time.sleep(0.1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ------------------------------------------------------------------- measuring

def run_measured(
    cmd: Sequence[str],
    *,
    cwd: str | os.PathLike,
    env: Mapping[str, str] | None = None,
    timeout_s: float = 1800.0,
    sample_interval_s: float = 0.2,
    keep_samples: bool = True,
    stdout_path: str | os.PathLike | None = None,
    stderr_path: str | os.PathLike | None = None,
    text_limit: int = 4000,
    stdin_text: str | None = None,
) -> Measurement:
    """Run *cmd* and measure it.  Never raises for a failing command."""
    cmd = [str(c) for c in cmd]
    cwd = str(cwd)
    run_env = dict(os.environ if env is None else env)

    out_f = open(stdout_path, "wb") if stdout_path else subprocess.DEVNULL
    err_f = open(stderr_path, "wb") if stderr_path else subprocess.DEVNULL
    try:
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            cmd, cwd=cwd, env=run_env,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=out_f, stderr=err_f,
            start_new_session=True,  # own process group => clean kills
        )
    finally:
        for f in (out_f, err_f):
            if f not in (subprocess.DEVNULL,):
                f.close()
    if stdin_text is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_text.encode())
        except BrokenPipeError:
            pass
        proc.stdin.close()

    pgid = proc.pid
    state = {
        "samples": [], "seen": set(), "lean_seen": set(), "census": {},
        "max_conc": 0, "max_conc_lean": 0, "timed_out": False, "stop": False,
    }

    def sampler() -> None:
        while not state["stop"]:
            now = time.perf_counter() - t0
            if now > timeout_s and not state["timed_out"]:
                state["timed_out"] = True
                kill_tree(proc.pid, pgid, state["seen"])
                return
            rows = _ps_snapshot()
            pids = tree_pids(rows, proc.pid, pgid)
            rss = 0
            n = 0
            nlean = 0
            for pid, _pp, _pg, rss_kb, comm in rows:
                if pid in pids:
                    base = _basename(comm)
                    n += 1
                    rss += rss_kb * 1024
                    if pid not in state["seen"]:
                        state["seen"].add(pid)
                        state["census"][base] = state["census"].get(base, 0) + 1
                    if base == "lean":
                        nlean += 1
                        state["lean_seen"].add(pid)
            if n:
                state["samples"].append(Sample(now, rss, n, nlean))
                state["max_conc"] = max(state["max_conc"], n)
                state["max_conc_lean"] = max(state["max_conc_lean"], nlean)
            # sleep the remainder of the interval (ps itself costs ~10-30 ms)
            elapsed = (time.perf_counter() - t0) - now
            time.sleep(max(0.0, sample_interval_s - elapsed))

    th = threading.Thread(target=sampler, name="rss-sampler", daemon=True)
    th.start()

    # If the harness itself is interrupted/terminated, take the child tree down with it.
    def _forward(signum, _frame):
        kill_tree(proc.pid, pgid, state["seen"], grace_s=2.0)
        raise KeyboardInterrupt(f"signal {signum}")

    old_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            old_handlers[sig] = signal.signal(sig, _forward)
        except ValueError:  # not in main thread
            pass
    try:
        try:
            _pid, status, ru = os.wait4(proc.pid, 0)
        except ChildProcessError:  # already reaped (should not happen)
            status, ru = proc.wait(), resource.getrusage(resource.RUSAGE_CHILDREN)
    finally:
        for sig, h in old_handlers.items():
            signal.signal(sig, h)
    wall = time.perf_counter() - t0
    state["stop"] = True
    th.join(timeout=5.0)
    exit_code = os.waitstatus_to_exitcode(status) if isinstance(status, int) else status
    proc.returncode = exit_code  # let Popen know it has been reaped

    # Belt and braces: nothing from the tree may survive the run.
    leftovers = {p for p in state["seen"] if p != proc.pid and _alive(p)}
    if leftovers:
        kill_tree(proc.pid, pgid, leftovers, grace_s=2.0)

    maxrss = ru.ru_maxrss if IS_DARWIN else ru.ru_maxrss * 1024
    samples: list[Sample] = state["samples"]
    if samples:
        peak = max(samples, key=lambda s: s.rss_bytes)
        peak_bytes, peak_t, exit_bytes = peak.rss_bytes, peak.t, samples[-1].rss_bytes
    else:
        peak_bytes, peak_t, exit_bytes = 0, 0.0, 0

    def _read(path, limit) -> tuple[str, int]:
        if not path or not os.path.exists(path):
            return "", 0
        data = Path(path).read_bytes()
        text = data.decode("utf-8", errors="replace")
        if len(text) > limit:
            text = text[: limit // 2] + f"\n... [truncated {len(text) - limit} chars] ...\n" + text[-limit // 2:]
        return text, len(data)

    so, so_n = _read(stdout_path, text_limit)
    se, se_n = _read(stderr_path, text_limit)
    return Measurement(
        cmd=cmd, cwd=cwd,
        wall_s=wall, user_s=ru.ru_utime, sys_s=ru.ru_stime,
        cpu_util=(ru.ru_utime + ru.ru_stime) / wall if wall > 0 else 0.0,
        maxrss_bytes=int(maxrss),
        minflt=ru.ru_minflt, majflt=ru.ru_majflt,
        nvcsw=ru.ru_nvcsw, nivcsw=ru.ru_nivcsw,
        inblock=ru.ru_inblock, oublock=ru.ru_oublock,
        exit_code=exit_code, timed_out=bool(state["timed_out"]),
        tree_rss_peak_bytes=peak_bytes, tree_rss_peak_t=peak_t, tree_rss_exit_bytes=exit_bytes,
        procs_seen=len(state["seen"]), lean_procs_seen=len(state["lean_seen"]),
        max_concurrent_procs=state["max_conc"], max_concurrent_lean=state["max_conc_lean"],
        comm_census=dict(sorted(state["census"].items())),
        sample_interval_s=sample_interval_s, n_samples=len(samples),
        samples=[s.as_list() for s in samples] if keep_samples else [],
        stdout=so, stderr=se, stdout_bytes=so_n, stderr_bytes=se_n,
        stdout_path=str(stdout_path) if stdout_path else None,
        stderr_path=str(stderr_path) if stderr_path else None,
    )


def read_text(path: str | os.PathLike | None) -> str:
    """Full text of a captured stdout/stderr file (untruncated)."""
    if not path or not os.path.exists(path):
        return ""
    return Path(path).read_text(encoding="utf-8", errors="replace")
