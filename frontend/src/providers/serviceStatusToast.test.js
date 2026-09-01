// frontend/src/providers/serviceStatusToast.test.js
//
// Run with:  node --test src/providers/serviceStatusToast.test.js   (from the frontend dir)
//
// The reconciler against a stubbed Notivue: each ``push.<type>`` records an
// item whose ``clear()`` is observable. What is asserted is the screen —
// which toast stands for which provider after each records update — through
// an incident's life and across two providers that must never interfere.

import test from 'node:test'
import assert from 'node:assert/strict'

import { push } from 'notivue'

const shown = []
for (const type of ['success', 'error', 'warning', 'info']) {
    push[type] = (options) => {
        const item = { type, options, cleared: false, clear() { this.cleared = true } }
        shown.push(item)
        return item
    }
}

const {
    __activeProviderStatusToasts,
    forgetProviderStatusToast,
    isProgrammaticClear,
    reconcileProviderStatusToasts,
} = await import('./serviceStatusToast.js')

const IDENTITIES = {
    claude_code: { productLabel: 'Claude Code', vendorLabel: 'Anthropic', statusUrl: 'https://status.claude.com/' },
    codex: { productLabel: 'Codex', vendorLabel: 'OpenAI', statusUrl: 'https://status.openai.com/' },
}
const identity = (provider) => IDENTITIES[provider] ?? null

const T0 = Date.parse('2026-09-01T14:02:00Z')
const MIN = 60_000
const iso = (minutes) => new Date(T0 + minutes * MIN).toISOString()
const NOW = T0 + 90 * MIN
const START = iso(0)

// A record as the backend writes it: ``changed`` / ``resolved`` / ``ack`` in
// minutes after the incident start; ``ackStatus`` the acknowledged level.
const record = (status, incidentStatus, { changed = 0, resolved = null, ackStatus = null, ackChanged = 0, startedAt = START } = {}) => ({
    status,
    incident: incidentStatus
        ? { started_at: startedAt, status: incidentStatus, changed_at: iso(changed), resolved_at: resolved === null ? null : iso(resolved) }
        : null,
    acknowledged: ackStatus ? { started_at: startedAt, status: ackStatus, changed_at: iso(ackChanged) } : null,
})
const key = (changed, status) => `${START}|${iso(changed)}|${status}`

const reconcile = (records) => reconcileProviderStatusToasts(records, { identity, now: NOW })
const onScreen = () => shown.filter((item) => !item.cleared)
const last = () => shown[shown.length - 1]

test('the whole life of an incident, one toast at a time', () => {
    // Quiet vendor: nothing.
    reconcile({ claude_code: record('operational', null) })
    assert.equal(shown.length, 0)

    // Degraded: one warning toast.
    reconcile({ claude_code: record('degraded_performance', 'degraded_performance') })
    assert.equal(shown.length, 1)
    assert.equal(last().type, 'warning')
    assert.equal(last().options.title, 'Anthropic status update')
    assert.deepEqual(__activeProviderStatusToasts(), { claude_code: key(0, 'degraded_performance') })

    // The same file again (reconnect replay): nothing new.
    reconcile({ claude_code: record('degraded_performance', 'degraded_performance') })
    assert.equal(shown.length, 1)

    // Up to major: the warning is cleared BY US and an error toast replaces it.
    const degradedItem = last()
    reconcile({ claude_code: record('major_outage', 'major_outage', { changed: 20 }) })
    assert.equal(degradedItem.cleared, true)
    assert.equal(isProgrammaticClear(degradedItem), true)
    assert.equal(shown.length, 2)
    assert.equal(last().type, 'error')
    assert.equal(onScreen().length, 1)

    // Acknowledged elsewhere: the file comes back with the ack → cleared by us.
    const majorItem = last()
    reconcile({ claude_code: record('major_outage', 'major_outage', { changed: 20, ackStatus: 'major_outage', ackChanged: 20 }) })
    assert.equal(majorItem.cleared, true)
    assert.equal(isProgrammaticClear(majorItem), true)
    assert.deepEqual(__activeProviderStatusToasts(), {})

    // Resolution: a success toast, although the outage was acknowledged.
    reconcile({ claude_code: record('operational', 'major_outage', { changed: 88, resolved: 88, ackStatus: 'major_outage', ackChanged: 20 }) })
    assert.equal(shown.length, 3)
    assert.equal(last().type, 'success')
    assert.equal(last().options.props.contentProps.episode.kind, 'resolved')

    // The user closes it here: Notivue clears the item, the body unmounts and
    // reports the dismissal — not a programmatic clear.
    const resolvedItem = last()
    resolvedItem.clear()
    assert.equal(isProgrammaticClear(resolvedItem), false)
    forgetProviderStatusToast('claude_code', resolvedItem)
    assert.deepEqual(__activeProviderStatusToasts(), {})

    // The acknowledgment comes back through the file: nothing to do, nothing new.
    reconcile({ claude_code: record('operational', 'major_outage', { changed: 88, resolved: 88, ackStatus: 'operational', ackChanged: 88 }) })
    assert.equal(shown.length, 3)
})

test('major → partial → major puts three toasts on screen in turn, the second major included', () => {
    shown.length = 0
    reconcile({ claude_code: record('major_outage', 'major_outage', { changed: 0 }) })
    const first = last()
    assert.equal(first.type, 'error')

    // Acknowledged, then down to partial: a warning replaces nothing (the
    // first was already cleared by the acknowledgment) and stands alone.
    reconcile({ claude_code: record('major_outage', 'major_outage', { changed: 0, ackStatus: 'major_outage', ackChanged: 0 }) })
    assert.equal(first.cleared, true)
    reconcile({ claude_code: record('partial_outage', 'partial_outage', { changed: 10, ackStatus: 'major_outage', ackChanged: 0 }) })
    assert.equal(onScreen().length, 1)
    assert.equal(last().type, 'warning')
    assert.deepEqual(__activeProviderStatusToasts(), { claude_code: key(10, 'partial_outage') })

    // Back up to major without dismissing the partial: the warning is
    // replaced by a new error toast — same level as the acknowledged first
    // major, different transition.
    const partial = last()
    reconcile({ claude_code: record('major_outage', 'major_outage', { changed: 20, ackStatus: 'major_outage', ackChanged: 0 }) })
    assert.equal(partial.cleared, true)
    assert.equal(onScreen().length, 1)
    assert.equal(last().type, 'error')
    assert.deepEqual(__activeProviderStatusToasts(), { claude_code: key(20, 'major_outage') })
    assert.equal(shown.length, 3)
})

test('providers own separate slots and never clear each other', () => {
    shown.length = 0
    reconcile({
        claude_code: record('major_outage', 'major_outage'),
        codex: record('degraded_performance', 'degraded_performance'),
    })
    assert.equal(onScreen().length, 2)
    const [claude, codex] = shown
    assert.equal(claude.options.title, 'Anthropic status update')
    assert.equal(codex.options.title, 'OpenAI status update')

    // Codex recovers and is acknowledged; Claude's toast must stand untouched.
    reconcile({
        claude_code: record('major_outage', 'major_outage'),
        codex: record('operational', 'degraded_performance', { changed: 10, resolved: 10, ackStatus: 'operational', ackChanged: 10 }),
    })
    assert.equal(claude.cleared, false)
    assert.equal(codex.cleared, true)
    assert.deepEqual(__activeProviderStatusToasts(), { claude_code: key(0, 'major_outage') })

    // Claude goes down to partial: replaced, Codex still silent.
    reconcile({
        claude_code: record('partial_outage', 'partial_outage', { changed: 30 }),
        codex: record('operational', 'degraded_performance', { changed: 10, resolved: 10, ackStatus: 'operational', ackChanged: 10 }),
    })
    assert.equal(claude.cleared, true)
    assert.equal(onScreen().length, 1)
    assert.equal(last().type, 'warning')
})

test('a provider without an identity (disabled, unknown) gets no toast and loses its standing one', () => {
    shown.length = 0
    reconcile({ codex: record('major_outage', 'major_outage') })
    assert.equal(onScreen().length, 1)
    const item = last()

    // Disabled: the identity resolver returns null → cleared by us.
    reconcileProviderStatusToasts({ codex: record('major_outage', 'major_outage') }, { identity: () => null, now: NOW })
    assert.equal(item.cleared, true)
    assert.equal(isProgrammaticClear(item), true)
    assert.deepEqual(__activeProviderStatusToasts(), {})

    // An unknown provider in the file is ignored.
    reconcile({ gemini: record('major_outage', 'major_outage') })
    assert.equal(shown.length, 1)
})

test('a provider that vanished from the file loses its toast', () => {
    shown.length = 0
    reconcile({ codex: record('major_outage', 'major_outage') })
    const item = last()
    reconcile({})
    assert.equal(item.cleared, true)
    assert.deepEqual(__activeProviderStatusToasts(), {})
})
