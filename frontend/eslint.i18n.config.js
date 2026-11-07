import { fileURLToPath } from 'node:url'
import vueI18n from '@intlify/eslint-plugin-vue-i18n'
import vueParser from 'vue-eslint-parser'
import tsParser from '@typescript-eslint/parser'
import vue from 'eslint-plugin-vue'

// Only props that present text to the user. Wire values remain unchanged.
const textAttributes = [
    'title', 'label', 'placeholder', 'alt', 'description', 'help-text', 'tooltip',
    'aria-label', 'aria-description', 'aria-placeholder', 'aria-roledescription', 'aria-valuetext',
    'empty-label', 'loading-label',
]

export default [
    {
        ignores: ['**/node_modules/**', '**/dist/**', '**/*.{test,spec}.{js,ts}',
            '**/__tests__/**', '**/__fixtures__/**', 'public/help/**', 'public/tips/**'],
    },
    { linterOptions: { reportUnusedDisableDirectives: 'off' } },
    ...vueI18n.configs.base,
    {
        files: ['src/**/*.{vue,js,ts}', 'src/i18n/locales/*.json'],
        settings: {
            'vue-i18n': {
                localeDir: fileURLToPath(new URL('./src/i18n/locales/*.json', import.meta.url)),
                messageSyntaxVersion: '~11.3.0',
            },
        },
        rules: {
            '@intlify/vue-i18n/no-missing-keys': 'error',
            '@intlify/vue-i18n/no-missing-keys-in-other-locales': 'error',
            '@intlify/vue-i18n/no-duplicate-keys-in-locale': 'error',
            '@intlify/vue-i18n/valid-message-syntax': 'error',
            '@intlify/vue-i18n/no-html-messages': 'error',
        },
    },
    {
        files: ['src/**/*.vue'],
        // The official Vue processor applies narrowly explained template exceptions.
        plugins: { vue },
        processor: vue.processors.vue,
        languageOptions: {
            parser: vueParser,
            parserOptions: { parser: { ts: tsParser }, ecmaVersion: 'latest', sourceType: 'module' },
        },
        rules: {
            'vue/comment-directive': 'error',
            '@intlify/vue-i18n/no-raw-text': ['error', {
                attributes: { '/.+/': textAttributes },
                ignorePattern: '^[\\p{P}\\p{S}\\p{N}\\s]*$',
            }],
        },
    },
    { files: ['src/**/*.ts'], languageOptions: { parser: tsParser } },
]
