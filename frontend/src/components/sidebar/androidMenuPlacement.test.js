import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript, compileTemplate, compileStyle } from '@vue/compiler-sfc'

test('one global Android menu mounts outside app-readiness gates; the rail only provides its first anchor', () => {
    const app = parse(readFileSync(new URL('../../App.vue', import.meta.url), 'utf8')).descriptor
    const top = app.template.ast.children.filter(child => child.type === 1)
    const menus = top.filter(node => node.tag === 'AndroidAppMenu')
    assert.equal(menus.length, 1)
    assert.ok(!menus[0].props.some(prop => prop.rawName === 'v-if'))
    const rail = readFileSync(new URL('./SidebarRail.vue', import.meta.url), 'utf8')
    assert.match(rail, /<div class="panel-card">\s*<AndroidAppMenuAnchor\s*\/>/)
    assert.doesNotMatch(rail, /<AndroidAppMenu\s*\/>/)
})
test('empty anchor has no browser layout box and its SFC compiles', () => {
    const source = readFileSync(new URL('./AndroidAppMenuAnchor.vue', import.meta.url), 'utf8')
    const { descriptor, errors } = parse(source, { filename: 'AndroidAppMenuAnchor.vue' })
    assert.deepEqual(errors, [])
    const script = compileScript(descriptor, { id: 'anchor' })
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'AndroidAppMenuAnchor.vue', id: 'anchor', compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
    for (const style of descriptor.styles) {
        assert.deepEqual(compileStyle({ source: style.content, filename: 'AndroidAppMenuAnchor.vue', id: 'anchor', scoped: true }).errors, [])
    }
    assert.match(source, /display: contents/)
})

test('the refresh confirmation stays outside the Teleport that moves the menu trigger', () => {
    const { descriptor } = parse(readFileSync(new URL('./AndroidAppMenu.vue', import.meta.url), 'utf8'))
    let found = false
    function visit(node, inTeleport = false) {
        if (node.type !== 1 && node.type !== 0) return
        if (node.tag === 'wa-dialog') { found = true; assert.equal(inTeleport, false) }
        for (const child of node.children ?? []) visit(child, inTeleport || node.tag === 'Teleport')
    }
    visit(descriptor.template.ast)
    assert.ok(found)
})
