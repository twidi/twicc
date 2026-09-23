/**
 * Model × effort scores — the "target ability" model.
 *
 * Spec: docs/plans/2026-09-23-artificial-analysis-benchmark-scores-design.md §4.
 * Rows come from the frozen Artificial Analysis snapshot
 * (``data/modelBenchmarks.json``). The user picks a task type, a difficulty and
 * whether to favor cost or speed. The difficulty sets a target on the task
 * type's ability axis; each (model, effort) pays a squared distance penalty
 * when below the target (none above it) plus log2 of its cost (or time) per
 * task. The lowest penalty scores 100; every "doubling" worse halves the score.
 *
 * Pure module: no store, no Vue. The scoring-set predicate is injected so the
 * store decides which rows count (enabled providers, available models,
 * selectable efforts) while this module stays testable.
 */

// Exponent of the concave difficulty → target curve (the slider's middle lands high).
export const CURVE = 0.55
// Being this many Intelligence Index points below the target weighs as much as
// paying (or waiting) twice as much. Other task types scale it by their range.
export const TOLERANCE_II = 4

// Display name of each Artificial Analysis evaluation key.
export const EVAL_LABELS = {
    terminalbench_4_0: 'Terminal-Bench 4.0',
    scicode: 'SciCode',
    aa_briefcase: 'AA-Briefcase',
    gdpval_aa: 'GDPval-AA',
    gdp_pdf: 'GDP.pdf',
    automationbench_aa: 'AutomationBench-AA',
    humanitys_last_exam: "Humanity's Last Exam",
    critpt: 'CritPt',
    aa_omniscience: 'AA-Omniscience',
    aa_lcr: 'AA-LCR',
}

// Task types, in display order. ``evals: null`` = the Intelligence Index, with
// Terminal-Bench 4.0 cost and time.
export const TASK_TYPES = [
    { id: 'general', label: 'General (Intelligence Index)', evals: null },
    { id: 'coding', label: 'Coding & terminal', evals: ['terminalbench_4_0', 'scicode'] },
    { id: 'office', label: 'Office & knowledge work', evals: ['aa_briefcase', 'gdpval_aa', 'gdp_pdf'] },
    { id: 'automation', label: 'Automation & tools', evals: ['automationbench_aa'] },
    { id: 'science', label: 'Science & reasoning', evals: ['humanitys_last_exam', 'critpt', 'scicode'] },
    { id: 'knowledge', label: 'Factual knowledge', evals: ['aa_omniscience'] },
    { id: 'longdocs', label: 'Long documents', evals: ['aa_lcr'] },
]

// Evaluation scores not already a 0..1 rate: Elo as Artificial Analysis converts
// it, and the Omniscience index from -100..100.
const EVAL_NORMALIZERS = {
    aa_briefcase: s => (s - 500) / 2000,
    gdpval_aa: s => (s - 500) / 2000,
    aa_omniscience: s => (s + 100) / 200,
}

const DASH = '—'

const isNum = v => typeof v === 'number' && Number.isFinite(v)

// Mean of the values, or null as soon as one is missing.
function meanOrNull(values) {
    if (!values.length || !values.every(isNum)) return null
    return values.reduce((a, b) => a + b, 0) / values.length
}

/** Lookup key joining a benchmark row to a matrix cell. */
export function scoreKey(provider, model, effort) {
    return `${provider} ${model} ${effort}`
}

const rowKey = row => scoreKey(row.provider, row.model, row.effort)

/**
 * Ability (0..100), cost per task (USD) and time per task (s) of one row for a
 * task type; each is null when a needed value is missing (spec §4.1).
 */
export function typeMetrics(row, taskType) {
    const type = TASK_TYPES.find(t => t.id === taskType)
    if (!type) return { ability: null, cost: null, time: null }
    if (type.evals === null) {
        const tb = row.evals?.terminalbench_4_0
        return {
            ability: isNum(row.intelligence_index) ? row.intelligence_index : null,
            cost: isNum(tb?.cost_usd) ? tb.cost_usd : null,
            time: isNum(tb?.time_s) ? tb.time_s : null,
        }
    }
    const parts = type.evals.map(k => row.evals?.[k] ?? null)
    const scores = parts.map((p, i) => {
        if (!isNum(p?.score)) return null
        const normalize = EVAL_NORMALIZERS[type.evals[i]]
        return normalize ? normalize(p.score) : p.score
    })
    const ability = meanOrNull(scores)
    return {
        ability: ability === null ? null : 100 * ability,
        cost: meanOrNull(parts.map(p => p?.cost_usd)),
        time: meanOrNull(parts.map(p => p?.time_s)),
    }
}

// Lowest and highest ability over the rows having both an ability and a cost
// (cost in both modes, so the slider mapping never depends on Favor), or null.
function abilityRange(rows, taskType) {
    let min = Infinity
    let max = -Infinity
    for (const row of rows) {
        const m = typeMetrics(row, taskType)
        if (m.ability === null || m.cost === null) continue
        if (m.ability < min) min = m.ability
        if (m.ability > max) max = m.ability
    }
    return min === Infinity ? null : { min, max }
}

/**
 * Score every scorable row of the scoring set (spec §4.2–§4.5, §4.7).
 *
 * @param {Array<object>} rows - snapshot rows.
 * @param {{taskType: string, difficulty: number, favor: 'cost'|'speed'}} controls
 * @param {(row: object) => boolean} isInScoringSet - which rows count.
 * @returns {Map<string, {score: number, penalty: number, ability: number, target: number, metric: number}>}
 *   keyed by scoreKey; a missing key means "no score" ("?").
 */
export function computeBenchmarkScores(rows, { taskType, difficulty, favor }, isInScoringSet) {
    const result = new Map()
    const set = (rows ?? []).filter(isInScoringSet)
    const range = abilityRange(set, taskType)
    if (!range) return result

    const general = abilityRange(set, 'general')
    const typeSpan = range.max - range.min
    const generalSpan = general ? general.max - general.min : 0
    const tolerance = generalSpan > 0 ? TOLERANCE_II * typeSpan / generalSpan : TOLERANCE_II
    const target = range.min + typeSpan * Math.pow(difficulty / 100, CURVE)

    const scored = []
    for (const row of set) {
        const m = typeMetrics(row, taskType)
        const metric = favor === 'speed' ? m.time : m.cost
        if (m.ability === null || metric === null || !(metric > 0)) continue
        // No distance penalty above the target, nor on a flat range (tolerance 0).
        const below = typeSpan > 0 && m.ability < target
        const distance = below ? ((target - m.ability) / tolerance) ** 2 : 0
        scored.push({ row, ability: m.ability, metric, penalty: distance + Math.log2(metric) })
    }
    if (!scored.length) return result

    const minPenalty = Math.min(...scored.map(s => s.penalty))
    for (const s of scored) {
        result.set(rowKey(s.row), {
            score: Math.round(100 * Math.pow(2, minPenalty - s.penalty)),
            penalty: s.penalty,
            ability: s.ability,
            target,
            metric: s.metric,
        })
    }
    return result
}

/**
 * Build the scoring-set predicate (spec §4.3): the row's provider is enabled,
 * its model is an available registry entry of that provider (enabled, not
 * retired), and its effort is selectable for that model — the same test that
 * enables a matrix cell (``agentMatrix.js``).
 *
 * @param {string[]} enabledProviders
 * @param {(provider: string) => object|null} getHelpers - provider helpers lookup.
 */
export function makeScoringSetPredicate(enabledProviders, getHelpers) {
    const enabled = new Set(enabledProviders ?? [])
    return (row) => {
        if (!enabled.has(row.provider)) return false
        const helpers = getHelpers(row.provider)
        if (!helpers) return false
        const entry = (helpers.getModelRegistry?.() ?? []).find(e => e.full_name === row.model)
        if (!entry || !helpers.isModelAvailable(entry)) return false
        const efforts = new Set((helpers.getFieldChoices('effort') ?? []).map(c => c.value))
        if (!efforts.has(row.effort)) return false
        return !helpers.isChoiceDisabled('effort', row.effort, { effectiveModel: entry.selected_model })
    }
}

/** "$" + 3 significant digits, trailing zeros dropped; em dash when missing. */
export function formatCost(x) {
    return isNum(x) ? `$${String(Number(x.toPrecision(3)))}` : DASH
}

/** Seconds (1 decimal) below 60 s after rounding, else minutes (1 decimal). */
export function formatTime(seconds) {
    if (!isNum(seconds)) return DASH
    const s = Math.round(seconds * 10) / 10
    return s < 60 ? `${s.toFixed(1)} s` : `${(s / 60).toFixed(1)} min`
}

/**
 * Rows of a matrix cell's "Benchmark data" panel (spec §5.3).
 *
 * @param {object} row - the snapshot row.
 * @param {{score: number, target: number}} scored - the computeBenchmarkScores entry.
 * @param {string} taskType
 * @returns {{label: string, value: string, description: string}[]}
 */
export function formatBenchmarkDetails(row, scored, taskType) {
    const type = TASK_TYPES.find(t => t.id === taskType) ?? TASK_TYPES[0]
    const m = typeMetrics(row, type.id)
    const one = x => (isNum(x) ? x.toFixed(1) : DASH)
    return [
        {
            label: 'Score',
            value: isNum(scored?.score) ? String(scored.score) : DASH,
            description: 'Our score for this task: 100 = best choice.',
        },
        {
            label: type.evals ? `${type.label} score` : 'Intelligence Index',
            value: one(m.ability),
            description: "The couple's ability on this task type (0–100).",
        },
        {
            label: 'Target',
            value: one(scored?.target),
            description: 'The level set by Task difficulty.',
        },
        {
            label: 'Cost / task',
            value: formatCost(m.cost),
            description: 'Average cost of one task.',
        },
        {
            label: 'Time / task',
            value: formatTime(m.time),
            description: 'Average decode time of one task.',
        },
        {
            label: 'Based on',
            value: type.evals
                ? type.evals.map(k => EVAL_LABELS[k]).join(', ')
                : 'Intelligence Index; cost and time from Terminal-Bench 4.0',
            description: 'The Artificial Analysis evaluations used.',
        },
    ]
}
