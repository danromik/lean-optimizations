#!/usr/bin/env python3
"""leansnap — a validated, header-keyed snapshot layer over Lean's experimental
`--incr-header-save` / `--incr-load` (Lean 4 v4.32+; developed against v4.33.1).

Demonstrator for sandbox pipelines (one `lean` process per check, every check paying a full
`import Mathlib`). Python 3.8+, stdlib only.

    leansnap save --header hdr.lean [--out DIR] [-- -Dopt=val ...]   build a snapshot for that header (+options)
    leansnap run  file.lean [-- lean args...]          run lean on file.lean, using a snapshot if valid
                                                       (`-D` options are part of the key: lean ignores
                                                       them at load time, the snapshot's options win)
    leansnap check file.lean                           print the key, snapshot status and check timings
    leansnap key  file.lean                            print the lookup key only

Environment / options (options win):
    LEANSNAP_LEAN       path of the `lean` binary            (--lean;   default: `lean` on PATH)
    LEAN_PATH           passed through to lean               (--lean-path)
    LEANSNAP_DIR        snapshot directory                   (--dir;    default ~/.cache/leansnap)
    LEANSNAP_CHECK      dep validation mode                  (--check;  stat | lakehash | trace | none)
    LEANSNAP_BINID      binary-identity strength in the key  (--binid;  quick | full | path)
    LEANSNAP_ENV        extra config env vars for the key    (--env;    comma-separated names)
    LEANSNAP_VERBOSE=1  log decisions to stderr              (--verbose)

Mechanism. Lean's loader trusts a snapshot completely: the snapshot is a compacted region whose
pointers refer to the 52k dependency region files (`.olean`, `.olean.server`, `.olean.private`,
`.ir.sig`, `.ir`) *by address*, and the `.deps` sidecar lists their absolute paths. Nothing checks
that those files still have the content they had at save time (same `base_addr`, since it is a hash
of the module name — a rebuilt olean lands at the same address with different bytes). The layer
therefore has two parts:

  1. a LOOKUP KEY computed from the request alone, before anything is loaded:
         sha256(lean --version line, BINARY IDENTITY, CONFIGURATION SWITCHES, LEAN_PATH,
                exact header text of the file, -D options)
     The header text must be byte-identical (Lean compares the header syntax *with source
     positions* and silently falls back to a full import otherwise).

     BINARY IDENTITY is the resolved path, size and a quick content digest (first and last
     64 KiB) of the `lean` executable *and* of the `libleanshared` it loads. It is in the key
     because a snapshot can only be loaded by the binary that saved it, and because the
     `--version` line does not distinguish a fork build from stock: every fork branch of this
     project reports the same `Lean (version 4.33.1, ..., commit 819816b2..., Release)` line, so
     without this the stock and fork snapshots of the same header share one filename and silently
     overwrite each other.

     CONFIGURATION SWITCHES are the `LEAN_*` environment variables in `CONFIG_ENV_VARS` that
     change what is imported or how (`LEAN_LAZY_PARTS`, `LEAN_TACTIC_INDEX`, `LEAN_SEARCH_INDEX`, ...). A
     `LEAN_LAZY_PARTS=all` snapshot has different contents (400.7 MB / 10,498 deps) from a `LEAN_LAZY_PARTS=0`
     one (310.1 MB / 52,490 deps) built from the same header; before this they shared a key.
     Add more with `LEANSNAP_ENV=NAME1,NAME2`.

     Both are *also* recorded in the manifest and re-checked before every load (§2), so even a
     hash collision or a hand-copied snapshot directory falls back to a plain run instead of
     handing a snapshot to a binary or a configuration that cannot use it;
  2. a VALIDITY MANIFEST written at save time and re-checked before every load, covering every
     dependency region file listed in the snapshot's `.deps` plus the runtime library:
         stat     size + mtime_ns + inode of each file (default; ~50k stats)
         lakehash Lake's `<file>.hash` sidecar content for each file (falls back to stat for files
                  without one, e.g. the toolchain's own oleans)
         trace    the `.trace` `depHash` of each directly imported module (transitive under Lake's
                  discipline; a handful of reads) + stat of the toolchain oleans
         none     trust the snapshot (what bare `--incr-load` does)

On a miss or a failed check the file is run with plain `lean` and the same arguments; stdout,
stderr and the exit code are passed through unchanged in both cases.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HEADER_LINE = re.compile(r'^\s*(module|prelude|(public\s+|meta\s+|private\s+)*import)\b')

# `LEAN_*` variables that change *what* is imported, or the shape of the imported environment, and
# therefore what a snapshot contains and whether it can be loaded at all. Verbosity/timing/thread
# knobs are deliberately not here: they do not change the environment. Extend with `LEANSNAP_ENV`.
CONFIG_ENV_VARS = (
    'LEAN_LAZY_PARTS', 'LEAN_LAZY_PARTS_INDEX_DIR',
    'LEAN_SEARCH_INDEX', 'LEAN_SEARCH_INDEX_CACHE_DIR', 'LEAN_SEARCH_INDEX_RECORD',
    'LEAN_TACTIC_INDEX', 'LEAN_TACTIC_INDEX_DIR', 'LEAN_TACTIC_INDEX_WRITE',
    'LEAN_LAZY_PARTS_MIN_FREE', 'LEAN_LAZY_PARTS_MAX_SIZE', 'LEAN_LAZY_PARTS_MIN_USES',
    'LEAN_MMAP_RESERVE', 'LEAN_NO_TOUCH', 'LEAN_OLEAN_LAYOUT',
    'LEAN_IMPORT_WORKERS', 'LEAN_NAT_MAX_SIZE', 'LEAN_SRC_PATH',
)

_probe_cache = {}


def _memo(key, fn):
    if key not in _probe_cache:
        _probe_cache[key] = fn()
    return _probe_cache[key]


# ----------------------------------------------------------------------------- helpers

def log(cfg, msg):
    if cfg.verbose:
        sys.stderr.write(f'leansnap: {msg}\n')
        sys.stderr.flush()


def lean_version(cfg):
    def go():
        out = subprocess.run([cfg.lean, '--version'], capture_output=True, text=True, check=True).stdout
        return out.strip().splitlines()[0]
    return _memo(('version', cfg.lean), go)


def header_text(src):
    """The header prefix of a Lean source: leading `module`/`prelude`/`import` lines (blank lines
    and line comments between them included, since they shift source positions). Returns the exact
    text up to the end of the last header line, trailing whitespace stripped."""
    lines = src.split('\n')
    end = 0  # index one past the last header line
    for i, line in enumerate(lines):
        s = line.strip()
        if s == '' or s.startswith('--'):
            continue
        if HEADER_LINE.match(line):
            end = i + 1
            continue
        break
    return '\n'.join(lines[:end]).rstrip()


def option_args(lean_args):
    """The `-D name=value` options among lean's arguments. They are baked into a header snapshot at
    save time and IGNORED at load time (measured), so they must be part of the key."""
    out = []
    it = iter(lean_args)
    for a in it:
        if a == '-D':
            out.append('-D' + next(it, ''))
        elif a.startswith('-D'):
            out.append(a)
    return sorted(out)


def quick_digest(path):
    """sha256 of the size, the first 64 KiB and the last 64 KiB of a file. Two reads (~0.2 ms for a
    100 MB shared library) and enough to separate two builds of the same source tree; `--binid full`
    hashes the whole file, `--binid path` uses path and size only."""
    h = hashlib.sha256()
    size = os.path.getsize(path)
    h.update(str(size).encode())
    with open(path, 'rb') as f:
        h.update(f.read(65536))
        if size > 65536:
            f.seek(max(0, size - 65536))
            h.update(f.read(65536))
    return h.hexdigest()[:16]


def full_digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()[:16]


def binary_identity(cfg):
    """Identity of the toolchain that will load the snapshot: the resolved `lean` executable and
    the `libleanshared` it links against. A snapshot is only loadable by the binary that saved it
    (`docs/linux/linux-full-fork.md` §5.2), and the `--version` line does not distinguish a fork
    build from stock, so this has to be in the key.

    Returns a list of `path\0size\0digest` strings (sorted, stable)."""
    def go():
        import shutil
        parts = []
        exe = shutil.which(cfg.lean) or cfg.lean
        for p in (exe, runtime_lib(cfg)):
            if not p:
                continue
            try:
                rp = os.path.realpath(p)
                size = os.path.getsize(rp)
                if cfg.binid == 'path':
                    dig = ''
                elif cfg.binid == 'full':
                    dig = full_digest(rp)
                else:
                    dig = quick_digest(rp)
                parts.append(f'{rp}\0{size}\0{dig}')
            except OSError as e:
                parts.append(f'{p}\0?\0{e.__class__.__name__}')
        return sorted(parts)
    return _memo(('binid', cfg.lean, cfg.binid), go)


def config_env(cfg):
    """The `LEAN_*` configuration switches that are set, as `NAME=value`, sorted. Unset variables
    contribute nothing, so a plain stock invocation keys exactly as it did before this change."""
    names = set(CONFIG_ENV_VARS) | set(n for n in (cfg.env_extra or '').split(',') if n)
    return sorted(f'{n}={os.environ[n]}' for n in names if n in os.environ)


def lookup_key(cfg, hdr, opts=()):
    h = hashlib.sha256()
    parts = [lean_version(cfg), cfg.lean_path or '', hdr, ' '.join(opts)]
    parts += ['|'.join(binary_identity(cfg)), '|'.join(config_env(cfg))]
    for part in parts:
        h.update(part.encode()); h.update(b'\0')
    return h.hexdigest()[:24]


def dep_files(deps_json):
    files = []
    for arts in deps_json:
        for k in ('olean', 'oleanServer', 'oleanPrivate', 'irSig', 'ir'):
            p = arts.get(k)
            if p:
                files.append(p)
    return files


def stat_id(path):
    st = os.stat(path)
    return [st.st_size, st.st_mtime_ns, st.st_ino]


def lake_hash(path):
    try:
        with open(path + '.hash') as f:
            return f.read().strip()
    except OSError:
        return None


def runtime_lib(cfg):
    """The shared runtime the snapshot's closures point into (its identity is baked into the
    snapshot's relocation table; a different library -> load error or worse)."""
    def go():
        prefix = subprocess.run([cfg.lean, '--print-prefix'], capture_output=True, text=True,
                                check=True).stdout.strip()
        for name in ('libleanshared.dylib', 'libleanshared.so', 'libleanshared.dll'):
            p = os.path.join(prefix, 'lib', 'lean', name)
            if os.path.exists(p):
                return os.path.realpath(p)
        return None
    return _memo(('runtimelib', cfg.lean), go)


def imports_of_header(hdr):
    mods = []
    for line in hdr.split('\n'):
        m = re.match(r'^\s*(?:public\s+|meta\s+|private\s+)*import\s+([\w.«»]+)', line)
        if m:
            mods.append(m.group(1))
    return mods


def trace_files(cfg, mods):
    """Lake `.trace` files of the directly imported modules, located via LEAN_PATH
    (`<dir>/<Mod/Path>.trace` next to the olean)."""
    out = {}
    for mod in mods:
        rel = mod.replace('.', os.sep)
        for d in (cfg.lean_path or '').split(os.pathsep):
            p = os.path.join(d, rel + '.trace')
            if os.path.exists(p):
                with open(p) as f:
                    out[mod] = json.load(f).get('depHash')
                break
        else:
            out[mod] = None
    return out


# ----------------------------------------------------------------------------- manifest

def build_manifest(cfg, hdr, deps_json):
    files = dep_files(deps_json)
    man = {
        'lean_version': lean_version(cfg),
        'lean_path': cfg.lean_path or '',
        'header': hdr,
        'runtime_lib': None,
        # the binary that saved the snapshot and the configuration it ran under. Recorded as well
        # as keyed so that a mismatch that survives the key (a copied cache directory, a hash
        # collision) causes a fallback to a plain run rather than a load into a binary or a
        # configuration that cannot use the snapshot.
        'binary_identity': binary_identity(cfg),
        'binid_mode': cfg.binid,
        'config_env': config_env(cfg),
        'files': {},          # path -> [size, mtime_ns, ino, lakehash|null]
        'traces': trace_files(cfg, imports_of_header(hdr)),
    }
    lib = runtime_lib(cfg)
    if lib:
        man['runtime_lib'] = [lib] + stat_id(lib)
    for p in files:
        man['files'][p] = stat_id(p) + [lake_hash(p)]
    return man


def check_manifest(cfg, man, mode):
    """Returns (ok, reason, seconds)."""
    t0 = time.perf_counter()
    try:
        if man['lean_version'] != lean_version(cfg):
            return False, 'lean version differs', time.perf_counter() - t0
        if man.get('runtime_lib'):
            lib, *sid = man['runtime_lib']
            if not os.path.exists(lib) or stat_id(lib) != sid:
                return False, f'runtime library changed: {lib}', time.perf_counter() - t0
            # ... and it must be the library *this* invocation will actually load. The check above
            # only says the saved library is unchanged on disk, which is true even when the caller
            # is a completely different toolchain.
            now_lib = runtime_lib(cfg)
            if now_lib and os.path.realpath(now_lib) != os.path.realpath(lib):
                return False, f'runtime library is {now_lib}, snapshot was saved with {lib}', \
                    time.perf_counter() - t0
        # A snapshot can only be loaded by the binary that saved it, and only under the
        # configuration it was saved under. Both are already in the key; these are the guards for
        # the cases the key cannot see (a copied cache directory, a manifest written by an older
        # leansnap, a hash collision). A mismatch means "no snapshot", i.e. a plain run.
        saved_bin = man.get('binary_identity')
        if saved_bin is None:
            return False, 'manifest predates binary-identity keying; rewrite the snapshot', \
                time.perf_counter() - t0
        if man.get('binid_mode') == cfg.binid and saved_bin != binary_identity(cfg):
            return False, 'binary identity differs from the one that saved the snapshot', \
                time.perf_counter() - t0
        if man.get('config_env', []) != config_env(cfg):
            return False, (f'configuration switches differ: {config_env(cfg)} vs '
                           f'{man.get("config_env", [])}'), time.perf_counter() - t0
        if mode == 'none':
            return True, 'unchecked', time.perf_counter() - t0
        if mode == 'trace':
            now = trace_files(cfg, imports_of_header(man['header']))
            if now != man['traces'] or any(v is None for v in now.values()):
                return False, f'trace depHash differs or missing: {now} vs {man["traces"]}', time.perf_counter() - t0
            # traces cover Lake-built modules only; the toolchain's own oleans are covered by the
            # runtime library identity + lean version above.
            return True, f'trace ok ({len(now)} modules)', time.perf_counter() - t0
        n = 0
        for p, (size, mtime, ino, lh) in man['files'].items():
            if mode == 'lakehash' and lh is not None:
                if lake_hash(p) != lh:
                    return False, f'lake hash differs: {p}', time.perf_counter() - t0
                if not os.path.exists(p):
                    return False, f'missing: {p}', time.perf_counter() - t0
            else:
                try:
                    if stat_id(p) != [size, mtime, ino]:
                        return False, f'stat differs: {p}', time.perf_counter() - t0
                except FileNotFoundError:
                    return False, f'missing: {p}', time.perf_counter() - t0
            n += 1
        return True, f'{mode} ok ({n} files)', time.perf_counter() - t0
    except Exception as e:  # any surprise -> not valid
        return False, f'check error: {e}', time.perf_counter() - t0


# ----------------------------------------------------------------------------- commands

def paths_for(cfg, key):
    base = os.path.join(cfg.dir, key)
    return base + '.snap', base + '.snap.deps', base + '.manifest.json'


def lean_env(cfg):
    env = dict(os.environ)
    if cfg.lean_path is not None:
        env['LEAN_PATH'] = cfg.lean_path
    return env


def cmd_save(cfg, args):
    with open(args.header) as f:
        src = f.read()
    hdr = header_text(src)
    if not hdr:
        sys.exit('leansnap: no header (import lines) found in ' + args.header)
    opts = option_args(args.lean_args)
    key = lookup_key(cfg, hdr, opts)
    os.makedirs(cfg.dir, exist_ok=True)
    snap, deps, manifest = paths_for(cfg, key)
    # Save from a canonical file that contains exactly the header text, so the baked-in header
    # syntax and positions match any file that starts with the same bytes.
    with tempfile.TemporaryDirectory() as td:
        hdrfile = os.path.join(td, 'leansnap_header.lean')
        with open(hdrfile, 'w') as f:
            f.write(hdr + '\n')
        tmp = snap + '.tmp'
        t0 = time.perf_counter()
        r = subprocess.run([cfg.lean, f'--incr-header-save={tmp}'] + opts + [hdrfile], env=lean_env(cfg),
                           capture_output=True, text=True)
        dt = time.perf_counter() - t0
        if r.returncode != 0 or not os.path.exists(tmp):
            sys.stderr.write(r.stdout + r.stderr)
            sys.exit(f'leansnap: save failed (exit {r.returncode})')
    with open(tmp + '.deps') as f:
        deps_json = json.load(f)
    man = build_manifest(cfg, hdr, deps_json)
    man['key'] = key
    man['options'] = opts
    man['save_seconds'] = round(dt, 2)
    with open(manifest + '.tmp', 'w') as f:
        json.dump(man, f)
    os.replace(tmp + '.deps', deps)
    os.replace(tmp, snap)
    os.replace(manifest + '.tmp', manifest)
    log(cfg, f'saved {snap} ({os.path.getsize(snap)/1e6:.1f} MB, {len(man["files"])} dep files, {dt:.1f} s)')
    print(key)


def find_snapshot(cfg, path, mode, lean_args=()):
    """Returns (key, snap_path or None, reason, check_seconds)."""
    with open(path) as f:
        src = f.read()
    hdr = header_text(src)
    key = lookup_key(cfg, hdr, option_args(lean_args)) if hdr else None
    if not hdr:
        return key, None, 'no header', 0.0
    snap, deps, manifest = paths_for(cfg, key)
    if not (os.path.exists(snap) and os.path.exists(deps) and os.path.exists(manifest)):
        return key, None, 'miss', 0.0
    with open(manifest) as f:
        man = json.load(f)
    ok, reason, dt = check_manifest(cfg, man, mode)
    return key, (snap if ok else None), reason, dt


def cmd_run(cfg, args):
    key, snap, reason, dt = find_snapshot(cfg, args.file, cfg.check, args.lean_args)
    cmd = [cfg.lean]
    if snap:
        cmd.append(f'--incr-load={snap}')
        log(cfg, f'key {key}: using snapshot ({reason}, check {dt*1000:.0f} ms)')
    else:
        log(cfg, f'key {key}: plain run ({reason}, check {dt*1000:.0f} ms)')
    cmd += args.lean_args + [args.file]
    r = subprocess.run(cmd, env=lean_env(cfg))
    sys.exit(r.returncode)


def cmd_check(cfg, args):
    key, snap, reason, dt = find_snapshot(cfg, args.file, cfg.check, args.lean_args)
    print(json.dumps({'key': key, 'snapshot': snap, 'reason': reason, 'check_ms': round(dt * 1000, 1),
                      'mode': cfg.check}))


def cmd_key(cfg, args):
    with open(args.file) as f:
        print(lookup_key(cfg, header_text(f.read()), option_args(args.lean_args)))


def main():
    ap = argparse.ArgumentParser(prog='leansnap', description=__doc__.split('\n\n')[0])
    ap.add_argument('--lean', default=os.environ.get('LEANSNAP_LEAN', 'lean'))
    ap.add_argument('--lean-path', default=os.environ.get('LEAN_PATH'))
    ap.add_argument('--dir', default=os.environ.get('LEANSNAP_DIR', os.path.expanduser('~/.cache/leansnap')))
    ap.add_argument('--check', default=os.environ.get('LEANSNAP_CHECK', 'stat'),
                    choices=['stat', 'lakehash', 'trace', 'none'])
    ap.add_argument('--binid', default=os.environ.get('LEANSNAP_BINID', 'quick'),
                    choices=['quick', 'full', 'path'])
    ap.add_argument('--env', dest='env_extra', default=os.environ.get('LEANSNAP_ENV', ''))
    ap.add_argument('--verbose', action='store_true', default=os.environ.get('LEANSNAP_VERBOSE') == '1')
    sub = ap.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('save'); s.add_argument('--header', required=True); s.add_argument('--out')
    s.set_defaults(fn=cmd_save)
    r = sub.add_parser('run'); r.add_argument('file'); r.set_defaults(fn=cmd_run)
    c = sub.add_parser('check'); c.add_argument('file'); c.set_defaults(fn=cmd_check)
    k = sub.add_parser('key'); k.add_argument('file'); k.set_defaults(fn=cmd_key)
    # `leansnap run file.lean -- -Dfoo=bar --json`: everything after `--` goes to lean verbatim
    argv = sys.argv[1:]
    extra = []
    if '--' in argv:
        i = argv.index('--'); argv, extra = argv[:i], argv[i + 1:]
    args = ap.parse_args(argv)
    args.lean_args = extra   # lean options (e.g. `-DautoImplicit=false`) for save/run/check/key
    if getattr(args, 'out', None):
        args.dir = args.out
    args.fn(args, args)


if __name__ == '__main__':
    main()
