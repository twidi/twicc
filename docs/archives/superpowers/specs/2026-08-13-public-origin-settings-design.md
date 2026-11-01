# Public Origin Settings Design

## Scope

TwiCC has three synced settings that identify public origins:

- `publicBaseUrl`, shown as **External address**;
- `shareBaseUrl`, shown as **Share host**;
- `peerBaseUrl`, shown as **Your address** in the Peer settings.

This design gives `publicBaseUrl`, `shareBaseUrl`, and `peerBaseUrl` one input,
normalization, and validation contract. Legacy migration applies only to the
deployed External and Share settings. It does not add the planned peer-only
host routing.

The setting names do not change. Existing code and persisted settings continue
to use the `*BaseUrl` names.

As part of this change, every user-facing **External URL** label becomes
**External address**. This includes the application, CLI help, help pages, and
tips. Internal identifiers such as `publicBaseUrl`, help keys, component names,
and function names do not change.

## Product meaning

Each non-empty value identifies an HTTP origin. It contains a scheme, a
hostname, and an optional port. It does not contain credentials, a non-root
path, a query, or a fragment.

An empty value keeps its current setting-specific meaning:

- `publicBaseUrl`: omit public links;
- `shareBaseUrl`: disable sharing;
- `peerBaseUrl`: disable peer messaging.

## Current behavior

The settings currently have three different contracts:

| Setting | UI input | Scheme-less value today |
|---|---|---|
| `publicBaseUrl` | Any string | Stored unchanged; generated links are relative and usually broken |
| `shareBaseUrl` | Bare hostname or URL | Stored bare; share URL builders add `https://` |
| `peerBaseUrl` | Absolute HTTP(S) URL | Rejected by the UI; a CLI-written value later fails in `httpx` |

The generic settings CLI accepts any string for `publicBaseUrl`,
`shareBaseUrl`, and `peerBaseUrl`. Backend settings writes do not validate
them.

## Common input contract

The common normalizer accepts an empty string. For a non-empty input, it:

1. trims the existing normative ASCII whitespace set: TAB, LF, VT, FF, CR, and
   SPACE;
2. preserves an explicit `http://` or `https://` scheme;
3. rejects every other explicit scheme;
4. infers a scheme when none is present;
5. requires a valid hostname and port;
6. rejects username or password credentials;
7. accepts only an empty path or `/`;
8. rejects a query or fragment;
9. serializes one canonical origin without a trailing slash.

Canonical output uses a lower-case scheme and hostname. It removes an explicit
default port (`:80` for HTTP and `:443` for HTTPS). It preserves a valid
non-default port and brackets IPv6 hostnames.

Examples:

| Input | Stored value |
|---|---|
| `https://TwiCC.Example.com/` | `https://twicc.example.com` |
| `http://localhost:3501` | `http://localhost:3501` |
| `share.example.com` | `https://share.example.com` |
| `localhost:3501` | `http://localhost:3501` |
| `devbox:3501` | `http://devbox:3501` |

The normalizer rejects `ftp://example.com`, credentials, invalid ports,
non-root paths, queries, fragments, protocol-relative inputs, and values
without a hostname.

## Scheme inference

Scheme inference follows the existing Browser address normalizer:

- use HTTP for `localhost`, IPv4 literals, `[::1]`, `*.local`, `*.test`,
  `*.localhost`, and a dotless single-label hostname with an explicit port;
- use HTTPS for every other scheme-less hostname.

An explicit scheme is authoritative. TwiCC never upgrades an explicit HTTP
origin to HTTPS.

This preserves the current working behavior of a bare public share hostname.
It also repairs scheme-less External addresses. New Peer writes use the common
normalizer directly.

## Shared implementation

The backend is authoritative. A pure Python module owns parsing,
normalization, validation, scheme inference, and origin metadata extraction.

A dependency-free JavaScript check provides a small set of immediate form
errors. It rejects fewer inputs than the Python check. It must never reject an
input that Python accepts. It can accept an input that Python later rejects.

The JavaScript check rejects only a non-string value, a protocol-relative
value, an unsupported explicit scheme, a missing apparent authority,
credentials, invalid raw authority characters, and a trailing colon without a
port. Invalid raw authority characters are non-ASCII data, control characters,
and percent signs. The check submits every other input unchanged after the
normative outer trim.

The JavaScript check does not validate DNS label details, an A-label verdict, a
port range, a path, a query, a fragment, or IPv6 canonical form. Browsers expose
UTS #46, which is not equivalent to IDNA2008. No browser exposes IDNA2008. The
JavaScript check does not use the browser URL parser for a validation verdict.

JavaScript has a separate stored-value consumer guard. Backend writes produce
canonical origins. The guard accepts only the recognizable shape of canonical
backend output. It does not normalize input. It treats every other stored value
as unset. An A-label can satisfy lexical canonical shape. The guard does not
decide its IDNA2008 validity.

The JSON fixture contains separate backend and frontend sections. Backend
sections cover valid, normalizable, local, public, IPv4, IPv6, port, A-label,
relationship, and invalid cases. Frontend sections cover the input subset and
the stored-value consumer guard.

The three Settings fields use the same JavaScript helper. Setting-specific UI
rules remain separate. For example, the Share address must still differ from
the working app hostname, and an HTTP Peer address can still show its existing
warning.

## Backend write contract

`settings_mutation.py` normalizes every changed origin-setting value before it
writes the merged settings. The normalized value is included in the existing
settings corrections broadcast, so every connected client converges on the
canonical value.

An invalid changed value rejects the settings update atomically with a field
error. The WebSocket and CLI surfaces expose the same error.

Settings clients can send a full snapshot rather than a narrow patch. An
unchanged invalid legacy External or Share value must not block an unrelated
settings update. Only a newly supplied or changed invalid value is rejected.

## Legacy migration

The first settings read applies a one-time, idempotent migration to
`publicBaseUrl` and `shareBaseUrl`. Empty values stay empty.

The migration canonicalizes values that it can repair deterministically:

- add the inferred scheme to a bare hostname;
- normalize case and default ports;
- remove surrounding whitespace and a root trailing slash;
- reduce a value with a path, query, or fragment to its HTTP origin.

Examples:

| Stored before migration | Stored after migration |
|---|---|
| `share.example.com` | `https://share.example.com` |
| `twicc.example.com` | `https://twicc.example.com` |
| `localhost:3501` | `http://localhost:3501` |
| `https://example.com/base?x=1#part` | `https://example.com` |

The Share URL produced from a bare hostname does not change. External links
become absolute.

The migration does not guess when repair is unsafe. It retains an existing
value with an unsupported scheme, credentials, an invalid port, or no
hostname. It logs only the affected setting key, never the value.

The Settings UI keeps such a retained External or Share value visible and shows
its validation error. Backend non-routing consumers treat every parser-invalid
value as unset. Frontend URL consumers treat a value without recognizable
canonical shape as unset. The lexical frontend guard does not detect an
IDNA2008-invalid A-label. `PublicOriginGate` follows section 11 of the Peer
Origin Routing design. It can recognize an invalid hostname or authority only
to disable or quarantine a public surface.

### No Peer migration or backward compatibility

The Peer System is new and has not shipped. `peerBaseUrl` therefore has no
deployed values and no legacy contract.

Settings reads never migrate `peerBaseUrl`. The implementation does not add
legacy parsing, compatibility branches, or migration tests for old Peer
formats. New Peer values use the same write-time validation as the other public
origins. Development instances already use valid values and need no migration.

## Runtime consumers

Backend runtime consumers use the authoritative parser instead of checking
only whether the stored string is non-empty. Frontend URL consumers use the
canonical stored-value guard from the Shared implementation section.

- External notifications omit links for an invalid `publicBaseUrl`.
- The Browser companion snippet falls back to the current origin when the
  stored External value does not have recognizable canonical shape.
- Frontend Share URL builders treat a stored Share value without recognizable
  canonical shape as unset. The backend share host gate uses Python.
- Share creation guards treat an invalid `shareBaseUrl` as unset.
- Peer messaging treats an invalid `peerBaseUrl` as unset.
- `PublicOriginGate` can recognize an invalid hostname or authority only to
  fail closed, as section 11 of the Peer Origin Routing design defines.
- The peer display-name fallback uses the parsed peer hostname.

Migrated External and Share reads, plus every new write, produce canonical
origins. Frontend URL builders do not retain a second scheme-less parser.

## Error behavior

The UI uses one stable message for structural errors:

> Enter a hostname or an HTTP(S) origin without a path, query, or fragment.

It can use a more specific message for an unsupported scheme or credentials
when the common helper provides that error code.

No invalid update partially changes settings. No backend consumer treats an
invalid retained value as a valid origin. `PublicOriginGate` can recognize its
hostname or authority only to fail closed under section 11 of the Peer Origin
Routing design. Frontend consumers do not use a stored value without
recognizable canonical shape.

## Tests

The implementation requires:

- Python fixture tests for every backend normalization, A-label, authority, and
  relationship case;
- JavaScript fixture tests for the input subset and stored-value consumer guard;
- a test that each JavaScript rejection case is also a Python rejection;
- an explicit JavaScript assertion that names and excludes all backend-only
  fixture sections;
- backend settings-mutation tests for corrections and atomic rejection;
- migration tests for deployed External and Share values, including bare
  values, explicit schemes, suffix removal, idempotence, and retained unsafe
  values;
- a scope regression test that settings reads never mutate `peerBaseUrl`;
- frontend Settings tests for `publicBaseUrl`, `shareBaseUrl`, and
  `peerBaseUrl`;
- consumer tests for notifications, companion snippets, sharing, and peer
  feature gating with invalid stored values;
- regression tests that an explicit HTTP origin remains HTTP;
- a regression test that migration of a bare Share address still produces the
  same HTTPS share link.

The tests do not compare Python and JavaScript error codes, error order, or
canonical output. JavaScript can defer any backend rejection until Apply.

## Deferred work

This design does not restrict a dedicated Peer origin to `/peer/`. That host
routing change follows after the three settings share this validated origin
contract.
