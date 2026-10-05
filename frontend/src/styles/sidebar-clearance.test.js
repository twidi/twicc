import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join, relative } from 'node:path'

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8')
const consumers = ['../App.vue', '../components/session/layout/SessionLayout.vue', '../components/message/CollapsedBar.vue', '../components/message/MessageInput.vue', '../components/terminal/TerminalExtraKeysBar.vue']

test('all clearance consumers depend on the floating toggle body class', () => {
    for (const path of consumers) {
        const source = read(path)
        assert.ok(source.includes('body.sidebar-toggle-floating'), path)
        assert.doesNotMatch(source.replace(/\/\*[\s\S]*?\*\//g, ''), /body\.sidebar-closed/, path)
    }
})

test('mobile composer, collapsed bar, and extra keys reserve space only for the floating control', () => {
    const cases = [[consumers[2], 'body.sidebar-toggle-floating .collapsed-bar--sidebar-clearance', '4rem'], [consumers[3], 'body.sidebar-toggle-floating .message-input-toolbar', '2.75rem'], [consumers[4], ':global(body.sidebar-toggle-floating .extra-keys-bar)', '4.25rem']]
    for (const [path, selector, padding] of cases) {
        const source = read(path).replace(/\/\*[\s\S]*?\*\//g, '')
        const blocks = [...source.matchAll(/[^{}]*\{\s*@media \(width < 640px\) \{[^}]*}/g)].map(match => match[0])
        const block = blocks.find(block => block.trim().startsWith(`${selector} {`))
        assert.ok(block, path)
        assert.ok(block.includes(`padding-left: ${padding};`), path)
        assert.equal(source.split(`padding-left: ${padding};`).length - 1, 1, `${path}: no unconditional duplicate`)
    }
    for (const path of [consumers[2], consumers[3]]) assert.ok(read(path).includes('@media (width >= 640px)'), path)
})

const srcDir = fileURLToPath(new URL('..', import.meta.url))

test('source files contain no obsolete sidebar names, including comments', () => {
    const obsoleteNames = /sidebar-closed|updateSidebarClosedClass|sidebarClosed|sidebar-toggle-label|search-advanced-button|SidebarViewSwitch|CommandPaletteButton/
    const files = readdirSync(srcDir, { recursive: true, withFileTypes: true })
        .filter(entry => entry.isFile() && /\.(vue|css|js)$/.test(entry.name) && !entry.name.endsWith('.test.js'))
        .map(entry => join(entry.parentPath ?? entry.path, entry.name))
    const offenders = files.filter(path => obsoleteNames.test(readFileSync(path, 'utf8')))
    assert.deepEqual(offenders.map(path => relative(srcDir, path)), [])
})
