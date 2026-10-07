import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { runInNewContext } from 'node:vm'
import { parse, compileScript, compileTemplate } from '@vue/compiler-sfc'

const source = readFileSync(new URL('./AppTooltip.vue', import.meta.url), 'utf8')

function fixture(trigger = 'manual', runtime = null) {
    const document = new EventTarget()
    const element = new EventTarget()
    element.trigger = trigger
    element.anchor = {}
    element.hiddenCount = 0
    element.show = () => { element.open = true; element.dispatchEvent(new Event('wa-show')) }
    element.hide = () => {
        element.open = false
        element.hiddenCount++
        element.dispatchEvent(new Event('wa-after-hide'))
    }
    let watcher
    let cleanup
    const { descriptor } = parse(source)
    const script = descriptor.script.content.replace(/export function /g, 'function ')
    const setup = descriptor.scriptSetup.content.replace(/^import .*$/gm, '')
    const createApi = runtime?.createApi || runInNewContext(`${script}\n(function() { ${setup}\n;return {tooltipEl, hideAllTooltips, onTooltipDismissal, show, hide, setTrigger, isOpen: typeof isOpen === 'function' ? isOpen : undefined} })`, {
        // Native lifecycle serialization has its own integration tests.
        document, clearTimeout, Set, serializeTooltipTransitions: () => {},
        defineProps: () => ({ force: true, interactive: true }), defineExpose: () => {},
        useSettingsStore: () => ({ isTouchDevice: true }), computed: fn => ({ get value() { return fn() } }),
        ref: () => ({ value: null }), watch: (_, callback) => { watcher = callback },
        onBeforeUnmount: callback => { cleanup = callback },
    })
    const api = createApi()
    const hooks = runtime ? runtime.takeHooks() : { watcher, cleanup }
    api.tooltipEl.value = element
    hooks.watcher([element, true])
    const press = path => {
        const event = new Event('pointerdown')
        event.composedPath = () => path
        document.dispatchEvent(event)
    }
    return { api, element, cleanup: hooks.cleanup, press,
        createOther: trigger => fixture(trigger, { createApi, takeHooks: () => ({ watcher, cleanup }) }) }
}

test('manual and click tooltips dismiss outside while their anchor and content stay interactive', () => {
    for (const trigger of ['manual', 'click']) {
        const f = fixture(trigger)
        f.api.show()
        f.press([f.element.anchor]); f.press([{}, f.element])
        assert.equal(f.element.hiddenCount, 0)
        f.press([{}]); assert.equal(f.element.hiddenCount, 1)
        f.press([{}]); assert.equal(f.element.hiddenCount, 1, 'hidden tooltip removes outside listener')
        f.cleanup()
    }
    const f = fixture('hover focus')
    f.api.show(); f.press([{}]); assert.equal(f.element.hiddenCount, 0)
    f.cleanup()
})

test('global dismissal cancels pending rail activation and unregisters its callback', () => {
    const f = fixture()
    let cancellations = 0
    const unregister = f.api.onTooltipDismissal(() => cancellations++)
    f.api.hideAllTooltips()
    assert.equal(cancellations, 1)
    assert.equal(f.element.hiddenCount, 1)
    unregister(); f.api.hideAllTooltips(); assert.equal(cancellations, 1)
    f.cleanup(); f.api.hideAllTooltips(); assert.equal(f.element.hiddenCount, 2)
})

test('manual trigger changes synchronously and show/hide use the existing element', () => {
    const f = fixture('hover focus')
    f.api.setTrigger('manual'); assert.equal(f.element.trigger, 'manual')
    f.api.show(); f.api.hide(); assert.equal(f.element.hiddenCount, 1)
    f.api.setTrigger('hover focus'); assert.equal(f.element.trigger, 'hover focus')
    f.cleanup()
})

test('AppTooltip SFC compiles its public API and template', () => {
    const { descriptor, errors } = parse(source, { filename: 'AppTooltip.vue' })
    assert.deepEqual(errors, [])
    const script = compileScript(descriptor, { id: 'tooltip-test' })
    assert.deepEqual(compileTemplate({ source: descriptor.template.content, filename: 'AppTooltip.vue', id: 'tooltip-test',
        compilerOptions: { bindingMetadata: script.bindings } }).errors, [])
})


test('interactive previews share mutual exclusion across component instances', () => {
    const first = fixture()
    const second = first.createOther('manual')
    first.api.show()
    assert.equal(first.element.hiddenCount, 0)
    second.api.show()
    assert.equal(first.element.hiddenCount, 1)
    assert.equal(second.element.hiddenCount, 0)
    first.api.show()
    assert.equal(second.element.hiddenCount, 1)
    first.cleanup(); second.cleanup()
})

test('the public visibility query follows the requested native open state', () => {
    const f = fixture()
    assert.equal(typeof f.api.isOpen, 'function')
    assert.equal(f.api.isOpen(), false)
    f.api.show(); assert.equal(f.api.isOpen(), true)
    f.api.hide(); assert.equal(f.api.isOpen(), false)
    f.api.tooltipEl.value = null
    assert.equal(f.api.isOpen(), false)
    f.cleanup()
})
