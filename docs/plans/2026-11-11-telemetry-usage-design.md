# Daily feature telemetry

## Approved scope

Add four daily buckets: manually generated summaries, manual TTS readings,
timeline TTS readings, and explicit timeline TTS activations. Count successful
new summary generations by initiating origin, and readings at first playback.
Exclude tests, technical announcements, cache-only summary retrieval, and
reconnect/recovery activations. Include manually requested selections as manual
use; the counters describe user actions, not unique source messages.

Snapshot configuration at send time: usable TTS kind and verbatim configured
OpenRouter model, effective summary engine (Haiku/Luna/OpenRouter/none), title
model preference, global default layout kind, and preset count per provider.
Keep existing automatic-title flags and layout counts unchanged.

Count daily client installations/profiles using a random local client key.
Serialize creation with an origin-wide Web Lock when available. Do not claim
physical-device identity. Report browser family and mobile or
desktop; Android app version and Gecko/WebView; actual UI languages. Do not
send client keys, user agents, instance addresses, or hardware information to
the collector. Multiple tabs/reconnects share one local profile key.

## Architecture and tradeoffs

Extend telemetry.json instead of adding DB tables or an event log. Four local
counters saturate at 101; payload buckets are 0, 1, 2-5, 6-20, 21-50, 51-100,
101+. Keep normal daily delivery, acknowledgement/opt-out gates, UTC dates,
30-complete-day catch-up cap, last-payload inspection, and no historical backfill.
Keep today's partial entry in addition to those 30 complete days. Prune older
calendar dates on accumulation and periodic ticks, including while disabled.
Read config
through existing accessors. Profile identity is only for local daily dedup;
persist a daily salted digest instead of the client key. Aggregate equal client
descriptions with counts in the outgoing payload.

Frontend reports Web playback/activation and active-client metadata over the
authenticated WebSocket. Native playback/activation and completed summaries
count on the backend. Prefer existing native protocol deduplication to another
event ledger. Do not queue browser activity while disconnected or disabled.
New metrics start with the next complete day; omit them from older day blocks.

The frontend receives an opaque collection epoch through its authenticated
WebSocket. Opt-out/re-enable and instance-ID reset invalidate previous epochs.
Explicit native Listen commands carry the epoch through the Android bridge;
recovery and notification Resume commands omit it. The backend validates it
before counting activations. Older Android clients omit native activation
contributions until upgraded; their actual playback can still be counted.
No Android capability or native protocol version changes are required.

On secure origins (HTTPS or localhost), Web Locks prevents simultaneous new
tabs from creating separate profile keys. Without Web Locks, the fallback uses
localStorage without a cross-tab lock. The first simultaneous tab creations can
temporarily overcount one profile. This includes HTTP LAN origins, not only old
browsers. Unavailable browser storage also limits identity to the current page.
The exact count describes the distinct keys observed under these limitations;
it is not an unconditional exact count of browser profiles or hardware.

Alternatives rejected: new database tables (unnecessary), counting HTTP audio
generation as playback (incorrect), counting summaries from saved rows (origin
and temporary results are missing), and a model allowlist (explicitly rejected).

## Privacy and review focus

No content scans, text, audio, paths, titles, or client identifiers in payloads.
The configured OpenRouter model is an explicit documented exception to the
closed-enum rule. All other new strings are normalized metadata.
Review shared summary consumers, native replay/reconciliation, opt-out races,
cross-midnight actions, legacy day blocks, and multiple browser tabs.
