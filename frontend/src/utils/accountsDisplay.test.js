import test from 'node:test'
import assert from 'node:assert/strict'
import { strongerLevel, userInitials } from './accountsDisplay.js'

test('strongerLevel keeps the strongest grant', () => {
    assert.equal(strongerLevel('', ''), '')
    assert.equal(strongerLevel('view', ''), 'view')
    assert.equal(strongerLevel('view', 'operate'), 'operate')
    assert.equal(strongerLevel(null, 'view'), 'view')
})

test('userInitials uses the name, else the email', () => {
    assert.equal(userInitials({ display_name: 'Ada Lovelace' }), 'AL')
    assert.equal(userInitials({ email: 'ada.lovelace@example.com' }), 'AL')
    assert.equal(userInitials({ email: 'x' }), 'X')
    assert.equal(userInitials(null), '?')
})

import { buildAccessMatrix, grantsPayload } from './accountsDisplay.js'

test('buildAccessMatrix groups by workspace and shows inherited levels', () => {
    const { groups } = buildAccessMatrix({
        workspaces: [
            { id: 'fpf', name: 'FPF', projectIds: ['a', 'b'] },
            { id: 'hub', name: 'Hub', projectIds: ['b'] },
        ],
        projects: [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'a-wt', worktree_of: 'a' }],
        projectLevels: { b: 'view', c: 'operate' },
        workspaceLevels: { hub: 'operate' },
    })
    assert.equal(groups.length, 3)
    assert.deepEqual(groups[0].rows.map(r => [r.project.id, r.direct, r.inherited, r.effective]), [
        ['a', '', '', ''],
        ['b', 'view', 'operate', 'operate'],
    ])
    assert.equal(groups[1].level, 'operate')
    assert.equal(groups[2].workspace, null)
    assert.deepEqual(groups[2].rows.map(r => r.project.id), ['c'])
})

test('grantsPayload drops "no access" entries', () => {
    assert.deepEqual(grantsPayload({ a: 'view', b: '' }, { w: 'operate', x: 'none' }), {
        project_grants: [{ project_id: 'a', level: 'view' }],
        workspace_grants: [{ workspace_id: 'w', level: 'operate' }],
    })
})
