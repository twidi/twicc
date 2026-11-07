import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import { parse as parseTemplate } from '@vue/compiler-dom'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createAppI18n } from './index.js'

function controlRender(file, className) {
    const source = readFileSync(new URL(`../components/session/layout/${file}`, import.meta.url), 'utf8')
    const { descriptor } = parse(source)
    let node
    function visit(value) {
        if (value.tag === 'wa-button' && value.props.some(prop =>
            prop.name === 'class' && prop.value.content.split(' ').includes(className))) node = value
        for (const child of value.children ?? []) visit(child)
    }
    visit(parseTemplate(descriptor.template.content))
    assert.ok(node, `${file}: ${className}`)
    const compiled = compileTemplate({
        source: node.loc.source, filename: file, id: 'translated-control-test',
        compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') },
    })
    assert.deepEqual(compiled.errors, [])
    return runInNewContext(`(function() { ${compiled.code} })()`, { Vue })
}

test('layout icon controls expose translated accessible names in both locales', async () => {
    const controls = [
        ['DockRegion.vue', 'dock-restore', 'session.layout.restore'],
        ['DockRegion.vue', 'dock-minimize', 'session.layout.minimize'],
        ['DockRegion.vue', 'dock-maximize', 'session.layout.maximize'],
        ['LayoutMenu.vue', 'layout-menu-trigger', 'session.layout.menuLabel'],
        ['LayoutOverlay.vue', 'overlay-close', 'session.layout.closeOverlay'],
        ['TabPlacementMenu.vue', 'placement-trigger', 'session.layout.placeTab'],
    ]
    for (const locale of ['en', 'pt-BR']) {
        for (const [file, className, key] of controls) {
            const i18n = createAppI18n(locale)
            const app = Vue.createSSRApp({
                render: controlRender(file, className), data: () => ({ maximized: true }),
            })
            app.use(i18n)
            const html = await renderToString(app)
            assert.ok(html.includes(`aria-label="${i18n.global.t(key)}"`), `${locale}: ${className}`)
            assert.doesNotMatch(html, /aria-:label|\$t\(/)
        }
    }
})
