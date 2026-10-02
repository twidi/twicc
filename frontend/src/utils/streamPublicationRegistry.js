// Client-local ownership. This module has no store, component, or buffer dependency.
let generation = 0
export function createStreamPublicationIdentity(sessionId, messageId, blockIndex) {
    return Object.freeze({ sessionId, messageId, blockIndex, generation: ++generation })
}

export function createStreamPublicationRegistry({ document = null } = {}) {
    const records = new Map(), slots = new Map(), tokens = new Map()
    let listening = false
    const visible = () => !document?.hidden
    const slot = identity => JSON.stringify([identity.sessionId, identity.messageId, identity.blockIndex])
    const eligible = state => !!state.viewActive && !!state.bodyActive && state.intersection === 'inside'
    const canBootstrap = state => !!state.viewActive && !!state.bodyActive && state.intersection === 'unknown'
    function record(identity) {
        let entry = records.get(identity)
        if (!entry) {
            entry = { identity, owners: new Set(), count: 0, active: false, binding: null, reservation: null }
            records.set(identity, entry)
        }
        return entry
    }
    function reconcile(entry) {
        if (!entry.binding) return
        const active = visible() && entry.count > 0
        if (entry.active !== active) {
            entry.active = active
            entry.binding.setActive(active)
        }
        if (!active && visible() && !entry.reservation) {
            for (const token of entry.owners) {
                if (canBootstrap(tokens.get(token).state)) {
                    entry.reservation = token
                    entry.binding.snapshot()
                    break
                }
            }
        }
    }
    function onVisibility() { for (const entry of slots.values()) reconcile(entry) }
    function updateListener() {
        if (slots.size && !listening) {
            document?.addEventListener('visibilitychange', onVisibility)
            listening = true
        } else if (!slots.size && listening) {
            document?.removeEventListener('visibilitychange', onVisibility)
            listening = false
        }
    }
    function removeBinding(entry) {
        if (entry.active) entry.binding?.setActive(false)
        entry.active = false
        entry.binding = null
        for (const token of entry.owners) tokens.delete(token)
        entry.owners.clear()
        entry.count = 0
        entry.reservation = null
        records.delete(entry.identity)
        if (slots.get(slot(entry.identity)) === entry) slots.delete(slot(entry.identity))
        updateListener()
    }
    return {
        bindBlock(identity, binding) {
            const previous = slots.get(slot(identity))
            if (previous) removeBinding(previous)
            const entry = record(identity)
            entry.binding = binding
            slots.set(slot(identity), entry)
            updateListener()
            reconcile(entry)
            return () => { if (entry.binding === binding) removeBinding(entry) }
        },
        acquire(identity, state) {
            const token = Object.freeze({}), entry = record(identity)
            tokens.set(token, { entry, state: { ...state } })
            entry.owners.add(token)
            if (eligible(state)) entry.count++
            reconcile(entry)
            return token
        },
        update(token, state) {
            const owner = tokens.get(token)
            if (!owner) return
            const { entry } = owner
            entry.count += Number(eligible(state)) - Number(eligible(owner.state))
            owner.state = { ...state }
            if (entry.reservation === token && !canBootstrap(state)) entry.reservation = null
            reconcile(entry)
        },
        release(token) {
            const owner = tokens.get(token)
            if (!owner) return
            const { entry, state } = owner
            tokens.delete(token)
            entry.owners.delete(token)
            if (eligible(state)) entry.count--
            if (entry.reservation === token) entry.reservation = null
            reconcile(entry)
            if (!entry.binding && !entry.owners.size) records.delete(entry.identity)
        },
        dispose() {
            for (const entry of [...slots.values()]) removeBinding(entry)
            records.clear()
            tokens.clear()
        },
    }
}

export const streamPublicationRegistry = createStreamPublicationRegistry({ document: globalThis.document })
