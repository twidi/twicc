# Peer Origin Routing Design

**Status:** approved design, not implemented
**Date:** 2026-08-13

## 1. Scope

This design restricts the Peer HTTP surface to the configured Peer address.
It also centralizes routing for the External, Share, and Peer addresses.

The three settings keep these internal names:

- `publicBaseUrl` is the **External address**;
- `shareBaseUrl` is the Share address, shown as **Share host**;
- `peerBaseUrl` is the Peer address, shown as **Your address**.

This design replaces the ingress decision in
`docs/plans/2026-07-24-peer-messaging-design.md` section 4.3. A Peer address can
now share the External address or use a dedicated routing authority.

The common origin syntax remains defined by
`docs/superpowers/specs/2026-08-13-public-origin-settings-design.md`.

### 1.1 Hard compatibility boundary

**This boundary is a core design invariant.** The Peer System exists only on
the owner's development machines. It has never been released, distributed, or
deployed to another user.

Every instance and database that contains Peer data is owner-controlled
development or test state. The owner can recreate that state when necessary.

The first released Peer version defines the only supported Peer contract.
Earlier development builds create no compatibility obligation.

No backward or forward compatibility is required between development builds.
This rule covers:

- frontend state, components, payloads, and cached data;
- backend APIs, services, validation, and behavior;
- Peer settings and their development-only stored values;
- the Peer wire protocol, endpoints, payloads, authentication, and responses;
- the database schema and every development-only Peer row;
- tests, fixtures, and generated Peer history.

The implementation must not add compatibility machinery for an unreleased
Peer behavior. In particular, it must not add:

- protocol or feature-version negotiation;
- capability detection for older Peer implementations;
- fallback endpoints or fallback request formats;
- dual-read or dual-write behavior;
- legacy parsers, schema adapters, or old-value readers;
- repair, backfill, merge, or conversion of development-only Peer history;
- rolling-deployment compatibility between old and new Peer code.

Unreleased Peer migrations can be replaced or rewritten. A Peer migration can
fail explicitly when it encounters unexpected development-only Peer state. It
does not need a repair path because the owner can recreate that database.

Released TwiCC databases contain no Peer data. A normal release migration must
still preserve data from released non-Peer features. This invariant does not
authorize damage to unrelated TwiCC data.

Tests target the final Peer contract and clean migration from a released TwiCC
schema. They do not test upgrade paths between Peer development commits.

A review must not require Peer compatibility machinery unless this release
boundary changes before the first Peer release.

## 2. Current behavior

`src/twicc/core/services/public_origin.py` defines
`normalize_public_origin`. It canonicalizes the three configured origins. It
removes scheme-specific default ports and exposes the normalized hostname and
remaining port.

`src/twicc/core/services/settings_mutation.py` normalizes changed origin
settings inside `_merge_and_write`. It rejects malformed changes atomically.
It does not reject an unchanged malformed value during an unrelated update.

`frontend/src/utils/publicOrigin.js` currently validates and normalizes origin
input in JavaScript. Its IPv6 and IDNA behavior diverges from Python. Python
preserves an expanded IPv6 spelling, while the JavaScript `URL` parser
compresses it. Python's built-in IDNA codec maps some Unicode hostnames
differently from the browser parser.

`frontend/src/components/app/SettingsPopover.vue` applies these additional UI
rules:

- Share cannot use `window.location.hostname`;
- plain HTTP for Peer produces a non-blocking warning.

`src/twicc/share/asgi_filter.py` defines `ShareHostGate` and `ShareOnlyApp`.
The gate reads `shareBaseUrl` for each request. It intends to compare only the
request hostname with the configured Share hostname. Its `_request_host`
helper splits at the first colon. It therefore does not parse a bracketed IPv6
`Host` value correctly, and it does not reject duplicate `Host` headers.

On the Share hostname, `ShareOnlyApp` serves the Share surface and required
assets. It rejects the working application, `/peer/`, `/mcp`, and general
static files.

On every other hostname, `ShareHostGate` serves the full application. It hides
Share-exclusive HTTP and WebSocket paths.

`src/twicc/asgi.py` places `ShareHostGate` outside BlackNoise. The gate runs
before static files, Django, `/mcp`, and application WebSockets.

No request-host gate currently protects `/peer/`. A configured Peer feature
can answer `/peer/` through any hostname that reaches the TwiCC listener.

Current coverage lives in:

- `tests/test_public_origin.py`;
- `tests/test_settings_mutation.py`;
- `tests/test_share_host_gate.py`;
- `frontend/src/utils/publicOrigin.test.js`;
- `frontend/src/stores/publicOriginSettings.test.js`.

## 3. Goals

- Expose `/peer/` only through the configured Peer address.
- Expose nothing except `/peer/` through a dedicated Peer address.
- Keep the dedicated Share hostname boundary.
- Keep the complete application on the External address.
- Permit local and LAN access without exposing `/peer/` there.
- Apply setting changes without a server restart.
- Validate each changed address and every relationship that contains it.

## 4. Out of scope

This design does not define relationship changes after `peerBaseUrl` changes.
It does not define revocation, credential invalidation, or reconnection.

`docs/plans/2026-08-13-peer-revocation-reconnection-design.md` is a temporary
work-in-progress draft. Readers, reviewers, planners, and implementers must
ignore it for this work.

That draft is not a requirement, dependency, or source of truth for this
design. It will be reviewed only after this routing work is complete.

This design allows the setting change without a relationship warning. It
deliberately leaves every resulting relationship effect unspecified.

The plain HTTP warning stays. It warns about unencrypted Peer tokens, not
about an address change.

This design does not add a Peer settings migration. The Peer System is new and
has not shipped. No deployed `peerBaseUrl` requires compatibility or repair.

## 5. Terms

### 5.1 Normalized origin

A normalized origin contains a scheme, hostname, and optional non-default
port. The common normalizer produces this value.

This design adds one strict hostname contract. Backend settings parsing and
request routing apply every rule. A hostname spelling must contain unescaped
ASCII only. A percent escape is invalid, even when it decodes to ASCII.
Unicode hostname input is invalid.

The hostname classes have these rules:

- `localhost` is a dedicated case-insensitive value. Its canonical value is
  `localhost`.
- IPv4 input is valid only as four decimal octets from 0 through 255. An octet
  has no leading zero unless it is `0`. A hostname made only of digits and dots
  is invalid unless it is canonical IPv4 dotted-decimal notation.
- IPv6 input accepts a valid bracketed IPv6 literal. Its canonical hostname is
  the lower-case, compressed RFC 5952 representation. An origin or authority
  keeps brackets around that hostname during serialization.
- Every other hostname is a DNS hostname. It contains 1 through 253 ASCII
  characters and one or more dot-separated labels. Each label contains 1
  through 63 ASCII letters, digits, or hyphens. A label starts and ends with an
  ASCII letter or digit. Empty labels, a leading or trailing hyphen, and a
  leading or trailing dot are invalid. Canonical DNS hostname case is
  lower-case.
- For each DNS label, first apply ASCII lower-casing to the whole candidate
  label. If the lower-cased label starts with `xn--`, the backend must validate
  it as an IDNA2008 A-label. It uses the lower-cased label for Punycode
  decoding. The payload must decode to a valid U-label. Re-encoding must
  produce the same lower-cased label. A malformed A-label is invalid. Explicit
  valid A-label input stays an ASCII A-label with lower-case canonical output.

The JavaScript input check is a simple subset of the Python check. It is not a
second origin parser. It rejects only these inputs:

- a non-string value;
- a protocol-relative value or an unsupported explicit scheme;
- a value without an apparent authority;
- an authority with credentials;
- a raw authority with non-ASCII data, a control character, or a percent sign;
- a trailing colon without a port.

The JavaScript check submits every other input unchanged after the normative
outer trim. It does not validate DNS label details, an A-label verdict, a port
range, a path, a query, a fragment, or IPv6 canonical form. Python returns the
authoritative verdict and canonical value after Apply.

This subset has one safety rule. JavaScript must never reject an input that
Python accepts. JavaScript can accept an input that Python rejects. Such an
input shows its error after Apply. The backend validates every changed setting.
The runtime gate also uses the Python validator. An invalid input never enters
routing.

Browsers expose UTS #46 through the URL parser. They do not expose IDNA2008.
UTS #46 is more permissive for some A-labels. JavaScript therefore makes no
IDNA2008 A-label verdict. It also does not use the browser parser to decide
whether an input is valid.

Stored values have a separate JavaScript consumer guard. Backend writes store
canonical values. The guard accepts only a value that has the recognizable
shape of a canonical stored origin. It does not normalize input or return a
validation verdict. Browser and Share URL consumers treat every other stored
value as unset. An A-label can satisfy lexical canonical shape. The guard does
not decide its IDNA2008 validity. The Python runtime gate remains authoritative
for routing.

Backend settings parsing and request parsing apply every hostname rule above
to the raw hostname token. They do not use percent decoding or Unicode-to-IDNA
conversion to make an invalid token valid.

Examples:

| Input | Normalized origin |
|---|---|
| `https://Example.com:443` | `https://example.com` |
| `http://Example.com:80` | `http://example.com` |
| `https://Example.com:8443` | `https://example.com:8443` |
| `https://[0:0:0:0:0:0:0:1]:8443` | `https://[::1]:8443` |
| `https://xn--fa-hia.de` | `https://xn--fa-hia.de` |
| `https://%65xample.com` | Invalid |
| `https://xn--.example` | Invalid |

### 5.2 Routing authority

A routing authority contains the normalized hostname and remaining port. It
does not contain the scheme.

The scheme is not reliable behind a proxy or tunnel. The ASGI scheme can
describe the proxy connection instead of the original client connection.

| Origin | Routing authority |
|---|---|
| `https://example.com` | `example.com` |
| `http://example.com` | `example.com` |
| `https://example.com:8443` | `example.com:8443` |
| `https://[::1]:8443` | `[::1]:8443` |

Default ports disappear during origin normalization. A non-default explicit
port remains part of the routing authority.

### 5.3 Share hostname

Share routing compares only the hostname. It ignores every port.

Browser cookies are not port-scoped. Therefore, a port cannot isolate Share
from the authenticated application.

## 6. Address relationships

### 6.1 Share

A non-empty Share address must use a different hostname from each non-empty
External or Peer address. A different scheme or port does not satisfy this
requirement.

The Share hostname is a dedicated public surface. It never serves the
working application, `/peer/`, `/mcp`, or general static files.

### 6.2 Peer shared with External

Peer shares External only when both normalized origins are exactly equal.

This authority serves the complete application. It also serves `/peer/`.

### 6.3 Dedicated Peer

Peer is dedicated when its routing authority differs from External. An empty
External address also makes every valid Peer address dedicated.

A dedicated Peer authority serves only HTTP paths below `/peer/`. It serves no
WebSocket path.

### 6.4 Ambiguous Peer and External addresses

Two origins can differ while producing the same routing authority. A
scheme-only difference is the relevant example.

TwiCC rejects this configuration. It cannot recover the external scheme after
a proxy removes that information.

Examples:

| External address | Peer address | Result |
|---|---|---|
| `https://x.example` | `https://x.example` | Shared |
| `https://x.example` | `http://x.example` | Rejected |
| `https://x.example` | `https://x.example:8443` | Dedicated |
| `https://x.example:7443` | `https://x.example:8443` | Dedicated |

## 7. Common settings validation

The backend validates each settings patch under the settings lock. An
**origin-setting patch** changes at least one of `publicBaseUrl`,
`shareBaseUrl`, or `peerBaseUrl`.

The backend validates only the origin fields that the patch changes. It also
validates each relationship that contains at least one changed field. An
unchanged invalid stored origin does not reject the patch. It contributes no
relationship operand until it becomes valid. This rule lets a user repair
several invalid stored origins one field at a time.

The backend applies these rules:

1. Empty values are valid.
2. Every changed non-empty value must satisfy the common origin syntax and the
   ASCII hostname contract in section 5.1.
3. Share and External cannot use the same non-empty hostname.
4. Share and Peer cannot use the same non-empty hostname.
5. Equal Peer and External normalized origins mean shared routing.
6. Different Peer and External routing authorities mean dedicated routing.
7. Different origins with one routing authority are rejected.

The update is atomic. A rejected update changes no setting and increments no
settings version.

Errors identify each invalid changed field. They also identify every field in
a checked relationship conflict. A multi-field patch can return more than one
field error.

Each Settings section keeps its own Apply action. One Apply submits only that
section's origin field. The stored value changes only after the backend accepts
the patch and broadcasts the authoritative value. A rejected patch retains the
field input. The UI shows only errors for the applied field in the active
section. It discards the symmetric error copies for fields in other sections.

Each visible relationship-conflict message names the other participating
address. Use these exact messages:

- Share and External:
  `The Share host must use a different hostname from the External address.`
- Share and Peer:
  `The Share host must use a different hostname from the Peer address.`
- Peer and External ambiguity:
  `The Peer and External addresses must be the same origin or use different authorities.`

This display rule loses no write-path error. A patch checks only relationships
that contain a changed field. The applied field is therefore one operand in
every relationship conflict that its write can report. The edit creates every
checked conflict. A conflict between two untouched stored values is not a
write-path concern. An unchanged invalid operand is also outside write-path
relationship checks. The runtime gate in section 11 neutralizes both states.

The WebSocket and CLI can still send several origin fields in one patch. The
backend validates every changed field and every relationship that contains a
changed field. It accepts or rejects that patch as one atomic update. An
accepted patch increments the settings version once.

The JavaScript subset provides immediate errors for its small rejection set.
The backend stays authoritative for the UI, WebSocket writes, and CLI writes.

The frontend must continue to compare Share with `window.location.hostname`.
This rule protects an active working hostname when the External address is
empty or different.

Changing the Peer address requires no confirmation or relationship warning.
This work performs no resulting Peer relationship transition.

## 8. Request authority parsing

The filter reads the HTTP `Host` header from the ASGI scope. It does not use
the ASGI scheme or forwarded-protocol headers.

The parser:

- requires exactly one `Host` header;
- rejects a missing, duplicate, or malformed `Host` header;
- applies the strict hostname contract from section 5.1 to the raw `Host`
  hostname token;
- accepts `localhost`, canonical IPv4, valid DNS hostnames, explicit valid
  A-labels, and valid bracketed IPv6;
- rejects Unicode, percent escapes, malformed A-labels, and non-canonical IPv4;
- lower-cases DNS hostname and A-label case and uses RFC 5952 for IPv6;
- requires brackets around an IPv6 `Host` hostname;
- preserves an explicit request port;
- serializes a bracketed IPv6 request authority;
- returns no authority for invalid input.

Expanded and compressed spellings of the same IPv6 address produce one
compressed routing authority. Python origin normalization and request parsing
must produce the same hostname representation.

A scope with no valid request authority rejects the whole request. HTTP
returns the plain `404 Not found` response. A WebSocket closes with `4404`.
The scope does not enter the `Every other authority` class and never reaches
the inner application.

The request parser does not remove a port. It cannot know the original scheme.

Configured origins remove their scheme-specific default ports. The outbound
Peer client therefore advertises and calls the same canonical origin.

`Host: example.com:443` does not match the authority `example.com`. It matches
only the configured authority `example.com:443`.

This contract assumes that the proxy or tunnel preserves `Host`. Standard
proxy operation does this. An explicit `Host` rewrite is operator configuration.

TwiCC cannot recover information removed by that configuration.

## 9. ASGI architecture

A common `PublicOriginGate` replaces the current `ShareHostGate`. It wraps the
application above BlackNoise.

The gate therefore runs before:

- general static file handling;
- Django and its SPA fallback;
- the raw `/mcp` ASGI endpoint;
- application WebSockets.

The gate reads the three settings for each request. The settings reader uses
the in-process cache.

One pure classifier converts the live settings into routing policy. A separate
request classifier compares the request authority and path with that policy.

The ASGI layer only executes the classifier result. This separation keeps
normalization, policy, and protocol responses independently testable.

Lifespan and other non-request ASGI scopes pass through unchanged.

## 10. Routing table

The Peer surface means every HTTP path starting with `/peer/`. Peer has no
public WebSocket surface.

This table defines routing when the stored settings produce a valid policy.
Section 11 defines the overrides for runtime-invalid settings.

| Request routing authority class | `/peer/` HTTP | Share-exclusive HTTP | Other HTTP | WebSockets |
|---|---|---|---|---|
| Share hostname | `404` | Existing Share policy | Existing Share-only policy | Share only |
| Dedicated Peer authority | Allowed | `404` | `404` | Close with `4404` |
| Shared External and Peer authority | Allowed | `404` | Full application | App only; Share closes `4404` |
| Every other authority | `404` | `404` | Full application | App only; Share closes `4404` |

"Every other authority" includes localhost, LAN names, and secondary tunnel
addresses. Those routing authorities can still serve the application, but
never `/peer/`.

An empty Peer address hides `/peer/` on every authority.

The common gate preserves these Share rules:

- the Share root redirects temporarily to `/share/`;
- Share pages and required public assets remain available;
- non-Share HTTP paths return `404`;
- only Share WebSockets remain available;
- Share-exclusive paths remain hidden on every other routing authority.

On a dedicated Peer authority, `/` returns `404`. The same applies to static
files, `/mcp`, `/api/`, `/rpc/`, artifacts, and every other route.

## 11. Runtime invalid settings

Normal write paths validate each changed field and each relationship that
contains it. They exclude every unchanged invalid stored field from those
relationship checks. Such a field can remain while the user repairs another
field. A changed field can therefore create a runtime conflict with it when
the runtime gate recognizes its hostname or authority. The runtime gate treats
the stored field as invalid and applies every rule below. Manual file changes
can also create active invalid stored state after restart.

The live policy builder fails closed for each affected public surface. It also
quarantines every recognizable Share hostname or Peer routing authority whose
public classification is invalid. A quarantined authority serves no HTTP or
WebSocket route. HTTP returns the plain `404 Not found` response without
calling the inner application. A WebSocket closes with `4404` without calling
the inner application.

At startup, a missing settings file and an empty JSON object activate valid
default settings. An unreadable file, malformed JSON, or a non-object JSON root
makes web routing unavailable. Concurrent startup requests use one consistent
settings result. They never combine default values with an available routing
state after a failed startup read.

A later manual edit to `settings.json` has no effect until TwiCC restarts.
Until restart, TwiCC continues the routing state established at startup.

After an unavailable startup, the owner can repair the file and restart TwiCC,
or use the settings CLI. The settings CLI remains usable while web routes are
unavailable. A successful CLI update restores routing without a restart.

A policy-build failure makes routing unavailable for that request. When routing
is unavailable, the gate rejects every HTTP request and WebSocket without
calling an inner application.

Runtime relationship checks use both valid and recognizable invalid settings.
A valid setting contributes its normalized hostname and routing authority. A
recognizable invalid setting contributes its recognized hostname or routing
authority. An unrecognizable invalid setting contributes no conflict operand.

The relationship conflict rows below use these operands. A recognizable
invalid setting can therefore disable another public surface until the setting
is corrected. Recognition never makes the invalid setting valid and never
enables its surface. Shared Peer routing still requires two valid equal
normalized origins.

The per-setting disabled and runtime-invalid states have these results:

| Stored state | Disabled public surface | Quarantine candidate |
|---|---|---|
| Empty Share address | Share | None |
| Invalid Share address with a recognizable ASCII hostname | Share | That Share hostname |
| Invalid Share address without a recognizable ASCII hostname | Share | None |
| Empty Peer address | Peer | None |
| Invalid Peer address with a recognizable routing authority | Peer | That Peer authority |
| Invalid Peer address without a recognizable routing authority | Peer | None |
| Share and External hostname conflict, including a recognizable invalid operand | Share | The Share hostname |
| Share and Peer hostname conflict, including a recognizable invalid operand | Share and Peer | The Share hostname and Peer authority |
| Ambiguous Peer and External authority, including a recognizable invalid operand | Peer | The Peer authority |
| Invalid non-empty External address | Peer classification | The configured Peer authority, when recognizable |

A Share quarantine candidate matches every request authority with that
hostname. A Peer quarantine candidate matches its exact routing authority,
including a remaining port.

The policy recognizes an invalid setting only when it can extract a raw
hostname token that satisfies section 5.1 and a valid optional port. It does
not decode percent escapes, convert Unicode through IDNA, or guess missing host
syntax. An unsupported scheme or a forbidden path can still leave an authority
recognizable. A missing or invalid hostname token cannot.

A valid External routing authority takes precedence over every quarantine
candidate. It keeps the working application available on that exact authority.
It serves `/peer/` only when a valid Peer origin is shared with that External
origin under section 6.2 and no invalid state disables Peer.

Runtime-invalid states compose cumulatively. The policy builder unions their
disabled public surfaces and quarantine candidates, then applies the valid
External-authority precedence rule. Rule order cannot remove an earlier
disable or quarantine result.

A setting can be too malformed to identify an authority. Such a setting
disables its affected public surface but cannot quarantine an unknown host.
Requests on an unknown host continue through the policy for their own request
authority.

The gate never repairs or writes settings. It never changes Peer rows.

An invalid value might not contain a parseable hostname. TwiCC cannot identify
or quarantine a tunnel hostname that the setting does not describe.

Hidden HTTP paths return a plain `404 Not found`. The response does not reveal
the configured addresses or the rejection reason.

Rejected WebSockets close with code `4404`.

## 12. Live changes

A successful per-field Apply changes routing for the next request without a
restart.

The routing change itself has no confirmation and no warning. Relationship
side effects remain unspecified and outside this work.

## 13. Verification

### 13.1 Origin and settings validation

The shared JSON fixture separates backend cases from frontend cases. Python
alone consumes the backend normalization, routing-authority, A-label, and
cross-address sections. These sections cover backend normalization,
routing-authority, A-label, relationship-classification, and pure
settings-validation cases from sections 5 through 7. The settings-mutation
tests below cover patch scope, atomicity, and version behavior.

The backend cases must reject Unicode hostnames and exercise explicit ASCII
A-label spellings. They must include expanded and compressed spellings of one
IPv6 address. Those spellings must produce the same RFC 5952 hostname,
normalized origin, routing authority, and cross-address classification.

The backend cases must cover `localhost`, canonical IPv4, every DNS label and
hostname length boundary, label-edge hyphens, empty labels, and a trailing dot.
They must reject a percent escape such as `https://%65xample.com`.

The backend A-label cases must require `HTTPS://XN--FA-HIA.DE` to normalize to
origin `https://xn--fa-hia.de`, hostname `xn--fa-hia.de`, and routing authority
`xn--fa-hia.de`. They must reject malformed A-labels in any ASCII case. One
case must show an A-label that UTS #46 accepts and IDNA2008 rejects.

JavaScript consumes only the fixture sections for its input subset and stored
consumer guard. The input cases assert each rejection that the subset keeps.
They also assert representative deferred inputs. The stored cases assert that
recognized canonical backend output is usable. They assert that non-canonical
stored text fails closed. The JavaScript suite names the backend-only sections
and does not consume their verdicts.

Every JavaScript rejection case must also be a Python rejection. Tests must
enforce this one direction. They must not compare error codes, error order, or
canonical output between the two languages.

`tests/test_settings_mutation.py` and
`frontend/src/stores/publicOriginSettings.test.js` must cover atomic conflict
rejection, multi-field changes, and unrelated updates. These tests catch a
partial write, a missed conflict that section 7 requires the patch to check, or
a frontend-only restriction. A defect changes one setting after rejection or
lets one write surface accept a checked relationship conflict.

The settings-mutation coverage must prove that an unchanged invalid stored
origin does not block a patch for another origin. It must prove that the user
can repair two invalid stored origins in either order. It must also prove that
a multi-field patch stays atomic and reports every error for its changed
fields and checked relationships.

Frontend Settings coverage must start with two invalid stored origins. It must
repair either field first while the other stays invalid. Each Apply must send
only its field. The store must change only through the authoritative backend
broadcast. A rejection must retain the field input. It must show only the
returned errors for the active field and discard copies for other sections.

### 13.2 ASGI routing

`tests/test_share_host_gate.py` must exercise every row and protocol column in
the routing table. It must include shared Peer, dedicated Peer, alternate
routing authorities, malformed `Host`, static files, `/mcp`, and live setting
changes.

The suite must cover every request-authority boundary from section 8:

- one valid `Host` header;
- missing, duplicate, and malformed `Host` headers;
- ASCII hostname case and explicit ASCII A-labels;
- Unicode hostname rejection;
- a percent escape such as `Host: %65xample.com` and a malformed A-label such
  as `Host: xn--.example`, both rejected;
- `Host: XN--FA-HIA.DE`, accepted as routing authority `xn--fa-hia.de`;
- `Host: XN--.example`, rejected as malformed with the section 8 HTTP and
  WebSocket outcomes;
- DNS label and hostname length boundaries, empty labels, label-edge hyphens,
  and a trailing dot;
- canonical IPv4 acceptance and non-canonical IPv4 rejection;
- bracketed expanded and compressed IPv6 `Host` values;
- an explicit non-default request port;
- an explicit scheme-default request port that does not match a portless
  configured authority.

The suite must also cover routing-settings availability. A missing file and an
empty JSON object must use the valid default policy. An unreadable file,
malformed JSON, a non-object JSON root, a settings-read exception, or a
policy-build exception must reject without calling either inner application.
Concurrent startup requests must use one startup settings result. They must not
serve either inner application from fallback defaults after a failed startup
read. The suite must prove that a later manual file edit has no effect until
restart. It must prove recovery after a repaired file plus restart. It must also
prove that a successful settings write restores routing without restart.

The uppercase `Host` cases catch a case-sensitive A-label prefix test and a
case-sensitive round-trip comparison. A defect appears as rejection of the
valid uppercase `Host`, acceptance of the malformed uppercase `Host`, or a
different canonical authority.

The suite must cover every per-setting disabled and runtime-invalid state in
section 11. It must replace a single compound invalid state with this
interaction basis:

1. Configure valid External `https://app.example`, recognizable invalid Share
   `ftp://share.example`, and recognizable invalid Peer
   `https://peer.example/forbidden`. The complete disabled-surface set is Share
   and Peer. The surviving quarantine set is `share.example` and
   `peer.example`. The exact External exception is `app.example`.
2. Configure unrecognizable invalid External `https://`, valid Share
   `https://share.example`, and recognizable invalid Peer
   `https://share.example/forbidden`. The Peer operand participates in the
   Share-and-Peer conflict and disables the otherwise valid Share surface. The
   complete disabled-surface set is Share and Peer, including Peer
   classification. The surviving quarantine set is `share.example`. There is
   no exact External exception because External is invalid. Another routing
   authority must still serve allowed application routes.
3. Configure valid External `https://app.example`, recognizable invalid Share
   `ftp://share.example`, and recognizable invalid Peer
   `https://app.example/forbidden`. The Peer operand participates in the
   Peer-and-External conflict. Before precedence, the quarantine candidates are
   `share.example` and `app.example`. Valid External precedence removes only
   `app.example`, so the surviving quarantine set is `share.example`. The
   complete disabled-surface set remains Share and Peer. The exact External
   exception is `app.example`.

Each interaction case must assert the complete disabled-surface set, the
surviving quarantine set, and the exact External exception stated above. On a
surviving quarantine authority, every HTTP request must receive plain `404`
and every WebSocket must close with `4404`, without an inner-application call.
On an allowed non-quarantined authority, application HTTP and application
WebSockets must reach the inner application. Disabled Share and `/peer/`
routes must still receive their defined gate responses.

Concrete failures are an inner-app call on a surviving quarantine authority,
an exposed disabled surface, a non-`404` HTTP result, a WebSocket close other
than `4404`, a quarantined authority that reaches the application, or a lost
application route at the exact External exception.

Each authority and runtime-policy case must distinguish an inner-application
call from a gate response. It must assert the
plain HTTP `404 Not found` response or the WebSocket `4404` close where the
policy rejects the request. These cases catch authority misclassification and
non-composing fail-closed rules. A defect appears as an inner-app call on a
rejected authority, an exposed disabled public surface, or a response that
does not match the defined HTTP or WebSocket outcome.

This suite catches an exposed application route on a dedicated Peer authority.
The defect appears as an inner-app call or non-404 response. It also catches a
hidden valid Peer route through a missing inner-app call or unexpected `404`.

### 13.3 Share regression

`tests/test_share_host_gate.py` must retain coverage for Share pages, assets,
the root redirect, favicon, and Share WebSocket. It must also cover Share
blocking Peer and the working application. A bracketed IPv6 `Host` case must
prove that the canonical Share hostname selects the preserved Share boundary.

This coverage catches a changed Share boundary. A defect appears as a missing
Share response or an inner-app call for a forbidden path.

### 13.4 Peer migration boundary

No test migrates or repairs `peerBaseUrl`. A regression in
`tests/test_public_origin.py` must confirm that settings reads leave that value
unchanged. This catches normalization, repair, or conversion during a settings
read. The defect appears as a changed stored value after a settings read. The
other forbidden compatibility mechanisms in section 1.1 remain
implementation-review constraints. This read-preservation test does not claim
to prove their absence.

Tests do not create an upgrade matrix for earlier Peer development schemas,
frontend payloads, backend APIs, or wire protocols. An unexpected
development-only Peer row can produce an explicit migration failure.
