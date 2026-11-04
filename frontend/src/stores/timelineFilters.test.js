// frontend/src/stores/timelineFilters.test.js
//
// Run with:  node --test src/stores/timelineFilters.test.js   (from the frontend dir)

import { test, beforeEach } from 'node:test'
import assert from 'node:assert/strict'
import { createPinia, setActivePinia } from 'pinia'
import { createFilters } from '../utils/timeline/filters.js'
import { useTimelineFiltersStore } from './timelineFilters.js'

beforeEach(() => {
    setActivePinia(createPinia())
})

test('filtersFor on an unknown page returns the frozen default and adds no entry', () => {
    const store = useTimelineFiltersStore()
    const filters = store.filtersFor('global')
    assert.deepEqual(filters, createFilters())
    assert.equal(Object.isFrozen(filters), true)
    for (const group of ['families', 'providers', 'authors']) {
        assert.equal(Object.isFrozen(filters[group]), true, `${group} is frozen too`)
        assert.throws(() => filters[group].push('x'), TypeError)
    }
    assert.equal(store.filtersFor('p:other'), filters, 'one shared default')
    assert.deepEqual(Object.keys(store.byPage), [])
})

test('two pages keep independent filters', () => {
    const store = useTimelineFiltersStore()
    store.setView('p:a', 'waits_problems')
    store.toggle('p:a', 'families', 'turns')
    store.toggle('p:b', 'authors', 'you')
    store.setSession('p:b', { id: 's1', title: 'One' })
    assert.deepEqual(store.filtersFor('p:a'), { ...createFilters(), view: 'waits_problems', families: ['turns'] })
    assert.deepEqual(store.filtersFor('p:b'), { ...createFilters(), authors: ['you'], session: { id: 's1', title: 'One' } })
    assert.deepEqual(store.filtersFor('global'), createFilters())
})

test('after a write filtersFor returns the same object on every call', () => {
    const store = useTimelineFiltersStore()
    store.toggle('p:a', 'providers', 'codex')
    const first = store.filtersFor('p:a')
    assert.equal(store.filtersFor('p:a'), first)
    assert.equal(Object.isFrozen(first), false)
    assert.deepEqual(store.filtersFor('p:a').providers, ['codex'])
})

test('toggle keeps the constant order of the group and unchecks a checked value', () => {
    const store = useTimelineFiltersStore()
    store.toggle('p', 'families', 'twicc')
    store.toggle('p', 'families', 'sessions')
    store.toggle('p', 'families', 'turns')
    assert.deepEqual(store.filtersFor('p').families, ['sessions', 'turns', 'twicc'])
    store.toggle('p', 'families', 'turns')
    assert.deepEqual(store.filtersFor('p').families, ['sessions', 'twicc'])
    store.toggle('p', 'authors', 'session_agent')
    store.toggle('p', 'authors', 'unknown')
    assert.deepEqual(store.filtersFor('p').authors, ['unknown', 'session_agent'])
    store.toggle('p', 'providers', 'codex')
    store.toggle('p', 'providers', 'claude_code')
    assert.deepEqual(store.filtersFor('p').providers, ['claude_code', 'codex'])
})

test('clearGroup and resetPanel clear the groups only', () => {
    const store = useTimelineFiltersStore()
    store.setView('p', 'without_turns')
    store.toggle('p', 'families', 'turns')
    store.toggle('p', 'providers', 'codex')
    store.toggle('p', 'authors', 'you')
    store.setSession('p', { id: 's1', title: null })
    store.clearGroup('p', 'providers')
    assert.deepEqual(store.filtersFor('p').providers, [])
    assert.deepEqual(store.filtersFor('p').families, ['turns'])
    store.resetPanel('p')
    assert.deepEqual(store.filtersFor('p'), { ...createFilters(), view: 'without_turns', session: { id: 's1', title: null } })
})

test('setSession and clearSession set and remove the session filter', () => {
    const store = useTimelineFiltersStore()
    store.setSession('p', { id: 's1', title: 'One' })
    assert.deepEqual(store.filtersFor('p').session, { id: 's1', title: 'One' })
    store.setSession('p', { id: 's2', title: 'Two' })
    assert.deepEqual(store.filtersFor('p').session, { id: 's2', title: 'Two' })
    store.clearSession('p')
    assert.equal(store.filtersFor('p').session, null)
})

test('clearAll resets the groups, the session and the view', () => {
    const store = useTimelineFiltersStore()
    store.setView('p', 'waits_problems')
    store.toggle('p', 'families', 'waits')
    store.toggle('p', 'authors', 'automatic')
    store.setSession('p', { id: 's1', title: 'One' })
    store.clearAll('p')
    assert.deepEqual(store.filtersFor('p'), createFilters())
})

test('an action never writes into the shared default', () => {
    const store = useTimelineFiltersStore()
    const fallback = store.filtersFor('p')
    store.toggle('p', 'families', 'turns')
    assert.deepEqual(fallback, createFilters())
    assert.deepEqual(store.filtersFor('q'), createFilters())
})

test('muted inclusion belongs to one page and resets with panel filters', () => {
    const store = useTimelineFiltersStore()
    store.setIncludeMuted('p', true)
    assert.equal(store.filtersFor('p').includeMuted, true)
    assert.equal(store.filtersFor('q').includeMuted, false)
    store.resetPanel('p')
    assert.equal(store.filtersFor('p').includeMuted, false)
    store.setIncludeMuted('p', true)
    store.clearGroup('p', 'includeMuted')
    assert.equal(store.filtersFor('p').includeMuted, false)
    store.setIncludeMuted('p', true)
    store.clearAll('p')
    assert.equal(store.filtersFor('p').includeMuted, false)
})

test('a fresh Pinia starts empty: the filters are never persisted', () => {
    useTimelineFiltersStore().toggle('p', 'families', 'turns')
    setActivePinia(createPinia())
    const store = useTimelineFiltersStore()
    assert.deepEqual(Object.keys(store.byPage), [])
    assert.deepEqual(store.filtersFor('p'), createFilters())
})
