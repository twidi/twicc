# GPT-5.6 Codex Models (Sol / Terra / Luna) — Implementation Plan

**Goal:** Add OpenAI's GPT-5.6 model family — the Sol, Terra and Luna tiers — to the Codex provider, including the two new Sol-only reasoning-effort levels (`max`, `ultra`), fallback pricing, and the effort-gating machinery Codex does not have today.

**Architecture:** Codex's model pipeline is data-driven from a single catalogue (`MODEL_VERSIONS`). Adding the three tiers there propagates automatically to the bootstrap registry, the frontend selector, CLI/MCP validation, `--help`, `twicc info`, and `resolve_sdk_model`. The real work is the **Sol-only effort gating**: Codex currently has an empty `CONSTRAINT_FLAG_MAPPING` and inherits the base `enforce_agent_settings_consistency`, which only substitutes an unavailable *model* — never demotes an *effort*. We replicate the Claude Code capability-flag pattern (`provider_extra` → `serialize_model_extra` → front `modelSupports*` + backend demotion cascade).

**Status: executed 2026-07-09.** Two assumptions below turned out to be wrong; both were corrected in the code, and the CLI — not the launch coverage — settled them.

1. **The effort matrix is NOT Sol-only.** The running CLI is authoritative (`model/list` → `supportedReasoningEfforts`): `max` is taken by Sol **and Terra and Luna**; `ultra` by Sol **and Terra**. Every press article cited here claimed "Sol only" — they were wrong. The demotion cascade needed no change: Luna's `ultra` correctly lands on `max` instead of falling to `xhigh`, which is exactly why the two steps were kept distinct.
2. **The SDK needed a two-value patch** (not "out of scope", as assumed here). The vendored `ReasoningEffort` enum (rust-v0.144.0) lacked `max`/`ultra` **while its own binary emitted them**, so pydantic rejected `model/list` outright, and `ReasoningEffort("ultra")` fell into a silent `except ValueError` that swapped the user's effort for the CLI default. The enum was patched and that fallback raised from `warning` to `error`.

The model slugs (`gpt-5.6-{sol,terra,luna}`) were confirmed verbatim against the CLI.

---

## Background (research summary)

GPT-5.6 introduces a **new naming axis**: the number is the generation (5.6), the name is a durable capability tier — replacing the old `mini`/`nano` suffixes. Three tiers, all available via the OpenAI API and Codex, public GA on 2026-07-09 (no preview gating to model — accessible to all).

| Tier | Slug (`full_name`) | Role | Price /1M (in / out) | `max`/`ultra` effort |
|------|--------------------|------|----------------------|----------------------|
| Sol  | `gpt-5.6-sol`   | Flagship — hardest coding / security | $5.00 / $30.00 | **Yes** |
| Terra| `gpt-5.6-terra` | Balanced everyday / high-volume | $2.50 / $15.00 | No |
| Luna | `gpt-5.6-luna`  | Fast & cheap | $1.00 / $6.00 | No |

- **`max`**: deepest single-agent reasoning (top of the effort dial). **Sol only.**
- **`ultra`**: spawns subagents to parallelise. Expressed in `~/.codex/config.toml` as `model_reasoning_effort = "ultra"` — i.e. Codex treats it as an effort value. **Sol only.** TwiCC only exposes the model + its available options; token budgets / subagent orchestration are the user's concern, not TwiCC's.
- **Context window**: not officially published (unofficial ~1.5M for Sol). **Do not touch `context_max` (272k)** until the model card confirms it.
- **Slugs to confirm**: `gpt-5.6-{sol,terra,luna}` come from third-party guides, not OpenAI's official docs. The SDK work will confirm the exact ids the CLI accepts.

Sources: OpenAI preview announcement, VentureBeat, DataCamp, Codex Knowledge Base (danielvaughan), OpenAI Developers (Codex subagents).

---

## Key architecture facts (verified in code)

1. **One catalogue drives everything.** `MODEL_VERSIONS` (`codex/constants.py:123`) → `serialize_model_registry()` → bootstrap → front selector, CLI validation (`_drop_request/validation.py`), `--help`, `twicc info models/agent-settings`, `find_model`/`resolve_sdk_model`. Adding entries there is the bulk of the model exposure.
2. **Pricing families auto-parse.** `extract_model_info("gpt-5.6-sol")` → family `gpt-sol`, version `5.6` (the `sol` suffix becomes the family). Both parsers — JSONL (`codex/pricing.py:44`) and OpenRouter (`codex/helpers.py:262`) — agree. So the pricing bucket for each tier is `gpt-sol`/`gpt-terra`/`gpt-luna`.
3. **Fallback pricing is required.** `DEFAULT_FAMILY_PRICES` (`codex/helpers.py:156`) is the fallback when no `ModelPrice` row matches. New families not yet on OpenRouter would otherwise cost `None`. Must add all three.
4. **Effort gating does not exist for Codex.** `CONSTRAINT_FLAG_MAPPING` is `{}` (base default, `helpers.py:290`); Codex inherits the base `enforce_agent_settings_consistency` (`helpers.py:1102`) which only substitutes an unavailable model. To make `max`/`ultra` Sol-only we must add: `provider_extra` flags, `serialize_model_extra`, the mapping, backend demotion, and the front capability methods.
5. **Alias `max` (effort) adapts to the model — proven for Claude Code.** `--effort max` resolves native-first to the literal `max`, then `enforce_agent_settings_consistency` demotes it in a cascade against the resolved model's flags (`claude_code/helpers.py:599`). We replicate this cascade for Codex (`ultra → max → xhigh`).
6. **Front gating goes through `isChoiceDisabled` + `enforceAgentSettingsConsistency`, not gate flags.** The `isEffort*Available` fields in `MessageInput.vue getSessionGateState()` (`:1785`) are **vestigial — read nowhere** (verified by full-tree grep). The real greying-out hook is `helpers.isChoiceDisabled(field, choiceValue, context)` (base `baseHelpers.js:683`; Claude override `claude_code/helpers.js:535`), called with `context.effectiveModel` by every settings surface (`AgentSettingsPopover.vue`, `ProviderSettingsSection.vue`, `SessionView.vue:1839`, `staticCommands.js`). The matching live-selection demotion is `enforceAgentSettingsConsistency` (base `baseHelpers.js:430`; Claude override `:461`), called by `useSessionAgentSettings.js:482`. Codex overrides **neither** and has no `modelSupports*`/`_resolveRegistryEntry` helpers — all must be added. The vestigial `isEffort*Available` gate flags in `MessageInput.vue` are dead and get removed as cleanup (§5); `SessionView.vue` needs no change.
7. **`getModelLabel` needs no change.** `getModelLabel("gpt-sol")` → "GPT sol", exactly analogous to the existing "GPT mini". Sol/Terra/Luna are the same tier axis as `mini` vs bare.

---

## Open decisions (defaults chosen — confirm or override before executing)

| # | Decision | Default in this plan | Alternative |
|---|----------|----------------------|-------------|
| D1 | Cross-family `weight`s (must be unique) | Sol 130 / Terra 120 / Luna 110 (5.6 above gpt-5.5=100) | Any unique ordering |
| D2 | Global default model `codexDefaultModel` | **`gpt-terra`** (5.6, competitive with 5.5 at 2× less cost) — chosen | `gpt` (5.5) |
| D3 | Semantic model aliases | `max`/`strongest`→`gpt-sol`, `medium`/`balanced`→`gpt-terra`, `min`/`fastest`/`cheapest`→ keep `gpt-mini` (truly cheapest) | Keep all three on the old targets |
| D4 | Effort alias `max` | `max`→`max` (native identity, cascade-demoted per model) | keep `max`→`xhigh` |
| D5 | Model label casing | lowercase "GPT sol" (matches "GPT mini") — no code change | custom-case "GPT Sol" (needs a `getModelLabel` special case) |
| D6 | `ultra` alias | none — `ultra` reachable only as an explicit effort value | add a `max`→`ultra` alias |

All defaults below encode D1–D6 as chosen. Changing D2/D3 only touches the two alias/default blocks in `codex/constants.py`.

---

## File-by-file changes

### Backend

#### 1. `src/twicc/providers/codex/constants.py`

**1a. Import `NamedTuple`** (top of file, with the other imports):

```python
from typing import NamedTuple
```

**1b. Add the capability-flag type** (just above `MODEL_VERSIONS`, ~line 117):

```python
class CodexModelExtra(NamedTuple):
    """Capability flags carried in :attr:`ModelVersion.provider_extra` for Codex.

    GPT-5.6 introduced two reasoning-effort levels beyond ``xhigh`` — ``max``
    (deepest single-agent reasoning) and ``ultra`` (subagent parallelisation) —
    unlocked only by the Sol tier. Every other tier (and the pre-5.6 families)
    leaves both False, so the shared effort-gating machinery
    (``CONSTRAINT_FLAG_MAPPING`` + ``enforce_agent_settings_consistency``)
    demotes an out-of-range effort automatically. Mirrors
    ``claude_code.constants.ClaudeCodeModelExtra``.
    """
    supports_effort_max: bool
    supports_effort_ultra: bool
```

**1c. Replace `MODEL_VERSIONS`** (lines 123-154) — add the three tiers first (order is cosmetic; the registry sorts by weight) and give every entry a `provider_extra` (required so `serialize_model_extra` never dereferences `None`, and so the front reads explicit `false` flags):

```python
MODEL_VERSIONS: list[ModelVersion] = [
    ModelVersion(
        provider=Provider.CODEX, model="gpt-sol", version="5.6",
        full_name="gpt-5.6-sol", retirement_date=None, latest=True, weight=130,
        provider_extra=CodexModelExtra(supports_effort_max=True, supports_effort_ultra=True),
    ),
    ModelVersion(
        provider=Provider.CODEX, model="gpt-terra", version="5.6",
        full_name="gpt-5.6-terra", retirement_date=None, latest=True, weight=120,
        provider_extra=CodexModelExtra(supports_effort_max=False, supports_effort_ultra=False),
    ),
    ModelVersion(
        provider=Provider.CODEX, model="gpt-luna", version="5.6",
        full_name="gpt-5.6-luna", retirement_date=None, latest=True, weight=110,
        provider_extra=CodexModelExtra(supports_effort_max=False, supports_effort_ultra=False),
    ),
    ModelVersion(
        provider=Provider.CODEX, model="gpt", version="5.5",
        full_name="gpt-5.5", retirement_date=None, latest=True, weight=100,
        provider_extra=CodexModelExtra(supports_effort_max=False, supports_effort_ultra=False),
    ),
    ModelVersion(
        provider=Provider.CODEX, model="gpt", version="5.4",
        full_name="gpt-5.4", retirement_date=None, latest=False, weight=90,
        provider_extra=CodexModelExtra(supports_effort_max=False, supports_effort_ultra=False),
    ),
    ModelVersion(
        provider=Provider.CODEX, model="gpt-mini", version="5.4",
        full_name="gpt-5.4-mini", retirement_date=None, latest=True, weight=50,
        provider_extra=CodexModelExtra(supports_effort_max=False, supports_effort_ultra=False),
    ),
]

assert_unique_weights(MODEL_VERSIONS)
```

**1d. Effort alias** — in `AGENT_SETTINGS_ALIASES` (line 96-98), repoint `max` (D4). `max` becomes a native effort value, so native-first keeps it and the demotion cascade adapts it per model:

```python
    "effort": {
        "min": "low", "max": "max",
    },
```

**1e. Model aliases (D3)** — in `AGENT_SETTINGS_ALIASES["selected_model"]` (lines 91-95):

```python
    "selected_model": {
        "min": "gpt-mini", "fastest": "gpt-mini", "cheapest": "gpt-mini",
        "medium": "gpt-terra", "balanced": "gpt-terra",
        "max": "gpt-sol", "strongest": "gpt-sol",
    },
```

**1f. (D2) Global default** — set `SYNCED_SETTINGS_DEFAULTS["codexDefaultModel"] = "gpt-terra"` (line 22): new Codex sessions default to Terra (5.6, competitive with 5.5 at half the cost). The default effort stays `medium`, which is valid on every tier, so no `enforce` demotion is triggered on a fresh default session.

#### 2. `src/twicc/providers/codex/helpers.py`

**2a. Imports** — add `Any` to the typing import (line 14) and `AgentSettings` to the `twicc.providers.helpers` import block (lines 21-27):

```python
from typing import TYPE_CHECKING, Any, ClassVar
...
from twicc.providers.helpers import (
    AgentSettingCategory,
    AgentSettings,
    BaseProviderHelpers,
    IndexableMessage,
    ModelVersion,
    UserMessage,
)
```

**2b. Effort choices** — in module-level `AGENT_SETTINGS_CHOICES` (line 77):

```python
    "effort": ["low", "medium", "high", "xhigh", "max", "ultra"],
```

**2c. Constraint mapping** — add a `ClassVar` on `CodexHelpers` (next to `OPENROUTER_MODEL_PREFIX` / `DEFAULT_FAMILY_PRICES`, ~line 145):

```python
    # Per-(field, value) capability flag gating the value against the model.
    # Sol is the only tier that unlocks GPT-5.6's ``max`` / ``ultra`` efforts.
    CONSTRAINT_FLAG_MAPPING: ClassVar[dict[tuple[str, Any], str]] = {
        ("effort", "ultra"): "supports_effort_ultra",
        ("effort", "max"):   "supports_effort_max",
    }
```

**2d. Fallback pricing** — add three entries to `DEFAULT_FAMILY_PRICES` (line 156), and update the table's docstring (`codex/helpers.py:149-155`), which currently restricts it to `gpt`/`gpt-codex`/`gpt-codex-max` (the tiers now belong there — they run under Codex CLI). `cache_read` = 10% of input (matching the existing `gpt` entry); `cache_write` = 0 (OpenRouter doesn't expose it for OpenAI):

```python
        "gpt-sol": FamilyPrices(  # gpt-5.6-sol
            input_price=Decimal("5.00"), output_price=Decimal("30.00"),
            cache_read_price=Decimal("0.50"),
            cache_write_5m_price=Decimal("0"), cache_write_1h_price=Decimal("0"),
        ),
        "gpt-terra": FamilyPrices(  # gpt-5.6-terra
            input_price=Decimal("2.50"), output_price=Decimal("15.00"),
            cache_read_price=Decimal("0.25"),
            cache_write_5m_price=Decimal("0"), cache_write_1h_price=Decimal("0"),
        ),
        "gpt-luna": FamilyPrices(  # gpt-5.6-luna
            input_price=Decimal("1.00"), output_price=Decimal("6.00"),
            cache_read_price=Decimal("0.10"),
            cache_write_5m_price=Decimal("0"), cache_write_1h_price=Decimal("0"),
        ),
```

**2e. Capability helpers + serialize + demotion** — add these methods to `CodexHelpers` (mirror `claude_code/helpers.py:399-463`, `:696-698`, `:566-627`):

```python
    def _resolve_to_default_model_version(self) -> ModelVersion | None:
        """Return the :class:`ModelVersion` for the synced default model.

        Defensive fallback for capability checks when the caller passes
        ``None`` or an unknown model. ``None`` if the default itself is
        missing/unknown. Mirrors the Claude Code helper.
        """
        from twicc.synced_settings import SYNCED_SETTINGS_DEFAULTS, read_synced_settings
        default_model = (
            read_synced_settings().get("codexDefaultModel")
            or SYNCED_SETTINGS_DEFAULTS.get("codexDefaultModel")
        )
        if not default_model:
            return None
        return self.find_model(default_model)

    def selected_model_supports_effort_max(self, selected_model: str | None) -> bool:
        """True if the model (or default fallback) unlocks the ``"max"`` effort."""
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra and mv.provider_extra.supports_effort_max)

    def selected_model_supports_effort_ultra(self, selected_model: str | None) -> bool:
        """True if the model (or default fallback) unlocks the ``"ultra"`` effort."""
        mv = self.find_model(selected_model) if selected_model else None
        if mv is None:
            mv = self._resolve_to_default_model_version()
        return bool(mv and mv.provider_extra and mv.provider_extra.supports_effort_ultra)

    def serialize_model_extra(self, mv: ModelVersion) -> dict:
        """Expose Codex's :class:`CodexModelExtra` flags on the wire."""
        return mv.provider_extra._asdict() if mv.provider_extra else {}

    def enforce_agent_settings_consistency(self, settings: AgentSettings) -> AgentSettings:
        """Substitute an unavailable model, then demote a Sol-only effort.

        1. Base substitution of a disabled/retired ``selected_model``.
        2. Demote ``effort == "ultra"`` → ``"max"`` (or ``"xhigh"`` when the
           model unlocks neither), then ``effort == "max"`` → ``"xhigh"``.
           ``xhigh`` and below are universal for Codex, so the cascade stops.
        """
        settings = super().enforce_agent_settings_consistency(settings)

        model = settings.selected_model
        effort = settings.effort

        if effort == "ultra" and not self.selected_model_supports_effort_ultra(model):
            effort = "max" if self.selected_model_supports_effort_max(model) else "xhigh"
        if effort == "max" and not self.selected_model_supports_effort_max(model):
            effort = "xhigh"

        if effort == settings.effort:
            return settings
        return settings._replace(effort=effort)
```

**2f. Default-settings normalisation** — Codex has no `enforce_synced_settings_consistency`. With `max` now native (D4), `twicc settings provider codex --effort max` stores `codexDefaultEffort = "max"` even when `codexDefaultModel` is a non-Sol tier — a misleading global default that would render greyed in the settings select (§4c). Add an effort-only override on `CodexHelpers`, firing when either pivot changes, mirroring `claude_code/helpers.py:508-564`:

```python
    def enforce_synced_settings_consistency(self, synced: dict, changes: dict) -> None:
        if "codexDefaultModel" not in changes and "codexDefaultEffort" not in changes:
            return
        candidate = AgentSettings(
            selected_model=synced.get(
                "codexDefaultModel", self.SYNCED_SETTINGS_DEFAULTS["codexDefaultModel"]),
            effort=synced.get(
                "codexDefaultEffort", self.SYNCED_SETTINGS_DEFAULTS["codexDefaultEffort"]),
            context_max=synced.get(
                "codexDefaultContextMax", self.SYNCED_SETTINGS_DEFAULTS["codexDefaultContextMax"]),
            permission_mode=synced.get(
                "codexDefaultPermissionMode", self.SYNCED_SETTINGS_DEFAULTS["codexDefaultPermissionMode"]),
        )
        adjusted = self.enforce_agent_settings_consistency(candidate)
        if "codexDefaultEffort" in changes and adjusted.effort != candidate.effort:
            synced["codexDefaultEffort"] = adjusted.effort
```

Secondary in impact (per-session creation already demotes via `enforce_agent_settings_consistency`), but it keeps the stored global default coherent. Tracked as task T6.

### Frontend

#### 3. `frontend/src/providers/codex/constants.js` — `EFFORT` (line 41):

```js
export const EFFORT = {
    LOW: 'low',
    MEDIUM: 'medium',
    HIGH: 'high',
    X_HIGH: 'xhigh',
    MAX: 'max',
    ULTRA: 'ultra',
}
```

#### 4. `frontend/src/providers/codex/helpers.js`

**4a. Effort choices** — `AGENT_SETTINGS_CHOICES.effort` (line 114):

```js
    effort: [
        { value: EFFORT.LOW,    label: 'Low',    display_label: 'Low effort' },
        { value: EFFORT.MEDIUM, label: 'Medium', display_label: 'Medium effort' },
        { value: EFFORT.HIGH,   label: 'High',   display_label: 'High effort' },
        { value: EFFORT.X_HIGH, label: 'xHigh',  display_label: 'xHigh effort' },
        { value: EFFORT.MAX,    label: 'Max',    display_label: 'Max effort' },
        { value: EFFORT.ULTRA,  label: 'Ultra',  display_label: 'Ultra effort' },
    ],
```

**4b. Registry lookup + capability methods** — add to the `CodexHelpers` class (mirror `claude_code/helpers.js:394-418`; `useCodexStore` is already imported and used by `getModelRegistry`):

```js
    _resolveRegistryEntry(selectedModel) {
        const store = useCodexStore()
        const registry = store.modelRegistry
        let entry = selectedModel ? registry.find(e => e.selected_model === selectedModel) : undefined
        if (!entry) {
            const defaultModel = store.defaultModel
            if (defaultModel) entry = registry.find(e => e.selected_model === defaultModel)
        }
        return entry
    }

    modelSupportsEffortMax(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? !!entry.provider_extra?.supports_effort_max : false
    }

    modelSupportsEffortUltra(selectedModel) {
        const entry = this._resolveRegistryEntry(selectedModel)
        return entry ? !!entry.provider_extra?.supports_effort_ultra : false
    }
```

**4c. Effort gating (the real hook)** — add an `isChoiceDisabled` override (mirror `claude_code/helpers.js:535-548`; `super` handles the untrusted `permission_mode` clamp at `baseHelpers.js:683`):

```js
    isChoiceDisabled(field, choiceValue, context) {
        if (super.isChoiceDisabled(field, choiceValue, context)) return true
        if (field === 'effort') {
            if (choiceValue === EFFORT.MAX)   return !this.modelSupportsEffortMax(context?.effectiveModel)
            if (choiceValue === EFFORT.ULTRA) return !this.modelSupportsEffortUltra(context?.effectiveModel)
        }
        return false
    }
```

This is what every settings surface calls to grey an option — `AgentSettingsPopover.vue`, `ProviderSettingsSection.vue`, `SessionView.vue:1839`, `staticCommands.js` — passing `context.effectiveModel`. The base renders the `(not available)` suffix automatically; no `getChoiceDisabledReason` override is needed (Claude Code adds none for effort either).

**4d. Live-selection demotion** — add an `enforceAgentSettingsConsistency` override (mirror `claude_code/helpers.js:461-493`; called by `useSessionAgentSettings.js:482` on every model/effort change, so switching a Sol+ultra session to Terra lowers the effort immediately instead of waiting for the backend). Chain `super` (base substitutes the model), then demote:

```js
    enforceAgentSettingsConsistency(settings) {
        const result = super.enforceAgentSettingsConsistency(settings)
        const model = result.selectedModel
        if (result.effort === EFFORT.ULTRA && !this.modelSupportsEffortUltra(model)) {
            result.effort = this.modelSupportsEffortMax(model) ? EFFORT.MAX : EFFORT.X_HIGH
        }
        if (result.effort === EFFORT.MAX && !this.modelSupportsEffortMax(model)) {
            result.effort = EFFORT.X_HIGH
        }
        return result
    }
```

#### 5. `MessageInput.vue` — delete two dead flags (cleanup); no `SessionView.vue` change

Greying-out and live demotion are handled entirely by 4c/4d (via `context.effectiveModel`). The `isEffortXhighAvailable` / `isEffortMaxAvailable` fields in `getSessionGateState()` (`MessageInput.vue:1785-1786`) are **provably dead** and should be **deleted** in this pass:

- The only occurrences of the substring `isEffort` in all of `frontend/src` are those two definition lines (`rg -n "isEffort" frontend/src`) — no reader, no dynamic/string access, none in tests/stories.
- `getSessionGateState` flows to exactly one consumer (`SessionView.vue:1794` → `sessionSettingsGate()`), which reads `gate.isStarting` and passes `gate` whole as the `context` to `isChoiceDisabled`/`isFieldDisabled`; those read `context.effectiveModel` / `isStarting` / `isContextMaxForced`, never `isEffort*`.

Shared (non-Codex) component, but the lines are dead → removal is behaviour-neutral. Delete:

```js
        isEffortXhighAvailable: helpers?.modelSupportsEffortXhigh?.(model) ?? false,
        isEffortMaxAvailable: helpers?.modelSupportsEffortMax?.(model) ?? false,
```

Do NOT remove the `modelSupportsEffortXhigh`/`modelSupportsEffortMax` helper methods — they ARE used (by `isChoiceDisabled`/`enforceAgentSettingsConsistency`). Scope note: all six `getSessionGateState` fields were audited. The other four are live and must stay — `effectiveModel` (the model every gating hook reads), `isStarting` (`SessionView.vue:1796`), and `isContextMaxForced` + `isContextMaxForcedByModel` (`SessionView.vue:1902` gates the "Change Context Size" command; `isContextMaxForced` also drives the Claude Code `isFieldDisabled`/help hooks). Only the two `isEffort*` fields are dead — the context flags carry runtime state (usage-driven 1M forcing) not derivable from the model.

### Docs / tests / packaging

#### 7. `tests/test_pricing_parsing.py` — add to `CODEX_MODEL_CASES` (~line 169, after the `gpt` block):

```python
    # gpt-5.6 tiers — each tier is its own pricing family (suffix → family)
    ModelCase("gpt-sol",   "5.6", ("openai/gpt-5.6-sol",),   ("gpt-5.6-sol",)),
    ModelCase("gpt-terra", "5.6", ("openai/gpt-5.6-terra",), ("gpt-5.6-terra",)),
    ModelCase("gpt-luna",  "5.6", ("openai/gpt-5.6-luna",),  ("gpt-5.6-luna",)),
```

#### 8. Skills (hardcoded lists) — bump requires `plugin.json` version:

- `twicc-create-session/SKILL.md:64` & `twicc-update-session/SKILL.md:56` (`--model`): append `gpt-sol`, `gpt-terra`, `gpt-luna` to the Codex list.
- `twicc-create-session/SKILL.md:65` & `twicc-update-session/SKILL.md:57` (`--effort`): Codex becomes `low, medium, high, xhigh` + `max`, `ultra` (note: `max`/`ultra` are Sol-only, silently demoted on other tiers — mirror the Claude Code `context-max` "silently capped" wording). Preserve the existing value order.
- `src/twicc/agent/plugin/twicc/.claude-plugin/plugin.json`: `0.56.0` → `0.57.0` (new options → minor).

#### 9. `SKILLS-AND-CLI.md:118` — `--effort` line currently says `xhigh; Claude Code also max`. Update to note Codex now also has `max`/`ultra` (Sol-only). No hardcoded Codex model list exists there.

---

## Ordered task checklist

- [ ] **T1 — Catalogue.** Edit `codex/constants.py`: `CodexModelExtra`, `NamedTuple` import, `MODEL_VERSIONS` (all 6 with `provider_extra`), effort alias `max`→`max`, model aliases (D3), optional `codexDefaultModel` (D2).
- [ ] **T2 — Backend gating.** Edit `codex/helpers.py`: imports (`Any`, `AgentSettings`), `AGENT_SETTINGS_CHOICES` effort, `CONSTRAINT_FLAG_MAPPING`, `DEFAULT_FAMILY_PRICES`, the five methods (2e), optional 2f.
- [ ] **T3 — Backend check.** `TWICC_DATA_DIR=$PWD uv run python -m django shell -c "..."` or run the CLI: verify the registry + demotion (see Verification).
- [ ] **T4 — Pricing test.** Add the three `ModelCase`s; run `uv run --active pytest tests/test_pricing_parsing.py -q`.
- [ ] **T5 — Front.** Edit `codex/constants.js` (EFFORT + max/ultra) and `codex/helpers.js` (4a choices, 4b lookups, 4c `isChoiceDisabled`, 4d `enforceAgentSettingsConsistency`). Delete the two dead `isEffort*Available` lines in `MessageInput.vue:1785-1786` (§5). No `SessionView.vue` edit.
- [ ] **T6 — Global-default parity (§2f).** Add Codex `enforce_synced_settings_consistency` (effort-only). Secondary.
- [ ] **T7 — Docs/packaging.** Two `SKILL.md`s (model + effort lines), `plugin.json` bump, `SKILLS-AND-CLI.md:118`.
- [ ] **T8 — Verify end-to-end** (see below), then commit.

---

## Verification

Backend (no server needed):

```bash
# Registry surfaces the three tiers, sorted by weight:
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.helpers import CodexHelpers
h = CodexHelpers()
print([(e['selected_model'], e['weight'], e['provider_extra']) for e in h.serialize_model_registry()])
"

# Effort demotion adapts to the model:
TWICC_DATA_DIR=$PWD uv run python -c "
from twicc.providers.codex.helpers import CodexHelpers
from twicc.providers.helpers import AgentSettings
h = CodexHelpers()
for m in ['gpt-sol','gpt-terra','gpt-luna']:
    s = AgentSettings(selected_model=m, effort='ultra', context_max=272000, permission_mode='read_only')
    print(m, '->', h.enforce_agent_settings_consistency(s).effort)   # sol->ultra, terra/luna->xhigh
"
```

Expected: Sol keeps `ultra`; Terra/Luna demote to `xhigh`. Repeat with `effort='max'` → Sol keeps `max`, others `xhigh`.

```bash
uv run --active pytest tests/test_pricing_parsing.py -q        # all green incl. 3 new cases
```

Frontend (HMR, no build needed): open a Codex session — the model selector shows GPT sol / terra / luna; the effort select greys **Max/Ultra** on every tier except Sol (via `isChoiceDisabled`); switching a Sol session at `ultra` to Terra in the popover auto-lowers the effort to `xhigh` (via `enforceAgentSettingsConsistency`).

**Sanity — no SDK yet:** `resolve_sdk_model("gpt-sol")` returns `gpt-5.6-sol`; sending it will fail at `thread_start` until the SDK accepts it. That's expected and gated by the separate SDK work.

---

## Risks & dependencies

1. **SDK (blocking, external):** the vendored `openai_codex` must accept `gpt-5.6-{sol,terra,luna}` and `model_reasoning_effort ∈ {max, ultra}`. Verify where TwiCC passes the effort to `thread_start` (`codex/agent/manager.py` / `sdk_wrappers.py`) — the `ultra`/`max` value must reach `model_reasoning_effort` unmodified. Handled by the other agent.
2. **Slug confirmation:** `gpt-5.6-{sol,terra,luna}` are third-party-sourced. If OpenAI's actual ids differ, only `full_name` (T1) and the pricing/test cases (T4) change — everything else is derived.
3. **Context window:** intentionally left at 272k. Revisit only when the model card publishes an official figure; a per-tier `context_max` would then reuse the same `provider_extra` + `CONSTRAINT_FLAG_MAPPING` pattern.
4. **OpenRouter:** once the tiers appear on OpenRouter, the 24h sync creates real `ModelPrice` rows and the fallback (2d) is only a bootstrap safety net — no change needed.
