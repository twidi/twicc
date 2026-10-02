// One observer per scroll root. Disconnection invalidates queued deliveries.
export function createRowVisibilityObserver({ root, IntersectionObserver = globalThis.IntersectionObserver }) {
    const registrations = new Map(), observed = new WeakSet()
    let connected = true, epoch = 0, observer = null
    function renewObserver() {
        observer?.disconnect()
        const expectedEpoch = ++epoch
        observer = IntersectionObserver ? new IntersectionObserver(entries => {
            if (!connected || epoch !== expectedEpoch) return
            for (const entry of entries) registrations.get(entry.target)?.(entry.isIntersecting ? 'inside' : 'outside')
        }, { root, rootMargin: '200px 0px', threshold: 0 }) : null
        for (const element of registrations.keys()) observer?.observe(element)
    }
    renewObserver()
    return {
        observe(element, onState) {
            if (!connected) return () => {}
            // Reusing a target cannot distinguish old queued entries. Renew the shared epoch.
            if (observed.has(element)) renewObserver()
            observed.add(element)
            registrations.set(element, onState)
            if (observer) observer.observe(element)
            // Without IO, mounted bodies still update. Offscreen savings are unavailable.
            else onState('inside')
            return () => {
                if (registrations.get(element) !== onState) return
                registrations.delete(element)
                observer?.unobserve(element)
            }
        },
        disconnect() {
            connected = false
            observer?.disconnect()
            registrations.clear()
        },
    }
}
