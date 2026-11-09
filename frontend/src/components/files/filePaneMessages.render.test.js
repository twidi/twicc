import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { compile } from '@vue/compiler-dom'
import * as Vue from 'vue'
import { renderToString } from '@vue/server-renderer'
import { i18n, loadLocaleMessages } from '../../i18n/index.js'
import { remoteUiMessage } from '../../i18n/remoteMessages.js'
import { resolveUiMessage } from '../../i18n/uiMessages.js'

// Render the real overlay without mounting the unrelated editor and frames.
const source = readFileSync(new URL('./FilePane.vue', import.meta.url), 'utf8')
const start = source.indexOf('<div v-if="showOverlay"')
const end = source.indexOf('\n        </div>\n\n        <!-- Save error', start)
assert.ok(start >= 0 && end > start)
const { code } = compile(source.slice(start, end), { mode: 'function' })
const render = new Function('Vue', code)(Vue)

for (const locale of ['en', 'fr', 'de', 'es', 'pt-BR']) {
    test(`oversized APK renders its error and download action in ${locale}`, async (context) => {
        await loadLocaleMessages(locale)
        const previousLocale = i18n.global.locale.value
        context.after(() => { i18n.global.locale.value = previousLocale })
        i18n.global.locale.value = locale
        const composer = i18n.global
        const error = remoteUiMessage({
            error: 'File too large',
            ui_message: { key: 'backend.file_content.file_too_large', params: { size: '202.3', maximum: '5' } },
        })
        const app = Vue.createSSRApp({
            render,
            setup: () => ({
                showOverlay: true, loading: false, error, tooLarge: true, canDownload: true,
                isBinary: false, handleDownload() {}, resolveUiMessage,
                t: composer.t,
            }),
        })
        app.use(i18n)
        app.config.warnHandler = () => {}
        const html = await renderToString(app)
        assert.ok(html.includes(resolveUiMessage(error)), html)
        assert.ok(html.includes(composer.t('files.actions.download')), html)
        assert.match(html, /<wa-button/)
    })
}
