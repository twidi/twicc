import assert from 'node:assert/strict'
import test from 'node:test'

import {
    activeCount, AUTHORS, createFilters, FAMILIES, groupPills, PROVIDERS, queryKey, toggleValue, toQuery, VIEWS,
} from './filters.js'

const familyKeys = FAMILIES.map((family) => family.value)
const authorKeys = AUTHORS.map((author) => author.value)
const providerLabel = (provider) => ({ claude_code: 'Claude Code', codex: 'Codex' })[provider] ?? provider
const filters = (fields = {}) => ({ ...createFilters(), ...fields })

test('the keys and labels of the global constraints', () => {
    assert.deepEqual(VIEWS, [
        { value: 'all', label: 'All', short: 'All' },
        { value: 'without_turns', label: 'Without turns', short: 'No turns' },
        { value: 'waits_problems', label: 'Waits & problems', short: 'Waits' },
    ])
    assert.deepEqual(FAMILIES, [
        { value: 'sessions', label: 'Sessions', hint: 'created, renamed' },
        { value: 'processes', label: 'Processes', hint: 'started, stopped' },
        { value: 'turns', label: 'Turns', hint: 'started, ended' },
        { value: 'waits', label: 'Waiting for you', hint: 'approvals, questions' },
        { value: 'settings', label: 'Settings', hint: 'model, effort, permissions' },
        { value: 'organization', label: 'Organization', hint: 'archived, hidden, muted, pinned, detached' },
        { value: 'twicc', label: 'TwiCC', hint: 'started, stopped' },
    ])
    assert.deepEqual(AUTHORS, [
        { value: 'unknown', label: 'Unknown' },
        { value: 'automatic', label: 'Automatic' },
        { value: 'you', label: 'You' },
        { value: 'agent_cli_mcp', label: 'Agent via CLI/MCP' },
        { value: 'external_mcp', label: 'External MCP' },
        { value: 'session_agent', label: "The session's agent" },
    ])
    assert.deepEqual(PROVIDERS, ['claude_code', 'codex'])
})

test('createFilters returns a fresh default each call', () => {
    const first = createFilters()
    assert.deepEqual(first, { view: 'all', families: [], providers: [], authors: [], session: null })
    first.families.push('turns')
    assert.deepEqual(createFilters().families, [])
})

test('toggleValue adds and removes, keeping the constant order', () => {
    let list = []
    list = toggleValue(list, 'twicc', familyKeys)
    list = toggleValue(list, 'sessions', familyKeys)
    list = toggleValue(list, 'turns', familyKeys)
    assert.deepEqual(list, ['sessions', 'turns', 'twicc'])
    const before = list
    list = toggleValue(list, 'turns', familyKeys)
    assert.deepEqual(list, ['sessions', 'twicc'])
    assert.deepEqual(before, ['sessions', 'turns', 'twicc'], 'the input list is not mutated')
    assert.deepEqual(toggleValue(['bogus'], 'codex', PROVIDERS), ['codex'], 'a value outside the order is dropped')
})

test('toQuery omits every default', () => {
    assert.deepEqual(toQuery(createFilters()), {})
})

test('toQuery keeps the constant order whatever the toggle order', () => {
    const query = toQuery(filters({
        view: 'without_turns',
        families: ['twicc', 'processes'],
        providers: ['codex'],
        authors: ['session_agent', 'you'],
        session: { id: 'abc123', title: 'A title' },
    }))
    assert.deepEqual(query, {
        view: 'without_turns',
        families: 'processes,twicc',
        provider: 'codex',
        authors: 'you,session_agent',
        session: 'abc123',
    })
})

test('toQuery omits a complete group (spec 4.5)', () => {
    const query = toQuery(filters({
        families: [...familyKeys].reverse(),
        providers: ['codex', 'claude_code'],
        authors: [...authorKeys],
    }))
    assert.deepEqual(query, {})
})

test('queryKey is equal for equal queries built in different orders', () => {
    const a = { view: 'waits_problems', provider: 'codex', session: 's' }
    const b = { session: 's', provider: 'codex', view: 'waits_problems' }
    assert.equal(queryKey(a), queryKey(b))
    assert.notEqual(queryKey(a), queryKey({ ...a, provider: 'claude_code' }))
    assert.equal(queryKey({}), queryKey(toQuery(createFilters())))
})

test('activeCount counts the checked panel values, not the view nor the session', () => {
    assert.equal(activeCount(createFilters()), 0)
    assert.equal(activeCount(filters({ view: 'waits_problems', session: { id: 's', title: 'T' } })), 0)
    assert.equal(activeCount(filters({ families: ['turns', 'twicc'], providers: ['codex'], authors: ['you'] })), 4)
})

test('groupPills: one pill per non-empty group, in order, then the session', () => {
    const pills = groupPills(filters({
        view: 'waits_problems',
        session: { id: 'session-1', title: 'Fix the bug' },
        authors: ['you'],
        providers: ['codex'],
        families: ['twicc', 'processes'],
    }), { providerLabel })
    assert.deepEqual(pills.map((pill) => pill.group), ['families', 'providers', 'authors', 'session'])
    assert.deepEqual(pills[0], {
        group: 'families', label: 'Events', values: ['Processes', 'TwiCC'], shown: ['Processes', 'TwiCC'], more: 0,
        title: 'Processes, TwiCC',
    })
    assert.deepEqual(pills[1], {
        group: 'providers', label: 'Provider', values: ['Codex'], shown: ['Codex'], more: 0, title: 'Codex',
    })
    assert.deepEqual(pills[2], {
        group: 'authors', label: 'Done by', values: ['You'], shown: ['You'], more: 0, title: 'You',
    })
    assert.deepEqual(pills[3], {
        group: 'session', label: 'Session', values: ['Fix the bug'], shown: ['Fix the bug'], more: 0,
        title: 'Fix the bug',
    })
})

test('groupPills: shown and more with 4 families', () => {
    const [pill] = groupPills(filters({ families: ['waits', 'sessions', 'twicc', 'turns'] }), { providerLabel })
    assert.deepEqual(pill.values, ['Sessions', 'Turns', 'Waiting for you', 'TwiCC'])
    assert.deepEqual(pill.shown, ['Sessions', 'Turns'])
    assert.equal(pill.more, 2)
    assert.equal(pill.title, 'Sessions, Turns, Waiting for you, TwiCC')
})

test('groupPills: the session falls back to the short id', () => {
    const pills = groupPills(filters({ session: { id: '0123456789abcdef', title: null } }), { providerLabel })
    assert.deepEqual(pills, [{
        group: 'session', label: 'Session', values: ['01234567'], shown: ['01234567'], more: 0, title: '01234567',
    }])
    const blank = groupPills(filters({ session: { id: '0123456789abcdef', title: '' } }), { providerLabel })
    assert.deepEqual(blank[0].values, ['01234567'])
})

test('groupPills: no pill without a checked value nor a session', () => {
    assert.deepEqual(groupPills(filters({ view: 'without_turns' }), { providerLabel }), [])
})

test('a session without an id is no session filter, everywhere', () => {
    const noId = filters({ session: { id: '', title: 'Ghost' } })
    assert.deepEqual(toQuery(noId), {})
    assert.deepEqual(groupPills(noId, { providerLabel }), [])
})

test('an empty view is the default view', () => {
    const noView = filters({ view: '' })
    assert.deepEqual(toQuery(noView), {})
})

test('an unknown value in a list counts nowhere', () => {
    const bogus = filters({ families: ['bogus', 'turns'], providers: ['other'], authors: ['nobody'] })
    assert.equal(activeCount(bogus), 1)
    assert.deepEqual(toQuery(bogus), { families: 'turns' })
    assert.deepEqual(groupPills(bogus, { providerLabel }).map((pill) => pill.group), ['families'])
    const onlyBogus = filters({ providers: ['other'] })
    assert.equal(activeCount(onlyBogus), 0)
})
