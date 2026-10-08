import test from 'node:test'
import assert from 'node:assert/strict'
const { changeAffectsHtmlPage } = await import('./fileRelevance.js').catch(() => ({}))
test('inline preview reloads only its own code and assets', () => {
    assert.equal(typeof changeAffectsHtmlPage, 'function')
    const page = 'inline-artifacts/a/index.html'
    for (const path of [page, 'inline-artifacts/a/main.js', 'inline-artifacts/a/assets/logo.svg']) assert.equal(changeAffectsHtmlPage(page, [path]), true, path)
    for (const path of ['inline-artifacts/b/index.html', 'inline-artifacts/b/data/value.json', 'inline-artifacts/a/data/value.json', 'inline-artifacts/a/data', 'data/value.json', 'outside.js']) assert.equal(changeAffectsHtmlPage(page, [path]), false, path)
})
test('ordinary preview folder and root relevance remains unchanged', () => {
    assert.equal(typeof changeAffectsHtmlPage, 'function')
    assert.equal(changeAffectsHtmlPage('page/index.html', ['page/assets/a.png']), true)
    assert.equal(changeAffectsHtmlPage('page/index.html', ['sibling/a.png']), false)
    assert.equal(changeAffectsHtmlPage('index.html', ['main.js']), true)
    assert.equal(changeAffectsHtmlPage('index.html', ['data/a.json']), false)
})
