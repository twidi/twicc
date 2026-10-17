# Data dir file backups — design

Status: draft · 2026-10-17

## 1. Problem

On 2026-10-17 a test run replaced `~/.twicc/settings.json` with pure defaults. The
user lost every synced setting and recovered them only because an old worktree
still held a copy from five days earlier. The test-isolation fix (commit
`88c6fc86`) removes that cause, but nothing protects the user's files against
the next one: a bug, a bad manual edit, a buggy migration of a settings key.

The small files at the root of the data dir hold the user's own data, and
nothing can rebuild them. The database is out of scope: most of it is rebuilt
from the provider files, and the user state it holds (project names, pins,
bookmarks, shares…) needs a different mechanism.

## 2. Goal

Keep dated copies of these files, taken each time their content changes, so
that when one of them is damaged or lost its previous versions are in one
obvious place.

## 3. Non-goals

- Restoring is not part of this feature: the user stops TwiCC, copies the
  wanted version over the file and starts TwiCC again. No restore command, UI
  or MCP tool.
- No backup of the database, of `logs/`, of `search-index/`, of any
  subdirectory (`project-icons/`, `artifacts/`, `shares/`, `uploads/`…).
- No backup of files the backend rewrites on its own as internal state (§4).
- No off-machine copy, no encryption, no compression.

## 4. Files covered

Only regular files at the root of the data dir, from a fixed list:

| File | Holds |
|---|---|
| `settings.json` | synced settings |
| `workspaces.json` | workspaces |
| `layouts.json` | named layouts |
| `message-snippets.json` | message snippets |
| `terminal-config.json` | terminal configuration |
| `claude_code-settings-presets.json`, `codex-settings-presets.json` (and any future `<provider>-settings-presets.json`) | agent settings presets |
| `seen-tips.json`, `seen-help.json` | tips and help pages already seen |
| `api-tokens.json` | API token records (digests only) |
| `.env` | instance configuration (password hash, ports, provider homes) |
| `secret-key` | Django secret key |

Not covered, on purpose:

- `providers-status.json` and `telemetry.json`: internal state the backend
  rewrites by itself (upstream incidents, daily telemetry bookkeeping). Backing
  them up would create versions nobody needs. The only user action they record
  is the last dismissed incident notice; losing it only shows that notice
  again.
- Runtime files: `twicc.info.json`, heartbeats, `*.lock`, temp files of atomic
  writes.

A new user-data file added to the data dir root later joins the list in the
same change that creates it.

## 5. When a version is taken

A new version of a file is stored when its content differs from the newest
stored version of that file, and only then. Two triggers:

1. **At backend startup**, before any backend write to a covered file (the
   startup itself writes some of them, e.g. `settings.json`). Each covered file
   is compared with its newest version. This takes the first
   version of every file on the first run with the feature, and catches the
   content left by changes made while the backend was stopped (manual edit,
   another tool, a test run). Two writes happen earlier, when Django loads its
   settings, and cannot be preceded: the creation of `secret-key` and the
   rename of a legacy `claude-settings-presets.json`. Each happens only when
   its target is missing (or, for `secret-key`, empty), so no existing content
   is at risk.
2. **While the backend runs**, a file watcher on the data dir root, not
   recursive. Any change to a covered file, whoever makes it (the backend, a
   CLI process, an editor, a test run), is compared the same way after a short
   quiet delay, so a burst of writes gives one version.

No change goes unseen between the two triggers: the watching covers everything
from the startup scan on, including the startup's own writes. At shutdown, a
change still waiting for its quiet delay is compared before the backend stops.

Only the backend process takes versions. One backend runs per data dir (the
instance lock), so one writer manages the backups of a data dir. CLI processes
never take versions, and a test run never takes versions in a live data dir.

### What "content differs" means

The stored copy is always the file exactly as it is on disk. The comparison
ignores the values that change on writes without carrying user data:

- `settings.json`: the `_version` write counter.
- `api-tokens.json`: each record's `last_used_at`, refreshed while a token is
  in use.

A file that is not valid JSON (damaged, half-written by another tool) is
compared as raw bytes. A damaged file is a version like any other: keeping it
documents what happened, and the versions before it stay.

A deleted file takes no version and removes nothing: its versions stay.

## 6. Where versions are stored

`<data_dir>/backups/<file name>/`, one folder per file. The leading dot of
`.env` is dropped from the folder name so the folder is not hidden (`env`).

Each version is one file. Its name starts with the date and time it was taken,
in UTC: a plain directory listing is then chronological and readable, with no
repeated hour when daylight saving time ends. It keeps the original extension
(`.json` or none). A version never replaces another: two versions of a file
taken at the same time get distinct names.

The backups hold secrets (`.env`, `secret-key`, token digests): the folders and
files are readable by the owner only, whatever the mode of the original.

`backups/` is local to its data dir. A worktree instance has its own, and
`devctl.py` does not copy it into a new worktree (its copy list names fixed
entries plus a root-level `*-settings-presets.json` pattern; none of them
matches `backups/`). In a worktree the data dir is the
checkout root, so `backups/` is git-ignored there explicitly, like the other
data dir entries: it holds secrets and must never be committed.

## 7. Retention

After a new version of a file is stored, the older versions of that file are
pruned. A version is kept when either rule keeps it:

1. **The 30 newest versions**, whatever their dates.
2. **The newest version of each of the 30 most recent days that have at least
   one version.** Days without a change do not count, so a file that rarely
   changes keeps its history for as long as it does not change.

Rule 2 protects the history from a burst: a loop that rewrites a file 200 times
in a few minutes fills rule 1 with versions of a single day, and rule 2 still
keeps the last version of each earlier day. Days are local calendar days of the
machine, because the user thinks "the version from Tuesday" in local time; the
UTC date in a version's name can therefore differ by one from the day that kept
it.

At most about 60 versions per file are kept. The covered files are a few KB
each, so the whole folder stays within a few MB.

Versions are pruned only after a new version of the same file is stored.
Nothing else removes them.

## 8. Failure behaviour

A backup is a safety net, never a dependency:

- Any failure (read, write, prune, watcher start) is logged and swallowed. It
  never fails or delays the write that triggered it, the startup, or the
  shutdown.
- A failed attempt is retried at the next trigger for that file (next change
  or next startup).

## 9. Tests

- A change to a covered file stores exactly one new version, a rewrite with the
  same content (or differing only in the ignored values) stores none.
- A burst of writes gives one version.
- The startup scan stores the first version of each file, then nothing until a
  file changes; it stores a change made while the backend was stopped.
- Retention keeps the union of both rules, and a burst cannot remove the
  versions of earlier days.
- Files outside the list and subdirectories are ignored.
- A backup failure does not break the triggering write or the startup.

All tests run on the isolated test data dir (`twicc.settings_test`).
