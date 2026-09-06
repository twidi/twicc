import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { parse, compileScript } from '@vue/compiler-sfc'
import { createSSRApp } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { createPinia } from 'pinia'
import { useMcpStore } from '../../stores/mcp.js'

const componentUrl = new URL('./McpSettings.vue', import.meta.url)
const dataModule = source => `data:text/javascript;base64,${Buffer.from(source).toString('base64')}`
const isCustomElement = tag => tag.startsWith('wa-')
function compileProtection() {
    const url = new URL('./McpProtection.vue', import.meta.url)
    const { descriptor } = parse(fs.readFileSync(url, 'utf8'), {
        filename: url.pathname, templateParseOptions: { isCustomElement },
    })
    return compileScript(descriptor, {
        id: 'mcp-protection-test', inlineTemplate: true,
        templateOptions: { compilerOptions: { isCustomElement } },
    }).content
        .replace(/from (['"])vue\1/g, `from '${import.meta.resolve('vue')}'`)
        .replace("from '../../stores/mcp'", `from '${new URL('../../stores/mcp.js', import.meta.url)}'`)
}
const protectionModule = dataModule(compileProtection())
const { descriptor } = parse(fs.readFileSync(componentUrl, 'utf8'), {
    filename: componentUrl.pathname, templateParseOptions: { isCustomElement },
})
const compiled = compileScript(descriptor, {
    id: 'mcp-settings-test',
    inlineTemplate: true,
    templateOptions: { compilerOptions: { isCustomElement } },
}).content
    .replace(/from (['"])vue\1/g, `from '${import.meta.resolve('vue')}'`)
    .replace("from '../../stores/mcp'", `from '${new URL('../../stores/mcp.js', import.meta.url)}'`)
    .replace("from '../help/HelpFeatureLink.vue'", `from '${dataModule('export default { render: () => null }')}'`)
    .replace("from './McpProtection.vue'", `from '${protectionModule}'`)
const component = (await import(dataModule(compiled))).default

async function render(config, protection = {}) {
    const pinia = createPinia()
    useMcpStore(pinia).config = { mcpBaseUrl: 'https://mcp.example.com', ...config }
    useMcpStore(pinia).protection = protection
    return renderToString(createSSRApp(component).use(pinia))
}

test('without a password the switch is disabled and a danger callout follows it', async () => {
    const html = await render({ passwordConfigured: false, externalMcpEnabled: true })
    const control = html.match(/<wa-switch\b[^>]*>/)[0]
    assert.match(control, /\bdisabled(?:="true"|="")?(?:\s|>)/)
    assert.doesNotMatch(control, /\bchecked(?:="true"|="")?(?:\s|>)/)
    assert.match(html, /<\/wa-switch>\s*<wa-callout[^>]*variant="danger"[^>]*>[^<]*password/i)
})

test('the switch stays disabled before the password status arrives', async () => {
    const html = await render({})
    assert.match(html.match(/<wa-switch\b[^>]*>/)[0], /\bdisabled(?:="true"|="")?(?:\s|>)/)
})

test('with a password the switch is usable and the password warning is absent', async () => {
    const html = await render({ passwordConfigured: true, externalMcpEnabled: true })
    const control = html.match(/<wa-switch\b[^>]*>/)[0]
    assert.doesNotMatch(control, /\bdisabled(?:="true"|="")?(?:\s|>)/)
    assert.match(control, /\bchecked(?:="true"|="")?(?:\s|>)/)
    assert.doesNotMatch(html, /<wa-callout/)
})

test('automatic protection explains the pause and keeps manual suspension available', async () => {
    const html = await render({ passwordConfigured: true, externalMcpEnabled: true }, {
        paused: true, retryAfter: 600, incident: { reason: 'Repeated OAuth admission limits' },
        registrations: 10, authorizations: 3, rejections: 20, sources: 4,
    })
    assert.match(html, /variant="danger"/)
    assert.match(html, /Suspected OAuth abuse/)
    assert.match(html, /10 more minutes/)
    assert.match(html, /Existing connections, token renewals/)
    assert.match(html, /10 registration attempts/)
    assert.match(html, /Suspend all external access/)
    assert.match(html, /Preserves authorizations/)
    assert.doesNotMatch(html, /Dismiss alert/)
})

test('an expired pause can be dismissed without claiming that disabled MCP is available', async () => {
    const html = await render({ passwordConfigured: true, externalMcpEnabled: false }, {
        paused: false, incident: { reason: 'Repeated OAuth admission limits' },
    })
    assert.match(html, /External MCP access is off/)
    assert.match(html, /Dismiss alert/)
    assert.doesNotMatch(html, /New connections are available again/)
    assert.doesNotMatch(html, /Suspend all external access/)
})
