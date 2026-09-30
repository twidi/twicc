// The projects list loading flag drives the home page spinner (HomeView.vue), which unmounts
// every card. A refresh while projects are already shown (the reconciliation that runs on
// every WebSocket connect, including the first one after the initial load) must not raise
// it, or the home flashes "Loading projects..." and replays its card cascade (step 7b).
// No store harness in the repo: the guard is checked on the source of both loaders.

import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, 'data.js'), 'utf8')

/** Body of the store action `name` (from its signature to the next action). */
function actionBody(name) {
    const start = source.search(new RegExp(`\\n        async ${name}\\(`))
    assert.ok(start >= 0, `action ${name} found`)
    const next = source.slice(start + 1).search(/\n        (async )?[a-zA-Z]+\([^)]*\) \{/)
    return source.slice(start, next < 0 ? undefined : start + 1 + next)
}

for (const name of ['loadProjects', 'loadHomeData']) {
    test(`${name}: raises the list loading flag only on the initial load`, () => {
        const body = actionBody(name)
        assert.match(body, /const isInitialLoad = Object\.keys\(this\.projects\)\.length === 0/)
        const raises = [...body.matchAll(/this\.localState\.projectsList\.loading = (true|false)/g)]
        assert.equal(raises.length, 2, 'one raise, one reset')
        for (const match of raises) {
            const before = body.slice(0, match.index).trimEnd()
            assert.ok(before.endsWith('if (isInitialLoad) {'), `"${match[0]}" guarded by isInitialLoad`)
        }
    })
}
