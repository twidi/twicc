# Android notification validation

Implementation: [plan](2026-11-06-android-notifications-plan.md).
Current user documentation: [Android app](../../android/README.md#android-notifications).

## Scope

The production feature replaces Android browser notifications for four existing
families: finished work, questions/approvals, extra usage, and peer events.
Native registration, encrypted push reception, and renewal do not need an open
page. Ordinary browser notifications and Apprise targets retain their behavior.

No backend restart, package installation, or new physical-phone push test was
performed by the agent. Tests use the previously installed isolated prototype
Python environment for the real Web Push dependency. The updated project lock
includes `pywebpush==2.5.0`; the normal `uv run` backend launch synchronizes it.

## Automated evidence

- Backend: 95 targeted tests pass. These cover all four event families without
  Apprise targets, preferences, password-bound grants, native endpoint renewal,
  browser/Android presence, real encryption/decryption, TTL/urgency, stale 410
  retirement, hidden/muted/resync behavior, and existing broadcast behavior.
- Frontend: the 4535-test suite passes. One additional enrollment-retry test
  passes in the final five-test helper run. The production frontend build passes.
- Native: policy, Gecko and WebView host suites pass, 64 tests each.
- Native: both engine release lint checks and instrumentation compilation pass.
- Emulator: actual notification taps preserve the live document, route to a new
  session, retain an already-open session, switch instances and work after the
  browser closes. Native failed registration persists a retry; stale upload
  completion cannot erase newer changes. A permission change preserves pending
  endpoint work. Both final emulator tests pass, including this regression.
- Independent final review finds two Important issues. Both receive regression
  tests that fail before their fixes: native FCM registration retry and peer
  notification destinations during login. No Critical or Minor findings remain.

Physical acceptance of the full feature remains pending. The earlier transport
probe succeeds on the user's phone; it does not prove the final preference and
presence pipeline. No new notification is sent without the user's readiness.

## Decisions and limits

- A fresh authenticated enrollment temporarily clears its old server subscription.
  Native Android uploads its retained endpoint with the new grant. A lost
  enrollment response therefore cannot leave an active, unmanageable grant.
  The cost is a brief enrollment gap until the native job finishes.
- The existing browser profiles remain distinct between engine variants. Native
  push infrastructure is shared; opening another engine profile applies that
  profile's local event choices. A variant switch can therefore change choices.
- Old APKs without the `androidPush` capability require an APK update for native
  notifications. The backend cannot add a receiver to an old binary.
- Interactive tunnel challenges, Android force-stop, missing Google Play
  Services, and OEM/network restrictions remain platform limits. The finite
  renewal job does not bypass them. Physical acceptance must verify the phone.
- One pending notification tap is retained until its matching acknowledgement.
  A newer tap replaces an older pending destination. No tap history is stored.
- No deferred minor review findings exist.

## Phone acceptance

1. Restart the backend with its normal `uv run` launch. Install the new APK.
2. Open an instance and enable the desired **Android notification** switches.
   Allow Android notification permission when asked.
3. Keep ordinary TwiCC browsers inactive for their presence grace window:
   five minutes for desktop, thirty seconds for mobile browsers.
4. Confirm finished-work and question/approval notifications while Android is
   backgrounded and the screen is locked. Confirm the correct instance/session
   opens and the live document does not reload unnecessarily.
5. Confirm active desktop browser activity suppresses Android notifications.
6. Confirm peer messages open their message and pairing requests open the manager,
   including after a login is required. Confirm extra-usage follows its master switch.
7. Confirm denied OS permission keeps all four choices available and settings
   provide access to Android notification settings.

## Deliverable

- APK: Gecko ARM64 release **0.5.10**, version code **21**.
- Application ID: `com.twidi.twicc`; non-debuggable.
- Existing installation signing certificate preserved.
- SHA-256: `7e6894d4ebe9308642bca3266f3c45d745e65b56b6ac603c5a0409f5a2852489`.
- APK size: 211,971,073 bytes.
- Final Gradle run: both release host suites, policy tests, both release lint
  tasks, both instrumentation compilations and Gecko release assembly pass.
- Only Gecko ARM64 release APKs remain in build output and user artifacts.
