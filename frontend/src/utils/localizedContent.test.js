import test from 'node:test'
import assert from 'node:assert/strict'
import { createLocalizedContentLoader } from './localizedContent.js'

test('loads the requested language, strips English front matter and preserves technical Markdown', async () => {
    const originalFetch = globalThis.fetch
    const urls = []
    globalThis.fetch = async url => {
        urls.push(url)
        if (url === '/help/pt-BR/worktrees.md') return new Response('Leia `git worktree` e [projetos](help/projects).')
        if (url === '/help/worktrees.md') return new Response('---\ntitle: Worktrees\n---\nRead `git worktree`.')
        throw new Error(`Unexpected URL: ${url}`)
    }
    try {
        const load = createLocalizedContentLoader('help')
        assert.deepEqual(await load('worktrees', 'pt-BR'), { body: 'Leia `git worktree` e [projetos](help/projects).', locale: 'pt-BR' })
        assert.deepEqual(await load('worktrees', 'en'), { body: 'Read `git worktree`.', locale: 'en' })
        assert.deepEqual(await load('worktrees', 'invalid'), { body: 'Read `git worktree`.', locale: 'en' })
        assert.deepEqual(urls, ['/help/pt-BR/worktrees.md', '/help/worktrees.md'])
    } finally { globalThis.fetch = originalFetch }
})

for (const reason of ['404', 'html', 'empty', 'network']) {
    test(`falls back to English for a ${reason} translation response`, async () => {
        const originalFetch = globalThis.fetch
        globalThis.fetch = async url => {
            if (url === '/tips/pt-BR/artifacts.md') {
                if (reason === '404') return new Response('', { status: 404 })
                if (reason === 'html') return new Response('<!doctype html><html><body>SPA</body></html>', { headers: { 'Content-Type': 'text/html' } })
                if (reason === 'empty') return new Response('   ')
                throw new TypeError('Failed to fetch')
            }
            assert.equal(url, '/tips/artifacts.md')
            return new Response('English artifact help.')
        }
        try {
            const load = createLocalizedContentLoader('tips')
            assert.deepEqual(await load('artifacts', 'pt-BR'), { body: 'English artifact help.', locale: 'en' })
        } finally { globalThis.fetch = originalFetch }
    })
}

test('rejects missing English content instead of displaying an SPA fallback document', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = async () => new Response('<!doctype html><html>Not Markdown</html>')
    try {
        await assert.rejects(createLocalizedContentLoader('help')('projects', 'en'), /content|Markdown/i)
    } finally { globalThis.fetch = originalFetch }
})

test('development reloads fresh bodies and build URLs retain the asset version', async () => {
    const originalFetch = globalThis.fetch
    globalThis.__PUBLIC_ASSETS_VERSION__ = 'test-build'
    let count = 0
    globalThis.fetch = async url => {
        assert.equal(url, '/tips/pt-BR/workspaces.md?v=test-build')
        count++
        return new Response(count === 1 ? 'Antes.' : 'Depois.')
    }
    try {
        const load = createLocalizedContentLoader('tips', { cache: false })
        assert.equal((await load('workspaces', 'pt-BR')).body, 'Antes.')
        assert.equal((await load('workspaces', 'pt-BR')).body, 'Depois.')
    } finally {
        globalThis.fetch = originalFetch
        delete globalThis.__PUBLIC_ASSETS_VERSION__
    }
})
