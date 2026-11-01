# CLI `--remote` forwarder — design

**Status:** draft for review
**Phase:** 2 of the RPC API work (phase 1 = the server-side `/rpc/` API, done)
**Related:** `2026-06-04-rpc-api-from-cli-design.md`, `RPC-API.md`

## Goal

Add a global `--remote <url>` flag so that any `twicc` CLI command executes
against a *remote* TwiCC's `/rpc/` HTTP API instead of locally. This lets one
TwiCC instance — or a human at a shell — drive another over the network; the
motivating case is several TwiCC instances on different ports / containers.

The server side already exists (phase 1). This phase is **only the client-side
forwarder**. No new server endpoint: the forwarder reuses the existing
`/rpc/<command>` routes and the `{"argv": [...]}` body form (already implemented
in `rpc/views.py`).

## Non-goals
- Named remote registry (URL aliases). `--remote` takes a raw URL.
- Client-side retries / backoff / streaming.
- Any server change.

## User-facing CLI

```
twicc --remote[=<url>] [--remote-token <token>] <command> [args...]
```

Remote mode is triggered by the **presence of `--remote`** (with or without an
inline URL). Absent → normal local execution, zero behavior change (env vars
alone never trigger remote mode).

- **URL** — `--remote=<url>`, or `--remote <url>` when the next token is a URL
  (contains `://`), sets it inline; a **bare `--remote`** takes it from
  `TWICC_REMOTE_URL`. Error if neither is provided.
- **Token** — `--remote-token <token>` (or `=` form) wins; otherwise
  `TWICC_REMOTE_TOKEN`; otherwise none (valid only if the remote is open).
- Everything after is the command to run **on the remote**, byte-for-byte the
  same syntax as a local invocation.

Precedence is the same for both URL and token: an explicitly passed value always
wins, the env var is the fallback. Because interception is manual (pre-Typer) we
disambiguate the space form `--remote <url>` by the `://` scheme — command names
never contain `://`, so there is no collision.

Examples:
```
twicc --remote http://box:3501 sessions --limit 5
twicc --remote http://box:3501 --remote-token twicc_pat_… create-session --provider claude_code "hello"
TWICC_REMOTE_TOKEN=twicc_pat_… twicc --remote http://box:3501 process wait <id> --timeout 30
```

## Architecture

### Pre-dispatch interception (entry point)

`--remote` cannot be a normal Typer option: when it is set we must **not** let
Typer parse and execute the command locally. So it is handled **before Typer**,
in the entry point (`cli.main()`):

1. Scan `sys.argv` for `--remote`/`--remote=` and `--remote-token`/
   `--remote-token=`. These are reserved names; no subcommand defines them, so
   extraction is unambiguous. Convention: they precede the command.
2. `--remote` absent → hand the argv to the normal Typer app unchanged (env vars
   alone never trigger remote mode).
3. `--remote` present → resolve the URL (inline value, else `TWICC_REMOTE_URL`,
   else error) and the token (inline value, else `TWICC_REMOTE_TOKEN`, else
   none), strip those tokens, and pass the **remaining argv** (the command) to
   the forwarder. Typer is never invoked.

### Forwarding — the reused mapping, both directions

The forwarder is the inverse of the server's generator, using the **same
`build_registry()`** (the canonical command↔URL mapping — "what maps
command→URL on the server maps argv→URL on the client"). Given the command
argv:

1. **Resolve the command-path**: greedily join the leading non-option tokens and
   take the longest prefix that is a registry key — e.g.
   `["process","wait","id","--timeout","5"]` → `process/wait`.
2. **Reject local-only / host-bound** inputs with a clear message, *before* any
   HTTP (see next section).
3. **Inline attachments**: rewrite each `--attach <local-path>` to a `data:` URI
   (see Attachments).
4. **POST** `<url>/rpc/<command-path>` with:
   - body `{"argv": [<remaining argv>]}` — the server already supports this form,
     so there is **no client-side argv→JSON rendering** and no need for the
     client to know each command's schema;
   - `Authorization: Bearer <token>` when a token is set;
   - `Content-Type: application/json`.
5. **Map the envelope** `{exit_code, result, error}` to local stdout / stderr /
   exit (see Exit codes & errors).

Why the `{"argv": [...]}` form rather than rendering a structured body: it is the
minimal reuse of the existing mapping, needs no schema knowledge on the client,
and the server still validates at execution (a bad option surfaces as the
command's own `exit_code`/`error` in the envelope). The server already
allowlist-checks `argv[0]` against exposed roots.

**Version-skew caveat:** the forwarder resolves the path against the *local*
registry; client and remote are assumed to expose the same command tree.
Documented limitation, not handled.

## Single source of truth for local-only commands  ← key decision

Today the API denylist lives as
`DENYLIST = {"password", "claude", "codex", "run", "token", "whoami"}` inside
`rpc/generator.py`. These are host-bound / interactive commands that must never
run over HTTP, and `--remote` must reject the **exact same set**.

**Decision:** extract it to one canonical constant — `LOCAL_ONLY_COMMANDS` in a
shared CLI module (proposed `src/twicc/cli/_local_only.py`) — consumed by both:

- `rpc/generator.py` → excludes them from the registry (current behavior: they
  404 over the API);
- the `--remote` forwarder → rejects them client-side with a clear message
  (`"<cmd> is a local-only command; not available over --remote"`).

This guarantees the API and `--remote` can never drift on what is forbidden.
(Because denylisted commands are absent from the registry, the forwarder's
path-resolution would already fail for them — the explicit check exists only to
produce a *helpful* error rather than a generic "unknown command".)

### Host-bound argument keywords: `self` / `parent`

Distinct from commands, the session-id keywords `self` and `parent` reference
the **caller's local** session identity and have no meaning on the remote.
Under `--remote` the forwarder rejects a command whose session-target argument
is `self` or `parent`.

Rejection is **spec-aware**: the forwarder rejects `self`/`parent` only where
they are actually accepted — the session-id argument of session-targeting
commands and the `--spawned-by` / `--spawn-tree` / `--descendants` filters — by
inspecting those specific parameters of the resolved command, **not** by a blind
argv scan. A free-text value that merely happens to be `self`/`parent` (e.g. a
message body) is therefore never affected. (Exact mechanism — Click
`make_context` parse vs a centralized set of host-bound param names — settled in
the plan.)

## Attachments

`--attach` is the one path argument that is **local to the client**. The
forwarder reads the local file, base64-encodes it, sniffs the real MIME (reusing
the existing `_sniff_mime` logic for consistency), and rewrites the argv value to
`data:<mime>;base64,<payload>`. The remote decodes it through the existing
`validate_and_encode` path — no server change.

Consequences:
- A **relative** `--attach` path works fine under `--remote` (read client-side;
  the server never sees a path). This is a feature.
- **No size handling** on our side: if the payload is too large, the remote /
  HTTP layer returns an error, surfaced as a remote error (below). We do not
  pre-check sizes.
- An `--attach` value that is already a `data:` URI is forwarded unchanged.

Contrast with other path args: `--project` / `--directory` are **remote-side**
paths — an absolute path on the remote (or, for `--project`, a project id),
exactly as the API requires.

## `wait` / long-poll

`process wait` and `processes wait` block server-side up to their `--timeout`.
The forwarder must set its HTTP **read timeout ≥ the command `--timeout`** (parse
`--timeout` from the argv, fall back to the command default, add a small margin).
The proxy idle-timeout caveat is already documented in `RPC-API.md`.

## Exit codes & error model

Two clearly separated cases:

1. **The command reached the remote and ran** (success *or* failure):
   - print `result` (JSON) to stdout,
   - if `error` is set, print it to stderr,
   - exit with the remote `exit_code`.

   A script then behaves identically whether run locally or via `--remote`.

2. **Transport / remote-layer failure** (cannot connect, DNS, HTTP timeout,
   401/404/5xx, malformed / non-JSON response):
   - print `twicc: remote error: <detail>` to stderr, where `<detail>` names the
     cause (connection refused / auth rejected (401) / unknown command (404) /
     HTTP `<status>` / timeout / bad response);
   - exit with a **dedicated reserved code**, distinct from the command
     vocabulary (`0/1/2/5/64`). Proposed: **`7`**.

   One dedicated code for v1 (rich detail on stderr); can be split into several
   later if useful.

## Affected modules (sketch — refined in the plan)

- entry point (`run.py` → `cli.main()`): pre-dispatch interception of
  `--remote` / `--remote-token`.
- `src/twicc/cli/_remote.py` (new): the forwarder — path resolution, attachment
  inlining, HTTP call, envelope→exit mapping, error model.
- `src/twicc/cli/_local_only.py` (new): `LOCAL_ONLY_COMMANDS` canonical constant.
- `src/twicc/rpc/generator.py`: import `LOCAL_ONLY_COMMANDS`, drop the in-file
  `DENYLIST`.
- HTTP client: **`httpx`** — the codebase's standard HTTP client (used by
  `pricing.py`, both providers' `usage.py`, the statuspage tasks, version check).
  The forwarder is sync, so it uses `httpx.Client`.

## Resolved
- **HTTP client:** `httpx` (the codebase standard), sync `httpx.Client`.
- **Both env vars in v1:** `TWICC_REMOTE_URL` and `TWICC_REMOTE_TOKEN`. Remote
  mode is still triggered only by the `--remote` flag; the env URL fills in a
  bare `--remote`. An explicit value always wins over the env var, for both URL
  and token.
- **`self` / `parent`:** rejected under `--remote`, spec-aware (only at the
  session-target parameters).
- **Transport-error exit code:** `7`.

## Open point (settled in the plan)
- `self` / `parent` rejection mechanism: Click `make_context` parse vs a
  centralized set of host-bound param names.
