import test from 'node:test'
import assert from 'node:assert/strict'

import {
    activePeerResolutionAction,
    chooseReplyTargetSource,
    deliveryPickerTransition,
    existingSessionActionLabel,
    isReplyTargetPickerEligible,
    PEER_MESSAGE_TITLE_MAX_CHARS,
    recoverReplyTargetPagination,
    replySubject,
    shouldShowReplyTargetPreparation,
    waitForNextPaint,
} from './peerReplyTarget.js'

const archivedProjectIds = new Set(['project-archived'])

function session(id, overrides = {}) {
    return {
        id,
        project_id: 'project-live',
        parent_session_id: null,
        hidden: false,
        draft: false,
        archived: false,
        mtime: 0,
        ...overrides,
    }
}

test('uses the exact normal candidate without requesting a load', () => {
    const target = session('target')
    const result = chooseReplyTargetSource('target', [session('other'), target])

    assert.deepEqual(result, { kind: 'candidate', session: target })
    assert.strictEqual(result.session, target)
    assert.equal('sessionId' in result, false)
})

test('requests a by-id load when normal candidates omit the target', () => {
    assert.deepEqual(
        chooseReplyTargetSource('target', [session('other')]),
        { kind: 'load', sessionId: 'target' },
    )
})

test('matches the unpaged picker exclusions without a project-list rule', () => {
    assert.equal(isReplyTargetPickerEligible(session('regular'), archivedProjectIds), true)
    assert.equal(isReplyTargetPickerEligible(
        session('worktree', { project_id: 'project-worktree' }),
        archivedProjectIds,
    ), true)
    assert.equal(isReplyTargetPickerEligible(
        session('stale-project', { project_id: 'project-stale' }),
        archivedProjectIds,
    ), true)

    assert.equal(isReplyTargetPickerEligible(null, archivedProjectIds), false)
    assert.equal(isReplyTargetPickerEligible(
        session('internal', { parent_session_id: 'parent-session' }),
        archivedProjectIds,
    ), false)
    assert.equal(isReplyTargetPickerEligible(session('hidden', { hidden: true }), archivedProjectIds), false)
    assert.equal(isReplyTargetPickerEligible(session('draft', { draft: true }), archivedProjectIds), false)
    assert.equal(isReplyTargetPickerEligible(session('archived', { archived: true }), archivedProjectIds), false)
    assert.equal(isReplyTargetPickerEligible(
        session('archived-project', { project_id: 'project-archived' }),
        archivedProjectIds,
    ), false)
})

test('recovers one eligible page-omitted target in normal sort order', () => {
    const newest = session('newest', { mtime: 30 })
    const target = session('target', { mtime: 20 })
    const oldest = session('oldest', { mtime: 10 })
    const compareSessions = (a, b) => b.mtime - a.mtime

    const result = recoverReplyTargetPagination(
        [newest, oldest], target, archivedProjectIds, compareSessions,
    )

    assert.deepEqual(result.map(candidate => candidate.id), ['newest', 'target', 'oldest'])
    assert.equal(result.filter(candidate => candidate.id === 'target').length, 1)
})

test('leaves existing and ineligible candidate arrays unchanged', () => {
    const target = session('target', { mtime: 20 })
    const candidates = [session('newest', { mtime: 30 }), target]
    const compareSessions = (a, b) => b.mtime - a.mtime

    assert.strictEqual(
        recoverReplyTargetPagination(candidates, target, archivedProjectIds, compareSessions),
        candidates,
    )
    assert.strictEqual(
        recoverReplyTargetPagination(
            candidates,
            session('hidden-target', { hidden: true }),
            archivedProjectIds,
            compareSessions,
        ),
        candidates,
    )
    assert.strictEqual(
        recoverReplyTargetPagination(
            candidates,
            session('archived-project-target', { project_id: 'project-archived' }),
            archivedProjectIds,
            compareSessions,
        ),
        candidates,
    )
    assert.strictEqual(
        recoverReplyTargetPagination(
            candidates,
            session('internal-target', { parent_session_id: 'parent-session' }),
            archivedProjectIds,
            compareSessions,
        ),
        candidates,
    )
    assert.strictEqual(
        recoverReplyTargetPagination(candidates, null, archivedProjectIds, compareSessions),
        candidates,
    )
})

test('waits until a browser paint can complete before continuing', async () => {
    const frames = []
    let settled = false
    const waiting = waitForNextPaint(callback => frames.push(callback))
    waiting.then(() => { settled = true })

    assert.equal(frames.length, 1)
    frames.shift()(0)
    await Promise.resolve()
    assert.equal(settled, false)
    assert.equal(frames.length, 1)

    frames.shift()(16)
    await waiting
    assert.equal(settled, true)
})

test('prepares the first existing-session activation without thread state', () => {
    assert.deepEqual(
        deliveryPickerTransition(null, 'existing', false),
        {
            mode: 'existing',
            prepareExisting: true,
            dismissRefusalConfirmation: true,
        },
    )
})

test('does not prepare the existing-session picker when delivery is globally blocked', () => {
    assert.deepEqual(
        deliveryPickerTransition(null, 'existing', false, true),
        {
            mode: 'existing',
            prepareExisting: false,
            dismissRefusalConfirmation: true,
        },
    )
})

test('a global delivery block exposes only the refusal action', async () => {
    const { peerDeliveryActionVisibility } = await import('./peerReplyTarget.js')
    assert.equal(typeof peerDeliveryActionVisibility, 'function')
    assert.deepEqual(
        peerDeliveryActionVisibility(true, true),
        { delivery: false, refusal: true },
    )
})

test('keeps a mounted existing-session picker warm across mode switches', () => {
    assert.deepEqual(
        deliveryPickerTransition('existing', 'new', true),
        {
            mode: 'new',
            prepareExisting: false,
            dismissRefusalConfirmation: true,
        },
    )
    assert.deepEqual(
        deliveryPickerTransition('new', 'existing', true),
        {
            mode: 'existing',
            prepareExisting: false,
            dismissRefusalConfirmation: true,
        },
    )
    assert.deepEqual(
        deliveryPickerTransition('existing', 'existing', true),
        {
            mode: null,
            prepareExisting: false,
            dismissRefusalConfirmation: true,
        },
    )
})

test('shows preparation while an inbound reply target is unresolved', () => {
    const pendingReply = {
        direction: 'in',
        status: 'pending',
        reply_target: 'target-session',
    }
    assert.equal(shouldShowReplyTargetPreparation(pendingReply, false), true)
    assert.equal(shouldShowReplyTargetPreparation(pendingReply, true), false)
    assert.equal(shouldShowReplyTargetPreparation({ ...pendingReply, reply_target: null }, false), false)
    assert.equal(shouldShowReplyTargetPreparation({ ...pendingReply, direction: 'out' }, false), false)
    assert.equal(shouldShowReplyTargetPreparation({ ...pendingReply, status: 'delivered' }, false), false)
})

test('identifies the one resolution button that owns busy progress', () => {
    assert.equal(activePeerResolutionAction(false, false, 'existing'), null)
    assert.equal(activePeerResolutionAction(true, false, 'existing'), 'existing')
    assert.equal(activePeerResolutionAction(true, false, 'new'), 'new')
    assert.equal(activePeerResolutionAction(true, true, 'new'), 'refuse')
    assert.equal(activePeerResolutionAction(true, false, null), null)
})

test('labels the existing-session action before selection and while prefilling', () => {
    assert.equal(existingSessionActionLabel(false, false), 'Select a session below')
    assert.equal(existingSessionActionLabel(true, false), 'Prefill session composer')
    assert.equal(existingSessionActionLabel(true, true), 'Prefilling…')
})

test('proposes the parent subject with a single Re: prefix', () => {
    assert.equal(replySubject('Front revamp'), 'Re: Front revamp')
    // One prefix per thread, whatever the depth (email convention).
    assert.equal(replySubject('Re: Front revamp'), 'Re: Front revamp')
    assert.equal(replySubject('RE: Front revamp'), 'RE: Front revamp')
    // "Reorg" is not a prefix: the marker needs its colon AND its space.
    assert.equal(replySubject('Reorg'), 'Re: Reorg')
    assert.equal(replySubject('Re:Front'), 'Re: Re:Front')
    // Flattened to one line, like the backend's own title validation.
    assert.equal(replySubject('Front\n  revamp'), 'Re: Front revamp')
    // Nothing to propose: the composer keeps an empty, required field.
    assert.equal(replySubject(''), '')
    assert.equal(replySubject(null), '')
    assert.equal(replySubject('   '), '')
})

test('truncates a proposed subject to the backend cap', () => {
    const long = 'x'.repeat(PEER_MESSAGE_TITLE_MAX_CHARS)
    const subject = replySubject(long)
    assert.equal(subject.length, PEER_MESSAGE_TITLE_MAX_CHARS)
    assert.ok(subject.startsWith('Re: '))
    assert.ok(subject.endsWith('\u2026'))
    // A subject that exactly fits is left alone.
    const exact = 'y'.repeat(PEER_MESSAGE_TITLE_MAX_CHARS - 4)
    assert.equal(replySubject(exact), `Re: ${exact}`)
})
