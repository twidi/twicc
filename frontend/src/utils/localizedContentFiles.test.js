import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import MarkdownIt from 'markdown-it'
import { DEFAULT_LOCALE, SUPPORTED_LOCALES } from '../i18n/index.js'
import { stripFrontMatter } from './frontMatter.js'

const markdown = new MarkdownIt({ html: false })

function technicalContent(body) {
    const code = [], destinations = []
    function inspect(tokens) {
        for (const token of tokens) {
            if (['fence', 'code_block', 'code_inline'].includes(token.type)) {
                code.push([token.type, token.info, token.content])
            }
            if (token.type === 'image') destinations.push(token.attrGet('src'))
            if (token.type === 'link_open') destinations.push(token.attrGet('href'))
            if (token.children) inspect(token.children)
        }
    }
    inspect(markdown.parse(body, {}))
    return { code, destinations }
}

for (const folder of ['help', 'tips']) {
    const directory = new URL(`../../public/${folder}/`, import.meta.url)
    const sources = readdirSync(directory).filter(name => /^[a-z0-9-]+\.md$/.test(name)).sort()
    for (const { value: locale } of SUPPORTED_LOCALES.filter(locale => locale.value !== DEFAULT_LOCALE)) {
        test(`${folder}/${locale} contains exactly the source content files`, () => {
            const translated = readdirSync(new URL(`${locale}/`, directory)).filter(name => name.endsWith('.md')).sort()
            assert.deepEqual(translated, sources)
        })
        for (const file of sources) {
            test(`${folder}/${locale}/${file} preserves technical code and link destinations`, () => {
                const english = stripFrontMatter(readFileSync(new URL(file, directory), 'utf8'))
                const translated = readFileSync(new URL(`${locale}/${file}`, directory), 'utf8')
                assert.ok(translated.trim(), 'translated body must not be empty')
                assert.doesNotMatch(translated, /^---\r?\n/, 'metadata belongs to the shared source and catalogs')
                assert.notEqual(translated.trim(), english.trim(), 'body must be translated')
                assert.deepEqual(technicalContent(translated), technicalContent(english))
            })
        }
    }
}
