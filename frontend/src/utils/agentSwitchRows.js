import { i18n } from '../i18n/index.js'

// Switch/toggle row building for the agent-settings surfaces — shared between
// the per-session popover (``AgentSettingsPopover``) and the per-provider
// defaults editor (``ProviderSettingsSection``). Extracted from the popover's
// ``settingRows`` so both build the compact switch row identically from the
// provider helper hooks. Pure over ``helpers`` + two callbacks; the caller owns
// the reactive plumbing (wrap in ``computed``).
//
// The output feeds ``AgentSettingsSwitches.vue`` (``rows`` prop).

// Fields rendered as switches/toggles, in order. ``selected_model`` / ``effort``
// are owned by the matrix; ``permission_mode`` stays a select (host-specific).
//   • toggle — two-value bi-label wa-switch (context: big value on, small off);
//              hidden when the field offers a single choice.
//   • switch — boolean wa-switch (thinking, Chrome MCP, fast mode).
export const SWITCH_FIELD_WIDGETS = [
    { field: 'context_max', kind: 'toggle' },
    { field: 'thinking_enabled', kind: 'switch' },
    { field: 'fast_mode', kind: 'switch' },
    { field: 'claude_in_chrome', kind: 'switch' },
]

/**
 * Build the switch/toggle rows for a provider's helpers.
 *
 * Switches/toggles are shown only when the feature is actually toggleable
 * (``fieldHasChoice``): context with no 1M / fixed window, thinking that can't
 * be disabled, and fast mode on unsupported models are dropped entirely. A
 * ``notice`` (icon + tooltip) surfaces what the compact switch can't spell out.
 *
 * @param {Object} helpers - The provider's BaseProviderHelpers instance.
 * @param {Object} opts
 * @param {{field:string,kind:'toggle'|'switch'}[]} [opts.fields]  Defaults to
 *        ``SWITCH_FIELD_WIDGETS``.
 * @param {(field:string)=>Object} opts.fieldContext  Per-field render context
 *        passed to the helper hooks (``effectiveModel``, ``isStarting``,
 *        ``isContextMaxForced``, ``selectedValue``, ``defaultValue``, …).
 * @param {(field:string)=>*} opts.valueFor  Current effective value of the field
 *        (drives ``checked`` and the context toggle's on/off pick).
 * @returns {Array} rows for ``AgentSettingsSwitches``.
 */
export function buildSwitchRows(helpers, { fields = SWITCH_FIELD_WIDGETS, fieldContext, valueFor }) {
    if (!helpers) return []
    const rows = []
    for (const { field, kind } of fields) {
        if (!helpers.supportsAgentSetting(field)) continue
        const ctx = fieldContext(field)
        const label = helpers.getFieldLabel(field)
        if (kind === 'toggle') {
            if (!helpers.fieldHasChoice(field, ctx)) continue
            // Two selectable values ordered small→big. Rendered as a single
            // switch labelled after the big value ("1M context"): ON selects the
            // big value, OFF the small one. When the runtime forced the big value
            // (context auto-promote), reflect it as ON regardless of the stored
            // value — the field is disabled and carries a warning notice.
            const choices = [...helpers.getFieldChoices(field)].sort((a, b) => Number(a.value) - Number(b.value))
            const small = choices[0]
            const big = choices[choices.length - 1]
            rows.push({
                field, kind,
                toggleLabel: i18n.global.t('session.agentSettings.contextWindow', { size: big.label }),
                bigValue: big.value,
                smallValue: small.value,
                checked: valueFor(field) === big.value || !!ctx.isContextMaxForced,
                disabled: helpers.isFieldDisabled(field, ctx),
                notice: helpers.getFieldNotice(field, ctx),
            })
        } else if (kind === 'switch') {
            // Only toggleable switches reach here (fieldHasChoice); the disabled
            // state left is the transient startup lock.
            if (!helpers.fieldHasChoice(field, ctx)) continue
            rows.push({
                field, kind, label,
                checked: valueFor(field) === true,
                disabled: helpers.isFieldDisabled(field, ctx),
                notice: helpers.getFieldNotice(field, ctx),
            })
        }
    }
    return rows
}
