# Peer Inbox Filtering Design

**Date:** 2026-08-13
**Status:** Validated in conversation

## 1. Scope

Improve the owner-facing peer inbox in `PeerInboxDialog.vue`.

This change:

- Renames the pending inbound section to **Received messages awaiting review**.
- Adds a peer selector and text filter directly below the dialog header.
- Searches message titles and complete message text.
- Hides empty Received and History sections.
- Shows one inbox-level empty result message when neither section has a row.

Peer relationship requests remain independent. They are not messages and the filters do not affect them.

## 2. Filter UI

The dialog body uses this order:

1. Peer selector and text filter.
2. Pending peer relationship requests, when present.
3. Received messages awaiting review, when present.
4. History, when present.
5. An inbox-level empty message when neither message section has a row.

The peer selector is a Web Awesome `wa-select`. It contains **All peers** and every established or broken peer that can own message history. Pending relationship requests are excluded.

The text control is a clearable `wa-input` with the placeholder **Filter messages…**.

The peer and text filters combine with AND. Filter state remains active when the user opens a message and returns to the inbox. A page reload resets it.

## 3. Search Semantics

The text filter matches the message title OR the complete message text.

It uses the shared session-filter grammar:

- A normal query uses case-insensitive subsequence matching.
- A query starting with `"` or `'` uses case-insensitive literal-substring matching.
- An optional matching closing quote is removed.
- The title and text are matched independently. A match cannot start in the title and finish in the body.

Attachment names, peer names, routing titles, recipient notes, and message identifiers are not text-search fields. The peer selector owns peer filtering.

## 4. Data Flow

Without an active filter, the dialog uses the live message summaries in the Pinia peer store. It sends no REST request.

With either filter active, the dialog calls the existing endpoint:

```text
GET /api/peer-messages/?peer_id=<peer-id>&q=<query>&limit=200
```

The backend applies the peer filter first. It then evaluates the shared text-filter grammar against the complete stored title and `payload.text`.

The search reads only the text member from the JSON payload. It does not send or deserialize attachment Base64 into the browser response. The response contains the existing peer-message summary shape only.

The endpoint keeps all matching pending inbound messages. It returns at most 200 matching History rows and reports whether more History rows exist.

The existing unfiltered endpoint and WebSocket snapshot behavior stay unchanged.

## 5. Frontend Request Lifecycle

Text input uses a trailing 300 ms debounce. Peer selection starts a request immediately.

Each request owns an `AbortController` and a generation. A new filter state, dialog closure, or component disposal aborts the old request. An obsolete response cannot replace current results.

While a filtered request is active, the dialog shows a compact **Searching…** progress state. It does not present stale rows as current results.

WebSocket message changes retrigger the active filtered search. Unfiltered lists continue to update directly from the store.

If the request fails, the dialog shows a compact danger callout. It keeps the filters available so the user can retry by changing either filter.

## 6. Sections and Empty States

The dialog partitions the active result source into:

- Received: inbound messages whose status is `pending`.
- History: every other message.

A section heading renders only when its partition contains at least one row.

When both partitions are empty:

- Active filter: **No messages match your filters.**
- No active filter: **No peer messages yet.**

When the backend truncates History, the dialog shows:

> Showing the first 200 results. Refine your filters to narrow the search.

Pending inbound messages are never truncated by this limit.

## 7. Backend Shape and Performance

The existing list endpoint accepts optional `peer_id`, `q`, and `limit` parameters. An unknown peer id produces an empty result, not an error.

The backend shares one filter-matching implementation with the session bulk-filter path. The JavaScript `matchQuery` behavior and Python behavior remain byte-for-byte equivalent for the supported grammar.

Search selects candidate ids, titles, and `payload.text`, then fetches only the matched model rows needed by the existing summary serializer. No schema change or migration is required.

The response adds:

```json
{
  "messages": [],
  "history_has_more": false
}
```

Existing consumers that read only `messages` remain compatible.

## 8. Verification

Backend tests cover:

- Title and complete-body matches.
- Content beyond the 300-character preview.
- Fuzzy and leading-quote literal matching.
- Peer-only filtering.
- Peer and text AND behavior.
- Pending partition preservation.
- The 200-row History cap and `history_has_more`.
- Summary responses excluding payload and attachment bytes.

Frontend tests cover pure partition, filter-state, and empty-state decisions. The repository has no Vue component harness, so it does not add one.

Final verification runs the focused backend tests, focused frontend tests, the full frontend Node suite, the full relevant backend suite, the Vite build, and `git diff --check`.

Manual verification checks filter placement, Web Awesome controls, section visibility, progress, empty messages, truncation notice, and filter persistence across inbox → review → inbox navigation.

## 9. Out of Scope

- Thread grouping.
- Inbox row cardinality changes.
- Search over attachments or metadata.
- A search index or database migration.
- Pagination or infinite scrolling.
- Changes to the review dialog, delivery gate, toast, or peer wire contract.
