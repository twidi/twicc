import test from 'node:test'
import assert from 'node:assert/strict'
import { getAgentDisplay, getAgentShortId } from './agentLabel.js'

const store = ({ links = {}, sessions = {} }) => ({
    getAgentLinkInfo: id => links[id] || null,
    getSession: id => sessions[id] || null,
})

test('the spawn name wins, and a name of its own is appended', () => {
    const s = store({ links: {
        a1: { displayName: 'Explore — Map the sidebar', ownerSessionId: 'root' },
        a2: { displayName: 'Frontend reader', slug: 'Epicurus', ownerSessionId: 'root' },
    } })
    assert.deepEqual(getAgentDisplay('a1', s), { name: 'Explore — Map the sidebar', isFallback: false })
    assert.deepEqual(getAgentDisplay('a2', s), { name: 'Frontend reader (Epicurus)', isFallback: false })
})
test('a nickname inherited from the launcher is not repeated', () => {
    // Codex hands a nested agent its launcher's nickname.
    const s = store({ links: {
        a1: { displayName: 'Launcher', slug: 'Epicurus', ownerSessionId: 'root' },
        a2: { displayName: 'Backend reader', slug: 'Epicurus', ownerSessionId: 'a1' },
    } })
    assert.equal(getAgentDisplay('a1', s).name, 'Launcher (Epicurus)')
    assert.equal(getAgentDisplay('a2', s).name, 'Backend reader')
})
test('siblings sharing a recycled nickname all keep it', () => {
    // Codex recycles names between agents of one session ("Tesla the 2nd" x4).
    // Only a name inherited from the launcher is dropped, never a repeat
    // between agents at the same level.
    const s = store({
        links: {
            a1: { displayName: 'First pass', slug: 'Tesla the 2nd', ownerSessionId: 'root' },
            a2: { displayName: 'Second pass', slug: 'Tesla the 2nd', ownerSessionId: 'root' },
        },
        sessions: { root: { slug: 'root-session' } },
    })
    assert.equal(getAgentDisplay('a1', s).name, 'First pass (Tesla the 2nd)')
    assert.equal(getAgentDisplay('a2', s).name, 'Second pass (Tesla the 2nd)')
})
test('the session slug Claude copies onto every agent never names one', () => {
    const s = store({
        links: { a1: { slug: 'abundant-honking-firefly', ownerSessionId: 'root' } },
        sessions: { root: { slug: 'abundant-honking-firefly' } },
    })
    assert.deepEqual(getAgentDisplay('a1', s), { name: 'a1', isFallback: true })
})
test('nothing known falls back to the short id, workflow ids included', () => {
    assert.deepEqual(getAgentDisplay('af2695778cf6c742f', store({})), { name: 'af269577', isFallback: true })
    // The run prefix is shared by every agent of the run; only the tail identifies.
    assert.equal(getAgentShortId('wf_34daedf1-96c:a59b84c79c349de40'), 'a59b84c7')
})
