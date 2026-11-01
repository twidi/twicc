# Artifact Data Persistence — Design

**Status:** design complete, decisions resolved with the user 2026-08-05; implementation not started
**Date:** 2026-08-05
**Scope:** give an HTML artifact a writable `data/` folder under its own directory, reachable with plain `fetch` (plus a thin `window.twicc.data` sugar). General-purpose persistence for interactive artifacts; the agent reading those files afterwards is one use case among others, not the mechanism's purpose.
**Companions:** `2026-06-18-artifact-network-broker-design.md` (the broker and its CSP/consent model, esp. §6.4/§6.6/§9), `2026-06-16-artifact-bookmarks-design.md`, `2026-07-05-sharing-design.md` (§8, share snapshots).

---

## 1. Background — what exists today

- The artifact iframe carries `connect-src 'none'` (`ARTIFACT_CSP`, `src/twicc/artifacts/broker_html.py:45`). Its **only** egress is postMessage to the trusted host, so every request it makes is observed by TwiCC code.
- `createBrokerHost` serves the artifact's **own** files without prompt and without the server proxy: `if (sameOrigin && url.href.startsWith(ownDir)) return await hostDirectFetch(req)` (`frontend/src/artifact-broker/host.js:212`), with `ownDir = new URL('.', documentUrl).href` (`host.js:139`).
- `hostDirectFetch` forwards **method, headers and body verbatim** (`host.js:190`), and the shim serializes a body for any non-GET/HEAD request (`frontend/src/artifact-broker/shim.js:34`).
- **Consequence:** a `PUT` fired from an artifact already traverses shim → host → Django today. It dies on the views' method guard: `if request.method not in ("GET", "HEAD"): return HttpResponseNotAllowed(...)` — `file_raw` (`src/twicc/views.py:2124`), `standalone_file_raw` (`views.py:2152`), `session_artifact` (`views.py:3440`). There is no `CsrfViewMiddleware` in the stack (`src/twicc/settings.py:168`). **The work is in the views, not in the transport.**
- An artifact document is served by three routes depending on context:

  | Context | Route | View | Confinement root |
  |---|---|---|---|
  | Artifacts tab preview | `/api/file-raw/<root_b64>/<path>` | `standalone_file_raw` (`views.py:2143`) | the session's artifacts dir |
  | Project Files tab preview | `/api/projects/<id>[/sessions/<sid>]/file-raw/<path>` | `file_raw` (`views.py:2115`) | the project's allowed base dirs |
  | Dedicated artifact page | `/artifacts/<bookmark_id>/<asset>` | `artifact_serve` (`views.py:3753`) | the bookmark's directory |

  `session_artifact` (`/artifacts/<session_id>/<file>`, `views.py:3417`) is **not** in scope: its URL accepts a single path segment and its extension allowlist serves images only, so no artifact document — and no `data/` path — is ever routed through it.
- Share serving is separate and reads from a **snapshot copy** of the artifact's parent directory (`confined_snapshot_path`, `src/twicc/share/artifact_views.py:32-49`), never the live folder.
- `src/twicc/artifacts_watcher.py` watches `<data_dir>/artifacts/` and broadcasts `artifact_files_changed` with paths relative to the session artifacts dir. On receipt, `FilesPanel.vue` refreshes the tree and **reloads the previewed HTML page** when the change shares its top-level folder (`changeAffectsHtmlPage`, `frontend/src/components/files/FilesPanel.vue:507`).
- `file_content.write_file_content` (`src/twicc/file_content.py:104`) is text-only, UTF-8, and requires the file to already exist — not reusable for this feature.

## 2. Requirements (user decisions, 2026-08-05)

1. An HTML artifact can **save data in its own folder**. This is a general capability; agent readback is a consequence, not the goal.
2. An artifact must **never write above the directory that serves it**.
3. Writes are **confined to a `data/` subfolder** of that directory: an artifact can neither overwrite its own `index.html` nor a sibling file.
4. **Silent under the artifacts tree, prompted elsewhere** (see §6).
5. **No agent notification of any kind** — no auto-sent message, no composer pre-fill. The agent reads the files when asked to.

## 3. Scope model — `<ownDir>/data/`, two independent locks

The write target must resolve inside `<directory of the served document>/data/`.

**Lock 1 — host side.** Same `ownDir` comparison the read path already performs, plus a required `data/` first segment. The host is TwiCC code and the CSP leaves the artifact no way around it, so this is a real boundary, not a convention. A non-GET request under `ownDir` but outside `data/` is rejected by the host without reaching the network.

**Lock 2 — server side.** The host sets a request header carrying the document's directory, **overwriting any homonym supplied by the artifact** (`hostDirectFetch` forwards artifact headers verbatim, so the overwrite is mandatory, not cosmetic). For write methods only — `GET`/`HEAD` behaviour is untouched — the view then requires all three:

1. the header is present. **Absent ⇒ 405**, so a write is only ever possible from a served document, never from `curl` or any other client;
2. the resolved target is under `<header dir>/data/`;
3. the resolved target is under the route's own confinement root (unchanged from the read path: `validate_path` for project scope, `validate_standalone_root` + realpath check for standalone scope, the bookmark dir for `artifact_serve`).

Symlinks are resolved before comparison, as the read path already does (`views.py:2167-2173`).

Accepted consequence: two HTML files sitting flat at the root of a session's artifacts dir share one `<artifacts>/data/`. Same session, same agent — no trust boundary is crossed, only a name collision is possible.

## 4. HTTP surface

Applied to `standalone_file_raw`, `file_raw` and `artifact_serve` only.

| Operation | Request | Behaviour |
|---|---|---|
| Read a file | `GET data/x.json` | unchanged, works today |
| Write a file | `PUT data/x.json` | create or overwrite; atomic (temp file in the target dir + `os.replace`); missing parent dirs under `data/` are created |
| Delete a file | `DELETE data/x.json` | 404 when absent |
| List | `GET data/` | JSON index: name, size, mtime, recursive |

Request/response bodies are raw bytes — text and binary alike; no charset assumption, no JSON envelope on `PUT`.

**Limits:** 10 MB per file, 100 MB cumulative over a `data/` tree. Both refused with a JSON error payload carrying the cap and the attempted size (not a bare 413 — the artifact author must be able to surface the reason). No global quota accounting.

**Concurrency:** last writer wins. `os.replace` guarantees a reader never observes a half-written file; nothing more.

New helper module `src/twicc/artifacts/data_store.py` (byte-oriented write/delete/list with the caps and atomic replace); the existing `file_content` helpers are text-only and create-hostile, so they are not extended.

## 5. Client surface

Plain `fetch` on relative URLs is the mechanism and stays fully supported — an artifact and any third-party library can use it with no knowledge of TwiCC.

The shim additionally exposes `window.twicc.data`:

| Method | Behaviour |
|---|---|
| `get(name)` | parsed JSON, `null` when absent |
| `set(name, value)` | JSON-serialized `PUT` |
| `list()` | the index from `GET data/` |
| `remove(name)` | `DELETE` |

It hides the `data/` prefix and the JSON round-trip. It is a ~40-line wrapper over the same `PUT`; it adds **no penpal method** and no server surface. Its real justification is **detectability**: an artifact can test `window.twicc?.data` at runtime, whereas nothing signals that a `PUT` would be accepted.

The agent writes the same folder directly with its own file tools. That is what provides pre-filling: the agent drops `data/config.json`, the artifact reads it on load, the user manipulates the UI, the artifact writes back, the agent re-reads.

## 6. Consent

- **Under a session's artifacts tree:** silent. That folder is the agent's own workspace.
- **Anywhere else** (typically HTML previewed from a project's Files tab): one prompt per page, in the same register as the broker's existing network prompt — "this page wants to store data in `<path>`" — remembered for the lifetime of the tab only.

No "forever": the broker's persistent memory hangs off an `ArtifactBookmark` (`allowed_hosts`), which an arbitrary repository file does not have. Introducing a model for it is not justified by the scenario it covers.

The host receives a boolean from its mount context stating whether the document sits under an artifacts root. `FilePane.vue` already holds what is needed (`rootRestriction` combined with `artifactBookmarkSessionId`, `frontend/src/components/files/FilePane.vue:307-322`); the dedicated artifact page is by construction always under an artifacts root.

**Threat model, stated plainly.** The only scenario this prompt addresses is previewing HTML the user did not produce — a vendored page under `node_modules`, downloaded documentation, a file from a cloned repository — whose script could otherwise create files beside itself silently. The delta in exposure is small: the broker's design (network-broker design §6.4) already makes TwiCC's own API reachable from an artifact **with the user's consent**, so this feature does not open a new privilege class; it removes friction from something a single click already permits.

## 7. Reload heuristic — required fix

Without this change the feature destroys itself in the main use case.

`artifacts_watcher` broadcasts `artifact_files_changed`; `changeAffectsHtmlPage` (`FilesPanel.vue:507`) reloads the previewed page whenever a changed file shares its top-level folder. An artifact writing `data/state.json` therefore triggers **its own reload**, discarding the in-page state it has just persisted — the user moves a slider, the artifact saves, the page resets.

**Fix:** the `data/` subtree is excluded from the preview-reload trigger. It stays included in the soft tree refresh (`refreshTreeSoft`), so written files still appear in the Artifacts tab. An artifact that wants to react to an external change of its data does so itself — it has `list()`.

The exclusion is by path shape (a `data/` segment under the previewed page's folder), evaluated where the reload decision is already made; the watcher and its broadcast are untouched.

## 8. Share mode

Strictly read-only: the share artifact routes refuse everything that is not a `GET`. A public link must not let any visitor fill the owner's disk.

Two consequences of the existing snapshot mechanism, worth stating because they are not obvious:

- A shared artifact reads the `data/` folder **as copied into the snapshot** — a point-in-time copy, not the live folder. Later owner-side writes are invisible to existing links. Limitation: the share routes serve files only — the `GET data/` listing 404s on a share (the shim's `list()` maps it to an empty list); `get()` on snapshot data files works.
- Sharing an artifact therefore **publishes whatever it had stored at snapshot time**, including anything the owner entered through the artifact's own UI. This is the existing snapshot behaviour (`confined_snapshot_path` copies the parent directory) applied to a new kind of content, not a new leak path — but it is user-visible and should be surfaced in the share dialog's copy rather than left implicit.

## 9. Out of scope, deliberately

- No agent notification, no auto-sent message, no composer pre-fill (user decision §2.5).
- No new database model and no new synced state: the filesystem is the store.
- No declarative form format. The point of an artifact is its sliders and live preview; a generic renderer defeats the purpose.
- No versioning, history or conflict resolution.

## 10. Implementation notes

- Touched backend: `src/twicc/views.py` (three views' method guards + write dispatch), new `src/twicc/artifacts/data_store.py`, `src/twicc/share/artifact_views.py` (explicit non-GET refusal).
- Touched frontend: `frontend/src/artifact-broker/host.js` (write routing, doc-dir header, consent branch), `frontend/src/artifact-broker/shim.js` (`window.twicc.data`), `frontend/src/components/files/FilePane.vue` (artifacts-root flag to the host), `frontend/src/components/files/FilesPanel.vue` (§7), `frontend/src/composables/useArtifactBroker.js` + `frontend/src/artifact-shell/` if the prompt copy is shared.
- **The shim and the shell are not HMR'd**: `cd frontend && npm run build` after touching `artifact-broker/*` or `artifact-shell/*`.
- The capability must be announced in the agent system-prompt addendum's artifacts section — otherwise no agent will know it exists, and the whole feature stays dormant.
