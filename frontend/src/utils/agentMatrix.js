// Provider × model × effort matrix data building — shared, provider-agnostic,
// session-agnostic. Extracted from ``useSessionAgentSettings`` so both the
// per-session agent-settings popover AND the per-provider defaults editor in
// the Settings panel (``ProviderSettingsSection``) build their matrix the same
// way. Pure functions over the provider helpers + the benchmarks store; the
// caller owns the reactive plumbing (wrap these in ``computed``).
//
// The output feeds ``AgentSettingsMatrix.vue`` directly (``blocks`` +
// ``effortColumns`` props).
import { getProviderHelpers, getProviderLabel, getProviderIcon } from '../providers'

/**
 * Shared effort columns for the matrix: the union of the given providers'
 * effort choices, first-provider-first so the ladder reads low→high→…→max
 * (→ultra). Each column carries its label.
 *
 * @param {string[]} providers - Provider wire keys, in display order.
 * @returns {{ effort: any, label: string }[]}
 */
export function buildEffortColumns(providers) {
    const seen = new Map()
    for (const provider of providers) {
        const helpers = getProviderHelpers(provider)
        for (const choice of helpers?.getFieldChoices('effort') ?? []) {
            if (!seen.has(choice.value)) seen.set(choice.value, choice.label ?? String(choice.value))
        }
    }
    return [...seen.entries()].map(([effort, label]) => ({ effort, label }))
}

/**
 * Build the matrix ``blocks`` — one block per provider, each a grid of models
 * (rows) × the shared effort columns, with per-cell enabled / selected /
 * default / benchmark-score state.
 *
 * @param {Object} opts
 * @param {string[]} opts.providers        Provider wire keys, in display order.
 * @param {{effort:any,label:string}[]} opts.effortColumns  From ``buildEffortColumns``.
 * @param {string|null|undefined} opts.currentProvider  The provider whose block
 *        may show a ``selected`` cell (the session's provider; for the defaults
 *        editor, the single provider being edited).
 * @param {*} opts.selectedModel   Model of the cell to mark ``selected`` (only in
 *        the current provider's block).
 * @param {*} opts.selectedEffort  Effort of the cell to mark ``selected``.
 * @param {{provider:string,model:*,effort:*}|null} opts.defaultCell  The cell to
 *        mark ``isDefault`` (filled dot) — the default provider's default, or
 *        ``null`` for none (defaults editor: the selected cell IS the default).
 * @param {{provider:string,model:*,effort:*}[]} [opts.providerDefaults]  Each
 *        shown provider's own default cell; a match that is NOT the main default
 *        is flagged ``isProviderDefault`` (hollow dot), so every provider's
 *        default stays referenced when several blocks are shown. Default: none.
 * @param {{getScore:(p:string,fullName:string,effort:any)=>number|null}} opts.benchmarksStore
 * @returns {Array} blocks for ``AgentSettingsMatrix``.
 */
export function buildMatrixBlocks({
    providers,
    effortColumns,
    currentProvider,
    selectedModel,
    selectedEffort,
    defaultCell,
    providerDefaults = [],
    benchmarksStore,
}) {
    const columns = effortColumns
    const def = defaultCell
    const nProviders = providers.length
    return providers.map(provider => {
        const helpers = getProviderHelpers(provider)
        // Retired models are dropped, not greyed out: the "show older models"
        // toggle covers models that are still valid but no longer their
        // family's latest, which a retired one is not. A merely disabled model
        // stays listed (greyed) — it can come back, a retired one cannot.
        const registry = (helpers?.getModelRegistry() ?? []).filter(e => !helpers.isModelRetired(e))
        const supported = new Set((helpers?.getFieldChoices('effort') ?? []).map(c => c.value))
        const isCurrent = provider === currentProvider
        // This provider's own default (hollow dot when it isn't the main one).
        const blockDefault = providerDefaults.find(pd => pd.provider === provider) ?? null
        const rows = registry.map(entry => {
            const model = entry.selected_model
            const modelEnabled = entry.enabled !== false
            const cells = columns.map(col => {
                const effort = col.effort
                const enabled = modelEnabled
                    && supported.has(effort)
                    && !helpers.isChoiceDisabled('effort', effort, { effectiveModel: model })
                const isDefault = !!def && provider === def.provider && model === def.model && effort === def.effort
                return {
                    effort,
                    enabled,
                    selected: isCurrent && model === selectedModel && effort === selectedEffort,
                    isDefault,
                    // Another provider's default (hollow dot): matches this block's
                    // default but isn't the main (filled) one.
                    isProviderDefault: !isDefault && !!blockDefault
                        && model === blockDefault.model && effort === blockDefault.effort,
                    // Benchmark score joins on the internal SDK id (full_name),
                    // not the picker alias (selected_model). null -> "?".
                    score: benchmarksStore.getScore(provider, entry.full_name, effort),
                    // Raw benchmark row (same join) for the cell's details
                    // tooltip; null when the benchmark doesn't cover the pair.
                    benchmark: benchmarksStore.getRow(provider, entry.full_name, effort),
                }
            })
            // Split name + version into two grid spans. The short label carries
            // the version for non-latest aliases (e.g. "Opus 4.7"); strip it so
            // the name span holds just the tier and the version renders on its
            // own, uniformly for latest and older models.
            const short = helpers.getModelShortLabel(model)
            const version = entry.version
            const name = version && short.endsWith(version)
                ? short.slice(0, -version.length).trim()
                : short
            return { model, name, version, isLatest: entry.latest === true, cells }
        })
        // Top-score border: mark the block's best-scoring enabled cell(s). A
        // single provider (or the default provider when several are shown) gets
        // a solid border; each other provider gets a dashed one — so the user
        // spots the best score for their provider and for each other.
        let maxScore = null
        for (const row of rows) for (const cell of row.cells) {
            if (cell.enabled && cell.score != null && (maxScore === null || cell.score > maxScore)) {
                maxScore = cell.score
            }
        }
        if (maxScore !== null) {
            const borderStyle = (nProviders < 2 || provider === def?.provider) ? 'solid' : 'dashed'
            for (const row of rows) for (const cell of row.cells) {
                if (cell.enabled && cell.score === maxScore) cell.borderStyle = borderStyle
            }
        }
        return { provider, label: getProviderLabel(provider), icon: getProviderIcon(provider), isCurrent, rows }
    })
}
