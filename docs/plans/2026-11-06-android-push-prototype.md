# Android push delivery prototype

Historical transport research. The production feature is documented in
[Android notifications](../../android/README.md#android-notifications).

This is a transport spike, not the notification feature. It tests Web Push/VAPID
sent directly to FCM and received by UnifiedPush in the shared Android code.
It has no Firebase project, TwiCC relay, permanent foreground service, or page
JavaScript requirement after enrollment. Google Play Services are required.

The server exposes an authenticated enrollment endpoint. The Android app menu
offers an explicit test enrollment action. Page fetch uses the existing login;
the native callback persists the endpoint and Web Push keys until enrollment
finishes. Each saved instance has its own registration. Notification taps open
that saved instance, using its saved URL. Removed instances cannot receive or
open test notifications.

Taps target BrowserActivity directly with CLEAR_TOP and SINGLE_TOP. A live
browser for the same instance stays open. An optional validated base session
path travels with the test. The existing menu heartbeat returns one pending
navigation target; Vue Router handles it and acknowledges its ID. The native
side then removes it. A newer tap replaces an older pending target. A cold
browser loads the target path directly. Gecko and WebView need no separate
navigation transport. An already open session keeps its tab and workspace.

A standalone uv script initializes VAPID keys and sends a small encrypted test
message. Script dependencies are isolated from the backend environment. Private
keys and subscriptions live below the explicitly selected data directory.
No migrations or automatic backend restart are required by the implementation.
The operator restarts the backend to load the new endpoint.

The enrollment page must remain open until server enrollment completes. Endpoint
rotation requires enrollment again in this spike. Event routing, notification
preferences, browser presence, background enrollment refresh, and alternative
distributors are deferred. They belong to the full feature after phone validation.

Accept delivery with the app visible, backgrounded, screen off, in Doze, and
after process destruction without force-stop. Record sent and received times.
A provider's successful HTTP response only confirms acceptance, not delivery.
Force-stop, blocked notifications, and missing network are separate conditions.

References:
- https://unifiedpush.org/kdoc/connector/
- https://unifiedpush.org/kdoc/embedded_fcm_distributor/
- https://github.com/web-push-libs/pywebpush
