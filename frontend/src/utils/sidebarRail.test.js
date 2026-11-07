import test from 'node:test'
import assert from 'node:assert/strict'
import { i18n, loadLocaleMessages } from '../i18n/index.js'
import {
    CLOSE_SIDEBAR_LABEL,
    CLOSE_SIDEBAR_LABEL_KEY,
    OPEN_SIDEBAR_LABEL,
    OPEN_SIDEBAR_LABEL_KEY,
    RAIL_ITEM_DEFINITIONS,
    resolveRailItems,
} from './sidebarRail.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

const state = { mode: 'sessions', sidebarOpen: true, peerConfigured: true, inboxCount: 3, isMac: false, speechStatus: 'idle' }

test('the timeline label identifies the listening destination independently of playback', () => {
    for (const speechStatus of ['idle', 'loading', 'playing']) {
        const items = resolveRailItems({ ...state, speechStatus, listeningTimelineLabel: 'My workspace' })
        const timeline = items.find(item => item.id === 'timeline')
        assert.equal(timeline.label, 'Listening timeline: My workspace')
        assert.equal(timeline.icon, 'clock-rotate-left')
        assert.equal(timeline.active, undefined, 'the destination remains navigation, not a toggle')
        assert.equal(items.filter(item => item.id === 'timeline').length, 1)
    }
    for (const listeningTimelineLabel of [undefined, null, '']) {
        const timeline = resolveRailItems({ ...state, listeningTimelineLabel }).find(item => item.id === 'timeline')
        assert.equal(timeline.label, 'All Projects timeline')
    }
})

test('resolves the shipped items with the specified icons, labels and groups', () => {
    const items = resolveRailItems(state)
    assert.deepEqual(items.map(({ id, icon, label, group }) => ({ id, icon, label, group })), [
        { id: 'home', icon: 'house', label: 'Back to projects list', group: 'top' },
        { id: 'sessions', icon: 'comments', label: 'Sessions', group: 'top' },
        { id: 'artifacts', icon: 'shapes', label: 'Artifacts', group: 'top' },
        { id: 'search', icon: 'magnifying-glass', label: 'Full-text search (Ctrl+Shift+F)', group: 'top' },
        { id: 'timeline', icon: 'clock-rotate-left', label: 'All Projects timeline', group: 'top' },
        { id: 'new-session', icon: 'plus', label: 'New session', group: 'bottom' },
        { id: 'palette', icon: 'bars-staggered', label: 'Open command palette (Ctrl+K)', group: 'bottom' },
        { id: 'inbox', icon: 'envelope', label: 'Peer inbox', group: 'bottom' },
        { id: 'settings', icon: 'gear', label: 'Settings', group: 'bottom' },
        { id: 'toggle', icon: 'angles-left', label: 'Close sidebar (Alt+Shift+B)', group: 'bottom' },
    ])
    assert.deepEqual(items.map(item => item.id), ['home', 'sessions', 'artifacts', 'search', 'timeline', 'new-session', 'palette', 'inbox', 'settings', 'toggle'])
    assert.ok(items.every(item => item.visibleWhen === 'always' && item.disabled === false))
    assert.equal(items.find(item => item.id === 'inbox').badge, 3)
    assert.ok(items.filter(item => item.id !== 'inbox').every(item => !Object.hasOwn(item, 'badge')))
    assert.deepEqual(resolveRailItems(state, RAIL_ITEM_DEFINITIONS), items)
})

test('sets boolean active states only on the three mode items', () => {
    for (const [mode, homeActive, sessionsActive, artifactsActive] of [
        ['home', true, false, false],
        ['sessions', false, true, false],
        ['artifacts', false, false, true],
    ]) {
        const items = resolveRailItems({ ...state, mode })
        assert.equal(items.find(item => item.id === 'home').active, homeActive)
        assert.equal(items.find(item => item.id === 'sessions').active, sessionsActive)
        assert.equal(items.find(item => item.id === 'artifacts').active, artifactsActive)
        assert.ok(items.filter(item => !['home', 'sessions', 'artifacts'].includes(item.id)).every(item => item.active === undefined))
    }
})

test('resolves platform-specific search and palette shortcut labels', () => {
    for (const [isMac, searchLabel, paletteLabel] of [
        [true, 'Full-text search (⌘⇧F)', 'Open command palette (⌘K)'],
        [false, 'Full-text search (Ctrl+Shift+F)', 'Open command palette (Ctrl+K)'],
    ]) {
        const items = resolveRailItems({ ...state, isMac })
        assert.equal(items.find(item => item.id === 'search').label, searchLabel)
        assert.equal(items.find(item => item.id === 'palette').label, paletteLabel)
    }
})

test('resolves the toggle action and icon from sidebar state', () => {
    assert.equal(CLOSE_SIDEBAR_LABEL, 'Close sidebar (Alt+Shift+B)')
    assert.equal(OPEN_SIDEBAR_LABEL, 'Open sidebar (Alt+Shift+B)')
    assert.equal(resolveRailItems(state).at(-1).label, 'Close sidebar (Alt+Shift+B)')
    const toggle = resolveRailItems({ ...state, sidebarOpen: false }).at(-1)
    assert.equal(toggle.label, 'Open sidebar (Alt+Shift+B)')
    assert.equal(toggle.icon, 'angles-right')
})

test('hides the inbox without a configured peer and preserves zero badge counts', () => {
    assert.equal(resolveRailItems({ ...state, peerConfigured: false }).some(item => item.id === 'inbox'), false)
    assert.equal(resolveRailItems({ ...state, inboxCount: 0 }).find(item => item.id === 'inbox').badge, 0)
})

test('filters contextual definitions and requirements with disabled defaults', () => {
    const definitions = [
        { id: 'default', icon: 'house', label: 'Default', group: 'top' },
        { id: 'always', icon: 'house', label: 'Always', group: 'top', visibleWhen: 'always', disabled: false },
        { id: 'open-only', icon: 'house', label: 'Open', group: 'top', visibleWhen: 'open', disabled: true },
        { id: 'closed-only', icon: 'house', label: 'Closed', group: 'top', visibleWhen: 'closed' },
        { id: 'requires-peer', icon: 'envelope', label: s => `Count ${s.inboxCount}`, group: 'bottom', requires: s => s.peerConfigured, badge: s => s.inboxCount },
    ]
    const openItems = resolveRailItems(state, definitions)
    assert.deepEqual(openItems.map(item => item.id), ['default', 'always', 'open-only', 'requires-peer'])
    assert.equal(openItems[0].visibleWhen, 'always')
    assert.equal(openItems[0].disabled, false)
    assert.equal(openItems[1].disabled, false)
    assert.equal(openItems[2].disabled, true)
    assert.equal(openItems[3].label, 'Count 3')
    assert.equal(openItems[3].badge, 3)
    assert.ok(openItems.every(item => item.active === undefined))
    const closedItems = resolveRailItems({ ...state, sidebarOpen: false, peerConfigured: false }, definitions)
    assert.deepEqual(closedItems.map(item => item.id), ['default', 'always', 'closed-only'])
    assert.equal(closedItems[2].disabled, false)
})

test('orders top items before bottom items and keeps toggle last', () => {
    const definitions = [
        { id: 'bottom-first', icon: 'gear', label: 'First bottom', group: 'bottom' },
        { id: 'top-first', icon: 'house', label: 'First top', group: 'top' },
        { id: 'toggle', icon: 'angles-left', label: 'Toggle', group: 'bottom' },
        { id: 'bottom-after-toggle', icon: 'gear', label: 'Last bottom', group: 'bottom' },
        { id: 'top-last', icon: 'house', label: 'Last top', group: 'top' },
    ]
    assert.deepEqual(resolveRailItems(state, definitions).map(item => item.id), [
        'top-first', 'top-last', 'bottom-first', 'bottom-after-toggle', 'toggle',
    ])
    assert.deepEqual(resolveRailItems(state, []), [])
})

test('session search is visible only in Sessions, with either sidebar state', () => {
    for (const mode of ['home', 'sessions', 'artifacts']) for (const sidebarOpen of [false, true]) {
        const items = resolveRailItems({ ...state, mode, sidebarOpen })
        assert.equal(items.some(item => item.id === 'search'), mode === 'sessions')
        assert.equal(items.some(item => item.id === 'timeline'), true)
        assert.equal(items.some(item => item.id === 'palette'), true)
    }
})

test('Home preserves navigation and global actions without sidebar controls', () => {
    for (const peerConfigured of [false, true]) {
        const items = resolveRailItems({ mode: 'home', peerConfigured, inboxCount: 3, isMac: false })
        assert.deepEqual(items.map(item => item.id), peerConfigured
            ? ['home', 'sessions', 'artifacts', 'timeline', 'new-session', 'palette', 'inbox', 'settings']
            : ['home', 'sessions', 'artifacts', 'timeline', 'new-session', 'palette', 'settings'])
        assert.equal(items.find(item => item.id === 'home').label, 'Home')
        if (peerConfigured) assert.equal(items.find(item => item.id === 'inbox').badge, 3)
    }
    for (const sidebarOpen of [false, true]) {
        const items = resolveRailItems({ ...state, mode: 'home', sidebarOpen })
        assert.equal(items.some(item => item.id === 'toggle'), false)
        assert.equal(items.some(item => item.id === 'search'), false)
    }
})

test('New session is always immediately above Command palette', () => {
    for (const mode of ['home', 'sessions', 'artifacts']) for (const sidebarOpen of [false, true]) {
        const items = resolveRailItems({ ...state, mode, sidebarOpen })
        const index = items.findIndex(item => item.id === 'new-session')
        assert.ok(index >= 0, `${mode}: New session is missing`)
        assert.equal(items[index].icon, 'plus')
        assert.equal(items[index].label, 'New session')
        assert.equal(items[index].group, 'bottom')
        assert.equal(items[index + 1].id, 'palette')
        assert.equal(items[index].disabled, false)
    }
})

test('shows the speech indicator first in the bottom group only while busy', () => {
    assert.equal(resolveRailItems(state).some(item => item.id === 'speech'), false)
    for (const [speechStatus, label] of [['loading', 'Cancel the text-to-speech request'], ['playing', 'Stop reading aloud']]) {
        const items = resolveRailItems({ ...state, speechStatus })
        const bottom = items.filter(item => item.group === 'bottom').map(item => item.id)
        assert.deepEqual(bottom, ['speech', 'new-session', 'palette', 'inbox', 'settings', 'toggle'])
        assert.equal(items.find(item => item.id === 'speech').label, label)
    }
})


test('labels resolve in the current locale while navigation identifiers and legacy constants stay stable', context => {
    const previousLocale = i18n.global.locale.value
    context.after(() => { i18n.global.locale.value = previousLocale })
    const english = resolveRailItems(state)
    i18n.global.locale.value = 'pt-BR'
    const translated = resolveRailItems(state)
    assert.deepEqual(translated.map(item => item.id), english.map(item => item.id))
    assert.deepEqual(translated.map(item => item.icon), english.map(item => item.icon))
    assert.equal(translated.find(item => item.id === 'sessions').label, i18n.global.t('sidebar.rail.sessions'))
    assert.notEqual(translated.find(item => item.id === 'sessions').label, english.find(item => item.id === 'sessions').label)
    assert.equal(translated.at(-1).label, i18n.global.t(CLOSE_SIDEBAR_LABEL_KEY))
    assert.equal(resolveRailItems({ ...state, sidebarOpen: false }).at(-1).label, i18n.global.t(OPEN_SIDEBAR_LABEL_KEY))
    const timeline = resolveRailItems({ ...state, listeningTimelineLabel: 'My workspace' }).find(item => item.id === 'timeline')
    assert.equal(timeline.label, i18n.global.t('sidebar.rail.listeningTimeline', { label: 'My workspace' }))
    assert.equal(OPEN_SIDEBAR_LABEL, 'Open sidebar (Alt+Shift+B)')
    assert.equal(CLOSE_SIDEBAR_LABEL, 'Close sidebar (Alt+Shift+B)')
})
