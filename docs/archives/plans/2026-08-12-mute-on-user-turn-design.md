# Per-session "mute on user turn" flag — design

Date: 2026-08-12
Status: design, approved in dialogue, not implemented

## 1. Problem

When an agent goes back to `USER_TURN`, TwiCC tells the user in four ways: an
in-app toast, a sound, a browser notification, and an external (Apprise)
notification. This is right for a session the user drives, and wrong for a
session spawned by another session.

In an orchestration tree, a parent spawns children that report back to the
parent. The user wants those children visible — so `--hidden` is not the
answer — but does not want one "finished working" notification per child.

The other alerts stay wanted, including on a spawned child: a pending request
means the child is blocked and nobody but the user can unblock it, and a
crash or a timeout means work stopped.

## 2. Current behaviour

Every claim in this section is about code as it exists today. The rest of the
document states decisions.

### 2.1 The single broadcast

`broadcast_process_state` (`src/twicc/asgi.py`) is the one callback the agent
manager registry fires on a process-state change. In order, it:

1. reads `Session.hidden` for the session id with a `values_list("hidden",
   flat=True).first()` query, and returns early when it is true — a hidden
   session produces no `process_state` message at all;
2. builds the message with `serialize_agent_info`;
3. lets the provider enrich it (`enrich_agent_state`);
4. adds `session_title`, `project_name` and `project_parent_name` from
   `get_session_and_project_display`, so a client can render a notification
   for a session it has not loaded;
5. calls `notify_agent_event` (external notifications);
6. `group_send`s the message to the `updates` group.

`serialize_agent_info` (`src/twicc/agent/states.py`) never touches the
database. Every DB-derived field on a `process_state` message is added by
`broadcast_process_state`.

`Session.hidden` carries `db_index=True` and is documented on the model as
absent from "every list, search result, count, and broadcast".

### 2.2 The frontend notification path

`notifyProcessStateChange` (`frontend/src/composables/useWebSocket.js`) is
called from the `process_state` branch of the WS message handler, with the
previous state read from `store.processStates[msg.session_id]` before the
store is updated. It contains three blocks and two non-notification calls:

| Block | Condition | Effects |
|---|---|---|
| user turn | `msg.state === 'user_turn'` and previous state differs | `forceNotifySessionViewed` when the session is on screen; in-app toast when it is not, deduplicated through `__hmrState.activeUserTurnToasts`; `playNotificationSound(settings.notifUserTurnSound)`; `sendBrowserNotification` when `settings.notifUserTurnBrowser` |
| pending request | `msg.pending_requests.length` grew | in-app toast when the session is not on screen; `playNotificationSound(settings.notifPendingRequestSound)`; `sendBrowserNotification` when `settings.notifPendingRequestBrowser` |
| death | `msg.state === 'dead'` | toast for `kill_reason` in `error`, `timeout_starting`, `timeout_assistant_turn`, `timeout_assistant_turn_absolute`; `useDataStore().failPendingSendsForSession` for `error`, `startup-failed`, `timeout_starting` |

`playNotificationSound` in the user-turn block sits outside the
`!isViewingSession` guard: the sound plays even when the user is looking at
the session.

`forceNotifySessionViewed` marks the session read. `failPendingSendsForSession`
recovers a message whose delivery was never confirmed. Neither is a
notification.

### 2.3 The external notification path

`notify_agent_event` (`src/twicc/external_notifications.py`) wraps
`_detect_and_send`, which:

1. reads the previous `(state, pending_count)` from the module-level
   `_last_seen` dict;
2. **writes the current one back into `_last_seen` before anything else** —
   the module comment states this is deliberate, so that enabling targets
   later starts from fresh state instead of replaying old events;
3. reads `externalNotificationTargets` from the synced settings and returns
   when none is enabled, tested and has a URL;
4. appends a `(title, opt_in_key)` event per detected transition:
   `notifyUserTurn` for the transition into `USER_TURN`, `notifyPendingRequest`
   for a grown pending-request count;
5. splits eligible targets by their `awayOnly` flag and dispatches.

### 2.4 Mutable per-session flags that are not agent settings

`archived`, `pinned`, `layout` and `browser_url` are mutable `Session`
columns outside the `AgentSettings` bundle. Each is written the same way:

- an `apply_session_<field>_change` helper in
  `src/twicc/core/services/session_update.py` writes under
  `run_under_db_write_lock` and does **not** broadcast;
- the HTTP `PATCH /api/projects/<id>/sessions/<id>/` branch in
  `session_detail` (`src/twicc/views.py`) calls the helper, sets
  `needs_broadcast`, and one combined `session_updated` broadcast fires at the
  end of the handler — skipped when `session.hidden`;
- for `archived` and `pinned`, an `update_session_<field>_from_payload`
  service is the CLI entry point, registered in `_KIND_HANDLERS`
  (`src/twicc/drop_requests_watcher.py`) under a `session:update_<field>` kind,
  and calls `_lookup_session_for_update` first (rejects a missing, subagent,
  or stale session, a project without a directory, an unknown provider, and a
  disabled provider).

`update_session_settings_from_payload` rejects any key absent from
`AgentSettings._fields`, so a non-bundle field cannot travel through
`twicc update-session <ID> settings`.

`serialize_session` (`src/twicc/core/serializers.py`) emits `hidden`,
`archived`, `pinned`, `layout`, `browser_url` and the bundle fields. It is the
payload of the REST detail endpoint, of the `session_updated` broadcast, and
of every CLI and MCP session read.

### 2.5 The creation path

`create_session_from_payload` (`src/twicc/core/services/session_creation.py`)
does not create the `Session` row. It reads `hidden` from the payload and
hands it, with the spawn links, annotations, system-prompt addendum, hybrid
flag and layout, to `set_pending_session_attributes`
(`src/twicc/pending_session_attributes.py`), whose `PendingSessionAttributes`
NamedTuple holds them in memory.

`BaseSessionsWatcher._create_session_row`
(`src/twicc/providers/sessions_watcher.py`) pops that buffer when the first
JSONL line makes it create the row, and copies each field into the
`Session.objects.create(...)` kwargs.

`BaseAgentManager` (`src/twicc/agent/base_manager.py`) re-keys the buffer when
a provider assigns a canonical session id different from the draft id: it pops
under the draft id and calls `set_pending_session_attributes` again under the
canonical id, field by field. Its comment states that every field must be
forwarded or it silently reverts to its default.

When no pending entry exists (server restarted between the create and the
first JSONL line), the row is created with the model defaults.

### 2.6 The CLI and MCP surface

`twicc create-session` (`src/twicc/cli/create_session/command.py`) declares
`--hidden` as a plain flag, and paired booleans as `bool | None` typer options
with a `"--flag/--no-flag"` spec: `--thinking/--no-thinking`,
`--claude-in-chrome/--no-claude-in-chrome`, `--fast-mode/--no-fast-mode`,
`--question-widget/--no-question-widget`.

`twicc update-session <ID>` has one module per action under
`src/twicc/cli/update_session/`. `hidden_command.py` holds `update_hide_cmd`
and `update_unhide_cmd`, two thin wrappers over one `_run_hidden_update`
helper that ensures the server is up, resolves the session with
`lookup_session`, submits the drop payload through
`twicc.cli._drop_request.transport`, waits, and maps the outcome status to an
exit code (0 updated, 3 rejected, 4 failed, 5 timeout).

`twicc update-sessions` (`src/twicc/cli/update_sessions/command.py`) exposes
one sub-command per batch action, each delegating to `run_batch`
(`src/twicc/cli/_batch_runner.py`) with a `kind` and a `prepare` callable
building the per-session payload, plus the shared `--spawned-by`,
`--descendants`, `--annotation` and `--timeout` options.

The MCP tool list is derived from the Click tree by `build_mcp_registry`
(`src/twicc/mcp/tools.py`), minus `MCP_EXCLUDED_ROOTS`. That set is the
local-only commands without `whoami`, plus `settings` and `share`. Neither
`update-session` nor `update-sessions` nor `create-session` is excluded, and
tool names are the registry path with `/` and `-` mapped to `_`.

### 2.7 The session header

`SessionHeader.vue` (`frontend/src/components/session/detail/`) renders a
`.session-title-actions` group, only when its `mode` prop is `session`. In DOM
order: the archived tag, the draft tag, the stale tag, the search button, the
pin dropdown, the archive button, the rename button. The pin dropdown and the
archive button both carry `v-if="!session.draft"`; the archive button also
requires `!session.archived`.

The pin trigger is `variant="brand"` with a `pin-button--active` class when
`session.pinned` is set, and neutral otherwise: the highlighted state is the
exception, and the default state is discreet. Its tooltip text comes from a
`pinTooltip` computed. Selecting a mode calls `handlePinSelect`, which calls
`store.setSessionPinMode`.

`setSessionPinMode` (`frontend/src/stores/data.js`) mutates the store
optimistically, `PATCH`es the session endpoint, merges the response into
`this.sessions[sessionId]`, and rolls the optimistic value back on failure.

### 2.8 Icon availability

Web Awesome's default icon library resolves an icon name to
`https://ka-f.fontawesome.com/releases/v<FA_VERSION>/svgs/<folder>/<name>.svg`
when no Pro kit code is set (`getIconUrl`, in the chunk re-exported by
`frontend/node_modules/@awesome.me/webawesome/dist/components/icon/library.default.js`).
Probed against that URL on the version this repo bundles: `bell` and
`bell-slash` answer 200, `bell-on` answers 403 (Pro-only).

## 3. Decisions

### 3.1 The flag, and its polarity

A new `Session` column, `mute_on_user_turn`, `BooleanField(default=False)`, no
`db_index`. It is never a query filter — it is read for one row at a time on
the broadcast path — so an index would cost writes and buy nothing.

Placed next to `hidden` in the model's user-controlled block.

The flag names the exception, not the default. `mute_on_user_turn=True`
silences; every other value — `False`, `NULL`, a missing key in a payload, a
row that does not exist yet — notifies. That property is structural, not a
convention some reader must uphold: silence requires an explicit `True`, so no
absent or unset value can ever mute a session by accident.

The rejected alternative was `notify_on_user_turn`, `default=True`. It reads
more directly, and it fails on the same property: every consumer would have to
test `=== false` rather than truthiness, because any falsy-by-absence value
would silence the session.

Naming it `mute_` rather than `do_not_notify_` keeps the polarity without a
double negative in the column name, the serializer key, the WS key, the CLI
flag, and every read site.

### 3.2 What it suppresses, exactly

`mute_on_user_turn=True` suppresses the "<Provider> finished working" family
and nothing else:

| Effect | Suppressed |
|---|---|
| In-app toast on the transition into `USER_TURN` | yes |
| `notifUserTurnSound` sound | yes |
| Browser notification titled "<Provider> finished working" | yes |
| Apprise event with opt-in key `notifyUserTurn` | yes |
| Anything in the pending-request block | no |
| Anything in the death block | no |
| Apprise event with opt-in key `notifyPendingRequest` | no |
| Apprise extra-usage event (`notify_extra_usage_started`) | no |
| `forceNotifySessionViewed` read-tracking | no |
| `failPendingSendsForSession` recovery | no |
| Unread badges, session-list ordering, active-process cross-filter | no |

The name states the scope. A flag that silenced a blocked child's question,
or its crash, would hide exactly the events the user must act on.

### 3.3 Relation to the global notification settings

For sound, browser notifications and each Apprise target, the existing global
setting remains the first gate. `mute_on_user_turn` adds a per-session gate: it
can suppress an enabled channel, but it cannot enable a globally disabled one.
The in-app toast has no independent global setting; its existing visibility
rule and the per-session flag control it. This feature changes no global
setting.

### 3.4 Relation to `hidden`

The two flags are independent columns with no validation between them. A
hidden session never notifies, because `broadcast_process_state` returns before
building the message; its `mute_on_user_turn` value is therefore inert while
it stays hidden, and becomes effective again if it is unhidden.

Setting `mute_on_user_turn` on a hidden session is accepted, not rejected:
rejecting it would force a caller to unhide before muting.

### 3.5 Not an agent setting

`mute_on_user_turn` does not join the `AgentSettings` bundle, and
`twicc update-session <ID> settings` does not accept it.

The bundle is per-provider (each provider declares the fields it uses through
`getAgentSettingsCategories`), it is resolved once at session creation, it
feeds presets and `Project.default_agent_settings`, and it is propagated to the
running agent through the provider SDK. `mute_on_user_turn` is none of those:
it is TwiCC-side, provider-agnostic, mutable at any moment, and never reaches
the agent.

### 3.6 Where the value is read

At each broadcast, from the database, in the query that already reads `hidden`.

`broadcast_process_state` already runs one `values_list` per state change.
Widening it to `values_list("hidden", "mute_on_user_turn")` adds no query and
no round-trip. The value is therefore always current, including a change made
while the agent is mid-turn, with no cache and no propagation to the agent
manager.

The alternative — caching the flag on the live agent process — would need an
invalidation path from every writer, and would buy nothing measurable.

### 3.7 How the value reaches the frontend

`broadcast_process_state` adds `mute_on_user_turn` to the `process_state`
message, next to `session_title`.

The frontend cannot read it from its own store: in the orchestration case the
spawned session is frequently absent from it — the user is on the projects
list, or in another project. That absence is the reason `session_title` and
`project_name` are already injected into this message.

Consumers test the value for truthiness. Per §3.1 that is safe by
construction: a message whose key is absent, or whose row did not exist at
broadcast time, notifies.

### 3.8 The `_last_seen` baseline

`notify_agent_event` gains a `mute_on_user_turn` parameter. `_detect_and_send`
updates `_last_seen` before selecting events, as it does today. On a muted
transition into `USER_TURN`, it skips only the `notifyUserTurn` append and
continues through the rest of the function.

The call is **not** skipped at the call site. `_last_seen` is the baseline
against which the next broadcast detects a transition. Skipping the call
leaves it stale, so the first broadcast after the user unmutes the session
compares against an old entry — or against no entry at all, which the code
reads as a transition into `USER_TURN` — and fires a notification for an event
that already happened.

The function must not return because the session is muted. Pending-request
event detection stays reachable on every call and remains independent of the
user-turn gate.

### 3.9 The frontend gate placement

The gate wraps the toast, the sound and the browser notification inside the
user-turn block. It does not wrap `notifyProcessStateChange`, and it does not
wrap the user-turn block as a whole.

`forceNotifySessionViewed` sits in that block and marks the session read. A
gate placed at the top of the function, or around the whole block, would stop
a muted session from ever being marked read, and its unread badge would
survive the user reading it.

`__hmrState.activeUserTurnToasts` is only touched on the path that shows a
toast, so a muted turn leaves the dedup set untouched.

### 3.10 The control: one button in the session header

The only UI is a toggle button in `SessionHeader.vue`, between the pin
dropdown and the archive button, carrying `v-if="!session.draft"` like both of
its neighbours.

There is no control in the agent-settings popover. That popover applies its
changes on the next Send — its callout says so, `hasDropdownsChanged` drives a
"Discard unsaved changes" link, and presets and resets rewrite every field it
owns. An immediately-applied field placed among them would be read as
deferred, and would have to be excluded from three separate mechanisms.

There is no indicator in the session list, and no column in any list view.

### 3.11 The button's appearance

Same rule as the pin button: the default state is discreet, the exception is
marked. `mute_on_user_turn` is falsy by default, so the two read the same way
in the template.

| State | Icon | Styling |
|---|---|---|
| `mute_on_user_turn` falsy | `bell` | `variant="neutral"`, `appearance="plain"`, like the search and archive buttons |
| `mute_on_user_turn` true | `bell-slash` | a marked state, distinct from the neutral one, and distinct from the pin's `brand` |

`bell-on` is not used: it is Font Awesome Pro and would 403 (§2.8).

Tooltip text, through `AppTooltip` like its neighbours:

- falsy: `Notifications on — click to mute the "finished working" notification`
- true: `Muted — click to restore the "finished working" notification`

### 3.12 The write path

A `setSessionMuteOnUserTurn(projectId, sessionId, value)` action in
`frontend/src/stores/data.js`, modelled on `setSessionPinMode`: optimistic
store mutation, `PATCH` with `{ mute_on_user_turn: value }`, merge of the
response, rollback on failure.

A `mute_on_user_turn` branch in `session_detail`'s `PATCH` handler
(`src/twicc/views.py`), rejecting a non-boolean with HTTP 400, calling
`apply_session_mute_on_user_turn_change` and setting `needs_broadcast` so the
change rides the handler's single combined `session_updated` broadcast.

An `apply_session_mute_on_user_turn_change(session, value)` helper in
`src/twicc/core/services/session_update.py`, writing under
`run_under_db_write_lock` and not broadcasting, exactly like
`apply_session_pinned_change`.

`serialize_session` emits `mute_on_user_turn`, which is what carries the value
to the header button, to the CLI and to MCP.

### 3.13 The creation path

`--mute-on-user-turn` on `twicc create-session`, a plain flag like `--hidden`,
not a `--flag/--no-flag` pair. Two things make the negative form unnecessary:
the polarity (omitting the flag already means "notify"), and the single
creation default (§5) — there is no context in which the caller would need to
override a computed value back to "notify".

Its typer `help=` string is where the "how to choose" guidance lives for
agents, because `json_schema_for` (`src/twicc/rpc/schema.py`) copies a
parameter's help into `schema["description"]`, and that schema is what
`build_mcp_registry` serves. One string covers `twicc create-session --help`,
the `/rpc/` schema and the MCP tool parameter.

`create_session_from_payload` reads the key from the payload and forwards it to
`set_pending_session_attributes`.

`PendingSessionAttributes` gains the field, and **both** consumers of that
buffer are updated:

- `BaseSessionsWatcher._create_session_row` copies it into the
  `Session.objects.create(...)` kwargs, unconditionally, the way it copies
  `hidden`;
- the re-keying block in `BaseAgentManager` forwards it when it moves the
  buffer from the draft id to the canonical id.

Missing the second one produces a defect visible only on Codex, and only for
a session whose canonical id differs from its draft id: the flag is accepted,
reported as applied, and silently lost.

When no pending entry exists at row creation, the column takes its model
default, `False`. This is also what every session TwiCC merely *discovers*
gets — initial sync and the file watcher create rows without a pending entry,
so a session TwiCC did not create is never muted.

### 3.14 The mutation path outside the UI

A new `src/twicc/cli/update_session/mute_command.py`, modelled on
`hidden_command.py`: one `_run_mute_update` helper and two commands,
`twicc update-session <ID> mute` and `twicc update-session <ID> notify`,
differing only by the boolean in the payload.

A new drop-request kind `session:update_mute_on_user_turn`, registered in
`_KIND_HANDLERS`, resolving to `update_session_mute_on_user_turn_from_payload`
in `src/twicc/core/services/session_update.py` with status `updated`. That
service validates the boolean, calls `_lookup_session_for_update`, calls
`apply_session_mute_on_user_turn_change`, and broadcasts `session_updated`
unless the session is hidden — the shape of
`update_session_pinned_from_payload`.

Two batch sub-commands on `twicc update-sessions`, `mute` and `notify`, each
calling `run_batch` with the same kind and the shared scope options. This is
the orchestration path: one call with `--spawned-by self` mutes every child.

No MCP work: `update_session_mute`, `update_session_notify`,
`update_sessions_mute` and `update_sessions_notify` follow from the Click tree
(§2.6).

## 4. Session states

Every state a session can be in, and what this feature does in it.

| State | Behaviour |
|---|---|
| Draft (no row yet) | No button (`v-if="!session.draft"`). No PATCH target. The flag is settable only through `create-session`. |
| Subagent (`type=SUBAGENT`) | Nothing new. The header actions render only for `mode === 'session'`, and `_lookup_session_for_update` already rejects subagents for every CLI update. |
| Hidden | No `process_state` broadcast at all, so the flag is inert (§3.4). The button is unreachable: the frontend never receives a hidden session. CLI and MCP accept the mutation. |
| Archived | Button shown (its neighbours' `!session.draft` rule, not the archive button's extra `!session.archived`). No live process, so nothing to notify about until it is resumed. |
| Stale | Same as the pin flag: the HTTP `PATCH` accepts it, the CLI path rejects it through `_lookup_session_for_update`. No new rule. |
| Discovered, not created by TwiCC | `False` — initial sync and the watcher create the row without a pending entry (§3.13). |
| Pre-existing at migration time | `False`, from the column default. |
| Row not yet created, broadcast already firing | `values_list(...).first()` returns `None`. Treated as not muted, like the current `hidden` handling. |
| Not loaded in the frontend store | Gated correctly: the flag rides on the message (§3.7). |
| On screen when the turn ends | Toast already skipped by the existing `isViewingSession` guard; the sound is additionally skipped when muted. |
| No live process | No broadcast, no notification, flag untouched. |

## 5. Out of scope

Said, not implied — none of the following is part of this feature:

- No inheritance. There is no `Project.default_mute_on_user_turn`, no
  workspace-level default, no global setting. The column's `default=False` is
  the only default.
- **No context-dependent creation default.** A session created by an agent
  gets the same default as one created by a human: not muted. Muting is always
  an explicit `--mute-on-user-turn`.

  The rejected alternative was to derive the default from
  `spawned_by_session_id`, which `create_session_from_payload` already receives
  and which is set identically on both agent paths — the MCP dispatcher
  (`dispatch_tool`, `src/twicc/mcp/server.py`) binds the caller into
  `forced_session_id` and then runs the same CLI command, so
  `resolve_current_session` returns the calling session whether the agent used
  the MCP tool or shelled `twicc create-session`. It was rejected because both
  intentions are real and equally common: a session an agent spawns to control
  itself, and a session an agent creates for the user to read. The predicate
  cannot tell them apart, and guessing wrong silences a session the user was
  meant to see. The choice belongs to the caller, and the agent-facing
  documentation (§7) is what teaches it.

  There is consequently no way, and no need, to detect the MCP transport, or a
  skill — a skill is text in an agent's context, and the call it produces is an
  ordinary CLI invocation.
- No mention in the TwiCC system-prompt addendum. `compose_addendum` receives
  `hidden`; it does not receive this flag, and an agent has no reason to reason
  about its own notification state.
- No suppression of the pending-request, death, or extra-usage notifications
  (§3.2).
- No session-list or sidebar indicator (§3.10).
- No control in the agent-settings popover, in presets, or in
  `Project.default_agent_settings` (§3.5, §3.10).
- No retroactive muting: the flag is read at broadcast time, so an event
  already delivered stays delivered.

## 6. Verification

Named means, each with the observation that distinguishes a correct
implementation from a broken one. The steps belong to the implementation plan.

- **Broadcast payload carries the flag.** A pytest case driving
  `broadcast_process_state` for a session with `mute_on_user_turn=True`
  asserts the key is present and `True` on the captured `group_send` payload.
  When the enrichment is missing the key is absent, the assertion fails, and in
  the running app the session would keep notifying.
- **An absent key notifies.** A node:test case over the gating predicate with a
  message carrying no `mute_on_user_turn` key asserts the notification path is
  taken. It fails if the gate is ever written as an inverted test
  (`!== false`, or a `notify_*` reading), which would silence every session.
- **Frontend pending requests remain active while muted.** A node:test case
  covers two messages with `mute_on_user_turn=true`: a transition into
  `USER_TURN`, and a separate grown pending-request count. It asserts that the
  user-turn toast, sound and browser notification are suppressed, while the
  pending-request toast, sound and browser notification still fire. It fails
  if the gate wraps the whole function instead of only the user-turn
  notification effects.
- **The Apprise baseline survives a muted turn.** A pytest case calls
  `notify_agent_event` for a `USER_TURN` broadcast with the flag true, then
  again with the flag false and the state still `USER_TURN`, with one enabled,
  tested target configured. It asserts no send is dispatched on either call.
  When the call is skipped at the call site, the second call sees no
  `_last_seen` entry, treats it as a fresh transition, and dispatches — the
  assertion fails.
- **Apprise pending requests remain active while muted.** A pytest case calls
  `notify_agent_event` with the flag true, state `ASSISTANT_TURN` and a grown
  pending-request count. It asserts that the `notifyPendingRequest` event is
  dispatched. It fails if `_detect_and_send` returns early for a muted
  session.
- **Round trip through the drop-request transport.** A pytest case submits a
  `session:update_mute_on_user_turn` payload and asserts the column changed
  and a `session_updated` broadcast carries the new value. When the kind is
  missing from `_KIND_HANDLERS`, the request ends `failed` instead of
  `updated`.
- **Creation through the re-keyed buffer.** A pytest case exercising the
  draft-id to canonical-id re-key asserts the flag survives it. When the field
  is not forwarded in `BaseAgentManager`, the created row carries `False`
  instead of the requested `True`.
- **MCP exposure.** The existing MCP tool-listing test asserts the four new
  tool names are present. When a sub-command is registered outside the Click
  tree, they are absent.
- Unverified by automation: the header button's appearance and its two
  tooltips.

## 7. Documentation to update

- `SKILLS-AND-CLI.md` at the repo root — the new sub-commands and the new
  `create-session` flag.
- The `twicc-create-session`, `twicc-update-session` and
  `twicc-update-sessions` skills under
  `src/twicc/agent/plugin/twicc/skills/`, and the `version` field of
  `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json` (a bundle change
  requires a bump; new flags make it a minor).
- The `Session` bullet of the **Database Models** section in `CLAUDE.md`, and
  its condensed mirror in `AGENTS.md`.
- The orchestration skills. `twicc-orchestration/SKILL.md` gets its own short
  section for this, **not** a bullet appended to its existing **Visibility and
  permission propagation** section. Muting is a third axis, orthogonal to the
  two that section names: a bullet under that title would read as a sub-case of
  visibility, and the confusion it invites — "hidden means silent, so visible
  means noisy" — is the exact belief this feature exists to break. A session an
  agent controls is very often one the user wants to *read* and not be
  *interrupted* by, and until now `--hidden` was the only way to stop the
  interruptions, at the cost of the reading.

  What the section must say, in this order: the two flags are independent and
  combine freely; mute a child you control yourself and that reports back to
  you, because its end-of-turn notification tells the user nothing they need;
  leave a session you create *for the user to read* unmuted. Both cases are
  real and neither is the majority (§5).

  The same file's existing `--hidden` recommendation is weakened in the same
  pass. It currently reads "**Strongly prefer `--hidden`** for the sessions you
  spawn (especially if you are hidden yourself): no UI clutter, and a hidden
  session can never get stuck on a UI dialog." It was written when hiding was
  the only way to stop the noise, so it bought silence at the cost of
  readability. Of its two motives, "no UI clutter" is largely the notification
  noise, which `--mute-on-user-turn` now removes without hiding anything, and
  "can never get stuck on a UI dialog" is already covered by the
  `--no-question-widget` bullet two lines below it.

  The new rule: `--hidden` when the user has no reason to read the child;
  otherwise visible, muted, and `--no-question-widget`. It stays a preference
  the user's own instruction overrides, like every other rule in that section.
  Not an inversion — an orchestration tree that is purely mechanical is still
  better hidden.

  `-leader` and `-manager` inherit both rules through their "read
  twicc-orchestration first" pointer, so they need a line only where they
  already restate spawn flags.
- `CHANGELOG.md`, `[Unreleased]` section only, on explicit request.
