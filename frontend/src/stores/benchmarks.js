import { defineStore } from 'pinia'
import benchmarkData from '../data/modelBenchmarks.json'
import { computeBenchmarkScores, makeLatestModelPredicate, makeScoringSetPredicate, scoreKey } from '../utils/benchmarkScores'
import { useBenchmarkTaskStore } from './benchmarkTask'
import { useSettingsStore } from './settings'
import { getProviderHelpers } from '../providers'

/**
 * Benchmark scores for the model × effort matrix (Agent Settings panel).
 *
 * The rows are the frozen Artificial Analysis snapshot bundled with the
 * frontend (``data/modelBenchmarks.json``; no fetch, no backend). ``scoreLookup``
 * scores them with the controls of the ``benchmarkTask`` store over the scoring
 * set — enabled providers, available models, selectable efforts — so scores
 * recompute when the controls, the enabled providers or the registries change.
 * The reference (slider range, tolerance, the 100) is the latest models only,
 * unless "Show older models" is on: older models are then shown AND counted;
 * otherwise they are still scored (an older row stays visible when selected)
 * but against the latest models. The matrix joins on ``(provider, model,
 * effort)`` with ``model`` = the registry ``full_name``.
 */
export const useBenchmarksStore = defineStore('benchmarks', {
    state: () => ({
        rows: benchmarkData.rows,
    }),

    getters: {
        /** Raw rows by scoreKey. */
        rowsByKey(state) {
            return new Map(state.rows.map(r => [scoreKey(r.provider, r.model, r.effort), r]))
        },

        /** Map scoreKey -> { score, penalty, ability, target, metric, reference } for the scored rows. */
        scoreLookup(state) {
            const task = useBenchmarkTaskStore()
            const inScoringSet = makeScoringSetPredicate(useSettingsStore().enabledProviders, getProviderHelpers)
            const isReference = task.showOlder ? () => true : makeLatestModelPredicate(getProviderHelpers)
            return computeBenchmarkScores(
                state.rows,
                { taskType: task.taskType, difficulty: task.difficulty, favor: task.favor },
                inScoringSet,
                isReference,
            )
        },

        /** Integer score for a (provider, model, effort) triple, or null ("?"). */
        getScore() {
            return (provider, model, effort) =>
                this.scoreLookup.get(scoreKey(provider, model, effort))?.score ?? null
        },

        /** ``{ row, scored }`` for a scored triple, else null. Feeds the cell's
         *  "Benchmark data" panel (``formatBenchmarkDetails``). */
        getRow() {
            return (provider, model, effort) => {
                const k = scoreKey(provider, model, effort)
                const scored = this.scoreLookup.get(k)
                if (!scored) return null
                return { row: this.rowsByKey.get(k), scored }
            }
        },
    },
})
