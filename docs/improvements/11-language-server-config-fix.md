# The language-server configuration fix

_Patch: [`patches/fix-server-config-watch.patch`](../../patches/fix-server-config-watch.patch),
**three files, +54 / −8**, against Lean `v4.33.1`
(`819816b2e0a3bf405af45ae5c7af2491d8f5bee6`). Ships separately from the fork, because it changes
Lean's behaviour. It affects **unmodified Lean as you use it today**: any editor session in which
you edit a lakefile with files open._

> **We found no public report** in either the `leanprover/lean4` or the `vscode-lean4` tracker,
> and `Watchdog.lean` on the current development branch is byte-identical to v4.33.1. Our search
> covers public channels only; the Zulip archive is stale and the live instance is login-walled.
> §3.2 also records a finding that narrows the claim considerably for VS Code users.

---

## 1. The defect

### 1.1 What the server watches

At `initialized`, `initAndRunWatchdogAux` (`src/Lean/Server/Watchdog.lean:1618–1624` at tag
`v4.33.1`) asks the client to watch two globs:

```json
{"id":"lean_watcher","method":"workspace/didChangeWatchedFiles",
 "registerOptions":{"watchers":[{"globPattern":"**/*.lean"},{"globPattern":"**/*.ilean"}]}}
```

`handleDidChangeWatchedFiles` then splits what arrives by extension:

```lean
let leanChanges  := changes.filter fun (_, path) => path.extension == "lean"
let ileanChanges := changes.filter fun (_, path) => path.extension == "ilean"
```

`.lean` changes notify the open workers whose import closure contains the changed URI; `.ilean`
changes update the go-to-definition index. **Anything else is dropped before it reaches either
branch.** Nothing in the watchdog ever reads a lakefile or a manifest, and its own environment
(`LEAN_PATH`, `LEAN_SRC_PATH`, `PATH`) was computed once by `lake serve` at start-up and is never
recomputed.

`lakefile.lean` is an interesting special case: it *does* have the `lean` extension, so it reaches
the `leanChanges` branch — where it is a no-op, because a lakefile is in no file's import closure
and `importedBy` has no entry for it.

### 1.2 Why that matters: the configuration is per file, and it arrives through Lake

A file worker turns its file's header into a `lake setup-file` call and uses the answer:
`options` is merged into the elaboration options — linters, `autoImplicit`, `pp.*`,
`maxSynthPendingDepth`, `warn.sorry`; `package?` sets the module's package; `dynlibs` are
`dlopen`ed. Those fields come from the *owning `lean_lib`* of the file, i.e. straight out of the
lakefile.

The worker asks **exactly once per header** — at `didOpen`, and again when an edit changes the
import block. So between two header events a worker's configuration is frozen at whatever the
lakefile said when it started.

`lake setup-file` reloads the whole workspace on every call, so a file opened *after* a lakefile
edit gets the new configuration. A file that was already open does not.

**That is the defect: not merely that a change is missed, but that two files of the same project,
at the same moment, elaborate under different configurations, and nothing says so.**

### 1.3 The defect, observed on a real project

`formal-conjectures` (1,387 files, Mathlib, one `lean_lib` with `leanOptions = {warn.sorry =
false}`). Two files opened; `warn.sorry` flipped to `true` in `lakefile.toml`; the change
announced; the first file restarted:

| | before the fix | after the fix |
|---|---|---|
| watchers registered | `**/*.lean`, `**/*.ilean` | the five globs |
| diagnostics on file 1 after the notification | **none** | the stale-configuration notice |
| diagnostics on file 2 after the notification | **none** | the stale-configuration notice |
| file 1 after "Restart File" | 6 × `declaration uses 'sorry'` | 6 × `declaration uses 'sorry'` |
| file 2, never restarted | none | the stale-configuration notice only |

The "before" column *is* the defect: one file warns six times and the other does not warn at all,
in the same project at the same moment, with nothing to tell the user why. The "after" column
keeps the same divergence — a restart is still what applies the change — but every open file now
says so.

## 2. What the change does

It uses the mechanism that is already there.

| file | change | lines |
|---|---|---|
| `src/Lean/Data/Lsp/Internal.lean` | `LeanStaleDependencyParams.isBuildConfig : Bool := false` — a field on the existing internal watchdog→worker notification. It defaults, so nothing else has to change. | +7 / −0 |
| `src/Lean/Server/Watchdog.lean` | `isBuildConfigFile`, a third branch in `handleDidChangeWatchedFiles`, an `isBuildConfig` parameter on `notifyAboutStaleDependency`, and three more watcher globs | +42 / −4 |
| `src/Lean/Server/FileWorker.lean` | `handleStaleDependency` takes the parameters instead of discarding them, and picks its wording from `isBuildConfig` | +13 / −4 |

The registration becomes five globs — `**/*.lean`, `**/*.ilean`, `**/lakefile.toml`,
`**/lakefile.lean`, `**/lake-manifest.json` — and the handler gains:

```lean
def isBuildConfigFile (path : System.FilePath) : Bool :=
  match path.fileName with
  | some "lakefile.toml" | some "lakefile.lean" | some "lake-manifest.json" => true
  | _ => false
…
let configChanges := changes.filter fun (_, path) => isBuildConfigFile path
let leanChanges   := changes.filter fun (_, path) =>
  path.extension == "lean" && ! isBuildConfigFile path
…
if let some (c, _) := configChanges[0]? then
  let fws ← (← read).fileWorkersRef.get
  for ⟨uri, _⟩ in fws do
    notifyAboutStaleDependency uri c.uri (isBuildConfig := true)
```

The worker's message, when the flag is set:

> The build configuration of this project has changed; this file is still using the configuration
> it was opened with. Use the "Restart File" command in your editor.

against the existing *"Imports are out of date and should be rebuilt; use the "Restart File"
command in your editor."* — same severity (information), same stickiness, same remedy.

### 2.1 What is watched, and what is deliberately not

| file | watched? | why |
|---|---|---|
| `lakefile.toml`, `lakefile.lean` | **yes** | they carry `leanOptions`, `globs`, `srcDir`, `roots`, `precompileModules`, `externLibs`, `plugins` — every field of Lake's answer that is not the import closure |
| `lake-manifest.json` | **yes** | it decides *which* dependency sources are used, and its failure modes (`missing manifest`, `dependency 'x' not in manifest`) are exit-1 errors that today become the next-opened file's only diagnostic |
| `lakefile.olean` | no | Lake's compiled form of `lakefile.lean`, which is already watched; watching the derived artefact adds a second event for the same edit, and it lives under `.lake/`, where nothing else is watched |
| `lean-toolchain` | **no, deliberately** | a file restart **cannot** pick up a toolchain change. The worker is spawned from the running server's own binary (`findWorkerPath`: `IO.appPath`) and inherits the environment `lake serve` computed at start-up, so the diagnostic's advice would be false. The correct remedy is a server restart, which is a client-side action |
| `.olean` build outputs | no | out of scope and a much larger change: a `lake build` in a terminal rewrites thousands of them, and the server has no signal today. §6.3 |

The globs are workspace-wide, so `.lake/packages/*/lakefile.toml` is watched too. That is
deliberate — a dependency's configuration is part of this workspace's configuration — but see
§6.2.

## 3. Why it is correct

**Only the first change of a batch is acted on.** `lake update` rewrites the lakefile and the
manifest together, and one notification per worker is enough. `appendStickyDiagnostic` also
replaces any sticky diagnostic with the same text, so repeats collapse rather than accumulate.

**Every worker, not the dependents.** `handleDidSave` and the `.lean` branch both notify
`importData.importedBy[uri]`, the workers whose import closure contains the changed file. **A
lakefile is in no import closure.** Worse, the relationship it *does* have — "this file's
`lean_lib` owns these options" — is Lake's knowledge, not the server's: the watchdog cannot even
determine which `lean_lib` claims a file, and `formal-conjectures` is a real project with two
libraries carrying the identical glob `FormalConjectures.+` and different options. Notifying every
open worker is the only sound choice available to the server, and it is cheap: one notification
per open file, and the worker's response is a diagnostic, not work.

**`leanChanges` now excludes config paths**, so `lakefile.lean` takes the new branch and not the
inert old one. `**/lakefile.lean` is registered although it is redundant with `**/*.lean`, for
clients that match by file name; a client honouring both reports one edit twice, which costs
nothing here.

**Notify, not auto-restart.** Restarting a user's workers because a file changed on disk would
discard their elaboration state mid-edit, and it is not what Lean does for any other invalidation
— a stale import, the closest analogue, has produced an advisory diagnostic since the feature was
added. A lakefile is also edited in ways that do not survive: an incomplete `[[require]]` block,
saved halfway through, makes `lake setup-file` exit 1, and an auto-restart would replace every
open file's diagnostics with Lake's error text until the user finishes typing — a failure mode
that was actually observed while measuring this. With a notification, the user restarts when they
are ready, and one `lake setup-file` costs 2.7–3.9 s, which is precisely the cost that should be
the user's to choose.

### 3.1 Do clients actually deliver these events?

The server registers globs and depends on the editor to honour them. That is the part of this fix
that could have failed, so it was checked before anything else.

**The protocol.** `workspace/didChangeWatchedFiles` registration is by glob pattern; nothing in
the LSP specification privileges any extension. Lean's existing `.lean`/`.ilean` watching works in
practice in VS Code — the stale-import diagnostic is a shipped feature — so the pipeline is
known-good; the only question was whether the *pattern* matters.

**Our client** (`wdtest.py`) speaks to a real `lake serve`, answers `client/registerCapability`,
and sends `workspace/didChangeWatchedFiles` with `{"changes":[{"uri":…,"type":2}]}` — the shape
VS Code's language client sends from its file watcher. It records the registration and asserts on
it: the five globs after the fix, two before. Delivery through it works.

**Real VS Code, read out of the shipped extension bundle**
(`leanprover.lean4-0.0.239/dist/extension.js`):

1. `vscode-languageclient`'s `FileSystemWatcherFeature.register` iterates the registered watchers
   and, for each, calls `workspace.createFileSystemWatcher(pattern, …)` and hooks
   `onDidCreate`/`onDidChange`/`onDidDelete` straight to `notifyFileEvent`. **No filtering by
   extension, by language or by document selector** — the glob is passed to VS Code verbatim.
2. `notifyFileEvent` batches events and sends `DidChangeWatchedFilesNotification`.
3. The lean4 extension's `sendNotification` middleware cannot drop it: its `isParamExcluded`
   inspects `params.uri` or `params.textDocument.uri` only, and `DidChangeWatchedFilesParams`
   (`{changes: […]}`) has neither.

Two limits of the mechanism, which apply equally to today's `**/*.lean` watcher: a plain-string
glob is resolved against the **open workspace folders**, so a Lean project not inside one is not
watched at all; and `files.watcherExclude` applies, though none of its defaults touch a lakefile
or `.lake/`.

### 3.2 The finding that narrows the claim

**vscode-lean4 0.0.239 already watches `lean-toolchain`, `lakefile.lean` and `lakefile.toml`**
in the project root, client-side. `LeanClient.registerRestartServerNotificationWatchers` creates a
`RelativePattern` watcher on each, compares the file's *contents* with what it saw before, and on
a real change offers:

> *Project configuration (`lakefile.toml`) of 'X' has changed. Do you wish to restart the Lean
> server?* [Restart Server]

So VS Code users are not entirely unwarned, and the correct claim is narrower than "nothing tells
you". But:

* it covers only the **root** lakefile — not a nested package's, and not a multi-root workspace's
  other projects;
* it does **not** watch `lake-manifest.json`, so `lake update` gives no signal at all;
* it fires only if the file **existed when the client started** (there is a `fileExists` guard);
* it is a whole-**server** restart, offered as a modal choice, where the server-side fix is a
  per-file advisory costing one `lake setup-file` for the files the user actually cares about;
* **it exists only in VS Code.** Any other client of `lake serve` — `lean.nvim`, `lean4-mode`, a
  scripted client — gets nothing.

It is also, incidentally, evidence for §3.1's last link: the extension ships a feature that
depends on VS Code's own watcher firing for `lakefile.toml`.

## 4. How it was verified, and what that does not cover

`wdtest.py <toolchain> [scenario…]` builds a scratch two-package workspace and runs each scenario
against a **fresh** `lake serve`, so no scenario inherits another's sticky diagnostics. Package
`wddep` (`def depVal : Nat := 41`) and package `wdmain` requiring it by path, with one `lean_lib`
carrying `leanOptions = {warn.sorry = false}` and three modules: one importing the dependency and
containing a `sorry`, one importing nothing and containing a `sorry`, one imported by nobody —
plus a `README.md`. Two packages so that `lake-manifest.json` is a real manifest with an entry;
`warn.sorry` because its effect is directly visible as a diagnostic. Everything runs with
`dependencyBuildMode = never` against a pre-built workspace, so no scenario triggers a build.

Run on the toolchain **with** and **without** the commit:

| scenario | assertion | before | after |
|---|---|---|---|
| `watchers` | registration includes `**/*.lean`, `**/*.ilean` | ok | ok |
| | includes `**/lakefile.toml` | **FAIL** | ok |
| | includes `**/lakefile.lean` | **FAIL** | ok |
| | includes `**/lake-manifest.json` | **FAIL** | ok |
| `lakefile` | A opens with no `sorry` warning | ok | ok |
| | after the edit, **A** is told its configuration is stale | **FAIL** | ok |
| | after the edit, **B** is told its configuration is stale | **FAIL** | ok |
| | the wording is about configuration, not imports | ok | ok |
| | restarting A picks up the new option | ok | ok |
| | the diagnostic is gone from A after the restart | ok | ok |
| | B, not restarted, still has the old option | ok | ok |
| `manifest` | rewriting `lake-manifest.json` tells **A** | **FAIL** | ok |
| | …and tells **B** | **FAIL** | ok |
| `dep` | a changed `.lean` that only A imports tells A, with the *imports* wording | ok | ok |
| | …and tells B nothing | ok | ok |
| `unrelated` | a changed `README.md` and a changed but unimported `.lean` tell A nothing | ok | ok |
| | …and tell B nothing | ok | ok |
| `ilean` | a changed `.ilean` raises no staleness diagnostic | ok | ok |
| | references still resolve through the reference index afterwards | ok | ok |

**7 assertions fail before and pass after; the other 12 are identical in both.** The behaviours
the fix must not disturb — dependency scoping by import closure, `.ilean` handling, silence on
unrelated files, and the fact that a restart picks the configuration up — are in the 12.

`fctest.py` repeats the same on the corpus project (§1.3), on an APFS clone whose original was
verified clean after every run (asserted in the test).

**Equivalence.** The fork's 26-case byte-for-byte CLI suite is 26/26 identical with this commit
applied, plus the `lake build` check. That is worth running as a build sanity check, but it should
be read for what it is: it exercises the `lean` **CLI**, and this commit touches only
`Lean/Server/*` and one internal LSP structure, none of which the CLI executes. **The behavioural
evidence for this change is the table above, not the equivalence suite.**

**What this does not cover.**

* **No run inside a real VS Code window.** That needs a GUI session, which this work did not open.
  The chain in §3.1 is read from the shipped bundle rather than observed on the wire; the last
  link is evidenced indirectly by §3.2.
* **No other client was tested** — `lean.nvim`, `lean4-mode`. The LSP registration is the same,
  but nothing here exercised them.
* **Cost was not measured.** Notifying every open worker is one small notification per open file
  and a diagnostic publish; with a handful of open files this is not worth measuring, and a
  session with dozens of open files was not tried.

## 5. Measured effect

There is nothing to measure in seconds; the effect is that a diagnostic appears where none did.
The one number worth quoting is the cost of acting on it: one `lake setup-file` per restarted
file, **2.7–3.9 s** on the corpus project, and the file restart in the corpus test took 7.7 s
including re-elaboration.

## 6. What is unfinished, provisional or known to be wrong

### 6.1 It reacts to the event, not to a content change

The `.lean` path does the same — a `didSave` of an unchanged file notifies dependents today — but
the consequences are more visible here. VS Code sends a `Changed` event for a save that changed
nothing, and a `lake update` or a package re-clone rewrites lakefiles under `.lake/packages`, so a
user can get the diagnostic when their configuration did not really change. vscode-lean4 hashes
contents before prompting; the watchdog could keep a `HashMap FilePath UInt64` of the config files
it has seen and suppress no-op events. That is more state than the existing idiom carries, so it
was not done.

### 6.2 Workspace-wide globs mean `lake update` lights up every open file

`.lake/packages/*/lakefile.toml` is watched deliberately, but a `lake update` that re-clones
packages will produce the diagnostic on every open file. That is the correct answer for that
event, and it is also the main source of possible noise.

### 6.3 Two things a running server still cannot see

* **`lean-toolchain`** (deliberately, §2.1) — only a server restart helps, and only the client can
  do that.
* **`.olean` build outputs.** A `lake build` in a terminal while the editor is open produces no
  signal at all. This is the common case, and it is a larger design question — thousands of
  watched files, or a Lake-side notification — rather than an extension of this patch.

### 6.4 One case the diagnostic's advice does not fully cover

If the lakefile edit adds a `lean_lib` with a new `srcDir`, the **server's** `LEAN_SRC_PATH` is
still the one `lake serve` computed at start-up, so `moduleFromDocumentUri` — and with it
references and go-to-definition — resolves against a stale path list even after a file restart.
The fix makes the user aware that something changed; for that particular change only a server
restart is enough, and the diagnostic does not say so.

### 6.5 Upstream

This is written as a report of a defect and a candidate fix, **not** as a proposal that has been
discussed with the Lean team. Whether the FRO considers the current behaviour a bug, and whether
they would prefer the client-side answer that vscode-lean4 already ships, is for them to say.
