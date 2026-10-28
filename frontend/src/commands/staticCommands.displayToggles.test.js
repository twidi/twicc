import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('./staticCommands.js', import.meta.url), 'utf8')

function commandBlock(id) {
    const start = source.indexOf(`id: '${id}'`)
    assert.notEqual(start, -1, `${id} is missing`)
    return source.slice(start, source.indexOf('\n        },', start))
}

const toggles = [
    ['display.toggle-sessions-as-tree', 'Toggle Show Orchestrated Sessions As Tree', 'sitemap', 'ShowSessionsAsTree', 'showSessionsAsTree'],
    ['display.toggle-provider-icons', 'Toggle Show Provider Icons', 'icons', 'ShowProviderIcons', 'showProviderIcons'],
]

for (const [id, label, icon, suffix, key] of toggles) {
    test(`${id} toggles the ${key} setting`, () => {
        const block = commandBlock(id)
        assert.match(block, new RegExp(`label: '${label}'`))
        assert.match(block, new RegExp(`icon: '${icon}'`))
        assert.match(block, /category: 'display'/)
        assert.match(block, new RegExp(`toggled: \\(\\) => settings\\.is${suffix}\\b`))
        assert.match(block, new RegExp(`action: \\(\\) => settings\\.set${suffix}\\(!settings\\.${key}\\)`))
    })
}
