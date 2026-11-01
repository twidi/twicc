# Inline HTML artifacts in session conversations

Date: 2026-10-08

Status: Main-session-only scope and matching implementation plan pass adversarial review. Implementation has not started.

Scope: Main-session private and shared conversations, for Claude Code and Codex. Native subagent conversations are excluded.

## 1. Purpose

Agents can display interactive HTML pages directly inside conversation messages.
Examples include small dashboards, configuration widgets, and question forms.
The same files remain available in the session's Artifacts tab.

Each artifact has one stable identity and one live source folder.
An agent updates that folder and inserts the artifact tag again after a correction.
The latest publication replaces the artifact's previous position in the conversation.

TwiCC keeps each loaded iframe alive while its owning session remains in the frontend cache.
Virtual scrolling removes message placeholders, but does not remove artifact iframes.

## 2. Confirmed product decisions

The user confirms these requirements during the design conversation:

- Support HTML pages only.
- Only the main agent of a regular session creates and publishes inline artifacts. Native subagents do neither.
- Do not implement inline artifact parsing, rendering, retention, or export for native subagent conversations.
- Support a single HTML file, or an HTML entry point with relative JavaScript, CSS, assets, and optional `data/`.
- Keep one live source folder per artifact.
- Do not store source versions or provide code history.
- Display an artifact at its latest tag occurrence.
- Remove its widget from earlier occurrences without rewriting the source messages.
- Preserve arbitrary HTML and JavaScript state by retaining the iframe itself.
- Accept state loss after browser refresh, session eviction, or another action that destroys the session view.
- Keep `window.twicc.data` separate from iframe retention.
- Do not instruct agents to save every input change through the data API.
- Allow the existing Artifacts viewer to load an independent iframe over the same source files and data.
- Provide Full screen and Reload actions.
- Exclude element selection and responsive inspection modes.
- Exclude Submit to discussion and automatic messages to agents.
- Support inline widgets in shared conversations.
- Add an Include inline artifacts option to the session sharing dialog.
- Enable Include inline artifacts by default, including existing shares without an explicit option value.
- Include `data/` in an enabled export, matching existing artifact sharing.
- Serve public artifacts from dedicated copies with strict file confinement.

The remaining operational choices in this spec are proposed defaults for review.
They include tag syntax, height limits, and export update timing.

## 3. Existing components

| Component | Existing responsibility | Required adaptation |
| --- | --- | --- |
| `frontend/src/components/ui/MarkdownContent.vue` | Renders sanitized Markdown blocks | Render dedicated artifact blocks through Vue |
| `frontend/src/components/session/detail/items/TextContent.vue` | Renders provider message text | Supply artifact publication context |
| `frontend/src/components/frames/PersistentFrame.vue` | Connects a placeholder to a pooled frame | Separate placeholder lifetime from frame lifetime |
| `frontend/src/components/frames/FrameHost.vue` | Keeps iframe DOM nodes outside movable panes | Support conversation frames and public viewers |
| `frontend/src/stores/framePool.js` | Stores frame identity and geometry | Support session ownership and temporary placeholder attachment |
| `frontend/src/composables/useArtifactBroker.js` | Connects the broker and displays consent prompts | Keep inline broker ownership outside message components |
| `frontend/src/components/files/FilesPanel.vue` | Reloads HTML previews after relevant file changes | Keep nested inline folders independent |
| `frontend/src/artifact-broker/host.js` | Brokers requests and confines data writes | Reuse owner and share modes |
| `frontend/src/artifact-broker/shim.js` | Installs fetch interception and `window.twicc.data` | Reuse the existing API |
| `src/twicc/providers/compute_base.py` | Computes cross-provider session metadata | Extract artifact publication metadata |
| `src/twicc/artifacts_watcher.py` | Reports artifact file changes | Route data changes to enabled live exports |
| `src/twicc/core/services/share_mutation.py` | Creates and updates shares | Build and replace confined inline exports |
| `src/twicc/share/session_views.py` | Serves token-authorized transcript content and media | Serve inline manifests, documents, assets, and data |
| `frontend/src/share-session/ShareSessionApp.vue` | Hosts the public conversation | Own public inline frames and their broker connections |
| `src/twicc/agent/system_prompt.py` | Documents available artifact capabilities | Document the tag and its lifecycle |

The current `PersistentFrame` unregisters its frame in `onBeforeUnmount`.
That behavior remains valid for ordinary pane-owned frames.
Inline frames require a separate ownership contract.

The existing artifact data API already supports `get`, `set`, `list`, and `remove`.
Existing artifact shares copy the entry point's parent folder, including `data/`, and refuse public writes.

## 4. Source files and artifact identity

### 4.1 Directory contract

Use the session's existing artifacts root:

```text
<session-artifacts-root>/
  inline-artifacts/
    preferences/
      index.html
      app.js
      style.css
      data/
        settings.json
    calculator/
      calculator.html
```

Every inline artifact gets its own folder, including a single self-contained HTML page.
This keeps each artifact's assets, export boundary, and optional `data/` together.

The HTML entry point is a direct child of its artifact folder.
Allow `.html` and `.htm` entry points.
Other files below that folder are assets or data, not additional inline identities.

Agents use relative asset paths, as in existing HTML artifacts.
The iframe loads through a real document URL, not `srcdoc` or an HTML string inside the message.
This preserves relative asset resolution and the existing broker injection.

### 4.2 Identity

Artifact identity is `(source_session_id, artifact_id)`.
The artifact ID identifies the folder under `inline-artifacts/`.

The ID matches `[a-z][a-z0-9_-]{0,63}`.
It remains stable across corrections.
Different sessions can use the same ID without sharing a frame or files.

Reusing an ID for an unrelated widget replaces the existing widget.
An agent uses a new ID when both widgets must remain in the conversation.

The entry filename can change within the same artifact folder.
The folder and `data/` remain stable.

### 4.3 Artifacts tab

The existing file tree exposes `inline-artifacts/` as an ordinary subfolder.
No new artifact browser or mandatory bookmark is required.

Opening its HTML file uses the existing Artifacts viewer.
That viewer owns an independent iframe with independent memory state.
Both private views access the same live source folder and optional data store.

Existing data writes retain their last-writer-wins behavior.
Do not imply shared in-memory state between the two viewers.

## 5. Publication tag

### 5.1 Syntax

The agent writes its files before emitting this standalone block:

```text
<twicc:inline-artifact
  id="preferences"
  src="inline-artifacts/preferences/index.html"
  title="Preferences"
  height="360"
/>
```

A single-line form is also valid:

```text
<twicc:inline-artifact id="calculator" src="inline-artifacts/calculator/calculator.html" />
```

| Attribute | Contract |
| --- | --- |
| `id` | Required stable artifact ID |
| `src` | Required path relative to the owning session's artifacts root |
| `title` | Optional plain text, limited to 200 characters; defaults to the ID |
| `height` | Optional integer height in CSS pixels; defaults to 360; clamped to 160–900 |

Require double-quoted attribute values.
Attribute order does not matter.
Duplicate attributes invalidate the block.
Unknown attributes have no effect.

`src` must name an HTML file directly under `inline-artifacts/<id>/`.
Reject absolute paths, URLs, query strings, fragments, backslashes, and traversal components.
Paths are filesystem references, not percent-encoded URLs.

Treat titles as text and escape them at rendering boundaries.
Never interpret title content as HTML.

### 5.2 Recognition boundaries

Only finalized, user-facing assistant text can publish an artifact.
Support both providers in regular sessions only.
The owning session is the session containing the message.
Sessions whose type is `subagent` do not publish inline artifacts.
Their tag-like text follows ordinary Markdown rendering, without artifact errors, placeholders, frames, or brokers.
The main agent does not delegate inline artifact generation or publication to native subagents.

Recognize complete tags as standalone top-level Markdown blocks.
Permit whitespace and line breaks inside a complete tag.
Do not recognize tags inside these contexts:

- User messages, tool output, or reasoning.
- Inline code, fenced code, or indented code.
- Blockquotes, list items, or nested Markdown containers.
- HTML comments.
- Incomplete streaming text.

The shared frontend and backend fixtures define the same grammar and exclusion rules.
Do not use unrestricted substring matching over raw message text.

Streaming can show a pending placeholder for a complete candidate tag.
It cannot execute the page or replace an existing publication.
Publication does not wait for the entire agent turn to end.

Deduplicate provider representations of the same finalized message.
A canonical message and its provider mirror must not create two publications.
Copied parent history in native subagent rollouts cannot publish an inline artifact.

### 5.3 Publication identity and ordering

Publication identity is `(session_id, line_num, text_block_index, tag_offset)`.
The offset is the character offset in that assistant text block.

Order publications by source line, text block index, and tag offset.
Do not order them by arrival time or wall-clock timestamps.
Recompute and repeated ingestion produce the same identity.

Each valid tag creates a publication, even when its attributes match a previous tag.
The new occurrence requests a reload of the current source files.

## 6. Computed publication index

Maintain a derived publication index for each session.
Records identify the artifact, source occurrence, entry path, title, and requested height.
The implementation plan selects the storage representation and internal schema.

These records index tag occurrences already present in the transcript.
They contain no HTML payloads, file copies, JavaScript state, or code versions.

Keep earlier occurrence metadata to resolve shares with a frozen transcript boundary.
A latest-only index cannot identify the last publication before that boundary.

The normal provider compute paths own this index:

- Full recompute rebuilds it authoritatively.
- Live compute merges records by publication identity.
- Compute-version processing rebuilds historical sessions.
- Existing compute coordination prevents older rebuild results from replacing newer live records.

Use the existing serialized DB writer and compute revision guards.
Do not add an independent history scanner or historical backfill service.

Parsing determines tag validity without reading each source file during history compute.
Current file availability is a separate load or export check.
Historical validity cannot depend on files that an agent later deletes or changes.

The private session payload exposes the latest descriptor per ID.
The frontend does not need the complete internal occurrence catalog.

The share service derives a separate filtered manifest from the catalog.
It checks source item visibility and requires the source session to be the shared regular session itself.
Do not expose the private catalog directly through public metadata.

## 7. Conversation rendering

The Markdown pipeline produces a typed artifact block for a recognized tag.
Ordinary Markdown blocks continue through the existing sanitized HTML path.
Raw HTML remains disabled for ordinary message content.

An artifact block receives its session and exact publication identity.
It compares that identity with the active manifest.

| Block state | Rendering |
| --- | --- |
| Latest publication | Widget placeholder and controls |
| Superseded publication | No widget; show the replacement notice at its original location |
| Complete streaming candidate | Noninteractive pending placeholder |
| Invalid finalized artifact block | Compact error at that location |
| Valid publication with unavailable files | Compact load error at the active location |

Preserve surrounding message text and raw-source access.
Do not edit or remove earlier source JSONL messages.

Superseded finalized publications display this notice in a Markdown blockquote.
Reuse the existing blockquote styles without a separate callout style.
Render the notice text in italics at the normal assistant text size:
"This artifact has been replaced. Its latest version appears later in the conversation."
Only a present latest publication with a different identity establishes replacement.
Missing runtime or catalog entries do not display the notice.
Native subagents remain ordinary Markdown without widgets or replacement notices.

An invalid tag does not supersede an existing valid publication.
A valid tag with a missing file remains the latest publication and displays a load error.
Do not silently show an earlier publication as the latest version.

Supersession works when earlier or later messages are not loaded in the browser.
It also works when a conversation detail group is collapsed.
There is no fallback to an older visible occurrence.

Changes to widget presence or size use the existing scroller height and anchor correction mechanisms.
Do not reset the conversation scroll position or steal focus after a publication.
Reconcile streaming-to-final message replacement through the stable publication identity.

## 8. Session-owned runtime

### 8.1 Ownership

The session view owns an inline runtime registry.
Each loaded artifact has one frame and one broker connection in that registry.

The message component owns only a placeholder attachment.
Mounting and unmounting a placeholder must not register and unregister the frame itself.

Frame identity uses the owning view and artifact identity.
Public frame identity also includes the share identity.
It never uses a transient message component ID.

Keep the frame pool's append-only DOM order invariant.
Never move, detach, sort, or Teleport a retained iframe.
The existing iframe host supplies geometry and the overlay layer.

### 8.2 Lifecycle

| Event | Required behavior |
| --- | --- |
| First actual visibility | Create and load the artifact iframe |
| Placeholder leaves the viewport | Hide the frame without unloading it |
| Virtual scroller unmounts the row | Detach the placeholder; retain the frame and broker |
| Row remounts | Attach the placeholder to the retained runtime |
| Chat pane becomes hidden | Hide inline frames without unloading them |
| Cached session deactivates | Hide its frames and retain its runtime |
| Cached session reactivates | Reattach visible placeholders |
| Dock movement or resize | Update geometry without changing iframe identity |
| Full screen opens | Expand the same frame without navigation |
| Full screen closes | Restore its inline attachment or leave it hidden if that row is absent |
| Session view unmounts or is evicted | Destroy its frames, brokers, listeners, and pending prompts |
| Browser reloads | Load fresh documents; memory state is lost |
| Session archive or navigation destroys the view | Memory state is lost |

Do not load artifacts merely because their rows enter the scroller's preload buffer.
After first visibility, retain loaded frames without a separate per-artifact eviction policy.
The session cache remains the lifetime boundary.

Session deactivation suspends interaction with consent prompts, but retains the broker connection.
Serve a pending prompt when its owning session and artifact become visible again.
Destroying the owning view settles pending prompts and cancels runtime listeners.
Document reload or entry-point changes can rebind the broker to the new document configuration.
They must not leave a connection bound to an earlier entry point or public export.

Iframe retention does not serialize its state or require an artifact persistence API.
Input values, DOM nodes, JavaScript objects, and internal page position remain in that iframe.

### 8.3 Geometry and focus

Clip inline frames to the chat scroller's visible rectangle.
They cannot cover chat headers, composers, navigation controls, or another pane.

Suppress frames beneath pane-local overlays and during divider dragging, following existing frame-pool behavior.
Update positions through a shared scheduled geometry pass for attached, visible placeholders.
Do not poll every retained background frame on every scroll event.

Detached or hidden frames cannot receive pointer or keyboard interaction.
If a focused widget becomes hidden, return focus to its conversation context.
Do not focus a widget automatically after loading or publication.

## 9. Height and controls

The tag's `height` supplies the initial content height.
Use 360 CSS pixels when the tag omits it.
The supported range is 160–900 CSS pixels.

Support bounded automatic height reporting through the injected inline bridge.
Measure content after changes and update the placeholder through the session runtime.
Retain the latest measured inline height when the placeholder unmounts.

Cap automatic height at 900 pixels and use internal page scrolling for larger content.
Prevent repeated height updates from creating a parent/iframe layout feedback loop.
Do not replace the remembered inline height with a full-screen measurement.
Pages with viewport-dependent layouts can use the requested height and internal scrolling.

Report geometry only through the connection bound to that artifact's `contentWindow`.
An arbitrary window message cannot resize another artifact.

Provide these controls only:

Use the shared draggable floating tools menu from the Files preview.
Offer only Reload and Full screen / Exit full screen for inline artifacts.
Keep the menu on the same frame overlay in both states. Do not add a fullscreen toolbar.
Keep the Tools anchor reachable while folded or expanded after dragging, resizing, and fullscreen changes.
Use the same placement and clamping implementation in FilePane and inline artifacts.

- **Full screen**: expand the current frame to the app window.
- **Reload**: reload the current document and its local assets.

Keep a title and compact loading or error presentation beside those controls.
Do not add element selection, responsive device controls, editing tools, or Submit to discussion.

Full screen ownership stays in the session runtime, outside the virtualized row.
Scrolling the source row out of the DOM cannot close or reload an expanded frame.
Escape and the close control restore inline mode.
Session deactivation, supersession, or view destruction closes full screen.

Reload intentionally loses unsaved in-memory state.
It does not delete or reset `data/`.

## 10. Corrections and file changes

The agent updates the live folder, then inserts the same artifact ID in a new finalized message.
That publication becomes the only active conversation placement.

For an already loaded artifact, advance its reload identity once for the new publication.
The same frame host cell can remain, but the document reloads.
The new document starts with fresh HTML and JavaScript state.

For an artifact never loaded by this viewer, update its descriptor without creating a hidden iframe.
Load it when its latest placeholder first becomes visible.

Do not reload private inline frames on HTML, JavaScript, CSS, or asset write events.
Such events still refresh the Artifacts tree.
Only a finalized new publication or the Reload action requests an inline document reload.

Writing `data/` never reloads an iframe.
Recomputation or reconnecting with the same publication identity never requests another reload.
A missed newer publication replaces the active descriptor during reconciliation.

This is a live source folder, not a file transaction or versioned bundle.
Manual Reload during an edit can load incomplete files.
Existing documents can read changed files through later resource requests.
Do not promise that the publication tag makes agent file writes atomic.

The Artifacts tab keeps auto-reload for changes to its displayed page's code and assets.
For nested inline folders, evaluate relevance within the selected artifact folder.
Do not reload its preview because another inline artifact changes files or saves data.
Its own `data/` changes remain excluded from preview reloads.

## 11. Optional artifact data

Reuse `window.twicc.data` without changing its private storage contract:

```js
const saved = await window.twicc.data.get('settings.json')
await window.twicc.data.set('settings.json', { density: 'compact' })
```

Data belongs to the artifact folder's `data/` subtree.
Private writes retain the existing 10 MiB file and 100 MiB tree limits.

A widget can use no data API at all.
Do not require generic state serialization, automatic input persistence, or storage callbacks before eviction.

If an artifact uses saved data, its own code determines when it loads and saves that data.
TwiCC does not convert DOM input values into data files.
TwiCC does not send agent messages after a save.

Browser refresh, cache eviction, and archive-related teardown can lose unsaved memory state.
That loss is accepted product behavior.

## 12. Sharing option and public manifest

### 12.1 Option

Add `include_inline_artifacts` to session share options.
The dialog label is **Include inline artifacts**.

Default it to `true` for new shares and existing shares without that key.
An explicit `false` remains disabled.
Apply the same default in the dialog, backend validation, public routes, and export coordinator.

When enabled, explain the export scope:

> Includes inline artifact pages, their assets, and their saved data/ files. Visitors cannot modify the saved data.

Use the same option through owner REST, CLI, and agent share payload validation.
Keep existing agent sharing settings, scope rules, and provenance gates.
The option does not authorize an agent to create a share when existing sharing gates refuse it.

When disabled, render an **Inline artifact not included** placeholder at the active tag location.
Do not provide a private URL or a link that requires another artifact share.
Public file and proxy routes refuse access, even when old copies still exist on disk.

### 12.2 Eligible publications

Build the public manifest from publications whose messages are accessible under that share.
Apply the existing display ceiling and frozen line to the shared session itself.
Never select an inline artifact from a native subagent conversation, even when Include subagents is enabled.
For live shares, choose the last permitted publication independently for each artifact identity.

For snapshots, capture that selection when preparing the initial export or an explicit snapshot update.
After publication, the captured manifest determines widget positions until Push update or a share mode change.
Do not reselect publications merely because share metadata is fetched again.

Changes to the display ceiling preserve captured publications and copies that remain permitted.
Newly included artifacts get a selected publication and a corresponding initial copy.
If a captured publication becomes excluded, remove its widget and deny its export routes.
Do not substitute another tag automatically.
Push update can capture another permitted publication for that artifact.
Changes to titles or timestamps do not reselect publications or replace copies.

For a snapshot share, ignore publications after `frozen_at_line`.
Include subagents continues to control existing subagent transcript visibility only.
Changing it does not change inline selection, copies, or code revisions.

The viewer's local choice to collapse details does not grant or revoke file access.
The server's share ceiling and source-session authorization determine access.

Never use the private latest-only manifest directly.
Never expose an artifact introduced solely in excluded messages or an excluded session.

Provide manifest identities without absolute source paths.
The public viewer cannot supply a filesystem root or an arbitrary source directory.

Persist the export's selected publications and readiness as server-owned share metadata.
Callers cannot supply export paths, ready states, or copied-publication identities through share options.
Snapshot metadata survives backend restart without rereading changed source folders.

## 13. Dedicated public copies

### 13.1 Export structure

Use the existing per-share snapshot storage root:

```text
<get_share_snapshot_dir(share_id)>/
  inline-artifacts/
    <source-session-id>/
      preferences/
        index.html
        app.js
        style.css
        data/
          settings.json
```

Export only folders selected by the public manifest.
Do not copy the complete session artifacts root, the complete `inline-artifacts/` root, or a project directory.

Include each selected artifact's HTML, assets, and optional `data/`.
Copying the data follows existing artifact sharing behavior.
Visitors get the copied values, not the owner's iframe memory state.

There is one current export per share and artifact.
Replacement removes the previous export after in-flight readers finish.
Temporary replacement folders are internal and are never addressable through public routes.
There is no version browser or permanent collection of older exports.

### 13.2 Confinement

Resolve the configured artifacts root first.
Its configured symlink, including a worktree's artifacts-root symlink, remains supported.

Require each selected artifact folder to remain under its canonical owning session artifacts root.
Require its entry point to remain inside that artifact folder.

For this first implementation, reject symlinks inside exported artifact folders.
Do not follow a nested symlink and then copy its target into the public bundle.
Reject device files, sockets, FIFOs, and other nonregular file entries.

Check containment at the actual file read boundary.
Do not rely on a prior check followed by an unrestricted path-based open.
Concurrent path changes must not allow copying an outside target.

Public reads resolve only within the requested artifact's export folder.
Reject traversal, encoded traversal after URL decoding, and attempts to enter another artifact or session export.
The public API has no absolute-path, root-encoding, or private raw-file fallback.

Reuse the existing 200 MiB share snapshot limit as an aggregate inline export limit per session share.
Include data files in size accounting.
An export exceeding the limit returns an owner-visible error.

Build replacements outside the served directory.
Commit file replacement and its manifest readiness together under serialized share export ownership.
Readers observe a complete published copy, never a partially copied tree.
Copying does not claim transactional consistency across concurrent agent edits of the source files.

## 14. Export timing

### 14.1 Snapshot session shares

When creating an enabled snapshot share:

1. Freeze the root transcript line through the existing mechanism.
2. Resolve the last eligible tag for each artifact.
3. Copy the current folders and their data.
4. Publish the share with its filtered manifest and completed exports.

Later source changes do not update that snapshot's files or data.
Later tags do not change its artifact positions.

**Push update** prepares a new transcript boundary, selected manifest, and copies from current source folders.
On success, all three replace the previous snapshot together, without storing its history.
On failure, all three retain their previous published state and the owner sees an error.

An open snapshot viewer adopts the new manifest and exports when it reloads or reconciles share metadata.
When it discovers a replaced export, reload that widget once even if its selected tag identity remains unchanged.
This reload can lose the widget's in-memory state, like an ordinary Reload action.
Do not require a live update channel for snapshot viewers.

Enabling the option on an existing snapshot exports artifacts within its existing transcript boundary.
It copies their current files, not historical files from that boundary's date.
Do not imply recovery of an earlier code version.

Existing snapshot shares without an option value use the enabled default.
When their first authorized manifest request finds no export metadata, schedule the initial confined exports.
Capture their selected publications once for that initialization.
Show pending placeholders until those exports complete; never fall back to private source file routes.
For an existing snapshot, use its existing transcript boundary and the current source folders for that initial copy.
A failed export changes its placeholder from pending to error at its captured location.
The rest of the conversation and successfully exported artifacts remain usable.
Reload can retry a failed initial export for its captured publication.
It does not recopy artifacts whose initial snapshot exports already succeed.
Successful copies and captured placements remain frozen until an explicit snapshot update.

Snapshot copies exist for sharing only.
The private source remains live and has no version history.

### 14.2 Live session shares

Creating or enabling a live share copies the current eligible artifact folders.
An existing default-on live share without export metadata initializes copies through the same live selection rules.
Its already-published conversation remains usable while exports initialize or fail.
Initial artifact failures use the live error and recovery behavior below.

A new finalized eligible tag requests a replacement export for that artifact.
Do not copy code or assets on every intermediate agent file write.

Publish the new placement through the filtered manifest.
While its export is pending, show a pending state at the latest tag location.
Do not show an older export as the newly published code.

After the replacement completes, the viewer loads or reloads that publication once.
Retained frames in public viewers follow the same publication identity rule as private frames.

Source `data/` changes update only the data subtree of existing eligible live exports.
Coalesce filesystem events and replace completed data files safely.
A data deletion removes that exported file.
Do not copy unpublished code while refreshing data.

Data export updates never reload the iframe or move its placement.
A later data read sees the refreshed exported value.
Existing JavaScript memory state remains independent.

Ordinary source code edits without another tag leave the public code export unchanged.
Manual Reload in the public viewer reloads its published copy, not unpublished private files.

### 14.3 Failure and recovery

Initial share creation fails if any requested export cannot complete.
Enabling exports on an existing share fails without applying the option when the initial copy fails.

Owner actions requiring replacement or additional snapshot exports are all-or-nothing.
This includes Push update, mode changes, and option changes that introduce new snapshot exports.
Failure preserves the previously published options, root transcript boundary, manifest, and copies.
Report the failure to the owner without exposing partial replacements.

Legacy default-on snapshot initialization is separate because its conversation link already exists.
Its captured manifest remains stable when an individual initial export fails.
Show an artifact error instead of an indefinite pending state.
Only failed initial exports can retry through Reload; successful snapshot copies remain unchanged.

A later live export failure affects only that artifact.
Its latest location shows an error and does not execute a stale export as the new publication.
Other exported widgets and the conversation remain usable.

Serialize work per share and artifact.
Before committing, recheck share activity, option state, source-session eligibility, and the expected publication identity.
Older work cannot replace a newer publication or restore disabled access.

Use the existing watcher and share lifecycle for retries and reconciliation.
Retry failed live exports after relevant source changes, a new publication, or share reconciliation.
Do not perform filesystem copies while holding the SQLite write lock or blocking the event loop.

Reconnect refreshes the public manifest and export readiness.
It does not reload a frame when both its selected publication and published export remain unchanged.
Reconciliation reloads a replaced code export once, even when its selected publication is unchanged.
Data-only updates in a live share remain excluded from document reloads.
Backend restart can rebuild pending live work from current share and publication metadata.
Snapshot exports are never rebuilt automatically from changed source files.

Changing the share mode rebuilds a coherent initial export set from the current permitted sources.
It then starts live updates or freezes those copies according to the selected mode.
Changing the display ceiling updates eligible artifact selection.
Changing subagent inclusion does not update inline artifact selection or copies.
For snapshots, retain both the captured publication and its copy when that publication remains permitted.
Copy current files only for newly included snapshot artifacts, using their newly selected publications.
An excluded captured publication has no fallback widget until an explicit snapshot update selects one.
Excluded artifact routes become unavailable immediately, before asynchronous copy cleanup.

Disabling the option immediately denies all inline routes.
Live viewers close their inline frames when they receive that option update.
Snapshot viewers close them on their next metadata reconciliation or page reload.
Removing export files can finish after access is denied.
Revocation, expiration, deletion, and password changes retain existing share authorization behavior.
Deleting a share removes its inline export copies through the existing snapshot cleanup path.

## 15. Public routes and broker

Provide token-scoped routes with explicit source-session identity:

```text
/share/<token>/inline-artifacts/<source-session-id>/<artifact-id>/<asset-path>
```

Provide a filtered manifest endpoint under the same token.
The manifest supplies the selected entry filename and publication identity.
HTML, CSS, JavaScript, and data resolve as relative URLs within the copied artifact folder.

Every route checks these conditions:

- The share token resolves to an active session share.
- The viewer satisfies its password gate.
- Include inline artifacts is enabled.
- The source session equals the share's regular session; native subagent IDs are refused.
- The artifact is selected by the share's current filtered manifest.
- Its export is ready for the selected publication.
- The requested file remains inside that artifact's export folder.

Serve documents through the existing HTML shim injection and artifact CSP.
Serve their assets through the existing raw-file content-type and no-cache behavior.
Use the existing public-origin gate and share response headers.

GET and HEAD can read exported files.
Support `get()` and read-only `list()` over the copied `data/` tree.
Reject PUT, DELETE, and other owner data-write methods.
The broker's share mode must not bypass those server checks.

Use the existing broker in share mode for outbound requests.
Inline inclusion grants access to exported files, not arbitrary network hosts or authenticated private APIs.

When the entry point has an existing bookmark, use that bookmark's persisted public host permissions.
Enforce those permissions server-side, as existing artifact shares do.
An unbookmarked artifact has no public outbound host grants.
It can still run local code and load its copied files and data.

Do not make bookmarking mandatory for inline display or sharing.
Do not add a public visitor consent flow or inherit owner-only temporary host grants.
Retain the existing network broker invariants, including its metadata-address block and header behavior.

The standalone public viewer owns a persistent frame host outside its virtualized transcript rows.
Keep its shared runtime free of private router, auth store, and owner WebSocket dependencies.
Extend the share bundle's existing API and store adapters rather than importing the private application wholesale.

The public live channel carries only filtered inline manifest and readiness changes for that share.
It must not relay raw artifact watcher paths or private session manifests.

## 16. Agent instructions

Update the TwiCC system-prompt addendum in `src/twicc/agent/system_prompt.py`.
Document inline artifacts in its existing artifact section for both Claude Code and Codex.
Include one single-file example and one folder-with-assets example.

Explain these authoring rules:

- Write under `inline-artifacts/<id>/` in the current session's artifacts root.
- Only the main session agent creates and publishes inline artifacts.
- Do not delegate inline artifact generation or publication to native subagents.
- A native subagent must not create or publish inline artifacts.
- Write a complete page before inserting its tag.
- Use relative assets and a real HTML entry file.
- Insert a standalone tag in finalized assistant text at the intended conversation position.
- Reuse the same ID and folder for corrections.
- Insert the tag again after corrections to replace its previous position.
- Use a different ID for a separate widget.
- Choose the initial height for a compact conversation widget.
- Use `window.twicc.data` only when persistent data serves the widget's purpose.
- Do not require automatic storage of every interface change.
- Expect memory state to disappear after Reload, correction, refresh, or session cache eviction.

Do not add a new artifact tool or an agent publishing service.
The existing file tools and message text are sufficient.

## 17. Non-goals

- Source-code version history or immutable private publications.
- Inline artifact generation, publication, rendering, retention, or sharing in native subagent conversations.
- Restoring arbitrary JavaScript state after a document reload.
- Shared memory state between inline and Artifacts-tab viewers.
- Automatic saving of DOM input values.
- Submit to discussion, automatic agent notifications, or declarative form schemas.
- Element selection, responsive inspection, source editing, or a new artifact browser.
- Public writes to owner data or persistent per-visitor data stores.
- Public access to unselected artifacts, other sessions, or repository files.
- A separate per-widget share token or mandatory bookmark.
- Changes to the existing standalone artifact-share behavior.
- General network broker redesign or unrelated security changes.

## 18. Acceptance and validation

### Publication and provider behavior

- Both providers publish a single-file widget and a folder-based widget.
- Native subagent tags and copied parent tags create no catalog records, placeholders, frames, brokers, or public exports.
- Attribute order and supported multiline syntax produce the same descriptor.
- Tags in examples, comments, tool results, user messages, and reasoning do not execute.
- Incomplete streaming tags do not publish or supersede.
- A finalized tag publishes before the full agent turn ends.
- Mirrored provider messages and repeated ingestion do not duplicate a publication.
- Full recompute and live ingestion produce identical publication identities and active placements.
- Recompute preserves publications arriving after the rebuild starts.
- Both providers receive the updated addendum with tag syntax, correction rules, and optional data API guidance.

### Runtime retention

- Enter text, adjust a range, change widget tabs, and scroll the message beyond the unload buffer.
- Return to the message and verify unchanged HTML and JavaScript state without data API calls.
- Switch to another cached session, then return with unchanged state.
- Change dock placement and hide or show the chat pane without reloading the widget.
- Open and close full screen without reloading or losing state.
- Unmount the source row while full screen remains open.
- Evict the owning session and verify frame, broker, prompt, and listener cleanup.
- Browser refresh and manual Reload start fresh memory state without deleting saved data.
- Frames never overlap the composer or receive input while hidden.

### Corrections and geometry

- Publish the same ID in a later message while the old message is not loaded.
- Verify that only the latest occurrence displays a widget.
- Load the old range later and verify that its widget remains absent.
- Verify the same behavior with collapsed message groups.
- Opening a subagent view hides main-session frames as needed without creating subagent inline runtimes.
- A new publication reloads once; recompute and reconnect do not reload it again.
- Intermediate code writes do not reload an inline widget.
- Data writes do not reload an inline or existing Artifacts-tab preview.
- A correction or height change preserves the conversation's scroll anchor.
- Automatic height reporting remains bounded and does not enter a layout loop.

### Sharing and confinement

- New shares and existing shares without the option use the enabled default.
- Existing links initialize confined exports before serving inline files; an explicit `false` denies access.
- An enabled share exports only artifacts selected by its permitted transcript.
- Exported HTML loads sibling CSS, JavaScript, assets, and saved data.
- Public inputs remain interactive while data writes are rejected.
- Opening an Artifacts-tab viewer privately does not share its iframe memory state with public viewers.
- Excluded sessions, post-boundary artifacts, and unselected artifact folders return unavailable responses.
- Traversal, encoded traversal, sibling export access, and absolute-root requests cannot escape the selected export.
- Symlinks and special files cannot import outside content into an export.
- Configured worktree artifacts-root symlinks remain supported.
- Missing files, oversized copies, and replacement failures do not expose partial exports.
- A live publication updates its copy and placement; a data change updates only copied data without reload.
- A snapshot remains unchanged until Push update; enabling it later copies current files at its existing tag placement.
- Include subagents changes transcript visibility without changing inline selections, copies, or code revisions.
- Selection-affecting option changes retain both the captured tag and copy for widgets that remain permitted.
- Excluding a captured tag removes its widget without substituting another tag or serving an incompatible copy.
- A successful Push update can replace a copy without another tag; viewer reconciliation reloads that copy once.
- A failed Push update preserves the prior root boundary, manifest, options, and all published copies.
- A failed legacy initial export shows an error; retry leaves successful snapshot copies and captured positions unchanged.
- Disabling inclusion or revoking the share prevents HTTP and live-channel access immediately.
- Password and expiration checks apply to HTML, assets, data, manifests, and proxy requests.
- Unbookmarked public widgets have no outbound grants; bookmarked widgets use the existing server-enforced public permissions.

Use focused backend, frontend, and real-browser lifecycle checks during implementation.
Validate the standalone share bundle with the production build because it is not HMR-managed.
No product implementation or runtime validation forms part of this spec-writing task.

## 19. Related designs

- [Artifact data persistence](../../plans/2026-08-05-artifact-data-persistence-design.md)
- [Artifact network broker](../../plans/2026-06-18-artifact-network-broker-design.md)
- [Session and artifact sharing](../../plans/2026-07-05-sharing-design.md)
- [Session artifact bookmark roots](../../plans/2026-07-15-session-artifact-bookmark-root-design.md)
- [WebSocket responsiveness and indexed session history](2026-09-29-websocket-sync-responsiveness-design.md)

After user review, create the implementation plan from this spec.
Spec approval does not itself start implementation.

## 20. Adversarial spec review

Two independent internal reviewers assess product behavior and public sharing across three review rounds.
They review spec-level contradictions and missing outcomes, without requiring implementation details.
Both final verdicts are **READY**, after a final verification of the snapshot-disable correction.

The original review resolves these issues:

- Freeze snapshot widget selection between explicit updates.
- Preserve matching captured publications and copies through visibility-option changes.
- Keep snapshot updates atomic on export failure, with separate recovery for existing default-on links.
- Reload replaced public code copies during reconciliation, even when their tag stays unchanged.
- Distinguish immediate route denial from viewer closure after a notification or metadata reconciliation.
- Leave the publication index's internal storage schema to the implementation plan.

The shared system-prompt addendum update remains required by section 16.
Product implementation and runtime checks remain outside this review.

The user subsequently excludes native subagents from inline artifact authoring and runtime support.
Two internal reviewers validate the main-session-only amendment and matching implementation plan.
Both final verdicts are **READY** after checking scope exclusion, ordinary subagent transcript behavior, and share-option independence.
