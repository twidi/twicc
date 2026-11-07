import { i18n } from '../i18n/index.js'
import { ARTIFACT_ICON } from './artifactBookmark.js'

export const OPEN_SIDEBAR_LABEL_KEY = 'sidebar.rail.open'
export const CLOSE_SIDEBAR_LABEL_KEY = 'sidebar.rail.close'

export const OPEN_SIDEBAR_LABEL = 'Open sidebar (Alt+Shift+B)'
export const CLOSE_SIDEBAR_LABEL = 'Close sidebar (Alt+Shift+B)'

export const RAIL_ITEM_DEFINITIONS = [
    { id: 'home', icon: 'house', label: state => state.mode === 'home' ? i18n.global.t('sidebar.rail.home') : i18n.global.t('sidebar.rail.projects'), group: 'top' },
    { id: 'sessions', icon: 'comments', label: () => i18n.global.t('sidebar.rail.sessions'), group: 'top' },
    { id: 'artifacts', icon: ARTIFACT_ICON, label: () => i18n.global.t('sidebar.rail.artifacts'), group: 'top' },
    {
        id: 'search',
        requires: state => state.mode === 'sessions',
        icon: 'magnifying-glass',
        label: state => state.isMac ? i18n.global.t('sidebar.rail.searchMac') : i18n.global.t('sidebar.rail.searchOther'),
        group: 'top',
    },
    {
        id: 'timeline', icon: 'clock-rotate-left', group: 'top',
        label: state => state.listeningTimelineLabel ? i18n.global.t('sidebar.rail.listeningTimeline', { label: state.listeningTimelineLabel }) : i18n.global.t('sidebar.rail.allTimeline'),
    },
    {
        id: 'speech',
        icon: 'volume-up',
        label: state => state.speechStatus === 'loading' ? i18n.global.t('sidebar.rail.cancelSpeech') : i18n.global.t('sidebar.rail.stopSpeech'),
        group: 'bottom',
        requires: state => state.speechStatus === 'loading' || state.speechStatus === 'playing',
    },
    { id: 'new-session', icon: 'plus', label: () => i18n.global.t('sidebar.rail.newSession'), group: 'bottom' },
    {
        id: 'palette',
        icon: 'bars-staggered',
        label: state => state.isMac ? i18n.global.t('sidebar.rail.paletteMac') : i18n.global.t('sidebar.rail.paletteOther'),
        group: 'bottom',
    },
    {
        id: 'inbox',
        icon: 'envelope',
        label: () => i18n.global.t('sidebar.rail.inbox'),
        group: 'bottom',
        requires: state => state.peerConfigured,
        badge: state => state.inboxCount,
    },
    { id: 'settings', icon: 'gear', label: () => i18n.global.t('sidebar.rail.settings'), group: 'bottom' },
    {
        id: 'toggle',
        requires: state => state.mode !== 'home',
        icon: 'angles-left',
        label: state => i18n.global.t(state.sidebarOpen ? CLOSE_SIDEBAR_LABEL_KEY : OPEN_SIDEBAR_LABEL_KEY),
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
                active: ['home', 'sessions', 'artifacts'].includes(id) ? state.mode === id : undefined,
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
