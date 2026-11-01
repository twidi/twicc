# Agent-created shares — design

**Date:** 2026-08-10
**Status:** design, awaiting implementation plan
**Supersedes:** decision **O5** and §14 of `docs/plans/2026-07-05-sharing-design.md` (sharing is human-only). That document stays untouched as historical record; this one is the current state of the decision.

---

## 1. Purpose

Let a TwiCC agent create and manage share links on the user's behalf, behind an explicit user opt-in.

Two motivating flows:

- **Peer messaging.** "Share the artifact you just built with David" → the agent creates an artifact share and sends the URL in a peer message. The peer system carries text and attachments, so it cannot carry an interactive HTML artifact or a live session; a share link is the only vehicle.
- **Plain delegation.** "Create a share for this session and give me the link" → a chore the user delegates rather than does by hand.

The feature is **not** coupled to the peer system. Peer messaging is the motivating case, not a dependency.

**Delivery: one single lot.** This design is scoped for exactly one implementation plan, shipped in one pass. Do not decompose it into sub-lots, phases or independently shippable increments — that is a deliberate instruction from the user, not an oversight. Everything in §4 through §12, including the `self`/`parent` keyword fix of §11 and the documentation updates of §12, belongs to that one lot.

## 2. Current state (verified)

| Fact | Evidence |
|---|---|
| The `share` root is excluded from the MCP registry | `src/twicc/mcp/tools.py:27-29` — `MCP_EXCLUDED_ROOTS = (set(LOCAL_ONLY_COMMANDS) - {"whoami"}) \| {"settings", "share"}` |
| No `twicc-share` skill exists | `src/twicc/agent/plugin/twicc/skills/` has no such directory |
| `share` is **not** local-only, so the `/rpc/` route exists | `src/twicc/cli/_local_only.py` — `LOCAL_ONLY_COMMANDS = {"password", "claude", "codex", "run", "token", "whoami"}` |
| The CLI is reachable from an agent's Bash tool | Every `twicc-*` skill resolves `$TWICC` and shells out |
| `twicc share` (list) prints the **plaintext token** of every share | `src/twicc/core/serializers.py:355` — `"token": share.token` |
| Share reads run **client-side, straight on the DB** | `src/twicc/cli/share.py:14,42` — `list_main` / `show_main`, no server needed |
| Share mutations run **server-side** via drop-requests | `src/twicc/drop_requests_watcher.py:135-163` → `core/services/share_mutation.py` `*_from_payload` |
| The owner REST UI calls `create_share_from_payload` too, but `patch`/`revoke`/`delete`/`propagate` **directly** | `src/twicc/share/owner_views.py:64,89,94,102,109,116` |
| An empty `shareBaseUrl` blocks creation **only on the REST path** | `src/twicc/share/owner_views.py:44-48`; no equivalent guard in `share_mutation.py` |
| Caller identity is already solved and in production use | `src/twicc/cli/_drop_request/whoami.py:56` `resolve_current_session()`; MCP sets the ContextVar at `src/twicc/mcp/server.py:69`; `peer-send` uses it at `src/twicc/cli/peer_send.py:136-149` |
| The spawn tree is denormalised on `Session` | `src/twicc/core/models.py:465` `spawned_by`, `:479` `spawn_root` |
| `spawned_by` is written only by `twicc create-session` | `src/twicc/core/services/session_creation.py:333,350`; `src/twicc/pending_session_attributes.py:67` |
| Claude subagents use a **different** edge | `src/twicc/core/models.py:413` `parent_session` — subagent sessions therefore have `spawned_by` NULL |
| A descendants BFS over the spawn tree already exists | `src/twicc/cli/_drop_request/whoami.py:201` `resolve_descendants_filter` |
| Session mutations already refuse siblings and the wider tree | `SKILLS-AND-CLI.md:295` — *"No `parent`, no `--spawn-tree`, no `--siblings` (unlike `send-messages`: you don't batch-mutate your peers)"* |
| Agents can already bookmark artifacts, ungated | `src/twicc/cli/__init__.py:535`; skill `twicc-artifacts`; MCP tool `mcp__twicc__artifacts_bookmark` |
| A share of an artifact **requires** a bookmark | `src/twicc/core/models.py:1619` — `Share.artifact_bookmark` FK; CheckConstraint `share_target_matches_kind` (`:1639-1646`) forces exactly one target, matching `kind` |
| `propagate` re-freezes a snapshot session share to `session.last_line` | `src/twicc/core/services/share_mutation.py:334-349` |
| `propagate` on a `live` session share is refused | same, error `not_snapshot` |
| The CLI `update` command cannot flip `live` ⇄ `frozen` — but the service can: a raw drop-request payload reaches `patch_share`'s `options` handling (§7.2) | `src/twicc/cli/__init__.py:706-722` exposes `--label/--password/--expires` only; `patch_share` accepts `options` (`share_mutation.py:304-325`) via `update_share_from_payload` (`:434-438`) |
| Synced settings have a central defaults dict | `src/twicc/synced_settings.py:107` (`"shareBaseUrl": ""`) |
| The CLI/MCP result of `share create` **loses `share_id`**: the watcher copies it onto the status payload, but `build_final` has no share branch and falls through to the project projection (`project_id: null`) | `src/twicc/drop_requests_watcher.py:188-199` (`_RESULT_ID_FIELDS` includes `"share_id"`); `src/twicc/cli/_drop_request/output.py:28-34,52-61` (no share dispatch) |
| Artifact share creation and propagation **discard** the validated title options — the stored options become `{"snapshot_at": …}` only | `src/twicc/core/services/share_mutation.py:266-271` (create), `:347-350` (propagate); the public serializer reads `show_title`/`display_title` from that dict (`src/twicc/core/serializers.py:424-435`) |
| The list `--session`/`--project` filters match only `Share.session`, so artifact shares (`session` NULL by the CheckConstraint) are excluded | `src/twicc/cli/share.py:23-30` — `session_id=` / `session__project_id__in=` only |
| `shareBaseUrl` accepts a **bare hostname** ("only the hostname matters"), and both URL builders concatenate it without adding a scheme — the `url` field can be a schemeless string | `src/twicc/synced_settings.py:103-107`; `frontend/src/stores/settings.js:916-919`; `src/twicc/cli/share.py:9-11`; `frontend/src/utils/shareUrl.js:7-12` |
| An invalid non-empty expiry is **silently dropped** on the `_parse_expires` paths — a typo creates a never-expiring link (drop-request and REST create) or clears an existing expiry (drop-request update) | `src/twicc/core/services/share_mutation.py:398-405` (create via `:417`), `:292-299` (update); REST create funnels through the same wrapper (`owner_views.py:64`) |
| The owner REST PATCH parses expiry **in the view** and raises on invalid input before the service — an unstructured error, not a silent widening | `src/twicc/share/owner_views.py:86-88` — `datetime.fromisoformat(raw)` |
| Bare `twicc share create` (no subcommand) is a **silent no-op**: the group is `invoke_without_command=True` with no callback, so it "succeeds" with exit 0 and does nothing — and a callable group becomes an RPC/MCP operation | `src/twicc/cli/__init__.py:637` (no `@share_create_app.callback` exists); `src/twicc/rpc/generator.py:69-71` registers callable groups |

**Consequence.** O5 is a convention, not a boundary. An agent with a Bash tool can create shares and read every existing token today. This design replaces the convention with an enforced, user-controlled gate — which **closes** a hole in addition to opening a feature. "Closes" within the trust model of §5.2: the gate stops an obedient agent, not a hostile one (A17).

## 3. Decisions

| # | Question | Decision |
|---|---|---|
| **A1** | Exposure | Full surface: a `twicc-share` skill **and** MCP tools. No per-option masking of the MCP schema |
| **A2** | Gate | Two **global** synced settings, both **off** by default: sessions, artifacts. No per-project override (§13) |
| **A3** | Gate placement | The `*_from_payload` service wrappers, plus the two CLI read functions. Never the MCP registry (the CLI is reachable regardless) |
| **A4** | Tool visibility | Tools and skill are **always** exposed — never a missing tool. A disabled setting rejects **mutations** at call time; **reads** succeed with the §7.3 redaction (A11) |
| **A5** | Caller identity | `resolve_current_session()`, carried to the server as a payload field. Best-effort, not a security boundary (§5) |
| **A6** | Scope | `self` + **spawn-tree descendants**, for both the target and the provenance (§6) |
| **A7** | Revoke | With the kind's setting **on**, an agent may revoke a share of **any** provenance or spawn tree. Revoke bypasses only the §6 scope test, never the kind setting (§7.1). Un-publishing is always safe |
| **A8** | Provenance | New nullable FK `Share.created_by_session`. NULL means **human-created or legacy/unattributed** (§9) |
| **A9** | Session shares | An agent-created session share defaults to `frozen`; `--live` stays available and explicit (§8) |
| **A10** | Display ceiling | `--max-display debug` is refused to agents; the other three modes are allowed |
| **A11** | Reads | `list` / `show` stay available, but `token` and `url` are **redacted** for a kind whose setting is off (§7.3) |
| **A12** | `--password` | Agents may **set or replace** a password (create and update), never **clear** one — on update, `password` must be a non-empty string, every other supplied value is refused; the rules are stated by type, not value (§7.2, §8). Setting or replacing narrows or rotates exposure; clearing widens it and stays human-only |
| **A13** | Missing `shareBaseUrl` | Hard error for an agent caller. Human behaviour stays surface-specific and unchanged: REST creation already rejects it; CLI and full-token `/rpc/` creation remain permissive (§7.4) |
| **A14** | Notifications | Deferred. Peer messaging does not notify either; consistency first (§13) |
| **A15** | Bookmark clutter | Accepted as-is. A share of an artifact requires a visible bookmark; that visibility is a feature (§10) |
| **A16** | `self`/`parent` keywords | Resolved in this lot on `artifacts bookmark`/`unbookmark` **and** on the new share surface (`create session`, list `--session`) (§11) |
| **A17** | Setting self-flip | **Accepted.** An agent can enable the two settings itself via the CLI (`twicc settings` from Bash — MCP exclusion does not cover the CLI). TwiCC's agent surface is designed for parity with the human surface. Governing settings writes is a separate future decision, out of scope here (§5.2, §13) |

## 4. Settings

Two boolean keys in the synced settings defaults (`src/twicc/synced_settings.py`), both `false`:

```python
# Let agents create and manage session shares (skill + MCP + CLI from inside a
# session). Off: those calls are refused with `agent_sharing_disabled`.
"allowAgentSessionShares": False,
# Same, for artifact shares. The two kinds are independent.
"allowAgentArtifactShares": False,
```

UI: two switches in the existing **Settings → Sharing** section (`frontend/src/components/app/SettingsPopover.vue`), below `shareBaseUrl`. The copy must disclose the **full** effect — enabling a kind lifts the §7.3 read redaction (every existing token of that kind becomes readable) **and** arms A7's revoke-anything (an agent may un-publish any existing link of that kind, whatever its provenance — including a link the user created by hand). Required wording, per switch: *"Allows agents to create &lt;kind&gt; shares whose target belongs to their own spawn subtree, and to manage &lt;kind&gt; shares created by agents in their own spawn subtree. When enabled, agents can also revoke any existing &lt;kind&gt; share, including links created by you, and read the URL of every existing &lt;kind&gt; share, including links created by you or by another agent."* This is consent-relevant, not a nicety: the switch text is where the user learns the read and revoke sides before flipping it. Swept against the §3 decisions table: create, own-subtree management (A6/A12), revoke-anything (A7) and read-all (A11) are the complete set of switch-enabled powers — nothing else in the table is enabled by the flip (A9/A10 are restrictions, A15's bookmark step is not gated by these settings, A17 is a property of the trust model, not of the flip).

Both are read at each call through `read_synced_settings()` — a write-through in-process cache (`src/twicc/synced_settings.py:167`, refreshed by every `write_synced_settings`, `:302-303`), so a settings save is visible to the very next call; a CLI invocation is a fresh process and reads the file. No per-session snapshot: flipping a switch takes effect on the next call, with no restart and no new session. Same mechanism the share host gate already relies on (`src/twicc/share/asgi_filter.py:45-46`).

The two settings are independent. Nothing outside this design's gate (§7.1) and read redaction (§7.3) reads them.

**Plumbing, required by existing invariants** — a literal two-key patch to the defaults dict fails them:

- **CLI:** one entry per key in `GENERIC_KEY_DESCRIPTIONS` (`src/twicc/cli/settings/_keys.py:22-37`). A new generic synced key without a description fails the exact-equality guard `tests/test_settings_cli.py` `test_generic_key_descriptions_match_generic_keys`. This is also where the A17 self-flip surface gets its `twicc settings set --help` line.
- **Frontend:** add both keys to `SYNCED_SETTINGS_KEYS` (`frontend/src/constants.js:204`), to `SETTINGS_SCHEMA` and as boolean entries in `SETTINGS_VALIDATORS` (`frontend/src/stores/settings.js:24`, `:107`), to the store's getters/setters (or equivalent bindings for the two switches), and to `collectAllSyncedSettings()` (`:1120-1178`). A key missing from any of these is ignored on receive, not persisted, or dropped from the sync payload.

## 5. Caller identity

### 5.1 Mechanism

`resolve_current_session()` (`src/twicc/cli/_drop_request/whoami.py:56`) returns the calling session, or `None`:

- **MCP call** — the dispatcher sets the `forced_session_id` ContextVar from the signed Bearer token (`src/twicc/mcp/server.py:69`, token minted at `src/twicc/mcp/identity.py:46`). Identity is cryptographically established.
- **CLI subprocess** — resolved by walking PID ancestry against the live agent processes.
- **Human terminal, `/rpc/` with a full-scope token, REST UI** — `None`.

Each mutating CLI entry point adds the resolved id to the drop-request payload as `caller_session_id`, exactly as `peer-send` adds `origin_session_id` (`src/twicc/cli/peer_send.py:136-149`). The payload is passed whole to the handler; no key allowlist stands in the way (`src/twicc/drop_requests_watcher.py` dispatches on `kind` only).

The read path needs no payload: `list_main` / `show_main` run in the caller's own process and call `resolve_current_session()` directly.

### 5.2 Honest threat model

**This is a guardrail, not a security boundary.** Three known limits, all accepted:

1. On the CLI path, `caller_session_id` is **self-declared**. An agent that omits it, or that detaches its process from the session's PID tree, presents itself as a human. The MCP path does not have this weakness (signed token), but nothing forces an agent to use MCP.
2. The read redaction (§7.3) lives in the CLI process. An agent with a Bash tool can read the SQLite file directly.
3. **Setting self-flip (A17).** The two settings are themselves agent-reachable. `"settings"` sits in `MCP_EXCLUDED_ROOTS` (`src/twicc/mcp/tools.py:27-29`), which stops the MCP tool but not the CLI: `settings` is absent from `LOCAL_ONLY_COMMANDS` (`src/twicc/cli/_local_only.py:18`), the group is registered on the app (`src/twicc/cli/__init__.py:1456-1457`), the `settings:update` drop kind exists (`src/twicc/drop_requests_watcher.py:165-169`), and `settings.json` sits on disk. An agent can flip the gate's own switch from Bash. Accepted by explicit user decision: TwiCC's agent surface is designed for parity with the human surface, so an agent always has a way around a limitation of this kind. Same trust model as the other two limits — an obedient agent does not self-enable a switch the user left off — and the skill says so explicitly. Governing settings writes is a separate future decision (§13), deliberately not taken here.

The gate defends against an **obedient** agent doing something the user did not enable — the same trust model as every other agent-facing surface in TwiCC. It does not defend against a hostile agent, and this design does not pretend to. State it in the spec, in the skill, and in `SKILLS-AND-CLI.md`; do not add machinery that would suggest otherwise.

## 6. Scope rule

**One rule, applied twice: an agent acts on itself and on its spawn-tree descendants.**

| Question | Rule |
|---|---|
| Which session may I share? | The caller, or a proper descendant of the caller in the spawn tree |
| Which artifact may I share? | One whose bookmark's `session` satisfies the same test |
| Which existing share may I update / unrevoke / delete / propagate? | One whose `created_by_session` satisfies the same test |
| Which existing share may I revoke? | **Any** provenance or spawn tree (A7 — the kind setting still applies, §7.1) |

Rationale — the codebase has already answered this question for session mutations: `update-sessions` deliberately accepts `--spawned-by` / `--descendants` but refuses `--siblings` and `--spawn-tree`, *"you don't batch-mutate your peers"* (`SKILLS-AND-CLI.md:295`). Creating a share is a publication **and** a mutation; it falls on that side. A wider spawn-tree rule would let a worker publish its parent's transcript — content the worker was never given.

**Implementation.** Extract the BFS core of `resolve_descendants_filter` (`src/twicc/cli/_drop_request/whoami.py:201-296`) into a new `src/twicc/core/services/spawn_scope.py`, exposing `descendant_ids(session_id: str) -> set[str]`, and have both callers use it. Direction of dependency: `cli` → `core`, which is the existing convention. The gate's allowed set is `{caller_id} | descendant_ids(caller_id)`. Sync/async boundary: `descendant_ids` stays sync ORM (the CLI calls it directly); the async gate calls it — and the caller-session load — via `sync_to_async`, like every other ORM access in `share_mutation.py` (e.g. `:248`, `:385`). The bare `read_synced_settings()` call is fine in async context — direct precedent in an async view at `src/twicc/share/owner_views.py:47-48`.

**Subagents are out.** A Claude subagent session carries `parent_session` (`src/twicc/core/models.py:413`), not `spawned_by` — `spawned_by` is written only by `twicc create-session` (`src/twicc/core/services/session_creation.py:333,350`). A subagent session is therefore not a spawn-tree descendant and cannot be shared as a target. This is deliberate.

**Not a content boundary, and deliberately so.** A session share with `include_subagents` on still serves that session's subagent content — the subagents belong to the shared session, they are not separate targets. The rule governs *what an agent may point a link at*, not what a legitimately shared session contains.

**A lone session** (no spawn tree) resolves to `{self}` with no special case. The human always retains the full surface from the UI and from a terminal, so no state is ever unreachable.

## 7. Enforcement

### 7.1 Mutations

The gate lives in the six `*_from_payload` wrappers of `src/twicc/core/services/share_mutation.py` (`:408`, `:434`, `:441`, `:448`, `:455`, `:462`). This placement is exact, not incidental:

- The drop-request path (CLI, MCP, `/rpc/`) enters **only** through those wrappers (`src/twicc/drop_requests_watcher.py:135-163`).
- The owner REST UI calls `patch_share` / `revoke_share` / `delete_share` / `propagate_share` **directly** (`src/twicc/share/owner_views.py:89,94,102,109,116`), so it never meets the gate.
- The one overlap is creation: `share/owner_views.py:64` also calls `create_share_from_payload`. It is safe by construction — that view builds its payload key by key from the request body (`:54-62`, an explicit eight-key dict), so a crafted browser body cannot inject `caller_session_id` and the gate reads "human".

Gate algorithm, per call:

1. `caller_session_id` absent → human → no check, current behaviour. When **present**, it must be a JSON **string** — validated before any ORM access, since resolving it is itself an ORM lookup (the gate's caller-session load, §6) and an unvalidated list/object/boolean would raise there and surface as transport `failed` (`drop_requests_watcher.py:237-255`); any other type → `field_forbidden`, field `caller_session_id`. A well-typed but **unknown** id resolves to no session → human, current behaviour.
2. **Shape first (§7.2 Layer 1):** validate the received envelope — top-level keys and JSON types, then `options`/`fields` keys and types — **before any `.strip()` or ORM use**. The resolvers assume strings (`.strip()` on `session_id` and `share_id`, `share_mutation.py:384`, `:425`; `bookmark_id` goes straight into an ORM filter, `:391`), so an unvalidated list or object would raise and surface as transport `failed` instead of the documented `rejected`/`field_forbidden` (`drop_requests_watcher.py:237-255`). (An agent payload carrying the legacy `share_kind` alias instead of `kind_target` dies here as an unknown key; the alias stays alive for the human path.)
3. Resolve the share kind: for `share:create`, from the kind returned by the target resolution — `_resolve_target_from_payload` reads `payload.get("kind_target") or payload.get("share_kind")` (`share_mutation.py:382`) and rejects an unresolvable kind (`kind` error `invalid`). The setting check runs on the kind it returned. For the other five operations, the kind comes from the loaded row.
4. Setting for that kind off → reject `agent_sharing_disabled` (§7.5).
5. Apply the §6 scope test → reject `out_of_scope` on failure. Skip for `share:revoke` (A7).
6. Apply the §7.2 value rules and the §7.4 `shareBaseUrl` requirement: `frozen` default, `debug` refusal, password and expiry rules.

### 7.2 Value rules on creation and update

Applied only when the caller is an agent, in two layers.

**Layer 1 — the shape contract: only what the CLI can produce, stated over the received envelope.** The legitimate producer of an agent payload is the `twicc share` CLI — directly, or through MCP, which renders its calls from the same Typer signature (`render_argv`, `src/twicc/rpc/generator.py:119`). The commands build the application fields (`src/twicc/cli/share_mutation.py:9-34`); **both real transports then add `"kind": kind`** before the wrapper receives the payload (file transport `src/twicc/cli/_drop_request/drop_file.py:38`, in-backend MCP/`/rpc/` transport `src/twicc/cli/_drop_request/transport.py:121`), and this design adds `caller_session_id` (§5.1). The contract is therefore over the envelope the wrapper actually receives — application fields **plus** those two fields — and rejects any other key, or a listed key with a wrong JSON type, with `field_forbidden`. Six envelopes:

- `share:create` (session) — `{kind: "share:create", caller_session_id: string, kind_target: "session", session_id: string, label: string, password: string|null, expires_at: string|null, options: object}`; `options` keys ⊆ `{mode, max_display_mode, include_subagents, show_title, display_title}`, where `mode`/`max_display_mode` are strings checked by the existing enum validation (`share_mutation.py:72-75` — already type-complete), `include_subagents`/`show_title` are **literal JSON booleans**, `display_title` is a string.
- `share:create` (artifact) — same, with `kind_target: "artifact"` and `bookmark_id: int` in place of `session_id`; `options` keys ⊆ `{show_title: boolean, display_title: string}`.
- `share:update` — `{kind: "share:update", caller_session_id: string, share_id: string, fields: object}`; `fields` keys ⊆ `{label: string, password: string, expires_at: string|null}`.
- `share:revoke` / `share:unrevoke` / `share:delete` / `share:propagate` — `{kind: "share:<op>", caller_session_id: string, share_id: string}`.

**Presence semantics — a CLI-compatible superset, not byte-exact presence.** Every listed key except `kind`, `caller_session_id`, the target id (`session_id`/`bookmark_id`/`share_id`) and create's `kind_target` is **optional**: an absent key takes the server default already in place (`payload.get(...)`); a present key must match its type. The CLI happens to always emit some keys (`label`, `password`, `expires_at`, the four fixed `options`) — requiring their presence would buy nothing. Two presence rules are explicit: `expires_at: ""` on **create** is CLI-producible and means no expiry; on **update** the CLI normalises `--expires ""` to `null` (`src/twicc/cli/__init__.py:720-721`), so a raw `""` there is not CLI-producible → `field_forbidden` (`null` is the explicit clear).

This closes the raw-payload class by construction instead of per-field rules. `frozen_at_line` is **server-owned**: the validator accepts it and `create_share` freezes at the current `last_line` only when it is absent (`share_mutation.py:76-85`, `:234-235`), so a raw value would pre- or post-date the snapshot boundary — rejected as an unknown key. `snapshot_at` is snapshot-step-owned, `show_timestamps` has no CLI flag, `notify_on_view` is human-UI-only — **any** presence of these keys in an agent payload is rejected, whatever the value. A boolean-looking string (`"false"`) is a wrong type, rejected instead of coercing to `True` (`bool(...)` coercions at `:68-70`, `:100`). A field the CLI cannot emit is refused by default: no table to keep complete, and no new rule when the next field is examined.

**Layer 2 — the value rules on top of the shape**, unchanged from the decisions:

- `options.mode` absent → `snapshot`, frozen at the session's current `last_line` (the same code path `create_share` already uses for an explicit `--frozen`). An explicit `mode: "live"` is honoured (A9).
- `options.max_display_mode == "debug"` → reject `display_mode_forbidden` (A10). The allowed values remain `conversation`, `simplified`, `normal` (`src/twicc/core/services/share_mutation.py:39`).
- `password`: on create, `null` or `""` → no initial password (nothing is cleared — there is nothing yet); a non-empty string → set. On update, `password` must be **non-empty** — the CLI *can* emit `--password ""`, but a clear widens exposure and stays human-only (A12): an empty string → `field_forbidden`, field `password`.
- `expires_at`: on create, `null` or `""` → no expiry; on update, absent → unchanged, `null` → explicit clear. A non-empty string must parse under the existing `datetime.fromisoformat` (`_parse_expires`, `share_mutation.py:398-405`); an unparseable non-empty value → `ShareError("expires_at", "invalid", …)` — see the expiry defect note below.

**Pre-existing defect — silent expiry degradation, fixed on the paths where it exists.** `_parse_expires` returns `None` for any unparseable value (`share_mutation.py:398-405`; used by drop-request update at `:292-299`), so an invalid non-empty expiry today silently creates a never-expiring link (drop-request and REST create both funnel through it, `owner_views.py:64`) and silently **clears** an existing expiry (drop-request update) — for humans too on those paths. The owner REST PATCH is a **different boundary**: it parses in the view, `datetime.fromisoformat(raw)` (`src/twicc/share/owner_views.py:86-88`), so an invalid non-empty value raises before the service — an unstructured error, but never a silent widening. In this lot the choke point rejects instead of degrading: invalid non-empty `expires_at` → `expires_at`/`invalid` on the `_parse_expires` paths (drop-request create/update, REST create). REST PATCH keeps its pre-existing raise — accepted: the owner UI's date picker does not produce invalid strings, and the defect being fixed (silent widening) does not exist there. Valid input behaves as before everywhere.

**Shared repairs to existing human share operations.** Expiry validation above is one of several shared fixes that also change human surfaces — each a repair of a §2-documented pre-existing defect, never a behaviour redesign: the artifact title-option preservation on create/propagate (§8), the cross-kind list filters (§8), the `share_id` result shape (§8), the URL builder default for bare hosts (§7.4), and the `share create` bare-group correction (§12, silent no-op → missing-command usage/error + exit 2). Beyond these repairs, the lot's **new** human-visible surfaces are: the two Settings switches and their consent copy (§4), the `created_by` serializer field and the creator badge in the owner UI (§9), the `self`/`parent` keyword resolution and its remote preflight (§11), and the documentation surfaces (§12).

**The CLI must be able to produce an absent `mode`, and today it cannot.** `_share_create_session` declares `live: bool = typer.Option(True, "--live/--frozen")` (`src/twicc/cli/__init__.py:647`) and `run_create_session` materialises `"mode": mode` into the payload unconditionally (`src/twicc/cli/share_mutation.py:14`); the MCP tool renders from the same Typer tree, and `render_argv` omits an absent option (`src/twicc/rpc/generator.py:106-108`), so Typer's default `True` still applies there. Without a CLI change, every agent-reachable payload carries an explicit `mode` and the default rule above never fires. The lot therefore makes the flag **tri-state**:

- `live: bool | None = typer.Option(None, "--live/--frozen")` — no flag means `None`.
- `run_create_session` puts `"mode"` into `options` only when the flag was given (`"live"` / `"snapshot"`).
- Server side: `mode` absent + agent caller → `"snapshot"`; `mode` absent + human caller → current behaviour, since `_validate_session_options` already defaults to `"live"` (`src/twicc/core/services/share_mutation.py:66`). The human CLI's **observable share behaviour** is unchanged — no flag still creates a `live` share — though the intermediate payload now omits `options.mode` (visible to anything inspecting payloads, including the §14 end-to-end test).
- The MCP path inherits the fix for free: an absent `live` argument in the tool call renders no flag, so the payload carries no `mode`.

**Why the boundary is server-side.** The CLI `update` exposes only `--label/--password/--expires` (`src/twicc/cli/__init__.py:706-722`), but `update_share_from_payload` forwards `payload["fields"]` whole to `patch_share` (`src/twicc/core/services/share_mutation.py:434-438`), which also accepts `options` (including `mode`) and `notify_on_view` (`:289-291`, `:304-325`) — and `patch_share` treats every falsy `password` as a clear (`hash_password(pw) if pw else ""`, `:300-303`). A hand-written **local drop payload** carrying an honestly resolved `caller_session_id` could therefore flip a frozen share to live, or clear its password, while passing every scope and setting check. (`/rpc/` is *not* such a route: it deliberately binds no session identity, and its schema-validated body rejects unknown fields — `src/twicc/rpc/views.py:115-125`, `:36-47`.) The Layer-1 shape contract on `fields` plus the Layer-2 password and expiry rules close all of it. The human REST PATCH path is untouched — it never meets the gate (§7.1).

This shape contract is what makes the frozen default meaningful rather than cosmetic: a frozen link cannot become live except by the human's hand. Advancing a frozen share is still fully delegable, through `propagate`.

### 7.3 Reads

`list_main` and `show_main` (`src/twicc/cli/share.py:14,42`) resolve the caller. For an agent caller, every row whose kind has its setting **off** is returned with `token`, `url` and `url_path` set to `null` and an added `"redacted": true`. The row itself is **never** dropped.

Dropping rows was considered and rejected: silence is misleading. A redacted row lets the agent say "a share exists here, I cannot read its URL, enable the setting" — which is precisely the failure mode the whole error-at-call-time design (A4) exists to produce.

The scope rule (§6) does **not** apply to reads: with a kind's setting on, an agent sees every share of that kind in the DB — human-created, other spawn trees, other projects — token included (`list_main` has no caller filter, `src/twicc/cli/share.py:14-39`). Deliberate, twice over: revoke-anything (A7) needs the full inventory, and the switch is precisely the user consenting to that visibility. Do not "helpfully" scope-filter the list.

Only the CLI read path applies redaction. `serialize_share` is shared with the WS broadcast and the owner REST API and must keep returning the real token there (its only change in this lot is the §9 `created_by` field).

### 7.4 Missing share host

`shareBaseUrl` empty means sharing is disabled: `/share/…` 404s everywhere (`src/twicc/share/asgi_filter.py`). The REST path already refuses creation in that state (`src/twicc/share/owner_views.py:44-48`); the drop-request path does not.

For an **agent** caller, creation is rejected with `share_host_unset` — the same code the REST backstop already returns (`src/twicc/share/owner_views.py:50`), so the two surfaces stay aligned. An agent about to paste the URL into a peer message must not receive a link that resolves nowhere. For a **human** caller, behaviour is unchanged and differs by surface: REST creation already rejects an unset host (`src/twicc/share/owner_views.py:44-48`); the CLI and full-token `/rpc/` stay permissive — `twicc share` prints the relative `/share/<token>/` path as the `url` value (`src/twicc/cli/share.py:31-39`). The "note" in that module's docstring (`:1-4`) is documentation only — no output field carries it, and this lot adds none.

**URL output contract: parity with the owner UI (fixed in this lot).** `shareBaseUrl` is under-constrained at write time: for a **non-empty** input, the settings UI requires a value `new URL()` can parse a hostname from and rejects the current app hostname (`frontend/src/components/app/SettingsPopover.vue:838-854`) — empty input is valid and disables sharing; its setter then stores the value trimmed of whitespace and trailing slashes (`frontend/src/stores/settings.js:916-919`); a value written through the CLI / `settings:update` path, however, reaches the frontend **raw** — the generic CLI parse returns strings unchanged (`src/twicc/cli/settings/_keys.py:82`) and `applySyncedSettings` assigns it directly, bypassing the setter (`frontend/src/stores/settings.js:969-975`). Chasing that input space with a read-time normalisation grammar is a losing construction. The contract is therefore **parity, not normalisation**: *the `url` an agent receives is byte-identical to the URL the owner UI shows for the same share.*

**Scope of the parity promise:** a **non-empty** `shareBaseUrl` and an **unredacted** read. A redacted row (§7.3) has `url: null` by definition. With the host unset, the surfaces keep their current deliberate split: the CLI prints the relative `/share/<token>/` path, the frontend builder returns `null` and the Share UI is disabled (`frontend/src/utils/shareUrl.js:10`).

One algorithm, implemented as a mirrored Python/JS pair, replacing today's two near-identical builders (`_base_url()`, `src/twicc/cli/share.py:9-11`, uses native `str.strip()`; `shareAbsoluteUrl`, `frontend/src/utils/shareUrl.js:7-12`, does not trim):

1. strip leading and trailing characters of the **normative set** {U+0009 TAB, U+000A LF, U+000B VT, U+000C FF, U+000D CR, U+0020 SPACE}, then strip trailing `/`. This set is part of the contract: neither side may use the language-native `str.strip()` / `String.prototype.trim()`, whose Unicode sets differ (JS trims U+FEFF, Python does not; Python trims U+001C, JS does not);
2. if the value contains no `://`, prefix `https://` — a bare `host` or `host:port` becomes an absolute HTTPS URL, port preserved; plain HTTP requires an explicit `http://`;
3. append `url_path`.

**Enforced, not promised:** a shared JSON fixture of (stored value → expected `url`) cases, consumed by both the pytest suite and the frontend `node:test` suite, covering the normal forms, the normative-trim edge cases (ASCII whitespace, U+FEFF, U+001C), a raw CLI-written value, trailing slashes, and pathological values (path, query, credentials, mixed-case and exotic schemes, `://x`). The `project_agent_defaults.py` ↔ `projectAgentDefaults.js` pair is the structural precedent for a mirrored implementation — but its comment-only mirror obligation is *not* the enforcement mechanism here; the fixture is.

A bare `host` or `host:port` comes out as an absolute HTTPS URL. Any other non-empty value follows the algorithm with **no absoluteness or validity guarantee** — a stored `://x` passes through as `://x/share/<token>/`, identically on both surfaces. An unusable link from such a value is a pre-existing configuration defect, visible in the UI, not a defect of this lot. The honest fix is validation on the settings **write** path, deferred (§13).

### 7.5 Error contract

Every refusal is a `ShareError(field, code, message)` (`src/twicc/core/services/share_mutation.py:42`) travelling an existing path — no new transport, no new status. On the drop-request paths it maps to status `rejected`, CLI exit `3`, and the existing MCP envelope (`{"exit_code": 3, ...}`). REST create renders the same error list as HTTP 400 through `_err_response` (`src/twicc/share/owner_views.py:15-16`) — the surface where §7.2's expiry `invalid` reaches a human. Owner REST PATCH keeps the §7.2 exception (in-view raise).

| Code | Field | Raised when | Message must say |
|---|---|---|---|
| `agent_sharing_disabled` | `settings` | The setting for the share's kind is off | Which kind is disabled, and that the user must enable it in **Settings → Sharing** before retrying |
| `out_of_scope` | `session_id` / `bookmark_id` / `share_id` | The target or the share is outside the caller's scope (§6) | **Split by operation.** Creation: that the *target* belongs to another session, outside the caller's own spawn subtree. `update`/`unrevoke`/`delete`/`propagate`: that the *share was created* outside the caller's spawn subtree (provenance, field `share_id`) — never phrase it as a target failure, which can be false: a descendant touching a parent-created share **of itself** fails on provenance while the target is its own session |
| `display_mode_forbidden` | `max_display_mode` | An agent asked for `debug` | That `debug` is not available to agents, and which three modes are |
| `field_forbidden` | the offending key | An agent payload violates the §7.2 shape contract — an unknown or server-owned key (`frozen_at_line`, `snapshot_at`, `show_timestamps`, `notify_on_view`, anything else the CLI cannot emit), a wrong JSON type (e.g. a boolean-looking string), a `fields` key outside the update set, or an empty `password` on update (Layer 2) | Always the offending key and the accepted keys/types. For a valid operation reserved to human surfaces, name that surface (password clearing: the human CLI or the owner UI). For unknown, server-owned or wrongly typed input, say the input is unsupported or invalid — never claim a human surface accepts it |
| `invalid` | `expires_at` | Any caller **on the `_parse_expires` paths** — drop-request create/update and REST create — supplies a non-empty `expires_at` that `datetime.fromisoformat` rejects (§7.2 expiry defect fix; the owner REST PATCH parses in-view and keeps its pre-existing raise) | The rejected value and the accepted ISO 8601 format |
| `share_host_unset` | `share_base_url` | An agent creates while `shareBaseUrl` is empty | That the user must configure a share host in **Settings → Sharing** first |

`agent_sharing_disabled` and `share_host_unset` are the two codes an agent must **relay to the user rather than retry**. The skill states this explicitly, in the same spirit as the peer skills' *"only the user can fix it in Settings › Peers"*.

## 8. Agent surface, command by command

Allowed = allowed when the setting for the relevant kind is on.

### `twicc share` (list) — `src/twicc/cli/__init__.py:611`

`--kind`, `--session`, `--project`, `--include-revoked`, `--limit`, `--offset` — all allowed. Redaction per §7.3. The `--session` filter resolves the `self`/`parent` keywords (§11).

**Cross-kind filter semantics, fixed in this lot.** Today the filters match only `Share.session` (`src/twicc/cli/share.py:23-30`); an artifact share has `session` NULL (the CheckConstraint forces exactly one target), so `--session` and `--project` silently exclude every artifact share. In this lot: `--session X` matches `Q(session_id=X) | Q(artifact_bookmark__session_id=X)`; `--project` applies the same worktree-aware id set to both sides — `session__project_id__in` and the bookmark's denormalised raw `project` FK (`artifact_bookmark__project_id__in`, equal by construction to the bookmark session's project). Without this, `--session self` cannot find the artifact share the agent just created (§10), and a project inventory is incomplete for humans too.

### `twicc share show <SHARE_ID>` — `:629`

Allowed. Same redaction.

### `twicc share create session <SESSION_ID>` — `:641`

| Option | Agent | Note |
|---|---|---|
| `SESSION_ID` | scoped | §6; resolves `self`/`parent` (§11 — `parent` then fails the scope test, `out_of_scope`) |
| `--label` | yes | owner-side only, never shown to viewers; peer convention below |
| `--password` | yes | A12 |
| `--expires` | yes | no forced value (§13) |
| `--live` / `--frozen` | yes | tri-state (§7.2); no flag → `frozen` for an agent |
| `--max-display` | partial | `debug` refused (§7.2) |
| `--include-subagents` / `--no-subagents` | yes | |
| `--title`, `--show-title` / `--no-title` | yes | |
| `--timeout` | yes | transport |

### `twicc share create artifact <BOOKMARK_ID>` — `:664`

`BOOKMARK_ID` (scoped through the bookmark's session), `--label`, `--password`, `--expires`, `--title`, `--show-title`/`--no-title`, `--timeout` — all allowed, no value rules. An artifact share snapshots the bookmarked file under an in-scope session's artifact directory; TwiCC does not track who wrote that file (`ArtifactBookmark` records session/project/path/name/scope, no creator provenance — `src/twicc/core/models.py:692-724`).

**Pre-existing defect, fixed in this lot:** creation validates the artifact title options then discards them — `create_share` replaces the stored options with `{"snapshot_at": …}` (`src/twicc/core/services/share_mutation.py:266-271`), and `propagate_share` does the same (`:347-350`) — while the public serializer reads `show_title`/`display_title` from that very dict (`src/twicc/core/serializers.py:424-435`). So `--no-title` and `--title` are silently ignored on create, and a propagate resets even UI-set titles. Fix at the cause: creation stores `{**opts, "snapshot_at": …}`; propagation stores `{**share.options, "snapshot_at": …}`, preserving the title options as `patch_share` already does (`:319-321`). This repairs the human REST path too — without it, the §8 promise above is false.

**Peer-share label convention (both kinds).** When the agent creates a share in order to send it through the peer system, it sets `--label` to `peer <PEER_NAME>` — the peer's local `name` as listed by `twicc peers` (`src/twicc/cli/peers.py:29`), or the peer id (`peer_…`, `src/twicc/core/models.py:1709-1712`) when the peer has no local name. `label` is owner-side only, never shown to viewers (`serialize_share_public_meta`, `src/twicc/core/serializers.py:388` — "Never label"), so the convention lets the user see at a glance, from their own share list, which link went to which peer. This is a convention the `twicc-share` skill states and the agent follows (§12) — **not** a server-side validation; no code enforces the format.

### `twicc share update <SHARE_ID>` — `:706`

`--label`, `--password`, `--expires`, `--timeout`. Allowed on agent-created shares in scope. `--password ""` — a clear — is refused for agents, as is any non-string raw-payload `password` value (§7.2 type rule, A12).

**Password-change semantics, exactly as the code behaves.** Setting or replacing a password invalidates every existing grant — grants are fingerprint-bound to the hash (`src/twicc/core/services/share_mutation.py:280-282`) — so every new HTTP request and every new WebSocket connect requires the new password (`src/twicc/share/resolver.py:53-56`, `src/twicc/share/consumer.py:33-38`). An **already-open live WebSocket is not closed** by a password change: on `share_updated` the consumer closes only for revoked/expired/snapshot and re-resolves display options, never the grant (`src/twicc/share/consumer.py:190-214`). Accepted limitation, deliberately without new consumer machinery: the immediate-cutoff tool is `revoke`, which does close open sockets (`share_closed`, same handler). The skill states both halves — new viewers need the new password; a viewer already streaming is cut off by `revoke`, not by a password change.

### `revoke` / `unrevoke` / `delete` / `propagate` — `:682`, `:688`, `:694`, `:700`

| Command | Agent |
|---|---|
| `revoke` | any provenance or spawn tree — the kind setting still applies (A7) |
| `unrevoke` | agent-created, in scope |
| `delete` | agent-created, in scope — it also removes the snapshot directory (`share_mutation.py:363-374`) |
| `propagate` | agent-created, in scope — republishes newer content |

### Success output and URL retrieval

`ShareMutationResult` carries `share_id` (`src/twicc/core/services/share_mutation.py:48-51`) and the watcher copies it onto the status payload (`_RESULT_ID_FIELDS`, `src/twicc/drop_requests_watcher.py:188-199`) — but the CLI/MCP result formatter loses it: `build_final` (`src/twicc/cli/_drop_request/output.py:45-66`) has no share dispatch branch, falls through to `_PROJECT_ID_FIELDS`, and emits `{"status": "created", "project_id": null}`. Without a fix, an agent that just created a share cannot deterministically find it again (list-and-guess breaks under concurrent or pre-existing shares) and both §1 flows fail end to end.

In this lot: add `_SHARE_ID_FIELDS = ("share_id",)` and a `"share_id" in d` dispatch branch to `build_final`, before the project fallback. Every share mutation then returns `{"status": <status>, "share_id": "shr_…", "request_uuid": "…"}`. Creation deliberately returns **no token and no URL**: the agent's flow is two calls — `share create …`, then `share show <share_id>`, and read its `url` — an absolute URL for the normal bare-host / origin forms of `shareBaseUrl` (§7.4 parity contract, no absoluteness guarantee for pathological stored values; §7.3 redaction applies). The skill documents this sequence (§12).

**Known gap, unchanged by this lot:** `notify_on_view` exists on the model and in the UI but no CLI flag exposes it, on `create` or `update`. Neither humans nor agents can set it from the CLI — and a raw agent payload cannot enable it either, on create or update: the key is outside the §7.2 shape contract on both operations. Out of scope here; noted for the deferred notification work (§13).

## 9. Data model

One column on `Share`:

```python
created_by_session = models.ForeignKey(
    Session, on_delete=models.SET_NULL, null=True, blank=True,
    related_name="created_shares",
)
```

`NULL` means **human-created or legacy/unattributed** — not provably human: §2 establishes that an agent with a Bash tool can already create shares today, ungated, and current writes record no provenance, so a pre-migration row's origin is unknowable. The conservative direction is preserved by the scope rule itself: a NULL `created_by_session` fails the §6 provenance test, so NULL rows are unavailable to agent management except `revoke` (A7). A separate `created_by` enum was considered and dropped: the only **targeted runtime** session deletion that can leave unrelated share rows alive is the Codex initial-sync purge of ignored internal Guardian rollouts (`_apply_delete_sessions_payload`, `src/twicc/providers/db_writer.py:2453-2497`, guarded by `_is_ignored_existing_session`, `src/twicc/providers/codex/initial_sync.py:140-151`), which can never be a share-creating agent session. The management `sync --reset` path also deletes sessions (`src/twicc/core/management/commands/sync.py:19-24`), but only by deleting every `Project` — the cascade takes all shares with them, session shares through `Project → Session → Share` and artifact shares through `Project → ArtifactBookmark → Share` (all `CASCADE`: `src/twicc/core/models.py:378-380`, `:698-706`, `:1616-1621`; an artifact share's `session` FK is NULL, so it needs the bookmark path), so no share survives to be laundered. `SET_NULL` therefore cannot in practice turn an agent-attributed share into an unattributed one.

- Set by `create_share` from the resolved caller; never set on the REST path.
- Migration: additive, nullable, **no backfill** — every pre-migration row stays NULL, i.e. unattributed.
- **Serializer contract.** `serialize_share` (`src/twicc/core/serializers.py:345`) gains one field, `created_by`: `{"kind": "human_or_legacy", "session": null}` for a NULL row; `{"kind": "agent", "session": {"id": …, "title": …, "project_id": …}}` for an attributed row. **Hidden creator:** the owner **frontend** surfaces hide such sessions (`src/twicc/core/models.py:448-459`; the frontend session APIs 404 them, `src/twicc/views.py:198-200`, `:991-994`), and `created_by` follows that frontend rule: for a hidden creator the serializer emits `{"kind": "agent", "session": null}` — the `created_by` channel never carries a hidden session's id or title. The explicit hidden-session CLI surfaces (`twicc sessions --include-hidden` / `--only-hidden`, the filiation filters that include hidden by design, `twicc session <id>` — `src/twicc/cli/sessions.py:63-77`, `src/twicc/cli/session.py:8-21`) are deliberate and unchanged by this rule. **Qualified for self-targets:** §6 allows sharing oneself, and a session share always serializes its target as `session_id`/`target_title` (`src/twicc/core/serializers.py:370-374`) — for a share whose target *is* the hidden creator, those target fields still identify it. Inherent and accepted: the share publishes the session's transcript, which reveals far more than an id; the non-disclosure rule governs the `created_by` channel, not the share's own target fields. The viewer-facing serializer (`serialize_share_public_meta`, `:387`) is **not** touched — it never exposes owner-side data.
- **Staleness accepted — the badge is a point-in-time serialization, no freshness mechanism.** Hiding a session broadcasts only `session_removed` + `project_updated` (dispatched at `src/twicc/core/services/session_visibility.py:55-57`; implementations `:171-179`, `:195-208`), and the frontend shares store changes only on share events (`frontend/src/composables/useWebSocket.js:1172-1182`) — so hiding or renaming the creator session after a share was serialized leaves the badge stale until the next `share_updated` for that share, a full `shares_updated` snapshot (reconnect), or a reload; any of those re-serializes and applies the hidden rule above. **Accepted, deliberately without a broadcast mechanism:** hiding a session removes it from lists and counters — it is not a secrecy property — so a stale title in the owner's own share list is cosmetic and not worth new machinery.
- Every queryset feeding `serialize_share` adds `"created_by_session"` to its `select_related` — `src/twicc/share/owner_views.py:19-24,34-38`, `src/twicc/asgi.py:572-580` (WS `shares_updated`), `src/twicc/cli/share.py:23,49`, `src/twicc/core/services/share_mutation.py:422-428`, `src/twicc/share/view_tracking.py:149-158`. This is correctness, not just N+1: the WS consumer and the async owner views call `serialize_share` in async context, where a lazy FK load raises `SynchronousOnlyOperation`.
- **Owner UI**, read-only: attributed + visible creator → badge with the session title (its id when the title is empty), linking to the session; attributed + hidden creator → a non-link badge "Agent-created (hidden session)"; NULL → no badge, and any wording says "human or legacy", never plain "human". The provenance filter stays out of scope (§13).

## 10. Artifact flow

An artifact share requires a bookmark (`Share.artifact_bookmark` FK). The agent's flow is therefore two steps:

```
artifacts bookmark <SESSION_ID> <PATH> --name "..."   →   share create artifact <BOOKMARK_ID>
```

Both steps are already available to agents except the second. The bookmark step is **not** gated by the new settings — it creates no public URL, and gating it now would break an existing shipped surface.

Path confinement makes the bookmark step incapable of widening the scope: the server resolves `PATH` under `get_session_artifacts_dir(session_id)` and rejects anything outside it (`src/twicc/core/services/artifact_bookmark_mutation.py:60-73`). An agent cannot bookmark session X's file under session Y's name, so the §6 test on `bookmark.session` is sound.

Consequence, accepted (A15): every agent-made artifact share leaves a visible bookmark in the Artifacts sidebar, `scope=project` by default. That visibility is a feature — an agent cannot publish an artifact without leaving an object in the user's UI. If the clutter becomes real, the remedy is a bookmark-level affordance, not a `share create artifact <SESSION_ID> <PATH>` shortcut that would duplicate the path resolution.

## 11. `self`/`parent` keyword resolution

The MCP server instructions promise that session-targeting arguments accept `self` and `parent` (`src/twicc/mcp/server.py:52-54`). That promise is **overbroad today**, independent of this lot: `update-session` resolves `self` only, `send-message` resolves `parent` only (the disjoint precedents below), and the `session <SESSION_ID>` command family resolves neither (`src/twicc/cli/session.py:8-20` queries the literal id). This lot does **not** harmonise the whole CLI (§13) — it makes the four artifact/share call sites it touches explicit and correct, and requalifies the instruction text so each parameter description is the authority (§12). The two surfaces this lot must fix:

- **`twicc artifacts bookmark` / `unbookmark`** pass `SESSION_ID` through raw (`src/twicc/cli/artifacts_mutation.py:60`, `:74`), and the server resolves the artifacts root from it. Under MCP it is the same: `forced_session_id` only makes `resolve_current_session()` work, it does not rewrite arguments (`src/twicc/mcp/server.py:69`).
- **The share surface this lot opens.** `share create session <SESSION_ID>` passes the id raw into the payload (`src/twicc/cli/__init__.py:643`, `src/twicc/cli/share_mutation.py:12`), and the list `--session` filter is a raw string too (`:615`). "Share **this** session" is the §1 motivating flow; without resolution, the primary use case forces a `whoami` round-trip or fails with `not_found` on the literal string `"self"` — the exact defect this section fixes on `artifacts bookmark`.

Fix: extract the inline `self` resolution block of `src/twicc/cli/update_session/command.py:59-78` into a shared helper and apply it to both artifacts commands, to `share create session`, and to the list `--session` filter. The two existing precedents are disjoint single-keyword resolvers — `update-session` resolves `self` only (`src/twicc/cli/update_session/command.py:59-78`), `send-message` resolves `parent` only (`src/twicc/cli/send_message/command.py:118`). **Decided, not an open point:** the helper takes an explicit allowed-keyword set, and each call site declares which keywords it accepts; `artifacts bookmark`/`unbookmark` and the share call sites accept both `{self, parent}` (`parent` is meaningful — bookmarking a parent's artifact).

Keyword resolution composes with §6, it does not bypass it: `self` always passes the scope test; a resolved `parent` on `share create session` always **fails** it and yields `out_of_scope`. That outcome is deliberate — an agent that tries to share its parent gets the scope error, not a `not_found` on the literal string `"parent"`.

**Local failure contract.** The two precedents fail differently — `update-session self` emits a plain error and exits 1 (`src/twicc/cli/update_session/command.py:70-76`), `send-message parent` emits a structured validation error (`src/twicc/cli/send_message/command.py:118-139`) — and a third state exists that neither §11 nor §14 previously named: `parent` from a **root** session (a current session resolves, but `spawned_by` is NULL). One contract for all four call sites: a keyword that cannot resolve fails **locally, before any request submission**, with the same structured `validation_error` shape and exit 1 — missing session context → code `session_context_not_found`; `parent` with no `spawned_by` → code `parent_not_found`; the error names the original parameter and the keyword.

**Remote preflight.** The remote CLI rejects host-bound keywords client-side by Click parameter name (`HOST_BOUND_PARAMS`, `src/twicc/cli/_remote.py:115-128`). The share list's option parameter is named `session` (`src/twicc/cli/__init__.py:615`) and is absent from that set, so `twicc --remote … share --session self` would forward to the remote server instead of producing the standard local exit-2 misuse error. Add `session` to `HOST_BOUND_PARAMS` (and its audit comment). The other new call sites — `share create session`, `artifacts bookmark`/`unbookmark` — use the already-listed `session_id` parameter name.

Not gated by the new settings: the artifacts part is a correctness fix to an already-open surface.

## 12. Agent-facing surface and docs

- **`mcp/tools.py`** — drop `"share"` from `MCP_EXCLUDED_ROOTS` (`:27-29`). `"settings"` stays excluded. Every `share` sub-command becomes an MCP tool automatically. One correction first: remove `invoke_without_command=True` from `share_create_app` (`src/twicc/cli/__init__.py:637`) — the group has **no callback**, and the registry registers every callable group (`src/twicc/rpc/generator.py:69-71`), which would otherwise expose a zero-argument `share_create` tool described "Create a share link." that returns silent success and creates nothing (`src/twicc/rpc/invoker.py:37-48`). Only `share/create/session` and `share/create/artifact` are create operations.
- **`MCP_READ_ONLY_PATHS`** (`src/twicc/mcp/tools.py:37-39`) — add `share` (the list) and `share/show`, so the two read tools advertise `readOnlyHint` honestly; the mutations stay unmarked. `COOKIE_READONLY_COMMANDS` stays **untouched**: the browser-cookie `/rpc/` surface does not need share reads (the owner UI has `/api/shares/`) and that list is deliberately fail-closed. `tests/test_mcp_tools.py:7-18` currently asserts the whole `share` root is absent — it flips to asserting presence and the correct hints (§14).
- **New skill `twicc-share`** — documents the full surface, the two settings, the `agent_sharing_disabled` **and** `share_host_unset` errors (both relay-to-user, never retry, §7.5), the instruction never to self-enable the two settings (§5.2 limit 3 / A17), the scope rule, the frozen default, the two-call URL sequence (`create` returns `share_id`, never the URL; `share show <share_id>` yields `url` — §8), the password semantics of §8 (set/replace only, never clear; new viewers need the new password; an already-streaming viewer is cut off by `revoke`, not by a password change), the §11 keyword syntax and local errors (`SESSION_ID|self|parent` on `create session`, `self|parent` on the list `--session`; `session_context_not_found` / `parent_not_found`), the bookmark prerequisite for artifacts, and the peer-share label convention of §8 (`--label "peer <PEER_NAME>"` — the peer's local name from `twicc peers`, or its `peer_…` id when unnamed — whenever the share is created to be sent through the peer system). It must **not** document `--max-display debug`. Calibrate against `src/twicc/agent/plugin/README.md` and a neighbouring skill.
- **Existing `twicc-artifacts` skill and its reference docs** — §11 changes a surface this skill already documents: today it presents `artifacts bookmark <SESSION_ID>` / `unbookmark <SESSION_ID>` with explicit ids only, lists `invalid_scope` as the only local error (`src/twicc/agent/plugin/twicc/skills/twicc-artifacts/SKILL.md:45-73`) and uses explicit UUIDs in every example (`:126-137`). Update the syntax, argument text and examples to `SESSION_ID|self|parent`, and document the §11 local errors (`session_context_not_found`, `parent_not_found`) in the Errors sections the plugin guide mandates for write commands (`src/twicc/agent/plugin/README.md:104-119`). Apply the same `SESSION_ID|self|parent` syntax to the Artifacts section of `SKILLS-AND-CLI.md` (`:300-306`).
- **`twicc-peer-send` skill** — one cross-reference, scoped to **creation**: when the requested peer message needs a *newly created* share, point to `twicc-share` and require the §8 `peer <PEER_NAME>` label on that new link. Forwarding an existing share URL requires no relabelling — `peer-send` carries only a peer, title, prompt, attachments and timeout (`src/twicc/cli/peer_send.py:8-48`), no share id and no label, and the agent may lack provenance scope over an existing link anyway (§6).
- **`SKILLS-AND-CLI.md`, beyond the two named sections** — the central `self`/`parent` keyword inventory (`SKILLS-AND-CLI.md:34-40`) lists accepting commands explicitly and must gain the artifacts and share entries; the Sharing section's command syntax (`--session ID`, `share create session <SESSION_ID>`, `:312-316`) adopts `SESSION_ID|self|parent` / `--session <ID|self|parent>` as part of its rewrite above.
- **Live Typer descriptions and metavars** — parameter help feeds **both** the CLI `--help` and the MCP input schemas (`src/twicc/rpc/schema.py:61-89`), so stale text would make the generated tools contradict the skills about accepted values. Update: the `artifacts bookmark`/`unbookmark` `session_id` help ("The session that owns the artifact.", `src/twicc/cli/__init__.py:537`), the share list `--session` help ("Filter by session id.", `:615`), and `share create session`'s bare `typer.Argument(...)` (`:643`) — each naming the exact accepted keywords.
- **Stale source comments and docstrings** — (1) the banner above the share mutations, *"Mutation commands (human-only: no skill, no MCP tool — O5)"* (`src/twicc/cli/__init__.py:636`), falsified by this lot; replace it. (The `MCP_EXCLUDED_ROOTS` comment "plus the settings group", `src/twicc/mcp/tools.py:26`, becomes exactly accurate once `share` leaves the set — no edit needed.) (2) The `cli/share.py:1-4` module docstring promises a "note" no output carries (§7.4) and describes the pre-parity URL behaviour; rewrite it to the §7.4 contract. (3) The `mcp/tools.py:1-7` module docstring says agents "must not mutate global settings" — contradicting A17/§5.2, which accept exactly that via the CLI; replace with the selection fact: `settings` has no skill and no MCP tool, while CLI reachability and the A17 acceptance stand as §5.2 states. (4) The `shareBaseUrl` comment "Required to create/serve shares" (`src/twicc/synced_settings.py:103-107`) overstates A13's surface split — only REST and agent creation require the host; human CLI and full-token `/rpc/` creation stay permissive; reword it to that split (serving links always requires it).
- **MCP server instructions** (`src/twicc/mcp/server.py:52-54`) — the universal sentence "Session-targeting arguments accept `self` … and `parent`" is overbroad (§11) and stays wrong for commands this lot does not touch. Requalify it: session-targeting parameters accept `self` and/or `parent` **where their parameter description says so**; the connection carries the identity needed to resolve them. This pairs with the Typer-description bullet above — the per-parameter text becomes the authority.
- **In-product help `frontend/public/help/sharing.md`** — the page the Sharing settings section and the share dialog open (`SettingsPopover.vue:1565-1570` `showHelp('sharing')`, `ShareDialog.vue:227`); today it covers only the host prerequisite and the human management controls (`sharing.md:22-47`). Add the two agent switches: off by default, and what enabling one grants — creation, own-subtree management, reading the URL of every existing link of the kind, revoking any link of the kind — consistent with the §4 consent copy. This is the one human-facing document a user reaches from the switches themselves.
- **`plugin.json`** — bump `version`, **minor** (new skill; the `twicc-artifacts` and `twicc-peer-send` edits ride the same bump).
- **Documentation sweep, recorded — search-based.** Scope: current behavioural documentation and policy-stating source text — skills, reference docs, in-product help, README, Typer help strings, module docstrings and comments; historical plans and frozen release entries are excluded by standing rule. Every behaviour this lot changes maps to a §12 entry: the share surface (new skill; `SKILLS-AND-CLI.md` Sharing section, central keyword inventory and syntax lines; `CLAUDE.md`/`AGENTS.md`; the Typer/MCP descriptions; the source banner and the two docstrings; the MCP server instructions), keywords on artifacts (`twicc-artifacts`, `SKILLS-AND-CLI.md`, the Typer help), keywords on share (`twicc-share` + syntax lines), the label convention (`twicc-share` + the creation-scoped `twicc-peer-send` cross-reference), the two settings (`GENERIC_KEY_DESCRIPTIONS` `--help` and the consent copy, §4; `frontend/public/help/sharing.md`; the `mcp/tools.py` docstring), MCP exposure (auto-generated from the Typer tree — which is exactly why the Typer descriptions matter), and the remote preflight (`SKILLS-AND-CLI.md:53-54`'s generic keyword-rejection wording stays accurate as written). Searched and **not** falsified: the other skills' occurrences of "share(d)" (ordinary verbs, not the feature), `frontend/public/help/what-are-artifacts.md` (no sharing content), the `MCP_EXCLUDED_ROOTS` comment (self-corrects), and `README.md:110`, `:192-196` (its Sharing entries describe the human feature generically and stay true with agents added behind the switches).
- **`SKILLS-AND-CLI.md`** — the Sharing section (`:310`) currently reads *"Human-only (O5): no skill and no MCP tool exist for `share`"*. Rewrite: the surface, the two settings, the scope rule, the redaction, and the honest threat model of §5.2.
- **`CLAUDE.md`** — the `Share` bullet in **Database Models** says *"Human-only (O5): no skill, no MCP tool (`share` in `MCP_EXCLUDED_ROOTS`)"*. Rewrite, and mirror into `AGENTS.md`.
- **`docs/plans/2026-07-05-sharing-design.md`** — untouched (historical record). This document is the pointer.

## 13. Out of scope

Deliberately excluded, each with its reason:

- **Notification on agent-created share.** Attractive, but sending a peer message does not notify either. Consistency first; revisit both together. Would also need the missing `notify_on_view` CLI flag if it reuses that plumbing.
- **Per-project override of the two settings.** A spawn tree can span projects, so "which project decides?" has no clean answer. Two global settings only.
- **Forced expiry on agent-created shares.** A skill exists to remove chores; an expiry that needs periodic re-delegation adds one. No forced value, and no default-expiry setting for now.
- **Per-option masking of the MCP schema.** `param_spec` already skips params marked `hidden` (`src/twicc/rpc/schema.py:37`), but `hidden=True` also hides the option from the human `--help`. A per-surface mask would be a new mechanism. Unnecessary: every restriction in this design is a shape or value rule (§7.2), enforced server-side.
- **Governing settings writes.** An agent can flip the two gate settings itself (A17, §5.2). Accepted by explicit user decision — agent/human surface parity is the design. Whether and how settings writes should be governed at all is a future decision; this lot must not gate `settings`, add machinery around it, or present the share gate as a security barrier.
- **Provenance filter in the share manager.** The owner UI ships the §9 badge only. An "agent-created" filter in `ShareManagerDialog.vue` (which today groups by target and has no filters, `frontend/src/components/share/ShareManagerDialog.vue:23-44`) is a separate user-visible feature — out of this lot, so two plans cannot ship different one-lot UI scopes.
- **Harmonising `self`/`parent` across every session-targeting command.** The MCP instructions' universal promise is overbroad today (§11): `update-session` resolves `self` only, `send-message` resolves `parent` only, the `session` family resolves neither. This lot adds both keywords to the four artifact/share call sites it touches and makes each parameter description authoritative (§12) — the promise becomes *less wrong*, not true. Extending both keywords to every remaining session-targeting command is a separate, whole-CLI change; this lot already carries the §2-documented pre-existing-defect repairs (the rows are the authority — no count to rot) and does not take it on.
- **`shareBaseUrl` write-path validation.** The UI accepts values that pass the §7.4 hostname check; the CLI settings path accepts **any** string (§7.4). Constraining it — an exact grammar, a scheme allowlist, IPv6 handling, a `share_host_invalid` creation error for legacy values — belongs to the settings **write** surfaces (UI, CLI, `settings:update`), not to this lot's read-time builders, which promise parity with the owner UI, never validity of the stored value. Deferred as one coherent future change.
- **Bookmark-clutter remedies** (§10).
- **Flipping `live` ⇄ `frozen` from the CLI.** Would weaken A9 and is not needed: `propagate` covers the real need.

## 14. Tests

Existing coverage to extend: `tests/test_share_mutation.py`, `tests/test_share_owner_api.py`, `tests/test_share_model.py`, `tests/test_artifact_bookmarks.py`.

| Area | Cases |
|---|---|
| Gate | Both settings off → each of the six mutations rejected with `agent_sharing_disabled`; kind independence (session on / artifact off and the reverse) |
| Human path | No `caller_session_id` bypasses the agent gate: with both settings off, a human payload still creates/updates/revokes, no frozen default, no display ceiling, no shape rules. The shared defect fixes still apply on their documented human surfaces — expiry validity (§7.2), artifact title options, cross-kind filters, `share_id` result shape (§8), URL builder (§7.4), bare `share create` group (§12) — each with its own row |
| Scope | Caller shares itself: allowed. Descendant: allowed. Parent: refused. Sibling: refused. Unrelated: refused. Subagent session (`parent_session` set, `spawned_by` NULL): refused |
| Provenance | `update`/`unrevoke`/`delete`/`propagate` on a NULL-provenance share (human or legacy): refused. On another branch's agent share: refused. `revoke` on a NULL-provenance share with the kind setting **on**: allowed; with it **off**: refused |
| Value rules | Agent create with no `mode` in the payload → `snapshot` + `frozen_at_line` set. Explicit `live` honoured. `debug` refused, other three accepted. **End-to-end:** CLI `share create session` with no `--live`/`--frozen` flag produces a payload without `options.mode` (the tri-state flag, §7.2); human CLI with no flag still yields `live` |
| Update shape | Agent `share:update` with `fields.options` or `fields.notify_on_view` → `field_forbidden`. Agent update `password` in {`""`, `null`, `false`, `0`, `[]`, `{}`} → `field_forbidden` (each type class tested); non-empty string replace → accepted, existing grants invalidated, new WS connect requires the new password, an already-open live WS keeps streaming (§8). Agent update with only allowed keys → accepted. Human REST PATCH with `options` or a password clear → accepted, unchanged |
| Shape contract | **End-to-end through both real transports** (file drop and in-backend): the genuine payload — `kind` included — passes; one extra key fails. Agent create with a non-object `options` → `field_forbidden`. Server-owned or unknown keys rejected **whatever the value**: `frozen_at_line` (incl. a future line number), `snapshot_at`, `show_timestamps`, and `notify_on_view` in {`true`, `false`, `"false"`, `1`, `null`, `""`, `0`, `[1]`, `{}`} — falsy cases included so a truthiness-based gate cannot pass. Wrong JSON types rejected **before resolution** with status `rejected`, never `failed`: `caller_session_id` as list/object/boolean (§7.1 step 1), `session_id`/`bookmark_id`/`share_id` as list/object/boolean, `include_subagents`/`show_title` as `"false"`, `display_title` non-string (§7.1 step 2). **Create:** `password` non-string (`123`, `[]`, `true`, `false`, `0`, `{}`) → `field_forbidden`; absent/`null`/`""` → no password; non-empty string → set — update password behaviour is the Update shape row's. Human REST create bypasses the agent shape contract; the shared expiry and artifact-option repairs still apply there (§7.2) |
| Expiry | Valid ISO string on create and update → accepted, stored. Update: absent → unchanged, `null` → clears, raw `""` from an agent → `field_forbidden` (§7.2 presence rule). Invalid non-empty → `invalid`, per surface: agent CLI/MCP create (**no row created**) and update (old expiry preserved); human CLI create/update; human REST POST. Owner REST PATCH: pre-existing in-view raise, unchanged (§7.2) |
| Share host & URL | Agent create with `shareBaseUrl` empty → `share_host_unset`; human CLI create → unchanged (relative `url`), human REST create → still rejected. **Parity (§7.4):** the shared JSON fixture runs on both suites (pytest + `node:test`) — normal forms (bare `host`, bare `host:port`, `https://origin`, `http://origin:port` → byte-identical, bare forms absolute with port preserved), normative-trim edges (ASCII set, U+FEFF, U+001C, raw CLI-written value, trailing slashes), pathological values (path, query, credentials, exotic scheme, `://x` → identical pass-through, no absoluteness asserted). Redacted read → `url` null |
| Reads | Through **both** `list` and `show` (separate implementations, `src/twicc/cli/share.py:14-39`, `:42-55`): agent with a kind off → row present, `token`/`url`/`url_path` null, `redacted` true; setting on → real token; human → never redacted |
| Result shape | `share create` (CLI **and** MCP) returns `share_id` on success; `share show <share_id>` then yields the `url` — the two-call sequence works end to end (§8) |
| Artifact options | Create with `show_title=False` and with a custom `display_title` → stored **and** served by the public serializer; `propagate` preserves them (§8) |
| List filters | `--session` and `--project` each return both kinds (a session share and an artifact share of the same session/project); `--session self` finds an artifact share the caller just created |
| Provenance serializer | `created_by` shapes: NULL row → `human_or_legacy`; attributed row → session `{id, title, project_id}`; hidden creator → `{"kind": "agent", "session": null}`, id/title absent from `created_by`; self-target by a hidden creator → `created_by.session` null **while** `session_id`/`target_title` remain (§9 qualification); untitled creator → UI shows the id. WS + async owner paths serialize without a lazy FK load (`select_related`) |
| Provenance staleness | A hide flip emits **no** share event (accepted staleness, §9); the badge refreshes only on a `share_updated` **for that share**, a full `shares_updated` snapshot, or a reload — a fresh serialization then returns `created_by.session` null for the now-hidden creator |
| Settings plumbing | `test_generic_key_descriptions_match_generic_keys` passes with the two new keys described; frontend settings tests cover schema, validators and sync round-trip for both keys. The switch copy carries both disclosures — revoke-anything and read-all — for both switches (§4) |
| MCP registry | `share` tools present in the registry; `share` (list) and `share/show` carry `readOnlyHint: true`, the mutations do not (`tests/test_mcp_tools.py` updated from its share-absent assertion). No `share_create` group tool exists; `share_create_session` and `share_create_artifact` do; bare `twicc share create` on the CLI → missing-command usage/error, exit 2 — not full help, and never silent success (§12) |
| Keywords | `artifacts bookmark self <path> --name test` and `artifacts bookmark parent <path> --name test` resolve; `artifacts unbookmark self <path>` and `artifacts unbookmark parent <path>` resolve. Failure contract (§11): unresolved `self`, unresolved `parent`, and root-session `parent` (no `spawned_by`) each fail **locally** with the structured `validation_error` (codes `session_context_not_found` / `parent_not_found`) and exit 1, across all four call sites. `share create session self` resolves and passes the scope test; `share create session parent` resolves and fails it with `out_of_scope` (§11); `share --session self` filters on the caller, `share --session parent` on the parent. Remote: `twicc --remote` with `self`/`parent` on any of these params (incl. the list's `session`) → local exit-2 misuse error, never forwarded (§11) |
| Model | Migration applies; `created_by` in `serialize_share`, absent from `serialize_share_public_meta` |

## 15. Open points for the plan

None. Every agent-facing contract and UI scope is decided in §4–§13. (Former candidates — the helper keyword contract, the `"redacted": true` field name, and the provenance-filter UI scope — are decided in §11, §7.3 and §9/§13 respectively, and tested as such in §14.)
