import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileTemplate } from '@vue/compiler-sfc'
import { parse as parseTemplate } from '@vue/compiler-dom'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { createAppI18n } from './index.js'

// Compile the actual component markup, including slot contents and handlers.
// Render only the message so these tests need no stores, DOM or provider process.
function messageRender(file, key) {
    const source = readFileSync(new URL(`../components/${file}`, import.meta.url), 'utf8')
    const { descriptor } = parse(source)
    let node
    function visit(value) {
        if (value.tag === 'i18n-t' && value.props.some(prop =>
            prop.name === 'keypath' ? prop.value.content === key :
                prop.arg?.content === 'keypath' && prop.exp.content.includes(`'${key}'`))) node = value
        for (const child of value.children ?? []) visit(child)
    }
    visit(parseTemplate(descriptor.template.content))
    assert.ok(node, `${file}: ${key}`)
    const compiled = compileTemplate({
        source: node.loc.source, filename: file, id: 'rich-message-test',
        compilerOptions: { mode: 'function', isCustomElement: tag => tag.startsWith('wa-') },
    })
    assert.deepEqual(compiled.errors, [])
    return runInNewContext(`(function() { ${compiled.code} })()`, { Vue })
}

async function renderMessage(file, key, locale, data = {}, override) {
    const instance = createAppI18n(locale)
    if (override) instance.global.mergeLocaleMessage(locale, { [key]: override })
    const app = Vue.createSSRApp({ render: messageRender(file, key), data: () => ({ n: instance.global.n, ...data }) })
    app.use(instance)
    return (await renderToString(app)).replace(/<!--[\s\S]*?-->/g, '')
}

test('complete rich hints preserve their elements and global scope in both locales', async context => {
    const warnings = []
    context.mock.method(console, 'warn', (...args) => warnings.push(args.join(' ')))
    for (const locale of ['en', 'pt-BR']) {
        const project = await renderMessage('project/ProjectList.vue', 'project.list.namingHint', locale)
        assert.match(project, /<wa-icon name="ellipsis-v" auto-width><\/wa-icon>/)
        assert.match(project, locale === 'en' ? /click the <wa-icon/ : /clique no menu <wa-icon/)
        const workspace = await renderMessage('workspace/WorkspaceList.vue', 'workspace.list.intro', locale)
        assert.match(workspace, /<wa-icon name="plus" auto-width><\/wa-icon>/)
        const wildcard = await renderMessage('workspace/WorkspaceManageDialog.vue', 'workspace.edit.patterns.wildcardHint', locale)
        assert.match(wildcard, /Use <code>\*<\/code>/)
        const action = await renderMessage('workspace/WorkspaceManageDialog.vue', 'workspace.edit.patterns.addExistingHint', locale)
        assert.match(action, locale === 'en'
            ? /click <strong>Add matching projects now<\/strong>\.$/
            : /clique em <strong>Adicionar projetos correspondentes agora<\/strong>\.$/)
        const prompt = await renderMessage('app/SettingsPopover.vue', 'settings.titles.systemPrompt.hint', locale)
        assert.match(prompt, /<code>\{text\}<\/code>/)
        assert.match(prompt, locale === 'en' ? /Press Apply to save/ : /Pressione Aplicar para salvar/)
        const notifications = await renderMessage('app/SettingsPopover.vue', 'settings.usage.extraUsageNotification.hint', locale, { selectSection() {} })
        assert.match(notifications, locale === 'en' ? /<a href="#">Notifications<\/a> tab/ : /aba <a href="#">Notificações<\/a>/)
    }
    assert.deepEqual(warnings, [])
})

test('inherited trust messages include the optional source and escape project names', async () => {
    for (const locale of ['en', 'pt-BR']) {
        for (const state of [true, false]) {
            const data = { resolvedTrust: { state }, resolvedSourceName: '<project>&' }
            const html = await renderMessage('project/ProjectEditDialog.vue', 'project.trust.inheritedFrom', locale, data)
            assert.match(html, /<strong>[^<]+<\/strong>/)
            assert.match(html, locale === 'en' ? /\(from &lt;project&gt;&amp;\)$/ : /\(de &lt;project&gt;&amp;\)$/)
            data.resolvedSourceName = null
            const withoutSource = await renderMessage('project/ProjectEditDialog.vue', 'project.trust.inherited', locale, data)
            assert.doesNotMatch(withoutSource, /\(|null|undefined/)
            assert.match(withoutSource, /<strong>[^<]+<\/strong>$/)
        }
    }
})

test('a translation can move rich slots to a different position', async () => {
    const html = await renderMessage('workspace/WorkspaceManageDialog.vue', 'workspace.edit.patterns.wildcardHint', 'pt-BR', {}, '{wildcard}: reordered')
    assert.equal(html, '<code>*</code>: reordered')
})

test('request toasts keep sender names and translated prose in one inline container', async () => {
    for (const locale of ['en', 'pt-BR']) {
        const mcp = await renderMessage('mcp/McpToast.vue', 'mcp.toast.request', locale, { clientName: '<client>&' })
        assert.match(mcp, /^<span class="mcp-toast-sentence"><strong>&lt;client&gt;&amp;<\/strong> /)
        assert.match(mcp, /<\/span>$/)
        const peer = await renderMessage('peer/PeerToastContent.vue', 'peer.toast.pairRequest', locale,
            { peer: { name: '<peer>&' } })
        assert.match(peer, /^<span><strong>&lt;peer&gt;&amp;<\/strong> /)
        assert.match(peer, /<\/span>$/)
    }
})

test('workflow completion agreement follows completed phases while the phase label follows total phases', async () => {
    for (const locale of ['en', 'pt-BR']) {
        for (const completed of [0, 1, 2]) {
            const html = await renderMessage('workflows/WorkflowRunDetail.vue', 'workflows.detail.incomplete', locale,
                { phaseCompletion: { completed, total: 3 } })
            assert.match(html, new RegExp(`<strong>${completed} ${locale === 'en' ? 'of its 3 phases' : 'de suas 3 fases'}</strong>`))
            if (locale === 'pt-BR') assert.match(html, completed === 1 ? /foi concluída/ : /foram concluídas/)
        }
    }
})

test('artifact host warnings escape host names and retain count selection', async () => {
    for (const locale of ['en', 'pt-BR']) {
        for (const blockedHosts of [['<host>&'], ['<host>&', 'other.test']]) {
            const html = await renderMessage('../artifact-shell/ArtifactShellApp.vue', 'artifacts.shell.blockedHosts', locale,
                { blockedHosts, blockedHostList: new Intl.ListFormat(locale, { style: 'long', type: 'conjunction' }).format(blockedHosts) })
            assert.match(html, /<code>&lt;host&gt;&amp;/)
            assert.doesNotMatch(html, /<host>|undefined/)
            assert.match(html, locale === 'en'
                ? blockedHosts.length === 1 ? /this host is not authorized/ : /these hosts are not authorized/
                : blockedHosts.length === 1 ? /este host não foi autorizado/ : /estes hosts não foram autorizados/)
        }
    }
})
