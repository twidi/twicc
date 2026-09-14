---
title: "External MCP"
---

External MCP lets an agent outside TwiCC work with your instance. For example,
you can connect an MCP client to inspect projects, create sessions, or send
messages with images.

TwiCC remains a single-user application. You approve each connection to your
instance. You do not create another user account for the connecting client.

### Internal and external MCP

Agents running inside TwiCC already have an internal MCP connection. They do
not need this setup or an OAuth approval. Disabling external MCP leaves their
internal access available.

External clients connect through a dedicated public address. They use OAuth
to obtain access after your approval.

### What a connection can do

An approved connection has full access to the external MCP tools. It can read
and change your TwiCC data, manage projects and sessions, send messages, and
manage shares. There are no per-connection permission choices.

Approval covers the connection, not each tool call. The client can act without
another TwiCC approval for each message or session it creates.

An external client is not a TwiCC session. It cannot use `whoami`, `self`, or
`parent` to identify a calling session. It must supply explicit session IDs
where the tool needs a session. Session-relative operations require that
context; they cannot infer it from the external connection.

### Before connecting

Start TwiCC with a password. External MCP cannot be enabled without one.
If you restart TwiCC without a password, TwiCC disables external MCP and preserves
existing authorizations. The dedicated URL stays saved. After restoring a password,
enable external MCP again. Clients can resume with their existing credentials,
subject to their normal expiration. Previously revoked authorizations stay revoked.

To set a password, run:

```
<twicc> password set
```

For `<twicc>`, use your usual way to start TwiCC. Restart TwiCC after you set
the password.

Open **Settings → External MCP**. Enter your **Dedicated MCP URL**, then select
**Apply**. Enter the HTTPS origin, such as `https://mcp.example.com`, without
`/mcp`. Then enable **external MCP access**.

Use a dedicated hostname, different from your External, Sharing, and Peers
addresses. This hostname serves only MCP and its OAuth endpoints. It does not
serve your TwiCC interface, files, or other application routes.

The address must be reachable from the client. MCP calls are machine-to-machine.
If your usual tunnel asks visitors to sign in by email, SSO, or another
interactive check, do not use it for MCP. The client cannot complete that check.

Create a separate tunnel address without that gate and point it to your TwiCC
server. Keep the protection on your usual TwiCC address. TwiCC protects the
MCP address with OAuth and your connection approval.

See [reaching TwiCC from anywhere](help/external-url) for the general tunnel
and password setup.

### Connecting a client

Copy the **MCP URL** from Settings and enter it in your client's remote MCP
configuration. This address includes `/mcp`, for example
`https://mcp.example.com/mcp`.

Use a client that supports remote MCP with OAuth. The exact setup controls
depend on the client. TwiCC supplies the authorization information through
standard MCP discovery endpoints.

Desktop clients can receive the browser callback on `http://127.0.0.1` or
`http://[::1]` with a different available port at each connection. The registered
address, path, and query must still match. This port exception does not apply to
remote HTTPS callbacks or the hostname `localhost`.

Start the connection from the client. It opens an authorization page with a
verification code. Keep that page open until the connection finishes.

TwiCC shows a persistent connection-request notification on your signed-in
devices. Select **Review**, then enter the code from the connecting browser.
The code connects your approval to the request you started.

You can approve from another device. The browser that started the connection
still completes the OAuth flow. The notification disappears from your other
devices once the request is resolved.

Review the client details before selecting **Authorize**. Select **Refuse**
if you did not start the request. Requests expire after ten minutes; start
again from the client if the request expires.

### Names and message headers

You can give each connection an optional name, such as **ChatGPT** or
**Desktop assistant**. You can change it later in the connection details.

The review starts the name field from the name the client declares for itself.
Confirm it, change it, or clear it before you authorize. TwiCC uses only the
name you approved: a client cannot name itself in your conversations.

Messages sent through that connection carry a header such as **Message via
ChatGPT**. Without a name, the header says **Message via external MCP**.
The header identifies the connection, not another TwiCC session.

The **Client ID** in the details is a technical identifier. It is not an access
token or the verification code. You do not need to copy it to approve a request.

### Managing connections

Open **Settings → External MCP → Manage connections**. The list shows three sections
when they contain entries:

- **Reviews** — requests waiting for your decision;
- **Active** — approved connections, including those still completing OAuth;
- **Revoked** — connections that no longer have access.

Select **Review** to inspect a request, or **Details** to inspect an existing
connection. Closing details returns to the list when you opened them there.
Opening a request from its notification goes directly to its details; closing
that dialogue does not open the list.

Several clients can connect independently. Revoking one connection does not
revoke the others.

### Revoking access

Select **Revoke** to stop a connection from making further MCP calls. Its
existing credentials no longer grant access. Revocation does not undo work
already done or stop sessions the client previously started.

The revoked connection remains in the list. To connect again, start a new
connection from the client and approve its new request.

Changing the dedicated MCP URL or disabling external access revokes existing
connections. Configure the new address in your clients and connect again.

### Protection against OAuth abuse

TwiCC limits new OAuth requests per client and per network source. One client
can have three pending requests; one source can have ten. One source can keep
twenty registered clients that have never received an authorization.

Repeated admission refusals or unusually high admission traffic trigger a
ten-minute pause on new registrations and authorization requests. The owner
receives a **Suspected OAuth abuse** notification. Settings and the connection
manager show the reason, recent counters, and the remaining pause time.

The pause leaves existing MCP connections, token renewals, revocation, and
authorization requests already in progress available. It expires automatically.
The alert remains until you dismiss it. High traffic is a suspicion of abuse,
not proof of an attack. Multiple clients can share a network source.

Select **Suspend all external access** to stop all new external MCP and OAuth
requests without revoking existing authorizations. Requests already executing
may finish. To resume, enable external access again in Settings. This manual
suspension persists across restarts.

Traffic counters, alerts, and automatic pauses reset when TwiCC restarts.
The registration and pending-request quotas remain stored. Normal token and
request expiration still applies throughout a suspension.

Rate limits use the client address supplied by the ASGI server. A proxy must
pass the real client address and be trusted by the server; TwiCC never trusts
arbitrary forwarding headers itself. IPv6 addresses in the same /64 share a
source quota. Only a keyed source hash is stored, not the IP address.

### If a connection does not finish

Check that external MCP is enabled and that the client uses the copied URL,
including `/mcp`. Check that the dedicated hostname reaches this TwiCC instance
and does not show a tunnel-provider login page.

Keep the connecting browser open while you approve. If the list says
**connecting**, approval succeeded but the client has not completed OAuth yet.
If the request expires, start a new connection from the client.

### Batch commands

Use `batch_read` to collect independent reads in one MCP call. It runs up to four
commands at once by default. Set `mode` to `sequential` to run them in order.
Use `batch` for ordered commands, including changes. It always runs sequentially.
The same tools are available to agents running inside TwiCC.

Discover each command's schema first. Supply its normal MCP name and arguments:

```json
{
  "calls": [
    {"id": "recent", "name": "sessions", "arguments": {"limit": 5}},
    {"id": "projects", "name": "projects", "arguments": {}}
  ]
}
```

Each call needs a unique `id`, a command `name`, and an `arguments` object.
TwiCC validates the complete batch before any command starts. One invalid call
rejects the batch without executing its other commands. Normal permission checks
still apply to every command. External calls still require explicit session IDs.

Results arrive together, in input order. Each record includes its ID, execution
status, and the command's normal `{exit_code, result, error}` response.
Check the aggregate `ok` field and each record. A completed batch can contain
failed or skipped commands. A command can also succeed while its large response
is omitted (`response_omitted: true`); in that case the aggregate `ok` is false.

`batch` stops after an execution error by default. Set `on_error` to `continue`
to attempt the remaining commands. `batch_read` defaults to `continue`.
Parallel execution requires `continue`; use sequential mode for `stop`.
Output omission does not stop subsequent commands. Completed changes stay applied:
there is no rollback, automatic retry, or transfer of results into later arguments.
Nested batches are not supported.

A batch accepts up to 20 commands. Both MCP connections share a limit of four
active batches and eight active batch commands across the instance. Extra batches
receive `server_busy`. Individual tools remain outside this batch admission limit.
The complete request retains the existing 48 MiB limit, including attachments.
Command responses over 384 KiB are explicitly omitted. The combined tool result,
including text and structured data, stays below 16 MiB. Clients can have smaller limits.

Keep batches short and use separate calls for long waits. Existing command timeouts
still apply; there is no additional batch timeout. Long waits can occupy all batch
capacity, even after the client cancels its request. A cancellation or timeout does
not prove that writes stopped. Inspect affected resources before retrying an uncertain
write. Batch IDs identify calls for diagnostics; they do not prevent duplicate work.

TwiCC checks external authorization before starting each command. Expiration,
revocation, or disabled external access stops the remaining commands. If TwiCC
cannot verify authorization, it also stops them. Already-started commands can
finish, and their collected results are preserved.
