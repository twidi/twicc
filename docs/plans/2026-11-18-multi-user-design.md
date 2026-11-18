# Multi-user accounts, Google sign-in and per-project access

Status: implemented (PR "feat: multi-user accounts with Google sign-in and per-project access").

## 1. Goal

TwiCC is protected by one instance password today: whoever knows it sees and
drives everything. Teams and agencies sharing one TwiCC host need to:

- give each person their own account (email + password, or Google sign-in);
- distinguish **admins** (everything, plus account management) from
  **members** (only what they were granted);
- grant members access **per project** and **per workspace** (e.g. client A's
  workspace vs client B's — whoever sees one never sees the other), at two
  levels: **view** (read) and **operate** (also drive agents and edit files);
- manage this interactively: an Admin panel (accounts + access matrix) and an
  *Access* section in the project and workspace dialogs.

## 2. Non-goals and honest limits

- **Not an OS sandbox.** Agents and terminals run as the TwiCC host account. A
  shell-capable agent started by a member can read whatever that account can.
  The feature therefore (a) keeps the interactive **terminal admin-only**,
  (b) forces every **member-driven session onto the untrusted-project security
  floor** (no `bypassPermissions` / `yolo`, repo-controlled settings ignored),
  and (c) documents the limit in the Admin panel. Real isolation would need one
  OS account or container per user — a separate, much larger project.
- No per-user synced settings yet: settings stay instance-wide and members can
  neither read the sensitive keys nor write any of them.
- Shares, peers, MCP management, usage quotas, native TTS and the artifact
  network proxy stay admin-only for now (each needs its own scoping design).

## 3. Backward compatibility

Nothing changes until the first account exists:

- No account, no password → unchanged (local-only open mode).
- No account, password → unchanged (single owner).
- The instance password keeps working as the built-in **owner** identity — an
  implicit admin with no DB row — so an admin can never lock the instance: the
  owner is the break-glass account. Demoting/disabling/deleting the last admin
  account is refused when no instance password exists.

## 4. Model (`core/models.py`, migration 0175)

| Model | Fields | Notes |
|---|---|---|
| `User` | email (unique, lowercased), display_name, role (`admin`/`member`), password_hash (PBKDF2, empty = Google-only), google_sub (unique, latched), avatar_url, is_active, auth_epoch, timestamps | `auth_epoch` is stamped in every login session; bumping it signs the account out everywhere |
| `ProjectGrant` | user, project, level (`view`/`operate`) | unique (user, project) |
| `WorkspaceGrant` | user, workspace_id, level | workspaces live in `workspaces.json`, so the link is the id string, resolved live |

`django.contrib.auth` is deliberately not used: TwiCC has no admin site, and the
hashers + session binding already exist (`twicc.auth.hashers`).

## 5. Identity: `Principal` (`twicc/accounts/principal.py`)

`resolve_request_principal` / `resolve_scope_principal` (`twicc/auth/access.py`)
return, in order: the account in the session (if active and epoch matches) →
the owner (session bound to the current password hash) → the owner via the
dev-worktree bypass → the owner in unprotected local mode → `None`.
`auth_required()` = instance password set **or** at least one active account.
Every gate (HTTP middleware, `/ws/`, terminal, native TTS, MCP management, RPC
cookie scope) now goes through these.

## 6. Access resolution (`twicc/accounts/project_access.py`)

`get_project_access(principal) → ProjectAccess(unrestricted, viewable, operable)`:
admins/owner are unrestricted; a member's set is the union of direct project
grants, the projects of granted workspaces, and the worktrees of all of those
(inheriting the level). The strongest level wins. Cached per account with
explicit invalidation (grant/workspace/project writes) and a 10 s TTL (the CLI
writes from another process).

## 7. Enforcement

**HTTP — deny by default** (`twicc/accounts/middleware.py`, `process_view`):
for a member, `/api/admin/*` is refused; a route with a `project_id`,
`session_id`/`parent_session_id` or `bookmark_id` kwarg requires the scoped
project to be viewable (404 otherwise — never confirm a foreign project exists)
and, for unsafe methods, operable; a few project routes are admin-only whatever
the grant (project metadata, trust decisions, directory recreation, worktree
adoption); any other route must be in the explicit `MEMBER_GLOBAL_ROUTES`
allowlist, whose views filter their output (`twicc/accounts/scoping.py`):
projects, sessions, home, daily activity, timeline, search, bookmarks,
uploads, bootstrap (workspaces stripped to visible projects; sensitive synced
settings removed). A route added later is therefore admin-only until someone
decides otherwise.

**WebSocket** (`twicc/accounts/ws_filter.py`): every frame of a member
connection — connect snapshot and `updates` broadcasts — passes through
`WSConsumer.send_json`, the single choke point, where `MemberFrameFilter` keeps
frames of viewable projects (resolving `session_id`-only frames through a
per-connection cache), rewrites list frames, and **drops unclassified types**.
Inbound frames are classified too (`_MEMBER_INBOUND_*` in `asgi.py`): agent
driving needs operate, read markers need view, instance configuration is
refused. `accounts_changed` (no payload) makes each connection re-resolve its
principal (closing it with `auth_failure` when the account was disabled or its
epoch bumped).

**Agents**: a member's `send_message` marks the session member-driven
(`twicc/accounts/agent_floor.py`); the three provider trust clamps treat it
exactly like an untrusted project.

**RPC / MCP / CLI**: `/rpc/` cookie scope is admin-only (the CLI reads are
instance-wide). `twicc user …` is local-only (never RPC/MCP): an agent can
never mint itself an admin.

## 8. Google sign-in (`twicc/accounts/google.py`)

OpenID Connect authorization-code flow with PKCE, `state` and `nonce` kept
single-use in the Django session. The ID token is received directly from
Google's token endpoint over TLS (OIDC Core §3.1.3.7), so no JWKS fetch; every
claim the decision relies on is still validated (`iss`, `aud`, `exp`, `nonce`,
`email_verified`). Matching: latched `sub` first, then verified email (latching
the `sub`); a different `sub` for an already-linked account is refused. Without
an existing account, sign-in is refused unless the address's domain is listed
in `TWICC_GOOGLE_ALLOWED_DOMAINS` **and** Google asserts it as the Workspace
hosted domain (`hd`), in which case a member with no access is created.

Configuration (data-dir `.env`): `TWICC_GOOGLE_CLIENT_ID`,
`TWICC_GOOGLE_CLIENT_SECRET`, optional `TWICC_GOOGLE_REDIRECT_URI` (set it when
behind a proxy; register `https://<host>/api/auth/google/callback/`), optional
`TWICC_GOOGLE_ALLOWED_DOMAINS`.

## 9. API

- `GET /api/auth/check/` — adds `accounts_enabled`, `google_enabled`,
  `owner_password`, `principal`, `google_error`.
- `POST /api/auth/login/` — `{email, password}` for accounts, `{password}` for
  the owner. Unknown emails burn a PBKDF2 round (no timing enumeration).
- `GET /api/auth/google/start/?next=…`, `GET /api/auth/google/callback/`.
- `GET /api/me/` — principal + visible project levels.
- `/api/admin/users/…`, `/api/admin/projects/<id>/access/…`,
  `/api/admin/workspaces/<id>/access/…` — admin-only CRUD (see
  `twicc/accounts/views.py`).

## 10. UI

Login (email/password, Google button, instance password), user menu, Admin
panel (accounts table, account dialog, access matrix grouped by workspace with
inherited levels shown), *Access* sections in the project and workspace
dialogs, and member mode (admin-only entry points hidden, terminal hidden,
composer disabled on view-only projects).

## 11. Follow-ups

Per-user synced settings and drafts; member-scoped shares; per-user audit trail
in the timeline ("done by"); member-scoped native TTS; OS-level isolation.
