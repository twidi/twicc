import assert from 'node:assert/strict'
import test from 'node:test'

import { DIRECT_TAB_MAP, PROJECT_TABS, directTab, routeNameForTab, tabForRouteName, visibleProjectTabs } from './projectTabs.js'

test('the tab strip is Timeline, Stats, Files, Git (only with a git root), Terminal', () => {
    assert.deepEqual(visibleProjectTabs({ hasGitRepo: true }).map((tab) => tab.id), ['timeline', 'stats', 'files', 'git', 'terminal'])
    assert.deepEqual(visibleProjectTabs({ hasGitRepo: false }).map((tab) => tab.id), ['timeline', 'stats', 'files', 'terminal'])
    assert.equal(PROJECT_TABS[0].label, 'Timeline')
    assert.ok(PROJECT_TABS.every((tab) => tab.label && tab.icon))
})

test('the fixed shortcut map: 1 Timeline, 2 Stats, 3 Files, 4 Git, 5 Terminal', () => {
    assert.deepEqual({ ...DIRECT_TAB_MAP }, { 1: 'timeline', 2: 'stats', 3: 'files', 4: 'git', 5: 'terminal' })
    for (const [key, id] of [[1, 'timeline'], [2, 'stats'], [3, 'files'], [4, 'git'], [5, 'terminal']]) {
        assert.equal(directTab(key, { hasGitRepo: true }), id)
    }
})

test('without a git root key 4 does nothing and key 5 is still the Terminal', () => {
    assert.equal(directTab(4, { hasGitRepo: false }), null)
    assert.equal(directTab(5, { hasGitRepo: false }), 'terminal')
    assert.equal(directTab(3, { hasGitRepo: false }), 'files')
})

test('keys outside 1-5 open nothing', () => {
    for (const key of [0, 6, 9, undefined, 'x']) assert.equal(directTab(key, { hasGitRepo: true }), null)
})

test('the active tab follows the route name; the bare routes are the Timeline', () => {
    assert.equal(tabForRouteName('project'), 'timeline')
    assert.equal(tabForRouteName('projects-all'), 'timeline')
    assert.equal(tabForRouteName('project-stats'), 'stats')
    assert.equal(tabForRouteName('projects-stats'), 'stats')
    assert.equal(tabForRouteName('project-files'), 'files')
    assert.equal(tabForRouteName('projects-git'), 'git')
    assert.equal(tabForRouteName('projects-terminal'), 'terminal')
    assert.equal(tabForRouteName('something-else'), 'timeline')
    assert.equal(tabForRouteName('toString'), 'timeline')
    assert.equal(tabForRouteName(undefined), 'timeline')
})

test('a tab opens its route: bare for the Timeline, /stats and the tool routes otherwise', () => {
    assert.equal(routeNameForTab('timeline', { isAllProjectsMode: false }), 'project')
    assert.equal(routeNameForTab('timeline', { isAllProjectsMode: true }), 'projects-all')
    assert.equal(routeNameForTab('stats', { isAllProjectsMode: false }), 'project-stats')
    assert.equal(routeNameForTab('stats', { isAllProjectsMode: true }), 'projects-stats')
    assert.equal(routeNameForTab('files', { isAllProjectsMode: false }), 'project-files')
    assert.equal(routeNameForTab('terminal', { isAllProjectsMode: true }), 'projects-terminal')
})

test('every tab round-trips through its route name', () => {
    for (const isAllProjectsMode of [false, true]) {
        for (const tab of PROJECT_TABS) {
            assert.equal(tabForRouteName(routeNameForTab(tab.id, { isAllProjectsMode })), tab.id)
        }
    }
})
