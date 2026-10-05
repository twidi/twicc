import test from 'node:test'
import assert from 'node:assert/strict'
import {
    CLOSE_SIDEBAR_LABEL,
    OPEN_SIDEBAR_LABEL,
    RAIL_ITEM_DEFINITIONS,
    resolveRailItems,
} from './sidebarRail.js'

const state = { mode: 'sessions', sidebarOpen: true, peerConfigured: true, inboxCount: 3, isMac: false }

test('resolves the shipped items with the specified icons, labels and groups', () => {
    const items = resolveRailItems(state)
    assert.deepEqual(items.map(({ id, icon, label, group }) => ({ id, icon, label, group })), [
        { id: 'home', icon: 'house', label: 'Back to projects list', group: 'top' },
        { id: 'sessions', icon: 'comments', label: 'Sessions', group: 'top' },
        { id: 'artifacts', icon: 'shapes', label: 'Artifacts', group: 'top' },
        { id: 'search', icon: 'magnifying-glass', label: 'Full-text search (Ctrl+Shift+F)', group: 'top' },
        { id: 'palette', icon: 'bars-staggered', label: 'Open command palette (Ctrl+K)', group: 'bottom' },
        { id: 'inbox', icon: 'envelope', label: 'Peer inbox', group: 'bottom' },
        { id: 'settings', icon: 'gear', label: 'Settings', group: 'bottom' },
        { id: 'toggle', icon: 'angles-left', label: 'Close sidebar (Alt+Shift+B)', group: 'bottom' },
    ])
    assert.deepEqual(items.map(item => item.id), ['home', 'sessions', 'artifacts', 'search', 'palette', 'inbox', 'settings', 'toggle'])
    assert.ok(items.every(item => item.visibleWhen === 'always' && item.disabled === false))
    assert.equal(items.find(item => item.id === 'inbox').badge, 3)
    assert.ok(items.filter(item => item.id !== 'inbox').every(item => !Object.hasOwn(item, 'badge')))
    assert.deepEqual(resolveRailItems(state, RAIL_ITEM_DEFINITIONS), items)
})

test('sets boolean active states only on the two mode items', () => {
    for (const [mode, sessionsActive, artifactsActive] of [
        ['sessions', true, false],
        ['artifacts', false, true],
    ]) {
        const items = resolveRailItems({ ...state, mode })
        assert.equal(items.find(item => item.id === 'sessions').active, sessionsActive)
        assert.equal(items.find(item => item.id === 'artifacts').active, artifactsActive)
        assert.ok(items.filter(item => !['sessions', 'artifacts'].includes(item.id)).every(item => item.active === undefined))
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
