import { ARTIFACT_ICON } from './artifactBookmark.js'

export const OPEN_SIDEBAR_LABEL = 'Open sidebar (Alt+Shift+B)'
export const CLOSE_SIDEBAR_LABEL = 'Close sidebar (Alt+Shift+B)'

export const RAIL_ITEM_DEFINITIONS = [
    { id: 'home', icon: 'house', label: 'Back to projects list', group: 'top' },
    { id: 'sessions', icon: 'comments', label: 'Sessions', group: 'top' },
    { id: 'artifacts', icon: ARTIFACT_ICON, label: 'Artifacts', group: 'top' },
    {
        id: 'search',
        requires: state => state.mode === 'sessions',
        icon: 'magnifying-glass',
        label: state => state.isMac ? 'Full-text search (⌘⇧F)' : 'Full-text search (Ctrl+Shift+F)',
        group: 'top',
    },
    {
        id: 'palette',
        icon: 'bars-staggered',
        label: state => state.isMac ? 'Open command palette (⌘K)' : 'Open command palette (Ctrl+K)',
        group: 'bottom',
    },
    {
        id: 'inbox',
        icon: 'envelope',
        label: 'Peer inbox',
        group: 'bottom',
        requires: state => state.peerConfigured,
        badge: state => state.inboxCount,
    },
    { id: 'settings', icon: 'gear', label: 'Settings', group: 'bottom' },
    {
        id: 'toggle',
        icon: 'angles-left',
        label: state => state.sidebarOpen ? CLOSE_SIDEBAR_LABEL : OPEN_SIDEBAR_LABEL,
        group: 'bottom',
    },
]

/** Resolve visible rail items without changing the definitions or state. */
export function resolveRailItems(state, definitions = RAIL_ITEM_DEFINITIONS) {
    const items = definitions
        .filter(definition => {
            const visibleWhen = definition.visibleWhen ?? 'always'
            const visible = visibleWhen === 'always'
                || (visibleWhen === 'open' && state.sidebarOpen)
                || (visibleWhen === 'closed' && !state.sidebarOpen)
            return visible && (!definition.requires || definition.requires(state))
        })
        .map(definition => {
            const { id, icon, label, group, visibleWhen = 'always', disabled = false, badge } = definition
            return {
                id,
                icon: id === 'toggle' ? (state.sidebarOpen ? 'angles-left' : 'angles-right') : icon,
                label: typeof label === 'function' ? label(state) : label,
                group,
                visibleWhen,
                active: id === 'sessions' || id === 'artifacts' ? state.mode === id : undefined,
                disabled,
                ...(badge ? { badge: badge(state) } : {}),
            }
        })

    return [
        ...items.filter(item => item.group === 'top' && item.id !== 'toggle'),
        ...items.filter(item => item.group === 'bottom' && item.id !== 'toggle'),
        ...items.filter(item => item.id === 'toggle'),
    ]
}
