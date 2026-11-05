import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { isChromium, STATIC_TOOLBAR_CLASS } from './staticBrowserToolbar.js'

const src = join(dirname(fileURLToPath(import.meta.url)), '..')

// User agents measured on Android devices.
test('1. Chrome Android is detected on the phone and tablet UIs', () => {
    assert.equal(isChromium('Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Mobile Safari/537.36'), true)
    assert.equal(isChromium('Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36'), true)
})

test('2. Firefox keeps the Web Awesome rule: its toolbar hides on the same drag today', () => {
    assert.equal(isChromium('Mozilla/5.0 (Android 16; Mobile; rv:160.0) Gecko/160.0 Firefox/160.0'), false)
})

test('3. the class sizes the body to the visible area, and is set before the app renders', () => {
    const app = readFileSync(join(src, 'App.vue'), 'utf8')
    assert.match(app, new RegExp(`html\\.${STATIC_TOOLBAR_CLASS} body \\{\\n\\s+min-height: 100dvh;`))
    const main = readFileSync(join(src, 'main.js'), 'utf8')
    assert.ok(main.indexOf('\ninitStaticBrowserToolbar()\n') > -1)
    assert.ok(main.indexOf('\ninitStaticBrowserToolbar()\n') < main.indexOf('createApp('))
})
