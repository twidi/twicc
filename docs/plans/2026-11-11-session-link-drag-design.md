# Session link: drag a sidebar session into the composer — spec

Date: 2026-11-11. Status: spec, not implemented.

> **Superseded (2026-11-11).** The owner chose a simpler implementation, modelled on the layout tab drag:
> pointer events for mouse and touch, a long press then a sideways move on touch (rows wear
> `touch-action: pan-y`, so a vertical move stays a native scroll), the existing chat drop overlay, and
> the row menu entry. No HTML5 drag, no cross-window support, no feasibility spike. The code
> (`frontend/src/composables/useSessionLinkDrag.js`, `frontend/src/utils/sessionLink.js`) is the reference.

This document describes the behaviour and the technical constraints that decide its feasibility.
It does not prescribe the code structure. The implementation plan comes after.

## Intent

The user wants to point an agent at another session: "look at this session, we did X there".
Today they must copy the URL by hand. The feature inserts a **Markdown link to a session** into the
composer of the open session, in one gesture.

Three entry points:

1. **Desktop drag and drop:** drag a sidebar session row, drop it on the chat of the open session.
2. **Mobile drag and drop:** long-press a sidebar session row, then move the finger to the chat.
3. **Row menu entry** (desktop and mobile): "Insert link in message" in the sidebar row menu.

## Goals

- One gesture inserts the link in the composer.
- Same result on desktop and mobile.
- A ghost of the session follows the pointer or the finger during the drag.
- The drop zone is highlighted with the existing chat drop overlay.
- Existing behaviour stays intact, except the intentional changes listed below: click to open, long-press
  multi-select, sidebar scroll, spring-loaded switching, file and text drops, native URL drag (to the browser tab
  bar, the address bar, other apps).

## Intentional behaviour changes

- A sidebar row dropped on the chat inserts the Markdown link instead of the absolute URL. A row dropped on its own
  session's chat inserts nothing (today it inserts the URL).
- When the touch drag is possible (all conditions of "When the drag is possible"), long-press multi-select starts
  at the release instead of at 500 ms.
- A long-press followed by a move, when the drag is possible, starts a drag: it no longer leaves multi-select
  entered, and the list no longer scrolls (today the list scrolls with that move).
- During a session-link drag, the source row no longer opens by spring-loaded switching.
- A touch on a sidebar row no longer starts the browser's native link drag (see C8). A mouse keeps it, on every
  device.
- In the narrow (drawer) layout, a pointer session-link drag with a link target session closes the drawer (see
  "Narrow layout (drawer) with a pointer"). Today a row drag leaves the drawer as it is.

## Non-goals

- Several sessions in one drag (see "Multi-select").
- Drag sources other than the main sidebar session list: rail recents, dialogs and pickers (for example the
  session picker of the peer message dialog), the timeline.
- Session links between two TwiCC instances (different origins, for example a worktree instance).
- A new link syntax, a link chip, or a special renderer for session links.
- Haptic feedback that needs a change to the Android app.
- Any backend change.

## Anti-goals (failure even if it "works")

- On mobile, a sidebar scroll starts a drag, or the sidebar list scrolls along with a drag.
- During a touch drag, the page reloads (pull-to-refresh), or something else scrolls or moves: an ancestor, the
  page, the chat, the browser toolbar. (A desktop native drag keeps today's edge auto-scroll.)
- Long-press multi-select changes when no drop target exists.
- A session-link drag breaks a later file or text drop.
- A session-link drag changes the URL or the text that other drop targets receive (tab bar, other apps).
- The link depends on the sidebar mode (workspace, all projects) and breaks when read later.

## Definitions

- **Link target session:** the session open in the main pane, when its chat shows a composer.
  The chat hides the composer in several cases: launched ephemeral session, unavailable session, stale session,
  disabled provider, missing project directory. In all these cases there is no link target session.
  A subagent view of a session still has that session's composer in its Chat tab, so it counts.
- **Chat visible:** the center pane of the link target session shows the Chat tab, no maximized dock hides
  the center pane, and no overlay (peek) dock is open over it. An open overlay dock means the chat is not visible. A docked tool tab can be the routed tab while the center still shows Chat: that counts.
  The mobile drawer over the chat does not count as hiding it (the drawer closes when the drag starts).
- **Linkable session:** a session that is not a draft and not ephemeral. These have no persistent transcript.
- **Self-link:** the linked session is the session that receives the link. It is always refused.

## The link

- Text: `[<session title>](/project/<project_id>/session/<session_id>)`.
- The path is always the canonical session route, whatever the current sidebar mode or route.
- The label is the displayed session title. Line breaks become spaces. Characters that change inline Markdown
  rendering inside link text are escaped with a backslash: `` \ [ ] ` * < > ~ & `` always, and `_` only where it
  can open or close emphasis (not inside a word: `snake_case` stays as it is). Other punctuation stays as it is,
  so the agent reads a clean title.
- TwiCC already renders such a link as a clickable SPA link in every message, user messages included.
  A click opens the session.
- The Markdown renderer applies typographic replacements (`typographer: true` in `frontend/src/utils/markdown.js`):
  `(c)`, `--`, `...` and straight quotes in the label can render as typographic characters. This is accepted;
  the label is not escaped against it.

## Insertion (all entry points)

- The link goes at the composer caret: the last caret position of the composer, or the end of the draft if the
  composer never had a caret. A text selection in the composer is replaced by the link (the existing insertion
  behaviour).
- One space is added before the link if it would touch a character that is not whitespace (a line break counts as
  whitespace), and one after for the same reason.
- The composer footer is an accordion: composer, hybrid terminal, pending-request form, goal panel; at most one
  is open. The link insertion never displaces another panel:
  - composer open: insert at the caret and focus the composer;
  - all panels minimized: open the composer, insert, focus;
  - "focus" follows the input of the gesture. After a touch gesture (touch drag, menu entry chosen by touch), the
    composer does not take the focus, so the virtual keyboard does not pop up and resize the view. After a mouse
    gesture, it takes the focus. When the input is not known, the existing device-class rule of the composer
    applies (no focus on devices without hover). The user taps the composer to go on writing;
  - another panel open (terminal, pending request, goal): append the link at the end of the draft, not at the
    caret, without opening the composer, exactly like the existing text drop does on a collapsed composer. The
    spacing rule applies there too.
- The existing text drop keeps its behaviour.
- A self-link is refused in every path, including the indirect ones (spring-loaded switching, menu).
  Nothing is inserted, and no message is shown.
- If the receiving session has no composer when the drop or the action happens (for example a session opened by
  spring-loaded switching is stale), nothing is inserted.

## Desktop drag and drop

### Start

- Any linkable session row of the main sidebar list can be dragged. Multi-select must be inactive.
- A ghost follows the pointer: project color dot, session title on one line (ellipsis), project name on a second,
  smaller line. It looks like a raised sidebar row.
- The drag keeps today's URL and text data unchanged: the row's absolute URL, which keeps the user's current frame
  (project, workspace, all projects). The tab bar, the address bar and other applications receive the same URL and
  text items as today, plus one extra TwiCC-private item that they ignore.
- The session-link information travels in a separate, TwiCC-specific drag data type. The type itself identifies
  the instance (origin), so a chat zone knows during the hover, before the drop, whether the drag is a session
  link of its own instance. Only chat zones of the same origin treat it as a session link.
- A row that is not linkable, or a drag in multi-select mode, keeps today's native URL drag only.

### Narrow layout (drawer) with a pointer

- In a desktop window narrower than the mobile breakpoint, the sidebar is a drawer, and its backdrop covers the
  chat. A pointer session-link drag (linkable row, multi-select inactive, a link target session exists and is not
  this session) follows the touch rule: the drawer closes when the drag starts, and it reopens if the drag ends
  without inserting the link (cancel, drop elsewhere, refused drop). The reopen also happens on the fallback
  signals of C9, so a lost drag end never leaves the drawer closed. After an insertion, it stays closed.
- Other pointer drags from a row keep the drawer as it is:
  - native URL drags (multi-select, non-linkable row): today's behaviour;
  - session-link drags without a link target session, or of the open session's own row: they stay session-link
    drags (ghost, session-link data, spring-loading to another row inserts the link there); only the drawer
    behaviour differs.
- When the drag closed the drawer, the sidebar rows are not reachable: row spring-loaded switching ("link A into
  B") is not available for that drag. Chat tab spring-loading stays available.

### Drop on the chat

- This replaces an existing behaviour. Today a row dropped on the chat is a text drop: it inserts the absolute URL.
  With this feature, a same-instance row drop inserts the Markdown link, and a self-drop inserts nothing.
- When the pointer enters the chat of the link target session, the existing drop overlay appears with a
  session-link variant: a link icon and "Drop to insert a link to “<title>”".
- A drop inserts the link (see "Insertion").
- Refused cases (self-link, no composer): no overlay, "not allowed" cursor, nothing inserted.
  A refused drag leaves the drop zone ready for the next drag (file, text or session).
- A drag from another browser window of the **same** instance also works. The overlay then shows a generic label
  ("Drop to insert a session link"): the browser hides the drag content before the drop, so the title and the
  session are not known yet. A self-link from another window is refused at the drop (nothing inserted, no message).
- A drag from another TwiCC instance (other origin) is not a session link. It behaves as today's text drop,
  with today's text overlay.
- The drop always uses the data carried by the drag itself. Information kept in the window about a drag in
  progress only serves the hover label, and only when it matches the current drag. Stale information never
  changes what is inserted.
- Artifact frames shown in the chat are not part of the desktop drop target: over a frame, the overlay disappears
  and a drop inserts nothing. This is today's behaviour for text drops, and it is kept.

### Spring-loaded switching

The existing spring-loaded behaviour stays active for session-link drags:

- Hovering the Chat tab for 1 s switches to the chat. A drop on the tab inserts the link.
- Hovering another sidebar row for 1 s opens that session. A drop on that row inserts the link into that
  session's composer. This gives "link session A into session B". Not available when the drag closed the drawer (see "Narrow layout").
- The source row itself never activates.
- A drop on a target that did not activate inserts nothing, as today for text drops:
  - a drop on the Chat tab before 1 s, or when the Chat tab is already shown;
  - a drop on a row before 1 s, or on the row of the session already open.

## Mobile drag and drop

### When the drag is possible

At the moment the long-press completes (500 ms), all of these must be true:

- the row is in the main sidebar list and the session is linkable;
- multi-select is inactive;
- a link target session exists, it is not this session, and its chat is visible. If the chat is not visible,
  the menu entry is the way to insert the link.

Otherwise the long-press behaves exactly as today: it enters multi-select at 500 ms.

### Gesture

| Phase | What the user does | What happens |
|---|---|---|
| Press | Finger down, no move | Nothing yet. A move > 10 px before 500 ms is a scroll, as today. If the list already scrolled during the press (the browser can start a pan before 10 px), the press never arms: it behaves exactly as today (multi-select at 500 ms unless the finger moved > 10 px). A release is a tap: it opens the session, as today. |
| Armed | 500 ms elapsed, drag possible | The row "lifts" (scale and shadow; under reduced motion, a background change only). Haptic feedback only where the platform already allows it (best effort). The sidebar list stops scrolling. |
| Armed → release | Finger up without move | Multi-select starts with this session, as today. Only the timing changes: at release instead of at 500 ms. |
| Armed → move | Finger moves > 10 px from its position at arming time | The drag starts. Multi-select does not start. |
| Dragging | Finger moves | The mobile drawer closes, so the chat is visible. A ghost (same look as desktop) follows the finger, about 24 px above it, so the finger does not hide it. The drop overlay (session-link variant) appears only when a release would count as a drop: the finger is over the chat and has moved at least 10 px over it. It disappears when the finger leaves the chat. The sidebar list does not scroll. |
| Drop | Finger up over the chat, after a real move over it | The link is inserted (see "Insertion"). The drawer stays closed. A release counts as a drop only if the finger moved at least 10 px while over the chat (after the drawer closed, in the narrow layout). Otherwise it is a cancel. This avoids an accidental insertion: in the narrow layout the finger is already over the chat when the drawer closes. |
| Cancel | Finger up elsewhere, finger up over the chat without a real move over it, a second finger touches the screen, the system cancels the touch, the open session stops being a link target, or its chat stops being visible | The ghost disappears. The drawer reopens if the drag closed it. Nothing is inserted. |

- During the armed and dragging phases, no click opens the session and no system context menu appears.
- A long-press on a row never starts the browser's own link drag, and the browser never cancels the gesture for
  that reason.
- The sidebar list may move by a few pixels between the start of the armed phase and the moment its scroll is
  frozen (see C5). More than that is a failure.
- The chat area counts as a drop target as a whole: the test is "is the finger inside the chat area". Artifact
  frames shown in the chat are inside it, so they count (this is the simplest rule on touch; desktop differs, see
  "Drop on the chat"). Toasts and tooltips over the composer do not block the drop.
- On a device with both a touchscreen and a pointer (touchscreen laptop), a touch on a row follows the touch
  gesture. The browser's native drag from that touch is suppressed, so the touch gesture always wins.
- The drawer only closes in the narrow (drawer) layout. A wide layout on a touch device (tablet, landscape phone)
  keeps the sidebar as it is.

### Not covered on mobile

- Spring-loaded switching, in every layout. In the narrow layout the drawer closes at drag start, so no row and
  no tab bar are reachable. In a wide touch layout they stay reachable, but hovering them has no effect: the
  only drop target of a touch drag is the chat of the link target session.

## Row menu entry

- Entry "Insert link in message", link icon, in the first group after "Rename" and "Share".
- Shown only in the main sidebar list, for a linkable session, when a link target session exists and is not
  this session. The chat does not need to be visible.
- Action: the link is inserted into the composer of the link target session (see "Insertion"). The chat is shown
  first: if another tab holds the chat's place, the view switches to the Chat tab; if an overlay dock is open over the
  chat, it closes; if a maximized dock hides the center pane, the maximized state ends. In the narrow layout, the drawer closes.
- The composer keeps the focus after the menu closes, when the insertion focuses it (pointer devices).
- The entry stays available in multi-select mode. It acts on one session and does not change the selection.

## Multi-select

- Multi-select active: no session-link drag. Desktop keeps today's native URL drag. Mobile keeps today's
  gesture: taps toggle, scroll works, no armed phase.
- Dragging several selected sessions is out of scope for now.

## Internationalization

New interface strings, in every locale (`frontend/src/i18n/locales/`, flat keys, next to the existing
`session.drop.*` and `session.list.*` keys):

- overlay label with the title: "Drop to insert a link to “{title}”";
- generic overlay label: "Drop to insert a session link";
- menu entry: "Insert link in message".

The link label is the session title: user data, not interface text.

## Accessibility

- The menu entry is the keyboard and screen-reader path. There is no keyboard drag.
- The ghost and the overlay are hidden from assistive technology.

## Technical constraints

These facts decide how the feature can work. The plan must respect them.

| # | Constraint | Consequence |
|---|---|---|
| C1 | Firefox Android does not start an HTML5 drag from touch input (Mozilla bug 1764177 is still open). Other engines may, but not in a way TwiCC can rely on. | Desktop uses the native HTML5 drag. Touch uses a custom gesture, in every engine. |
| C2 | The native drag keeps the existing drop zones working: chat overlay (`SessionItemsList.vue`), Chat tab and row spring-load (`composables/useDragHover.js`), and the sidebar → session hand-off (`pendingDropData` in `ProjectView.vue`, consumed by `SessionView.vue`). | Desktop reuses them. The session-link information must survive every hand-off, so self-link refusal, spacing and the insertion rules also apply there. |
| C3 | `App.vue` prevents default on every `dragover` and `drop` (`.app-container`). | The browser treats every drop as valid, and native text insertion into fields is cancelled everywhere in the app. The chat zone must refuse a self-link or no-composer drag itself, in all drag events, symmetrically, so its enter/leave counting stays correct for later drags. |
| C4 | A drag image snapshot is taken synchronously at drag start. `wa-*` components render their shadow DOM asynchronously. | The desktop ghost is plain DOM, rendered at that moment. Verify in Firefox first. |
| C5 | Browsers decide early in a touch block whether it can block scrolling. Firefox (APZ) uses the response to `touchstart` or to the **first** `touchmove`; a `preventDefault()` on a later `touchmove` is ignored. A finger at rest still sends small `touchmove` events before 500 ms. A non-passive `touchstart` or `touchmove` listener makes the browser wait for the main thread before every scroll starts, and TwiCC's main thread is busy while a session streams. The rows have passive `touchstart` and `touchmove` listeners today. | Cancelling `touchmove` events is not a usable means: it is ignored in Firefox after the first move, and making the listeners non-passive would delay every normal sidebar scroll. The row touch listeners stay passive, and the start of a normal sidebar scroll never waits for the main thread. The only means is to freeze the list scroll for the armed and dragging phases (the list stops being scrollable). Today the list stops scroll chaining with `overscroll-behavior: contain` (`VirtualScroller.vue`); a list that is no longer scrollable may hand the pan to an ancestor or to the page (pull-to-refresh). The freeze must not let the pan reach anything else. Blocking scroll from the first touch (the tab strip way, `SessionLayout.vue`: `touch-action: none` plus a hand-made pan) is rejected for the sidebar: it would replace native vertical scrolling and its inertia. |
| C6 | Pointer events end with `pointercancel` as soon as native panning starts. | The mobile gesture uses touch events. |
| C7 | The sidebar is a virtual scroller. A row component can be recycled for another session, or unmounted, during a gesture. A touch sequence keeps its original target node; once that node is detached, its events no longer bubble to `document` or `window`. | The drag state lives outside the row component, captured at drag start. The gesture is observed on the original target node itself. Row lifecycle hooks do not cancel a drag in progress. |
| C8 | Chromium on Android (the WebView flavor of the Android app) starts a native drag on a long-pressed link, and can cancel the touch sequence for it. Recent Gecko builds may do the same. The tab strip disables this with `-webkit-user-drag: none` (`SessionLayout.vue`), which has no effect in Gecko and applies to the mouse too. | In every engine, a touch on a sidebar row never starts a native drag, so the touch gesture is never cancelled. The means must work in Chromium and in Gecko, and must depend on the touch input, not on the device class: a mouse keeps its native drag on every device (tablet with a mouse included). |
| C9 | When the drag source node left the DOM during the drag, its `dragend` fires on the detached node and does not reach `document` listeners (all engines). | The shared drag state is also cleared by other signals (a drop, the next pointer activity). Stale state never blocks a later drag and never changes what a drop inserts. |
| C10 | Cached sessions (KeepAlive) keep their chat and composer alive but detached. The composer exists only in some branches of the chat footer. | "Link target session" and "chat visible" come from the live, displayed chat, not from session fields alone. |
| C11 | The `SessionListItem` component also renders rows outside the sidebar (peer message dialog picker), with selection enabled by default. | Drag and menu entry need an explicit "main sidebar list" signal. |
| C12 | `wa-dropdown` can return the focus to its trigger after a selection. | The menu entry leaves the focus in the composer. |
| C13 | During `dragenter` and `dragover`, the browser exposes the drag data types but not their content. | The overlay can show the title only for a drag that started in the same window. Other windows of the same instance get the generic label, and the self-link check happens at the drop. The instance must be readable from the type, not from the content. |
| C14 | The Android app has no `VIBRATE` permission. Firefox Android support for vibration is uncertain. | Haptic feedback is best effort. The lift of the row is the feedback that must always work. |
| C15 | Firefox is the owner's browser (desktop and Android). The Android app has two flavors: WebView (Chromium) and Gecko (GeckoView, the Firefox engine). | Every check runs in Firefox first, then in both Android app flavors. |

## Feasibility gate

C5 and C8 depend on real mobile browser behaviour. The first step of the plan is a throwaway check on a real
device, in Firefox Android and in both Android app flavors (WebView and Gecko):

- the sidebar list can be frozen during a long-press + move, with at most a few pixels of residual scroll.
  Test long moves in several directions, mostly downward, with the list scrolled to the top. During the move:
  no pull-to-refresh or reload, no scroll of an ancestor, the page or the chat, no browser toolbar movement, and
  touch events keep reaching the gesture until the release (the ghost follows the finger, no system cancel);
- the same, with a short list that does not scroll at all (`overscroll-behavior: contain` may not stop chaining
  on a list that cannot scroll);
- the same, in a wide layout on a touch device (sidebar not in a drawer);
- touch events keep reaching the gesture while the drawer closes under the finger;
- a long-press on a row does not start a native link drag and does not cancel the touch;
- the gesture survives a reorder or an unmount of the source row during the drag (C7);
- with a session streaming in the open chat, a normal sidebar scroll starts without delay (C5).

On desktop, the same first step checks, in Firefox (Linux) and Chromium:

- the custom ghost shows during a native drag (C4);
- in a narrow window, closing the drawer at drag start does not abort the native drag;
- the TwiCC-specific drag type set in window A appears in the drag types during the hover in window B (C13).
  If it does not, the cross-window session-link support is dropped: a drag from another window is then a plain
  text drop, as today, and the "same instance, other window" rule of "Drop on the chat" and the "from another
  window" part of the acceptance checks no longer apply.

If any other check fails, the work stops and the owner chooses how to go on.

## Acceptance checks

Desktop (Firefox, then Chromium):

- Drop on the chat inserts the link at the caret, with spacing, following the insertion rules for the footer panels.
- Ghost visible during the drag.
- Chat tab spring-load, then drop: inserted. Another row spring-load, then drop: inserted in that session.
  Spring-load to a session without composer, then drop: nothing inserted.
- Self-link refused everywhere, including from another window. After a refused drag, file and text drops still work.
- Multi-select active: no session-link drag. Non-linkable row: native URL drag only.
- Drop on the browser tab bar opens the same URL as today (frame kept). Drop in another application: same text as today.
- Narrow window with a pointer: the drawer closes at drag start, the drop on the chat inserts the link, a cancel reopens the drawer.
- Drop over an artifact frame in the chat: no overlay there, nothing inserted (as today for text).

Mobile (Firefox Android, then both Android app flavors: WebView and Gecko):

- Sidebar scroll unchanged and smooth. Tap opens. Long-press without a link target: multi-select at 500 ms.
- Long-press with a link target: armed phase, release enters multi-select.
- Long-press + move: the list does not scroll, nothing else scrolls, no reload, the drawer closes, the ghost is
  above the finger, the overlay covers the chat, the drop inserts the link, the keyboard does not pop up.
- Drop outside the chat: cancel, the drawer reopens.
- Long-press, small move (just over 10 px), release without moving over the chat: cancel, nothing inserted, the
  drawer reopens.
- Drop over an artifact frame in the chat: inserted.
- Multi-select active: taps toggle, no armed phase.
- No native link drag, no system cancel and no system menu on long-press.
- Wide layout on a touch device: the sidebar stays, the drag works, the list does not scroll.

Menu entry (both):

- Visibility rules above. The link lands in the composer following the insertion rules, and the Chat tab is
  selected. On a pointer device the composer has the focus when it is open; on a touch device it does not.

Link:

- After sending, a click on the link opens the session. The label shows the title without stray escapes.
