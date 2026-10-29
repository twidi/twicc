// Pure helpers of the simple model & effort picker (provider / model / effort),
// working on the ``blocks`` + ``effortColumns`` built by ``utils/agentMatrix.js``
// so the simple mode and the matrix always agree on what exists and what is
// selected.

/**
 * The block whose cells may be selected (the session's provider), else the first.
 *
 * @param {Array} blocks
 * @returns {object|null}
 */
export function currentBlock(blocks) {
    return blocks.find(b => b.isCurrent) ?? blocks[0] ?? null
}

/**
 * The selected row of a block (the one holding the ``selected`` cell), or null
 * when the selection matches no listed model.
 */
export function selectedRow(block) {
    return block?.rows.find(r => r.cells.some(c => c.selected)) ?? null
}

/** The selected effort of a row, or null. */
export function selectedEffort(row) {
    return row?.cells.find(c => c.selected)?.effort ?? null
}

/**
 * Models of the slider: the latest ones with at least one usable effort, from
 * the weakest to the strongest. The registry lists the strongest first, so the
 * order is reversed.
 */
export function sliderRows(block) {
    return (block?.rows ?? [])
        .filter(r => r.isLatest && r.cells.some(c => c.enabled))
        .reverse()
}

/** Efforts a row supports, in ladder order (low → max). */
export function enabledEfforts(row) {
    return (row?.cells ?? []).filter(c => c.enabled).map(c => c.effort)
}

/**
 * The effort to use after the model changed: the wanted one when the new model
 * supports it, else the closest supported one on the ladder (the lower on a tie).
 *
 * @param {object} row              The new model's row.
 * @param {{effort:any}[]} columns  The shared effort ladder.
 * @param {*} wanted                The effort selected before the change.
 * @returns {*} an effort, or null when the model supports none
 */
export function effortForModel(row, columns, wanted) {
    const supported = enabledEfforts(row)
    if (!supported.length) return null
    if (supported.includes(wanted)) return wanted
    const rank = effort => columns.findIndex(c => c.effort === effort)
    const wantedRank = rank(wanted)
    if (wantedRank < 0) return supported[0]
    let best = supported[0]
    for (const effort of supported) {
        if (Math.abs(rank(effort) - wantedRank) < Math.abs(rank(best) - wantedRank)) best = effort
    }
    return best
}

/**
 * The (model, effort) to select when the user switches to another provider's
 * block: that provider's default cell when the matrix flags one, else its
 * strongest latest model at its middle supported effort.
 *
 * @returns {{model:*, effort:*}|null}
 */
export function providerStartSelection(block) {
    if (!block) return null
    for (const row of block.rows) {
        const cell = row.cells.find(c => c.enabled && (c.isDefault || c.isProviderDefault))
        if (cell) return { model: row.model, effort: cell.effort }
    }
    const row = [...sliderRows(block)].pop() ?? block.rows.find(r => r.cells.some(c => c.enabled))
    if (!row) return null
    const efforts = enabledEfforts(row)
    return { model: row.model, effort: efforts[Math.floor((efforts.length - 1) / 2)] }
}
