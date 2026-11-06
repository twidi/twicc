/** Keep the current document, including its connection and unsaved state. */
export async function navigateAndroidNotification(client, router, target, dispatch = (name, detail) => window.dispatchEvent(new CustomEvent(name, { detail }))) {
    const hasPath = target?.path != null
    const hasAction = target?.action != null
    if (!target || typeof target.id !== 'string' || !target.id || target.id.length > 96 ||
        (!hasPath && !hasAction) ||
        (hasPath && (typeof target.path !== 'string' || /^\/projects?\/[A-Za-z0-9_-]{1,255}\/session\/[A-Za-z0-9_-]{1,255}\/?$/.exec(target.path)?.[0] !== target.path)) ||
        (hasAction && !['home', 'peerInbox', 'peerRequests'].includes(target.action)) ||
        (target.detail != null && (typeof target.detail !== 'string' || !/^[A-Za-z0-9_-]{1,96}$/.test(target.detail)))) {
        throw new Error('Invalid notification target.')
    }
    // Login reloads the document. Keep peer actions native until the authenticated page can handle them.
    const onLoginPage = () => router.currentRoute.value.path === '/login'
    if (hasAction && onLoginPage()) return
    if (hasPath) {
        const destination = router.resolve(target.path)
        const current = router.currentRoute.value
        if (current.params.projectId !== destination.params.projectId || current.params.sessionId !== destination.params.sessionId) {
            // A user or guard can cancel navigation. Consume that tap instead of repeating it later.
            await router.push(destination)
        }
    }
    if (hasAction && onLoginPage()) return
    if (target.action === 'home') await router.push('/')
    if (target.action === 'peerInbox') dispatch('twicc:open-peer-inbox', { messageId: target.detail })
    if (target.action === 'peerRequests') dispatch('twicc:open-peers-manager')
    await client.request('navigation.acknowledge', { navigationId: target.id })
}
