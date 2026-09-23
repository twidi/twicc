import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// Read the file the way the store cannot be read under node --test: a bare JSON
// import needs an import attribute there, so tests parse it explicitly.
const data = JSON.parse(readFileSync(new URL('./modelBenchmarks.json', import.meta.url), 'utf8'))

const EVAL_KEYS = [
    'terminalbench_4_0', 'scicode', 'aa_briefcase', 'gdpval_aa', 'gdp_pdf',
    'automationbench_aa', 'humanitys_last_exam', 'critpt', 'aa_omniscience', 'aa_lcr',
]

const isNumOrNull = v => v === null || (typeof v === 'number' && Number.isFinite(v))

test('carries the source metadata', () => {
    assert.equal(data.source, 'Artificial Analysis')
    assert.equal(data.url, 'https://artificialanalysis.ai/')
    assert.equal(data.retrieved_at, '2026-09-23')
})

test('holds the 56 rows of the spec, with unique keys', () => {
    assert.equal(data.rows.length, 56)
    const keys = new Set(data.rows.map(r => `${r.provider} ${r.model} ${r.effort}`))
    assert.equal(keys.size, 56)
    for (const r of data.rows) {
        assert.ok(['claude_code', 'codex'].includes(r.provider), r.provider)
        assert.ok(['low', 'medium', 'high', 'xhigh', 'max'].includes(r.effort), r.effort)
    }
})

test('every row has exactly the 10 evaluations with numeric or null values', () => {
    for (const r of data.rows) {
        assert.ok(isNumOrNull(r.intelligence_index))
        assert.deepEqual(Object.keys(r.evals), EVAL_KEYS)
        for (const k of EVAL_KEYS) {
            const e = r.evals[k]
            assert.deepEqual(Object.keys(e), ['score', 'cost_usd', 'time_s'])
            assert.ok(isNumOrNull(e.score) && isNumOrNull(e.cost_usd) && isNumOrNull(e.time_s), `${r.model} ${r.effort} ${k}`)
        }
    }
})

test('the only nulls are the times of claude-opus-5-5 max', () => {
    for (const r of data.rows) {
        for (const k of EVAL_KEYS) {
            const e = r.evals[k]
            const opusMax = r.model === 'claude-opus-5-5' && r.effort === 'max'
            assert.equal(e.score === null, false)
            assert.equal(e.cost_usd === null, false)
            assert.equal(e.time_s === null, opusMax, `${r.model} ${r.effort} ${k}`)
        }
    }
})
