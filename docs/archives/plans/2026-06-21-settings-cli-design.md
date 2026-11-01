# Settings CLI (`twicc settings`) — design

Status: design, agreed in chat (2026-06-21). No code written yet.

## 1. Goal

Give the `twicc` CLI a way to **read and mutate the synced settings**
(`<data_dir>/settings.json`), the global user preferences today editable only
through the Settings panel (WS `update_synced_settings`). This unlocks
scripting and, notably, a *limited TwiCC front-end driven by a program* that
configures an instance without the Vue UI.

Design constraints, all agreed with the user:

- **Hybrid surface**: a generic key/value backbone for the open-ended scalar
  tail, plus dedicated sub-commands for the keys with rich server-side
  semantics (provider activation/orchestration, per-provider agent defaults,
  notification targets).
- **Full coverage** of the behaviour-affecting keys, including the ones with
  special server logic.
- **No agent skill** is added — the CLI is human/program facing only. Only
  `SKILLS-AND-CLI.md` (root) is updated at build time.

## 2. Background: how synced settings work today

### Storage and defaults

- `<data_dir>/settings.json` is a flat JSON object. The backend owns the
  default values: `SYNCED_SETTINGS_DEFAULTS` in
  `src/twicc/synced_settings.py`, built from the generic defaults plus each
  provider's `BaseProviderHelpers.SYNCED_SETTINGS_DEFAULTS` ClassVar
  (`claudeCode*`, `codex*`).
- `read_synced_settings()` reads the file, applies legacy rename/drop
  migrations, merges defaults, and caches. It returns a copy and **needs no
  running server** — so all read commands work offline, like `twicc project`.
- `write_synced_settings()` writes atomically (temp + rename) and refreshes
  the in-memory cache; it does **not** itself acquire `_settings_lock` — the
  caller holds the lock around the whole read-modify-write (as the WS path
  does, and as the new service must).
- `_version` is an internal optimistic-concurrency counter, stripped before
  the value reaches a client (`prepare_settings_for_client`).

### The write path that already exists (WS only)

`UpdatesConsumer._handle_update_synced_settings` in `src/twicc/asgi.py`
(≈ line 1247) is the single rich write path. Under `_settings_lock` it:

1. Optimistic concurrency: reject if the client's `baseVersion` is behind
   `_version` (accept when `baseVersion is None`).
2. Merge the client's partial dict into the current settings.
3. `get_provider_helpers_registry().enforce_synced_settings_consistency(merged,
   changes)` — each provider normalises its own keys (Claude Code: the
   `claudeCodeDefaultModel` pivot re-runs `enforce_agent_settings_consistency`
   over the bundle and writes back only the keys present in `changes`).
4. `disabledProviders` safety: refuse to disable a provider that still has live
   agents; refuse toggles while a provider is in a transient
   (`STARTING`/`STOPPING`) state — reverted entries are reported in
   `corrections`.
5. `defaultProvider` rebind: if the current default is no longer enabled, pick
   the first enabled provider in `Provider` enum order (→ `corrections`).
6. Bump `_version`, `write_synced_settings`.
7. Compute orchestrator deltas (`to_start`/`to_stop`) from *what was running*
   vs *what should run*, then **outside the lock** run `begin_start` /
   `begin_shutdown` (awaited, fast broadcasts) + `schedule_finish_start` /
   `schedule_finish_shutdown` (fire-and-forget). Finally broadcast
   `synced_settings_updated`.

### The mutation channel the CLI uses: drop-requests

Write-side CLI commands (`update-workspace`, `update-project`, …) drop a JSON
request file under `<data_dir>/drop-requests/`. The watcher
(`src/twicc/drop_requests_watcher.py`) dispatches by `kind` through
`_KIND_HANDLERS` to an **async** `*_from_payload(payload)` service, writes a
status file, and the CLI polls it. Services return a result object with
`success` / `errors`; the CLI maps that to exit codes (`update-workspace`:
0 ok, 3 rejected, 4 failed, 5 timeout, plus 1 validation / 2 server-down).
Because the handler is awaited in async context, a settings service **can run
the orchestrator transitions** exactly like the WS path.

### The notification test path

`POST /api/external-notifications/test/` → `views.external_notifications_test`
→ `external_notifications.test_notification_urls(urls)` **sends** a test per
URL and reports per-URL results; it does **not** touch settings. The frontend
persists `tested` afterwards. Crucially, a target only ever fires when
`tested is True` (`external_notifications.py` lines 124 and 227), so a target
created without a successful test is dead weight.

## 3. The complete synced-settings inventory and its CLI treatment

| Key | Type | CLI treatment |
|---|---|---|
| `waTheme`, `waBrand` | enum str | **excluded** (visual, no behaviour) |
| `defaultLayoutId` | str | **excluded** (visual) |
| `_version` | int | **excluded** (internal) |
| `defaultWorktreeDirectory` | str | generic `set`/`unset` |
| `autoUnpinOnArchive` | bool | generic |
| `terminalUseTmux` | bool | generic |
| `terminalTmuxConfigPath` | str | generic |
| `titleGenerationEnabled`, `titleAutoApply` | bool | generic |
| `titleSystemPrompt` | str (multiline, `{text}`) | generic (`unset` = reset) |
| `publicBaseUrl` | str (url) | generic |
| `notifyOnExtraUsageStart` | bool | generic |
| `defaultProvider` | enum provider | `settings provider <p> set-default` |
| `disabledProviders` | list[str] | `settings provider <p> enable/disable` |
| `orchestrationDisabledProviders` | list[str] | `settings provider <p> orchestration-enable/disable` |
| `externalNotificationTargets` | list[obj] | `settings notifications …` |
| `{provider}Default{Model,Effort,…}` (agent bundle) | mixed | `settings provider <p>` flags |
| `{provider}DefaultUntrustedPermissionMode` | enum (restricted) | `settings provider <p> --untrusted-permission-mode` |
| `{provider}Usage{Read,Dump}File{Enabled,Path}` | bool + str | `settings provider <p> --usage-*-file` flags |

Device-local UI preferences (color scheme, font size, display mode,
copy-on-select, …) are **not** in `settings.json` and are out of scope.

The real persisted shape of a notification target (from
`NotificationSettings.vue`, richer than the stale docstring in
`synced_settings.py`):

```
{ id: <uuid>, name: str, url: str, enabled: bool, tested: bool|null,
  notifyUserTurn: bool, notifyPendingRequest: bool,
  notifyExtraUsageStart: bool, awayOnly: bool }
```

The backend reads `enabled`/`url`/`tested`/`notifyUserTurn`/
`notifyPendingRequest`/`notifyExtraUsageStart`/`awayOnly`; `id` and `name` are
front-end-only (so `id` is a perfect stable handle for the CLI).

## 4. CLI surface

```bash
# ── READ (offline, reads settings.json) ────────────────────────────────
twicc settings                       # full settings JSON (defaults+file, _version stripped)
twicc settings get <KEY>             # one value
twicc info settings                  # schema: keys / types / defaults / which command owns each

# ── GENERIC BACKBONE (global behaviour scalars; allowlist) ─────────────
twicc settings set <KEY> <VALUE>     # value type inferred from the key's default
twicc settings unset <KEY>           # revert to default (e.g. titleSystemPrompt)

# ── PROVIDER (activation + agent defaults + usage files, merged) ───────
twicc settings provider <provider>                 # show this provider's slice (offline)
twicc settings provider <provider> \               # agent defaults patch
    --model … --effort … --permission-mode … --untrusted-permission-mode … \
    --context-max … --thinking/--no-thinking --fast/--no-fast --chrome/--no-chrome \
    --usage-read-file PATH/--no-usage-read-file --usage-dump-file PATH/--no-usage-dump-file
twicc settings provider <provider> enable
twicc settings provider <provider> disable
twicc settings provider <provider> set-default
twicc settings provider <provider> orchestration-enable
twicc settings provider <provider> orchestration-disable

# ── NOTIFICATIONS (object list; identity by id) ────────────────────────
twicc settings notifications                        # list targets (id, url, flags) + global flags
twicc settings notifications add <url> [--name N] [--disabled] \
    [--no-user-turn] [--no-pending] [--no-extra-usage] [--away-only] [--test]
twicc settings notifications update <id> [--url …] [--name …] \
    [--enabled/--disabled] [--user-turn/--no-user-turn] [--pending/--no-pending] \
    [--extra-usage/--no-extra-usage] [--away-only/--no-away-only]
twicc settings notifications remove <id>
twicc settings notifications test <id>
```

### 4.1 Generic backbone

- Allowlist = the global behaviour scalars in §3. A key outside the allowlist
  is rejected with a pointed message: visual keys → "UI-only visual
  preference"; dedicated-command keys (`disabledProviders`, provider defaults,
  notification targets) → "use `twicc settings provider …` / `… notifications
  …`".
- **Type inference from the default**: `type(SYNCED_SETTINGS_DEFAULTS[key])`
  drives parsing — bool (`true/false/1/0/yes/no`), int (`int()`), str
  (verbatim). No hand-maintained schema; a new scalar default is settable for
  free. Enum membership is validated server-side by the existing consistency
  rules / a small per-key validator where one already exists.
- `unset` deletes the key from the file so the default re-applies (this is the
  Settings panel's "Reset to default", e.g. `titleSystemPrompt`).

### 4.2 `settings provider <provider>`

Like `twicc update-project`, the group's callback carries a flat patch while
sub-commands carry the rest — but the shapes differ and that matters for the
implementation. `update-project` keeps its flat patch on the *top* callback
(`update-project <P> --name …`) with `settings` as a leaf sub-command; here the
flat patch is the **agent-defaults bundle** and it sits on the **`provider <p>`
group callback** alongside the `enable`/`disable`/… sub-commands. A Click group
stops parsing its own options at the first positional, so this requires the
same custom `TyperGroup` (`allow_interspersed_args` flip) that `update-project`
uses — see §5.4.

- **Show (no flag, no sub-command)**: print the provider's slice — `enabled`
  (derived from `disabledProviders`), `is_default` (derived from
  `defaultProvider`), `orchestration_enabled` (derived from
  `orchestrationDisabledProviders`), the agent defaults, untrusted default, and
  usage-file settings. Offline read. (This diverges from `update-project`'s
  no-op error, agreed: the `settings` namespace is read-friendly.)
- **Agent-defaults flags** map to `{provider}Default*` via
  `AGENT_SETTINGS_FIELDS_MAPPING`. Only fields the provider declares are
  accepted (Codex has no `--thinking`/`--fast`/`--chrome`): a flag passed for a
  provider that does not declare it is **rejected** with a validation error.
  This is a **deliberate divergence** from the existing `resolve_overrides` /
  `update-project settings` convention, which *silently drops* unsupported
  fields — for an explicit, human-typed CLI an error beats a silent no-op
  ("`--fast` is not supported by codex"). Flags reuse the **existing client-side alias
  resolution and validation** from `cli/_drop_request/aliases.py`
  (`resolve_overrides`, `clamp_untrusted_permission_mode`) so `--model max`,
  `--context-max 1m`, `--permission-mode safe`, etc. resolve and invalid combos
  are caught before the drop — identical to `create-session`.
- `--untrusted-permission-mode` maps to
  `{provider}DefaultUntrustedPermissionMode`
  (`UNTRUSTED_PERMISSION_MODE_SYNCED_KEY`), restricted to
  `UNTRUSTED_PERMISSION_MODES`. Exposed because `twicc update-project` already
  exposes trust mutation (`--trust`/`--untrust`/`--reset-trust`) — consistent,
  and the key driver for the limited-front-end scenario. (This is the *global
  untrusted default*, not the per-project `trust` boolean, which stays on
  `update-project`.)
- **Usage files**: `--usage-read-file PATH` sets the path **and** enables
  (`…ReadFilePath` + `…ReadFileEnabled=true`); `--no-usage-read-file` disables
  (keeps the path). Same for `--usage-dump-file` / `--no-usage-dump-file`.
- **Sub-commands** mutate the cross-provider keys, all through the shared merge
  service so the orchestrator transitions / safety / rebind apply:
  - `enable` / `disable` → add/remove the provider in `disabledProviders`.
  - `set-default` → set `defaultProvider` to this provider (must be enabled).
  - `orchestration-enable` / `orchestration-disable` → toggle membership in
    `orchestrationDisabledProviders` (the soft auto-pick opt-out).

### 4.3 `settings notifications`

Targets are edited by **stable `id`**. The CLI reads the current list
(offline), applies the add/update/remove locally, and sends the **whole new
`externalNotificationTargets` list** as the `settings:update` patch (the WS path
overwrites the key the same way — last-write-wins, fine for a one-shot CLI
action).

- `add <url>`: append `{ id: <new uuid>, name, url, enabled, tested: null,
  notifyUserTurn, notifyPendingRequest, notifyExtraUsageStart, awayOnly }` with
  flags setting the booleans (defaults = opted in / enabled / away-only as in
  the UI). Returns the new `id`.
  - `--test`: run `add`; **only if the add succeeds**, run `test` on the new
    target and report the test outcome.
- `update <id>`: patch the named fields. Changing `--url` resets `tested` to
  `null` (mirrors `onExternalTargetUrlChange`).
- `remove <id>`: drop the target.
- `test <id>`: **own server path** (not a plain settings write) — a dedicated
  drop kind `settings:notification_test` whose service resolves the target by
  id, captures its url, calls
  `external_notifications.test_notification_urls([url])`, then persists `tested`
  (true/false) through the shared merge. Reuses the exact server function the UI
  uses. **Stale-url guard**: before persisting, the service re-reads the target
  and writes `tested` only if its url is unchanged since the capture — mirrors
  the UI guard (`NotificationSettings.vue` re-checks `url === url` before
  persisting `tested`), so a `tested:true` is never written against a url that
  was edited mid-test. The drop reports success as `updated` (see §5.3).

`publicBaseUrl` and `notifyOnExtraUsageStart` stay on the generic backbone (not
duplicated here).

### 4.4 Errors, exit codes, remote

- Validation (bad key/value/flag combo, unknown provider, unknown target id)
  is done client-side before the drop → exit 1 with the standard validation
  envelope (`emit_validation_errors`).
- Server outcomes mirror `update-workspace`: 0 ok, 3 rejected (e.g. a refused
  provider disable — `corrections` surfaced to the user), 4 failed, 5 timeout,
  2 server-down.
- All write commands need the live server (drop-requests). Reads are offline.
- Settings commands are **remote-forwardable** like workspace/project
  mutations (not added to `LOCAL_ONLY_COMMANDS`).

## 5. Backend changes (the core work)

### 5.1 New shared service — `src/twicc/core/services/settings_mutation.py`

Extract the body of `asgi.py::_handle_update_synced_settings` into a reusable
async service so WS **and** CLI share identical semantics:

```python
# The WS-service return (broadcasts itself when broadcast=True).
class SettingsUpdateResult(NamedTuple):
    status: str            # "accepted" | "rejected"
    version: int
    corrections: dict
    clean: dict            # full clean settings (resync / CLI display)

async def update_synced_settings(
    patch: dict, *, base_version: int | None = None, broadcast: bool = True,
) -> SettingsUpdateResult: ...

# The drop-glue return — the watcher reads `.success` / `.errors`, and merges
# `.status_extra` into the status file (a generic passthrough added to the
# watcher) so `corrections` / `tested` reach the CLI.
class SettingsDropError(NamedTuple):
    field: str
    code: str
    message: str

class SettingsDropResult(NamedTuple):
    success: bool
    errors: tuple = ()
    status_extra: dict = {}

async def update_synced_settings_from_payload(payload: dict) -> SettingsDropResult: ...
async def notification_test_from_payload(payload: dict) -> SettingsDropResult: ...
```

The optimistic-concurrency check runs only when `base_version is not None`; the
CLI passes `None`. The `SettingsUpdateResult` "rejected" status is the
stale-baseVersion case (WS only). CLI-side validation happens before the drop,
so the drop glue trusts the patch and maps accept → `success=True`.

- `update_synced_settings(patch, base_version=None)` does the
  lock+merge+consistency+disabledProviders-safety+rebind+version-bump+write
  (the current `_merge_and_write` closure) **and** the orchestrator transitions
  + `synced_settings_updated` broadcast (both call sites are async). The
  optimistic-concurrency check runs only when `base_version is not None` — so
  the CLI (a fresh read-modify-write each call) passes `None` and is
  last-write-wins, exactly like the current "old client" branch.
  **Preserve verbatim** the orchestrator-delta computation from asgi: the
  `to_start`/`to_stop` sets are derived from *what was running* vs *what should
  run* using `old_key_present` / `old_running` / `new_running` — NOT from a diff
  of `disabledProviders`. Naïvely diffing the disabled sets breaks first-time
  activation (when the key was absent, nothing was running, so a disabled-set
  diff yields an empty `to_start`).
- `update_synced_settings_from_payload` unwraps the patch from the drop payload
  and calls the above; returns a drop result (success/errors) for the watcher.
- `notification_test_from_payload` implements §4.3 `test`.

### 5.2 `src/twicc/asgi.py`

`_handle_update_synced_settings` becomes a thin caller: validate shape, call
`update_synced_settings(subset, base_version=client_base_version)`, and keep
the WS-only behaviour (resync only this client on rejection). No behaviour
change for the UI.

### 5.3 `src/twicc/drop_requests_watcher.py`

Register in `_KIND_HANDLERS`:

```python
"settings:update": ("twicc.core.services.settings_mutation",
                    "update_synced_settings_from_payload", "updated"),
"settings:notification_test": ("twicc.core.services.settings_mutation",
                               "notification_test_from_payload", "updated"),
```

The success status **must** be one `poll_status` / `build_final` already treat
as terminal — `created`/`sent`/`updated`/`stopped`/`deleted`/`rejected`/`failed`
(`cli/_drop_request/polling.py`, `output.py`). A fresh status string like
`"tested"` would never be recognised and the CLI would dead-end at timeout
(exit 5) even on success. `notification_test` therefore reports `updated` (it
does persist the `tested` field); the test outcome (sent ok / Apprise error) is
carried in the status payload body, not the status string. (Same constraint
transitively covers `add --test`.)

### 5.4 `src/twicc/cli/`

New `settings` sub-package, lazy Django setup inside command bodies (fast
`--help`), reusing `_drop_request/*` (drop_file, polling, output, validation)
and `_drop_request/aliases.py` for agent-settings resolution:

- `cli/settings/command.py` — the `settings` Typer group (no sub-command →
  full dump), plus `get`, `set`, `unset`.
- `cli/settings/provider.py` — `settings provider <p>` group: callback
  (agent-defaults flags + show-on-empty) and the `enable` / `disable` /
  `set-default` / `orchestration-enable` / `orchestration-disable`
  sub-commands. Because the callback carries flat option flags *and* the group
  has sub-commands, this group needs the **custom `TyperGroup`** that flips
  `allow_interspersed_args = True` when the token after `<provider>` is not a
  known sub-command — copy the `_FlatBackcompatGroup` pattern from
  `cli/update_project/command.py` (without it, flags after the positional with
  no sub-command, e.g. `settings provider claude_code --model opus`, won't
  parse onto the callback). The untrusted-permission-mode alias handling also
  needs the extra branch `update_project settings` demonstrates (the
  `permission_mode_if_untrusted` aliasable field), on top of `resolve_overrides`.
- `cli/settings/notifications.py` — `list` (default) / `add` / `update` /
  `remove` / `test`.
- Register the group in `cli/__init__.py`.

### 5.5 `src/twicc/cli/info/`

`info` is a **single command with a validated positional `sections` list**
(`VALID_SECTIONS = ("presets", "commands", "models", "agent-settings")` in
`cli/info/command.py`), not a sub-command host. So `twicc info settings` is
invoked exactly as written, but implementing it means: add `"settings"` to
`VALID_SECTIONS` and add an `info/settings.py` build module emitting the schema
(key → type, default, owner: generic / provider / notifications / excluded) so
callers can script without guessing keys — same spirit as `info agent-settings`.

### 5.6 Docs

Update `SKILLS-AND-CLI.md` (root) with the new commands (per the project doc-
sync rule). **No new skill, no `plugin.json` bump.** Opportunistically fix the
stale notification-target shape comment in `synced_settings.py` (add
`id`/`name`/`notifyExtraUsageStart`).

## 6. Decisions (agreed)

1. Exclude `waTheme`, `waBrand`, `defaultLayoutId` from mutation (visual only).
2. Merge provider activation + per-provider agent defaults under a single
   `settings provider <p>` (enable/disable/set-default/orchestration-* as
   sub-commands; agent defaults + usage files as callback flags).
3. No agent skill; only `SKILLS-AND-CLI.md`.
4. Expose `--untrusted-permission-mode` (consistent with `update-project`'s
   trust flags; serves the limited-front-end use case).
5. Notification target identity = stable `id` (not url).
6. `settings provider <p>` with no flags = show.
7. Keep both `notifications test <id>` and `add --test` (add then test-if-ok).
8. Usage-file settings = flags of `settings provider <p>`.
9. A provider-defaults flag inapplicable to the target provider is **rejected**
   (validation error), a deliberate divergence from the silent-drop convention
   of `resolve_overrides` / `update-project settings`.

## 7. Known issues / deferred

- **Concurrent target edits**: notifications use whole-list overwrite, so a
  CLI edit racing a UI edit is last-write-wins. Acceptable for a one-shot CLI
  action; documented, not guarded.
- **Agent-defaults consistency on partial change**: the provider
  `enforce_synced_settings_consistency` only fires when the model key is in the
  change set (matches the WS path, where the UI always sends model + related
  together). The CLI closes the gap with the existing client-side validation
  (`cli/_drop_request/aliases.py`) so an invalid effort/context/model combo is
  rejected before the drop.
- **Per-project `trust` boolean** is not duplicated here — it already lives on
  `twicc update-project`.
- The three visual keys remain UI-only by decision (read still shows them in
  the full dump; only mutation is blocked).

## 8. Why hybrid (rationale)

`settings.json` is an open-ended, fast-churning dict. Its scalar tail is best
served by a generic, type-from-default setter that needs zero maintenance as
keys are added. But three families carry behaviour the generic setter would
silently break: provider activation (orchestrator transitions + safety + rebind),
the per-provider agent bundle (alias resolution + the model-pivot consistency),
and the notification object list (`tested`-gated, id-keyed). Those get dedicated
commands that route through the same shared merge service the UI uses, so the
CLI and the panel can never diverge.
