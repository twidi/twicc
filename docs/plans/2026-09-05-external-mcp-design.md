# External MCP access

Date: 2026-09-05
Status: Design for review. Implementation is not part of this change.

## 1. Purpose and decisions

Expose TwiCC to common remote MCP clients, including cloud clients and local applications.
Use standard MCP authorization. Do not build adapters for obscure clients.
Keep TwiCC single-user, in its existing backend process, with its existing SQLite database.

The owner authorizes each external connection. Each connection has separate, revocable credentials.
External connections receive full access to the existing MCP tools, except session-relative operations.
Internal Claude and Codex agents keep automatic access with their existing session tokens.

Confirmed product decisions:

- Require a dedicated HTTPS MCP origin. Do not fall back to `publicBaseUrl`.
- Separate its hostname from the Public, Share, and Peer hostnames.
- Serve MCP at `/mcp` and OAuth endpoints under `/mcp/oauth/`.
- Serve standard discovery documents under `/.well-known/`.
- Expose no unrelated application routes on the MCP origin.
- Approve or refuse requests from any authenticated TwiCC device.
- Show persistent, synchronized pending-request notifications.
- Provide connection management in Settings, including optional names and revocation.
- Attribute external messages to the connection, never to a fabricated TwiCC session.

## 2. Existing components and approach

`src/twicc/mcp/endpoint.py` authenticates session tokens and API tokens before dispatch.
`server.py` uses the official Python MCP SDK with stateless Streamable HTTP.
`tools.py` derives tools from the CLI registry. Dispatch already runs inside the backend.
`identity.py` supplies deterministic session tokens. `mcp_base_url()` always points agents to loopback.

`origin_gate.py` wraps the application before static files, Django, MCP, and WebSockets.
`core/services/origin_policy.py` owns the public-origin classification rules.
Settings already support public-origin validation and synchronized changes.
The peer UI supplies manager-dialog and notification patterns.

Use the official MCP SDK's OAuth handlers and provider interface.
Add TwiCC persistence, owner consent, token lifecycle, and external caller identity around these components.
Keep the low-level MCP dispatcher and the existing command services.

Rejected alternatives:

- Django OAuth Toolkit: do not introduce Django user authentication to satisfy a library integration.
- An external authorization service: conflicts with the single-process deployment requirement.
- A second MCP command implementation: creates avoidable drift from internal tools.

The SDK is not a complete authorization service. Verify its pinned-version behavior before implementation.
In particular, inspect CIMD support, resource validation, client authentication, and code consumption.
Use focused protocol extensions where needed. Do not claim that SDK defaults satisfy every requirement below.

## 3. Origins and routes

Add `mcpBaseUrl` and `externalMcpEnabled` to synced settings. Default external access to disabled.
Reuse the public-origin parser, collision validation, live cache, and invalid-setting quarantine behavior.
Require HTTPS and an origin-only value. Reject credentials, paths, queries, and fragments.

For `mcpBaseUrl = https://mcp.example.com`:

| Resource | URL or path |
|---|---|
| MCP resource identifier | `https://mcp.example.com/mcp` |
| OAuth issuer identifier | `https://mcp.example.com/mcp/oauth` |
| MCP endpoint | `/mcp` |
| Authorization endpoint | `/mcp/oauth/authorize` |
| Token endpoint | `/mcp/oauth/token` |
| Dynamic registration endpoint | `/mcp/oauth/register` |
| Revocation endpoint | `/mcp/oauth/revoke` |
| Browser waiting flow | Explicit routes below `/mcp/oauth/` |
| Resource metadata | `/.well-known/oauth-protected-resource/mcp` |
| Authorization metadata | `/.well-known/oauth-authorization-server/mcp/oauth` |

The authorization metadata path follows RFC 8414 for an issuer with a path component.
This keeps the MCP issuer separate from any future, unrelated TwiCC OAuth issuer.
Discovery remains rooted at `/.well-known/`; OAuth operations remain below `/mcp/oauth/`.
Advertise the exact resource metadata URL in the MCP `401` challenge.
Do not add discovery aliases without a demonstrated requirement from a supported mainstream client.

The MCP host allows only registered MCP, OAuth, discovery, and OAuth-page asset routes.
Unknown routes return plain `404`. A prefix alone does not authorize arbitrary files or handlers.
Reject application WebSockets, REST/RPC endpoints, SPA fallback, shares, peers, artifacts, and general static assets.
The root URL also returns `404`.

Public, Share, and Peer origins do not expose the external MCP or its OAuth routes.
Owner connection-management endpoints remain on the normal authenticated application surface.
Changing the MCP origin invalidates its outstanding requests and grants. The UI explains that reconnection is required.
Disabling external access revokes its credentials and expires pending requests. Re-enabling does not restore grants.
Neither operation changes internal MCP configuration or session tokens.

The tunnel forwards this host to the existing backend port and preserves the request authority.
It must not require a provider login. No path exemptions on the Public origin are needed.

## 4. Authentication boundaries

Use explicit caller kinds: internal session, external connection, and the existing non-MCP local caller context.
An external call has a connection ID and no session ID.
Carry this immutable context across async dispatch and worker threads with ContextVars.
Always reset context after invocation, including failed calls.

Internal entry:

- Keep the existing loopback URL and deterministic session tokens.
- Preserve Codex draft-to-canonical aliases and backend-restart behavior.
- Require the direct local route for session tokens; do not accept them on the MCP public host.
- Retain local API-token behavior if needed for existing tooling, without exposing it remotely.

External entry:

- Require the configured MCP host and enabled external access.
- Accept only valid OAuth access tokens issued for the MCP resource.
- Reject internal session tokens, API tokens, and application cookies as MCP credentials.
- Validate the grant on every request, including after refresh or backend restart.
- Never infer an internal session through PID ancestry.

Do not use loopback source IP alone to identify an internal call.
Reuse direct-local checks and validate the local authority as well as the public-host routing policy.
Document the proxy contract: preserve Host and forwarding information; never rewrite public traffic as a direct local call.

The current password-based remote gate must not reject an otherwise valid OAuth request.
Make that exception specific to the external MCP/OAuth surface. Do not weaken application authentication.
Owner decisions require authenticated application access, or the existing direct-local owner access.
No owner password or application session cookie is required on the dedicated public MCP host.

## 5. OAuth protocol and compatibility

Support Authorization Code with PKCE S256 for public and confidential clients.
Publish resource metadata and authorization-server metadata.
Bind codes and tokens to the client, exact redirect URI, granted scope, and MCP resource.
Return the issuer identifier in authorization responses and advertise that support accurately.
Reject missing or mismatched protocol fields before creating an actionable owner request.

Support CIMD and Dynamic Client Registration for common clients.
Use registered clients as the fallback for mainstream clients that require fixed credentials.
Keep these mechanisms behind the normal connection flow; the owner does not choose an OAuth protocol variant.
Provide advanced fixed-client setup only when a supported client needs it.
Registration creates a client record, not an authorization to use TwiCC.

CIMD retrieval requires HTTPS, bounded fetches, redirect controls, and SSRF protection.
These controls belong to OAuth metadata retrieval and do not change artifact-network-broker policy.
Validate metadata and redirect URIs. Do not trust a client display name as verified identity.
Do not implement OIDC accounts, enterprise federation, device authorization grants, or client-credentials grants in this scope.

Use one application scope, `twicc:full`, for the existing MCP surface.
No per-tool permission editor is required. Consent clearly states the full-access grant.
Do not advertise unrelated identity scopes such as `openid`, `email`, or `profile`.

Initial lifecycle defaults:

- Pending authorization: 10 minutes.
- Authorization code: 60 seconds, single use.
- Access token: 15 minutes.
- Refresh token: 90 days of inactivity, rotated on every use.

Generate opaque random credentials and store only their hashes where lookup permits.
Store client secrets as hashes. Never expose tokens in owner lists, logs, URLs, or notifications.
Consume authorization codes atomically. Rotate refresh tokens atomically and detect reuse.
Revoke the affected token family on confirmed refresh-token reuse.
Authorize rotation and revocation through the same durable grant state.

Apply bounded request sizes, registration/request rate limits, and pending-request limits.
Keep owner-facing failures actionable without exposing secrets.
Use SDK protocol errors and standard HTTP status codes, including discoverable `401` responses.
Handle browser-client CORS and preflight requests on the protocol surface only.

## 6. Consent from any device

1. A client starts OAuth and opens the authorization page on the MCP origin.
2. TwiCC validates the request and creates a short-lived pending authorization.
3. The initiating browser receives a private continuation handle and a displayed verification code.
4. Authenticated TwiCC devices receive a pending-request notification.
5. The owner opens the review dialog and verifies the code from the initiating browser.
6. The owner approves or refuses. The backend commits the first valid decision atomically.
7. All connected devices remove the pending notification.
8. The initiating browser detects completion and resumes the registered OAuth callback.
9. The client exchanges the code with its PKCE verifier and receives credentials.

Require code verification before approval. Do not provide a blind one-click approval in the toast.
The review displays the client name, client identifier, redirect host, request time, and full-access scope.
The owner can assign an optional connection name during review or later.
Only an authenticated owner action changes the decision. Polling the waiting page cannot approve anything.

Keep the waiting page and its assets inside the OAuth surface.
Use bounded HTTP polling with a private continuation handle; no general application WebSocket is needed.
Do not send tokens, codes, or continuation handles in the shared owner WebSocket broadcasts.
Only the initiating browser can redeem the continuation and receive the callback redirect.
Preserve the client's state and exact validated redirect URI.

The approving device does not execute the callback. This preserves localhost callbacks on another device.
If the initiating flow expires or is abandoned, the client starts a new flow.
Approval can precede token exchange; do not label a connection active until exchange succeeds.
Persist the request state so a backend restart does not erase an unexpired request.

State transitions: `pending -> approved -> completed`, `pending -> refused`, or expiration before completion.
Requests remain bounded and eventually expire even if no browser returns.
Repeated decisions return the committed state; they do not issue a second grant.

## 7. Persistence and management UI

Add database models for OAuth clients, authorization requests, external connections, and token credentials.
Use the existing database-write coordination and broadcast-after-commit patterns.

A connection represents one owner-approved grant, not a global client ID or an HTTP transport session.
The same client ID can have multiple independent connections.
Revoking one connection must not revoke another device or integration using that client ID.

Connection fields include its opaque ID, client reference, optional owner name, creation time,
last-use time, revocation time, and authorized resource/scope.
Keep short-lived codes and refresh-token families linked to the connection.
Throttle last-use writes to avoid a database update on every tool call.

Settings contains an MCP section with external enablement, dedicated origin, and the copyable MCP URL.
`Manage connections` shows active/revoked connections and unexpired pending requests.
Allow optional renaming, individual revocation, request review, and refusal.
Show the declared client identity separately from the owner-supplied name.

Pending notifications have no display timeout. Reconcile them from durable state on startup and reconnect.
Approval, refusal, and expiration remove them everywhere. Use stable request IDs to prevent duplicate toasts.
Connected devices receive live notifications; suspended devices recover state when they resume.
Browser/system notifications can reuse existing mechanisms but are not a guaranteed mobile push service.

The connection manager is owner-only. It is not exposed as an MCP tool.
Revocation rejects subsequent requests and refreshes immediately.
It does not undo completed operations or promise cancellation of an operation already executing.

## 8. Tool behavior and provenance

Reuse the existing tool registry, schemas, argument validation, and result envelope.
Generate an external description variant without internal tool-search instructions or session-relative promises.
Hide `whoami` externally and reject explicit calls to it.
Validate session-relative arguments at the external boundary, not by scanning arbitrary prompt text.

| Internal behavior | External behavior |
|---|---|
| `whoami` | Unavailable |
| Target `self` or `parent` | Reject; require an explicit session ID |
| Relative spawn/tree/descendant/sibling filters | Reject keywords; accept explicit IDs |
| `topology` defaulting to `self` | Require an explicit anchor ID |
| `create_session` automatic parent attribution | Create without a session parent; record external provenance |
| Session sender headers | Use external-connection attribution |
| `peer_send` origin session | No fabricated session; retain connection provenance locally |
| Client-local file path | No client filesystem access; accept uploaded base64 data or a server path |

`siblings` is a supported filter with an explicit session ID, not a globally forbidden word.
Keep unrelated aliases such as model/effort aliases unchanged.
Do not treat an explicit target session as the caller or allow caller impersonation through tool arguments.

All existing project, workspace, session, process, search, artifact, peer, usage, status,
and share operations remain available within their existing technical contracts.
Existing provider constraints and remote peer approval requirements remain in force.
Paths always refer to the TwiCC host. Existing attachment and response-size limits still apply.
Long-running waits remain subject to client/proxy timeouts; do not introduce special client adapters for them.

External full-access grants explicitly permit share operations across the owner's resources.
Do not obtain this authority accidentally from the current missing-caller-means-human fallback.
Keep internal agent share toggles, subtree restrictions, and provenance checks unchanged.
Record the external connection for externally created shares instead of setting `created_by_session`.

Messages use the existing sender-header primitive:

- Unnamed connection: `Message via external MCP`.
- Named connection: `Message via ChatGPT`, for example.

The backend gets the name from the authenticated connection, never from a tool argument.
Escape the name using the existing inline-header escaping rules.
Store the rendered name with the message so a later rename does not rewrite conversation history.
Keep durable connection provenance for initial session prompts, subsequent messages, and external share creation.
Use the same attribution for singular and batch sends. Preserve internal message wording.

## 9. Implementation boundaries

- `mcp/identity.py`: caller kinds and transport-authenticated provenance.
- `mcp/endpoint.py`: separate internal and external authentication policies.
- `mcp/server.py` and `tools.py`: external catalog and validated caller-aware dispatch.
- New focused modules under `mcp/oauth/`: provider adapter, routes, credentials, consent, and cleanup.
- `core/models.py` and migrations: clients, requests, connections, credentials, and required provenance fields.
- `core/services/origin_policy.py`, `origin_gate.py`, and synced settings: dedicated MCP routing.
- Owner REST handlers and WebSocket snapshots/events: management and consent state.
- Settings and connection-manager components: configuration, review, naming, and revocation.
- Existing CLI caller resolution and sender-header helpers: explicit external identity without PID fallback.
- Share services: explicit external full-access branch and provenance.

Do not refactor unrelated CLI commands or authentication flows.
Preserve CLI PID resolution outside authenticated in-process MCP dispatch.
Update MCP descriptions and relevant documentation. Bump the plugin version if packaged skills change.

## 10. Verification and acceptance

Protocol tests cover discovery, PKCE, exact redirects, issuer/resource binding, expiry, and client registration.
Test code replay, concurrent exchange, refresh rotation/reuse, revocation, and restart persistence.
Test owner authentication, consent-code verification, polling isolation, expiration, and simultaneous decisions.

Routing tests cover every origin, malformed settings, collisions, disabled access, and unknown paths.
Prove that the MCP host cannot reach the SPA, RPC, files, shares, peers, or application WebSockets.
Prove that public session/API tokens cannot bypass OAuth, including through a loopback tunnel.

Caller tests cover concurrent internal and external calls without identity leakage.
Test every keyword-bearing parameter, explicit-ID alternatives, unnamed/named messages, batch sends, and shares.
Keep current MCP result envelopes, annotations, attachments, and internal tool availability intact.

Internal regression tests cover Claude and Codex wiring, draft aliases, backend restart, and automatic access.
Exercise existing `tests/test_mcp_*.py` and origin-policy tests.
Frontend checks cover persistent toasts, reconnect snapshots, naming, revocation, and nested dialog events.

Compatibility acceptance includes one cloud client and one local mainstream OAuth-capable MCP client.
Validate ChatGPT and Claude connection flows where accounts are available.
Record actual client results separately from protocol tests; do not claim compatibility from schema inspection alone.
If account access is unavailable, report the unexecuted checks explicitly.

No package installation, migration application, or server restart is required to review this design.
Implementation creates migrations; the owner applies them through the normal devctl startup workflow.

## 11. References

- [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
- [MCP authorization security](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/security-considerations)
- [RFC 8414: issuer metadata paths](https://www.rfc-editor.org/rfc/rfc8414.html#section-3)
- [MCP Python SDK OAuth routes](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/auth/routes.py)
- [MCP Python SDK provider interface](https://github.com/modelcontextprotocol/python-sdk/blob/main/src/mcp/server/auth/provider.py)
- [OpenAI MCP authentication](https://developers.openai.com/plugins/build/auth)
- [Claude remote connectors](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [Existing origin routing design](2026-08-13-peer-origin-routing-design.md)
- [Existing agent sharing design](2026-08-10-agent-sharing-design.md)

External documentation was consulted on 2026-09-05.
Recheck client-specific registration requirements against the implementation's pinned SDK before coding.
