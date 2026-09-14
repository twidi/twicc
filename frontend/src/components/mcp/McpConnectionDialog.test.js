import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createPinia } from 'pinia'

const componentUrl = new URL('./McpConnectionDialog.vue', import.meta.url)
const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const isCustomElement = tag => tag.startsWith('wa-')
const { descriptor } = parse(fs.readFileSync(componentUrl, 'utf8'), {
    filename: componentUrl.pathname, templateParseOptions: { isCustomElement },
})
const compiled = compileScript(descriptor, {
    id: 'mcp-connection-dialog-test',
    inlineTemplate: true,
    templateOptions: { compilerOptions: { isCustomElement } },
}).content
    .replace(/from (['"])vue\1/g, `from '${import.meta.resolve('vue')}'`)
    .replace("from '../../stores/mcp'", `from '${new URL('../../stores/mcp.js', import.meta.url)}'`)
    .replace("from '../help/HelpFeatureLink.vue'", `from '${dataModule('export default { render: () => null }')}'`)
const component = (await import(dataModule(compiled))).default

async function render(entry, review) {
    return renderToString(createSSRApp(component, { entry, review }).use(createPinia()))
}

/** The name field, isolated from the verification-code field next to it. */
function nameInput(html) {
    return html.match(/<wa-input\b[^>]*label="Connection name \(optional\)"[^>]*>/)[0]
}

test('reviewing a request prefills the name with the identity the client declared', async () => {
    const html = await render({ id: 'r1', client_name: 'ChatGPT', client_id: 'c1', redirect_uri: 'https://x/cb' }, true)
    assert.match(nameInput(html), /\bvalue="ChatGPT"/)
    // The declared identity still shows on its own line, above the field.
    assert.match(html, /class="mcp-detail__client">ChatGPT</)
    assert.match(html, /The client declared this name/)
})

test('a client that declares nothing leaves the name field empty', async () => {
    const html = await render({ id: 'r1', client_id: 'c1', redirect_uri: 'https://x/cb' }, true)
    assert.doesNotMatch(nameInput(html), /\bvalue="[^"]/)
    assert.match(html, /message via external MCP/)
})

test('a declared name is flattened and capped at the length the backend accepts', async () => {
    const declared = ` Multi\nline   client ${'x'.repeat(120)} `
    const html = await render({ id: 'r1', client_name: declared, client_id: 'c1', redirect_uri: 'https://x/cb' }, true)
    const value = nameInput(html).match(/\bvalue="([^"]*)"/)[1]
    assert.equal(value, `Multi line client ${'x'.repeat(120)}`.slice(0, 80))
    assert.equal(value.length, 80)
})

test('an existing connection keeps its owner name and never adopts the declared one', async () => {
    const named = await render({ id: 'k1', name: 'Desktop assistant', client_name: 'ChatGPT', client_id: 'c1' }, false)
    assert.match(nameInput(named), /\bvalue="Desktop assistant"/)

    // Cleared on purpose by the owner: reopening the details must not refill it.
    const unnamed = await render({ id: 'k1', name: '', client_name: 'ChatGPT', client_id: 'c1' }, false)
    assert.doesNotMatch(nameInput(unnamed), /\bvalue="[^"]/)
})
