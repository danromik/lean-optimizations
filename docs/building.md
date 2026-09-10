# Building the fork, and using it in your editor

## 1. Build

You need a Lean 4 source checkout at the exact base commit, the usual Lean build
prerequisites (`cmake`, `gmp`, `libuv`, `pkgconf`, and clang), and about 10 GB of disk.

```sh
git clone https://github.com/leanprover/lean4
cd lean4
git checkout v4.33.1                      # commit 819816b2e0a3bf405af45ae5c7af2491d8f5bee6
git apply --binary /path/to/release/patches/lean4-v4.33.1-optimized.patch
```

One of the sixteen files — `stage0/src/runtime/object.cpp`, a generated file git treats as
binary — is carried as a binary hunk. `--binary` is a no-op on current git, which applies
binary hunks unconditionally, but it is what older versions needed and it costs nothing.
`scripts/check-patches.sh --lean4 <checkout>` verifies the patch applies before you spend
an hour on it.

### 1.1 macOS (arm64)

```sh
cmake --preset release \
      -DLEAN_GITHASH_OVERRIDE=819816b2e0a3bf405af45ae5c7af2491d8f5bee6 \
      -DLEAN_PLATFORM_TARGET=arm64-apple-darwin24.6.0
cmake --build --preset release -- -j8                  # stage 1
cmake --build --preset release --target stage2 -- -j8  # stage 2
```

Both `-D` flags matter.

* **`LEAN_GITHASH_OVERRIDE`** makes the built `lean --version` print the stock v4.33.1
  string and githash. Lake then accepts the toolchain for a project pinned to
  `leanprover/lean4:v4.33.1`, and — the important part — the stock `.olean` files load
  unchanged, because a release build checks the githash in the olean header. Without it you
  cannot use the community Mathlib cache and every measurement needs a full rebuild.
* **`LEAN_PLATFORM_TARGET`** matches the stock release triple, so the built toolchain is
  interchangeable with the released one.

**Stage 2 is what you want.** Stage 1 carries the C++ changes but its `Init`/`Std`/`Lean`
oleans were written by the *stock* stage-0 compiler; stage 2's own core oleans are written by
the patched stage-1 compiler, which is what two of the six changes need. Times on an M2 Pro:
about 25 minutes from scratch, 9.5 minutes incremental (stage 1 ~5 min, stage 2 ~4.5 min).

### 1.2 Linux

Use the Linux patch instead — `patches/lean4-v4.33.1-optimized-linux.patch` — which is the
same six changes with the address-reservation change replaced by a per-region registration
scheme. (Reserving an address range on Linux would double the VMA count against
`vm.max_map_count`, and Linux does not have the problem the reservation solves: its free-address
list is a balanced tree, not a sorted list walked from the front.)

```sh
cmake -S . -B build/release -G "Unix Makefiles" \
      -DCMAKE_BUILD_TYPE=Release -DSTRIP_BINARIES=OFF \
      -DCMAKE_C_COMPILER=/usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++ \
      -DLEAN_GITHASH_OVERRIDE=819816b2e0a3bf405af45ae5c7af2491d8f5bee6
cmake --build build/release -- -j8
```

Three things bit us here and will bite you: give absolute compiler paths, build GMP ≥ 6.3.0
from source if your distribution has an older one, and keep the `tests/` directory present —
the build fails without it. From scratch, stage 0 ~5m40s + stage 1 ~6m30s on a 12-vCPU
container.

While raising `vm.max_map_count` is standard advice for Lean on Linux, note that the fork
makes it much less urgent: `import Mathlib` maps 52,583 VMAs on stock and 12,922 under the
fork — 80 % of the 65,530 default against 20 %.

### 1.3 Register the toolchain

```sh
elan toolchain link lean-fork ~/lean4/build/release/stage2
elan run lean-fork lean --version    # or: ~/lean4/build/release/stage2/bin/lean --version
```

It should print exactly the stock v4.33.1 string:

```
Lean (version 4.33.1, arm64-apple-darwin24.6.0, commit 819816b2e0a3bf405af45ae5c7af2491d8f5bee6, Release)
```

That is the point of `LEAN_GITHASH_OVERRIDE`, and it has a consequence worth stating here
because §2.3 depends on it: **`lean --version` cannot tell you whether you are running the
fork.** The fork and stock print the same line, byte for byte. Use the binary's path, or the
fork's verbose logging, to tell them apart — see §2.3.

## 2. Using it in your editor

**Please read §2.3 before you draw any conclusion from an editor session.**

### 2.1 Point the project at it

The Lean 4 VS Code extension (checked against `leanprover.lean4` 0.0.239) has **no setting
that names a toolchain path**. It asks elan, and elan answers from the project. So you select
the toolchain the same way you would select any Lean version:

* from VS Code: the ∀ title-bar menu → **"Select Project Lean Version…"**
  (`lean4.project.selectProjectToolchain`), and pick `lean-fork`; or
* from a shell, in the project directory:

  ```sh
  elan override set lean-fork      # this project only
  ```

  or edit the project's `lean-toolchain` file to contain the single line `lean-fork`.

Then restart the Lean server (VS Code will offer to; a toolchain change needs a *server*
restart, not a file restart).

You do not need to set any environment variables. The fork's switches all default to on, and
the lazy-loading index — the one cache the fork still keeps — defaults to
`$HOME/.cache/lean-lazy-parts` (the directory carries the change's development name; see
[`switches.md`](switches.md)). The first file you open on a new import closure pays a one-off
cost of roughly half a minute to write it; after that it is warm. The tactic index image is
*not* a cache and is never written at run time: if the library you are using ships one it is
mapped, and if not, Lean rebuilds the tables as it always has. If you want to override a switch
for the server specifically, the extension has no environment setting either — wrap it: make a
directory whose `bin/lean` and
`bin/lake` are two-line shell scripts that `export` what you want and `exec` the real binary,
symlink `lib/`, `include/` and `share/` to the real toolchain, and `elan toolchain link` that
directory instead.

### 2.2 Keeping a fork setup and a stock setup side by side

Use **VS Code Profiles** (File → Preferences → Profiles → New Profile), not two installs of
the extension. A profile carries its own settings and its own extension set, so you can have
a "Lean (fork)" profile and a "Lean (stock)" profile and switch between them in one window,
with no risk of one configuration leaking into the other. Two copies of the extension in one
profile is not a supported configuration and the two will fight over the language client.

The toolchain selection itself lives in the *project*, not the profile, so a clean A/B is
either two checkouts of the project (one `lean-toolchain` saying `lean-fork`, one saying
`leanprover/lean4:v4.33.1`) or an `elan override` you flip deliberately between sessions.

### 2.3 The trap: Lake quietly uses the project's toolchain

**This is the mistake we made ourselves, and it is very easy to make.**

`lake` reads the project's `lean-toolchain` file and runs *that* toolchain's `lean`, whatever
`lean` happens to be first on your `PATH` and whatever you believe you linked. So:

* `lake env lean file.lean` in a project pinned to `leanprover/lean4:v4.33.1` runs **stock
  Lean**, even if you invoked it from the fork's `bin/` directory.
* `lake build` does the same for every worker it spawns.
* `lake serve` — which is what your editor actually runs — does the same, so the file worker
  that produces your diagnostics is a stock worker.

You will see plausible-looking output the whole time. The only thing that changes is the
number.

Two ways to be sure:

```sh
# 1. Ask Lake which toolchain *directory* it will use.  Do NOT use `lake env lean
#    --version` for this: the fork is built with LEAN_GITHASH_OVERRIDE and prints the
#    stock version string byte for byte (§1.3), so the two are indistinguishable by it.
cd <project> && lake env printenv LEAN_SYSROOT   # must name the fork's directory
elan show                                        # the active override and where it came from

# 2. Ask the fork to say something only the fork says:
LEAN_TACTIC_INDEX_VERBOSE=1 LEAN_LAZY_PARTS_VERBOSE=1 lake env lean file.lean
# the fork logs cache hits/misses on stderr; stock Lean prints nothing.
```

There is a second, related trap for anyone doing measurements from a shell: **never run a
fork binary inside `lake env`.** `lake env` exports `DYLD_LIBRARY_PATH`/`LD_LIBRARY_PATH`
pointing at the stock toolchain's `lib/lean`; the `lean` executable is a stub that loads
`libleanshared` through that path, so the fork binary loads the *stock* runtime library and
reports stock numbers. Our first measurement of the address-reservation change came back at
10.35 s — a textbook stock number — for exactly this reason. Compute `LEAN_PATH` once and run
the bare binary, as `scripts/repro-import.py` does.

## 3. What you get without rebuilding Mathlib

**Three** of the six changes are purely in the runtime and take effect immediately against the
stock olean cache — the address reservation, the no-touch reference counts and lazy part loading.
That is where most of the gain is, and it is what `scripts/repro-import.py` measures.

Two have a **library-side half** (the shell-first `.olean` layout and the stored `exact?` index)
and need Mathlib rebuilt from source with the patched compiler to take full effect. That rebuild
costs ~43 min and ~11 GB. It no longer needs `LEAN_TACTIC_INDEX=0`: that instruction belonged to the
tactic index image while it was a cache, and the image writes nothing at run time now — see
[`warnings.md`](warnings.md) §1, which is retained as the record of why the design changed rather
than as a live instruction.

The sixth — the tactic index image — needs neither a rebuild nor a switch, but it does need
somebody to have **built the image**, and no library ships one today. Until one does, you build
your own, once, per import closure you care about (§3.1). Without it the feature is inert: it
costs nothing and gives nothing.

### 3.1 Building a tactic index image for yourself

```sh
cd <your Lake project>
echo 'import Mathlib' > /tmp/closure.lean          # the header you actually open files with
elan run lean-fork lean /tmp/closure.lean          # pass 1: builds the lazy-loading index
LEAN_TACTIC_INDEX_WRITE=1 elan run lean-fork lean /tmp/closure.lean   # pass 2: writes the image
ls .lake/build/lib/lean/exts-*.tacticindex*                # ~212 MB + a 10 KB .deps sidecar
```

Three things about this that are easy to get wrong:

* **Run pass 1 first.** The image's key includes the memory-mapped region list, and the
  lazy-loading index is one of those regions. An image built while that index was still cold is
  keyed on a configuration you will never present again, and will never be found.
* **Do not write it through `lake env lean`.** `lake env` runs the toolchain the *project's*
  `lean-toolchain` names (§2.3). If that is `leanprover/lean4:v4.33.1`, `LEAN_TACTIC_INDEX_WRITE=1 lake env
  lean …` runs **stock** Lean, which ignores the variable and writes nothing — and says nothing
  either. Either point the project at the fork first (§2.1), or run the fork binary with an
  explicit `LEAN_PATH`, as `scripts/repro-import.py` does.
* **The image is per-closure and per-configuration**, and an image built for one is *invisible*
  under another — not wrong, invisible. In particular a `lake serve` worker does not present the
  same closure as a command-line `lean`, so an image built by the recipe above is not the one your
  editor will look for. `LEAN_TACTIC_INDEX_VERBOSE=1` prints the exact path being looked for, which is how
  you tell.

`scripts/repro-import.py` does all of this for you inside its own `--cache-dir`, so the numbers it
prints are for the configuration this package claims.
