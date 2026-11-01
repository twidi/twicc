# Provider Settings section — adopt the agent-settings matrix design

**Date:** 2026-07-14
**Status:** design (pending review)

## 1. Goal

The per-session agent-settings popover (`AgentSettingsPopover.vue`, message input)
was recently reworked around a **provider × model × effort matrix**, its
adjustable **benchmark-score weighting** controls, and a compact row of
**switches** (context toggle, thinking, Chrome MCP, fast mode), leaving only
**permission** as a `wa-select`.

We want the exact same design for the **per-provider defaults** edited in the
Settings panel — `ProviderSettingsSection.vue` — which today is a flat list of
`wa-select`s (one per field: model, context size, effort, thinking, permission,
untrusted permission, Chrome MCP, fast mode).

Explicitly **out of scope**: the project-edit dialog
(`ProjectAgentDefaultsSection.vue`). It carries an inheritance model
(inherit / override per field) with no matrix design yet — left untouched.

**Hard constraint (user):** maximise component sharing, **zero duplication**,
while respecting each provider's field support. The recently-shipped popover must
keep behaving identically — every popover-side change here is a behaviour-
preserving refactor.

## 2. Current state

### 2.1 The popover (reference design)
- `AgentSettingsPopover.vue` renders, top→bottom: Reset/Presets dropdown,
  action callouts, then the scrollable panel = `<AgentSettingsMatrix>` +
  `<AgentSettingsBenchmarkWeights>` + a wrapping row of switches + the
  permission `wa-select`.
- Matrix data (`matrixBlocks`, `matrixEffortColumns`, `matrixDefaultCell`) is
  assembled in `useSessionAgentSettings.js` — **session-coupled** (drafts,
  provider switching, per-session override refs vs resolved defaults, benchmark
  scores).
- Switch/select rows (`settingRows` → `switchRows`/`selectRows`) are assembled
  **inline in the popover** from the provider helper hooks
  (`supportsAgentSetting`, `fieldHasChoice`, `getFieldLabel`, `getFieldChoices`,
  `isFieldDisabled`, `getFieldNotice`, `getDisplayedSelectValue`, …).
- `AgentSettingsMatrix.vue` and `AgentSettingsBenchmarkWeights.vue` are already
  **pure, session-agnostic** presentational components:
  - Matrix: props `blocks` + `effortColumns`, emits `select({provider, model,
    effort})`. Nothing session-specific.
  - Weights: drives the **global** `benchmarkWeights` store directly; only prop
    is `providerCount` (hides "Default provider only" when ≤ 1).

### 2.2 The target (`ProviderSettingsSection.vue`)
- Iterates a `FIELD_ORDER` (`selected_model`, `context_max`, `effort`,
  `thinking_enabled`, `permission_mode`, `permission_mode_if_untrusted`,
  `claude_in_chrome`, `fast_mode`) filtered by `supportsAgentSetting`, rendering
  a labelled `wa-select` per field.
- Reads/writes each field through `helpers.getDefaultValue(field)` /
  `helpers.setDefaultValue(field, value)` (persisted + synced provider store).
  **No local draft / apply step** — writes are immediate.
- Values are always **concrete defaults** (never the null "follow default"
  sentinel the session popover uses).
- Also renders: a Claude-only Hybrid-mode group, a Presets group ("Manage
  presets…"), and an Orchestration opt-out switch. All three stay unchanged.
- Rendered in the Settings popover detail pane (`min(90vw,700px)` total, 200px
  nav + divider → detail content ≈ 460–470px wide, comparable to the popover's
  matrix width). Fits.

### 2.3 Provider field support (drives what shows, via existing hooks)
| Field | Claude | Codex |
|---|---|---|
| model × effort matrix | opus/sonnet/fable × low…max | gpt tiers × low…**ultra** |
| context_max | 200K/1M — gated on model 1M support | 272K/372K **fixed by model** → `fieldHasChoice` false |
| thinking_enabled | yes (unless model forces on) | **unsupported** |
| claude_in_chrome | yes | **unsupported** |
| fast_mode | yes (only on supported Opus) | **unsupported** |
| permission_mode | 6 modes | 5 modes |
| permission_mode_if_untrusted | yes | yes |

⇒ For **Codex** the switches row collapses to empty (context hidden as
model-fixed, no thinking/chrome/fast) and the section shows **matrix + weights +
two permission selects** only. This falls out of the existing
`supportsAgentSetting` / `fieldHasChoice` hooks — no provider branching.

## 3. Reuse & extraction plan

### 3.1 Reused (near-zero change)
- `AgentSettingsMatrix.vue` — one small additive tweak only (3.6 / D2).
- `AgentSettingsBenchmarkWeights.vue` — **one additive prop** `showAutoSelect`
  (default `true`; popover untouched). The settings section passes
  `:show-auto-select="false"` to hide the "Auto-select best" / "Default provider
  only" line — see D1. `providerCount = 1`.

> **Why the prop (review finding, MAJOR).** `benchmarkWeights` is a **global**
> store and the message-input popover is **always mounted** (`MessageInput.vue`,
> not `v-if`-gated on open), so its auto-select watcher (`AgentSettingsPopover.vue`
> ~L158) is always live and fires on any global weight/flag change. Surfacing the
> auto-select switch in the settings section would let a Settings-side action arm
> and trigger a **silent mutation of the currently-open session**. We therefore
> hide the auto-select controls here and add **no** section-side auto-select
> watcher. The sliders stay: they re-rank scores globally (a legitimate global
> preference) and only move the "best" ring — the user clicks the cell manually.
> Residual, accepted + rare: if `autoSelectBest` was already armed from a prior
> popover use, dragging a slider in Settings still triggers the popover's watcher.
> The flag is in-memory (resets on reload) and defaults off; and semantically
> "auto-select best" already means "selections follow my weights everywhere".

### 3.2 Extract: matrix data building → `frontend/src/utils/agentMatrix.js`
Pure functions, no session concept:

```
buildEffortColumns(providers) -> [{ effort, label }]
    Union of the providers' effort choices in ladder order (current
    matrixEffortColumns logic).

buildMatrixBlocks({ providers, effortColumns, currentProvider,
                    selectedModel, selectedEffort, defaultCell, benchmarksStore })
    -> [{ provider, label, icon, isCurrent, rows:[{ model, name, version,
          isLatest, cells:[{ effort, enabled, selected, isDefault, score,
          borderStyle? }] }] }]
    (current matrixBlocks body verbatim, parameterised)
```

Callers:
- `useSessionAgentSettings.js`: `matrixBlocks`/`matrixEffortColumns` become thin
  wrappers over these (same inputs it computes today: `matrixProviders`,
  session provider as `currentProvider`, `selectedModel ?? resolvedDefault` /
  `selectedEffort ?? resolvedDefault`, `matrixDefaultCell`). **Behaviour
  identical.**
- `ProviderSettingsSection.vue`: `providers = [props.provider]`,
  `currentProvider = props.provider`, `defaultCell = null`, and the highlighted
  `(selectedModel, selectedEffort)` = the pair reconciled through
  `helpers.enforceAgentSettingsConsistency({ selectedModel:
  getDefaultValue('selected_model'), effort: getDefaultValue('effort') })`. This
  resolves a retired/disabled stored model to its available substitute **and**
  demotes an effort the substitute doesn't support, so the matrix never
  highlights a disabled (hatched) cell — mirroring the effective state the
  popover shows post-consistency-watcher (review finding, minor).

Rationale the shared fn already satisfies both: `selected` highlight is gated on
`isCurrent` (single-provider settings block is current → its default cell shows
the check); `borderStyle` picks solid when `nProviders < 2` (single provider) —
so the section's best-score cell gets the solid ring, matching the popover.

### 3.3 Extract: switch-row building → `frontend/src/utils/agentSwitchRows.js`
```
SWITCH_FIELD_WIDGETS = [
  { field: 'context_max',      kind: 'toggle' },
  { field: 'thinking_enabled', kind: 'switch' },
  { field: 'fast_mode',        kind: 'switch' },
  { field: 'claude_in_chrome', kind: 'switch' },
]

buildSwitchRows(helpers, { fieldContext, valueFor }) -> rows[]
    Exactly the popover's current switch/toggle branch of `settingRows`,
    factored out. `fieldContext(field)` returns the per-field render ctx;
    `valueFor(field)` returns the current effective value (for `checked` and the
    context-toggle default). Rows skip unsupported fields (`supportsAgentSetting`)
    and non-toggleable ones (`fieldHasChoice`).
```

Callers:
- Popover: `valueFor = f => currentEffective.value[f]`, `fieldContext` = its
  existing per-field ctx (with `isStarting`, `isContextMaxForced`, forced
  notices…). Permission stays in its own `selectRows` path (unchanged).
- Settings section: `valueFor = f => getDefaultValue(f)`,
  `fieldContext = f => ({ field: f, effectiveModel: resolvedDefaultModel,
  selectedValue: getDefaultValue(f), defaultValue: getDefaultValue(f) })`
  (no `isStarting`/forced state → no session-only notices, which is correct).

### 3.4 Extract: switches presentation → `AgentSettingsSwitches.vue`
New presentational component (in `components/message/`, alongside the other two
shared ones), holding the switch/toggle markup **and its scoped styles**
currently inline in the popover:
- Props: `rows` (shape from `buildSwitchRows`), optional `uidPrefix`.
- Emits: `change({ field, value })` where `value` is the final typed value
  (`kind === 'toggle' ? (checked ? bigValue : smallValue) : checked`). The host
  applies it (popover → its `SELECTED_REFS[field]`; section →
  `setDefaultValue(field, value)`).
- Notice icon + `AppTooltip` stays in the component (identical markup).
- **Move** the `.settings-switches` / `.setting-switch` / `.setting-notice-icon` /
  `.notice-*` scoped rules from `AgentSettingsPopover.vue` into this component,
  and **delete** them from the popover (they become dead there).

Popover refactor: replace its inline `.settings-switches` block with
`<AgentSettingsSwitches :rows="switchRows" @change="onSwitchRowChange">`; keep
its permission `selectRows` block as-is.

### 3.5 Permission select — NOT forced into a shared widget
The popover permission select carries the session-only **Default / Force-to**
sentinel machinery (`DEFAULT_SENTINEL`, per-field reset link, forced/clamped
display). The section edits **concrete defaults**, so it renders **plain**
`wa-select`s (no sentinel), one for `permission_mode` and one for
`permission_mode_if_untrusted` — essentially the section's current permission
markup, kept. Both still drive the same helper hooks (`getFieldChoices`,
`isChoiceDisabled`, `getChoiceDisabledReason`, `getFieldHelpText`), so option
disabling/`(not available)`/reasons stay consistent with the popover.

_Sharing here would need a `mode: 'session' | 'default'` toggle on a new select
component for marginal gain and real coupling — deliberately skipped. (Open
decision D5.)_

## 4. New `ProviderSettingsSection.vue` structure

```
[Hybrid Mode group]            (Claude only, unchanged)   + divider
[Default model & effort label] (subtle group label — D3)
  <model-fallback callout>     (when stored default model unavailable — kept)
  <AgentSettingsMatrix :blocks :effort-columns @select=onMatrixSelect>
  <AgentSettingsBenchmarkWeights :provider-count="1" :show-auto-select="false">
  <AgentSettingsSwitches :rows="switchRows" @change=onSwitchChange>  (hidden if empty → Codex)
  <permission select>          "Default permission mode"
  <untrusted permission select> "Default permission mode (untrusted projects)"
  + divider
[Presets group]                (unchanged)                 + divider
[Orchestration group]          (unchanged)
```

- `onMatrixSelect({ model, effort })`:
  `helpers.setDefaultValue('selected_model', model)` then
  `helpers.setDefaultValue('effort', effort)`. Order rationale: Claude's
  `setDefaultValue('selected_model')` re-runs `enforceAgentSettingsConsistency`
  (cascading context/effort/fast/permission/thinking demotion against the new
  model, exactly like the popover's consistency watcher), then the explicit
  effort write applies the clicked (guaranteed-enabled) cell. Codex's setter is
  a plain write; the matrix only enables valid (model, effort) cells, so the
  pair is always consistent.
- `onSwitchChange({ field, value })`: `helpers.setDefaultValue(field, value)`.
- **No auto-select watcher** (D1, revised): the section never auto-writes a
  persisted default from a weight change. The weights block is rendered with
  `:show-auto-select="false"`.

## 5. Files

**New**
- `frontend/src/utils/agentMatrix.js` — `buildEffortColumns`, `buildMatrixBlocks`.
- `frontend/src/utils/agentSwitchRows.js` — `SWITCH_FIELD_WIDGETS`, `buildSwitchRows`.
- `frontend/src/components/message/AgentSettingsSwitches.vue` — shared switches.

**Modified**
- `frontend/src/composables/useSessionAgentSettings.js` — matrix computeds call
  `agentMatrix.js` (behaviour-preserving).
- `frontend/src/components/message/AgentSettingsMatrix.vue` — hide the "• default"
  legend item when no cell is a default (additive; popover unaffected).
- `frontend/src/components/message/AgentSettingsBenchmarkWeights.vue` — additive
  `showAutoSelect` prop (default `true`), gating the `.weights-autoselect` line.
- `frontend/src/components/message/AgentSettingsPopover.vue` — use
  `buildSwitchRows` + `<AgentSettingsSwitches>`; delete the moved switch CSS
  (behaviour-preserving).
- `frontend/src/components/app/ProviderSettingsSection.vue` — the redesign.

**Testing note:** there is no frontend test runner wired (no `vitest`; the few
`*.test.js` files aren't executed and none touch agent settings). The guardrail
for the "behaviour-preserving" popover refactor is therefore a careful diff-read
of the popover output + live verification (matrix pick, switches, notices,
context toggle, permission) — see §7.

No backend, no store, no CSS-build (shim/shell) changes. `DEFAULT_SENTINEL`
export from the composable is untouched (still used by
`ProjectAgentDefaultsSection.vue`).

## 6. Open decisions (for review)

- **D1 — Auto-select best in the defaults context (REVISED after review).**
  *Chosen: render the weight sliders/presets but HIDE the auto-select controls
  (`:show-auto-select="false"`) and add NO section-side auto-select watcher.*
  Reason: the global weights store + always-mounted popover watcher mean a
  Settings-side auto-select would silently mutate the live session, and writing
  a persisted default on a slider drag is semantically wrong for a defaults
  editor. The sliders still guide a manual pick via the solid "best" ring. See
  §3.1 for the full rationale and the accepted residual.
- **D2 — Default-dot legend.** Section passes `defaultCell = null` (the selected
  cell *is* the default; a redundant dot would confuse). Matrix hides the "•
  default" legend entry when no default cell exists (§3.4). ✓ chosen.
- **D3 — Group label above the matrix.** Add a subtle "Default model & effort"
  label (the section is a defaults panel; the popover has none because context
  makes it obvious). Switch labels stay **bare** (`Thinking`, `1M context`, …)
  to match the popover rather than the old `Default thinking` phrasing. ✓ chosen,
  low-confidence — reviewer may prefer keeping "Default …" phrasing.
- **D4 — Shared-component folder.** Keep the 3 shared components in
  `components/message/` (minimal churn) vs. move to a neutral
  `components/agentSettings/`. ✓ chosen: keep in place, cross-dir import.
- **D5 — Permission select not shared** (§3.5). ✓ chosen.
- **D7 — Codex loses its "Default context size" select (intentional).** Today the
  section renders a context select for Codex; the new design routes context only
  through the switch toggle, hidden for Codex (`fieldHasChoice` false — window
  fixed by the model). Net removal of an unpickable control. ✓ accepted as an
  improvement, called out as a deliberate user-visible change.
- **D6 — Codex context default drift.** Codex `setDefaultValue('selected_model')`
  does not re-pin `context_max` to the new model's window (pre-existing; context
  is derived from the model at runtime via `getEffectiveContextMax`, so a stale
  stored default is inert). Not addressed here. ✓ acknowledged, out of scope.

## 7. Risks & mitigations
- **Popover regression** from the two extractions. Mitigation: strictly
  behaviour-preserving refactor; diff-review the popover output; verify the live
  popover (matrix pick, switches, notices, context toggle, permission) unchanged.
- **Circular imports (HMR).** `utils/agentMatrix.js` imports `providers/index`
  (already done by the composable; providers don't import back) — safe. New
  component imports are leaf-ward. Verify no Vite full-reload appears.
- **`.settings-sections .setting-group` global CSS** applies `margin-left` to
  non-label children — keep the matrix/weights/switches **outside** that
  `.setting-group` pattern (own wrapper class) to avoid stray indentation.
- **Matrix width on the ~460px pane / mobile 90vw.** Matrix columns are
  `minmax(2.25rem, 1fr)` — responsive; verify at narrow width.

## 8. Implementation outcome (built + reviewed)

Implemented as specified. Main Vite build is green (imports resolve, SFCs
compile). Two independent review passes:

- **Popover/composable regression review** — verdict *preserved*: the matrix +
  switch-row extractions are byte-for-byte identical to the pre-refactor inline
  code; the popover feeds the same inputs; reactivity and ids are intact. No
  divergence.
- **New-section correctness review** — verdict *correct-with-fixes*. One
  actionable finding, fixed: an explicit `<wa-divider>` after the
  matrix/weights/switches group stacked on top of the weights component's own
  trailing divider, producing a **double divider on Codex** (empty switch row)
  and one divider more than the popover. Removed it — permission now follows the
  group with only the section gap, matching the popover for both providers. The
  other notes were confirmed by-design (D1 residual; the global weights block is
  never actually duplicated on screen — only the active provider section mounts,
  gated by `v-if="activeSection === section.id"`) or negligible (sub-second
  pre-registry-seed Codex context toggle, inert).
```

