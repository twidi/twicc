import { defineStore } from 'pinia'
import { TASK_TYPES } from '../utils/benchmarkScores.js'

/**
 * Controls of the model × effort score (spec §5.1): task type, task difficulty
 * (0..100) and whether to favor cost or speed, plus the auto-select switches
 * and the matrix's "Show older models" toggle — older models only take part in
 * the scoring reference when shown, so the 100 always sits on a visible cell.
 * In memory only — reset to the defaults on reload, like the former weights;
 * the older-models and auto-select switches are also reset whenever a surface
 * showing the matrix opens or closes (``resetTransientControls``).
 */
export const useBenchmarkTaskStore = defineStore('benchmarkTask', {
    state: () => ({
        taskType: 'general',
        difficulty: 50,
        favor: 'cost',
        autoSelectBest: false,
        defaultProviderOnly: false,
        showOlder: false,
    }),

    actions: {
        setTaskType(id) {
            if (TASK_TYPES.some(t => t.id === id)) this.taskType = id
        },

        setDifficulty(value) {
            const n = Math.round(Number(value))
            if (Number.isFinite(n)) this.difficulty = Math.min(100, Math.max(0, n))
        },

        setFavor(value) {
            if (value === 'cost' || value === 'speed') this.favor = value
        },

        // "Show older models" and "Auto-select best" (+ its "Default provider
        // only" sub-switch) never carry over from one opening to the next: a
        // forgotten one silently skews the scores or rewrites the selection.
        // Called when each surface showing the matrix opens and closes.
        resetTransientControls() {
            this.showOlder = false
            this.autoSelectBest = false
            this.defaultProviderOnly = false
        },
    },
})
