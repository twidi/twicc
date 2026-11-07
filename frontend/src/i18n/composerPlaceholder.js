// Keep complete placeholder messages in the catalog. Lists contain independent
// shortcut labels; the locale controls their conjunctions and message order.
export function getComposerPlaceholder(t, locale, {
    state, provider, workingMessageKey, commandChars = [],
    isDraft = false, isTouchDevice = false, isMac = false,
}) {
    if (state === 'starting') return t('composer.status.startingProvider', { provider })
    if (state === 'assistant_turn') {
        return t(workingMessageKey ?? 'composer.shortcuts.sendWhileWorking', { provider })
    }

    const alternatives = new Intl.ListFormat(locale, { type: 'disjunction' })
    const startHints = []
    if (commandChars.length) {
        startHints.push(t('composer.shortcuts.commands', { chars: alternatives.format(commandChars) }))
    }
    if (!isDraft) {
        startHints.push(t(isTouchDevice ? 'composer.shortcuts.historyTouch' : 'composer.shortcuts.historyKeyboard'))
    }
    const hints = new Intl.ListFormat(locale, { type: 'unit' }).format(startHints)
    if (isTouchDevice) {
        return t(startHints.length ? 'composer.shortcuts.touchWithStart' : 'composer.shortcuts.touch', { startHints: hints })
    }
    const keys = alternatives.format(isMac ? ['⌘↵', 'Ctrl↵'] : ['Ctrl↵', 'Meta↵'])
    return t(startHints.length ? 'composer.shortcuts.keyboardWithStart' : 'composer.shortcuts.keyboard', { startHints: hints, keys })
}
