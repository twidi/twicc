# Android menu placement

## Approved behavior

The Android app shows the TwiCC logo before Home when the rail is visible. Without a visible rail, it shows the same logo at the top left and opens the same Web menu. Login, connecting states, and routes without a rail retain access to instance switching and app commands. Ordinary browsers show no Android menu. When the page or bridge is unavailable, a fixed native robot button opens the native fallback menu. No drag gesture or saved button position remains. Native instance management, saved URLs, keyboard behavior, and both engines stay supported.

## Design

Mount one AndroidAppMenu at App level, outside authentication/readiness gates. SidebarRail provides a lightweight registered anchor before Home. Vue Teleport moves the menu into the visible anchor. Otherwise its wrapper uses fixed top-left positioning. One bridge client, one menu state, one visibility lease, and one confirmation dialog survive route changes. The confirmation dialog stays outside Teleport to preserve its native modal state. Anchor registration handles replacement and cleanup. Resize/ancestor-style observers detect collapsed or hidden rails; the existing heartbeat also refreshes placement. Android rails scroll when their controls exceed the available height. No iframe moves through this Teleport.

Replace the native movable ellipsis with an ImageButton using the existing robot asset. Use the root's system insets and a fixed top-left hit area. Keep all native fallback commands and lease behavior. Build version 0.4.1/code 10 with the existing package/signature.

## Tasks

- [x] Write failing tests for registered anchors, hidden/removed rails, replacement cleanup, and app-wide mounting outside auth gates.
- [x] Add anchor registration/placement and move the single Web menu to App. Preserve unrelated App edits.
- [x] Replace the native floating control with the fixed robot and update current-state documentation.
- [x] Run frontend tests/build, policy tests, both Android lint/builds, and one independent review.
- [x] Verify both engines on the emulator: rail/no-rail/hidden rail/menu actions/native fallback. Deliver signed ARM64 APKs.

## Validation

All 58 focused frontend tests pass. The production frontend build, 14 policy tests, and both Android lint/build tasks pass. The full frontend suite passes 4,471 of 4,472 tests. The remaining failure is the existing ten-versus-eleven rail-control assertion in `frontend/src/styles/rail-button.test.js`; that test and its control definitions are unchanged from HEAD.

One independent review identifies two placement defects: moving an open dialog loses modality, and the extra Android control can overflow the rail. Both receive fixes and emulator checks. WebView and Gecko retain one button across rail removal, rail hiding, and the connecting override. Fixed positioning opens the same menu. The confirmation dialog preserves modality after movement. Constrained rails scroll to the final control. An unreachable Gecko instance exposes the fixed native robot and its working fallback menu.

ARM64 APKs use version 0.4.1/code 10, application ID `com.twidi.twicc`, and the existing signing certificate. No physical-device validation or commit is included in this task.

## Review focus

Only one menu and lease exist across route changes. Removed or hidden anchors cannot strand the menu. Ordinary browsers have no extra rail gap. Native fallback stays reachable when the document cannot load. Shared App edits remain intact.
