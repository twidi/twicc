import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'

const read = path => fs.readFileSync(new URL(path, import.meta.url), 'utf8')
const widget = read('./TextSelectionComment.vue')

/** Every `<TextSelectionComment ... />` tag of a source. */
function widgetTags(source) {
    return source.match(/<TextSelectionComment[\s\S]*?\/>/g) ?? []
}

test('the widget declares readOrigin, null by default', () => {
    assert.match(widget, /readOrigin:\s*\{\s*type:\s*String,\s*default:\s*null\s*\}/)
})

test('the Read button sits in the collapsed row, after Copy', () => {
    const collapsed = widget.slice(widget.indexOf('<div v-if="!expanded" class="tsc-collapsed">'), widget.indexOf('<!-- Expanded'))
    const copyAt = collapsed.indexOf(`@click.stop="onCollapsedClick('copy')"`)
    const readAt = collapsed.indexOf(`@click.stop="onCollapsedClick('read')"`)
    assert.ok(copyAt > -1 && readAt > copyAt)
    const button = collapsed.slice(collapsed.lastIndexOf('<wa-button', readAt), collapsed.indexOf('</wa-button>', readAt))
    assert.match(button, /v-if="showRead"/)
    assert.match(button, /@mousedown\.prevent/)
})

test('the Read handler stops while busy, else plays the selection with its origin', () => {
    const handler = widget.slice(widget.indexOf('async function read()'))
    const body = handler.slice(0, handler.indexOf('\n}\n') + 3)
    assert.match(body, /if \(readBusy\.value\) \{\s*speechStore\.stop\(\)\s*return/)
    assert.match(body, /speechStore\.play\(props\.selectedText, \{ origin: props\.readOrigin \}\)/)
    assert.match(body, /!result\.ok && !result\.aborted\) toast\.error\(result\.error/)
    assert.match(body, /result\.ended\.then\(end => \{\s*if \(!end\.ok && !end\.aborted\) toast\.error\(end\.error/)
    assert.doesNotMatch(body, /close\(|clearSourceSelection/)
})

test('SessionItemsList and the FilePane selection widget pass the origin', () => {
    const list = widgetTags(read('./SessionItemsList.vue'))
    assert.equal(list.length, 1)
    assert.match(list[0], /read-origin="rendered"/)
    const tags = widgetTags(read('../../files/FilePane.vue'))
    assert.equal(tags.length, 2)
    const [selection, card] = tags
    assert.match(selection, /read-origin="file"/)
    assert.match(card, /\bpersistent\b/)
    assert.doesNotMatch(card, /read-origin/)
})

test('the other widgets pass no origin', () => {
    for (const path of ['../../browser/BrowserPane.vue', '../../terminal/TerminalPanel.vue']) {
        for (const tag of widgetTags(read(path))) assert.doesNotMatch(tag, /read-origin/)
    }
})

test('the collapsed buttons route touch taps through the tap fallback', () => {
    assert.match(widget, /createTouchTapFallback\(\)/)
    const collapsed = widget.slice(widget.indexOf('<div v-if="!expanded" class="tsc-collapsed">'), widget.indexOf('<!-- Expanded'))
    for (const id of ['expand', 'copy', 'read', 'summary']) {
        assert.ok(collapsed.includes(`@pointerdown="tapFallback.down('${id}', $event)"`), id)
        assert.ok(collapsed.includes(`@pointerup="onTapUp('${id}', $event)"`), id)
        assert.ok(collapsed.includes(`@click.stop="onCollapsedClick('${id}')"`), id)
    }
    assert.equal(collapsed.match(/@pointercancel="tapFallback\.cancel\(\)"/g).length, 4)
    assert.equal(collapsed.match(/@mousedown\.prevent/g).length, 4)
    const expandedPart = widget.slice(widget.indexOf('<!-- Expanded'))
    assert.ok(!expandedPart.includes('tapFallback'))
})

test('the tap fallback is cancelled on unmount, close and panel opening', () => {
    for (const marker of ['onBeforeUnmount(() => {', 'function close() {', 'function showPanel() {']) {
        const at = widget.indexOf(marker)
        assert.ok(at > -1, marker)
        assert.match(widget.slice(at, at + marker.length + 40), /tapFallback\.cancel\(\)/, marker)
    }
})
