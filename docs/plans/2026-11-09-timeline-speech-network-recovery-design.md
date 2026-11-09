# Timeline speech network recovery

## Approved behavior

The user enables automatic timeline speech once. Network loss does not disable that intent.
An outage shorter than 120 seconds catches up eligible missed messages in order without replaying started readings.
An outage of 120 seconds or longer skips the outage backlog and resumes from successful reconnection time.
The threshold is a product policy, independent of the server session retention timeout.

Android keeps its foreground service and notification while reconnecting. Expired server sessions are replaced automatically.
After a long outage, one announcement uses the Android installation language and existing configured TTS before fresh timeline speech:
"Connection restored. Messages received during the interruption will not be read. Automatic reading resumes."
Stop and pause remain authoritative, including during disconnection. Fatal authentication and invalid scope errors require intervention.
This does not promise recovery after Android kills the process.

Web reconnects automatically while the page remains visible. Long outages show localized feedback and skip stale backlog.
When the page becomes hidden or leaves the foreground, automatic speech suspends. Returning to the visible page requires an explicit Resume action.
The explicit action primes browser audio. Short suspension can catch up; long suspension discards stale backlog.
No queued callback or response may play stale speech after a scope change, pause, stop, or reconnect reset.

## Approach

Preserve listening intent on the native controller, independently of ephemeral backend sessions.
Use existing native transport and configured speech pipeline for recovery and announcements.
Preserve a web coordinator activation while suspended, with no background playback, and expose explicit resume through the timeline speech control.
Keep translation sources in the five existing catalogs. Regenerate Android resources after integration.

## Constraints

Stay on the shared checkout. Preserve unrelated changes. Do not restart servers, install packages, migrate databases, commit, or build a new APK without a request.
Keep APK naming changes already in progress. Do not add one-time artifact cleanup to the build script.
All application-owned UI text uses i18n. English code and docs only.
