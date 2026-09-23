# TwiCC RPC API

Every `twicc` CLI command is automatically exposed over HTTP under `/rpc/`, so
one TwiCC instance (or any HTTP client) can drive another over the network. The
API is a thin transport in front of the exact same command code — there is **no
hand-written endpoint layer** — so the CLI reference *is* the API reference:

- the command tree, its options and arguments — [`SKILLS-AND-CLI.md`](SKILLS-AND-CLI.md) or `twicc --help`;
- a machine-readable schema of every route — `GET /rpc/openapi.json` (OpenAPI 3.1).

This file only documents what differs from running the CLI locally.

> **Shared machinery with the MCP server.** TwiCC's built-in MCP server (`/mcp`, see [`SKILLS-AND-CLI.md`](SKILLS-AND-CLI.md)) is generated from the *same* registry, JSON schemas and response envelope as `/rpc/` — one command tree, three front doors. Both now execute in-process: a `/rpc/` mutation runs its request straight through the service handlers on the backend's event loop (no drop file, no polling), same as an MCP tool call.

## Endpoints

| Method & path | Purpose |
|---|---|
| `POST /rpc/<command>[/<subcommand>…]` | Run a command (e.g. `POST /rpc/sessions`, `POST /rpc/sessions/wait-reply`). |
| `GET /rpc/` | List the exposed routes with their one-line summary. |
| `GET /rpc/openapi.json` | OpenAPI 3.1 schema for every route. |

A command's options and arguments are passed as a single JSON object in the
request body, using the field names from the route's OpenAPI schema. An empty
body is valid for commands that take no input.

## Response

Every call returns a JSON envelope mirroring the CLI outcome:

```json
{ "exit_code": 0, "result": "<command output, or null>", "error": "<message, or null>" }
```

`exit_code` is the same code the CLI would have returned, `result` is the JSON
the command prints on success, and `error` carries the message on failure. The
HTTP status is `200` whenever the command ran (inspect `exit_code` for the
outcome); `4xx`/`405` are reserved for transport-level problems (unknown route,
malformed body, wrong method, failed auth).

## Authentication

`/rpc/` accepts two kinds of credential, independently of the web UI's password:

- **Neither a password nor any token configured** → `/rpc/` is open (local-only, protection opted out).
- **A password is set, or at least one token exists** → a credential is **mandatory**, in one of two forms:
  - a valid **Bearer token** → **full** access to every command;
  - failing that, a valid **session cookie** (the one the web UI sets at login) → **read-only commands only**. Same-origin pages — including rendered artifact pages — carry this cookie automatically, so they can call read commands without minting a token. Mutating commands (`create-session`, `send-message`, the `*/stop` controls, `update-*`, `create`/`delete-*`, `artifacts bookmark/unbookmark`) and the `{"argv": […]}` body form stay token-only.

A request with no valid credential gets `401`; a cookie-authenticated request to a non-read command (or the argv form) gets `403`. A cross-site request can't ride the cookie at all (the session cookie is `SameSite=Lax`), so cookie auth only ever serves the user's own same-origin pages.

Mint a token locally — this command is host-only and is itself never exposed over the API:

```bash
twicc token create --name "my-script"   # prints the secret once: twicc_pat_…
twicc token list                        # metadata only, never the secret
twicc token revoke <id>
```

Send it on every request:

```
Authorization: Bearer twicc_pat_…
```

## Limitations and gotchas

The things to keep in mind that don't apply when running the CLI on the same machine.

### Paths are resolved on the server, and must be absolute

Every path argument — a project directory, a `--directory`, an `--attach` file —
is interpreted on the **machine running TwiCC**, not on the client. There is no
meaningful working directory over HTTP, so **relative paths are rejected**. Pass
absolute paths as they exist on the server. (A project can also be addressed by
its id instead of its path.)

### Attachments: absolute server path or base64 data URI

`--attach` accepts either an absolute path to a file **on the server**, or an
inline base64 data URI when the file only exists on the client:

```
data:<media-type>;base64,<base64-payload>
```

Only the `;base64,` form is supported. This is what lets a remote caller attach
a file that isn't present on the server's filesystem.

### Blocking waits can be cut short — mind the timeouts

`session/wait-reply` and `sessions/wait-reply` are **blocking long-polls**: the
server holds the HTTP response open until the wait concludes or the command's
`--wait-timeout` (default 300 s) elapses (a timeout is a normal result, not an
HTTP error). The `--wait-reply` forms of `create-session`, `send-message` and
`send-messages` hold the connection the same way, for the send and then the
wait. Over HTTP this means:

- the **client's** HTTP read timeout must be **≥** the command `--wait-timeout`
  (plus the send's `--timeout` on a `--wait-reply` form), or the client aborts a
  wait that would otherwise have succeeded;
- any **intermediate proxy** (or the remote server itself) can drop a connection it
  considers idle before `--wait-timeout` is reached, interrupting the wait.

Keep `--wait-timeout` modest and resume from the returned cursor, or raise the
relevant client/proxy idle limits.

The `process`, `process/*`, `processes` and `processes/*` routes stop working on
2026-10-01 (exit `64`, error naming the replacement); use the `session` /
`sessions` routes.

### Some commands are local-only

A few commands are host-bound or interactive and are **not** exposed over the API:
`password`, `token`, `whoami`, `run`, and the `claude` / `codex` passthroughs.

## Driving a remote from the CLI (`--remote`)

The `twicc` CLI can run any command against a remote TwiCC instead of locally:

```
twicc --remote <url> [--remote-token <token>] <command> [args…]
```

- `--remote <url>` (or `--remote=<url>`) targets the remote's `/rpc/`. A **bare**
  `--remote` takes the URL from `TWICC_REMOTE_URL`.
- `--remote-token <token>` (or `TWICC_REMOTE_TOKEN`) supplies the Bearer token.
- For both, an explicitly passed value wins over the environment variable.

The forwarder mirrors the CLI: it resolves the command, POSTs it to the matching
`/rpc/` route, prints the remote command's `result` to stdout and `error` to
stderr, and **exits with the remote command's exit code** — so a script behaves
the same whether run locally or via `--remote`. A transport / remote-layer
failure instead (unreachable host, rejected auth, HTTP error, timeout, malformed
response) prints `twicc: remote error…` to stderr and exits with a reserved code
(**7**).

Remote-specific behavior (the same limitations as above, from the client side):

- **`--attach <local file>`** is read on the client and inlined as a base64
  `data:` URI, so a local — even relative — path works without the file existing
  on the server.
- **`remote:` scheme** — the inverse of inlining: to point at a file that already
  lives on the **server**, prefix an **absolute** server path with `remote:` (e.g.
  `remote:/srv/data/audit.md`). The forwarder strips the scheme and sends the bare
  path; the server reads it from its own filesystem. Supported on the prompt
  (`create-session` / `send-message`), `--message` (`send-messages`), and
  `--attach`. Only valid with `--remote`, and the path must be absolute.
- **Path arguments** (`--project`, `--directory`) are resolved on the server, so
  they must be absolute (or, for `--project`, an id) — there is no caller working
  directory over HTTP.
- **`wait-reply` commands** and the **`--wait-reply`** forms block as a
  long-poll; the client read timeout is sized to `--wait-timeout` (plus the
  send's `--timeout` on a `--wait-reply` form; mind any intermediate proxy idle
  limit).
- **Local-only commands** and the **`self`/`parent`** session keywords are
  rejected client-side over `--remote` — they only mean something on the local
  host.
