// frontend/src/composables/useTrustGate.js
//
// Global gate run before starting a new session in a project: make sure the
// project's trust is settled (or recorded) first. A single ProjectTrustDialog is
// mounted at the app root and registered here; every "new session" entry point
// calls `ensureProjectTrust(projectId)` and proceeds only when it returns a
// truthy result.
//
// Flow (docs/archives/plans/2026-06-09-project-trust-design.md §5):
//   1. resolve locally from the store (no round-trip when already resolved)
//   2. else ask the backend (it may seed from the provider configs)
//   3. else prompt the user; persist the decision via /trust/decide/
// On any error we proceed (the gate must never hard-block starting a session;
// the project simply stays unresolved and is asked again next time).
//
// The result is AUTHORITATIVE for the caller: `{ state: true|false|null }` —
// what the gate actually settled on — or `false` when the user cancelled the
// dialog. Callers MUST pass `state` down to the draft seeding instead of
// re-resolving the store, which may not have caught up with a backend seed yet
// (the broadcast races the draft creation — the root cause of the "session
// silently seeded with the untrusted default" incident).

import { apiFetch } from '../utils/api'
import { resolveProjectTrust } from '../utils/trust'
import { useDataStore } from '../stores/data'

// Module-level singleton, set by App.vue once the dialog is mounted.
let trustDialog = null

export function registerTrustDialog(instance) {
    trustDialog = instance
}

/**
 * Ensure a project's trust is settled before a new session is created in it.
 * @param {string} projectId
 * @returns {Promise<{state: (boolean|null)} | false>} the settled effective
 *   trust (truthy — `state` may still be null when nothing could settle it),
 *   or `false` when the user cancelled the dialog (abort the new session).
 */
export async function ensureProjectTrust(projectId) {
    if (!projectId) return { state: null }
    const store = useDataStore()

    // 1. Resolve locally from the projects we already have.
    const local = resolveProjectTrust(projectId, store.projects)
    if (local.state != null) return { state: local.state }

    // 2. Ask the backend (it may seed from the provider configs and persist).
    try {
        const res = await apiFetch(`/api/projects/${projectId}/trust/resolve/`, { method: 'POST' })
        if (res.ok) {
            const data = await res.json()
            if (data.state != null) {
                // Optimistic local patch on a seed: the authoritative
                // project_updated broadcast may land after the caller seeds the
                // draft's settings, and the store should reflect the settled
                // trust as soon as possible (§13.3).
                if (data.via === 'seed') {
                    const project = store.getProject(projectId)
                    if (project) project.trust = data.state
                }
                return { state: data.state }
            }
        } else {
            // The backend answered but could not settle the state (it never
            // 500s the resolve anymore — this covers proxies and the like).
            // Don't prompt: the backend may have partially settled things, and
            // a dialog here could contradict it. Proceed unresolved.
            console.warn(`Trust resolve for ${projectId} returned HTTP ${res.status}; proceeding unresolved`)
            return { state: null }
        }
    } catch (err) {
        console.warn(`Trust resolve failed for ${projectId}; proceeding unresolved`, err)
        return { state: null }
    }

    // 3. Still unknown → prompt the user.
    const project = store.getProject(projectId)
    if (!trustDialog || !project) {
        console.warn(`Trust gate for ${projectId}: dialog or project unavailable; proceeding unresolved`)
        return { state: null }
    }
    const decision = await trustDialog.requestDecision(project)
    if (!decision) return false // cancelled → abort the new session

    try {
        await apiFetch(`/api/projects/${projectId}/trust/decide/`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                trusted: decision.trusted,
                propagation: decision.propagation,
            }),
        })
    } catch (err) {
        // The decision didn't persist, but the user expressed intent — proceed.
        console.warn('Trust decide failed', err)
    }
    // Optimistic local patch (same reason as the seed case above): the caller
    // is about to seed a draft and must see the decision the user just made.
    project.trust = decision.trusted
    project.trust_propagation = decision.propagation
    return { state: decision.trusted }
}
