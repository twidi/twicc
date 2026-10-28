// Source checks: the `tooltip` prop of ProcessActivityIndicator switches off the AppTooltip of
// all three display modes (the pill of a collapsed tree row carries one tooltip for itself).
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const source = readFileSync(join(here, 'ProcessActivityIndicator.vue'), 'utf8')

test('declares a Boolean tooltip prop defaulting to true', () => {
    assert.match(source, /tooltip:\s*\{[^}]*type:\s*Boolean,\s*default:\s*true[^}]*\}/s)
})

test('every AppTooltip of the template is guarded by the tooltip prop', () => {
    const template = source.slice(source.indexOf('<template>'), source.indexOf('</template>\n\n<style'))
    const all = template.match(/<AppTooltip\b[^>]*>/g) ?? []
    const guarded = template.match(/<AppTooltip\b[^>]*v-if="tooltip"[^>]*>/g) ?? []
    assert.equal(all.length, 3)
    assert.equal(guarded.length, 3)
})

test('the ProcessIndicator branch renders the unread background mode', () => {
    const template = source.slice(source.indexOf('<template>'), source.indexOf('</template>\n\n<style'))
    const tag = template.match(/<ProcessIndicator\b[^>]*>/s)?.[0] ?? ''
    assert.match(tag, /:unread=/)
    assert.match(tag, /summary\.unreadShellCount/)
})
