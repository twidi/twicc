# Android menu inside the Web UI

## Approved behavior

Show a TwiCC robot button before Home in SidebarRail, only when a native Android bridge answers. Render the menu with Vue and Web Awesome. Include app version/engine, every saved instance with the active instance marked, Manage instances, Reload page, Force refresh (keep login), Server home, and Open in browser. Keep the instance manager native. Switching instances closes the current engine session, saves its safe URL, and restores the target instance's persisted URL. Selecting the active instance does nothing.

Keep the floating native menu as a fallback whenever the Web menu is absent, hidden, unsupported, or stops responding. This covers login, failed loads, older servers, and a collapsed rail. Preserve the existing keyboard policy and persistent per-instance/per-engine storage. Do not add background execution or migrate the native instance manager into Vue.

## Architecture and approaches

Use one shared request/response protocol and native command dispatcher. Vue owns presentation and force-refresh confirmation. Android owns saved instances, navigation, engine resets, and external intents. WebView uses the origin-scoped AndroidX WebMessage listener. Gecko uses a built-in content-script extension and a session-specific native message delegate. The frontend uses a single client for both transports. Ordinary browsers receive no native response and show no Android menu.

A custom URL scheme could carry commands but would mix control messages with navigation and need a separate response path. DOM injection of menu markup would duplicate Vue presentation and make updates brittle. The approved bridge approach keeps the existing engine flavor boundaries and the app's own styles.

## Protocol and lifecycle

Requests carry protocol 1, an opaque request ID, and one allowlisted command. Commands: getState, menuVisibility, switchInstance, manageInstances, reload, forceRefresh, serverHome, openInBrowser. Only switchInstance accepts an instance UUID; native code resolves it from InstanceStore. No arbitrary URL, JavaScript, file path, or method name is dispatched. Responses repeat the request ID and protocol and return either state/success or an error.

Native code accepts only the configured HTTPS origin in the top-level frame of the live session. Embedded artifacts, other iframes, and closed sessions cannot invoke commands. Navigation/session replacement invalidates Web-menu readiness. The Web menu reports visibility every two seconds while mounted; readiness expires after six seconds. Native UI remains usable if JavaScript stops. Visibility reports include an owner ID so unmounting an old rail cannot revoke a replacement rail's lease.

The state snapshot contains app version, engine name, active instance UUID, and saved instance UUID/name/address. It contains no passwords or cookies. A switch saves the old URL before selecting and opening the target Activity, then finishes the old Activity. The old Activity retains its original instance identity during lifecycle callbacks.

## Validation and limits

Test protocol correlation, unavailable/late bridges, command rejection, main-frame/origin restrictions, fallback leases, and rendered menu ordering/active state. Build/lint both APK flavors and run the existing rail tests. Check both engines on an emulator: handshake, hidden/visible menu fallback, switching/restoring independent URLs across process restarts, force refresh, iframe rejection, and older-page fallback. Physical-phone fluidity and real streaming TTS remain device checks. The frontend must be deployed to each server for that server to render the Web menu.
