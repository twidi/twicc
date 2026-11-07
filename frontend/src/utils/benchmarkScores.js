import { i18n } from '../i18n/index.js'

/**
 * Model × effort scores — the "target ability" model.
 *
 * Spec: docs/plans/2026-09-23-artificial-analysis-benchmark-scores-design.md §4.
 * Tuned since the spec: tolerance 2 (was 4), 4th-power penalty (was squared),
 * score 100 / (1 + log2(1 + d)) (was 100 × 2^-d); older models only form the
 * scoring reference while shown (was: always); no "Based on" detail row;
 * Intelligence Index v5.0 evaluations (was v4.3), 6 task types ("Long
 * documents" dropped: AA-LCR left the index with its cost and time).
 * Rows come from the frozen Artificial Analysis snapshot
 * (``data/modelBenchmarks.json``). The user picks a task type, a difficulty and
 * whether to favor cost or speed. The difficulty sets a target on the task
 * type's ability axis; each (model, effort) pays a distance penalty when below
 * the target (4th power: a small shortfall stays cheap, a large one is crushed;
 * none above the target) plus log2 of its cost (or time) per task. The lowest
 * reference penalty scores 100 and one "doubling" worse scores 50; the score
 * then decays logarithmically, so far-off couples keep small, non-zero scores
 * (neighbours may share an integer; the best-per-provider ring ranks on the
 * unrounded penalty).
 *
 * Pure module: no store, no Vue. Two predicates are injected so the store
 * decides which rows count while this module stays testable: the scoring set
 * (enabled providers, available models, selectable efforts) gets scores; the
 * reference among them (the latest models unless older ones are shown) sets
 * the slider range, the tolerance and the 100. Rows outside the reference are
 * scored against it, capped at 100.
 */

// Exponent of the concave difficulty → target curve (the slider's middle lands high).
export const CURVE = 0.55
// Being this many Intelligence Index points below the target weighs as much as
// paying (or waiting) twice as much. Other task types scale it by their range.
export const TOLERANCE_II = 2
// Power of the below-target distance penalty, in tolerances: 1 tolerance below
// = 1 doubling, 2 below = 16, so a much weaker couple cannot win on price alone.
export const PENALTY_EXPONENT = 4

// The Artificial Analysis evaluation keys of a snapshot row, with their display
// names (the keys drive TASK_TYPES and the data tests; the help page lists the names).
// The nine evaluations of the Intelligence Index v5.0.
export const EVAL_LABELS = {
    terminalbench_4_0: 'Terminal-Bench 4.0',
    frontiercode_2_0: 'FrontierCode 2.0',
    aa_briefcase: 'AA-Briefcase',
    gdp_pdf: 'GDP.pdf',
    automationbench_aa: 'AutomationBench-AA',
    hle_diamond: 'HLE Diamond',
    critpt_v2_hard: 'CritPt v2 Hard',
    terminal_bench_science: 'Terminal-Bench-Science',
    aa_omniscience: 'AA-Omniscience',
}

// Task types, in display order. ``evals: null`` = the Intelligence Index, with
// Terminal-Bench 4.0 cost and time.
export const TASK_TYPES = [
    { id: 'general', get label() { return i18n.global.t('session.benchmarks.taskTypes.general') }, evals: null },
    { id: 'coding', get label() { return i18n.global.t('session.benchmarks.taskTypes.coding') }, evals: ['terminalbench_4_0', 'frontiercode_2_0'] },
    { id: 'office', get label() { return i18n.global.t('session.benchmarks.taskTypes.office') }, evals: ['aa_briefcase', 'gdp_pdf'] },
    { id: 'automation', get label() { return i18n.global.t('session.benchmarks.taskTypes.automation') }, evals: ['automationbench_aa'] },
    { id: 'science', get label() { return i18n.global.t('session.benchmarks.taskTypes.science') }, evals: ['hle_diamond', 'critpt_v2_hard', 'terminal_bench_science'] },
    { id: 'knowledge', get label() { return i18n.global.t('session.benchmarks.taskTypes.knowledge') }, evals: ['aa_omniscience'] },
]

// Evaluation scores not already a 0..1 rate: Elo as Artificial Analysis converts
// it, and the Omniscience index from -100..100.
const EVAL_NORMALIZERS = {
    aa_briefcase: s => (s - 500) / 2000,
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
 * @param {(row: object) => boolean} isInScoringSet - which rows get a score.
 * @param {(row: object) => boolean} [isReference] - among those, which rows set
 *   the slider range, the tolerance and the best (100). Defaults to all of them.
 *   The others (e.g. an older model kept visible because it is selected) are
 *   scored against that reference, capped at 100, and flagged ``reference: false``.
 * @returns {Map<string, {score: number, penalty: number, ability: number, target: number, metric: number, reference: boolean}>}
 *   keyed by scoreKey; a missing key means "no score" ("?").
 */
export function computeBenchmarkScores(rows, { taskType, difficulty, favor }, isInScoringSet, isReference = () => true) {
    const result = new Map()
    const set = (rows ?? []).filter(isInScoringSet)
    const reference = set.filter(isReference)
    const range = abilityRange(reference, taskType)
    if (!range) return result

    const general = abilityRange(reference, 'general')
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
        const distance = below ? ((target - m.ability) / tolerance) ** PENALTY_EXPONENT : 0
        scored.push({ row, ability: m.ability, metric, penalty: distance + Math.log2(metric), reference: isReference(row) })
    }
    const referencePenalties = scored.filter(s => s.reference).map(s => s.penalty)
    if (!referencePenalties.length) return result

    const minPenalty = Math.min(...referencePenalties)
    for (const s of scored) {
        // d = doublings behind the best: 0 → 100, 1 → 50, then a logarithmic
        // decay that never collapses far-off couples to 0. A non-reference row
        // better than the best is capped at 100 (d floored at 0).
        const d = Math.max(0, s.penalty - minPenalty)
        result.set(rowKey(s.row), {
            score: Math.round(100 / (1 + Math.log2(1 + d))),
            penalty: s.penalty,
            ability: s.ability,
            target,
            metric: s.metric,
            reference: s.reference,
        })
    }
    return result
}

/**
 * Lowest finite penalty among ``values`` (missing entries skipped), or null.
 * Ranks cells on the unrounded penalty so couples that round to the same score
 * still have a single real best (best-per-provider ring, auto-select).
 */
export function lowestPenalty(values) {
    let best = null
    for (const v of values) {
        if (isNum(v) && (best === null || v < best)) best = v
    }
    return best
}

/**
 * Build the "latest model" predicate: the row's model is its provider's latest
 * registry entry of its family (``latest: true``). With "Show older models"
 * off, only these rows form the scoring reference.
 *
 * @param {(provider: string) => object|null} getHelpers - provider helpers lookup.
 */
export function makeLatestModelPredicate(getHelpers) {
    return (row) => {
        const helpers = getHelpers(row.provider)
        if (!helpers) return false
        const entry = (helpers.getModelRegistry?.() ?? []).find(e => e.full_name === row.model)
        return entry?.latest === true
    }
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
    return isNum(x) ? new Intl.NumberFormat(i18n.global.locale.value, { style: 'currency', currency: 'USD', maximumSignificantDigits: 3 }).format(x) : DASH
}

/** Seconds (1 decimal) below 60 s after rounding, else minutes (1 decimal). */
export function formatTime(seconds) {
    if (!isNum(seconds)) return DASH
    const s = Math.round(seconds * 10) / 10
    return s < 60 ? new Intl.NumberFormat(i18n.global.locale.value, { style: 'unit', unit: 'second', unitDisplay: 'short', minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(s) : new Intl.NumberFormat(i18n.global.locale.value, { style: 'unit', unit: 'minute', unitDisplay: 'short', minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(s / 60)
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
    const one = x => (isNum(x) ? new Intl.NumberFormat(i18n.global.locale.value, { minimumFractionDigits: 1, maximumFractionDigits: 1 }).format(x) : DASH)
    return [
        {
            label: i18n.global.t('session.benchmarks.details.score'),
            value: isNum(scored?.score) ? String(scored.score) : DASH,
            description: i18n.global.t('session.benchmarks.details.scoreDescription'),
        },
        {
            label: type.evals ? i18n.global.t('session.benchmarks.details.taskScore', { task: type.label }) : 'Intelligence Index',
            value: one(m.ability),
            description: i18n.global.t('session.benchmarks.details.abilityDescription'),
        },
        {
            label: i18n.global.t('session.benchmarks.details.target'),
            value: one(scored?.target),
            description: i18n.global.t('session.benchmarks.details.targetDescription'),
        },
        {
            label: i18n.global.t('session.benchmarks.details.cost'),
            value: formatCost(m.cost),
            description: i18n.global.t('session.benchmarks.details.costDescription'),
        },
        {
            label: i18n.global.t('session.benchmarks.details.time'),
            value: formatTime(m.time),
            description: i18n.global.t('session.benchmarks.details.timeDescription'),
        },
    ]
}
