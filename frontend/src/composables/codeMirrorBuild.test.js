import test from 'node:test'
import assert from 'node:assert/strict'
import { mkdtemp, mkdir, writeFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, dirname } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { build } from 'vite'

// Node resolves package names that browsers cannot resolve. Run the production
// bundler, then import its output outside node_modules to catch unbundled modes.
test('production editor loads every supported language, including TOML', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'twicc-editor-languages-'))
    const fixture = '\0editor-language-fixture'
    const root = fileURLToPath(new URL('../../', import.meta.url))
    const previousWindow = globalThis.window
    const failures = []
    globalThis.window = { dispatchEvent(event) { failures.push(event.payload) } }
    try {
        const { output } = await build({
            configFile: false,
            root,
            logLevel: 'silent',
            plugins: [{
                name: 'editor-language-fixture',
                resolveId(id) { if (id === fixture) return id },
                load(id) {
                    if (id === fixture) return `export { CM6_LANGUAGE_MAP, resolveLanguage } from ${JSON.stringify(join(root, 'src/composables/useCodeMirror.js'))}`
                },
            }],
            build: {
                write: false,
                minify: false,
                // Unused UI integration stays outside this editor language test.
                rollupOptions: { input: fixture, external: [/\/stores\/settings$/], preserveEntrySignatures: 'strict' },
                modulePreload: { resolveDependencies: () => [] },
            },
        })
        await writeFile(join(directory, 'package.json'), '{"type":"module"}')
        for (const item of output) {
            const path = join(directory, item.fileName)
            await mkdir(dirname(path), { recursive: true })
            await writeFile(path, item.type === 'chunk' ? item.code : item.source)
        }
        const entry = output.find(item => item.type === 'chunk' && item.isEntry)
        const { CM6_LANGUAGE_MAP, resolveLanguage } = await import(pathToFileURL(join(directory, entry.fileName)))
        assert.ok(await resolveLanguage('pyproject.toml'), 'TOML must load without falling back to plain text')
        for (const [name, load] of Object.entries(CM6_LANGUAGE_MAP)) {
            const extension = await load()
            const language = extension.language ?? extension
            assert.ok(language.parser, `${name} must load a parser`)
        }
        assert.deepEqual(failures, [], 'language loading must not trigger asset recovery')
    } finally {
        if (previousWindow === undefined) delete globalThis.window
        else globalThis.window = previousWindow
        await rm(directory, { recursive: true, force: true })
    }
})
