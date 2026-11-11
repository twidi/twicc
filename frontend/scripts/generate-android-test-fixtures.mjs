import { build } from 'vite'
import { fileURLToPath } from 'node:url'
import { readFile } from 'node:fs/promises'
import { resolve } from 'node:path'

const frontend = fileURLToPath(new URL('../', import.meta.url))

// Use the installed frontend bundler explicitly during catalog generation.
// Each module is self-contained because native fixture servers serve only these
// adapter paths. No bare imports, JSON import attributes, or locale chunks can leak.
export async function generateAndroidTestFixtures(manifest) {
    const outputs = {}
    for (const [name, entry] of Object.entries({
        androidAppClient: 'utils/androidAppClient',
        nativeTtsClient: 'utils/nativeTtsClient',
        androidLocale: 'i18n/androidLocale',
    })) {
        const result = await build({
            root: frontend,
            configFile: false,
            // These native test adapters need only the selected native translations.
            // Embedding web catalogs would make unrelated web edits invalidate Android fixtures.
            plugins: [{
                name: 'android-native-translations',
                enforce: 'pre',
                async load(id) {
                    const path = id.split('?')[0]
                    if (!manifest.locales.some(locale => path === resolve(frontend, `src/i18n/locales/${locale}.json`))) return null
                    const catalog = JSON.parse(await readFile(path, 'utf8'))
                    return JSON.stringify(Object.fromEntries(Object.keys(manifest.messages).sort().map(key => [key, catalog[key]])))
                },
            }],
            logLevel: 'silent',
            define: {
                'process.env.NODE_ENV': '"production"',
                __VUE_I18N_FULL_INSTALL__: 'false',
                __VUE_I18N_LEGACY_API__: 'false',
                __INTLIFY_PROD_DEVTOOLS__: 'false',
            },
            build: {
                write: false,
                target: 'es2020',
                minify: true,
                sourcemap: false,
                lib: { entry: `${frontend}src/${entry}.js`, formats: ['es'], fileName: () => `${name}.js` },
                rolldownOptions: { output: { codeSplitting: false } },
            },
        })
        const output = (Array.isArray(result) ? result[0] : result).output
        if (output.length !== 1 || output[0].type !== 'chunk' || output[0].imports.length
            || output[0].dynamicImports.some(path => path !== `${name}.js`)) {
            throw new Error(`Android fixture must be one self-contained module: ${name}`)
        }
        outputs[`android/app/src/androidTest/generated/${name}.js`] = output[0].code
    }
    return outputs
}
