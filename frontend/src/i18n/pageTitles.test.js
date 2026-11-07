import test from 'node:test'
import assert from 'node:assert/strict'
import { computed } from 'vue'
import { i18n, loadLocaleMessages } from './index.js'
import { sharedPageTitle, artifactPageTitle } from './pageTitles.js'

// These rendering tests exercise both catalogs; production loads them on demand.
await loadLocaleMessages('pt-BR')

test('JavaScript shell titles follow locale while preserving user titles', () => {
    const previous = i18n.global.locale.value
    const title = 'A user title {literal} <example>'
    const cases = [
        [() => sharedPageTitle({ mode: 'recent' }), 'TwiCC - Shared with you', 'TwiCC - Compartilhado com você'],
        [() => sharedPageTitle({ mode: 'session', meta: { title } }), `TwiCC - Shared session - ${title}`, `TwiCC - Sessão compartilhada - ${title}`],
        [() => sharedPageTitle({ mode: 'doc', meta: { title } }), `TwiCC - Shared artifact - ${title}`, `TwiCC - Artefato compartilhado - ${title}`],
        [() => artifactPageTitle({ mode: 'share', title }), `TwiCC - Shared artifact - ${title}`, `TwiCC - Artefato compartilhado - ${title}`],
        [() => artifactPageTitle({ mode: 'owner' }), 'Artifact', 'Artefato'],
        [() => sharedPageTitle({ mode: 'session', meta: { title: '  ' } }), 'TwiCC - Shared session', 'TwiCC - Sessão compartilhada'],
        [() => sharedPageTitle({ mode: 'doc' }), 'TwiCC - Shared artifact', 'TwiCC - Artefato compartilhado'],
    ]
    try {
        for (const [resolve, en, pt] of cases) {
            const retained = computed(resolve)
            i18n.global.locale.value = 'en'
            assert.equal(retained.value, en)
            i18n.global.locale.value = 'pt-BR'
            assert.equal(retained.value, pt)
            i18n.global.locale.value = 'en'
            assert.equal(retained.value, en)
        }
    } finally { i18n.global.locale.value = previous }
})
